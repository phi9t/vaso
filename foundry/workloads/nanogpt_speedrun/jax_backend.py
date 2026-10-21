"""JAX implementation of the matched decoder-family workload."""

from __future__ import annotations

import importlib
import json
import math
import os
import time
from typing import Any

import jax
import jax.numpy as jnp

from .common import (
    base_result,
    finalize_case,
    measured_step_stats,
    module_identity,
    token_values,
)
from .spec import FamilySpec, RunSpec


Array = Any
Params = dict[str, Any]


def _normal(key: Array, shape: tuple[int, ...]) -> Array:
    return jax.random.normal(key, shape, dtype=jnp.float32) * 0.02


class _Keys:
    def __init__(self, seed: int) -> None:
        self._key = jax.random.key(seed)

    def take(self) -> Array:
        self._key, result = jax.random.split(self._key)
        return result


def _linear(x: Array, weight: Array) -> Array:
    return jnp.einsum(
        "...d,df->...f", x.astype(jnp.bfloat16), weight.astype(jnp.bfloat16)
    )


def _norm(x: Array, weight: Array, bias: Array | None, kind: str) -> Array:
    value = x.astype(jnp.float32)
    if kind == "layer":
        mean = jnp.mean(value, axis=-1, keepdims=True)
        variance = jnp.mean((value - mean) ** 2, axis=-1, keepdims=True)
        value = (value - mean) * jax.lax.rsqrt(variance + 1.0e-5)
        assert bias is not None
        value = value * weight + bias
    else:
        value = value * jax.lax.rsqrt(
            jnp.mean(value * value, axis=-1, keepdims=True) + 1.0e-6
        )
        value = value * weight
    return value.astype(x.dtype)


def _apply_rope(x: Array) -> Array:
    seq_len = x.shape[1]
    head_dim = x.shape[-1]
    half = head_dim // 2
    frequencies = 1.0 / (
        1_000_000.0 ** (jnp.arange(half, dtype=jnp.float32) / float(max(half, 1)))
    )
    angles = jnp.outer(jnp.arange(seq_len, dtype=jnp.float32), frequencies)
    cosine = jnp.cos(angles)[None, :, None, :].astype(x.dtype)
    sine = jnp.sin(angles)[None, :, None, :].astype(x.dtype)
    first = x[..., :half]
    second = x[..., half : 2 * half]
    rotated = jnp.concatenate(
        (first * cosine - second * sine, first * sine + second * cosine), axis=-1
    )
    if head_dim == 2 * half:
        return rotated
    return jnp.concatenate((rotated, x[..., 2 * half :]), axis=-1)


def _qk_rms_norm(x: Array) -> Array:
    scale = jax.lax.rsqrt(
        jnp.mean(x.astype(jnp.float32) ** 2, axis=-1, keepdims=True) + 1.0e-6
    )
    return x * scale.astype(x.dtype)


def _softmax_attention(
    q: Array,
    k: Array,
    v: Array,
    *,
    optimized: bool,
) -> Array:
    implementation = "cudnn" if optimized else "xla"
    return jax.nn.dot_product_attention(
        q,
        k,
        v,
        is_causal=True,
        implementation=implementation,
    )


def _linear_attention(q: Array, k: Array, v: Array) -> Array:
    q_features = jax.nn.elu(q.astype(jnp.float32)) + 1.0
    k_features = jax.nn.elu(k.astype(jnp.float32)) + 1.0
    state = jnp.cumsum(
        jnp.einsum("blhk,blhv->blhkv", k_features, v.astype(jnp.float32)),
        axis=1,
    )
    normalizer = jnp.einsum(
        "blhk,blhk->blh", q_features, jnp.cumsum(k_features, axis=1)
    )
    output = jnp.einsum("blhk,blhkv->blhv", q_features, state)
    return (output / jnp.maximum(normalizer[..., None], 1.0e-6)).astype(v.dtype)


