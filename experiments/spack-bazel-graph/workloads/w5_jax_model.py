#!/usr/bin/env python3
"""Real JAX modeling-load workloads W5a through W5f."""

from __future__ import annotations

import argparse
import inspect
import json
import math
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


VOCAB_SIZE = 51_200
ACTIVE_VOCAB_SIZE = 16
SEQ_LEN = 1024
MODEL_DIM = 768
N_HEADS = 12
HEAD_DIM = MODEL_DIM // N_HEADS
N_LAYERS = 12
MLP_DIM = 4 * MODEL_DIM
GLOBAL_BATCH = 8
W5A_STEPS = 400
W5B_STEPS = 200
W5F_STEPS = 50
WARMUP_STEPS = 40
PEAK_LR = 2.5e-3
WEIGHT_DECAY = 0.01
GRAD_CLIP_NORM = 1.0
LOSS_DROP_RATIO = 0.40
MARKOV_MAJOR_PROBABILITY = 0.80
MARKOV_LOSS_FLOOR_TOLERANCE_RATIO = 0.10
MARKOV_LOSS_FLOOR_ABS_TOLERANCE = 1.0e-6
MARKOV_BATCH_SEED_BASE = 500_500
MARKOV_MINOR_PROBABILITY = (1.0 - MARKOV_MAJOR_PROBABILITY) / (ACTIVE_VOCAB_SIZE - 1)
DATA_ENTROPY_FLOOR_NATS = -(
    MARKOV_MAJOR_PROBABILITY * math.log(MARKOV_MAJOR_PROBABILITY)
    + (ACTIVE_VOCAB_SIZE - 1) * MARKOV_MINOR_PROBABILITY * math.log(MARKOV_MINOR_PROBABILITY)
)
DATA_ENTROPY_NATS = DATA_ENTROPY_FLOOR_NATS
LOSS_FLOOR_UPPER_NATS = DATA_ENTROPY_FLOOR_NATS * (1.0 + MARKOV_LOSS_FLOOR_TOLERANCE_RATIO)
W5B_LOSS_TOLERANCE_NATS = 0.50
CPU_GPU_TOLERANCE = 1.0e-4
BF16_ATTENTION_TOLERANCE = 8.0e-2
GEMM_TOLERANCE = 1.0e-2
DETERMINISM_TOLERANCE = 1.0e-6
B200_BF16_PEAK_FLOPS_PER_GPU = 2.25e15
DETERMINISTIC_XLA_FLAG = "--xla_gpu_deterministic_ops=true"
DETERMINISTIC_NCCL_ALGO = "Ring"
DETERMINISTIC_NCCL_PROTO = "Simple"

ATTENTION_SEQ_LENGTHS = (1024, 2048)
W5E_MAT_DIM = 512
PALLAS_ASYNC_COPY_DIM_LIMIT = 256
XLA_TRITON_GEMM_ENABLE_FLAGS = (
    "--xla_gpu_enable_triton_gemm=true",
    "--xla_gpu_triton_gemm_any=true",
)
XLA_TRITON_GEMM_DISABLE_FLAGS = (
    "--xla_gpu_enable_triton_gemm=false",
    "--xla_gpu_triton_gemm_any=false",
)

PER_LAYER_PARAMETER_COUNT = (
    MODEL_DIM * (3 * MODEL_DIM)
    + MODEL_DIM * MODEL_DIM
    + MODEL_DIM * MLP_DIM
    + MLP_DIM * MODEL_DIM
    + 4 * MODEL_DIM
)
TARGET_PARAMETER_COUNT = (
    VOCAB_SIZE * MODEL_DIM
    + SEQ_LEN * MODEL_DIM
    + N_LAYERS * PER_LAYER_PARAMETER_COUNT
    + 2 * MODEL_DIM
    + VOCAB_SIZE
)


def _finite(value: object) -> bool:
    return isinstance(value, (int, float)) and math.isfinite(float(value))


def _positive_finite(value: object) -> bool:
    return _finite(value) and float(value) > 0.0


def _series_is_finite(values: object) -> bool:
    return isinstance(values, list) and bool(values) and all(_finite(value) for value in values)


def _loss_drop(initial: object, final: object, ratio: float = LOSS_DROP_RATIO) -> bool:
    return _finite(initial) and _finite(final) and float(final) <= float(initial) * ratio


def markov_transition_entropy_nats(
    *,
    active_vocab_size: int = ACTIVE_VOCAB_SIZE,
    major_probability: float = MARKOV_MAJOR_PROBABILITY,
) -> float:
    if active_vocab_size < 2:
        raise ValueError("active_vocab_size must be at least 2")
    if not 0.0 < major_probability < 1.0:
        raise ValueError("major_probability must be between 0 and 1")
    minor_probability = (1.0 - major_probability) / float(active_vocab_size - 1)
    return -(
        major_probability * math.log(major_probability)
        + float(active_vocab_size - 1) * minor_probability * math.log(minor_probability)
    )


def _markov_transition_probabilities_np(
    *,
    active_vocab_size: int = ACTIVE_VOCAB_SIZE,
    major_probability: float = MARKOV_MAJOR_PROBABILITY,
) -> Any:
    import numpy as np

    if active_vocab_size < 2:
        raise ValueError("active_vocab_size must be at least 2")
    if not 0.0 < major_probability < 1.0:
        raise ValueError("major_probability must be between 0 and 1")
    minor_probability = (1.0 - major_probability) / float(active_vocab_size - 1)
    probs = np.full((active_vocab_size, active_vocab_size), minor_probability, dtype=np.float32)
    rows = np.arange(active_vocab_size, dtype=np.int32)
    probs[rows, (rows + 1) % active_vocab_size] = major_probability
    return probs


def _configured_global_batch() -> int:
    raw = os.environ.get("VASO_W5_GLOBAL_BATCH")
    if not raw:
        return GLOBAL_BATCH
    try:
        batch_size = int(raw)
    except ValueError as exc:
        raise ValueError(f"VASO_W5_GLOBAL_BATCH must be an integer, got {raw!r}") from exc
    if batch_size < 1:
        raise ValueError(f"VASO_W5_GLOBAL_BATCH must be positive, got {batch_size}")
    if batch_size % 8 != 0:
        raise ValueError(f"VASO_W5_GLOBAL_BATCH must be divisible by 8, got {batch_size}")
    return batch_size


def _xla_flags_with_overrides(*flags: str, drop_prefixes: tuple[str, ...] = ()) -> str:
    existing = [
        flag
        for flag in os.environ.get("XLA_FLAGS", "").split()
        if not any(flag.startswith(prefix) for prefix in drop_prefixes)
    ]
    return " ".join(_dedupe([*existing, *flags]))


def _dedupe(items: list[str]) -> list[str]:
    result: list[str] = []
    for item in items:
        if item and item not in result:
            result.append(item)
    return result


def _enable_w5f_deterministic_mode() -> dict[str, str]:
    os.environ["XLA_FLAGS"] = _xla_flags_with_overrides(
        DETERMINISTIC_XLA_FLAG,
        drop_prefixes=("--xla_gpu_deterministic_ops=", "--xla_gpu_exclude_nondeterministic_ops="),
    )
    os.environ["NCCL_ALGO"] = DETERMINISTIC_NCCL_ALGO
    os.environ["NCCL_PROTO"] = DETERMINISTIC_NCCL_PROTO
    return {
        "xla_flags": os.environ["XLA_FLAGS"],
        "nccl_algo": os.environ["NCCL_ALGO"],
        "nccl_proto": os.environ["NCCL_PROTO"],
    }


def _deterministic_mode_enabled(metrics: dict[str, Any]) -> bool:
    return (
        metrics.get("deterministic_mode") is True
        and DETERMINISTIC_XLA_FLAG in str(metrics.get("xla_flags", ""))
        and metrics.get("nccl_algo") == DETERMINISTIC_NCCL_ALGO
        and metrics.get("nccl_proto") == DETERMINISTIC_NCCL_PROTO
    )


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


def _mesh_shape(metrics: dict[str, Any], axis: str) -> int:
    mesh_shape = metrics.get("mesh_shape")
    if not isinstance(mesh_shape, dict):
        return 0
    return int(mesh_shape.get(axis, 0) or 0)


def evaluate_w5a(metrics: dict[str, Any]) -> dict[str, object]:
    floor = float(metrics.get("data_entropy_floor_nats", DATA_ENTROPY_FLOOR_NATS))
    upper = float(metrics.get("loss_floor_upper_nats", floor * (1.0 + MARKOV_LOSS_FLOOR_TOLERANCE_RATIO)))
    checks = {
        "step_count": int(metrics.get("steps", 0) or 0) == W5A_STEPS,
        "loss_drop_60_percent": _loss_drop(metrics.get("initial_loss"), metrics.get("final_loss")),
        "loss_not_below_entropy_floor": _finite(metrics.get("final_loss"))
        and float(metrics["final_loss"]) + MARKOV_LOSS_FLOOR_ABS_TOLERANCE >= floor,
        "loss_near_entropy_floor": _finite(metrics.get("final_loss"))
        and float(metrics["final_loss"]) <= upper,
        "finite_losses": metrics.get("finite", True) is True and _series_is_finite(metrics.get("losses")),
        "device_count": int(metrics.get("device_count", 0) or 0) == 8,
        "model_shape": int(metrics.get("parameter_count", 0) or 0) >= 120_000_000
        and int(metrics.get("layers", N_LAYERS) or 0) == N_LAYERS
        and int(metrics.get("model_dim", MODEL_DIM) or 0) == MODEL_DIM
        and int(metrics.get("heads", N_HEADS) or 0) == N_HEADS
        and int(metrics.get("seq_len", SEQ_LEN) or 0) == SEQ_LEN,
        "fsdp_sharding": _mesh_shape(metrics, "data") == 8
        and metrics.get("param_sharding") == "fsdp_named_sharding_data_axis"
        and metrics.get("optimizer_sharding") == "fsdp_named_sharding_data_axis"
        and metrics.get("batch_sharding") == "data",
        "tokens_per_second_recorded": _positive_finite(metrics.get("tokens_per_second")),
        "mfu_recorded": _finite(metrics.get("mfu")) and float(metrics["mfu"]) >= 0.0,
    }
    return _criteria_result("W5a", metrics, checks)


