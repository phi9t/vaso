#!/usr/bin/env python3
"""Generate final text with checkpoint-native packed experts and FP32 computation.

Run through the Pascal capsule's inference-python wrapper. The caller owns GPU
admission and process supervision. The vision tower stays on CPU; this entrypoint
accepts text only. Expert CPU backing and its bounded GPU cache are independent
of ordinary model parameter placement. Repeat --prompt to reuse one model load.
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
import os
import resource
from pathlib import Path
import time
import traceback

from inkling_chat import parse_text_response, prepare_text_prompt


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(value, indent=2) + "\n")
    tmp.replace(path)


def cuda_memory_snapshot(torch, devices):
    """Capture completed allocation and allocator peaks before request resets."""
    memory = {}
    for device in devices:
        torch.cuda.synchronize(device)
        free, total = torch.cuda.mem_get_info(device)
        memory[str(device)] = {
            "device_name": torch.cuda.get_device_name(device),
            "allocated_bytes": torch.cuda.memory_allocated(device),
            "reserved_bytes": torch.cuda.memory_reserved(device),
            "peak_allocated_bytes": torch.cuda.max_memory_allocated(device),
            "peak_reserved_bytes": torch.cuda.max_memory_reserved(device),
            "free_bytes": free, "total_bytes": total,
        }
    return memory


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--checkpoint", type=Path, required=True)
    p.add_argument("--prompt", action="append", required=True,
                   help="Text prompt; repeat to serve multiple prompts in one process")
    p.add_argument("--result", type=Path, required=True)
    p.add_argument("--device", default="cuda:0")
    p.add_argument("--cache-mib", type=int, default=256)
    p.add_argument("--archive-device",
                   help="Optional separate CUDA device for a fixed packed-weight archive")
    p.add_argument("--archive-mib", type=int, default=0,
                   help="Retained archive payload budget; requires --archive-device")
    p.add_argument("--max-new-tokens", type=int, default=32)
    p.add_argument("--repeat", type=int, default=2,
                   help="Fresh requests per prompt; require exact deterministic repeats")
    p.add_argument("--response-mode", choices=("final", "model"), default="final",
                   help="Prefill final text, or let the model choose its native channel")
    p.add_argument("--require-final-answer", action="store_true",
                   help="Fail unless every request produces nonempty final text and sampling EOS")
    p.add_argument("--raw-prompt", action="store_true",
                   help="Tokenize the supplied prompt unchanged; report unframed output as raw text")
    a = p.parse_args()
    if a.cache_mib <= 0 or a.max_new_tokens <= 0 or a.repeat <= 0:
        p.error("cache-mib, max-new-tokens and repeat must be positive")
    if bool(a.archive_device) != (a.archive_mib > 0):
        p.error("--archive-device and --archive-mib must be provided together")
    if a.archive_mib < 0:
        p.error("archive-mib must be nonnegative")
    report = {
        "passed": False, "state": "loading", "checkpoint": str(a.checkpoint),
        "device": a.device, "weight_storage": "native_nvfp4_mxfp8",
        "activation_dtype": "float32", "compute_dtype": "float32",
        "activation_quantization": False, "cache_budget_bytes": a.cache_mib * 1024**2,
        "archive_device": a.archive_device,
        "archive_budget_bytes": a.archive_mib * 1024**2,
        "text_only": True, "requests": [], "prompt_results": [], "prompts": a.prompt,
        "response_mode": "raw" if a.raw_prompt else a.response_mode,
        "require_final_answer": a.require_final_answer,
        "pass_scope": "finite deterministic generation and packed-cache lifecycle"
            + (" with complete final answers" if a.require_final_answer else ""),
    }
    acquisition = a.checkpoint / "acquisition.json"
    if acquisition.is_file():
        identity = json.loads(acquisition.read_text())
        report["checkpoint_identity"] = {
            key: identity.get(key) for key in ("repo", "revision", "complete")
        }
    started = time.monotonic()
    model = None
    cleanup_devices = []
    write_json(a.result, report)
    try:
        os.environ.setdefault("HF_DEACTIVATE_ASYNC_LOAD", "1")
        report["deactivate_async_load"] = os.environ["HF_DEACTIVATE_ASYNC_LOAD"]
        import torch
        from transformers import AutoTokenizer
        from packed_checkpoint import load_packed_checkpoint, place_packed_text_model
        device = torch.device(a.device)
        if device.type not in ("cpu", "cuda"):
            raise ValueError("device must select CPU or CUDA")
        if device.type == "cuda":
            if torch.cuda.get_device_capability(device) != (6, 1):
                raise ValueError("This qualified entrypoint targets SM61")
            torch.cuda.set_device(device)
            device = torch.device("cuda", torch.cuda.current_device())
            cleanup_devices.append(device)
        archive_device = None
        if a.archive_device:
            if device.type != "cuda":
                raise ValueError("A packed CUDA archive requires CUDA computation")
            archive_device = torch.device(a.archive_device)
            if archive_device.type != "cuda" or archive_device.index is None:
                raise ValueError("archive-device must be an explicit CUDA index")
            if archive_device == device:
                raise ValueError("archive-device must differ from the compute device")
            if torch.cuda.get_device_capability(archive_device) != (6, 1):
                raise ValueError("The qualified archive path targets SM61")
            cleanup_devices.append(archive_device)
        torch.set_num_threads(2)
        torch.set_float32_matmul_precision("highest")
        torch.backends.cuda.matmul.allow_tf32 = False
        tokenizer = AutoTokenizer.from_pretrained(str(a.checkpoint), local_files_only=True)
        prepared = [
            prepare_text_prompt(tokenizer, prompt, response_mode=a.response_mode,
                                raw_prompt=a.raw_prompt)
            for prompt in a.prompt
        ]
        report["prompt_tokens"] = prepared[0][1]["prompt_tokens"]
        report["prompt_construction"] = [metadata for _, metadata in prepared]
        model, loading = load_packed_checkpoint(
            a.checkpoint, cache_budget_bytes=report["cache_budget_bytes"], text_device=device)
        for field in ("missing_keys", "unexpected_keys", "mismatched_keys",
                      "error_msgs", "conversion_errors"):
            if loading.get(field):
                raise RuntimeError(f"Incomplete checkpoint load: {field}={loading[field]}")
        model.eval()
        place_packed_text_model(model, device)
        report["text_placement"] = model.packed_text_placement
        report["ordinary_parameters_by_device"] = {
            str(dev): sum(param.numel() * param.element_size() for param in model.parameters()
                          if param.device == dev)
            for dev in {param.device for param in model.parameters()}
        }
        report["packed_load"] = model.packed_weights.stats()
        if report["packed_load"]["resident_bytes"] != 0:
            raise AssertionError("Placement populated the packed cache before execution")
        report["pre_generation_memory_peak_scope"] = (
            "process counters observed before the first per-request compute-device reset")
        report["gpu_memory_post_model_load"] = cuda_memory_snapshot(torch, cleanup_devices)
        if archive_device is not None:
            if not hasattr(model.packed_weights, "prepare_archive"):
                raise RuntimeError("This packed loader does not support a CUDA archive")
            report["archive_preparation"] = model.packed_weights.prepare_archive(
                archive_device, compute_device=device,
                budget_bytes=report["archive_budget_bytes"])
        report["gpu_memory_post_archive_load"] = cuda_memory_snapshot(torch, cleanup_devices)
        report["load_seconds"] = time.monotonic() - started
        if device.type == "cuda":
            import model_triton_adapter as backend
            backend.install()
            torch.cuda.synchronize(device)
        totals = Counter()
        for prompt_index, (cpu_inputs, metadata) in enumerate(prepared):
            inputs = {key: value.to(device) for key, value in cpu_inputs.items()}
            prompt_start = len(report["requests"])
            reference = None
            for repeat in range(a.repeat):
                report.update(state="generating", active_prompt_index=prompt_index,
                              active_request=repeat)
                write_json(a.result, report)
                if device.type == "cuda":
                    backend.calls.clear()
                    torch.cuda.reset_peak_memory_stats(device)
                    torch.cuda.synchronize(device)
                begin = time.monotonic()
                with torch.inference_mode():
                    result = model.generate(
                        **inputs, max_new_tokens=a.max_new_tokens,
                        do_sample=False, use_cache=True,
                        return_dict_in_generate=True, output_logits=True,
                    )
                if device.type == "cuda":
                    torch.cuda.synchronize(device)
                elapsed = time.monotonic() - begin
                sequences = result.sequences.detach().cpu()
                logits = torch.stack([value.detach().float().cpu() for value in result.logits])
                if not torch.isfinite(logits).all():
                    raise AssertionError("Nonfinite generation logits")
                if reference is None:
                    reference = (sequences, logits)
                else:
                    if not torch.equal(sequences, reference[0]):
                        raise AssertionError("Generated tokens changed across fresh requests of one prompt")
                    torch.testing.assert_close(logits, reference[1], atol=0.0, rtol=0.0)
                generated = sequences[:, metadata["prompt_tokens"]:].tolist()
                responses = [
                    parse_text_response(tokenizer, ids, response_mode=metadata["response_mode"],
                                        max_new_tokens=a.max_new_tokens)
                    for ids in generated
                ]
                residency = model.packed_weights.stats()
                if residency["whole_expert_stack_dequantized"]:
                    raise AssertionError("Expert stack was expanded")
                if any(value["resident_bytes"] > residency["cache_budget_bytes"]
                       or value["peak_bytes"] > residency["cache_budget_bytes"]
                       for value in residency["devices"].values()):
                    raise AssertionError("Packed cache exceeded its retained-payload budget")
                archive = residency.get("archive", {})
                if (archive.get("resident_bytes", 0) > report["archive_budget_bytes"]
                        or archive.get("peak_bytes", 0) > report["archive_budget_bytes"]):
                    raise AssertionError("Packed archive exceeded its retained-payload budget")
                row = {
                    "prompt_index": prompt_index, "prompt": a.prompt[prompt_index],
                    "prompt_tokens": metadata["prompt_tokens"],
                    "repeat": repeat, "generation_seconds": elapsed,
                    "generated_token_ids": generated, "responses": responses,
                    "final_text": responses[0]["final_text"],
                    "completions": [response["raw_text"] if a.raw_prompt else response["final_text"]
                                    for response in responses],
                    "analysis_present": any(response["analysis_present"] for response in responses),
                    "answer_complete": all(response["answer_complete"] for response in responses),
                    "finish_reasons": [response["finish_reason"] for response in responses],
                    "finish_reason": responses[0]["finish_reason"],
                    "turn_complete": all(response["turn_complete"] for response in responses),
                    "finite_logits": True, "matches_first_request": True if repeat else None,
                    "residency": residency,
                    "peak_allocated_bytes": torch.cuda.max_memory_allocated(device)
                        if device.type == "cuda" else None,
                }
                if device.type == "cuda":
                    free, total = torch.cuda.mem_get_info(device)
                    row.update(gpu_free_bytes_after_request=free, gpu_total_bytes=total,
                               peak_reserved_bytes=torch.cuda.max_memory_reserved(device))
                    row["triton_calls"] = dict(backend.calls)
                    if not all(backend.calls.get(name, 0) > 0
                               for name in ("packed_nvfp4", "packed_mxfp8")):
                        raise AssertionError("Both native packed formats must execute")
                    totals.update(backend.calls)
                report["requests"].append(row)
                print(json.dumps({
                    "prompt_index": prompt_index, "repeat": repeat,
                    "completions": row["completions"], "analysis_present": row["analysis_present"],
                    "answer_complete": row["answer_complete"], "finish_reasons": row["finish_reasons"],
                    "generation_seconds": elapsed,
                }), flush=True)
                write_json(a.result, report)
                if a.require_final_answer and not row["answer_complete"]:
                    raise AssertionError("Request did not produce a complete final-channel answer")
                del result, logits
            first = report["requests"][prompt_start]
            report["prompt_results"].append({
                "prompt_index": prompt_index, "prompt": a.prompt[prompt_index],
                "prompt_tokens": metadata["prompt_tokens"],
                "request_count": a.repeat, "generated_token_ids": first["generated_token_ids"],
                "completions": first["completions"], "final_text": first["final_text"],
                "responses": first["responses"], "finish_reason": first["finish_reason"],
                "turn_complete": first["turn_complete"],
                "answer_complete": all(row["answer_complete"]
                                       for row in report["requests"][prompt_start:]),
                "repeated_requests_match": a.repeat >= 2,
            })
            # Keep only one prompt's reference logits alive at a time.
            del reference, sequences, inputs
            write_json(a.result, report)
        first = report["requests"][0]
        report.update(
            passed=True, state="passed",
            generation_seconds=sum(row["generation_seconds"] for row in report["requests"]),
            generated_token_ids=first["generated_token_ids"],
            completions=first["completions"], final_text=first["final_text"],
            responses=first["responses"], finish_reason=first["finish_reason"],
            turn_complete=all(row["turn_complete"] for row in report["requests"]),
            answer_complete=all(row["answer_complete"] for row in report["requests"]),
            residency=model.packed_weights.stats(), triton_calls=dict(totals),
            repeated_requests_match=a.repeat >= 2,
            peak_allocated_bytes=max((row["peak_allocated_bytes"] or 0
                                      for row in report["requests"]), default=0),
        )
        return 0
    except BaseException as error:
        report.update(passed=False, state="failed", error=repr(error),
                      traceback=traceback.format_exc())
        raise
    finally:
        had_primary_error = "error" in report
        cleanup_failure = None
        if model is not None:
            try:
                store = model.packed_weights
                # Older single-device loaders have no archive and expose only clear_cache.
                if hasattr(store, "close"):
                    store.close()
                else:
                    store.clear_cache()
                released = store.stats()
                report["cache_released"] = released["resident_bytes"] == 0
                report["archive_released"] = released.get("archive", {}).get("resident_bytes", 0) == 0
                if not report["cache_released"] or not report["archive_released"]:
                    raise RuntimeError("Packed compute-cache/archive release was not confirmed")
                memory = {}
                for cleanup_device in cleanup_devices:
                    with torch.cuda.device(cleanup_device):
                        torch.cuda.empty_cache()
                        memory[str(cleanup_device)] = {
                            "allocated_bytes": torch.cuda.memory_allocated(cleanup_device),
                            "reserved_bytes": torch.cuda.memory_reserved(cleanup_device),
                        }
                report["gpu_memory_after_packed_release"] = memory
                report["ordinary_model_parameters_live_at_packed_release"] = True
            except BaseException as error:
                cleanup_failure = error
                report.update(passed=False, state="failed", cache_released=False,
                              cleanup_error=repr(error), cleanup_traceback=traceback.format_exc())
                report.setdefault("error", repr(error))
        report["elapsed_seconds"] = time.monotonic() - started
        report["process_peak_rss_bytes"] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024
        write_json(a.result, report)
        if cleanup_failure is not None and not had_primary_error:
            raise RuntimeError("Generation cleanup failed; see the result receipt") from cleanup_failure


if __name__ == "__main__":
    raise SystemExit(main())