def _sparse_attention(q: Array, k: Array, v: Array, *, top_k: int) -> Array:
    seq_len = q.shape[1]
    scores = jnp.einsum(
        "blhk,bshk->bhls", q.astype(jnp.float32), k.astype(jnp.float32)
    ) / math.sqrt(q.shape[-1])
    causal = jnp.arange(seq_len)[:, None] >= jnp.arange(seq_len)[None, :]
    scores = jnp.where(causal[None, None], scores, -jnp.inf)
    _, selected = jax.lax.top_k(scores, min(top_k, seq_len))
    selected_mask = jnp.zeros_like(scores, dtype=jnp.bool_)
    selected_mask = jnp.put_along_axis(
        selected_mask, selected, True, axis=-1, inplace=False
    )
    probabilities = jax.nn.softmax(jnp.where(selected_mask, scores, -jnp.inf), axis=-1)
    return jnp.einsum("bhls,bshk->blhk", probabilities.astype(v.dtype), v)


def _reference_activation(x: Array, kind: str) -> Array:
    if kind == "gelu":
        return jax.nn.gelu(x, approximate=True)
    if kind == "relu2":
        return jax.nn.relu(x) ** 2
    gate, up = jnp.split(x, 2, axis=-1)
    if kind == "swiglu":
        return jax.nn.silu(gate) * up
    if kind == "situ":
        return (
            4.0
            * jnp.tanh(gate / 4.0)
            * jax.nn.sigmoid(gate)
            * 25.0
            * jnp.tanh(up / 25.0)
        )
    raise ValueError(f"unknown activation: {kind}")


def _activation(x: Array, kind: str, *, optimized: bool) -> Array:
    if not optimized:
        return _reference_activation(x, kind)
    module = importlib.import_module("workloads.nanogpt_speedrun.pallas_ops")
    return module.activation(x, kind)


def _init_norm(keys: _Keys, dim: int, kind: str) -> Params:
    del keys
    return {
        "weight": jnp.ones((dim,), dtype=jnp.float32),
        "bias": jnp.zeros((dim,), dtype=jnp.float32) if kind == "layer" else None,
    }


def _init_ffn(
    keys: _Keys,
    dim: int,
    hidden_dim: int,
    activation: str,
) -> Params:
    gated = activation in {"swiglu", "situ"}
    return {
        "up": _normal(keys.take(), (dim, hidden_dim * (2 if gated else 1))),
        "down": _normal(keys.take(), (hidden_dim, dim)),
    }