def evaluate_w5b(metrics: dict[str, Any]) -> dict[str, object]:
    tolerance = float(metrics.get("loss_tolerance", W5B_LOSS_TOLERANCE_NATS))
    tensor_parallelism = metrics.get("tensor_parallelism")
    if not isinstance(tensor_parallelism, dict):
        tensor_parallelism = {}
    checks = {
        "step_count": int(metrics.get("steps", 0) or 0) == W5B_STEPS,
        "loss_falls": _finite(metrics.get("initial_loss"))
        and _finite(metrics.get("final_loss"))
        and float(metrics["final_loss"]) < float(metrics["initial_loss"]),
        "finite_losses": metrics.get("finite", True) is True and _series_is_finite(metrics.get("losses")),
        "device_count": int(metrics.get("device_count", 0) or 0) == 8,
        "mesh_shape": _mesh_shape(metrics, "data") == 4 and _mesh_shape(metrics, "model") == 2,
        "tensor_parallel_sharding": tensor_parallelism.get("attention") == "column_row"
        and tensor_parallelism.get("mlp") == "column_row",
        "collectives_exercised": tensor_parallelism.get("all_gather") is True
        and tensor_parallelism.get("reduce_scatter") is True,
        "loss_within_w5a_tolerance": _finite(metrics.get("final_loss"))
        and _finite(metrics.get("w5a_reference_loss_at_step"))
        and abs(float(metrics["final_loss"]) - float(metrics["w5a_reference_loss_at_step"])) <= tolerance,
    }
    return _criteria_result("W5b", metrics, checks)


def evaluate_w5c(metrics: dict[str, Any]) -> dict[str, object]:
    tolerance = float(metrics.get("tolerance", CPU_GPU_TOLERANCE))
    checks = {
        "gpu_backend": metrics.get("gpu_backend") in ("gpu", "cuda")
        and metrics.get("gpu_ran", True) is True,
        "cpu_backend": metrics.get("cpu_backend") == "cpu" and metrics.get("cpu_ran", True) is True,
        "loss_relative_error": _finite(metrics.get("loss_max_relative_error"))
        and float(metrics["loss_max_relative_error"]) <= tolerance,
        "grad_relative_error": _finite(metrics.get("grad_max_relative_error"))
        and float(metrics["grad_max_relative_error"]) <= tolerance,
        "finite": metrics.get("finite", True) is True,
    }
    return _criteria_result("W5c", metrics, checks)


def evaluate_w5d(metrics: dict[str, Any]) -> dict[str, object]:
    tolerance = float(metrics.get("tolerance", BF16_ATTENTION_TOLERANCE))
    checks = {
        "seq_lengths": tuple(metrics.get("seq_lengths", ())) == ATTENTION_SEQ_LENGTHS,
        "cudnn_ran": metrics.get("cudnn_ran") is True,
        "xla_ran": metrics.get("xla_ran") is True,
        "output_agreement": _finite(metrics.get("max_output_abs_error"))
        and float(metrics["max_output_abs_error"]) <= tolerance,
        "grad_agreement": _finite(metrics.get("max_grad_abs_error"))
        and float(metrics["max_grad_abs_error"]) <= tolerance,
        "finite": metrics.get("finite", True) is True,
    }
    return _criteria_result("W5d", metrics, checks)


def _flags_have_triton_toggles(flags: object) -> bool:
    if not isinstance(flags, dict):
        return False
    enabled = str(flags.get("enabled", ""))
    disabled = str(flags.get("disabled", ""))
    return (
        "xla_gpu_enable_triton_gemm=true" in enabled
        and "xla_gpu_triton_gemm_any=true" in enabled
        and "xla_gpu_enable_triton_gemm=false" in disabled
        and "xla_gpu_triton_gemm_any=false" in disabled
    )


def evaluate_w5e(metrics: dict[str, Any]) -> dict[str, object]:
    tolerance = float(metrics.get("tolerance", GEMM_TOLERANCE))
    checks = {
        "xla_triton_enabled_ran": metrics.get("xla_triton_enabled_ran") is True,
        "xla_triton_disabled_ran": metrics.get("xla_triton_disabled_ran") is True,
        "pallas_matmul_ran": metrics.get("pallas_matmul_ran") is True,
        "xla_enabled_disabled_agreement": _finite(metrics.get("enabled_disabled_max_abs_error"))
        and float(metrics["enabled_disabled_max_abs_error"]) <= tolerance,
        "pallas_agreement": _finite(metrics.get("pallas_max_abs_error"))
        and float(metrics["pallas_max_abs_error"]) <= tolerance,
        "xla_flags_recorded": _flags_have_triton_toggles(metrics.get("xla_flags")),
    }
    return _criteria_result("W5e", metrics, checks)


def evaluate_w5f(metrics: dict[str, Any]) -> dict[str, object]:
    tolerance = float(metrics.get("tolerance", DETERMINISM_TOLERANCE))
    checks = {
        "step_count": int(metrics.get("steps", 0) or 0) == W5F_STEPS,
        "seed_recorded": isinstance(metrics.get("seed"), int),
        "deterministic_mode": _deterministic_mode_enabled(metrics),
        "finite_losses": _series_is_finite(metrics.get("run1_losses"))
        and _series_is_finite(metrics.get("run2_losses")),
        "loss_curves_match": _finite(metrics.get("max_loss_delta"))
        and float(metrics["max_loss_delta"]) <= tolerance,
    }
    return _criteria_result("W5f", metrics, checks)


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


def _tree_map(jax: Any, fn: Any, tree: Any, *rest: Any) -> Any:
    if hasattr(jax, "tree") and hasattr(jax.tree, "map"):
        return jax.tree.map(fn, tree, *rest)
    return jax.tree_util.tree_map(fn, tree, *rest)


def _tree_leaves(jax: Any, tree: Any) -> list[Any]:
    if hasattr(jax, "tree") and hasattr(jax.tree, "leaves"):
        return list(jax.tree.leaves(tree))
    return list(jax.tree_util.tree_leaves(tree))


def _tree_named_map(tree: Any, fn: Any, path: tuple[str, ...] = ()) -> Any:
    if isinstance(tree, dict):
        return {key: _tree_named_map(value, fn, (*path, str(key))) for key, value in tree.items()}
    if isinstance(tree, tuple):
        return tuple(_tree_named_map(value, fn, (*path, str(index))) for index, value in enumerate(tree))
    if isinstance(tree, list):
        return [_tree_named_map(value, fn, (*path, str(index))) for index, value in enumerate(tree)]
    return fn(path, tree)


def _shard_map_compat(jax: Any, fn: Any, *, mesh: Any, in_specs: Any, out_specs: Any) -> Any:
    kwargs = {"mesh": mesh, "in_specs": in_specs, "out_specs": out_specs}
    try:
        parameters = inspect.signature(jax.shard_map).parameters
    except (TypeError, ValueError):  # pragma: no cover - defensive for wrapped runtimes
        parameters = {}
    if "check_vma" in parameters:
        return jax.shard_map(fn, **kwargs, check_vma=False)
    return jax.shard_map(fn, **kwargs, check_rep=False)


def model_size_breakdown() -> dict[str, int]:
    return {
        "token_embedding": VOCAB_SIZE * MODEL_DIM,
        "position_embedding": SEQ_LEN * MODEL_DIM,
        "per_layer": PER_LAYER_PARAMETER_COUNT,
        "layers": N_LAYERS * PER_LAYER_PARAMETER_COUNT,
        "final_layer_norm": 2 * MODEL_DIM,
        "output_bias": VOCAB_SIZE,
        "total": TARGET_PARAMETER_COUNT,
    }


def _runtime_metadata(jax: Any, devices: list[Any]) -> dict[str, object]:
    import jaxlib

    return {
        "jax_version": getattr(jax, "__version__", ""),
        "jaxlib_version": getattr(jaxlib, "__version__", ""),
        "platform": devices[0].platform if devices else "",
        "device_count": len(devices),
        "devices": [str(device) for device in devices],
        "xla_flags": os.environ.get("XLA_FLAGS", ""),
    }


