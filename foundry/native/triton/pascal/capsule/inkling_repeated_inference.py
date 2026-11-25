#!/usr/bin/env python3
"""Repeated real Inkling text-graph inference on reduced, random FP32 weights.

Run in the Pascal capsule. This validates the adapter, not trained-checkpoint
quality or the packed NVFP4/MXFP8 loader. The process owner supplies GPU leases
and lifecycle supervision; this program does not allocate host resources.
"""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import importlib
import json
import os
from pathlib import Path
import time
import traceback
from typing import Any, Iterator


REQUIRED_CALLS = (
    "rmsnorm", "softcap", "swiglu", "message_mix", "topk_route",
    "expert_gate_up", "expert_down", "expert_accum", "attention",
    "dynamic_conv", "conv_prefill", "conv_cache_update",
)


@dataclass(frozen=True)
class Scenario:
    name: str
    batch: int
    prefill: int


SCENARIOS = (
    Scenario("short", 1, 1),
    Scenario("below_window", 1, 7),
    Scenario("at_window", 1, 8),
    Scenario("above_window", 1, 9),
    Scenario("long", 1, 17),
    Scenario("batched", 2, 9),
)


class AdapterPatches:
    """Temporarily select the adapter while keeping the reference unpatched.

    Construct this before any caller installs the adapter. No inference may
    run concurrently while global model methods are being switched.
    """

    def __init__(self, backend: Any):
        self.backend = backend
        module = backend.model
        names = (
            "Inkling2LoopUnweightedRMSNorm", "Inkling2LoopRMSNorm",
            "Inkling2LoopSoftcap", "Inkling2LoopSoftcappedSwiGLU",
            "Inkling2LoopMessageGate", "Inkling2LoopDynamicShortConvolution",
            "Inkling2LoopTopKRouter", "Inkling2LoopExperts",
        )
        self.targets = [(getattr(module, name), "forward") for name in names]
        self.targets += [(module, name) for name in (
            "causal_conv1d_fn", "causal_conv1d_update", "eager_attention_forward",
        )]
        self.originals = [getattr(owner, name) for owner, name in self.targets]
        try:
            backend.install()
            self.patched = [getattr(owner, name) for owner, name in self.targets]
        finally:
            self._apply(self.originals)

    def _apply(self, methods: list[Any]) -> None:
        for (owner, name), method in zip(self.targets, methods, strict=True):
            setattr(owner, name, method)

    @contextmanager
    def enabled(self) -> Iterator[None]:
        self._apply(self.patched)
        try:
            yield
        finally:
            self._apply(self.originals)


@dataclass
class GreedyResult:
    sequences: Any
    logits: Any
    cache: Any


def generate_greedy(model: Any, prompt: Any, new_tokens: int) -> GreedyResult:
    """Generate a fixed number of greedy tokens with a new cache per call.

    EOS stopping is intentionally disabled for a repeatable decode workload.
    Inputs are unpadded token IDs. This exercises the model's forward/cache
    API, not GenerationMixin's separate sampling and stopping machinery.
    """
    import torch

    if new_tokens < 1:
        raise ValueError("new_tokens must be positive")
    cache = None
    step_input = prompt
    sequences = prompt
    logits = []
    for _ in range(new_tokens):
        out = model(step_input, cache=cache, use_cache=True)
        step_logits = out.logits[:, -1, :]
        token = step_logits.argmax(dim=-1, keepdim=True)
        logits.append(step_logits)
        sequences = torch.cat((sequences, token), dim=1)
        step_input, cache = token, out.cache
    return GreedyResult(sequences, torch.stack(logits, dim=1), cache)


def cache_snapshot(cache: Any) -> dict[str, Any]:
    """Copy actual attention and convolution states for endpoint comparisons."""
    import torch

    result: dict[str, Any] = {}
    for index, layer in enumerate(cache.layers):
        for name in ("keys", "values", "conv_states", "recurrent_states"):
            value = getattr(layer, name, None)
            if isinstance(value, torch.Tensor):
                result[f"{index}/{name}"] = value.detach().cpu().clone()
            elif isinstance(value, dict):
                for slot, tensor in value.items():
                    if isinstance(tensor, torch.Tensor):
                        result[f"{index}/{name}/{slot}"] = tensor.detach().cpu().clone()
    if not result:
        raise AssertionError("no attention or convolution tensors found in model cache")
    return result


