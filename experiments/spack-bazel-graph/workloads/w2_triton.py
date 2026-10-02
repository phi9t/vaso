#!/usr/bin/env python3
"""Triton live workloads W2a through W2c."""

from __future__ import annotations

import argparse
import json
import math
import time
from pathlib import Path
from typing import Any


Tensor: Any = None

VECTOR_ADD_ATOL = 1.0e-6
SOFTMAX_ATOL = 1.0e-4
MATMUL_ATOL = 5.0e-2
GRAD_ATOL = 5.0e-3
LOSS_DROP_RATIO = 0.50
COMPILE_ATOL = 1.0e-2
COMPILE_RTOL = 1.0e-2


def _finite(value: object) -> bool:
    return isinstance(value, (int, float)) and math.isfinite(float(value))


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


def evaluate_w2a(metrics: dict[str, Any]) -> dict[str, object]:
    checks = {
        "vector_add_error": _finite(metrics.get("vector_add_max_abs_error"))
        and float(metrics["vector_add_max_abs_error"]) <= VECTOR_ADD_ATOL,
        "softmax_error": _finite(metrics.get("softmax_max_abs_error"))
        and float(metrics["softmax_max_abs_error"]) <= SOFTMAX_ATOL,
        "matmul_error": _finite(metrics.get("matmul_max_abs_error"))
        and float(metrics["matmul_max_abs_error"]) <= MATMUL_ATOL,
        "matmul_tflops_recorded": _finite(metrics.get("matmul_tflops"))
        and float(metrics["matmul_tflops"]) > 0.0,
    }
    return _criteria_result("W2a", metrics, checks)


def evaluate_w2b(metrics: dict[str, Any]) -> dict[str, object]:
    checks = {
        "gradcheck": metrics.get("gradcheck_passed") is True,
        "eager_loss_drop": _loss_drop(metrics.get("eager_initial_loss"), metrics.get("eager_final_loss")),
        "compiled_loss_drop": _loss_drop(
            metrics.get("compiled_initial_loss"), metrics.get("compiled_final_loss")
        ),
    }
    if "max_grad_abs_error" in metrics:
        checks["gradient_match"] = _finite(metrics.get("max_grad_abs_error")) and float(
            metrics["max_grad_abs_error"]
        ) <= GRAD_ATOL
    return _criteria_result("W2b", metrics, checks)


def evaluate_w2c(metrics: dict[str, Any]) -> dict[str, object]:
    checks = {
        "compile_succeeded": metrics.get("compile_succeeded") is True,
        "max_abs_error": _finite(metrics.get("max_abs_error"))
        and float(metrics["max_abs_error"]) <= COMPILE_ATOL,
        "max_rel_error": _finite(metrics.get("max_rel_error"))
        and float(metrics["max_rel_error"]) <= COMPILE_RTOL,
    }
    return _criteria_result("W2c", metrics, checks)


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


def _torch_triton_imports():
    import torch
    import triton
    import triton.language as tl

    return torch, triton, tl


def _require_cuda(torch: Any, gpus: int) -> None:
    if not torch.cuda.is_available():
        raise RuntimeError("CUDA is not available")
    available = torch.cuda.device_count()
    if available < gpus:
        raise RuntimeError(f"requested {gpus} GPU(s), but torch sees {available}")


def _runtime_metadata(torch: Any, triton: Any) -> dict[str, object]:
    return {
        "torch_version": getattr(torch, "__version__", ""),
        "torch_cuda_version": getattr(torch.version, "cuda", ""),
        "triton_version": getattr(triton, "__version__", ""),
        "device_name": torch.cuda.get_device_name(0),
        "device_capability": list(torch.cuda.get_device_capability(0)),
    }


def _w1_module():
    try:
        from workloads import w1_torch_train
    except ImportError:
        import w1_torch_train

        return w1_torch_train
    return w1_torch_train