def _nontraining_metric_envelope(
    *,
    steps: int,
    started: float,
    tokens: int = 0,
    device_info: dict[str, object] | None = None,
) -> dict[str, object]:
    elapsed = time.perf_counter() - started
    metrics = {
        "steps": steps,
        "loss_curve": [],
        "wall_time_seconds": elapsed,
        "tokens_per_second": (tokens / elapsed) if tokens and elapsed > 0.0 else 0.0,
        "mfu": 0.0,
        "mfu_formula": "not applicable for non-training probe",
        "xla_flags": os.environ.get("XLA_FLAGS", ""),
    }
    if device_info:
        metrics.update(device_info)
    return metrics


def _init_gpt_master_params(jax: Any, jnp: Any, *, seed: int) -> dict[str, Any]:
    key = jax.random.key(seed)

    def normal(shape: tuple[int, ...], scale: float = 0.02) -> Any:
        nonlocal key
        key, subkey = jax.random.split(key)
        return jax.random.normal(subkey, shape, dtype=jnp.float32) * jnp.asarray(scale, dtype=jnp.float32)

    def ones(shape: tuple[int, ...]) -> Any:
        return jnp.ones(shape, dtype=jnp.float32)

    def zeros(shape: tuple[int, ...]) -> Any:
        return jnp.zeros(shape, dtype=jnp.float32)

    blocks = []
    for _ in range(N_LAYERS):
        blocks.append(
            {
                "ln1_scale": ones((MODEL_DIM,)),
                "ln1_bias": zeros((MODEL_DIM,)),
                "qkv": normal((MODEL_DIM, 3, N_HEADS, HEAD_DIM)),
                "attn_out": normal((MODEL_DIM, MODEL_DIM)),
                "ln2_scale": ones((MODEL_DIM,)),
                "ln2_bias": zeros((MODEL_DIM,)),
                "mlp_in": normal((MODEL_DIM, MLP_DIM)),
                "mlp_out": normal((MLP_DIM, MODEL_DIM)),
            }
        )
    out_bias = jnp.full((VOCAB_SIZE,), -8.0, dtype=jnp.float32)
    out_bias = out_bias.at[:ACTIVE_VOCAB_SIZE].set(0.0)
    return {
        "tok": normal((VOCAB_SIZE, MODEL_DIM)),
        "pos": normal((SEQ_LEN, MODEL_DIM), scale=0.01),
        "blocks": tuple(blocks),
        "ln_f_scale": ones((MODEL_DIM,)),
        "ln_f_bias": zeros((MODEL_DIM,)),
        "out_bias": out_bias,
    }


def _make_lm_batch(step: int, *, batch_size: int = GLOBAL_BATCH) -> Any:
    import numpy as np

    rng = np.random.default_rng(MARKOV_BATCH_SEED_BASE + step)
    tokens = np.empty((batch_size, SEQ_LEN + 1), dtype=np.int32)
    tokens[:, 0] = rng.integers(0, ACTIVE_VOCAB_SIZE, size=batch_size, dtype=np.int32)
    for position in range(1, SEQ_LEN + 1):
        previous = tokens[:, position - 1]
        major = (previous + 1) % ACTIVE_VOCAB_SIZE
        minor = rng.integers(0, ACTIVE_VOCAB_SIZE - 1, size=batch_size, dtype=np.int32)
        minor = minor + (minor >= major)
        choose_major = rng.random(batch_size) < MARKOV_MAJOR_PROBABILITY
        tokens[:, position] = np.where(choose_major, major, minor)
    return tokens


def _to_bf16_params(jax: Any, jnp: Any, master_params: Any) -> Any:
    return _tree_map(jax, lambda value: value.astype(jnp.bfloat16), master_params)


def _replicate_for_compute(jax: Any, params: Any, compute_shardings: Any | None) -> Any:
    if compute_shardings is None:
        return params
    return _tree_map(
        jax,
        lambda value, sharding: jax.lax.with_sharding_constraint(value, sharding),
        params,
        compute_shardings,
    )


def _layer_norm(jnp: Any, x: Any, scale: Any, bias: Any) -> Any:
    x32 = x.astype(jnp.float32)
    mean = jnp.mean(x32, axis=-1, keepdims=True)
    var = jnp.mean((x32 - mean) ** 2, axis=-1, keepdims=True)
    y = (x32 - mean) * jax_rsqrte(jnp, var + jnp.asarray(1.0e-5, dtype=jnp.float32))
    y = y * scale.astype(jnp.float32) + bias.astype(jnp.float32)
    return y.astype(jnp.bfloat16)


def jax_rsqrte(jnp: Any, value: Any) -> Any:
    return 1.0 / jnp.sqrt(value)


def _sharding_constraint(jax: Any, value: Any, sharding: Any | None) -> Any:
    if sharding is None:
        return value
    return jax.lax.with_sharding_constraint(value, sharding)


def _dense(jnp: Any, x: Any, weight: Any) -> Any:
    return (x.astype(jnp.bfloat16) @ weight.astype(jnp.bfloat16)).astype(jnp.float32)


def _gpt_forward(
    jax: Any,
    jnp: Any,
    params: dict[str, Any],
    tokens: Any,
    *,
    layout: str,
    shardings: dict[str, Any],
) -> Any:
    x = params["tok"][tokens] + params["pos"][jnp.arange(tokens.shape[1], dtype=jnp.int32)][None, :, :]
    x = x.astype(jnp.bfloat16)
    if layout == "tp2d":
        x = _sharding_constraint(jax, x, shardings["act_hidden_model"])

    for block in params["blocks"]:
        if layout == "tp2d":
            residual = x
            attn_input = _sharding_constraint(jax, x, shardings["act_replicated"])
        else:
            residual = x
            attn_input = x
        h = _layer_norm(jnp, attn_input, block["ln1_scale"], block["ln1_bias"])
        qkv = jnp.einsum(
            "btd,dxhy->btxhy",
            h.astype(jnp.bfloat16),
            block["qkv"].astype(jnp.bfloat16),
            preferred_element_type=jnp.float32,
        )
        if layout == "tp2d":
            qkv = _sharding_constraint(jax, qkv, shardings["qkv"])
        query, key, value = jnp.moveaxis(qkv, 2, 0)
        attn = jax.nn.dot_product_attention(query, key, value, is_causal=True, implementation="xla")
        attn = attn.reshape(tokens.shape[0], tokens.shape[1], MODEL_DIM)
        if layout == "tp2d":
            attn = _sharding_constraint(jax, attn, shardings["act_hidden_model"])
        attn_out = _dense(jnp, attn, block["attn_out"]).astype(jnp.bfloat16)
        if layout == "tp2d":
            attn_out = _sharding_constraint(jax, attn_out, shardings["act_hidden_model"])
        x = (residual + attn_out).astype(jnp.bfloat16)

        if layout == "tp2d":
            residual = x
            mlp_input = _sharding_constraint(jax, x, shardings["act_replicated"])
        else:
            residual = x
            mlp_input = x
        h = _layer_norm(jnp, mlp_input, block["ln2_scale"], block["ln2_bias"])
        h = jax.nn.gelu(_dense(jnp, h, block["mlp_in"])).astype(jnp.bfloat16)
        if layout == "tp2d":
            h = _sharding_constraint(jax, h, shardings["mlp_hidden_model"])
        mlp_out = _dense(jnp, h, block["mlp_out"]).astype(jnp.bfloat16)
        if layout == "tp2d":
            mlp_out = _sharding_constraint(jax, mlp_out, shardings["act_hidden_model"])
        x = (residual + mlp_out).astype(jnp.bfloat16)

    if layout == "tp2d":
        x = _sharding_constraint(jax, x, shardings["act_replicated"])
    x = _layer_norm(jnp, x, params["ln_f_scale"], params["ln_f_bias"])
    logits = jnp.einsum(
        "btd,vd->btv",
        x.astype(jnp.bfloat16),
        params["tok"].astype(jnp.bfloat16),
        preferred_element_type=jnp.float32,
    )
    logits = logits + params["out_bias"].astype(jnp.float32)
    if layout == "tp2d":
        logits = _sharding_constraint(jax, logits, shardings["logits"])
    return logits


def _lm_loss(jax: Any, jnp: Any, params: dict[str, Any], batch: Any, layout: str, shardings: dict[str, Any]) -> Any:
    if layout == "fsdp":
        params = _replicate_for_compute(jax, params, shardings["compute_param_shardings"])
    inputs = batch[:, :SEQ_LEN]
    logits = _gpt_forward(jax, jnp, params, inputs, layout=layout, shardings=shardings)
    logits = logits.astype(jnp.float32)
    active_logits = logits[..., :ACTIVE_VOCAB_SIZE]
    major_targets = (inputs + 1) % ACTIVE_VOCAB_SIZE
    major_logits = jnp.take_along_axis(active_logits, major_targets[..., None], axis=-1)[..., 0]
    active_sum = jnp.sum(active_logits, axis=-1)
    expected_target_logits = (
        jnp.asarray(MARKOV_MINOR_PROBABILITY, dtype=jnp.float32) * active_sum
        + jnp.asarray(MARKOV_MAJOR_PROBABILITY - MARKOV_MINOR_PROBABILITY, dtype=jnp.float32) * major_logits
    )
    return jnp.mean(jax.nn.logsumexp(logits, axis=-1) - expected_target_logits)


def _global_norm(jax: Any, jnp: Any, tree: Any) -> Any:
    total = jnp.asarray(0.0, dtype=jnp.float32)
    for leaf in _tree_leaves(jax, tree):
        leaf32 = leaf.astype(jnp.float32)
        total = total + jnp.sum(leaf32 * leaf32)
    return jnp.sqrt(total)


