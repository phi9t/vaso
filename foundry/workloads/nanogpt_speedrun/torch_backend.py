"""PyTorch implementation of the matched decoder-family workload."""

from __future__ import annotations

import importlib
import math
import time
from typing import Any

import torch
import torch.nn.functional as F
from torch import nn
from torch.nn.attention import SDPBackend, sdpa_kernel

from .common import (
    base_result,
    finalize_case,
    measured_step_stats,
    module_identity,
    token_values,
)
from .spec import FamilySpec, RunSpec


def _rms_norm(
    x: torch.Tensor, weight: torch.Tensor, eps: float = 1.0e-6
) -> torch.Tensor:
    normalized = x.float() * torch.rsqrt(
        x.float().square().mean(dim=-1, keepdim=True) + eps
    )
    return normalized.to(x.dtype) * weight


def _apply_rope(x_BLHK: torch.Tensor) -> torch.Tensor:
    seq_len = x_BLHK.shape[1]
    head_dim = x_BLHK.shape[-1]
    half = head_dim // 2
    frequencies = torch.arange(half, device=x_BLHK.device, dtype=torch.float32)
    frequencies = 1.0 / (1_000_000.0 ** (frequencies / max(half, 1)))
    angles = torch.outer(
        torch.arange(seq_len, device=x_BLHK.device, dtype=torch.float32),
        frequencies,
    )
    cos = angles.cos()[None, :, None, :].to(x_BLHK.dtype)
    sin = angles.sin()[None, :, None, :].to(x_BLHK.dtype)
    first, second = x_BLHK[..., :half], x_BLHK[..., half : 2 * half]
    rotated = torch.cat(
        (first * cos - second * sin, first * sin + second * cos), dim=-1
    )
    if head_dim == 2 * half:
        return rotated
    return torch.cat((rotated, x_BLHK[..., 2 * half :]), dim=-1)


def _qk_rms_norm(x_BLHK: torch.Tensor) -> torch.Tensor:
    return x_BLHK * torch.rsqrt(
        x_BLHK.float().square().mean(dim=-1, keepdim=True) + 1.0e-6
    ).to(x_BLHK.dtype)


def _repeat_kv(x_BLHK: torch.Tensor, num_heads: int) -> torch.Tensor:
    repeat = num_heads // x_BLHK.shape[2]
    return x_BLHK.repeat_interleave(repeat, dim=2) if repeat > 1 else x_BLHK


def _softmax_attention(
    q_BLHK: torch.Tensor,
    k_BLHK: torch.Tensor,
    v_BLHK: torch.Tensor,
    *,
    optimized: bool,
) -> torch.Tensor:
    backend = SDPBackend.FLASH_ATTENTION if optimized else SDPBackend.MATH
    with sdpa_kernel(backend):
        out_BHLK = F.scaled_dot_product_attention(
            q_BLHK.transpose(1, 2),
            k_BLHK.transpose(1, 2),
            v_BLHK.transpose(1, 2),
            is_causal=True,
        )
    return out_BHLK.transpose(1, 2).contiguous()


def _linear_attention(
    q_BLHK: torch.Tensor,
    k_BLHK: torch.Tensor,
    v_BLHK: torch.Tensor,
) -> torch.Tensor:
    q_BLHK = F.elu(q_BLHK.float()) + 1.0
    k_BLHK = F.elu(k_BLHK.float()) + 1.0
    state_BLHKK = torch.einsum("blhk,blhv->blhkv", k_BLHK, v_BLHK.float()).cumsum(dim=1)
    normalizer_BLH = torch.einsum("blhk,blhk->blh", q_BLHK, k_BLHK.cumsum(dim=1))
    out_BLHK = torch.einsum("blhk,blhkv->blhv", q_BLHK, state_BLHKK)
    return (out_BLHK / normalizer_BLH.clamp_min(1.0e-6)[..., None]).to(v_BLHK.dtype)


