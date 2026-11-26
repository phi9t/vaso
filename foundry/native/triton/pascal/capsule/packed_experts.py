"""Packed Inkling expert linear operators with FP32 computation on Pascal.

``packed_expert_linear`` maps each activation row to one expert. Leading
activation dimensions are retained, so a routed ``[tokens, top_k, K]`` input
produces ``[tokens, top_k, N]``. The checkpoint's output-row order is retained:
an interleaved gate/up matrix must be split with ``[..., 0::2]`` and
``[..., 1::2]`` by its model adapter.

Supported storage contracts are explicit, rather than inferred from global
quantization metadata:

* NVFP4: uint8 ``[E, N, K/2]``, even K-index in the low nibble; E2M1 values;
  uint8 E4M3FN scales ``[E, N, K/16]``. K must be divisible by 16.
* MXFP8: uint8 E4M3FN bit views ``[E, N, K]``; uint8 UE8M0 scales
  ``[E, N, ceil(K/32)]``. Scale rows are independent, not shared in groups
  of 32. A final partial K block is supported.

``outer_scale`` is an optional CUDA FP32 scalar or E-element tensor. For
NVFP4 this is normally the checkpoint's per-expert ``amax / denominator``.
The operator computes each weight as FP32 ``(value * block_scale) * outer``
before multiplying the FP32 activation. Moving outer into an epilogue would
change rounding, underflow, overflow, and nonfinite behavior.

This is an inference operator. It neither quantizes activations nor allocates
a dequantized weight matrix. Each program decodes at most 4096 weights in
registers; larger reductions and activation batches are processed in bounded
tiles/chunks. Noncontiguous activation/index inputs may require copies. The
GEMV-based prefill path prioritizes bounded storage and correctness, without
claiming the throughput of an optimized grouped GEMM.

All tensors must reside on the same CUDA device. Weights and scales must be
contiguous. Expert IDs must be in [0, E); invalid IDs produce NaN rows and
are masked before all expert-buffer accesses, without synchronizing the hot
path to the host. IEEE nonfinite propagation is retained: E4M3FN codes
0x7f/0xff and UE8M0 code 0xff decode as NaN. UE8M0 code 0 is 2**-127,
not zero. No native FP8/BF16 or tensor-core instructions are required.
"""
from __future__ import annotations

import math

import torch
import triton
import triton.language as tl


@triton.jit
def _decode_e2m1(code):
    c = code.to(tl.uint32)
    exponent, mantissa = (c >> 1) & 3, c & 1
    twice = tl.where(
        exponent == 0, mantissa,
        (2 + mantissa) << tl.maximum(exponent.to(tl.int32) - 1, 0),
    )
    magnitude = twice.to(tl.float32) * 0.5
    # Set the sign bit explicitly, including the negative-zero encoding.
    bits = magnitude.to(tl.uint32, bitcast=True) | ((c & 8) << 28)
    return bits.to(tl.float32, bitcast=True)


@triton.jit
def _decode_e4m3fn(code):
    c = code.to(tl.uint32)
    exponent, mantissa = (c >> 3) & 15, c & 7
    normal_bits = ((exponent + 120) << 23) | (mantissa << 20)
    subnormal = mantissa.to(tl.float32) * (1.0 / 512.0)
    magnitude_bits = tl.where(
        exponent == 0, subnormal.to(tl.uint32, bitcast=True), normal_bits,
    )
    bits = magnitude_bits | ((c & 128) << 24)
    result = bits.to(tl.float32, bitcast=True)
    return tl.where((c & 127) == 127, float("nan"), result)


@triton.jit
def _decode_ue8m0(code):
    c = code.to(tl.uint32)
    # Codes 1..254 have the same exponent bits as a positive FP32 power of
    # two. Code 0 needs an FP32 subnormal; 255 is NaN, not infinity.
    bits = tl.where(c == 0, 0x00400000, c << 23)
    bits = tl.where(c == 255, 0x7FC00000, bits).to(tl.uint32)
    return bits.to(tl.float32, bitcast=True)


