#!/usr/bin/env python3
"""JAX live workloads W3a through W3c."""

from __future__ import annotations

import argparse
import functools
import json
import math
import time
from pathlib import Path
from typing import Any


INPUT_DIM = 256
HIDDEN_DIM = 768
OUTPUT_DIM = 32
BATCH_SIZE = 512
W3_STEPS = 300
LOSS_DROP_RATIO = 0.50
PARAMETER_ATOL = 0.0
PALLAS_ATOL = 1.0e-3


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


def evaluate_w3a(metrics: dict[str, Any]) -> dict[str, object]:
    checks = {
        "loss_drop": _loss_drop(metrics.get("initial_loss"), metrics.get("final_loss")),
        "finite_losses": _series_is_finite(metrics.get("losses")),
        "step_count": int(metrics.get("steps", 0) or 0) == W3_STEPS,
    }
    return _criteria_result("W3a", metrics, checks)


def evaluate_w3b(metrics: dict[str, Any]) -> dict[str, object]:
    checks = {
        "device_count": int(metrics.get("device_count", 0) or 0) == 8,
        "loss_drop": _loss_drop(metrics.get("initial_loss"), metrics.get("final_loss")),
        "finite_losses": metrics.get("finite") is True,
        "parameter_consistency": _finite(metrics.get("max_parameter_delta"))
        and float(metrics["max_parameter_delta"]) <= PARAMETER_ATOL,
        "psum": _finite(metrics.get("psum_result")) and float(metrics["psum_result"]) == 8.0,
    }
    return _criteria_result("W3b", metrics, checks)


def evaluate_w3c(metrics: dict[str, Any]) -> dict[str, object]:
    tolerance = float(metrics.get("tolerance", PALLAS_ATOL))
    checks = {
        "compile_succeeded": metrics.get("compile_succeeded") is True,
        "max_abs_error": _finite(metrics.get("max_abs_error"))
        and float(metrics["max_abs_error"]) <= tolerance,
    }
    return _criteria_result("W3c", metrics, checks)


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


def _jax_imports():
    import jax
    import jax.numpy as jnp

    return jax, jnp


def _require_gpu_devices(jax: Any, count: int) -> list[Any]:
    devices = list(jax.devices("gpu"))
    if len(devices) < count:
        raise RuntimeError(f"requested {count} GPU(s), but JAX sees {len(devices)}")
    return devices[:count]


def _runtime_metadata(jax: Any, devices: list[Any]) -> dict[str, object]:
    import jaxlib

    return {
        "jax_version": getattr(jax, "__version__", ""),
        "jaxlib_version": getattr(jaxlib, "__version__", ""),
        "platform": devices[0].platform if devices else "",
        "devices": [str(device) for device in devices],
    }


def _tree_map(jax: Any, fn: Any, tree: Any, *rest: Any) -> Any:
    if hasattr(jax, "tree") and hasattr(jax.tree, "map"):
        return jax.tree.map(fn, tree, *rest)
    return jax.tree_util.tree_map(fn, tree, *rest)


def _tree_leaves(jax: Any, tree: Any) -> list[Any]:
    if hasattr(jax, "tree") and hasattr(jax.tree, "leaves"):
        return list(jax.tree.leaves(tree))
    return list(jax.tree_util.tree_leaves(tree))


def _replicate_for_pmap(jax: Any, jnp: Any, tree: Any, replicas: int) -> Any:
    return _tree_map(jax, lambda leaf: jnp.stack([leaf] * replicas), tree)


def _init_params(jax: Any, jnp: Any, seed: int = 0) -> dict[str, Any]:
    key = jax.random.key(seed)
    k1, k2 = jax.random.split(key)
    scale1 = jnp.asarray(1.0 / math.sqrt(INPUT_DIM), dtype=jnp.float32)
    scale2 = jnp.asarray(1.0 / math.sqrt(HIDDEN_DIM), dtype=jnp.float32)
    return {
        "w1": jax.random.normal(k1, (INPUT_DIM, HIDDEN_DIM), dtype=jnp.float32) * scale1,
        "b1": jnp.zeros((HIDDEN_DIM,), dtype=jnp.float32),
        "w2": jax.random.normal(k2, (HIDDEN_DIM, OUTPUT_DIM), dtype=jnp.float32) * scale2,
        "b2": jnp.zeros((OUTPUT_DIM,), dtype=jnp.float32),
    }