def _sparse_attention(
    q_BLHK: torch.Tensor,
    k_BLHK: torch.Tensor,
    v_BLHK: torch.Tensor,
    *,
    top_k: int,
) -> torch.Tensor:
    seq_len = q_BLHK.shape[1]
    scores_BHLL = torch.einsum("blhk,bshk->bhls", q_BLHK.float(), k_BLHK.float())
    scores_BHLL = scores_BHLL / math.sqrt(q_BLHK.shape[-1])
    positions = torch.arange(seq_len, device=q_BLHK.device)
    causal_LL = positions[:, None] >= positions[None, :]
    scores_BHLL = scores_BHLL.masked_fill(~causal_LL, float("-inf"))
    selected = torch.topk(scores_BHLL, min(top_k, seq_len), dim=-1).indices
    selected_mask = torch.zeros_like(scores_BHLL, dtype=torch.bool)
    selected_mask.scatter_(-1, selected, True)
    probs_BHLL = torch.softmax(
        scores_BHLL.masked_fill(~selected_mask, float("-inf")), dim=-1
    )
    return torch.einsum("bhls,bshk->blhk", probs_BHLL.to(v_BLHK.dtype), v_BLHK)


def _reference_activation(x: torch.Tensor, kind: str) -> torch.Tensor:
    if kind == "gelu":
        return F.gelu(x, approximate="tanh")
    if kind == "relu2":
        return F.relu(x).square()
    gate, up = x.chunk(2, dim=-1)
    if kind == "swiglu":
        return F.silu(gate) * up
    if kind == "situ":
        gate = 4.0 * torch.tanh(gate / 4.0) * torch.sigmoid(gate)
        return gate * (25.0 * torch.tanh(up / 25.0))
    raise ValueError(f"unknown activation: {kind}")


def _activation(x: torch.Tensor, kind: str, *, triton_arm: bool) -> torch.Tensor:
    if not triton_arm:
        return _reference_activation(x, kind)
    module = importlib.import_module("workloads.nanogpt_speedrun.triton_ops")
    return module.activation(x, kind)


class Norm(nn.Module):
    def __init__(self, dim: int, kind: str) -> None:
        super().__init__()
        self.kind = kind
        if kind == "layer":
            self.layer_norm = nn.LayerNorm(dim)
            self.weight = None
        else:
            self.layer_norm = None
            self.weight = nn.Parameter(torch.ones(dim))

    def forward(self, x_BLD: torch.Tensor) -> torch.Tensor:
        if self.layer_norm is not None:
            return self.layer_norm(x_BLD)
        assert self.weight is not None
        return _rms_norm(x_BLD, self.weight)