def _learning_rate(jnp: Any, step: Any, total_steps: int) -> Any:
    step32 = step.astype(jnp.float32)
    warmup = jnp.minimum(step32 / jnp.asarray(WARMUP_STEPS, dtype=jnp.float32), 1.0)
    denom = jnp.asarray(max(1, total_steps - WARMUP_STEPS), dtype=jnp.float32)
    progress = jnp.clip((step32 - WARMUP_STEPS) / denom, 0.0, 1.0)
    cosine = 0.5 * (1.0 + jnp.cos(jnp.asarray(math.pi, dtype=jnp.float32) * progress))
    return jnp.asarray(PEAK_LR, dtype=jnp.float32) * warmup * cosine


def _adamw_state(jax: Any, jnp: Any, params: Any) -> dict[str, Any]:
    return {
        "step": jnp.asarray(0, dtype=jnp.int32),
        "m": _tree_map(jax, jnp.zeros_like, params),
        "v": _tree_map(jax, jnp.zeros_like, params),
    }


def _adamw_update(
    jax: Any,
    jnp: Any,
    master_params: Any,
    grads: Any,
    opt_state: dict[str, Any],
    total_steps: int,
) -> tuple[Any, dict[str, Any], Any, Any]:
    beta1 = jnp.asarray(0.9, dtype=jnp.float32)
    beta2 = jnp.asarray(0.95, dtype=jnp.float32)
    eps = jnp.asarray(1.0e-8, dtype=jnp.float32)
    step = opt_state["step"] + jnp.asarray(1, dtype=jnp.int32)
    step32 = step.astype(jnp.float32)
    grad_norm = _global_norm(jax, jnp, grads)
    clip_scale = jnp.minimum(1.0, jnp.asarray(GRAD_CLIP_NORM, dtype=jnp.float32) / (grad_norm + eps))
    clipped = _tree_map(jax, lambda grad: grad.astype(jnp.float32) * clip_scale, grads)
    m = _tree_map(jax, lambda old, grad: beta1 * old + (1.0 - beta1) * grad, opt_state["m"], clipped)
    v = _tree_map(jax, lambda old, grad: beta2 * old + (1.0 - beta2) * (grad * grad), opt_state["v"], clipped)
    m_hat = _tree_map(jax, lambda value: value / (1.0 - jnp.power(beta1, step32)), m)
    v_hat = _tree_map(jax, lambda value: value / (1.0 - jnp.power(beta2, step32)), v)
    lr = _learning_rate(jnp, step, total_steps)
    new_master = _tree_map(
        jax,
        lambda param, mh, vh: param
        - lr * (mh / (jnp.sqrt(vh) + eps) + jnp.asarray(WEIGHT_DECAY, dtype=jnp.float32) * param),
        master_params,
        m_hat,
        v_hat,
    )
    return new_master, {"step": step, "m": m, "v": v}, grad_norm, lr


def _make_train_step(jax: Any, jnp: Any, *, layout: str, shardings: dict[str, Any], total_steps: int) -> Any:
    tp_collective_probe = None
    if layout == "tp2d":
        from jax.sharding import PartitionSpec as P

        def mapped_probe(local):
            gathered = jax.lax.all_gather(local, "model", axis=1, tiled=True)
            return jax.lax.psum_scatter(gathered, "model", scatter_dimension=1, tiled=True)

        tp_collective_probe = _shard_map_compat(
            jax,
            mapped_probe,
            mesh=shardings["mesh"],
            in_specs=P("data", "model"),
            out_specs=P("data", "model"),
        )

    def train_step(state: dict[str, Any], batch: Any) -> tuple[dict[str, Any], Any, Any, Any, Any]:
        loss, grads = jax.value_and_grad(
            lambda params: _lm_loss(jax, jnp, params, batch, layout, shardings)
        )(state["params"])
        collective_checksum = jnp.asarray(0.0, dtype=jnp.float32)
        if tp_collective_probe is not None:
            probe = jax.lax.with_sharding_constraint(batch[:4, :2].astype(jnp.float32), shardings["tp_probe"])
            collective_checksum = jnp.sum(tp_collective_probe(probe))
        grads = _tree_map(jax, lambda grad: grad.astype(jnp.float32), grads)
        master, opt_state, grad_norm, lr = _adamw_update(jax, jnp, state["master"], grads, state["opt"], total_steps)
        params = _to_bf16_params(jax, jnp, master)
        finite = state["finite"] & jnp.isfinite(loss) & jnp.isfinite(grad_norm)
        return {"master": master, "params": params, "opt": opt_state, "finite": finite}, loss, grad_norm, lr, collective_checksum

    return train_step


def _fsdp_param_sharding(jax: Any, mesh: Any, param: Any) -> Any:
    from jax.sharding import NamedSharding
    from jax.sharding import PartitionSpec as P

    if param.ndim == 0:
        return NamedSharding(mesh, P())
    if param.shape[0] % mesh.shape["data"] == 0:
        return NamedSharding(mesh, P("data", *([None] * (param.ndim - 1))))
    return NamedSharding(mesh, P(*([None] * param.ndim)))


def _replicated_param_sharding(mesh: Any, param: Any) -> Any:
    from jax.sharding import NamedSharding
    from jax.sharding import PartitionSpec as P

    return NamedSharding(mesh, P(*([None] * param.ndim)))


def _tp_param_sharding(mesh: Any, path: tuple[str, ...], param: Any) -> Any:
    from jax.sharding import NamedSharding
    from jax.sharding import PartitionSpec as P

    if param.ndim == 0:
        return NamedSharding(mesh, P())
    name = path[-1] if path else ""
    if name in {"tok", "pos"} and param.ndim == 2:
        return NamedSharding(mesh, P(None, "model"))
    if name == "qkv":
        return NamedSharding(mesh, P(None, None, "model", None))
    if name == "mlp_in":
        return NamedSharding(mesh, P(None, "model"))
    if name in {"attn_out", "mlp_out"}:
        return NamedSharding(mesh, P("model", None))
    if name == "out_bias":
        return NamedSharding(mesh, P(None))
    return NamedSharding(mesh, P(*([None] * param.ndim)))


def _make_sharding_context(jax: Any, devices: list[Any], *, layout: str) -> dict[str, Any]:
    import numpy as np
    from jax.sharding import Mesh
    from jax.sharding import NamedSharding
    from jax.sharding import PartitionSpec as P

    if layout == "fsdp":
        mesh = Mesh(np.array(devices).reshape((8,)), ("data",))
        batch = NamedSharding(mesh, P("data", None))
        scalar = NamedSharding(mesh, P())
        return {
            "mesh": mesh,
            "batch": batch,
            "scalar": scalar,
            "layout": layout,
            "mesh_shape": {"data": 8},
            "param_kind": "fsdp_named_sharding_data_axis",
            "param_sharding_for": lambda path, value: _fsdp_param_sharding(jax, mesh, value),
            "compute_param_sharding_for": lambda path, value: _replicated_param_sharding(mesh, value),
        }

    if layout == "tp2d":
        mesh = Mesh(np.array(devices).reshape((4, 2)), ("data", "model"))
        batch = NamedSharding(mesh, P("data", None))
        scalar = NamedSharding(mesh, P())
        return {
            "mesh": mesh,
            "batch": batch,
            "scalar": scalar,
            "layout": layout,
            "mesh_shape": {"data": 4, "model": 2},
            "param_kind": "tensor_parallel_named_sharding_2d",
            "param_sharding_for": lambda path, value: _tp_param_sharding(mesh, path, value),
            "act_replicated": NamedSharding(mesh, P("data", None, None)),
            "act_hidden_model": NamedSharding(mesh, P("data", None, "model")),
            "mlp_hidden_model": NamedSharding(mesh, P("data", None, "model")),
            "qkv": NamedSharding(mesh, P("data", None, None, "model", None)),
            "logits": NamedSharding(mesh, P("data", None, None)),
            "tp_probe": NamedSharding(mesh, P("data", "model")),
        }

    raise ValueError(f"unknown layout {layout!r}")


def _put_tree(jax: Any, tree: Any, shardings: Any) -> Any:
    return _tree_map(jax, lambda value, sharding: jax.device_put(value, sharding), tree, shardings)


def _state_shardings(jax: Any, param_shardings: Any, scalar_sharding: Any) -> dict[str, Any]:
    del jax
    return {
        "master": param_shardings,
        "params": param_shardings,
        "opt": {"step": scalar_sharding, "m": param_shardings, "v": param_shardings},
        "finite": scalar_sharding,
    }


def _sample_steps(steps: int, *, every_step: bool = False, include: tuple[int, ...] = ()) -> set[int]:
    if every_step:
        return set(range(1, steps + 1))
    points = {1, 2, 5, 10, 20, 50, 100, 150, 200, 300, steps}
    points.update(include)
    return {step for step in points if 1 <= step <= steps}


def _compiled_text_markers(compiled: Any) -> dict[str, bool]:
    try:
        text = compiled.as_text()
    except Exception:
        text = ""
    normalized = text.lower().replace("_", "-")
    return {
        "all_gather": "all-gather" in normalized,
        "reduce_scatter": "reduce-scatter" in normalized,
        "all_reduce": "all-reduce" in normalized,
    }


