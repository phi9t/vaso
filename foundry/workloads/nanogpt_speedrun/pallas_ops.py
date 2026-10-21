"""Explicit JAX Pallas activation kernels lowered through the GPU backend."""

from __future__ import annotations

import hashlib
from functools import partial
from typing import Any

import jax
import jax.numpy as jnp
from jax.experimental import pallas as pl


_BLOCK_SIZE = 256
_ACTIVATIONS = {"gelu", "relu2", "swiglu", "situ"}


def _require_tiled_size(value: Any) -> int:
    size = int(value.size)
    if size % _BLOCK_SIZE:
        raise ValueError(
            f"Pallas activation element count {size} must be divisible by {_BLOCK_SIZE}"
        )
    return size


def _unary_forward(x: Any, kind: str) -> Any:
    original_shape = x.shape
    flat = x.reshape((-1,))
    size = _require_tiled_size(flat)

    def kernel(x_ref: Any, out_ref: Any) -> None:
        value = x_ref[...].astype(jnp.float32)
        if kind == "gelu":
            coefficient = 0.7978845608028654
            value = (
                0.5
                * value
                * (
                    1.0
                    + jnp.tanh(coefficient * (value + 0.044715 * value * value * value))
                )
            )
        else:
            value = jnp.maximum(value, 0.0) ** 2
        out_ref[...] = value.astype(x_ref.dtype)

    output = pl.pallas_call(
        kernel,
        out_shape=jax.ShapeDtypeStruct(flat.shape, flat.dtype),
        grid=(size // _BLOCK_SIZE,),
        in_specs=[pl.BlockSpec((_BLOCK_SIZE,), lambda block: (block,))],
        out_specs=pl.BlockSpec((_BLOCK_SIZE,), lambda block: (block,)),
        name=f"vaso_{kind}_forward",
    )(flat)
    return output.reshape(original_shape)


def _unary_backward(x: Any, grad: Any, kind: str) -> Any:
    original_shape = x.shape
    flat_x = x.reshape((-1,))
    flat_grad = grad.reshape((-1,))
    size = _require_tiled_size(flat_x)

    def kernel(x_ref: Any, grad_ref: Any, out_ref: Any) -> None:
        value = x_ref[...].astype(jnp.float32)
        upstream = grad_ref[...].astype(jnp.float32)
        if kind == "gelu":
            coefficient = 0.7978845608028654
            inner = coefficient * (value + 0.044715 * value * value * value)
            tanh_inner = jnp.tanh(inner)
            inner_grad = coefficient * (1.0 + 3.0 * 0.044715 * value * value)
            derivative = (
                0.5 * (1.0 + tanh_inner)
                + 0.5 * value * (1.0 - tanh_inner * tanh_inner) * inner_grad
            )
        else:
            derivative = jnp.where(value > 0.0, 2.0 * value, 0.0)
        out_ref[...] = (upstream * derivative).astype(x_ref.dtype)

    output = pl.pallas_call(
        kernel,
        out_shape=jax.ShapeDtypeStruct(flat_x.shape, flat_x.dtype),
        grid=(size // _BLOCK_SIZE,),
        in_specs=[
            pl.BlockSpec((_BLOCK_SIZE,), lambda block: (block,)),
            pl.BlockSpec((_BLOCK_SIZE,), lambda block: (block,)),
        ],
        out_specs=pl.BlockSpec((_BLOCK_SIZE,), lambda block: (block,)),
        name=f"vaso_{kind}_backward",
    )(flat_x, flat_grad)
    return output.reshape(original_shape)


def _gated_forward(x: Any, kind: str) -> Any:
    gate, up = jnp.split(x, 2, axis=-1)
    output_shape = gate.shape
    flat_gate = gate.reshape((-1,))
    flat_up = up.reshape((-1,))
    size = _require_tiled_size(flat_gate)

    def kernel(gate_ref: Any, up_ref: Any, out_ref: Any) -> None:
        gate_value = gate_ref[...].astype(jnp.float32)
        up_value = up_ref[...].astype(jnp.float32)
        sigmoid = jax.nn.sigmoid(gate_value)
        if kind == "swiglu":
            value = gate_value * sigmoid * up_value
        else:
            value = (
                4.0
                * jnp.tanh(gate_value / 4.0)
                * sigmoid
                * 25.0
                * jnp.tanh(up_value / 25.0)
            )
        out_ref[...] = value.astype(gate_ref.dtype)

    output = pl.pallas_call(
        kernel,
        out_shape=jax.ShapeDtypeStruct(flat_gate.shape, flat_gate.dtype),
        grid=(size // _BLOCK_SIZE,),
        in_specs=[
            pl.BlockSpec((_BLOCK_SIZE,), lambda block: (block,)),
            pl.BlockSpec((_BLOCK_SIZE,), lambda block: (block,)),
        ],
        out_specs=pl.BlockSpec((_BLOCK_SIZE,), lambda block: (block,)),
        name=f"vaso_{kind}_forward",
    )(flat_gate, flat_up)
    return output.reshape(output_shape)


def _gated_backward(x: Any, grad: Any, kind: str) -> Any:
    gate, up = jnp.split(x, 2, axis=-1)
    original_shape = gate.shape
    flat_gate = gate.reshape((-1,))
    flat_up = up.reshape((-1,))
    flat_grad = grad.reshape((-1,))
    size = _require_tiled_size(flat_gate)
    output_shape = (flat_gate.size * 2,)

    def kernel(
        gate_ref: Any,
        up_ref: Any,
        grad_ref: Any,
        gate_grad_ref: Any,
        up_grad_ref: Any,
    ) -> None:
        gate_value = gate_ref[...].astype(jnp.float32)
        up_value = up_ref[...].astype(jnp.float32)
        upstream = grad_ref[...].astype(jnp.float32)
        sigmoid = jax.nn.sigmoid(gate_value)
        if kind == "swiglu":
            activated_gate = gate_value * sigmoid
            gate_derivative = sigmoid + gate_value * sigmoid * (1.0 - sigmoid)
            activated_up = up_value
            up_derivative = 1.0
        else:
            gate_tanh = jnp.tanh(gate_value / 4.0)
            up_tanh = jnp.tanh(up_value / 25.0)
            clipped_gate = 4.0 * gate_tanh
            activated_gate = clipped_gate * sigmoid
            gate_derivative = (1.0 - gate_tanh * gate_tanh) * sigmoid + (
                clipped_gate * sigmoid * (1.0 - sigmoid)
            )
            activated_up = 25.0 * up_tanh
            up_derivative = 1.0 - up_tanh * up_tanh
        gate_grad_ref[...] = (upstream * gate_derivative * activated_up).astype(
            gate_ref.dtype
        )
        up_grad_ref[...] = (upstream * activated_gate * up_derivative).astype(
            up_ref.dtype
        )

    gate_grad, up_grad = pl.pallas_call(
        kernel,
        out_shape=(
            jax.ShapeDtypeStruct(flat_gate.shape, flat_gate.dtype),
            jax.ShapeDtypeStruct(flat_up.shape, flat_up.dtype),
        ),
        grid=(size // _BLOCK_SIZE,),
        in_specs=[
            pl.BlockSpec((_BLOCK_SIZE,), lambda block: (block,)),
            pl.BlockSpec((_BLOCK_SIZE,), lambda block: (block,)),
            pl.BlockSpec((_BLOCK_SIZE,), lambda block: (block,)),
        ],
        out_specs=(
            pl.BlockSpec((_BLOCK_SIZE,), lambda block: (block,)),
            pl.BlockSpec((_BLOCK_SIZE,), lambda block: (block,)),
        ),
        name=f"vaso_{kind}_backward",
    )(flat_gate, flat_up, flat_grad)
    del output_shape
    return jnp.concatenate(
        (gate_grad.reshape(original_shape), up_grad.reshape(original_shape)), axis=-1
    )


def _activation_forward(x: Any, kind: str) -> Any:
    if kind not in _ACTIVATIONS:
        raise ValueError(f"unsupported Pallas activation: {kind}")
    if kind in {"swiglu", "situ"}:
        return _gated_forward(x, kind)
    return _unary_forward(x, kind)


def _activation_backward(x: Any, grad: Any, kind: str) -> Any:
    if kind in {"swiglu", "situ"}:
        return _gated_backward(x, grad, kind)
    return _unary_backward(x, grad, kind)


@partial(jax.custom_vjp, nondiff_argnums=(1,))
def activation(x: Any, kind: str) -> Any:
    return _activation_forward(x, kind)


def _activation_fwd(x: Any, kind: str) -> tuple[Any, Any]:
    return _activation_forward(x, kind), x


def _activation_bwd(kind: str, x: Any, grad: Any) -> tuple[Any]:
    return (_activation_backward(x, grad, kind),)


activation.defvjp(_activation_fwd, _activation_bwd)


def reference_activation(x: Any, kind: str) -> Any:
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
    raise ValueError(f"unsupported activation: {kind}")


def parity(kind: str, *, width: int) -> dict[str, Any]:
    input_width = width * 2 if kind in {"swiglu", "situ"} else width
    key = jax.random.key(1979)
    input_value = jax.random.normal(key, (128, input_width), dtype=jnp.float32)
    grad = jax.random.normal(
        jax.random.fold_in(key, 1), (128, width), dtype=jnp.float32
    )

    def reference_loss(value: Any) -> Any:
        return jnp.sum(reference_activation(value, kind) * grad)

    def candidate_loss(value: Any) -> Any:
        return jnp.sum(activation(value, kind) * grad)

    reference_output = jax.jit(lambda value: reference_activation(value, kind))(
        input_value
    )
    candidate_output = jax.jit(lambda value: activation(value, kind))(input_value)
    reference_grad = jax.jit(jax.grad(reference_loss))(input_value)
    candidate_grad = jax.jit(jax.grad(candidate_loss))(input_value)
    jax.block_until_ready((candidate_output, candidate_grad))
    forward_error = float(
        jax.device_get(jnp.max(jnp.abs(reference_output - candidate_output)))
    )
    backward_error = float(
        jax.device_get(jnp.max(jnp.abs(reference_grad - candidate_grad)))
    )
    lowered = jax.jit(lambda value: activation(value, kind)).lower(input_value)
    compiled_text = lowered.compile().as_text()
    lowered_markers = sorted(
        marker
        for marker in ("pallas", "triton", "custom_call")
        if marker in compiled_text.lower()
    )
    return {
        "executed": "pallas" in lowered_markers and "custom_call" in lowered_markers,
        "forward_max_abs_error": forward_error,
        "backward_max_abs_error": backward_error,
        "max_abs_error": max(forward_error, backward_error),
        "tolerance": 1.0e-4 if kind == "situ" else 5.0e-5,
        "lowering_markers": lowered_markers,
        "compiled_text_sha256": hashlib.sha256(compiled_text.encode()).hexdigest(),
        "kernel": f"pallas_{kind}_forward_backward",
    }