@triton.jit
def _packed_rows(
    X, IDS, W, S, G, Y,
    ROW_BEGIN,
    E: tl.constexpr, N: tl.constexpr, K: tl.constexpr,
    W_COLUMNS: tl.constexpr, S_COLUMNS: tl.constexpr,
    NVFP4: tl.constexpr, G_COUNT: tl.constexpr, BLOCK_K: tl.constexpr,
):
    output_column = tl.program_id(0)
    activation_row = ROW_BEGIN + tl.program_id(1)
    expert = tl.load(IDS + activation_row).to(tl.int64)
    valid_expert = (expert >= 0) & (expert < E)
    safe_expert = tl.where(valid_expert, expert, 0)
    weight_row = safe_expert * N + output_column
    if G_COUNT == 0:
        outer = 1.0
    elif G_COUNT == 1:
        outer = tl.load(G)
    else:
        outer = tl.load(G + safe_expert)

    offsets = tl.arange(0, BLOCK_K)
    partial = tl.full((BLOCK_K,), 0, tl.float32)
    for tile in range(tl.cdiv(K, BLOCK_K)):
        k = tile * BLOCK_K + offsets
        valid_k = k < K
        valid = valid_k & valid_expert
        if NVFP4:
            byte = tl.load(W + weight_row * W_COLUMNS + k // 2, valid, 0)
            code = (byte.to(tl.uint32) >> ((k & 1) * 4)) & 15
            value = _decode_e2m1(code)
            scale_code = tl.load(S + weight_row * S_COLUMNS + k // 16, valid, 0)
            scale = _decode_e4m3fn(scale_code)
        else:
            code = tl.load(W + weight_row * W_COLUMNS + k, valid, 0)
            value = _decode_e4m3fn(code)
            scale_code = tl.load(S + weight_row * S_COLUMNS + k // 32, valid, 127)
            scale = _decode_ue8m0(scale_code)
        weight = value * scale
        if G_COUNT != 0:
            weight = weight * outer
        activation = tl.load(X + activation_row.to(tl.int64) * K + k, valid_k, 0)
        # Mask after arithmetic too: padded lanes must not turn a valid
        # result into NaN when outer is nonfinite.
        contribution = tl.where(valid_k, weight * activation, 0.0)
        partial = partial + contribution
    total = tl.sum(partial, 0)
    total = tl.where(valid_expert, total, float("nan"))
    tl.store(Y + activation_row.to(tl.int64) * N + output_column, total)


def packed_expert_linear(
    x: torch.Tensor,
    expert_ids: torch.Tensor,
    packed_weight: torch.Tensor,
    block_scales: torch.Tensor,
    *,
    format: str,
    outer_scale: torch.Tensor | None = None,
    out: torch.Tensor | None = None,
    rows_per_chunk: int = 256,
) -> torch.Tensor:
    """Return FP32 expert linear outputs, preserving ``x``'s leading shape.

    ``expert_ids.shape`` must equal ``x.shape[:-1]``. Raw output row order
    is preserved. Shape/dtype/layout errors raise before launch. Invalid
    expert ID values yield NaN rows without unsafe weight accesses. ``out``
    must be contiguous and must not share storage with any input. Gradients
    through this inference-only operator are unsupported.
    """
    if format not in ("nvfp4", "mxfp8"):
        raise ValueError(f"Unsupported packed expert format: {format!r}")
    if not isinstance(rows_per_chunk, int) or not 1 <= rows_per_chunk <= 65535:
        raise ValueError("rows_per_chunk must be an integer in [1, 65535]")
    if x.ndim < 1 or x.dtype != torch.float32 or x.device.type != "cuda":
        raise ValueError("x must be a CUDA float32 tensor with at least one dimension")
    if x.shape[-1] <= 0:
        raise ValueError("The activation K dimension must be positive")
    if expert_ids.dtype not in (torch.int32, torch.int64):
        raise ValueError("expert_ids must have int32 or int64 dtype")
    if expert_ids.shape != x.shape[:-1]:
        raise ValueError("expert_ids shape must equal the leading activation shape")
    if packed_weight.ndim != 3 or packed_weight.dtype != torch.uint8:
        raise ValueError("packed_weight must have uint8 shape [E, N, packed_K]")
    if block_scales.ndim != 3 or block_scales.dtype != torch.uint8:
        raise ValueError("block_scales must have uint8 shape [E, N, scale_K]")
    if not packed_weight.is_contiguous() or not block_scales.is_contiguous():
        raise ValueError("packed_weight and block_scales must be contiguous")
    experts, outputs, stored_columns = packed_weight.shape
    if experts <= 0 or outputs <= 0:
        raise ValueError("Expert and output dimensions must be positive")
    columns = x.shape[-1]
    is_nvfp4 = format == "nvfp4"
    if is_nvfp4 and columns % 16:
        raise ValueError("NVFP4 requires K divisible by 16")
    expected_columns = columns // 2 if is_nvfp4 else columns
    scale_columns = triton.cdiv(columns, 16 if is_nvfp4 else 32)
    if stored_columns != expected_columns:
        raise ValueError(f"packed_weight last dimension must be {expected_columns}")
    expected_scales = (experts, outputs, scale_columns)
    if tuple(block_scales.shape) != expected_scales:
        raise ValueError(f"block_scales must have rowwise shape {expected_scales}")

    inputs = [x, expert_ids, packed_weight, block_scales]
    outer_count = 0
    if outer_scale is not None:
        if outer_scale.dtype != torch.float32 or outer_scale.numel() not in (1, experts):
            raise ValueError("outer_scale must be float32 with one or E elements")
        inputs.append(outer_scale)
        outer_count = outer_scale.numel()
    if any(tensor.device != x.device for tensor in inputs):
        raise ValueError("All packed expert tensors must be on the same CUDA device")
    if torch.is_grad_enabled() and any(tensor.requires_grad for tensor in inputs):
        raise ValueError("packed_expert_linear is inference-only; use torch.no_grad()")

    result_shape = (*x.shape[:-1], outputs)
    if out is None:
        out = torch.empty(result_shape, dtype=torch.float32, device=x.device)
    elif out.shape != result_shape or out.dtype != torch.float32 or out.device != x.device:
        raise ValueError(f"out must be CUDA float32 with shape {result_shape} on {x.device}")
    if not out.is_contiguous():
        raise ValueError("out must be contiguous")
    if out.numel() and any(
        out.untyped_storage().data_ptr() == tensor.untyped_storage().data_ptr()
        for tensor in inputs
    ):
        raise ValueError("out must not share storage with any input")
    if torch.is_grad_enabled() and out.requires_grad:
        raise ValueError("out must not require gradients")

    rows = math.prod(x.shape[:-1])
    if rows == 0:
        return out
    flat_x = x.reshape(rows, columns).contiguous()
    flat_ids = expert_ids.reshape(rows).contiguous()
    gamma = outer_scale.reshape(-1).contiguous() if outer_scale is not None else x
    block_k = min(4096, triton.next_power_of_2(columns))
    with torch.cuda.device(x.device):
        for row_begin in range(0, rows, rows_per_chunk):
            row_count = min(rows_per_chunk, rows - row_begin)
            _packed_rows[(outputs, row_count)](
                flat_x, flat_ids, packed_weight, block_scales, gamma, out,
                row_begin, experts, outputs, columns, stored_columns, scale_columns,
                is_nvfp4, outer_count, block_k,
                num_warps=4,
            )
    return out