def check_close(actual: Any, expected: Any, label: str, *, atol: float, rtol: float) -> float:
    import torch

    a, b = actual.detach().cpu(), expected.detach().cpu()
    if not torch.isfinite(a).all() or not torch.isfinite(b).all():
        raise AssertionError(f"{label}: nonfinite values")
    torch.testing.assert_close(a, b, atol=atol, rtol=rtol, msg=lambda message: f"{label}: {message}")
    return float((a.float() - b.float()).abs().max()) if a.numel() else 0.0


def compare_caches(actual: dict[str, Any], expected: dict[str, Any], label: str) -> float:
    if actual.keys() != expected.keys():
        raise AssertionError(f"{label}: different cache tensors: {sorted(actual)} / {sorted(expected)}")
    return max(check_close(actual[key], expected[key], f"{label}/{key}", atol=5e-4, rtol=5e-4)
               for key in actual)


def cached_chunks(model: Any, tokens: Any, prefill: int) -> tuple[Any, Any]:
    import torch

    # Exercise both one-token in-place conv updates and multi-token continuation.
    lengths = [prefill, 1, 2, 1]
    if sum(lengths) != tokens.shape[1]:
        raise AssertionError("invalid chunk plan")
    outputs, cache, position = [], None, 0
    for length in lengths:
        out = model(tokens[:, position:position + length], cache=cache, use_cache=True)
        cache = out.cache
        outputs.append(out.logits)
        position += length
    return torch.cat(outputs, dim=1), cache


def make_model(seed: int) -> Any:
    import torch
    from transformers.models.inkling2_loop.configuration_inkling2_loop import Inkling2LoopTextConfig
    from transformers.models.inkling2_loop.modeling_inkling2_loop import Inkling2LoopForCausalLM

    torch.manual_seed(seed)
    config = Inkling2LoopTextConfig(
        vocab_size=128, unpadded_vocab_size=128, hidden_size=64,
        num_hidden_layers=6, num_attention_heads=4, num_key_value_heads=2,
        head_dim=16, swa_num_attention_heads=4, swa_num_key_value_heads=2,
        swa_head_dim=16, sliding_window_size=8, d_rel=4, rel_extent=16,
        moe_intermediate_size=32, n_routed_experts=8, num_experts_per_tok=2,
        n_layers_per_attn_res_block=2, loop_layer_index_start=2,
        loop_layer_index_end=4, loop_num_passes=3, loop_gate_rank=8,
        layer_types=["hybrid_sliding", "hybrid"] * 3,
        eos_token_id=127, pad_token_id=0,
    )
    config._attn_implementation = "eager"
    return Inkling2LoopForCausalLM(config).eval()


def tokens_for(seed: int, case: Scenario) -> Any:
    import torch

    generator = torch.Generator().manual_seed(seed + 1000 * case.prefill + 100 * case.batch)
    return torch.randint(1, 127, (case.batch, case.prefill + 4), generator=generator)


def reference_case(model: Any, tokens: Any, case: Scenario, new_tokens: int) -> dict[str, Any]:
    full = model(tokens, use_cache=True)
    uncached = model(tokens, use_cache=False).logits
    check_close(full.logits, uncached, "reference full cached/uncached", atol=5e-4, rtol=5e-4)
    chunks, cache = cached_chunks(model, tokens, case.prefill)
    chunk_error = check_close(chunks, full.logits, "reference cached/full", atol=5e-4, rtol=5e-4)
    full_cache = cache_snapshot(full.cache)
    cache_error = compare_caches(cache_snapshot(cache), full_cache, "reference cached/full cache")
    generated = generate_greedy(model, tokens[:, :case.prefill], new_tokens)
    generation_full = model(generated.sequences[:, :-1], use_cache=False).logits[:, case.prefill - 1:]
    check_close(generated.logits, generation_full, "reference generation cached/uncached", atol=5e-4, rtol=5e-4)
    return {
        "full": full.logits.detach().clone(), "cache": full_cache,
        "generation": generated, "chunk_error": chunk_error, "cache_error": cache_error,
    }


