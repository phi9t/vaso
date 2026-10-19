#!/usr/bin/env python3
"""Torch and JAX co-residence workload W4."""

from __future__ import annotations

import argparse
import json
import math
import time
from pathlib import Path
from typing import Any


def _finite(value: object) -> bool:
    return isinstance(value, (int, float)) and math.isfinite(float(value))


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


def evaluate_w4(metrics: dict[str, Any]) -> dict[str, object]:
    checks = {
        "torch_imported": metrics.get("torch_imported") is True,
        "jax_imported": metrics.get("jax_imported") is True,
        "torch_to_jax_exact": metrics.get("torch_to_jax_exact") is True,
        "jax_to_torch_exact": metrics.get("jax_to_torch_exact") is True,
        "torch_step_finite": _finite(metrics.get("torch_step_loss")),
        "jax_step_finite": _finite(metrics.get("jax_step_loss")),
    }
    return _criteria_result("W4", metrics, checks)


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


def _imports():
    import jax
    import jax.numpy as jnp
    import torch

    return torch, jax, jnp


def _require_cuda(torch: Any, jax: Any) -> list[Any]:
    if not torch.cuda.is_available():
        raise RuntimeError("CUDA is not available to torch")
    devices = list(jax.devices("gpu"))
    if not devices:
        raise RuntimeError("CUDA is not available to JAX")
    return devices


def run_w4(args: argparse.Namespace) -> dict[str, object]:
    torch_imported = False
    jax_imported = False
    torch, jax, jnp = _imports()
    torch_imported = True
    jax_imported = True
    devices = _require_cuda(torch, jax)
    torch.cuda.set_device(0)

    torch_source = torch.arange(32, device="cuda", dtype=torch.float32)
    jax_from_torch = jax.dlpack.from_dlpack(torch_source)
    torch_to_jax_exact = bool(
        jnp.array_equal(jax_from_torch, jnp.arange(32, dtype=jnp.float32)).item()
    )

    with jax.default_device(devices[0]):
        jax_source = jnp.arange(32, dtype=jnp.float32)
    torch_from_jax = torch.utils.dlpack.from_dlpack(jax_source)
    jax_to_torch_exact = bool(torch.equal(torch_from_jax, torch_source))

    torch.manual_seed(404)
    model = torch.nn.Linear(16, 1, bias=False).cuda()
    optimizer = torch.optim.SGD(model.parameters(), lr=1.0e-2)
    x_torch = torch.arange(64, device="cuda", dtype=torch.float32).reshape(4, 16) / 64.0
    y_torch = x_torch.sum(dim=1, keepdim=True)
    optimizer.zero_grad(set_to_none=True)
    torch_loss = torch.nn.functional.mse_loss(model(x_torch), y_torch)
    torch_loss.backward()
    optimizer.step()
    torch.cuda.synchronize()

    @jax.jit
    def jax_step(w):
        x = jnp.arange(64, dtype=jnp.float32).reshape(4, 16) / 64.0
        y = jnp.sum(x, axis=1, keepdims=True)

        def loss_fn(weights):
            prediction = x @ weights
            return jnp.mean((prediction - y) ** 2)

        loss, grad = jax.value_and_grad(loss_fn)(w)
        return w - 1.0e-2 * grad, loss

    with jax.default_device(devices[0]):
        weights = jnp.ones((16, 1), dtype=jnp.float32) * 0.01
        _, jax_loss = jax_step(weights)

    metrics = {
        "torch_imported": torch_imported,
        "jax_imported": jax_imported,
        "torch_version": getattr(torch, "__version__", ""),
        "torch_cuda_version": getattr(torch.version, "cuda", ""),
        "jax_version": getattr(jax, "__version__", ""),
        "torch_to_jax_exact": torch_to_jax_exact,
        "jax_to_torch_exact": jax_to_torch_exact,
        "torch_step_loss": float(torch_loss.detach().cpu()),
        "jax_step_loss": float(jax.device_get(jax_loss)),
        "device_count": len(devices),
    }
    return evaluate_w4(metrics)


RUNNERS = {"W4": run_w4}


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