def run_w2a(args: argparse.Namespace) -> dict[str, object]:
    torch, triton, tl = _torch_triton_imports()
    _require_cuda(torch, 1)
    torch.cuda.set_device(0)

    @triton.jit
    def vector_add_kernel(x_ptr, y_ptr, out_ptr, n_elements: tl.constexpr, block_size: tl.constexpr):
        pid = tl.program_id(axis=0)
        offsets = pid * block_size + tl.arange(0, block_size)
        mask = offsets < n_elements
        x = tl.load(x_ptr + offsets, mask=mask)
        y = tl.load(y_ptr + offsets, mask=mask)
        tl.store(out_ptr + offsets, x + y, mask=mask)

    @triton.jit
    def softmax_kernel(x_ptr, out_ptr, n_cols: tl.constexpr, block_size: tl.constexpr):
        row = tl.program_id(0)
        offsets = tl.arange(0, block_size)
        mask = offsets < n_cols
        x = tl.load(x_ptr + row * n_cols + offsets, mask=mask, other=-float("inf"))
        z = x - tl.max(x, axis=0)
        numerator = tl.exp(z)
        denominator = tl.sum(numerator, axis=0)
        tl.store(out_ptr + row * n_cols + offsets, numerator / denominator, mask=mask)

    @triton.jit
    def matmul_kernel(
        a_ptr,
        b_ptr,
        c_ptr,
        m: tl.constexpr,
        n: tl.constexpr,
        k: tl.constexpr,
        block_m: tl.constexpr,
        block_n: tl.constexpr,
        block_k: tl.constexpr,
    ):
        pid_m = tl.program_id(axis=0)
        pid_n = tl.program_id(axis=1)
        offs_m = pid_m * block_m + tl.arange(0, block_m)
        offs_n = pid_n * block_n + tl.arange(0, block_n)
        offs_k = tl.arange(0, block_k)
        acc = tl.zeros((block_m, block_n), dtype=tl.float32)
        for k_start in range(0, k, block_k):
            a = tl.load(
                a_ptr + offs_m[:, None] * k + (k_start + offs_k[None, :]),
                mask=(offs_m[:, None] < m) & (k_start + offs_k[None, :] < k),
                other=0.0,
            )
            b = tl.load(
                b_ptr + (k_start + offs_k[:, None]) * n + offs_n[None, :],
                mask=(k_start + offs_k[:, None] < k) & (offs_n[None, :] < n),
                other=0.0,
            )
            acc = tl.dot(a, b, acc)
        tl.store(
            c_ptr + offs_m[:, None] * n + offs_n[None, :],
            acc,
            mask=(offs_m[:, None] < m) & (offs_n[None, :] < n),
        )

    n = 1 << 20
    x = torch.randn(n, device="cuda", dtype=torch.float32)
    y = torch.randn(n, device="cuda", dtype=torch.float32)
    z = torch.empty_like(x)
    block = 256
    vector_add_kernel[(triton.cdiv(n, block),)](x, y, z, n, block)
    vector_error = float((z - (x + y)).abs().max().detach().cpu())

    rows, cols = 4096, 1024
    softmax_in = torch.randn((rows, cols), device="cuda", dtype=torch.float32)
    softmax_out = torch.empty_like(softmax_in)
    softmax_kernel[(rows,)](softmax_in, softmax_out, cols, triton.next_power_of_2(cols))
    softmax_ref = torch.softmax(softmax_in, dim=-1)
    softmax_error = float((softmax_out - softmax_ref).abs().max().detach().cpu())

    m = n_size = k = 2048
    a = torch.randn((m, k), device="cuda", dtype=torch.float16) * 0.1
    b = torch.randn((k, n_size), device="cuda", dtype=torch.float16) * 0.1
    c = torch.empty((m, n_size), device="cuda", dtype=torch.float32)
    grid = (triton.cdiv(m, 32), triton.cdiv(n_size, 64))
    matmul_kernel[grid](a, b, c, m, n_size, k, 32, 64, 64, num_warps=4, num_stages=4)
    reference = torch.matmul(a, b).float()
    matmul_error = float((c - reference).abs().max().detach().cpu())

    for _ in range(3):
        matmul_kernel[grid](a, b, c, m, n_size, k, 32, 64, 64, num_warps=4, num_stages=4)
    torch.cuda.synchronize()
    start = torch.cuda.Event(enable_timing=True)
    end = torch.cuda.Event(enable_timing=True)
    repeats = 20
    start.record()
    for _ in range(repeats):
        matmul_kernel[grid](a, b, c, m, n_size, k, 32, 64, 64, num_warps=4, num_stages=4)
    end.record()
    torch.cuda.synchronize()
    milliseconds = start.elapsed_time(end) / repeats
    tflops = 2.0 * m * n_size * k / (milliseconds / 1000.0) / 1.0e12
    metrics = {
        **_runtime_metadata(torch, triton),
        "vector_add_max_abs_error": vector_error,
        "softmax_max_abs_error": softmax_error,
        "matmul_max_abs_error": matmul_error,
        "matmul_tflops": tflops,
        "matmul_shape": [m, n_size, k],
    }
    return evaluate_w2a(metrics)