def _init_attention(keys: _Keys, family: FamilySpec, run: RunSpec) -> Params:
    dim = run.model_dim
    kv_dim = family.num_kv_heads * run.head_dim
    if family.latent_qkv:
        latent_dim = max(run.head_dim, dim // 3)
        return {
            "q_down": _normal(keys.take(), (dim, latent_dim)),
            "q_up": _normal(keys.take(), (latent_dim, dim)),
            "kv_down": _normal(keys.take(), (dim, latent_dim)),
            "kv_up": _normal(keys.take(), (latent_dim, 2 * kv_dim)),
            "out": _normal(keys.take(), (dim, dim)),
        }
    return {
        "q": _normal(keys.take(), (dim, dim)),
        "kv": _normal(keys.take(), (dim, 2 * kv_dim)),
        "out": _normal(keys.take(), (dim, dim)),
    }


def _init_model(family: FamilySpec, run: RunSpec) -> Params:
    keys = _Keys(run.seed)
    layers: list[Params] = []
    for layer_index in range(run.num_layers):
        layer: Params = {
            "attention_norm": _init_norm(keys, run.model_dim, family.norm),
            "attention": _init_attention(keys, family, run),
            "ffn_norm": _init_norm(keys, run.model_dim, family.norm),
        }
        if family.moe_first_layer is not None and layer_index >= family.moe_first_layer:
            layer["moe"] = {
                "router": _normal(keys.take(), (run.model_dim, family.num_experts)),
                "experts": tuple(
                    _init_ffn(
                        keys,
                        run.model_dim,
                        run.mlp_dim,
                        family.activation,
                    )
                    for _ in range(family.num_experts)
                ),
            }
        else:
            layer["ffn"] = _init_ffn(
                keys, run.model_dim, run.mlp_dim, family.activation
            )
        layers.append(layer)
    model: Params = {
        "token_embedding": _normal(keys.take(), (run.vocab_size, run.model_dim)),
        "layers": tuple(layers),
        "final_norm": _init_norm(keys, run.model_dim, family.norm),
    }
    if family.positional == "learned":
        model["position_embedding"] = _normal(keys.take(), (run.seq_len, run.model_dim))
    if not family.tie_embeddings:
        model["lm_head"] = _normal(keys.take(), (run.model_dim, run.vocab_size))
    return model


def _attention(
    params: Params,
    x: Array,
    family: FamilySpec,
    run: RunSpec,
    *,
    layer_index: int,
    optimized: bool,
) -> Array:
    batch, seq_len, _ = x.shape
    if family.latent_qkv:
        q = _linear(_linear(x, params["q_down"]), params["q_up"])
        kv = _linear(_linear(x, params["kv_down"]), params["kv_up"])
    else:
        q = _linear(x, params["q"])
        kv = _linear(x, params["kv"])
    q = q.reshape((batch, seq_len, run.num_heads, run.head_dim))
    k, v = jnp.split(
        kv.reshape((batch, seq_len, 2, family.num_kv_heads, run.head_dim)),
        2,
        axis=2,
    )
    k = jnp.squeeze(k, axis=2)
    v = jnp.squeeze(v, axis=2)
    if family.qk_norm:
        q = _qk_rms_norm(q)
        k = _qk_rms_norm(k)
    if family.positional == "rope":
        q = _apply_rope(q)
        k = _apply_rope(k)
    repeat = run.num_heads // family.num_kv_heads
    if repeat > 1:
        k = jnp.repeat(k, repeat, axis=2)
        v = jnp.repeat(v, repeat, axis=2)
    kind = family.attention_kind(layer_index)
    if kind == "softmax":
        output = _softmax_attention(q, k, v, optimized=optimized)
    elif kind == "linear":
        output = _linear_attention(q, k, v)
    else:
        output = _sparse_attention(q, k, v, top_k=run.sparse_top_k)
    return _linear(output.reshape((batch, seq_len, run.model_dim)), params["out"])


def _ffn(
    params: Params,
    x: Array,
    family: FamilySpec,
    *,
    optimized: bool,
) -> Array:
    hidden = _activation(
        _linear(x, params["up"]), family.activation, optimized=optimized
    )
    return _linear(hidden, params["down"])


def _moe(
    params: Params,
    x: Array,
    family: FamilySpec,
    *,
    optimized: bool,
) -> Array:
    scores = jax.nn.softmax(_linear(x, params["router"]).astype(jnp.float32), axis=-1)
    values, indices = jax.lax.top_k(scores, family.experts_per_token)
    weights = jnp.zeros_like(scores)
    weights = jnp.put_along_axis(weights, indices, values, axis=-1, inplace=False)
    weights = weights / jnp.maximum(jnp.sum(weights, axis=-1, keepdims=True), 1.0e-6)
    outputs = jnp.stack(
        [_ffn(expert, x, family, optimized=optimized) for expert in params["experts"]],
        axis=-2,
    )
    return jnp.einsum("bled,ble->bld", outputs, weights.astype(outputs.dtype))


def _model(
    params: Params,
    tokens: Array,
    family: FamilySpec,
    run: RunSpec,
    *,
    optimized: bool,
) -> Array:
    x = params["token_embedding"][tokens].astype(jnp.bfloat16)
    if family.positional == "learned":
        x = x + params["position_embedding"][None, : tokens.shape[1]].astype(
            jnp.bfloat16
        )
    for layer_index, layer in enumerate(params["layers"]):
        normalized = _norm(
            x,
            layer["attention_norm"]["weight"],
            layer["attention_norm"]["bias"],
            family.norm,
        )
        x = x + _attention(
            layer["attention"],
            normalized,
            family,
            run,
            layer_index=layer_index,
            optimized=optimized,
        )
        normalized = _norm(
            x,
            layer["ffn_norm"]["weight"],
            layer["ffn_norm"]["bias"],
            family.norm,
        )
        if "moe" in layer:
            update = _moe(layer["moe"], normalized, family, optimized=optimized)
        else:
            update = _ffn(layer["ffn"], normalized, family, optimized=optimized)
        x = x + update
    x = _norm(
        x,
        params["final_norm"]["weight"],
        params["final_norm"]["bias"],
        family.norm,
    )
    if family.tie_embeddings:
        return _linear(x, params["token_embedding"].T)
    return _linear(x, params["lm_head"])


def _loss(
    params: Params,
    tokens: Array,
    labels: Array,
    family: FamilySpec,
    run: RunSpec,
    *,
    optimized: bool,
) -> Array:
    logits = _model(params, tokens, family, run, optimized=optimized).astype(
        jnp.float32
    )
    targets = jax.nn.one_hot(labels, run.vocab_size, dtype=jnp.float32)
    return -jnp.mean(jnp.sum(targets * jax.nn.log_softmax(logits), axis=-1))


def _batch(run: RunSpec, step: int) -> tuple[Array, Array]:
    values = jnp.asarray(token_values(run, step), dtype=jnp.int32).reshape(
        (run.batch_size, run.seq_len + 1)
    )
    return values[:, :-1], values[:, 1:]


def _parameter_count(params: Params) -> int:
    return sum(int(value.size) for value in jax.tree.leaves(params))


def _attention_parity(run: RunSpec) -> dict[str, Any]:
    shape = (1, min(run.seq_len, 256), run.num_heads, run.head_dim)
    key = jax.random.key(run.seed + 91)
    q_key, k_key, v_key, grad_key = jax.random.split(key, 4)
    q = jax.random.normal(q_key, shape, dtype=jnp.float32).astype(jnp.bfloat16)
    k = jax.random.normal(k_key, shape, dtype=jnp.float32).astype(jnp.bfloat16)
    v = jax.random.normal(v_key, shape, dtype=jnp.float32).astype(jnp.bfloat16)
    grad = jax.random.normal(grad_key, shape, dtype=jnp.float32).astype(jnp.bfloat16)

    def loss(inputs: tuple[Array, Array, Array], optimized: bool) -> Array:
        output = _softmax_attention(*inputs, optimized=optimized)
        return jnp.sum(output.astype(jnp.float32) * grad.astype(jnp.float32))

    inputs = (q, k, v)
    reference_output = jax.jit(
        lambda values: _softmax_attention(*values, optimized=False)
    )(inputs)
    candidate_output = jax.jit(
        lambda values: _softmax_attention(*values, optimized=True)
    )(inputs)
    reference_grads = jax.jit(jax.grad(lambda values: loss(values, False)))(inputs)
    candidate_grads = jax.jit(jax.grad(lambda values: loss(values, True)))(inputs)
    jax.block_until_ready((candidate_output, candidate_grads))
    forward_error = float(
        jax.device_get(
            jnp.max(
                jnp.abs(
                    reference_output.astype(jnp.float32)
                    - candidate_output.astype(jnp.float32)
                )
            )
        )
    )
    backward_errors = [
        float(
            jax.device_get(
                jnp.max(
                    jnp.abs(
                        reference.astype(jnp.float32) - candidate.astype(jnp.float32)
                    )
                )
            )
        )
        for reference, candidate in zip(reference_grads, candidate_grads)
    ]
    errors = [forward_error, *backward_errors]
    return {
        "executed": True,
        "backend": "jax.nn.dot_product_attention implementation=cudnn",
        "reference": "jax.nn.dot_product_attention implementation=xla",
        "forward_max_abs_error": forward_error,
        "backward_max_abs_error": max(backward_errors),
        "max_abs_error": max(errors),
        "tolerance": 0.25,
    }


def _optimization_evidence(family: FamilySpec, run: RunSpec) -> dict[str, Any]:
    module = importlib.import_module("workloads.nanogpt_speedrun.pallas_ops")
    activation = module.parity(family.activation, width=run.mlp_dim)
    attention = (
        _attention_parity(run)
        if "softmax" in family.attention_pattern
        else {
            "executed": False,
            "reason": "family has no softmax-attention layer",
        }
    )
    errors = [float(activation["max_abs_error"])]
    tolerances = [float(activation["tolerance"])]
    if attention["executed"]:
        errors.append(float(attention["max_abs_error"]))
        tolerances.append(float(attention["tolerance"]))
    attention_required = "softmax" in family.attention_pattern
    return {
        "executed": activation["executed"] is True
        and (not attention_required or attention["executed"] is True),
        "kind": "pallas_triton_activation_and_cudnn_attention",
        "activation": activation,
        "attention": attention,
        "max_abs_error": max(errors),
        "tolerance": max(tolerances),
        "jax": module_identity(jax),
    }


def run_case(family: FamilySpec, run: RunSpec, *, arm: str) -> dict[str, Any]:
    if arm not in {"baseline", "triton"}:
        raise ValueError(f"unknown arm: {arm}")
    devices = [device for device in jax.devices() if device.platform in {"cuda", "gpu"}]
    if not devices:
        raise RuntimeError("JAX speedrun requires a CUDA device")
    device = devices[0]
    optimized = arm == "triton"
    case = base_result(backend="jax", arm=arm, family=family, run=run)
    case["runtime"] = module_identity(jax)
    runtime_manifest = os.environ.get("VASO_JAX_RUNTIME_MANIFEST")
    if runtime_manifest:
        with open(runtime_manifest, encoding="utf-8") as handle:
            case["runtime_dependencies"] = json.load(handle)
    case["execution"] = {
        "device": str(device),
        "precision": "bf16 compute with fp32 master parameters",
        "optimizer": "AdamW",
        "compile": "jax.jit whole training step",
    }
    if optimized:
        case["optimization"] = _optimization_evidence(family, run)

    with jax.default_device(device):
        params = _init_model(family, run)
        first_moment = jax.tree.map(jnp.zeros_like, params)
        second_moment = jax.tree.map(jnp.zeros_like, params)

        def train_step(
            current_params: Params,
            current_first: Params,
            current_second: Params,
            tokens: Array,
            labels: Array,
            step: Array,
        ) -> tuple[Params, Params, Params, Array]:
            loss, grads = jax.value_and_grad(_loss)(
                current_params,
                tokens,
                labels,
                family,
                run,
                optimized=optimized,
            )
            squared_norm = sum(
                jnp.sum(gradient.astype(jnp.float32) ** 2)
                for gradient in jax.tree.leaves(grads)
            )
            clip_scale = jnp.minimum(1.0, 1.0 / jnp.sqrt(squared_norm + 1.0e-6))
            grads = jax.tree.map(lambda gradient: gradient * clip_scale, grads)
            first = jax.tree.map(
                lambda moment, gradient: 0.9 * moment + 0.1 * gradient,
                current_first,
                grads,
            )
            second = jax.tree.map(
                lambda moment, gradient: 0.999 * moment + 0.001 * gradient * gradient,
                current_second,
                grads,
            )
            step_float = step.astype(jnp.float32) + 1.0
            first_scale = 1.0 - 0.9**step_float
            second_scale = 1.0 - 0.999**step_float
            updated = jax.tree.map(
                lambda parameter, first_value, second_value: (
                    parameter
                    - run.learning_rate
                    * (
                        (first_value / first_scale)
                        / (jnp.sqrt(second_value / second_scale) + 1.0e-8)
                        + 0.01 * parameter
                    )
                ),
                current_params,
                first,
                second,
            )
            return updated, first, second, loss

        compiled_step = jax.jit(train_step)
        losses: list[float] = []
        step_seconds: list[float] = []
        started = time.perf_counter()
        for step in range(run.steps):
            tokens, labels = _batch(run, step)
            step_started = time.perf_counter()
            params, first_moment, second_moment, loss = compiled_step(
                params,
                first_moment,
                second_moment,
                tokens,
                labels,
                jnp.asarray(step, dtype=jnp.int32),
            )
            jax.block_until_ready(loss)
            step_seconds.append(time.perf_counter() - step_started)
            losses.append(float(jax.device_get(loss)))
        wall_time = time.perf_counter() - started
        memory_stats = device.memory_stats() or {}
    case.update(
        {
            "losses": losses,
            "timing": {
                **measured_step_stats(step_seconds, run),
                "wall_time_seconds": wall_time,
                "step_seconds": step_seconds,
            },
            "memory": {
                key: int(value)
                for key, value in memory_stats.items()
                if key in {"bytes_in_use", "peak_bytes_in_use", "bytes_limit"}
                and isinstance(value, (int, float))
            },
            "parameter_count": _parameter_count(params),
        }
    )
    return finalize_case(case)