class Attention(nn.Module):
    def __init__(
        self,
        family: FamilySpec,
        run: RunSpec,
        *,
        layer_index: int,
        optimized: bool,
    ) -> None:
        super().__init__()
        self.family = family
        self.run = run
        self.kind = family.attention_kind(layer_index)
        self.optimized = optimized
        dim = run.model_dim
        kv_dim = family.num_kv_heads * run.head_dim
        if family.latent_qkv:
            latent_dim = max(run.head_dim, dim // 3)
            self.q_proj = nn.Sequential(
                nn.Linear(dim, latent_dim, bias=False),
                nn.Linear(latent_dim, dim, bias=False),
            )
            self.kv_proj = nn.Sequential(
                nn.Linear(dim, latent_dim, bias=False),
                nn.Linear(latent_dim, 2 * kv_dim, bias=False),
            )
        else:
            self.q_proj = nn.Linear(dim, dim, bias=False)
            self.kv_proj = nn.Linear(dim, 2 * kv_dim, bias=False)
        self.out_proj = nn.Linear(dim, dim, bias=False)

    def forward(self, x_BLD: torch.Tensor) -> torch.Tensor:
        batch, seq_len, _ = x_BLD.shape
        q_BLHK = self.q_proj(x_BLD).view(
            batch, seq_len, self.run.num_heads, self.run.head_dim
        )
        k_BLHK, v_BLHK = (
            self.kv_proj(x_BLD)
            .view(batch, seq_len, 2, self.family.num_kv_heads, self.run.head_dim)
            .unbind(dim=2)
        )
        if self.family.qk_norm:
            q_BLHK = _qk_rms_norm(q_BLHK)
            k_BLHK = _qk_rms_norm(k_BLHK)
        if self.family.positional == "rope":
            q_BLHK = _apply_rope(q_BLHK)
            k_BLHK = _apply_rope(k_BLHK)
        k_BLHK = _repeat_kv(k_BLHK, self.run.num_heads)
        v_BLHK = _repeat_kv(v_BLHK, self.run.num_heads)
        if self.kind == "softmax":
            out_BLHK = _softmax_attention(
                q_BLHK,
                k_BLHK,
                v_BLHK,
                optimized=self.optimized,
            )
        elif self.kind == "linear":
            out_BLHK = _linear_attention(q_BLHK, k_BLHK, v_BLHK)
        else:
            out_BLHK = _sparse_attention(
                q_BLHK, k_BLHK, v_BLHK, top_k=self.run.sparse_top_k
            )
        return self.out_proj(out_BLHK.reshape(batch, seq_len, self.run.model_dim))


class FeedForward(nn.Module):
    def __init__(
        self,
        dim: int,
        hidden_dim: int,
        activation: str,
        *,
        triton_arm: bool,
    ) -> None:
        super().__init__()
        self.activation = activation
        self.triton_arm = triton_arm
        gated = activation in {"swiglu", "situ"}
        self.up = nn.Linear(dim, hidden_dim * (2 if gated else 1), bias=False)
        self.down = nn.Linear(hidden_dim, dim, bias=False)

    def forward(self, x_BLD: torch.Tensor) -> torch.Tensor:
        hidden = _activation(
            self.up(x_BLD), self.activation, triton_arm=self.triton_arm
        )
        return self.down(hidden)


class SparseMoE(nn.Module):
    def __init__(self, family: FamilySpec, run: RunSpec, *, triton_arm: bool) -> None:
        super().__init__()
        self.top_k = family.experts_per_token
        self.router = nn.Linear(run.model_dim, family.num_experts, bias=False)
        self.experts = nn.ModuleList(
            FeedForward(
                run.model_dim,
                run.mlp_dim,
                family.activation,
                triton_arm=triton_arm,
            )
            for _ in range(family.num_experts)
        )

    def forward(self, x_BLD: torch.Tensor) -> torch.Tensor:
        scores_BLE = torch.softmax(self.router(x_BLD).float(), dim=-1)
        values, indices = torch.topk(scores_BLE, self.top_k, dim=-1)
        weights = torch.zeros_like(scores_BLE).scatter(-1, indices, values)
        weights = weights / weights.sum(dim=-1, keepdim=True).clamp_min(1.0e-6)
        expert_outputs = torch.stack([expert(x_BLD) for expert in self.experts], dim=-2)
        return torch.einsum(
            "bled,ble->bld", expert_outputs, weights.to(expert_outputs.dtype)
        )


class DecoderBlock(nn.Module):
    def __init__(
        self,
        family: FamilySpec,
        run: RunSpec,
        *,
        layer_index: int,
        triton_arm: bool,
    ) -> None:
        super().__init__()
        self.attention_norm = Norm(run.model_dim, family.norm)
        self.attention = Attention(
            family,
            run,
            layer_index=layer_index,
            optimized=triton_arm,
        )
        self.ffn_norm = Norm(run.model_dim, family.norm)
        if family.moe_first_layer is not None and layer_index >= family.moe_first_layer:
            self.ffn = SparseMoE(family, run, triton_arm=triton_arm)
        else:
            self.ffn = FeedForward(
                run.model_dim,
                run.mlp_dim,
                family.activation,
                triton_arm=triton_arm,
            )

    def forward(self, x_BLD: torch.Tensor) -> torch.Tensor:
        x_BLD = x_BLD + self.attention(self.attention_norm(x_BLD))
        return x_BLD + self.ffn(self.ffn_norm(x_BLD))


class FamilyDecoder(nn.Module):
    def __init__(self, family: FamilySpec, run: RunSpec, *, triton_arm: bool) -> None:
        super().__init__()
        self.family = family
        self.run = run
        self.token_embedding = nn.Embedding(run.vocab_size, run.model_dim)
        self.position_embedding = (
            nn.Embedding(run.seq_len, run.model_dim)
            if family.positional == "learned"
            else None
        )
        self.layers = nn.ModuleList(
            DecoderBlock(
                family,
                run,
                layer_index=index,
                triton_arm=triton_arm,
            )
            for index in range(run.num_layers)
        )
        self.final_norm = Norm(run.model_dim, family.norm)
        self.lm_head = nn.Linear(run.model_dim, run.vocab_size, bias=False)
        if family.tie_embeddings:
            self.lm_head.weight = self.token_embedding.weight
        self.apply(self._init_weights)

    @staticmethod
    def _init_weights(module: nn.Module) -> None:
        if isinstance(module, (nn.Linear, nn.Embedding)):
            nn.init.normal_(module.weight, mean=0.0, std=0.02)
            if isinstance(module, nn.Linear) and module.bias is not None:
                nn.init.zeros_(module.bias)

    def forward(self, tokens_BL: torch.Tensor) -> torch.Tensor:
        x_BLD = self.token_embedding(tokens_BL)
        if self.position_embedding is not None:
            positions = torch.arange(tokens_BL.shape[1], device=tokens_BL.device)
            x_BLD = x_BLD + self.position_embedding(positions)[None]
        for layer in self.layers:
            x_BLD = layer(x_BLD)
        return self.lm_head(self.final_norm(x_BLD))


def _batch(
    run: RunSpec, step: int, device: torch.device
) -> tuple[torch.Tensor, torch.Tensor]:
    values = torch.tensor(token_values(run, step), device=device, dtype=torch.long)
    values = values.view(run.batch_size, run.seq_len + 1)
    return values[:, :-1], values[:, 1:]


def _attention_optimization_evidence(run: RunSpec) -> dict[str, Any]:
    device = torch.device("cuda")
    shape = (1, min(run.seq_len, 512), run.num_heads, run.head_dim)
    generator = torch.Generator(device=device).manual_seed(run.seed + 91)
    reference_inputs = tuple(
        torch.randn(
            shape,
            generator=generator,
            device=device,
            dtype=torch.bfloat16,
            requires_grad=True,
        )
        for _ in range(3)
    )
    candidate_inputs = tuple(
        value.detach().clone().requires_grad_(True) for value in reference_inputs
    )
    reference = _softmax_attention(*reference_inputs, optimized=False)
    candidate = _softmax_attention(*candidate_inputs, optimized=True)
    grad = torch.randn(
        reference.shape,
        generator=generator,
        device=device,
        dtype=reference.dtype,
    )
    reference_grads = torch.autograd.grad((reference * grad).sum(), reference_inputs)
    candidate_grads = torch.autograd.grad((candidate * grad).sum(), candidate_inputs)
    forward_error = float((reference - candidate).abs().max().detach().cpu())
    backward_errors = [
        float((reference_grad - candidate_grad).abs().max().detach().cpu())
        for reference_grad, candidate_grad in zip(reference_grads, candidate_grads)
    ]
    errors = [forward_error, *backward_errors]

    def elapsed_ms(*, optimized: bool) -> float:
        inputs = tuple(value.detach() for value in reference_inputs)
        for _ in range(5):
            _softmax_attention(*inputs, optimized=optimized)
        torch.cuda.synchronize(device)
        start = torch.cuda.Event(enable_timing=True)
        end = torch.cuda.Event(enable_timing=True)
        start.record()
        for _ in range(25):
            _softmax_attention(*inputs, optimized=optimized)
        end.record()
        end.synchronize()
        return float(start.elapsed_time(end)) / 25.0

    reference_ms = elapsed_ms(optimized=False)
    candidate_ms = elapsed_ms(optimized=True)
    return {
        "executed": True,
        "backend": "torch SDPA FLASH_ATTENTION",
        "reference": "torch SDPA MATH",
        "forward_max_abs_error": forward_error,
        "backward_max_abs_error": max(backward_errors),
        "max_abs_error": max(errors),
        "tolerance": 0.25,
        "reference_forward_ms": reference_ms,
        "candidate_forward_ms": candidate_ms,
        "microbenchmark_speedup": reference_ms / candidate_ms,
    }


def _optimization_evidence(family: FamilySpec, run: RunSpec) -> dict[str, Any]:
    module = importlib.import_module("workloads.nanogpt_speedrun.triton_ops")
    activation = module.parity(
        family.activation,
        width=run.mlp_dim,
        device=torch.device("cuda"),
    )
    attention = (
        _attention_optimization_evidence(run)
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
        "kind": "explicit_triton_activation_and_flash_attention_sdpa",
        "activation": activation,
        "attention": attention,
        "max_abs_error": max(errors),
        "tolerance": max(tolerances),
        "triton": module_identity(module.triton),
    }


class _AdamW:
    """Small dependency-free AdamW for the workload's matched training loop."""

    def __init__(
        self,
        parameters: Any,
        *,
        learning_rate: float,
        weight_decay: float,
    ) -> None:
        self.parameters = list(parameters)
        self.learning_rate = learning_rate
        self.weight_decay = weight_decay
        self.step_count = 0
        self.first_moments = [torch.zeros_like(value) for value in self.parameters]
        self.second_moments = [torch.zeros_like(value) for value in self.parameters]

    def zero_grad(self) -> None:
        for parameter in self.parameters:
            parameter.grad = None

    @torch.no_grad()
    def step(self) -> None:
        self.step_count += 1
        first_scale = 1.0 - 0.9**self.step_count
        second_scale = 1.0 - 0.999**self.step_count
        for parameter, first, second in zip(
            self.parameters, self.first_moments, self.second_moments
        ):
            if parameter.grad is None:
                continue
            gradient = parameter.grad
            first.mul_(0.9).add_(gradient, alpha=0.1)
            second.mul_(0.999).addcmul_(gradient, gradient, value=0.001)
            parameter.mul_(1.0 - self.learning_rate * self.weight_decay)
            parameter.addcdiv_(
                first / first_scale,
                (second / second_scale).sqrt().add_(1.0e-8),
                value=-self.learning_rate,
            )


def run_case(family: FamilySpec, run: RunSpec, *, arm: str) -> dict[str, Any]:
    if not torch.cuda.is_available():
        raise RuntimeError("PyTorch speedrun requires CUDA")
    if arm not in {"baseline", "triton"}:
        raise ValueError(f"unknown arm: {arm}")
    device = torch.device("cuda", 0)
    torch.manual_seed(run.seed)
    torch.cuda.manual_seed_all(run.seed)
    torch.cuda.set_device(device)
    torch.cuda.reset_peak_memory_stats(device)
    triton_arm = arm == "triton"
    case = base_result(backend="torch", arm=arm, family=family, run=run)
    case["runtime"] = module_identity(torch)
    case["execution"] = {
        "device": torch.cuda.get_device_name(device),
        "precision": "bf16 autocast with fp32 master parameters",
        "optimizer": "AdamW",
        "compile": False,
    }
    if triton_arm:
        case["optimization"] = _optimization_evidence(family, run)

    model = FamilyDecoder(family, run, triton_arm=triton_arm).to(device)
    optimizer = _AdamW(
        model.parameters(), learning_rate=run.learning_rate, weight_decay=0.01
    )
    losses: list[float] = []
    step_seconds: list[float] = []
    started = time.perf_counter()
    for step in range(run.steps):
        tokens_BL, labels_BL = _batch(run, step, device)
        torch.cuda.synchronize(device)
        step_started = time.perf_counter()
        optimizer.zero_grad()
        with torch.autocast(device_type="cuda", dtype=torch.bfloat16):
            logits_BLV = model(tokens_BL)
            loss = F.cross_entropy(
                logits_BLV.reshape(-1, run.vocab_size), labels_BL.reshape(-1)
            )
        loss.backward()
        torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
        optimizer.step()
        torch.cuda.synchronize(device)
        step_seconds.append(time.perf_counter() - step_started)
        losses.append(float(loss.detach().cpu()))
    wall_time = time.perf_counter() - started
    case.update(
        {
            "losses": losses,
            "timing": {
                **measured_step_stats(step_seconds, run),
                "wall_time_seconds": wall_time,
                "step_seconds": step_seconds,
            },
            "memory": {
                "peak_allocated_bytes": torch.cuda.max_memory_allocated(device),
                "peak_reserved_bytes": torch.cuda.max_memory_reserved(device),
            },
            "parameter_count": sum(
                parameter.numel() for parameter in model.parameters()
            ),
        }
    )
    return finalize_case(case)