def _make_batch(jnp: Any, step: int, *, per_device: int = BATCH_SIZE, device_id: int = 0) -> tuple[Any, Any]:
    rows = jnp.arange(per_device, dtype=jnp.float32)[:, None]
    cols = jnp.arange(INPUT_DIM, dtype=jnp.float32)[None, :]
    phase = step * 0.013 + device_id * 0.071
    x = jnp.sin(rows * 0.017 + cols * 0.019 + phase)
    x = x.astype(jnp.bfloat16)
    y = jnp.stack(
        [
            jnp.mean(x[:, group::OUTPUT_DIM].astype(jnp.float32), axis=1)
            for group in range(OUTPUT_DIM)
        ],
        axis=1,
    )
    return x, y


def _forward(jnp: Any, params: dict[str, Any], x: Any) -> Any:
    h = (x.astype(jnp.bfloat16) @ params["w1"].astype(jnp.bfloat16)).astype(jnp.float32) + params["b1"]
    h = jnp.tanh(h).astype(jnp.bfloat16)
    return (h @ params["w2"].astype(jnp.bfloat16)).astype(jnp.float32) + params["b2"]


def _loss_fn(jnp: Any, params: dict[str, Any], x: Any, y: Any) -> Any:
    prediction = _forward(jnp, params, x)
    return jnp.mean((prediction - y) ** 2)


def _adam_state(jax: Any, jnp: Any, params: dict[str, Any]) -> dict[str, Any]:
    return {
        "step": jnp.asarray(0, dtype=jnp.int32),
        "m": _tree_map(jax, jnp.zeros_like, params),
        "v": _tree_map(jax, jnp.zeros_like, params),
    }


