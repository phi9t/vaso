#!/usr/bin/env python3
"""Torch live training workloads W1a through W1d."""

from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import time
from pathlib import Path
from typing import Any


VOCAB_SIZE = 128
SEQ_LEN = 128
MODEL_DIM = 512
N_HEADS = 8
N_LAYERS = 7
COMPILE_COMPARE_STEPS = 20
COMPILE_LOSS_PREFIX_STEPS = 16
W1_STEPS = 300
DDP_STEPS = 300
FSDP_PRE_SAVE_STEPS = 120
FSDP_POST_SAVE_STEPS = 120
LOSS_DROP_RATIO = 0.50
COMPILE_LOSS_ATOL = 5.0e-2
PARAMETER_ATOL = 0.0
RESUME_LOSS_ATOL = 5.0e-2
EXPECTED_NCCL_VERSION = (2, 30, 7)


def _finite(value: object) -> bool:
    return isinstance(value, (int, float)) and math.isfinite(float(value))


def _series_is_finite(values: object) -> bool:
    return isinstance(values, list) and bool(values) and all(_finite(value) for value in values)


def _loss_drop(initial: object, final: object, ratio: float = LOSS_DROP_RATIO) -> bool:
    return _finite(initial) and _finite(final) and float(final) <= float(initial) * ratio


def _criteria_result(
    workload_id: str,
    metrics: dict[str, Any],
    checks: dict[str, bool],
) -> dict[str, object]:
    failed = [name for name, passed in checks.items() if not passed]
    return {
        "id": workload_id,
        "verdict": "failed" if failed else "passed",
        "failed_criteria": failed,
        "criteria": checks,
        "metrics": metrics,
    }


def evaluate_w1a(metrics: dict[str, Any]) -> dict[str, object]:
    checks = {
        "loss_drop": _loss_drop(metrics.get("initial_loss"), metrics.get("final_loss")),
        "finite_losses": _series_is_finite(metrics.get("losses")),
        "tokens_per_second_recorded": _finite(metrics.get("tokens_per_second"))
        and float(metrics["tokens_per_second"]) > 0.0,
    }
    return _criteria_result("W1a", metrics, checks)


def evaluate_w1b(metrics: dict[str, Any]) -> dict[str, object]:
    eager_losses = metrics.get("eager_losses")
    compiled_losses = metrics.get("compiled_losses")
    tolerance = float(metrics.get("loss_tolerance", COMPILE_LOSS_ATOL))
    prefix_steps = int(metrics.get("loss_prefix_steps", COMPILE_LOSS_PREFIX_STEPS))
    loss_prefix_match = False
    finite_compare_losses = _series_is_finite(eager_losses) and _series_is_finite(compiled_losses)
    if _series_is_finite(eager_losses) and _series_is_finite(compiled_losses):
        paired = list(zip(eager_losses, compiled_losses))
        if prefix_steps > 0 and len(paired) >= prefix_steps:
            loss_prefix_match = all(abs(float(a) - float(b)) <= tolerance for a, b in paired[:prefix_steps])
    checks = {
        "compile_succeeded": metrics.get("compile_succeeded") is True,
        "loss_prefix_tolerance": loss_prefix_match,
        "full_compiled_loss_drop": _loss_drop(
            metrics.get("full_compiled_initial_loss"),
            metrics.get("full_compiled_final_loss"),
        ),
        "finite_losses": finite_compare_losses
        and _finite(metrics.get("full_compiled_initial_loss"))
        and _finite(metrics.get("full_compiled_final_loss")),
        "speedup_recorded": _finite(metrics.get("speedup")),
    }
    return _criteria_result("W1b", metrics, checks)


def evaluate_w1c(metrics: dict[str, Any]) -> dict[str, object]:
    rank_results = metrics.get("rank_results")
    world_size = int(metrics.get("world_size", 0) or 0)
    all_ranks = isinstance(rank_results, list) and len(rank_results) == world_size == 8
    all_finite = all_ranks and all(bool(result.get("finite")) for result in rank_results)
    all_drop = all_ranks and all(
        _loss_drop(result.get("initial_loss"), result.get("final_loss")) for result in rank_results
    )
    checks = {
        "all_ranks_finished": all_ranks,
        "loss_drop": all_drop,
        "finite_losses": all_finite,
        "parameter_consistency": _finite(metrics.get("max_parameter_delta"))
        and float(metrics["max_parameter_delta"]) <= PARAMETER_ATOL,
        "nccl_version": tuple(metrics.get("nccl_version", ())) == EXPECTED_NCCL_VERSION,
    }
    return _criteria_result("W1c", metrics, checks)