def _run_tp_collective_probe(jax: Any, jnp: Any, devices: list[Any]) -> dict[str, Any]:
    import numpy as np
    from jax.sharding import Mesh
    from jax.sharding import NamedSharding
    from jax.sharding import PartitionSpec as P

    mesh = Mesh(np.array(devices).reshape((4, 2)), ("data", "model"))
    sharding = NamedSharding(mesh, P("data", "model"))
    value = jax.device_put(np.arange(8, dtype=np.float32).reshape(4, 2), sharding)

    def probe(x):
        def mapped(local):
            gathered = jax.lax.all_gather(local, "model", axis=1, tiled=True)
            scattered = jax.lax.psum_scatter(gathered, "model", scatter_dimension=1, tiled=True)
            return scattered

        return _shard_map_compat(jax, mapped, mesh=mesh, in_specs=P("data", "model"), out_specs=P("data", "model"))(x)

    compiled = jax.jit(probe, in_shardings=sharding, out_shardings=sharding).lower(value).compile()
    output = compiled(value)
    jax.block_until_ready(output)
    markers = _compiled_text_markers(compiled)
    return {
        **markers,
        "output_checksum": float(jax.device_get(jnp.sum(output.astype(jnp.float32)))),
    }


def _estimate_mfu(tokens: int, elapsed: float, device_count: int) -> tuple[float, float]:
    training_flops = float(tokens) * float(6 * TARGET_PARAMETER_COUNT)
    peak = B200_BF16_PEAK_FLOPS_PER_GPU * float(device_count)
    if elapsed <= 0.0 or peak <= 0.0:
        return 0.0, training_flops
    return training_flops / (elapsed * peak), training_flops


def _run_gpt_training(
    *,
    steps: int,
    seed: int,
    layout: str,
    sample_every_step: bool = False,
    capture_steps: tuple[int, ...] = (),
    batch_size: int | None = None,
) -> dict[str, Any]:
    jax, jnp = _jax_imports()
    if batch_size is None:
        batch_size = _configured_global_batch()
    devices = _require_gpu_devices(jax, 8)
    sharding_ctx = _make_sharding_context(jax, devices, layout=layout)
    master = _init_gpt_master_params(jax, jnp, seed=seed)
    param_shardings = _tree_named_map(master, sharding_ctx["param_sharding_for"])
    compute_param_shardings = None
    if "compute_param_sharding_for" in sharding_ctx:
        compute_param_shardings = _tree_named_map(master, sharding_ctx["compute_param_sharding_for"])
    sharding_ctx["compute_param_shardings"] = compute_param_shardings
    master = _put_tree(jax, master, param_shardings)
    params = _to_bf16_params(jax, jnp, master)
    opt = _adamw_state(jax, jnp, master)
    state = {
        "master": master,
        "params": params,
        "opt": opt,
        "finite": jnp.asarray(True),
    }
    state_shardings = _state_shardings(jax, param_shardings, sharding_ctx["scalar"])
    state = _put_tree(jax, state, state_shardings)
    first_batch = jax.device_put(_make_lm_batch(0, batch_size=batch_size), sharding_ctx["batch"])
    train_step_fn = _make_train_step(jax, jnp, layout=layout, shardings=sharding_ctx, total_steps=steps)
    train_step = jax.jit(
        train_step_fn,
        in_shardings=(state_shardings, sharding_ctx["batch"]),
        out_shardings=(
            state_shardings,
            sharding_ctx["scalar"],
            sharding_ctx["scalar"],
            sharding_ctx["scalar"],
            sharding_ctx["scalar"],
        ),
        donate_argnums=(0,),
    )
    compile_start = time.perf_counter()
    compiled = train_step.lower(state, first_batch).compile()
    compile_seconds = time.perf_counter() - compile_start
    collective_markers = _compiled_text_markers(compiled)
    sample_points = _sample_steps(steps, every_step=sample_every_step, include=capture_steps)
    loss_curve: list[dict[str, float]] = []
    loss_by_step: dict[int, float] = {}
    grad_norm_by_step: dict[int, float] = {}
    lr_by_step: dict[int, float] = {}
    collective_checksum_by_step: dict[int, float] = {}
    initial_loss: float | None = None
    final_loss: float | None = None
    start = time.perf_counter()
    for step_index in range(steps):
        if step_index == 0:
            batch = first_batch
        else:
            batch = jax.device_put(_make_lm_batch(step_index, batch_size=batch_size), sharding_ctx["batch"])
        state, loss, grad_norm, lr, collective_checksum = compiled(state, batch)
        step_number = step_index + 1
        if step_number in sample_points:
            loss_value = float(jax.device_get(loss))
            grad_norm_value = float(jax.device_get(grad_norm))
            lr_value = float(jax.device_get(lr))
            collective_checksum_value = float(jax.device_get(collective_checksum))
            loss_curve.append({"step": step_number, "loss": loss_value})
            loss_by_step[step_number] = loss_value
            grad_norm_by_step[step_number] = grad_norm_value
            lr_by_step[step_number] = lr_value
            collective_checksum_by_step[step_number] = collective_checksum_value
            if step_number == 1:
                initial_loss = loss_value
            if step_number == steps:
                final_loss = loss_value
    jax.block_until_ready(state)
    elapsed = time.perf_counter() - start
    if initial_loss is None:
        initial_loss = loss_by_step.get(1)
    if final_loss is None:
        final_loss = loss_by_step.get(steps)
    finite = bool(jax.device_get(state["finite"]))
    tokens = batch_size * SEQ_LEN * steps
    tokens_per_second = tokens / elapsed if elapsed > 0.0 else 0.0
    mfu, estimated_flops = _estimate_mfu(tokens, elapsed, len(devices))
    metrics = {
        **_runtime_metadata(jax, devices),
        "steps": steps,
        "seed": seed,
        "global_batch": batch_size,
        "seq_len": SEQ_LEN,
        "layers": N_LAYERS,
        "model_dim": MODEL_DIM,
        "heads": N_HEADS,
        "head_dim": HEAD_DIM,
        "mlp_dim": MLP_DIM,
        "vocab_size": VOCAB_SIZE,
        "active_vocab_size": ACTIVE_VOCAB_SIZE,
        "parameter_count": TARGET_PARAMETER_COUNT,
        "parameter_breakdown": model_size_breakdown(),
        "initial_loss": initial_loss,
        "final_loss": final_loss,
        "losses": [sample["loss"] for sample in loss_curve],
        "loss_curve": loss_curve,
        "loss_by_step": {str(step): value for step, value in sorted(loss_by_step.items())},
        "grad_norm_by_step": {str(step): value for step, value in sorted(grad_norm_by_step.items())},
        "lr_by_step": {str(step): value for step, value in sorted(lr_by_step.items())},
        "collective_checksum_by_step": {
            str(step): value for step, value in sorted(collective_checksum_by_step.items())
        },
        "finite": finite,
        "data_distribution": "fixed_seed_markov_chain",
        "data_entropy_nats": DATA_ENTROPY_NATS,
        "data_entropy_floor_nats": DATA_ENTROPY_FLOOR_NATS,
        "markov_major_probability": MARKOV_MAJOR_PROBABILITY,
        "markov_minor_probability": MARKOV_MINOR_PROBABILITY,
        "loss_floor_upper_nats": LOSS_FLOOR_UPPER_NATS,
        "loss_floor_tolerance_ratio": MARKOV_LOSS_FLOOR_TOLERANCE_RATIO,
        "final_loss_floor_ratio": (
            None if final_loss is None else float(final_loss) / DATA_ENTROPY_FLOOR_NATS
        ),
        "final_loss_floor_gap_nats": (
            None if final_loss is None else float(final_loss) - DATA_ENTROPY_FLOOR_NATS
        ),
        "final_loss_entropy_ratio": (
            None if final_loss is None else float(final_loss) / DATA_ENTROPY_NATS
        ),
        "final_loss_entropy_gap_nats": (
            None if final_loss is None else float(final_loss) - DATA_ENTROPY_NATS
        ),
        "wall_time_seconds": elapsed,
        "compile_seconds": compile_seconds,
        "tokens_per_second": tokens_per_second,
        "mfu": mfu,
        "mfu_formula": "tokens * 6 * parameter_count / (wall_time * 8 * B200_BF16_PEAK_FLOPS_PER_GPU)",
        "estimated_training_flops": estimated_flops,
        "dtype": "bf16_params_and_activations_with_fp32_master_and_adamw_state",
        "optimizer": "hand_written_adamw",
        "lr_schedule": "linear_warmup_cosine_decay",
        "warmup_steps": WARMUP_STEPS,
        "peak_lr": PEAK_LR,
        "weight_decay": WEIGHT_DECAY,
        "grad_clip_norm": GRAD_CLIP_NORM,
        "mesh_shape": sharding_ctx["mesh_shape"],
        "param_sharding": sharding_ctx["param_kind"],
        "optimizer_sharding": sharding_ctx["param_kind"],
        "batch_sharding": "data",
        "collective_markers": collective_markers,
    }
    return metrics


def run_w5a(args: argparse.Namespace) -> dict[str, object]:
    metrics = _run_gpt_training(steps=W5A_STEPS, seed=5005, layout="fsdp", capture_steps=(W5B_STEPS,))
    return evaluate_w5a(metrics)