_TRITON_SILU_OP: Any | None = None


def _install_tensor_annotation(torch: Any) -> None:
    globals()["Tensor"] = torch.Tensor


def register_triton_silu():
    global _TRITON_SILU_OP
    if _TRITON_SILU_OP is not None:
        return _TRITON_SILU_OP
    torch, triton, tl = _torch_triton_imports()
    _install_tensor_annotation(torch)

    @triton.jit
    def silu_kernel(x_ptr, out_ptr, n_elements: tl.constexpr, block_size: tl.constexpr):
        pid = tl.program_id(axis=0)
        offsets = pid * block_size + tl.arange(0, block_size)
        mask = offsets < n_elements
        x = tl.load(x_ptr + offsets, mask=mask)
        x_fp32 = x.to(tl.float32)
        sigmoid = 1.0 / (1.0 + tl.exp(-x_fp32))
        tl.store(out_ptr + offsets, x_fp32 * sigmoid, mask=mask)

    @triton.jit
    def silu_backward_kernel(x_ptr, grad_ptr, out_ptr, n_elements: tl.constexpr, block_size: tl.constexpr):
        pid = tl.program_id(axis=0)
        offsets = pid * block_size + tl.arange(0, block_size)
        mask = offsets < n_elements
        x = tl.load(x_ptr + offsets, mask=mask)
        grad = tl.load(grad_ptr + offsets, mask=mask)
        x_fp32 = x.to(tl.float32)
        sigmoid = 1.0 / (1.0 + tl.exp(-x_fp32))
        derivative = sigmoid * (1.0 + x_fp32 * (1.0 - sigmoid))
        tl.store(out_ptr + offsets, grad * derivative, mask=mask)

    def _launch_silu(x):
        out = torch.empty_like(x)
        n_elements = x.numel()
        block_size = 256
        silu_kernel[(triton.cdiv(n_elements, block_size),)](x, out, n_elements, block_size)
        return out

    def _launch_silu_backward(x, grad):
        out = torch.empty_like(x)
        n_elements = x.numel()
        block_size = 256
        silu_backward_kernel[(triton.cdiv(n_elements, block_size),)](
            x, grad.contiguous(), out, n_elements, block_size
        )
        return out

    @torch.library.custom_op("vaso_workloads::triton_silu_backward", mutates_args=())
    def triton_silu_backward(x: Tensor, grad: Tensor) -> Tensor:
        return _launch_silu_backward(x.contiguous(), grad)

    @torch.library.register_fake("vaso_workloads::triton_silu_backward")
    def _(x, grad):
        return torch.empty_like(x)

    @torch.library.custom_op("vaso_workloads::triton_silu", mutates_args=())
    def triton_silu(x: Tensor) -> Tensor:
        return _launch_silu(x.contiguous())

    @torch.library.register_fake("vaso_workloads::triton_silu")
    def _(x):
        return torch.empty_like(x)

    def setup_context(ctx, inputs, output) -> None:
        (x,) = inputs
        ctx.save_for_backward(x)

    def backward(ctx, grad):
        (x,) = ctx.saved_tensors
        return triton_silu_backward(x, grad)

    torch.library.register_autograd(
        "vaso_workloads::triton_silu",
        backward,
        setup_context=setup_context,
    )
    _TRITON_SILU_OP = triton_silu
    return triton_silu


