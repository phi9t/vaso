#!/usr/bin/env python3
"""Qualify native packed Inkling expert inference against independent FP32 references.

The pinned tiny checkpoint is untrained. This acceptance covers the real checkpoint
loader, both packed expert formats, bounded residency, text prefill and decode,
and repeated requests. It does not establish trained-model quality.
"""
from __future__ import annotations

import argparse
from collections import Counter
import gc
import json
import os
from pathlib import Path
import time
import traceback

from inkling_checkpoint_inference import (
    EXPECTED_REPO, EXPECTED_REVISION, OPERATOR_NAMES, adapter_enabled, write_json,
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--result", type=Path, required=True)
    parser.add_argument("--process-label", default="unspecified")
    parser.add_argument("--new-tokens", type=int, default=8)
    parser.add_argument("--repeats", type=int, default=2)
    parser.add_argument("--cache-mib", type=int, default=64)
    parser.add_argument("--archive-mib", type=int, default=0,
                        help="Optional packed archive on the other admitted CUDA device")
    parser.add_argument("--gpus", default="all", help="Logical indices inside this capsule")
    parser.add_argument("--case", action="append",
                        choices=("plain", "chat", "window_boundary", "batch_two"))
    args = parser.parse_args()
    if args.new_tokens < 2 or args.repeats < 2 or args.cache_mib < 1:
        parser.error("use at least two new tokens, two repeats, and one MiB of cache")
    if args.archive_mib < 0:
        parser.error("archive-mib must be nonnegative")
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    os.environ.setdefault("HF_DEACTIVATE_ASYNC_LOAD", "1")

    import torch
    import transformers
    import triton
    from transformers import AutoTokenizer, Inkling2LoopForConditionalGeneration
    import model_triton_adapter as backend
    from packed_checkpoint import load_packed_checkpoint, place_packed_text_model

    torch.set_num_threads(2)
    torch.set_float32_matmul_precision("highest")
    torch.backends.cuda.matmul.allow_tf32 = False
    torch.backends.cudnn.allow_tf32 = False
    started = time.monotonic()
    report = {
        "state": "loading", "passed": False, "process_label": args.process_label,
        "scope": "native NVFP4/MXFP8 expert weights with FP32 activations and computation",
        "checkpoint": EXPECTED_REPO, "revision": EXPECTED_REVISION, "trained": False,
        "new_tokens": args.new_tokens, "repeats": args.repeats,
        "activation_quantization": False, "runs": [], "cpu_references": [],
        "eviction_runs": [], "cache_budget_bytes": args.cache_mib * 1024**2,
        "archive_budget_bytes": args.archive_mib * 1024**2,
        "archive_preparations": [], "store_closes": [],
        "tolerances": {"atol": 2e-3, "rtol": 1e-4, "warm_repeat_atol": 0.0},
        "versions": {"torch": torch.__version__, "triton": triton.__version__,
                     "transformers": transformers.__version__},
        "source_paths": {"adapter": backend.__file__},
    }

    def save(stage):
        report["stage"] = stage
        report["elapsed_seconds"] = time.monotonic() - started
        write_json(args.result, report)
        print(json.dumps({"stage": stage, "completed": len(report["runs"]),
                          "elapsed_seconds": round(report["elapsed_seconds"], 3)}), flush=True)

    def clean_loading(info):
        for field in ("missing_keys", "unexpected_keys", "mismatched_keys", "error_msgs"):
            if info.get(field):
                raise AssertionError((field, info[field]))

    def compare(actual, expected, *, exact=False):
        if not torch.equal(actual["sequences"], expected["sequences"]):
            raise AssertionError({"actual": actual["sequences"].tolist(),
                                  "expected": expected["sequences"].tolist()})
        torch.testing.assert_close(actual["logits"], expected["logits"],
                                   atol=0.0 if exact else 2e-3,
                                   rtol=0.0 if exact else 1e-4)
        diff = (actual["logits"] - expected["logits"]).abs()
        return {"tokens_equal": True, "max_abs": float(diff.max()),
                "per_step_max_abs": diff.flatten(1).amax(1).tolist()}

    def generate(model, batch, use_cache, device, new_tokens):
        inputs = {k: v.to(device) for k, v in batch.items()}
        if device.type == "cuda":
            torch.cuda.synchronize(device)
            torch.cuda.reset_peak_memory_stats(device)
        begin = time.monotonic()
        with torch.inference_mode():
            out = model.generate(
                **inputs, max_new_tokens=new_tokens, min_new_tokens=new_tokens,
                do_sample=False, use_cache=use_cache, return_dict_in_generate=True,
                output_logits=True,
            )
        if device.type == "cuda":
            torch.cuda.synchronize(device)
        seconds = time.monotonic() - begin
        logits = torch.stack([v.detach().float().cpu() for v in out.logits])
        sequences = out.sequences.detach().cpu()
        if not torch.isfinite(logits).all():
            raise AssertionError("Nonfinite generation logits")
        assert logits.shape[0] == new_tokens
        assert sequences.shape[1] == batch["input_ids"].shape[1] + new_tokens
        cache = out.cache
        layers = len(cache.layers) if cache is not None else 0
        expected_layers = model.config.text_config.total_num_hidden_layers
        assert layers == (expected_layers if use_cache else 0)
        return {"sequences": sequences, "logits": logits, "seconds": seconds,
                "cache_layers": layers,
                "peak_allocated_bytes": torch.cuda.max_memory_allocated(device)
                    if device.type == "cuda" else None}

    def describe(result, case, batch, use_cache):
        prompt_len = batch["input_ids"].shape[1]
        new_ids = result["sequences"][:, prompt_len:].tolist()
        return {"case": case, "use_cache": use_cache, "batch": len(new_ids),
                "prompt_length": int(prompt_len), "new_token_ids": new_ids,
                "seconds": result["seconds"], "cache_layers": result["cache_layers"],
                "peak_allocated_bytes": result["peak_allocated_bytes"]}

    active_stores = []

    def check_residency(model):
        stats = model.packed_weights.stats()
        assert stats["whole_expert_stack_dequantized"] is False
        assert stats["avoided_dense_expert_bytes"] > stats["raw_storage_bytes"]
        for device_stats in stats.get("devices", {}).values():
            assert device_stats["resident_bytes"] <= stats["cache_budget_bytes"]
            assert device_stats["peak_bytes"] <= stats["cache_budget_bytes"]
        archive = stats.get("archive", {})
        if archive.get("enabled"):
            assert archive["resident_bytes"] == archive["selected_bytes"]
            assert archive["resident_bytes"] <= archive["budget_bytes"]
            assert archive["peak_bytes"] <= archive["budget_bytes"]
            assert archive["budget_bytes"] <= report["archive_budget_bytes"]
            assert archive["device"] != archive["compute_device"]
        return stats

    def prepare_archive(model, device, *, mode, partial=False):
        if not args.archive_mib:
            return
        store = model.packed_weights
        before = check_residency(model)
        assert before["resident_bytes"] == 0 and before["misses"] == 0
        budget = report["archive_budget_bytes"]
        requested = store.plan_archive(budget)
        if partial:
            # Keep one complete selected layer in the archive. Every model
            # generation still traverses the remaining CPU-backed layers.
            first_layer = requested["selected_layers"][0]
            budget = sum(bank["bytes"] for bank in requested["banks"]
                         if bank["layer"] == first_layer)
            assert 0 < budget <= report["archive_budget_bytes"]
            assert budget < before["raw_storage_bytes"]
        planned = store.plan_archive(budget)
        config = model.config.text_config
        usage = {
            str(layer): config.loop_num_passes
                if config.loop_layer_index_start <= layer < config.loop_layer_index_end else 1
            for layer in range(config.num_hidden_layers)
        }
        assert planned["layer_usage"] == usage
        assert planned["total_logical_visits"] == sum(usage.values()) == config.total_num_hidden_layers
        assert planned["selected_logical_visits"] == sum(
            usage[str(layer)] for layer in planned["selected_layers"])
        assert planned["policy"] == "whole layers: visits descending, bytes ascending, index ascending"
        assert all(bank["uses_per_forward"] == usage[str(bank["layer"])]
                   for bank in planned["banks"])
        all_layers = store.plan_archive(before["raw_storage_bytes"])
        layer_bytes = {
            layer: sum(bank["bytes"] for bank in all_layers["banks"] if bank["layer"] == layer)
            for layer in range(config.num_hidden_layers)
        }
        assert all(value > 0 for value in layer_bytes.values())
        # Test priority against the model's loop configuration, without
        # assuming a fixed core-layer range or a particular checkpoint layout.
        eligible = [layer for layer, size in layer_bytes.items() if size <= budget]
        assert usage[str(planned["selected_layers"][0])] == max(usage[str(layer)] for layer in eligible)
        assert planned["selected_layers"] == sorted(
            planned["selected_layers"],
            key=lambda layer: (-usage[str(layer)], layer_bytes[layer], layer))
        archive_device = torch.device("cuda", 1 - device.index)
        prepared = store.prepare_archive(
            archive_device, compute_device=device, budget_bytes=budget)
        assert prepared["enabled"]
        assert prepared["device"] == str(archive_device)
        assert prepared["compute_device"] == str(device)
        assert prepared["cache_misses_at_prepare"] == before["misses"]
        for field in ("selected_layers", "selected_bytes", "banks", "layer_usage", "policy"):
            assert prepared[field] == planned[field]
        assert check_residency(model)["resident_bytes"] == 0
        if partial:
            assert prepared["selected_bytes"] < before["raw_storage_bytes"]
        report["archive_preparations"].append(
            {"gpu": device.index, "mode": mode, "partial_for_mixed_source_test": partial,
             "archive": prepared})

    def check_archive_sources(stats, *, mixed=False):
        if not args.archive_mib:
            return
        archive = stats["archive"]
        assert archive["enabled"] and archive["source_hits"] > 0
        assert archive["source_hit_bytes"] > 0
        # Preparation happens before the first lookup, so every compute-cache
        # miss has exactly one archive or CPU source.
        assert archive["source_hits"] + archive["source_misses"] == (
            stats["misses"] - archive["cache_misses_at_prepare"])
        if mixed:
            assert archive["source_misses"] > 0 and archive["source_miss_bytes"] > 0
            assert archive["selected_bytes"] < stats["raw_storage_bytes"]

    def close_store(store, gpu, mode, *, after_error=False):
        store.close()
        released = store.stats()
        archive = released.get("archive", {})
        assert released["resident_bytes"] == 0
        assert archive.get("resident_bytes", 0) == 0 and not archive.get("enabled", False)
        report["store_closes"].append({
            "gpu": gpu, "mode": mode, "after_error": after_error,
            "cache_released": True, "archive_released": True,
            "archive_device": archive.get("device"),
            "compute_device": archive.get("compute_device"),
        })
        active_stores.remove((store, gpu, mode))

    def clear_and_close(model, gpu, mode):
        store = model.packed_weights
        before = store.stats()
        store.clear_cache()
        cleared = store.stats()
        assert cleared["resident_bytes"] == 0
        if args.archive_mib:
            old_archive, archive = before["archive"], cleared["archive"]
            assert old_archive["enabled"] and archive["enabled"]
            for field in ("device", "compute_device", "resident_bytes", "selected_bytes",
                          "selected_layers", "banks"):
                assert archive[field] == old_archive[field]
        close_store(store, gpu, mode)
        report["store_closes"][-1]["clear_cache_preserved_archive"] = bool(args.archive_mib)

    def release_allocator_cache(device):
        devices = [device]
        if args.archive_mib:
            devices.append(torch.device("cuda", 1 - device.index))
        for cleanup_device in devices:
            with torch.cuda.device(cleanup_device):
                torch.cuda.empty_cache()

    save("checkpoint_validation")
    try:
        count = torch.cuda.device_count()
        if args.archive_mib and count != 2:
            raise ValueError("Archive qualification requires exactly two visible, admitted GPUs")
        acquisition = json.loads((args.checkpoint / "acquisition.json").read_text())
        assert acquisition["complete"]
        assert acquisition["repo"] == EXPECTED_REPO
        assert acquisition["revision"] == EXPECTED_REVISION
        tokenizer = AutoTokenizer.from_pretrained(str(args.checkpoint), local_files_only=True)
        dense, dense_loading = Inkling2LoopForConditionalGeneration.from_pretrained(
            str(args.checkpoint), dtype=torch.float32, attn_implementation="eager",
            local_files_only=True, output_loading_info=True,
        )
        clean_loading(dense_loading)
        dense.eval()
        cfg = dense.config.text_config
        plain = tokenizer("The next number after one, two, three is", return_tensors="pt")
        chat = tokenizer.apply_chat_template(
            [{"role": "user", "content": "Explain in one sentence why the sky is usually blue."}],
            add_generation_prompt=True, tokenize=True, return_dict=True,
            return_tensors="pt", reasoning_effort="none",
        )
        boundary = tokenizer("one two three four " * 64, return_tensors="pt")
        boundary = {k: v[:, :int(cfg.sliding_window)].contiguous() for k, v in boundary.items()}
        assert boundary["input_ids"].shape[1] == int(cfg.sliding_window)
        # Distinct equal-length rows expose batch indexing, routing, and cache bugs.
        other = tokenizer("The next number after five, six, seven is", return_tensors="pt")
        width = min(plain["input_ids"].shape[1], other["input_ids"].shape[1])
        batch_two = {k: torch.cat([v[:, :width], other[k][:, :width]], dim=0).contiguous()
                     for k, v in plain.items()}
        assert not torch.equal(batch_two["input_ids"][0], batch_two["input_ids"][1])
        all_cases = {"plain": plain, "chat": chat, "window_boundary": boundary, "batch_two": batch_two}
        cases = {name: all_cases[name] for name in (args.case or list(all_cases))}
        report.update(
            physical_layers=cfg.num_hidden_layers, logical_layers=cfg.total_num_hidden_layers,
            sliding_window=int(cfg.sliding_window),
            dense_parameter_bytes=sum(p.numel() * p.element_size() for p in dense.parameters()),
            cases={n: {"batch": int(b["input_ids"].shape[0]),
                       "prompt_tokens": b["input_ids"].tolist()} for n, b in cases.items()},
        )
        refs = {}
        for name, batch in {**cases, "plain": plain}.items():
            for cached in (False, True):
                save(f"cpu_reference:{name}:{cached}")
                value = generate(dense, batch, cached, torch.device("cpu"), args.new_tokens)
                row = describe(value, name, batch, cached)
                if cached:
                    row["cached_vs_uncached"] = compare(value, refs[(name, False)])
                refs[(name, cached)] = value
                report["cpu_references"].append(row)

        gpus = list(range(count)) if args.gpus == "all" else [int(v) for v in args.gpus.split(",")]
        assert gpus and len(set(gpus)) == len(gpus) and all(0 <= i < count for i in gpus)
        assert all(torch.cuda.get_device_capability(i) == (6, 1) for i in gpus)
        report["devices"] = [{"logical_id": i, "name": torch.cuda.get_device_name(i),
                              "capability": [6, 1]} for i in gpus]
        report["cuda_visible_devices"] = os.environ.get("CUDA_VISIBLE_DEVICES")
        if args.archive_mib:
            assert all(torch.cuda.get_device_capability(i) == (6, 1) for i in range(count))
            report["archive_roles"] = [
                {"compute_logical_id": gpu, "archive_logical_id": 1 - gpu} for gpu in gpus
            ]
        totals = Counter()
        cross_gpu = {}
        for gpu in gpus:
            torch.cuda.set_device(gpu)
            device = torch.device("cuda", gpu)
            dense.to(device)
            baseline = {}
            with adapter_enabled(backend):
                for repeat in range(args.repeats):
                    for name, batch in cases.items():
                        for cached in (False, True):
                            save(f"dense_gpu:{gpu}:{repeat}:{name}:{cached}")
                            backend.calls.clear()
                            value = generate(dense, batch, cached, device, args.new_tokens)
                            row = describe(value, name, batch, cached)
                            row.update(mode="dense_fp32", gpu=gpu, repeat=repeat,
                                       vs_cpu=compare(value, refs[(name, cached)]),
                                       triton_calls=dict(backend.calls))
                            if repeat:
                                row["vs_warm_repeat"] = compare(value, baseline[(name, cached)], exact=True)
                            else:
                                baseline[(name, cached)] = value
                            report["runs"].append(row)
            dense.to("cpu")
            torch.cuda.empty_cache()

            save(f"packed_load:{gpu}")
            packed, packed_loading = load_packed_checkpoint(
                args.checkpoint, cache_budget_bytes=report["cache_budget_bytes"], text_device=device)
            active_stores.append((packed.packed_weights, gpu, "normal"))
            clean_loading(packed_loading)
            packed.eval()
            stats = check_residency(packed)
            assert stats["resident_bytes"] == 0
            report.setdefault("packed_loads", []).append(stats)
            place_packed_text_model(packed, device)
            # Direct native placement must preserve the independently loaded
            # ordinary CPU weights, while leaving the expert cache empty.
            expected_parameters = {name: value for name, value in dense.named_parameters()
                                   if ".mlp.experts." not in name}
            actual_parameters = dict(packed.named_parameters())
            assert actual_parameters.keys() == expected_parameters.keys()
            for name, parameter in actual_parameters.items():
                if not torch.equal(parameter.detach().cpu(), expected_parameters[name]):
                    raise AssertionError(f"Direct placement changed ordinary parameter {name}")
            expected_buffers = dict(dense.named_buffers())
            actual_buffers = dict(packed.named_buffers())
            assert actual_buffers.keys() == expected_buffers.keys()
            for name, buffer in actual_buffers.items():
                if not torch.equal(buffer.detach().cpu(), expected_buffers[name]):
                    raise AssertionError(f"Direct placement changed ordinary buffer {name}")
            report.setdefault("direct_loads", []).append({
                "gpu": gpu, "ordinary_parameters_compared": len(actual_parameters),
                "ordinary_buffers_compared": len(actual_buffers),
                "parameters_and_buffers_match_cpu_exactly": True,
                "placement": packed.packed_text_placement,
            })
            del actual_parameters, actual_buffers, expected_parameters, expected_buffers
            assert check_residency(packed)["resident_bytes"] == 0
            prepare_archive(packed, device, mode="normal")
            first = {}
            with adapter_enabled(backend):
                for repeat in range(args.repeats):
                    for name, batch in cases.items():
                        for cached in (False, True):
                            save(f"packed_gpu:{gpu}:{repeat}:{name}:{cached}")
                            backend.calls.clear()
                            value = generate(packed, batch, cached, device, args.new_tokens)
                            calls = dict(backend.calls)
                            required = OPERATOR_NAMES if cached else OPERATOR_NAMES[:-1]
                            assert all(calls.get(op, 0) > 0 for op in required), calls
                            assert calls.get("packed_nvfp4", 0) > 0 and calls.get("packed_mxfp8", 0) > 0
                            totals.update(calls)
                            row = describe(value, name, batch, cached)
                            row.update(mode="packed_fp32", gpu=gpu, repeat=repeat,
                                       vs_cpu=compare(value, refs[(name, cached)]),
                                       vs_dense_gpu=compare(value, baseline[(name, cached)]),
                                       triton_calls=calls, residency=check_residency(packed))
                            key = (name, cached)
                            if repeat:
                                row["vs_warm_repeat"] = compare(value, first[key], exact=True)
                            else:
                                first[key] = value
                                if key in cross_gpu:
                                    row["vs_other_gpu"] = compare(value, cross_gpu[key])
                                else:
                                    cross_gpu[key] = value
                            report["runs"].append(row)
            stats = check_residency(packed)
            assert stats["hits"] > 0 and stats["misses"] > 0
            check_archive_sources(stats)
            clear_and_close(packed, gpu, "normal")
            packed.to("cpu")
            del packed, first
            gc.collect()
            release_allocator_cache(device)

            # Force bounded residency through repeated eviction on the same actual checkpoint.
            # Twice the largest expert fits an individual projection while remaining much
            # smaller than the complete tiny expert bank.
            eviction_budget = max(512 * 1024, 2 * int(stats["largest_expert_bytes"]))
            assert eviction_budget < stats["raw_storage_bytes"]
            save(f"eviction_load:{gpu}")
            evicted, info = load_packed_checkpoint(args.checkpoint, cache_budget_bytes=eviction_budget,
                                                    text_device=device)
            active_stores.append((evicted.packed_weights, gpu, "forced_eviction"))
            clean_loading(info)
            evicted.eval()
            place_packed_text_model(evicted, device)
            prepare_archive(evicted, device, mode="forced_eviction", partial=True)
            with adapter_enabled(backend):
                for repeat in range(2):
                    save(f"eviction_generate:{gpu}:{repeat}")
                    value = generate(evicted, plain, True, device, args.new_tokens)
                    # Plain is always available even when a narrower case set was requested.
                    stats_evict = check_residency(evicted)
                    assert stats_evict["evictions"] > 0
                    check_archive_sources(stats_evict, mixed=True)
                    row = describe(value, "plain", plain, True)
                    row.update(gpu=gpu, repeat=repeat, vs_cpu=compare(value, refs[("plain", True)]),
                               residency=stats_evict)
                    report["eviction_runs"].append(row)
            clear_and_close(evicted, gpu, "forced_eviction")
            evicted.to("cpu")
            del evicted
            gc.collect()
            release_allocator_cache(device)
        expected = len(gpus) * args.repeats * len(cases) * 2 * 2
        assert len(report["runs"]) == expected
        assert not active_stores and len(report["store_closes"]) == 2 * len(gpus)
        if args.archive_mib:
            assert len(report["archive_preparations"]) == 2 * len(gpus)
            report["archive_qualification"] = {
                "requested_budget_bytes": report["archive_budget_bytes"],
                "compute_roles": gpus, "priority_derived_from_model_loop": True,
                "mixed_archive_cpu_source_coverage": all(
                    row["residency"]["archive"]["source_hits"] > 0
                    and row["residency"]["archive"]["source_misses"] > 0
                    for row in report["eviction_runs"]),
                "clear_cache_preserved_archive": all(
                    row["clear_cache_preserved_archive"] for row in report["store_closes"]),
                "both_device_stores_released": all(
                    row["cache_released"] and row["archive_released"]
                    for row in report["store_closes"]),
            }
        report.update(
            state="passed", passed=True, triton_calls=dict(totals),
            completed_generations=len(report["runs"]) + len(report["eviction_runs"]),
            completed_cpu_generations=len(report["cpu_references"]),
            all_cache_clears_confirmed=True, all_store_closes_confirmed=True,
        )
        save("complete")
        return 0
    except BaseException as error:
        report.update(state="failed", passed=False, error=repr(error), traceback=traceback.format_exc())
        # Release both devices even if loading, comparison, or generation fails.
        # A cleanup error is recorded without replacing the original failure.
        for store, gpu, mode in list(active_stores):
            try:
                close_store(store, gpu, mode, after_error=True)
            except BaseException as cleanup_error:
                report.setdefault("cleanup_errors", []).append(
                    {"gpu": gpu, "mode": mode, "error": repr(cleanup_error)})
        save("failed")
        raise


if __name__ == "__main__":
    raise SystemExit(main())