def evaluate_w1d(metrics: dict[str, Any]) -> dict[str, object]:
    saved_loss = metrics.get("saved_loss")
    resume_first_loss = metrics.get("resume_first_loss")
    post_resume_final_loss = metrics.get("post_resume_final_loss")
    tolerance = float(metrics.get("resume_tolerance", RESUME_LOSS_ATOL))
    checks = {
        "resume_continuity": _finite(saved_loss)
        and _finite(resume_first_loss)
        and abs(float(saved_loss) - float(resume_first_loss)) <= tolerance,
        "post_resume_progress": _finite(saved_loss)
        and _finite(post_resume_final_loss)
        and float(post_resume_final_loss) < float(saved_loss),
        "finite_losses": metrics.get("finite") is True,
        "world_size": int(metrics.get("world_size", 0) or 0) == 8,
    }
    return _criteria_result("W1d", metrics, checks)


def _write_json(path: Path, doc: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_json_safe(doc), indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")


def _json_safe(value: Any) -> Any:
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, tuple):
        return [_json_safe(item) for item in value]
    return value


def _torch_imports():
    import torch
    import torch.nn as nn
    import torch.nn.functional as F

    return torch, nn, F


def _require_cuda(torch: Any, gpus: int) -> None:
    if not torch.cuda.is_available():
        raise RuntimeError("CUDA is not available")
    available = torch.cuda.device_count()
    if available < gpus:
        raise RuntimeError(f"requested {gpus} GPU(s), but torch sees {available}")


def _runtime_metadata(torch: Any) -> dict[str, object]:
    return {
        "torch_version": getattr(torch, "__version__", ""),
        "torch_cuda_version": getattr(torch.version, "cuda", ""),
        "device_name": torch.cuda.get_device_name(0),
        "device_capability": list(torch.cuda.get_device_capability(0)),
    }


def _seed_everything(torch: Any, seed: int) -> None:
    torch.manual_seed(seed)
    torch.cuda.manual_seed_all(seed)


def _make_batch(torch: Any, batch_size: int, step: int, rank: int = 0, device: str = "cuda") -> tuple[Any, Any]:
    offsets = torch.arange(SEQ_LEN + 1, device=device, dtype=torch.long)
    starts = (
        torch.arange(batch_size, device=device, dtype=torch.long)[:, None] * 17
        + step * 13
        + rank * 7
    )
    tokens = (starts + offsets[None, :]) % VOCAB_SIZE
    return tokens[:, :-1].contiguous(), tokens[:, 1:].contiguous()


def _autocast_context(torch: Any):
    return torch.autocast(device_type="cuda", dtype=torch.bfloat16)


def _sdpa_context(torch: Any):
    if hasattr(torch.nn, "attention") and hasattr(torch.nn.attention, "sdpa_kernel"):
        return torch.nn.attention.sdpa_kernel(torch.nn.attention.SDPBackend.FLASH_ATTENTION)
    return torch.backends.cuda.sdp_kernel(enable_flash=True, enable_math=False, enable_mem_efficient=False)


