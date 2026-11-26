#!/usr/bin/env python3
"""Two-GPU fixed packed-archive qualification on the pinned tiny checkpoint.

Run only inside an admitted Insula capsule holding both GPU leases. The numerical
reference is the same compute GPU using CPU-backed packed weights. This isolates
source selection, copying, residency, and lifecycle; it does not replace the
independent dense-reference suite or establish trained-model quality.
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
from unittest.mock import patch

from inkling_checkpoint_inference import (
    EXPECTED_REPO, EXPECTED_REVISION, OPERATOR_NAMES, adapter_enabled, write_json,
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--result", type=Path, required=True)
    parser.add_argument("--compute-device", default="cuda:1")
    parser.add_argument("--archive-device", default="cuda:0")
    parser.add_argument("--new-tokens", type=int, default=4)
    parser.add_argument("--repeats", type=int, default=2)
    args = parser.parse_args()
    if args.new_tokens < 2 or args.new_tokens > 8 or args.repeats < 2:
        parser.error("use 2-8 new tokens and at least two repeats")
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    os.environ.setdefault("HF_DEACTIVATE_ASYNC_LOAD", "1")

    import torch
    from transformers import AutoTokenizer, Inkling2LoopConfig
    import model_triton_adapter as backend
    from packed_checkpoint import load_packed_checkpoint

    torch.set_num_threads(2)
    torch.set_float32_matmul_precision("highest")
    torch.backends.cuda.matmul.allow_tf32 = False
    torch.backends.cudnn.allow_tf32 = False
    compute, archive = torch.device(args.compute_device), torch.device(args.archive_device)
    if compute.type != "cuda" or archive.type != "cuda" or compute == archive:
        parser.error("compute and archive must be distinct, explicit CUDA devices")
    if compute.index is None or archive.index is None:
        parser.error("explicit CUDA indices are required")
    if max(compute.index, archive.index) >= torch.cuda.device_count():
        parser.error("both selected devices must be visible and admitted")
    # The existing non-expert Triton adapter follows the current CUDA device.
    torch.cuda.set_device(compute)

    started = time.monotonic()
    report = {
        "passed": False, "stage": "load", "checkpoint": EXPECTED_REPO,
        "revision": EXPECTED_REVISION, "trained": False,
        "reference": "same compute GPU with CPU-backed native packed weights",
        "compute_device": str(compute), "archive_device": str(archive),
        "repeats": args.repeats, "new_tokens": args.new_tokens, "runs": [],
    }
    model = store = None

    def stage(name):
        report["stage"] = name
        write_json(args.result, report)
        print(json.dumps({"event": name, "seconds": time.monotonic() - started}), flush=True)

    def memory():
        return {
            str(device): {
                "allocated_bytes": int(torch.cuda.memory_allocated(device)),
                "reserved_bytes": int(torch.cuda.memory_reserved(device)),
                "max_memory_allocated_bytes": int(torch.cuda.max_memory_allocated(device)),
                "max_memory_reserved_bytes": int(torch.cuda.max_memory_reserved(device)),
            }
            for device in (compute, archive)
        }

    def generation(tokens, use_cache):
        with torch.inference_mode():
            output = model.generate(
                input_ids=tokens.to(compute), do_sample=False, use_cache=use_cache,
                min_new_tokens=args.new_tokens, max_new_tokens=args.new_tokens,
                pad_token_id=0, return_dict_in_generate=True, output_logits=True,
            )
            assert output.sequences.shape[1] == tokens.shape[1] + args.new_tokens
            assert len(output.logits) == args.new_tokens
            logits = torch.stack(output.logits).float().cpu()
            assert torch.isfinite(logits).all()
            return {"tokens": output.sequences.cpu(), "logits": logits}

    def compare(actual, expected):
        torch.testing.assert_close(actual["tokens"], expected["tokens"], atol=0, rtol=0)
        torch.testing.assert_close(actual["logits"], expected["logits"], atol=0, rtol=0)
        return float((actual["logits"] - expected["logits"]).abs().max())

    def check_archive_bytes():
        # Deliberately inspect internal banks only in this qualification probe.
        # Return no tensor references, so clear/rollback residency checks are real.
        checked = 0
        for key, bank in store._archive_banks.items():
            source = store.matrices[key]
            for field in ("weight", "scales", "outer_scale"):
                actual, expected = getattr(bank, field), getattr(source, field)
                if expected is None:
                    assert actual is None
                    continue
                assert actual.device == archive and expected.device.type == "cpu"
                assert actual.dtype == expected.dtype and actual.is_contiguous()
                torch.testing.assert_close(
                    actual.view(torch.uint8).cpu(), expected.view(torch.uint8), atol=0, rtol=0,
                )
            checked += 1
        return checked

    def archive_pointers():
        return {
            key: tuple(None if value is None else value.data_ptr()
                       for value in (bank.weight, bank.scales, bank.outer_scale))
            for key, bank in store._archive_banks.items()
        }

    def check_nonalias():
        key = next(iter(store._archive_banks))
        before = int(store.matrices[key].weight.reshape(-1)[0])
        with torch.inference_mode():
            entry = store.get(*key, 0, compute)
            entry.weight.reshape(-1)[0].bitwise_xor_(1)
            torch.cuda.synchronize(compute)
            assert int(store._archive_banks[key].weight.reshape(-1)[0]) == before
            assert int(store.matrices[key].weight.reshape(-1)[0]) == before
            del entry
        store.clear_cache()
        with torch.inference_mode():
            entry = store.get(*key, 0, compute)
            assert int(entry.weight.reshape(-1)[0]) == before
            del entry
        store.clear_cache()

    try:
        stage("load")
        acquisition = json.loads((args.checkpoint / "acquisition.json").read_text())
        assert acquisition["complete"] is True
        assert acquisition["repo"] == EXPECTED_REPO
        assert acquisition["revision"] == EXPECTED_REVISION
        config = Inkling2LoopConfig.from_pretrained(str(args.checkpoint), local_files_only=True)
        if config.get_text_config().hidden_size > 512:
            raise ValueError("This bounded probe accepts the tiny integration checkpoint only")
        for device in (compute, archive):
            torch.cuda.reset_peak_memory_stats(device)
        model, info = load_packed_checkpoint(
            args.checkpoint, cache_budget_bytes=64 * 1024**2, text_device=compute,
        )
        for field in ("missing_keys", "unexpected_keys", "mismatched_keys", "error_msgs", "conversion_errors"):
            assert not info.get(field), (field, info[field])
        store = model.packed_weights
        initial = store.stats()
        assert initial["archive"]["enabled"] is False
        assert initial["archive"]["resident_bytes"] == 0
        assert initial["raw_storage_bytes"] < 256 * 1024**2
        assert initial["whole_expert_stack_dequantized"] is False
        assert initial["cpu_reference_expert_decodes"] == 0
        report["loading_info"] = {
            key: sorted(value) if isinstance(value, set) else value for key, value in info.items()
        }
        report["initial"] = initial
        tokenizer = AutoTokenizer.from_pretrained(str(args.checkpoint), local_files_only=True)
        tokens = tokenizer("Write two short words.", add_special_tokens=False, return_tensors="pt").input_ids
        assert tokens.ndim == 2 and tokens.shape[0] == 1 and 1 <= tokens.shape[1] <= 32
        report["prompt_tokens"] = tokens.tolist()
        store.cache_budget_bytes = 2 * initial["largest_expert_bytes"]
        full_budget = initial["raw_storage_bytes"]
        plan = store.plan_archive(full_budget)
        assert plan["selected_bytes"] == full_budget
        assert len(plan["banks"]) == initial["matrix_count"]
        first_layer = plan["selected_layers"][0]
        partial_budget = sum(bank["bytes"] for bank in plan["banks"] if bank["layer"] == first_layer)
        assert 0 < partial_budget < full_budget
        partial_plan = store.plan_archive(partial_budget)
        assert len(partial_plan["selected_layers"]) < len(plan["selected_layers"])
        assert partial_plan["layer_usage"][str(partial_plan["selected_layers"][0])] == max(
            partial_plan["layer_usage"].values()
        )
        report.update(full_plan=plan, partial_plan=partial_plan)

        stage("cpu_source_gpu_reference")
        baseline = {}
        visits, forwards = Counter(), Counter()
        hooks = [
            model.register_forward_pre_hook(lambda *_: forwards.update(["forward"]))
        ]
        for index, layer in enumerate(model.model.language_model.layers):
            hooks.append(layer.mlp.experts.register_forward_pre_hook(
                lambda *_, index=index: visits.update([index])
            ))
        del layer
        with adapter_enabled(backend):
            try:
                baseline[True] = generation(tokens, True)
            finally:
                for hook in hooks:
                    hook.remove()
            assert forwards["forward"] == args.new_tokens
            assert all(visits[index] == count * forwards["forward"]
                       for index, count in store.layer_usage.items())
            report["actual_forward_usage"] = {
                "forward_calls": forwards["forward"],
                "physical_layer_visits": dict(visits),
                "visits_per_forward": {
                    str(index): visits[index] // forwards["forward"] for index in sorted(visits)
                },
            }
            baseline[False] = generation(tokens, False)
            store.clear_cache()

            stage("budget_rejection_and_rollback")
            before = int(torch.cuda.memory_allocated(archive))
            smallest_layer = min(
                sum(matrix.raw_bytes for matrix in store.matrices.values() if matrix.layer == index)
                for index in store.layer_usage
            )
            try:
                store.prepare_archive(archive, compute_device=compute, budget_bytes=smallest_layer - 1)
            except ValueError as error:
                assert "cannot fit" in str(error)
            else:
                raise AssertionError("An undersized whole-layer budget was accepted")
            assert int(torch.cuda.memory_allocated(archive)) == before
            assert store.stats()["archive"]["resident_bytes"] == 0
            try:
                store.prepare_archive(compute, compute_device=compute, budget_bytes=full_budget)
            except ValueError as error:
                assert "distinct" in str(error)
            else:
                raise AssertionError("The archive accepted its compute device")

            original_copy = store._copy_archive_tensor
            calls = Counter()
            first_key = (plan["banks"][0]["layer"], plan["banks"][0]["projection"])
            fail_at = (3 if store.matrices[first_key].outer_scale is not None else 2) + 2

            def fail_after_complete_and_partial_bank(source, device):
                calls["copy"] += 1
                if calls["copy"] == fail_at:
                    raise RuntimeError("injected archive initialization copy failure")
                return original_copy(source, device)

            with patch.object(store, "_copy_archive_tensor", side_effect=fail_after_complete_and_partial_bank):
                try:
                    store.prepare_archive(archive, compute_device=compute, budget_bytes=full_budget)
                except RuntimeError as error:
                    assert "injected archive" in str(error)
                else:
                    raise AssertionError("Injected archive failure was not propagated")
            gc.collect()
            torch.cuda.synchronize(archive)
            assert int(torch.cuda.memory_allocated(archive)) == before
            rollback = store.stats()["archive"]
            assert rollback["enabled"] is False and rollback["resident_bytes"] == 0
            assert 0 < rollback["peak_bytes"] <= full_budget
            assert not store._archive_banks
            report.update(undersized_budget_rejected=True, same_device_rejected=True,
                          initialization_rollback=rollback)

            stage("full_archive_exact_bytes")
            prepared = store.prepare_archive(archive, compute_device=compute, budget_bytes=full_budget)
            assert prepared["enabled"] and prepared["resident_bytes"] == full_budget
            assert prepared["peak_bytes"] <= prepared["budget_bytes"]
            report["archive_banks_bitwise_equal"] = check_archive_bytes()
            assert report["archive_banks_bitwise_equal"] == initial["matrix_count"]
            pointers = archive_pointers()
            check_nonalias()
            assert archive_pointers() == pointers
            assert store.stats()["archive"]["resident_bytes"] == full_budget
            report["cache_copies_do_not_alias_archive_or_cpu"] = True
            try:
                store.prepare_archive(archive, compute_device=compute, budget_bytes=full_budget)
            except RuntimeError as error:
                assert "already installed" in str(error)
            else:
                raise AssertionError("An immutable archive was replaced without clearing")
            try:
                store.get(*first_key, 0, archive)
            except ValueError as error:
                assert "changing" in str(error)
            else:
                raise AssertionError("Archive source device was accepted as a compute role")

            for mode, budget in (("full", full_budget), ("mixed", partial_budget)):
                if mode == "mixed":
                    stage("mixed_archive_with_eviction")
                    store.clear_archive()
                    assert store.stats()["archive"]["resident_bytes"] == 0
                    store.clear_cache()
                    prepared = store.prepare_archive(archive, compute_device=compute, budget_bytes=budget)
                    assert prepared["selected_layers"] == partial_plan["selected_layers"]
                    pointers = archive_pointers()
                else:
                    stage("full_archive_generation")
                start_evictions = store.stats()["evictions"]
                for use_cache in (True, False):
                    for repeat in range(args.repeats):
                        store.clear_cache()
                        assert archive_pointers() == pointers
                        generated = generation(tokens, use_cache)
                        difference = compare(generated, baseline[use_cache])
                        report["runs"].append({
                            "archive_mode": mode, "use_cache": use_cache, "repeat": repeat,
                            "tokens": generated["tokens"].tolist(), "logits_max_abs": difference,
                        })
                        del generated
                        print(json.dumps({"event": "generation_passed", **report["runs"][-1]}), flush=True)
                state = store.stats()
                assert state["evictions"] > start_evictions
                assert state["resident_bytes"] <= store.cache_budget_bytes
                assert state["archive"]["resident_bytes"] <= budget
                assert state["archive"]["source_hits"] > 0
                assert archive_pointers() == pointers
                if mode == "full":
                    assert state["archive"]["source_misses"] == 0
                    assert all(state["archive"]["source_hits_by_format"].get(fmt, 0) > 0
                               for fmt in ("nvfp4", "mxfp8"))
                else:
                    assert state["archive"]["source_misses"] > 0
                report[mode + "_archive_final"] = state
                write_json(args.result, report)

            assert all(backend.calls[name] > 0 for name in OPERATOR_NAMES)
            assert all(backend.calls["packed_" + fmt] > 0 for fmt in ("nvfp4", "mxfp8"))
            report["operators"] = dict(backend.calls)
            assert store.stats()["cpu_reference_expert_decodes"] == 0

        stage("close_both_devices")
        store.close()
        closed = store.stats()
        assert closed["resident_bytes"] == 0
        assert closed["archive"]["resident_bytes"] == 0 and not closed["archive"]["enabled"]
        assert not store._archive_banks
        assert all(state["resident_bytes"] == 0 for state in closed["devices"].values())
        assert int(torch.cuda.memory_allocated(archive)) == before
        report.update(closed=closed, passed=True, stage="complete")
    except BaseException as error:
        report.update(error=repr(error), traceback=traceback.format_exc())
        raise
    finally:
        if store is not None:
            store.close()
            report["cleanup_store"] = store.stats()
        model = None
        gc.collect()
        for device in (compute, archive):
            with torch.cuda.device(device):
                torch.cuda.empty_cache()
        report["allocator_after_model_cleanup"] = memory()
        report["seconds"] = time.monotonic() - started
        write_json(args.result, report)
    print(json.dumps(report), flush=True)


if __name__ == "__main__":
    main()