def _train_with_custom_activation(torch: Any, *, compile_model: bool) -> dict[str, float]:
    w1 = _w1_module()
    op = register_triton_silu()
    _, nn, F = w1._torch_imports()
    model, optimizer = w1._new_model_and_optimizer(torch, nn, F, fused_activation=op, seed=2468)
    if compile_model:
        model = torch.compile(model)
    losses, tokens_per_second = w1._train_series(torch, model, optimizer, steps=120, batch_size=16)
    return {
        "initial_loss": losses[0],
        "final_loss": losses[-1],
        "tokens_per_second": tokens_per_second,
    }


def run_w2b(args: argparse.Namespace) -> dict[str, object]:
    torch, triton, _ = _torch_triton_imports()
    _require_cuda(torch, 1)
    torch.cuda.set_device(0)
    op = register_triton_silu()

    gradcheck_passed = False
    gradcheck_error = ""
    try:
        small = torch.randn(8, device="cuda", dtype=torch.double, requires_grad=True)
        gradcheck_passed = bool(
            torch.autograd.gradcheck(lambda x: op(x), (small,), eps=1.0e-4, atol=1.0e-3, rtol=1.0e-2)
        )
    except Exception as exc:  # pragma: no cover - live runtime failure path
        gradcheck_error = f"{type(exc).__name__}: {exc}"

    lhs = torch.randn(1024, device="cuda", dtype=torch.float32, requires_grad=True)
    rhs = lhs.detach().clone().requires_grad_(True)
    op(lhs).sum().backward()
    torch.nn.functional.silu(rhs).sum().backward()
    max_grad_error = float((lhs.grad - rhs.grad).abs().max().detach().cpu())

    eager = _train_with_custom_activation(torch, compile_model=False)
    compiled = _train_with_custom_activation(torch, compile_model=True)
    metrics = {
        **_runtime_metadata(torch, triton),
        "gradcheck_passed": gradcheck_passed,
        "gradcheck_error": gradcheck_error,
        "max_grad_abs_error": max_grad_error,
        "eager_initial_loss": eager["initial_loss"],
        "eager_final_loss": eager["final_loss"],
        "eager_tokens_per_second": eager["tokens_per_second"],
        "compiled_initial_loss": compiled["initial_loss"],
        "compiled_final_loss": compiled["final_loss"],
        "compiled_tokens_per_second": compiled["tokens_per_second"],
    }
    return evaluate_w2b(metrics)


def run_w2c(args: argparse.Namespace) -> dict[str, object]:
    torch, triton, _ = _torch_triton_imports()
    _require_cuda(torch, 1)
    torch.cuda.set_device(0)
    torch.manual_seed(1357)

    class MatmulMLP(torch.nn.Module):
        def __init__(self) -> None:
            super().__init__()
            self.w1 = torch.nn.Parameter(torch.randn(2048, 4096, device="cuda", dtype=torch.float16) * 0.02)
            self.w2 = torch.nn.Parameter(torch.randn(4096, 2048, device="cuda", dtype=torch.float16) * 0.02)

        def forward(self, x):
            h = torch.nn.functional.gelu(x @ self.w1)
            return h @ self.w2

    model = MatmulMLP()
    x = torch.randn((256, 2048), device="cuda", dtype=torch.float16)
    with torch.no_grad():
        eager = model(x).float()
    compile_succeeded = True
    compile_error = ""
    max_abs_error: float | None = None
    max_rel_error: float | None = None
    try:
        compiled_model = torch.compile(model, mode="max-autotune")
        with torch.no_grad():
            compiled = compiled_model(x).float()
        torch.cuda.synchronize()
        diff = (compiled - eager).abs()
        max_abs_error = float(diff.max().detach().cpu())
        max_rel_error = float((diff / eager.abs().clamp_min(1.0e-3)).max().detach().cpu())
    except Exception as exc:  # pragma: no cover - live runtime failure path
        compile_succeeded = False
        compile_error = f"{type(exc).__name__}: {exc}"
    metrics = {
        **_runtime_metadata(torch, triton),
        "compile_succeeded": compile_succeeded,
        "compile_error": compile_error,
        "max_abs_error": max_abs_error,
        "max_rel_error": max_rel_error,
        "shape": [256, 2048, 4096],
    }
    return evaluate_w2c(metrics)


RUNNERS = {
    "W2a": run_w2a,
    "W2b": run_w2b,
    "W2c": run_w2c,
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