def _build_model(torch: Any, nn: Any, F: Any, *, fused_activation: Any | None = None):
    class CausalSelfAttention(nn.Module):
        def __init__(self) -> None:
            super().__init__()
            self.qkv = nn.Linear(MODEL_DIM, 3 * MODEL_DIM, bias=False)
            self.proj = nn.Linear(MODEL_DIM, MODEL_DIM, bias=False)

        def forward(self, x):
            batch, seq, channels = x.shape
            qkv = self.qkv(x)
            q, k, v = qkv.split(channels, dim=2)
            q = q.view(batch, seq, N_HEADS, channels // N_HEADS).transpose(1, 2)
            k = k.view(batch, seq, N_HEADS, channels // N_HEADS).transpose(1, 2)
            v = v.view(batch, seq, N_HEADS, channels // N_HEADS).transpose(1, 2)
            with _sdpa_context(torch):
                y = F.scaled_dot_product_attention(q, k, v, is_causal=True)
            y = y.transpose(1, 2).contiguous().view(batch, seq, channels)
            return self.proj(y)

    class MLP(nn.Module):
        def __init__(self) -> None:
            super().__init__()
            self.fc = nn.Linear(MODEL_DIM, 4 * MODEL_DIM, bias=False)
            self.proj = nn.Linear(4 * MODEL_DIM, MODEL_DIM, bias=False)

        def forward(self, x):
            x = self.fc(x)
            x = fused_activation(x) if fused_activation is not None else F.gelu(x)
            return self.proj(x)

    class Block(nn.Module):
        def __init__(self) -> None:
            super().__init__()
            self.ln1 = nn.LayerNorm(MODEL_DIM)
            self.attn = CausalSelfAttention()
            self.ln2 = nn.LayerNorm(MODEL_DIM)
            self.mlp = MLP()

        def forward(self, x):
            x = x + self.attn(self.ln1(x))
            x = x + self.mlp(self.ln2(x))
            return x

    class TinyGPT(nn.Module):
        def __init__(self) -> None:
            super().__init__()
            self.tok = nn.Embedding(VOCAB_SIZE, MODEL_DIM)
            self.pos = nn.Embedding(SEQ_LEN, MODEL_DIM)
            self.blocks = nn.ModuleList([Block() for _ in range(N_LAYERS)])
            self.ln = nn.LayerNorm(MODEL_DIM)
            self.head = nn.Linear(MODEL_DIM, VOCAB_SIZE, bias=False)

        def forward(self, idx, targets=None):
            positions = torch.arange(idx.shape[1], device=idx.device)
            x = self.tok(idx) + self.pos(positions)[None, :, :]
            for block in self.blocks:
                x = block(x)
            logits = self.head(self.ln(x))
            if targets is None:
                return logits
            return F.cross_entropy(logits.view(-1, logits.size(-1)), targets.reshape(-1))

    return TinyGPT()


def _new_model_and_optimizer(
    torch: Any,
    nn: Any,
    F: Any,
    *,
    device: str = "cuda",
    fused_activation: Any | None = None,
    seed: int = 1234,
):
    _seed_everything(torch, seed)
    model = _build_model(torch, nn, F, fused_activation=fused_activation).to(device)
    optimizer = torch.optim.AdamW(model.parameters(), lr=3.0e-3, weight_decay=0.01)
    return model, optimizer


def _train_step(torch: Any, model: Any, optimizer: Any, batch: tuple[Any, Any]) -> float:
    x, y = batch
    optimizer.zero_grad(set_to_none=True)
    with _autocast_context(torch):
        loss = model(x, y)
    loss.backward()
    torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
    optimizer.step()
    return float(loss.detach().cpu())


def _eval_loss(torch: Any, model: Any, batch: tuple[Any, Any]) -> float:
    x, y = batch
    model.eval()
    with torch.no_grad(), _autocast_context(torch):
        value = float(model(x, y).detach().cpu())
    model.train()
    return value


def _train_series(
    torch: Any,
    model: Any,
    optimizer: Any,
    *,
    steps: int,
    batch_size: int,
    rank: int = 0,
) -> tuple[list[float], float]:
    losses: list[float] = []
    tokens = batch_size * SEQ_LEN * steps
    start = time.perf_counter()
    for step in range(steps):
        losses.append(_train_step(torch, model, optimizer, _make_batch(torch, batch_size, step, rank)))
    torch.cuda.synchronize()
    elapsed = time.perf_counter() - start
    return losses, tokens / elapsed


def _losses_finite(losses: list[float]) -> bool:
    return bool(losses) and all(math.isfinite(loss) for loss in losses)


def run_w1a(args: argparse.Namespace) -> dict[str, object]:
    torch, nn, F = _torch_imports()
    _require_cuda(torch, 1)
    torch.cuda.set_device(0)
    model, optimizer = _new_model_and_optimizer(torch, nn, F)
    losses, tokens_per_second = _train_series(torch, model, optimizer, steps=W1_STEPS, batch_size=24)
    metrics = {
        **_runtime_metadata(torch),
        "steps": W1_STEPS,
        "parameters": sum(param.numel() for param in model.parameters()),
        "initial_loss": losses[0],
        "final_loss": losses[-1],
        "losses": losses,
        "tokens_per_second": tokens_per_second,
        "dtype": "bf16_autocast",
        "sdpa_backend": "flash",
        "optimizer": "AdamW",
        "grad_clip_norm": 1.0,
    }
    return evaluate_w1a(metrics)


def _run_compare_series(torch: Any, nn: Any, F: Any, *, compile_model: bool) -> tuple[list[float], float]:
    model, optimizer = _new_model_and_optimizer(torch, nn, F, seed=4321)
    if compile_model:
        model = torch.compile(model)
    return _train_series(torch, model, optimizer, steps=COMPILE_COMPARE_STEPS, batch_size=16)


def run_w1b(args: argparse.Namespace) -> dict[str, object]:
    torch, nn, F = _torch_imports()
    _require_cuda(torch, 1)
    torch.cuda.set_device(0)
    eager_losses, eager_tokens_per_second = _run_compare_series(torch, nn, F, compile_model=False)
    compile_succeeded = True
    compiled_losses: list[float] = []
    compiled_tokens_per_second = 0.0
    full_compiled_losses: list[float] = []
    full_compiled_tokens_per_second = 0.0
    compile_error = ""
    try:
        compiled_losses, compiled_tokens_per_second = _run_compare_series(torch, nn, F, compile_model=True)
        model, optimizer = _new_model_and_optimizer(torch, nn, F, seed=1234)
        model = torch.compile(model)
        full_compiled_losses, full_compiled_tokens_per_second = _train_series(
            torch,
            model,
            optimizer,
            steps=W1_STEPS,
            batch_size=24,
        )
    except Exception as exc:  # pragma: no cover - exercised only in live runtime failure
        compile_succeeded = False
        compile_error = f"{type(exc).__name__}: {exc}"
    metrics = {
        **_runtime_metadata(torch),
        "compare_steps": COMPILE_COMPARE_STEPS,
        "steps": W1_STEPS,
        "compile_succeeded": compile_succeeded,
        "compile_error": compile_error,
        "eager_losses": eager_losses,
        "compiled_losses": compiled_losses,
        "full_compiled_initial_loss": full_compiled_losses[0] if full_compiled_losses else None,
        "full_compiled_final_loss": full_compiled_losses[-1] if full_compiled_losses else None,
        "loss_tolerance": COMPILE_LOSS_ATOL,
        "loss_prefix_steps": COMPILE_LOSS_PREFIX_STEPS,
        "eager_tokens_per_second": eager_tokens_per_second,
        "compiled_tokens_per_second": compiled_tokens_per_second,
        "full_compiled_tokens_per_second": full_compiled_tokens_per_second,
        "speedup": compiled_tokens_per_second / eager_tokens_per_second if eager_tokens_per_second else 0.0,
    }
    return evaluate_w1b(metrics)


def _distributed_init_method(run_dir: Path, name: str) -> str:
    rendezvous = (run_dir / f"{name}.rdzv").resolve(strict=False)
    if rendezvous.exists():
        rendezvous.unlink()
    return "file://" + str(rendezvous)


def _rank_result_path(run_dir: Path, workload_id: str, rank: int) -> Path:
    return run_dir / f"{workload_id}.rank{rank}.json"


def _write_rank_result(run_dir: Path, workload_id: str, rank: int, doc: dict[str, object]) -> None:
    _write_json(_rank_result_path(run_dir, workload_id, rank), doc)


def _read_rank_results(run_dir: Path, workload_id: str, world_size: int) -> list[dict[str, object]]:
    results: list[dict[str, object]] = []
    for rank in range(world_size):
        path = _rank_result_path(run_dir, workload_id, rank)
        if path.exists():
            results.append(json.loads(path.read_text(encoding="utf-8")))
    return results


def _ddp_worker(rank: int, world_size: int, run_dir_raw: str, init_method: str) -> None:
    import torch
    import torch.distributed as dist
    from torch.nn.parallel import DistributedDataParallel

    torch.cuda.set_device(rank)
    dist.init_process_group("nccl", init_method=init_method, rank=rank, world_size=world_size)
    try:
        _, nn, F = _torch_imports()
        model, optimizer = _new_model_and_optimizer(torch, nn, F, device=f"cuda:{rank}", seed=5678)
        model = DistributedDataParallel(model, device_ids=[rank], output_device=rank)
        losses, tokens_per_second = _train_series(
            torch,
            model,
            optimizer,
            steps=DDP_STEPS,
            batch_size=12,
            rank=rank,
        )
        max_delta = torch.zeros((), device=f"cuda:{rank}")
        for param in model.parameters():
            reference = param.detach().clone()
            dist.broadcast(reference, src=0)
            max_delta = torch.maximum(max_delta, (param.detach() - reference).abs().max())
        dist.all_reduce(max_delta, op=dist.ReduceOp.MAX)
        _write_rank_result(
            Path(run_dir_raw),
            "W1c",
            rank,
            {
                "rank": rank,
                "initial_loss": losses[0],
                "final_loss": losses[-1],
                "finite": _losses_finite(losses),
                "tokens_per_second": tokens_per_second,
                "max_parameter_delta": float(max_delta.detach().cpu()),
                "nccl_version": list(torch.cuda.nccl.version()),
            },
        )
    finally:
        dist.destroy_process_group()


def run_w1c(args: argparse.Namespace) -> dict[str, object]:
    torch, _, _ = _torch_imports()
    _require_cuda(torch, args.gpus)
    import torch.multiprocessing as mp

    for rank_path in args.run_dir.glob("W1c.rank*.json"):
        rank_path.unlink()
    init_method = _distributed_init_method(args.run_dir, "w1c")
    mp.spawn(_ddp_worker, args=(args.gpus, str(args.run_dir), init_method), nprocs=args.gpus, join=True)
    rank_results = _read_rank_results(args.run_dir, "W1c", args.gpus)
    max_delta = max((float(result.get("max_parameter_delta", math.inf)) for result in rank_results), default=None)
    nccl_version = list(rank_results[0].get("nccl_version", [])) if rank_results else []
    metrics = {
        **_runtime_metadata(torch),
        "world_size": args.gpus,
        "steps": DDP_STEPS,
        "rank_results": rank_results,
        "max_parameter_delta": max_delta,
        "nccl_version": nccl_version,
    }
    return evaluate_w1c(metrics)


def _fsdp_worker(rank: int, world_size: int, run_dir_raw: str, init_method: str) -> None:
    import torch
    import torch.distributed as dist
    import torch.distributed.checkpoint as dcp
    from torch.distributed.checkpoint.state_dict import get_state_dict, set_state_dict
    from torch.distributed.device_mesh import init_device_mesh
    from torch.distributed.fsdp import MixedPrecisionPolicy, fully_shard

    torch.cuda.set_device(rank)
    dist.init_process_group("nccl", init_method=init_method, rank=rank, world_size=world_size)
    try:
        _, nn, F = _torch_imports()

        def make_fsdp_model(seed: int):
            model, optimizer = _new_model_and_optimizer(torch, nn, F, device=f"cuda:{rank}", seed=seed)
            mesh = init_device_mesh("cuda", (world_size,))
            mp_policy = MixedPrecisionPolicy(param_dtype=torch.bfloat16, reduce_dtype=torch.float32)
            for block in model.blocks:
                fully_shard(block, mesh=mesh, mp_policy=mp_policy)
            fully_shard(model, mesh=mesh, mp_policy=mp_policy, reshard_after_forward=False)
            optimizer = torch.optim.AdamW(model.parameters(), lr=3.0e-3, weight_decay=0.01)
            return model, optimizer

        run_dir = Path(run_dir_raw)
        checkpoint_dir = run_dir / "checkpoint"
        model, optimizer = make_fsdp_model(seed=6789)
        pre_losses, _ = _train_series(
            torch,
            model,
            optimizer,
            steps=FSDP_PRE_SAVE_STEPS,
            batch_size=8,
            rank=rank,
        )
        eval_batch = _make_batch(torch, 8, FSDP_PRE_SAVE_STEPS, rank, device=f"cuda:{rank}")
        saved_loss = _eval_loss(torch, model, eval_batch)
        model_state, optim_state = get_state_dict(model, optimizers=optimizer)
        dcp.save({"model": model_state, "optimizer": optim_state}, checkpoint_id=str(checkpoint_dir))
        dist.barrier()

        model2, optimizer2 = make_fsdp_model(seed=9876)
        model_state2, optim_state2 = get_state_dict(model2, optimizers=optimizer2)
        state = {"model": model_state2, "optimizer": optim_state2}
        dcp.load(state, checkpoint_id=str(checkpoint_dir))
        set_state_dict(
            model2,
            optimizer2,
            model_state_dict=state["model"],
            optim_state_dict=state["optimizer"],
        )
        resume_first_loss = _eval_loss(torch, model2, eval_batch)
        post_losses, _ = _train_series(
            torch,
            model2,
            optimizer2,
            steps=FSDP_POST_SAVE_STEPS,
            batch_size=8,
            rank=rank,
        )
        rank_doc = {
            "rank": rank,
            "pre_initial_loss": pre_losses[0],
            "saved_loss": saved_loss,
            "resume_first_loss": resume_first_loss,
            "post_resume_final_loss": post_losses[-1],
            "finite": _losses_finite(pre_losses) and _losses_finite(post_losses),
        }
        _write_rank_result(run_dir, "W1d", rank, rank_doc)
    finally:
        dist.destroy_process_group()


def run_w1d(args: argparse.Namespace) -> dict[str, object]:
    torch, _, _ = _torch_imports()
    _require_cuda(torch, args.gpus)
    import torch.multiprocessing as mp

    for rank_path in args.run_dir.glob("W1d.rank*.json"):
        rank_path.unlink()
    checkpoint_dir = args.run_dir / "checkpoint"
    if checkpoint_dir.exists():
        shutil.rmtree(checkpoint_dir)
    init_method = _distributed_init_method(args.run_dir, "w1d")
    mp.spawn(_fsdp_worker, args=(args.gpus, str(args.run_dir), init_method), nprocs=args.gpus, join=True)
    rank_results = _read_rank_results(args.run_dir, "W1d", args.gpus)
    rank0 = rank_results[0] if rank_results else {}
    metrics = {
        **_runtime_metadata(torch),
        "world_size": args.gpus,
        "pre_save_steps": FSDP_PRE_SAVE_STEPS,
        "post_save_steps": FSDP_POST_SAVE_STEPS,
        "rank_results": rank_results,
        "saved_loss": rank0.get("saved_loss"),
        "resume_first_loss": rank0.get("resume_first_loss"),
        "resume_tolerance": RESUME_LOSS_ATOL,
        "post_resume_final_loss": rank0.get("post_resume_final_loss"),
        "finite": len(rank_results) == args.gpus and all(bool(result.get("finite")) for result in rank_results),
    }
    return evaluate_w1d(metrics)


RUNNERS = {
    "W1a": run_w1a,
    "W1b": run_w1b,
    "W1c": run_w1c,
    "W1d": run_w1d,
}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workload", required=True, choices=sorted(RUNNERS))
    parser.add_argument("--out", required=True)
    parser.add_argument("--run-dir", required=True, type=Path)
    parser.add_argument("--line", required=True)
    parser.add_argument("--gpus", type=int, default=8)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    args.run_dir.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    try:
        result = RUNNERS[args.workload](args)
    except Exception as exc:  # pragma: no cover - live runtime failure path
        result = {
            "id": args.workload,
            "verdict": "failed",
            "failed_criteria": ["exception"],
            "criteria": {},
            "metrics": {},
            "exception": f"{type(exc).__name__}: {exc}",
        }
    result["line"] = args.line
    result["duration_seconds"] = time.monotonic() - started
    _write_json(Path(args.out), result)
    return 0 if result.get("verdict") == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