def _adam_update(jax: Any, jnp: Any, params: dict[str, Any], grads: dict[str, Any], state: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    beta1 = 0.9
    beta2 = 0.999
    learning_rate = 3.0e-3
    eps = 1.0e-8
    step = state["step"] + 1
    m = _tree_map(jax, lambda old, grad: beta1 * old + (1.0 - beta1) * grad, state["m"], grads)
    v = _tree_map(jax, lambda old, grad: beta2 * old + (1.0 - beta2) * (grad * grad), state["v"], grads)
    m_hat = _tree_map(jax, lambda value: value / (1.0 - beta1 ** step), m)
    v_hat = _tree_map(jax, lambda value: value / (1.0 - beta2 ** step), v)
    new_params = _tree_map(
        jax,
        lambda param, mh, vh: param - learning_rate * mh / (jnp.sqrt(vh) + eps),
        params,
        m_hat,
        v_hat,
    )
    return new_params, {"step": step, "m": m, "v": v}


def _train_single_device(jax: Any, jnp: Any) -> tuple[dict[str, Any], dict[str, Any], list[float], float]:
    params = _init_params(jax, jnp, seed=1001)
    state = _adam_state(jax, jnp, params)

    @jax.jit
    def train_step(params, state, x, y):
        loss, grads = jax.value_and_grad(functools.partial(_loss_fn, jnp))(params, x, y)
        params, state = _adam_update(jax, jnp, params, grads, state)
        return params, state, loss

    losses: list[float] = []
    start = time.perf_counter()
    for step in range(W3_STEPS):
        x, y = _make_batch(jnp, step)
        params, state, loss = train_step(params, state, x, y)
        losses.append(float(jax.device_get(loss)))
    jax.block_until_ready(params)
    elapsed = time.perf_counter() - start
    examples_per_second = BATCH_SIZE * W3_STEPS / elapsed
    return params, state, losses, examples_per_second


def run_w3a(args: argparse.Namespace) -> dict[str, object]:
    jax, jnp = _jax_imports()
    devices = _require_gpu_devices(jax, 1)
    with jax.default_device(devices[0]):
        params, _, losses, examples_per_second = _train_single_device(jax, jnp)
    metrics = {
        **_runtime_metadata(jax, devices),
        "steps": W3_STEPS,
        "parameters": sum(int(leaf.size) for leaf in _tree_leaves(jax, params)),
        "initial_loss": losses[0],
        "final_loss": losses[-1],
        "losses": losses,
        "examples_per_second": examples_per_second,
        "dtype": "bf16_matmul",
        "optimizer": "hand_written_adam",
    }
    return evaluate_w3a(metrics)


def _max_replicated_parameter_delta(jax: Any, jnp: Any, params: dict[str, Any]) -> float:
    host_params = jax.device_get(params)
    max_delta = 0.0
    for leaf in _tree_leaves(jax, host_params):
        reference = leaf[0]
        delta = float(jnp.max(jnp.abs(jnp.asarray(leaf) - jnp.asarray(reference))).item())
        max_delta = max(max_delta, delta)
    return max_delta


def run_w3b(args: argparse.Namespace) -> dict[str, object]:
    jax, jnp = _jax_imports()
    devices = _require_gpu_devices(jax, args.gpus)
    initial_params = _init_params(jax, jnp, seed=2002)
    params = _replicate_for_pmap(jax, jnp, initial_params, len(devices))
    state = _replicate_for_pmap(jax, jnp, _adam_state(jax, jnp, initial_params), len(devices))

    @functools.partial(jax.pmap, axis_name="data", devices=devices)
    def psum_probe(x):
        return jax.lax.psum(x, "data")

    @functools.partial(jax.pmap, axis_name="data", devices=devices)
    def train_step(params, state, x, y):
        loss, grads = jax.value_and_grad(functools.partial(_loss_fn, jnp))(params, x, y)
        grads = jax.lax.pmean(grads, "data")
        loss = jax.lax.pmean(loss, "data")
        params, state = _adam_update(jax, jnp, params, grads, state)
        return params, state, loss

    psum_result = float(jax.device_get(psum_probe(jnp.ones((args.gpus,), dtype=jnp.float32)))[0])
    losses: list[float] = []
    per_device_batch = max(1, BATCH_SIZE // args.gpus)
    for step in range(W3_STEPS):
        xs = []
        ys = []
        for device_id in range(args.gpus):
            x, y = _make_batch(jnp, step, per_device=per_device_batch, device_id=device_id)
            xs.append(x)
            ys.append(y)
        x_batch = jnp.stack(xs)
        y_batch = jnp.stack(ys)
        params, state, loss = train_step(params, state, x_batch, y_batch)
        losses.append(float(jax.device_get(loss)[0]))
    jax.block_until_ready(params)
    metrics = {
        **_runtime_metadata(jax, devices),
        "device_count": len(devices),
        "steps": W3_STEPS,
        "initial_loss": losses[0],
        "final_loss": losses[-1],
        "finite": _series_is_finite(losses),
        "losses": losses,
        "max_parameter_delta": _max_replicated_parameter_delta(jax, jnp, params),
        "psum_result": psum_result,
        "optimizer": "hand_written_adam",
    }
    return evaluate_w3b(metrics)


def run_w3c(args: argparse.Namespace) -> dict[str, object]:
    jax, jnp = _jax_imports()
    devices = _require_gpu_devices(jax, 1)
    compile_succeeded = True
    compile_error = ""
    max_abs_error: float | None = None
    try:
        from jax.experimental import pallas as pl

        block = 256
        size = 4096

        def add_kernel(x_ref, y_ref, out_ref):
            out_ref[...] = x_ref[...] + y_ref[...]

        @jax.jit
        def add_vectors(x, y):
            return pl.pallas_call(
                add_kernel,
                out_shape=jax.ShapeDtypeStruct((size,), x.dtype),
                grid=(size // block,),
                in_specs=[
                    pl.BlockSpec((block,), lambda i: (i,)),
                    pl.BlockSpec((block,), lambda i: (i,)),
                ],
                out_specs=pl.BlockSpec((block,), lambda i: (i,)),
            )(x, y)

        with jax.default_device(devices[0]):
            x = jnp.arange(size, dtype=jnp.float32)
            y = jnp.sin(x)
            out = add_vectors(x, y)
            expected = x + y
            max_abs_error = float(jax.device_get(jnp.max(jnp.abs(out - expected))))
    except Exception as exc:  # pragma: no cover - live runtime failure path
        compile_succeeded = False
        compile_error = f"{type(exc).__name__}: {exc}"
    metrics = {
        **_runtime_metadata(jax, devices),
        "compile_succeeded": compile_succeeded,
        "compile_error": compile_error,
        "max_abs_error": max_abs_error,
        "tolerance": PALLAS_ATOL,
        "kernel": "pallas_vector_add",
    }
    return evaluate_w3c(metrics)


RUNNERS = {
    "W3a": run_w3a,
    "W3b": run_w3b,
    "W3c": run_w3c,
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
