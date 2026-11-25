#!/usr/bin/env python3
"""Repeated actual-checkpoint Inkling text generation acceptance inside Insula.

The pinned Hugging Face tiny integration checkpoint is untrained. This verifies
checkpoint conversion, tokenization, generation, and the Pascal Triton adapter;
it does not establish quality or full-sized production-model feasibility.
"""
import argparse
from collections import Counter
from contextlib import contextmanager
import json
import os
from pathlib import Path
import time
import traceback

EXPECTED_REVISION = "ee242a48af34ff0729518f4ac28cfcb847e470ab"
EXPECTED_REPO = "hf-internal-testing/tiny-inkling2-mini"
OPERATOR_NAMES = (
    "rmsnorm", "softcap", "swiglu", "message_mix", "topk_route",
    "expert_gate_up", "expert_down", "expert_accum", "attention",
    "dynamic_conv", "conv_prefill", "conv_cache_update",
)

def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, indent=2) + "\n")
    temp.replace(path)

def emit(event, **fields):
    print(json.dumps({"event": event, **fields}), flush=True)

@contextmanager
def adapter_enabled(backend):
    module = backend.model
    target_classes = (
        "Inkling2LoopUnweightedRMSNorm", "Inkling2LoopRMSNorm",
        "Inkling2LoopSoftcap", "Inkling2LoopSoftcappedSwiGLU",
        "Inkling2LoopMessageGate", "Inkling2LoopDynamicShortConvolution",
        "Inkling2LoopTopKRouter", "Inkling2LoopExperts",
    )
    targets = [(getattr(module, name), "forward") for name in target_classes]
    targets += [(module, name) for name in
                ("causal_conv1d_fn", "causal_conv1d_update", "eager_attention_forward")]
    originals = [(owner, name, getattr(owner, name)) for owner, name in targets]
    try:
        backend.install()
        yield
    finally:
        for owner, name, original in originals:
            setattr(owner, name, original)

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--result", type=Path, required=True)
    parser.add_argument("--repeats", type=int, default=2)
    parser.add_argument("--new-tokens", type=int, default=4)
    parser.add_argument("--case", action="append",
                        choices=("chat_short", "plain_short", "window_boundary"))
    parser.add_argument("--process-label", default="unspecified")
    parser.add_argument("--atol", type=float, default=2e-3)
    parser.add_argument("--rtol", type=float, default=1e-4)
    args = parser.parse_args()
    if args.repeats < 1 or args.new_tokens < 2:
        parser.error("repeats must be positive and new-tokens must be at least two")
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    import torch
    import transformers
    import triton
    from transformers import AutoTokenizer, Inkling2LoopForConditionalGeneration
    torch.set_num_threads(2)
    torch.set_float32_matmul_precision("highest")
    torch.backends.cuda.matmul.allow_tf32 = False
    torch.backends.cudnn.allow_tf32 = False
    started = time.monotonic()
    report = {
        "state": "loading", "passed": False, "process_label": args.process_label,
        "checkpoint": EXPECTED_REPO, "revision": EXPECTED_REVISION,
        "trained": False, "scope": "published tiny checkpoint, FP32 text generation",
        "torch": torch.__version__, "transformers": transformers.__version__,
        "triton": triton.__version__,
        "package_paths": {name: module.__file__ for name, module in
                          (("torch", torch), ("transformers", transformers), ("triton", triton))},
        "triton_cache_dir": os.environ.get("TRITON_CACHE_DIR"),
        "new_tokens": args.new_tokens, "repeats_per_gpu": args.repeats,
        "tolerances": {"atol": args.atol, "rtol": args.rtol,
                       "repeat_atol": 0.0, "repeat_rtol": 0.0},
        "cpu_references": [], "runs": [], "stage": "checkpoint_load",
    }
    write_json(args.result, report)
    try:
        emit("loading_checkpoint", checkpoint=str(args.checkpoint))
        acquisition = json.loads((args.checkpoint / "acquisition.json").read_text())
        assert acquisition["complete"], "Incomplete checkpoint acquisition"
        assert acquisition["repo"] == EXPECTED_REPO
        assert acquisition["revision"] == EXPECTED_REVISION
        tokenizer = AutoTokenizer.from_pretrained(str(args.checkpoint), local_files_only=True)
        model, loading = Inkling2LoopForConditionalGeneration.from_pretrained(
            str(args.checkpoint), dtype=torch.float32, attn_implementation="eager",
            local_files_only=True, output_loading_info=True,
        )
        loading = {key: sorted(value) if isinstance(value, set) else value for key, value in loading.items()}
        for field in ("missing_keys", "unexpected_keys", "mismatched_keys", "error_msgs"):
            assert not loading.get(field), (field, loading.get(field))
        model.eval()
        cfg = model.config.text_config
        report.update(
            dtype=str(model.dtype),
            parameters=sum(p.numel() for p in model.parameters()),
            parameter_bytes=sum(p.numel()*p.element_size() for p in model.parameters()),
            physical_layers=cfg.num_hidden_layers, logical_layers=cfg.total_num_hidden_layers,
            sliding_window=cfg.sliding_window, loading_info=loading,
        )
        # No padding, batched requests, image inputs, packed sequences, or caller cache.
        # Every model.generate call creates a fresh request and fresh decoding cache.
        short_chat = tokenizer.apply_chat_template(
            [{"role": "user", "content": "Explain in one sentence why the sky is usually blue."}],
            add_generation_prompt=True, tokenize=True, return_dict=True,
            return_tensors="pt", reasoning_effort="none",
        )
        short_plain = tokenizer("The next number after one, two, three is", return_tensors="pt")
        boundary = tokenizer("one two three four " * 64, return_tensors="pt")
        # A full-window prefill followed by new tokens crosses the sliding boundary.
        width = int(cfg.sliding_window)
        boundary = {key: value[:, :width].contiguous() for key, value in boundary.items()}
        assert boundary["input_ids"].shape[1] == width
        cases = {"chat_short": short_chat, "plain_short": short_plain, "window_boundary": boundary}
        selected = args.case or list(cases)
        cases = {name: cases[name] for name in selected}
        report["cases"] = {
            name: {"prompt_tokens": batch["input_ids"][0].tolist(),
                   "prompt_length": int(batch["input_ids"].shape[1])}
            for name, batch in cases.items()
        }
        report["load_seconds"] = time.monotonic() - started
        emit("loaded", parameters=report["parameters"], cases=selected)

        def generate(batch, use_cache, device):
            gpu = device.type == "cuda"
            inputs = {key: value.to(device) for key, value in batch.items()}
            if gpu:
                torch.cuda.synchronize(device)
                torch.cuda.reset_peak_memory_stats(device)
            begin = time.monotonic()
            with torch.inference_mode():
                output = model.generate(
                    **inputs, max_new_tokens=args.new_tokens, min_new_tokens=args.new_tokens,
                    do_sample=False, use_cache=use_cache, return_dict_in_generate=True,
                    output_logits=True,
                )
            if gpu:
                torch.cuda.synchronize(device)
            duration = time.monotonic() - begin
            sequence = output.sequences.detach().cpu()
            logits = torch.stack([value.detach().float().cpu() for value in output.logits])
            assert logits.shape[0] == args.new_tokens, tuple(logits.shape)
            assert sequence.shape[1] == batch["input_ids"].shape[1] + args.new_tokens
            assert bool(torch.isfinite(logits).all()), "Non-finite generation logits"
            cache = output.cache
            cache_layers = len(cache.layers) if cache is not None else 0
            if use_cache:
                assert cache_layers == cfg.total_num_hidden_layers, cache_layers
            else:
                assert cache is None, "Uncached generation unexpectedly returned a cache"
            peak_bytes = torch.cuda.max_memory_allocated(device) if gpu else None
            del output, inputs, cache
            return {"sequence": sequence, "logits": logits, "seconds": duration,
                    "cache_layers": cache_layers, "peak_allocated_bytes": peak_bytes}

        def compare(actual, expected, *, repeat=False):
            assert torch.equal(actual["sequence"], expected["sequence"]), {
                "actual_tokens": actual["sequence"].tolist(),
                "expected_tokens": expected["sequence"].tolist(),
            }
            atol, rtol = (0.0, 0.0) if repeat else (args.atol, args.rtol)
            torch.testing.assert_close(actual["logits"], expected["logits"], atol=atol, rtol=rtol)
            differences = (actual["logits"] - expected["logits"]).abs()
            return {"max_abs": float(differences.max()),
                    "per_step_max_abs": differences.flatten(1).amax(dim=1).tolist(),
                    "tokens_equal": True}

        def describe(result, name, use_cache):
            count = cases[name]["input_ids"].shape[1]
            tokens = result["sequence"][0, count:].tolist()
            return {"case": name, "use_cache": use_cache, "new_token_ids": tokens,
                    "completion": tokenizer.decode(tokens, skip_special_tokens=False),
                    "seconds": result["seconds"], "cache_layers": result["cache_layers"],
                    "peak_allocated_bytes": result["peak_allocated_bytes"],
                    "all_logits_finite": True}

        # Preserve independent eager CPU results before any adapter monkey patch.
        references = {}
        for name, batch in cases.items():
            for use_cache in (False, True):
                report["stage"] = f"cpu:{name}:cache={use_cache}"
                write_json(args.result, report)
                emit("cpu_start", case=name, use_cache=use_cache)
                result = generate(batch, use_cache, torch.device("cpu"))
                row = describe(result, name, use_cache)
                if use_cache:
                    row["cached_vs_uncached"] = compare(result, references[(name, False)])
                references[(name, use_cache)] = result
                report["cpu_references"].append(row)
                emit("cpu_reference", **row)
        report["state"] = "testing_gpu"
        report["stage"] = "gpu_discovery"
        count = torch.cuda.device_count()
        assert count == 2, f"Expected both Pascal GPUs, observed {count}"
        report["gpus"] = [
            {"index": index, "name": torch.cuda.get_device_name(index),
             "capability": list(torch.cuda.get_device_capability(index))}
            for index in range(count)
        ]
        assert all(item["capability"] == [6, 1] for item in report["gpus"])
        import model_triton_adapter as backend
        totals = Counter()
        gpu0_first = {}
        with adapter_enabled(backend):
            for index in range(count):
                torch.cuda.set_device(index)
                device = torch.device("cuda", index)
                model.to(device)
                first_repeat = {}
                for repeat in range(args.repeats):
                    for name, batch in cases.items():
                        for use_cache in (False, True):
                            report["stage"] = f"gpu{index}:repeat{repeat}:{name}:cache={use_cache}"
                            write_json(args.result, report)
                            emit("gpu_start", gpu=index, repeat=repeat, case=name, use_cache=use_cache)
                            backend.calls.clear()
                            result = generate(batch, use_cache, device)
                            actual_calls = dict(backend.calls)
                            required = OPERATOR_NAMES if use_cache else OPERATOR_NAMES[:-1]
                            assert all(actual_calls.get(op, 0) > 0 for op in required), actual_calls
                            totals.update(actual_calls)
                            row = describe(result, name, use_cache)
                            row.update(gpu=index, repeat=repeat, triton_calls=actual_calls,
                                       vs_cpu=compare(result, references[(name, use_cache)]))
                            key = (name, use_cache)
                            if repeat:
                                row["vs_fresh_repeat"] = compare(result, first_repeat[key], repeat=True)
                            else:
                                first_repeat[key] = result
                            if index == 0 and repeat == 0:
                                gpu0_first[key] = result
                            elif index == 1 and repeat == 0:
                                row["vs_gpu0"] = compare(result, gpu0_first[key])
                            report["runs"].append(row)
                            emit("gpu_pass", **row)
                model.to("cpu")
                torch.cuda.empty_cache()
        assert all(totals.get(op, 0) > 0 for op in OPERATOR_NAMES)
        expected_count = len(cases) * 2 * count * args.repeats
        assert len(report["runs"]) == expected_count
        report.update(state="passed", passed=True, stage="complete", triton_calls=dict(totals),
                      completed_gpu_generations=len(report["runs"]),
                      completed_cpu_generations=len(report["cpu_references"]),
                      elapsed_seconds=time.monotonic()-started)
    except BaseException as exc:
        report.update(state="failed", passed=False, error_type=type(exc).__name__,
                      error=str(exc), traceback=traceback.format_exc(),
                      elapsed_seconds=time.monotonic()-started)
        write_json(args.result, report)
        raise
    write_json(args.result, report)
    emit("complete", state=report["state"],
         gpu_generations=report["completed_gpu_generations"],
         cpu_generations=report["completed_cpu_generations"],
         seconds=report["elapsed_seconds"])

if __name__ == "__main__":
    main()