def gpu_case(model: Any, tokens: Any, case: Scenario, reference: dict[str, Any],
             backend: Any, new_tokens: int, warm_repeats: int) -> dict[str, Any]:
    import torch

    backend.calls.clear()
    full = model(tokens, use_cache=True)
    result: dict[str, Any] = {
        "full_logit_max_abs_error": check_close(full.logits, reference["full"], "GPU/reference logits", atol=3e-4, rtol=3e-4),
        "cache_reference_max_abs_error": compare_caches(cache_snapshot(full.cache), reference["cache"], "GPU/reference cache"),
    }
    chunks, cache = cached_chunks(model, tokens, case.prefill)
    result["chunk_full_max_abs_error"] = check_close(chunks, full.logits, "GPU cached/full logits", atol=5e-4, rtol=5e-4)
    result["chunk_reference_max_abs_error"] = check_close(chunks, reference["full"], "GPU cached/reference logits", atol=5e-4, rtol=5e-4)
    result["cache_chunk_full_max_abs_error"] = compare_caches(cache_snapshot(cache), cache_snapshot(full.cache), "GPU cached/full cache")
    if len(cache.layers) != model.config.total_num_hidden_layers:
        raise AssertionError("loop cache layer count differs from logical model depth")
    result["cache_layers"] = len(cache.layers)
    generations = []
    first = None
    for repeat in range(warm_repeats):
        generated = generate_greedy(model, tokens[:, :case.prefill], new_tokens)
        error = check_close(generated.logits, reference["generation"].logits, "GPU/reference generated logits", atol=5e-4, rtol=5e-4)
        sequences = generated.sequences.cpu()
        if not torch.equal(sequences, reference["generation"].sequences):
            raise AssertionError(f"generated token mismatch: GPU={sequences.tolist()}, reference={reference['generation'].sequences.tolist()}")
        if first is not None:
            if not torch.equal(sequences, first.sequences.cpu()) or not torch.equal(generated.logits, first.logits):
                raise AssertionError("fresh-cache warm repeat changed tokens or logits")
            compare_caches(cache_snapshot(generated.cache), cache_snapshot(first.cache), "warm generation cache")
        else:
            first = generated
        generations.append({"repeat": repeat, "logit_max_abs_error": error,
                            "generated_tokens": sequences[:, case.prefill:].tolist()})
    missing = [name for name in REQUIRED_CALLS if not backend.calls[name]]
    if missing:
        raise AssertionError(f"required Triton operations were not invoked: {missing}")
    result.update(generations=generations, triton_calls=dict(backend.calls),
                  warm_repeat_logits_exact=True, generation_tokens_exact=True)
    return result


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n")
    os.replace(temporary, path)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--seeds", default="101,202,303")
    parser.add_argument("--gpus", default="0,1", help="Logical GPU indices; all visible GPUs must be included")
    parser.add_argument("--cases", default=",".join(case.name for case in SCENARIOS))
    parser.add_argument("--new-tokens", type=int, default=4)
    parser.add_argument("--warm-repeats", type=int, default=2)
    parser.add_argument("--adapter", default="model_triton_adapter")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    state: dict[str, Any] = {
        "state": "initializing", "passed": False, "results": [],
        "scope": "reduced random FP32 Inkling2Loop text graph; fixed-step greedy forward/cache inference",
        "trained_checkpoint": False, "padding": False, "quantized_checkpoint_loader": False,
        "start_utc": datetime.now(timezone.utc).isoformat(),
        "process_id": os.getpid(), "warm_repeats": args.warm_repeats,
        "new_tokens": args.new_tokens,
        "tolerances": {"full_cpu": {"atol": 3e-4, "rtol": 3e-4},
                       "cache_chunks_generation": {"atol": 5e-4, "rtol": 5e-4},
                       "warm_repeat": {"atol": 0.0, "rtol": 0.0}},
    }
    started = time.monotonic()
    def save(event: str) -> None:
        state["updated_utc"] = datetime.now(timezone.utc).isoformat()
        state["elapsed_seconds"] = time.monotonic() - started
        atomic_json(args.output, state)
        print(json.dumps({"event": event, "state": state["state"], "completed": len(state["results"]),
                          "current": state.get("current"), "elapsed_seconds": round(state["elapsed_seconds"], 3)}), flush=True)
    save("starting")
    try:
        import torch
        import triton
        import transformers
        torch.set_num_threads(1)
        seeds = [int(seed) for seed in args.seeds.split(",")]
        gpus = [int(gpu) for gpu in args.gpus.split(",")]
        chosen = args.cases.split(",")
        by_name = {case.name: case for case in SCENARIOS}
        if not seeds or len(set(seeds)) != len(seeds):
            raise ValueError("seeds must be nonempty and unique")
        if not chosen or any(name not in by_name for name in chosen) or len(set(chosen)) != len(chosen):
            raise ValueError(f"cases must be unique names from {list(by_name)}")
        cases = [by_name[name] for name in chosen]
        if args.new_tokens < 2 or args.warm_repeats < 2:
            raise ValueError("use at least two new tokens and two warm repeats")
        count = torch.cuda.device_count()
        if count != 2 or sorted(gpus) != list(range(count)):
            raise RuntimeError(f"qualification requires both Pascal GPUs, visible={count}, selected={gpus}")
        devices = []
        for gpu in gpus:
            if torch.cuda.get_device_capability(gpu) != (6, 1):
                raise RuntimeError(f"GPU {gpu} is not sm_61")
            devices.append({"logical_id": gpu, "name": torch.cuda.get_device_name(gpu), "capability": [6, 1]})
        backend = importlib.import_module(args.adapter)
        patches = AdapterPatches(backend)
        state.update(versions={"torch": torch.__version__, "triton": triton.__version__, "transformers": transformers.__version__},
                     devices=devices, seeds=seeds, scenarios=[case.__dict__ for case in cases],
                     expected_cases=len(seeds) * len(cases) * len(gpus),
                     source_paths={"torch": torch.__file__, "triton": triton.__file__, "transformers": transformers.__file__, "adapter": backend.__file__},
                     physical_layers=6, logical_layers=10, sliding_window=8)
        with torch.inference_mode():
            for seed in seeds:
                model = make_model(seed)
                references = {}
                for case in cases:
                    state.update(state="reference", current={"seed": seed, "case": case.name, "device": "cpu"})
                    save("reference-start")
                    tokens = tokens_for(seed, case)
                    references[case.name] = reference_case(model, tokens, case, args.new_tokens)
                for gpu in gpus:
                    torch.cuda.set_device(gpu)
                    device = torch.device("cuda", gpu)
                    model.to(device)
                    with patches.enabled():
                        for case in cases:
                            state.update(state="inference", current={"seed": seed, "case": case.name, "device": gpu})
                            save("case-start")
                            before = time.monotonic()
                            torch.cuda.reset_peak_memory_stats(gpu)
                            details = gpu_case(model, tokens_for(seed, case).to(device), case, references[case.name], backend, args.new_tokens, args.warm_repeats)
                            torch.cuda.synchronize(gpu)
                            result = {"seed": seed, "case": case.name, "gpu": gpu,
                                      "batch": case.batch, "prefill": case.prefill,
                                      "forced_tokens": case.prefill + 4, "chunks": [case.prefill, 1, 2, 1],
                                      "seconds": time.monotonic() - before,
                                      "peak_allocated_bytes": torch.cuda.max_memory_allocated(gpu),
                                      "reference_chunk_max_abs_error": references[case.name]["chunk_error"],
                                      "passed": True, **details}
                            state["results"].append(result)
                            save("case-passed")
                    model.to("cpu")
                del references, model
        if len(state["results"]) != state["expected_cases"]:
            raise AssertionError("incomplete case matrix")
        counts: Counter[str] = Counter()
        for result in state["results"]:
            counts.update(result["triton_calls"])
        state.update(state="passed", passed=True, total_triton_calls=dict(counts),
                     generation_requests=len(state["results"]) * args.warm_repeats,
                     generated_sequences=sum(result["batch"] for result in state["results"]) * args.warm_repeats)
        state.pop("current", None)
        save("complete")
        return 0
    except BaseException as error:
        state.update(state="failed", error_type=type(error).__name__, error=str(error), traceback=traceback.format_exc())
        save("failed")
        raise


if __name__ == "__main__":
    raise SystemExit(main())
