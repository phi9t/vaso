"""Explicit Triton activation kernels used by the Torch optimized arm."""

from __future__ import annotations

import torch
import triton
import triton.language as tl
from triton.language.extra import libdevice


_ACTIVATIONS = {"gelu": 0, "relu2": 1, "swiglu": 2, "situ": 3}


@triton.jit
def _activation_forward(
    x_ptr,
    out_ptr,
    num_outputs: tl.constexpr,
    width: tl.constexpr,
    activation: tl.constexpr,
    block_size: tl.constexpr,
):
    offsets = tl.program_id(0) * block_size + tl.arange(0, block_size)
    mask = offsets < num_outputs
    if activation == 0 or activation == 1:
        x = tl.load(x_ptr + offsets, mask=mask).to(tl.float32)
        if activation == 0:
            coefficient = 0.7978845608028654
            cubic = x * x * x
            value = (
                0.5 * x * (1.0 + libdevice.tanh(coefficient * (x + 0.044715 * cubic)))
            )
        else:
            relu = tl.maximum(x, 0.0)
            value = relu * relu
    else:
        row = offsets // width
        column = offsets - row * width
        base = row * (2 * width) + column
        gate = tl.load(x_ptr + base, mask=mask).to(tl.float32)
        up = tl.load(x_ptr + base + width, mask=mask).to(tl.float32)
        sigmoid = 1.0 / (1.0 + tl.exp(-gate))
        if activation == 2:
            value = gate * sigmoid * up
        else:
            gate_clip = 4.0 * libdevice.tanh(gate / 4.0)
            up_clip = 25.0 * libdevice.tanh(up / 25.0)
            value = gate_clip * sigmoid * up_clip
    tl.store(out_ptr + offsets, value, mask=mask)


@triton.jit
def _activation_backward(
    x_ptr,
    grad_ptr,
    out_ptr,
    num_outputs: tl.constexpr,
    width: tl.constexpr,
    activation: tl.constexpr,
    block_size: tl.constexpr,
):
    offsets = tl.program_id(0) * block_size + tl.arange(0, block_size)
    mask = offsets < num_outputs
    grad = tl.load(grad_ptr + offsets, mask=mask).to(tl.float32)
    if activation == 0 or activation == 1:
        x = tl.load(x_ptr + offsets, mask=mask).to(tl.float32)
        if activation == 0:
            coefficient = 0.7978845608028654
            inner = coefficient * (x + 0.044715 * x * x * x)
            tanh_inner = libdevice.tanh(inner)
            inner_grad = coefficient * (1.0 + 3.0 * 0.044715 * x * x)
            derivative = (
                0.5 * (1.0 + tanh_inner)
                + 0.5 * x * (1.0 - tanh_inner * tanh_inner) * inner_grad
            )
        else:
            derivative = tl.where(x > 0.0, 2.0 * x, 0.0)
        tl.store(out_ptr + offsets, grad * derivative, mask=mask)
    else:
        row = offsets // width
        column = offsets - row * width
        base = row * (2 * width) + column
        gate = tl.load(x_ptr + base, mask=mask).to(tl.float32)
        up = tl.load(x_ptr + base + width, mask=mask).to(tl.float32)
        sigmoid = 1.0 / (1.0 + tl.exp(-gate))
        if activation == 2:
            gate_value = gate * sigmoid
            gate_grad = sigmoid + gate * sigmoid * (1.0 - sigmoid)
            up_value = up
            up_grad = 1.0
        else:
            gate_tanh = libdevice.tanh(gate / 4.0)
            up_tanh = libdevice.tanh(up / 25.0)
            gate_clip = 4.0 * gate_tanh
            up_value = 25.0 * up_tanh
            gate_value = gate_clip * sigmoid
            gate_grad = (
                1.0 - gate_tanh * gate_tanh
            ) * sigmoid + gate_clip * sigmoid * (1.0 - sigmoid)
            up_grad = 1.0 - up_tanh * up_tanh
        tl.store(out_ptr + base, grad * gate_grad * up_value, mask=mask)
        tl.store(out_ptr + base + width, grad * gate_value * up_grad, mask=mask)


class _TritonActivation(torch.autograd.Function):
    @staticmethod
    def forward(ctx, x: torch.Tensor, activation: str) -> torch.Tensor:
        if activation not in _ACTIVATIONS:
            raise ValueError(f"unsupported Triton activation: {activation}")
        x = x.contiguous()
        gated = activation in {"swiglu", "situ"}
        width = x.shape[-1] // 2 if gated else x.shape[-1]
        if gated and x.shape[-1] % 2:
            raise ValueError("gated activation input width must be even")
        output_shape = (*x.shape[:-1], width)
        out = torch.empty(output_shape, device=x.device, dtype=x.dtype)
        count = out.numel()
        block_size = 256
        _activation_forward[(triton.cdiv(count, block_size),)](
            x,
            out,
            num_outputs=count,
            width=width,
            activation=_ACTIVATIONS[activation],
            block_size=block_size,
        )
        ctx.save_for_backward(x)
        ctx.activation = activation
        ctx.width = width
        return out

    @staticmethod
    def backward(ctx, grad: torch.Tensor) -> tuple[torch.Tensor, None]:
        (x,) = ctx.saved_tensors
        grad = grad.contiguous()
        out = torch.empty_like(x)
        count = grad.numel()
        block_size = 256
        _activation_backward[(triton.cdiv(count, block_size),)](
            x,
            grad,
            out,
            num_outputs=count,
            width=ctx.width,
            activation=_ACTIVATIONS[ctx.activation],
            block_size=block_size,
        )
        return out, None


def activation(x: torch.Tensor, kind: str) -> torch.Tensor:
    return _TritonActivation.apply(x, kind)


def reference_activation(x: torch.Tensor, kind: str) -> torch.Tensor:
    if kind == "gelu":
        return torch.nn.functional.gelu(x, approximate="tanh")
    if kind == "relu2":
        return torch.relu(x).square()
    gate, up = x.chunk(2, dim=-1)
    if kind == "swiglu":
        return torch.nn.functional.silu(gate) * up
    if kind == "situ":
        gate = 4.0 * torch.tanh(gate / 4.0) * torch.sigmoid(gate)
        up = 25.0 * torch.tanh(up / 25.0)
        return gate * up
    raise ValueError(f"unsupported activation: {kind}")


def parity(kind: str, *, width: int, device: torch.device) -> dict[str, float | bool]:
    input_width = width * 2 if kind in {"swiglu", "situ"} else width
    generator = torch.Generator(device=device).manual_seed(1979)
    reference_x = torch.randn(
        (128, input_width),
        generator=generator,
        device=device,
        dtype=torch.float32,
        requires_grad=True,
    )
    candidate_x = reference_x.detach().clone().requires_grad_(True)
    reference = reference_activation(reference_x, kind)
    candidate = activation(candidate_x, kind)
    grad = torch.randn(reference.shape, generator=generator, device=device)
    (reference * grad).sum().backward()
    (candidate * grad).sum().backward()
    forward_error = float((reference - candidate).abs().max().detach().cpu())
    backward_error = float(
        (reference_x.grad - candidate_x.grad).abs().max().detach().cpu()
    )
    return {
        "executed": True,
        "forward_max_abs_error": forward_error,
        "backward_max_abs_error": backward_error,
        "max_abs_error": max(forward_error, backward_error),
        "tolerance": 2.0e-5,
    }