def _read_w5a_reference_loss(args: argparse.Namespace, step: int) -> tuple[float | None, str]:
    sibling = args.run_dir.parent / "W5a.json"
    if not sibling.is_file():
        return None, "missing"
    try:
        doc = json.loads(sibling.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None, "invalid_json"
    metrics = doc.get("metrics")
    if not isinstance(metrics, dict):
        return None, "missing_metrics"
    loss_by_step = metrics.get("loss_by_step")
    if isinstance(loss_by_step, dict) and _finite(loss_by_step.get(str(step))):
        return float(loss_by_step[str(step)]), "sibling_w5a_json"
    if step == int(metrics.get("steps", 0) or 0) and _finite(metrics.get("final_loss")):
        return float(metrics["final_loss"]), "sibling_w5a_final_loss"
    return None, "missing_step"


def run_w5b(args: argparse.Namespace) -> dict[str, object]:
    reference_loss, reference_source = _read_w5a_reference_loss(args, W5B_STEPS)
    if reference_loss is None:
        reference_metrics = _run_gpt_training(steps=W5B_STEPS, seed=5005, layout="fsdp", capture_steps=(W5B_STEPS,))
        reference_loss = float(reference_metrics["final_loss"])
        reference_source = "inline_w5a_200_step_reference"

    metrics = _run_gpt_training(steps=W5B_STEPS, seed=5005, layout="tp2d", capture_steps=(W5B_STEPS,))
    jax, jnp = _jax_imports()
    devices = _require_gpu_devices(jax, 8)
    collective_probe = _run_tp_collective_probe(jax, jnp, devices)
    train_markers = metrics.get("collective_markers")
    if not isinstance(train_markers, dict):
        train_markers = {}
    metrics.update(
        {
            "w5a_reference_loss_at_step": reference_loss,
            "w5a_reference_source": reference_source,
            "loss_tolerance": W5B_LOSS_TOLERANCE_NATS,
            "tensor_parallelism": {
                "attention": "column_row",
                "mlp": "column_row",
                "all_gather": bool(train_markers.get("all_gather")) or bool(collective_probe.get("all_gather")),
                "reduce_scatter": bool(train_markers.get("reduce_scatter"))
                or bool(collective_probe.get("reduce_scatter")),
                "train_hlo_all_gather_marker": bool(train_markers.get("all_gather")),
                "train_hlo_reduce_scatter_marker": bool(train_markers.get("reduce_scatter")),
                "probe_hlo_all_gather_marker": bool(collective_probe.get("all_gather")),
                "probe_hlo_reduce_scatter_marker": bool(collective_probe.get("reduce_scatter")),
            },
            "tensor_parallel_collective_probe": collective_probe,
        }
    )
    return evaluate_w5b(metrics)


def _small_block_params(jax: Any, jnp: Any) -> dict[str, Any]:
    del jax
    vocab = 128
    d_model = 64
    heads = 4
    head_dim = d_model // heads
    mlp_dim = 128

    def values(shape: tuple[int, ...], scale: float) -> Any:
        size = math.prod(shape)
        data = jnp.arange(size, dtype=jnp.float32).reshape(shape)
        return (jnp.sin(data * 0.017) * scale).astype(jnp.float32)

    blocks = []
    for layer in range(2):
        offset = float(layer + 1)
        blocks.append(
            {
                "ln1_scale": jnp.ones((d_model,), dtype=jnp.float32),
                "ln1_bias": values((d_model,), 0.001 * offset),
                "qkv": values((d_model, 3 * d_model), 0.02),
                "attn_out": values((d_model, d_model), 0.02),
                "ln2_scale": jnp.ones((d_model,), dtype=jnp.float32),
                "ln2_bias": values((d_model,), 0.001 * offset),
                "mlp_in": values((d_model, mlp_dim), 0.02),
                "mlp_out": values((mlp_dim, d_model), 0.02),
            }
        )
    return {
        "tok": values((vocab, d_model), 0.02),
        "pos": values((64, d_model), 0.01),
        "blocks": tuple(blocks),
        "ln_f_scale": jnp.ones((d_model,), dtype=jnp.float32),
        "ln_f_bias": jnp.zeros((d_model,), dtype=jnp.float32),
        "out_bias": jnp.zeros((vocab,), dtype=jnp.float32),
    }


def _small_block_loss(jax: Any, jnp: Any, params: dict[str, Any], tokens: Any) -> Any:
    d_model = params["tok"].shape[1]
    heads = 4
    head_dim = d_model // heads
    x = params["tok"][tokens[:, :-1]] + params["pos"][jnp.arange(tokens.shape[1] - 1)][None, :, :]
    for block in params["blocks"]:
        h = _layer_norm_fp32(jnp, x, block["ln1_scale"], block["ln1_bias"])
        qkv = (h @ block["qkv"]).reshape(tokens.shape[0], tokens.shape[1] - 1, 3, heads, head_dim)
        q, k, v = jnp.moveaxis(qkv, 2, 0)
        attn = jax.nn.dot_product_attention(q, k, v, is_causal=True, implementation="xla")
        x = x + attn.reshape(tokens.shape[0], tokens.shape[1] - 1, d_model) @ block["attn_out"]
        h = _layer_norm_fp32(jnp, x, block["ln2_scale"], block["ln2_bias"])
        x = x + jax.nn.gelu(h @ block["mlp_in"]) @ block["mlp_out"]
    x = _layer_norm_fp32(jnp, x, params["ln_f_scale"], params["ln_f_bias"])
    logits = jnp.einsum("btd,vd->btv", x, params["tok"], preferred_element_type=jnp.float32) + params["out_bias"]
    targets = tokens[:, 1:]
    target_logits = jnp.take_along_axis(logits, targets[..., None], axis=-1)[..., 0]
    return jnp.mean(jax.nn.logsumexp(logits, axis=-1) - target_logits)


def _layer_norm_fp32(jnp: Any, x: Any, scale: Any, bias: Any) -> Any:
    mean = jnp.mean(x, axis=-1, keepdims=True)
    var = jnp.mean((x - mean) ** 2, axis=-1, keepdims=True)
    normalized = (x - mean) * jax_rsqrte(jnp, var + jnp.asarray(1.0e-5, dtype=jnp.float32))
    return normalized * scale + bias


def _small_tokens(jnp: Any) -> Any:
    offsets = jnp.arange(65, dtype=jnp.int32)
    starts = jnp.arange(2, dtype=jnp.int32)[:, None] * 11
    return (starts + offsets[None, :]) % 128


def _probe_w5c(args: argparse.Namespace) -> int:
    import numpy as np

    jax, jnp = _jax_imports()
    backend = args.backend
    devices = jax.devices("gpu" if backend in ("gpu", "cuda") else "cpu")
    if not devices:
        raise RuntimeError(f"JAX backend {backend!r} has no devices")
    with jax.default_device(devices[0]), jax.default_matmul_precision("highest"):
        params = _small_block_params(jax, jnp)
        tokens = _small_tokens(jnp)

        @jax.jit
        def loss_and_grad(params, tokens):
            return jax.value_and_grad(lambda p: _small_block_loss(jax, jnp, p, tokens))(params)

        loss, grads = loss_and_grad(params, tokens)
        loss = float(jax.device_get(loss))
        flat_grads = []
        for leaf in _tree_leaves(jax, grads):
            flat_grads.append(np.ravel(np.asarray(jax.device_get(leaf), dtype=np.float32)))
        grads_np = np.concatenate(flat_grads) if flat_grads else np.zeros((0,), dtype=np.float32)
        array_path = Path(args.probe_array_out)
        array_path.parent.mkdir(parents=True, exist_ok=True)
        np.save(array_path, grads_np)
    _write_json(
        Path(args.probe_out),
        {
            "backend": backend,
            "platform": devices[0].platform,
            "device": str(devices[0]),
            "xla_flags": os.environ.get("XLA_FLAGS", ""),
            "loss": loss,
            "grad_array": str(args.probe_array_out),
            "grad_size": int(grads_np.size),
        },
    )
    return 0


def _max_relative_error(reference: Any, candidate: Any) -> float:
    import numpy as np

    ref = np.asarray(reference, dtype=np.float64)
    cand = np.asarray(candidate, dtype=np.float64)
    denom = max(float(np.max(np.abs(ref))), 1.0e-12)
    return float(np.max(np.abs(ref - cand)) / denom)


def _run_probe(args: argparse.Namespace, name: str, env_updates: dict[str, str], *, timeout: float = 600.0) -> dict[str, Any]:
    probe_dir = args.run_dir / "probes" / name
    probe_dir.mkdir(parents=True, exist_ok=True)
    json_out = probe_dir / "result.json"
    array_out = probe_dir / ("arrays.npz" if name.startswith("w5e") else "arrays.npy")
    stdout_path = probe_dir / "stdout.txt"
    stderr_path = probe_dir / "stderr.txt"
    env = dict(os.environ)
    env.update(env_updates)
    env["JAX_COMPILATION_CACHE_DIR"] = str(probe_dir / "jax_cache")
    command = [
        sys.executable,
        "-m",
        "workloads.w5_jax_model",
        "--probe",
        name,
        "--probe-out",
        str(json_out),
        "--probe-array-out",
        str(array_out),
        "--run-dir",
        str(probe_dir),
    ]
    if "w5c" in name:
        backend = "gpu" if env_updates["JAX_PLATFORMS"] == "cuda" else env_updates["JAX_PLATFORMS"]
        command.extend(["--backend", backend])
    started = time.perf_counter()
    returncode = 0
    exec_error = ""
    with stdout_path.open("w", encoding="utf-8") as stdout, stderr_path.open("w", encoding="utf-8") as stderr:
        try:
            completed = subprocess.run(
                command,
                cwd=str(Path(__file__).resolve().parents[1]),
                env=env,
                stdout=stdout,
                stderr=stderr,
                text=True,
                check=False,
                timeout=timeout,
            )
            returncode = completed.returncode
        except subprocess.TimeoutExpired as exc:  # pragma: no cover - live runtime failure path
            exec_error = f"TimeoutExpired after {exc.timeout} seconds"
            stderr.write(exec_error + "\n")
            returncode = 124
    result: dict[str, Any]
    if json_out.is_file():
        result = json.loads(json_out.read_text(encoding="utf-8"))
    else:
        result = {}
    result.update(
        {
            "name": name,
            "returncode": returncode,
            "stdout": str(stdout_path),
            "stderr": str(stderr_path),
            "duration_seconds": time.perf_counter() - started,
        }
    )
    if exec_error:
        result["exec_error"] = exec_error
    return result


def run_w5c(args: argparse.Namespace) -> dict[str, object]:
    import numpy as np

    started = time.perf_counter()
    gpu = _run_probe(args, "w5c-gpu", {"JAX_PLATFORMS": "cuda"}, timeout=900.0)
    cpu = _run_probe(args, "w5c-cpu", {"JAX_PLATFORMS": "cpu"}, timeout=900.0)
    loss_rel = None
    grad_rel = None
    finite = False
    if gpu.get("returncode") == 0 and cpu.get("returncode") == 0:
        gpu_grad = np.load(str(gpu["grad_array"]))
        cpu_grad = np.load(str(cpu["grad_array"]))
        loss_rel = _max_relative_error(float(cpu["loss"]), float(gpu["loss"]))
        grad_rel = _max_relative_error(cpu_grad, gpu_grad)
        finite = math.isfinite(loss_rel) and math.isfinite(grad_rel)
    metrics = {
        **_nontraining_metric_envelope(
            steps=1,
            started=started,
            tokens=256,
            device_info={
                "device_count": 1 if gpu.get("returncode") == 0 else 0,
                "devices": [str(gpu.get("device", ""))] if gpu.get("device") else [],
                "platform": str(gpu.get("platform", "")),
            },
        ),
        "gpu_ran": gpu.get("returncode") == 0,
        "cpu_ran": cpu.get("returncode") == 0,
        "gpu_backend": gpu.get("platform", "gpu"),
        "cpu_backend": cpu.get("platform", "cpu"),
        "gpu_probe": gpu,
        "cpu_probe": cpu,
        "loss_max_relative_error": loss_rel,
        "grad_max_relative_error": grad_rel,
        "tolerance": CPU_GPU_TOLERANCE,
        "finite": finite,
    }
    if gpu.get("returncode") == 0 and _finite(gpu.get("loss")):
        metrics["loss_curve"] = [{"step": 1, "loss": float(gpu["loss"])}]
        metrics["losses"] = [float(gpu["loss"])]
    return evaluate_w5c(metrics)


def _attention_case(jax: Any, jnp: Any, seq_len: int, implementation: str) -> tuple[Any, Any, Any]:
    key = jax.random.key(7100 + seq_len)
    q_key, k_key, v_key = jax.random.split(key, 3)
    shape = (1, seq_len, 4, 64)
    q = (jax.random.normal(q_key, shape, dtype=jnp.float32) * 0.25).astype(jnp.bfloat16)
    k = (jax.random.normal(k_key, shape, dtype=jnp.float32) * 0.25).astype(jnp.bfloat16)
    v = (jax.random.normal(v_key, shape, dtype=jnp.float32) * 0.25).astype(jnp.bfloat16)

    @jax.jit
    def run(q, k, v):
        def loss_fn(q, k, v):
            out = jax.nn.dot_product_attention(q, k, v, is_causal=True, implementation=implementation)
            return jnp.mean(out.astype(jnp.float32) * out.astype(jnp.float32)), out

        (loss, out), grads = jax.value_and_grad(loss_fn, argnums=(0, 1, 2), has_aux=True)(q, k, v)
        return out, loss, grads

    return run(q, k, v)


def run_w5d(args: argparse.Namespace) -> dict[str, object]:
    jax, jnp = _jax_imports()
    devices = _require_gpu_devices(jax, 1)
    started = time.perf_counter()
    per_seq: list[dict[str, Any]] = []
    cudnn_ran = True
    xla_ran = True
    max_output_abs_error = 0.0
    max_grad_abs_error = 0.0
    finite = True
    with jax.default_device(devices[0]):
        for seq_len in ATTENTION_SEQ_LENGTHS:
            item: dict[str, Any] = {"seq_len": seq_len}
            try:
                xla_out, xla_loss, xla_grads = _attention_case(jax, jnp, seq_len, "xla")
                jax.block_until_ready(xla_out)
                item["xla_loss"] = float(jax.device_get(xla_loss))
            except Exception as exc:  # pragma: no cover - live runtime failure path
                xla_ran = False
                finite = False
                item["xla_error"] = f"{type(exc).__name__}: {exc}"
                per_seq.append(item)
                continue
            try:
                cudnn_out, cudnn_loss, cudnn_grads = _attention_case(jax, jnp, seq_len, "cudnn")
                jax.block_until_ready(cudnn_out)
                item["cudnn_loss"] = float(jax.device_get(cudnn_loss))
            except Exception as exc:  # pragma: no cover - live runtime failure path
                cudnn_ran = False
                finite = False
                item["cudnn_error"] = f"{type(exc).__name__}: {exc}"
                per_seq.append(item)
                continue
            output_error = float(jax.device_get(jnp.max(jnp.abs(xla_out.astype(jnp.float32) - cudnn_out.astype(jnp.float32)))))
            grad_error = 0.0
            for xla_grad, cudnn_grad in zip(xla_grads, cudnn_grads):
                grad_error = max(
                    grad_error,
                    float(jax.device_get(jnp.max(jnp.abs(xla_grad.astype(jnp.float32) - cudnn_grad.astype(jnp.float32))))),
                )
            max_output_abs_error = max(max_output_abs_error, output_error)
            max_grad_abs_error = max(max_grad_abs_error, grad_error)
            finite = finite and math.isfinite(output_error) and math.isfinite(grad_error)
            item.update({"output_abs_error": output_error, "grad_abs_error": grad_error})
            per_seq.append(item)
    metrics = {
        **_nontraining_metric_envelope(
            steps=len(ATTENTION_SEQ_LENGTHS),
            started=started,
            tokens=sum(ATTENTION_SEQ_LENGTHS),
            device_info=_runtime_metadata(jax, devices),
        ),
        **_runtime_metadata(jax, devices),
        "seq_lengths": list(ATTENTION_SEQ_LENGTHS),
        "cudnn_ran": cudnn_ran,
        "xla_ran": xla_ran,
        "max_output_abs_error": max_output_abs_error,
        "max_grad_abs_error": max_grad_abs_error,
        "tolerance": BF16_ATTENTION_TOLERANCE,
        "finite": finite,
        "per_sequence": per_seq,
    }
    metrics["loss_curve"] = [
        {"step": index + 1, "loss": float(item["xla_loss"])}
        for index, item in enumerate(per_seq)
        if _finite(item.get("xla_loss"))
    ]
    metrics["losses"] = [sample["loss"] for sample in metrics["loss_curve"]]
    return evaluate_w5d(metrics)


def _w5e_inputs(jax: Any, jnp: Any) -> tuple[Any, Any, Any]:
    size = W5E_MAT_DIM
    data = jnp.arange(size * size, dtype=jnp.float32).reshape(size, size)
    a = (jnp.sin(data * 0.003) * 0.25).astype(jnp.bfloat16)
    w1 = (jnp.cos(data * 0.005) * 0.25).astype(jnp.bfloat16)
    w2 = (jnp.sin(data * 0.007 + 0.5) * 0.25).astype(jnp.bfloat16)
    return a, w1, w2


def _probe_w5e_xla(args: argparse.Namespace) -> int:
    import numpy as np

    jax, jnp = _jax_imports()
    devices = _require_gpu_devices(jax, 1)
    with jax.default_device(devices[0]):
        a, w1, w2 = _w5e_inputs(jax, jnp)

        @jax.jit
        def step(a, w1, w2):
            def loss_fn(w1, w2):
                matmul = (a @ w1).astype(jnp.float32)
                hidden = jax.nn.gelu(matmul).astype(jnp.bfloat16)
                out = (hidden @ w2).astype(jnp.float32)
                return jnp.mean(out * out), (matmul, out)

            (loss, (matmul, out)), grads = jax.value_and_grad(loss_fn, argnums=(0, 1), has_aux=True)(w1, w2)
            return loss, matmul, out, grads

        loss, matmul, out, grads = step(a, w1, w2)
        jax.block_until_ready(out)
        array_path = Path(args.probe_array_out)
        array_path.parent.mkdir(parents=True, exist_ok=True)
        np.savez(
            array_path,
            loss=np.asarray([float(jax.device_get(loss))], dtype=np.float32),
            matmul=np.asarray(jax.device_get(matmul), dtype=np.float32),
            out=np.asarray(jax.device_get(out), dtype=np.float32),
            grad_w1=np.asarray(jax.device_get(grads[0]), dtype=np.float32),
            grad_w2=np.asarray(jax.device_get(grads[1]), dtype=np.float32),
        )
    _write_json(
        Path(args.probe_out),
        {
            "platform": devices[0].platform,
            "device": str(devices[0]),
            "arrays": str(args.probe_array_out),
            "xla_flags": os.environ.get("XLA_FLAGS", ""),
        },
    )
    return 0


def _pallas_matmul(jax: Any, jnp: Any, x: Any, y: Any) -> Any:
    from jax.experimental.pallas.ops.gpu import blackwell_matmul_mgpu

    block_m = 128
    block_n = 128
    block_k = 64
    m, k = x.shape
    k2, n = y.shape
    if k != k2:
        raise ValueError(f"incompatible matmul shapes {x.shape} and {y.shape}")
    for name, value, tile in (("M", m, block_m), ("N", n, block_n), ("K", k, block_k)):
        if value % tile != 0:
            raise ValueError(f"matmul {name} dimension {value} must be divisible by pallas tile {tile}")
    config = blackwell_matmul_mgpu.TuningConfig(
        tile_m=block_m,
        tile_n=block_n,
        tile_k=block_k,
        max_concurrent_steps=2,
        collective=False,
        epilogue_tile_n=32,
    )
    return blackwell_matmul_mgpu.matmul_kernel(x, y, config)


def _probe_w5e_pallas(args: argparse.Namespace) -> int:
    import numpy as np

    jax, jnp = _jax_imports()
    devices = _require_gpu_devices(jax, 1)
    with jax.default_device(devices[0]):
        a, w1, _ = _w5e_inputs(jax, jnp)

        @jax.jit
        def run(a, w1):
            return _pallas_matmul(jax, jnp, a, w1)

        out = run(a, w1)
        jax.block_until_ready(out)
        array_path = Path(args.probe_array_out)
        array_path.parent.mkdir(parents=True, exist_ok=True)
        np.savez(array_path, matmul=np.asarray(jax.device_get(out), dtype=np.float32))
    _write_json(
        Path(args.probe_out),
        {
            "platform": devices[0].platform,
            "device": str(devices[0]),
            "arrays": str(args.probe_array_out),
            "xla_flags": os.environ.get("XLA_FLAGS", ""),
        },
    )
    return 0


def _xla_flags_with(*flags: str) -> str:
    drop_prefixes = ("--xla_gpu_enable_triton_gemm=", "--xla_gpu_triton_gemm_any=")
    existing = [
        flag
        for flag in os.environ.get("XLA_FLAGS", "").split()
        if not any(flag.startswith(prefix) for prefix in drop_prefixes)
    ]
    return " ".join([*existing, *flags])


def run_w5e(args: argparse.Namespace) -> dict[str, object]:
    import numpy as np

    started = time.perf_counter()
    enabled_flags = _xla_flags_with(*XLA_TRITON_GEMM_ENABLE_FLAGS)
    disabled_flags = _xla_flags_with(*XLA_TRITON_GEMM_DISABLE_FLAGS)
    enabled = _run_probe(args, "w5e-xla-enabled", {"JAX_PLATFORMS": "cuda", "XLA_FLAGS": enabled_flags})
    disabled = _run_probe(args, "w5e-xla-disabled", {"JAX_PLATFORMS": "cuda", "XLA_FLAGS": disabled_flags})
    pallas = _run_probe(args, "w5e-pallas", {"JAX_PLATFORMS": "cuda", "XLA_FLAGS": enabled_flags})
    enabled_disabled_error = None
    pallas_error = None
    if enabled.get("returncode") == 0 and disabled.get("returncode") == 0:
        enabled_arrays = np.load(str(enabled["arrays"]))
        disabled_arrays = np.load(str(disabled["arrays"]))
        enabled_disabled_error = max(
            _max_relative_error(disabled_arrays["loss"], enabled_arrays["loss"]),
            float(np.max(np.abs(disabled_arrays["out"] - enabled_arrays["out"]))),
            float(np.max(np.abs(disabled_arrays["grad_w1"] - enabled_arrays["grad_w1"]))),
            float(np.max(np.abs(disabled_arrays["grad_w2"] - enabled_arrays["grad_w2"]))),
        )
    if enabled.get("returncode") == 0 and pallas.get("returncode") == 0:
        enabled_arrays = np.load(str(enabled["arrays"]))
        pallas_arrays = np.load(str(pallas["arrays"]))
        pallas_error = float(np.max(np.abs(enabled_arrays["matmul"] - pallas_arrays["matmul"])))
    device = enabled.get("device") or disabled.get("device") or pallas.get("device")
    metrics = {
        **_nontraining_metric_envelope(
            steps=3,
            started=started,
            device_info={
                "device_count": 1 if enabled.get("returncode") == 0 else 0,
                "devices": [str(device)] if device else [],
                "platform": str(enabled.get("platform") or disabled.get("platform") or pallas.get("platform") or ""),
            },
        ),
        "xla_triton_enabled_ran": enabled.get("returncode") == 0,
        "xla_triton_disabled_ran": disabled.get("returncode") == 0,
        "pallas_matmul_ran": pallas.get("returncode") == 0,
        "enabled_probe": enabled,
        "disabled_probe": disabled,
        "pallas_probe": pallas,
        "enabled_disabled_max_abs_error": enabled_disabled_error,
        "pallas_max_abs_error": pallas_error,
        "tolerance": GEMM_TOLERANCE,
        "xla_flags": {"enabled": enabled_flags, "disabled": disabled_flags},
        "pallas_kernel": "pallas_call_block_matmul",
    }
    if enabled.get("returncode") == 0:
        enabled_arrays = np.load(str(enabled["arrays"]))
        loss_value = float(enabled_arrays["loss"][0])
        metrics["loss_curve"] = [{"step": 1, "loss": loss_value}]
        metrics["losses"] = [loss_value]
    return evaluate_w5e(metrics)


def run_w5f(args: argparse.Namespace) -> dict[str, object]:
    started = time.perf_counter()
    seed = 5050
    deterministic_env = _enable_w5f_deterministic_mode()
    first = _run_gpt_training(steps=W5F_STEPS, seed=seed, layout="fsdp", sample_every_step=True)
    second = _run_gpt_training(steps=W5F_STEPS, seed=seed, layout="fsdp", sample_every_step=True)
    run1_losses = list(first["losses"])
    run2_losses = list(second["losses"])
    if len(run1_losses) == len(run2_losses):
        max_delta = max((abs(float(a) - float(b)) for a, b in zip(run1_losses, run2_losses)), default=0.0)
    else:
        max_delta = math.inf
    metrics = {
        **_nontraining_metric_envelope(
            steps=W5F_STEPS,
            started=started,
            tokens=GLOBAL_BATCH * SEQ_LEN * W5F_STEPS * 2,
            device_info={
                "device_count": first.get("device_count", 0),
                "devices": first.get("devices", []),
                "platform": first.get("platform", ""),
            },
        ),
        "steps": W5F_STEPS,
        "seed": seed,
        "run1_losses": run1_losses,
        "run2_losses": run2_losses,
        "max_loss_delta": max_delta,
        "tolerance": DETERMINISM_TOLERANCE,
        "deterministic_mode": True,
        **deterministic_env,
        "run1": first,
        "run2": second,
        "determinism_note": "W5f opts into XLA GPU deterministic ops plus Ring/Simple NCCL before importing JAX; default XLA GPU reductions are not expected to be bitwise repeatable.",
    }
    metrics["loss_curve"] = [
        {"step": step, "loss": float(loss)}
        for step, loss in enumerate(run1_losses, start=1)
    ]
    metrics["losses"] = list(run1_losses)
    return evaluate_w5f(metrics)


RUNNERS = {
    "W5a": run_w5a,
    "W5b": run_w5b,
    "W5c": run_w5c,
    "W5d": run_w5d,
    "W5e": run_w5e,
    "W5f": run_w5f,
}

PROBES = {
    "w5c-gpu": _probe_w5c,
    "w5c-cpu": _probe_w5c,
    "w5e-xla-enabled": _probe_w5e_xla,
    "w5e-xla-disabled": _probe_w5e_xla,
    "w5e-pallas": _probe_w5e_pallas,
}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workload", choices=sorted(RUNNERS))
    parser.add_argument("--probe", choices=sorted(PROBES))
    parser.add_argument("--probe-out")
    parser.add_argument("--probe-array-out")
    parser.add_argument("--backend", choices=("cpu", "cuda", "gpu"))
    parser.add_argument("--out")
    parser.add_argument("--run-dir", required=True, type=Path)
    parser.add_argument("--line", default="")
    parser.add_argument("--gpus", type=int, default=8)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    args.run_dir.mkdir(parents=True, exist_ok=True)
    if args.probe:
        if not args.probe_out or not args.probe_array_out:
            raise SystemExit("--probe requires --probe-out and --probe-array-out")
        return PROBES[args.probe](args)
    if not args.workload or not args.out:
        raise SystemExit("--workload and --out are required unless --probe is used")
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
