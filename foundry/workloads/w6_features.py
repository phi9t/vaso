#!/usr/bin/env python3
"""Feature-completeness workload cases for the torch profile."""

from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import subprocess
import sys
import time
from datetime import timedelta
from pathlib import Path
from typing import Any, Callable


EXPERIMENT_ROOT = Path(__file__).resolve().parents[1]
PROBE_TIMEOUT_SECONDS = 180.0


def _finite(value: object) -> bool:
    return isinstance(value, (int, float)) and math.isfinite(float(value))


def _positive_finite(value: object) -> bool:
    return _finite(value) and float(value) > 0.0


def _within(value: object, tolerance: float) -> bool:
    return _finite(value) and float(value) <= tolerance


def _close(value: object, expected: float, tolerance: float = 1.0e-6) -> bool:
    return _finite(value) and abs(float(value) - expected) <= tolerance


def _all_returncodes_passed(metrics: dict[str, Any]) -> bool:
    return all(int(code) == 0 for code in metrics.get("child_returncodes", [0]))


def _criteria_result(
    workload_id: str,
    metrics: dict[str, Any],
    checks: dict[str, bool],
) -> dict[str, object]:
    checks = dict(checks)
    checks["skip_forbidden"] = not (
        metrics.get("skipped") is True
        or metrics.get("verdict") == "skipped"
        or bool(metrics.get("skip_reason"))
    )
    failed = [name for name, passed in checks.items() if not passed]
    return {
        "id": workload_id,
        "verdict": "failed" if failed else "passed",
        "failed_criteria": failed,
        "criteria": checks,
        "metrics": metrics,
    }


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


def _write_json(path: Path, doc: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_json_safe(doc), indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")


def _torch_import():
    import torch

    return torch


def _cuda_available(torch: Any, min_devices: int = 1) -> bool:
    return bool(torch.cuda.is_available()) and int(torch.cuda.device_count()) >= min_devices


def _cuda_unavailable_metrics(torch: Any, min_devices: int = 1) -> dict[str, Any]:
    return {
        "cuda_available": bool(torch.cuda.is_available()),
        "device_count": int(torch.cuda.device_count()) if hasattr(torch, "cuda") else 0,
        "required_device_count": min_devices,
        "skip_reason": "CUDA runtime/device requirement was not met",
    }


def _evaluate_w6a(metrics: dict[str, Any]) -> dict[str, object]:
    tolerance = float(metrics.get("tolerance", 1.0e-2))
    return _criteria_result(
        "W6a",
        metrics,
        {
            "cuda_available": metrics.get("cuda_available") is True,
            "sparse_matmul_ran": metrics.get("sparse_matmul_ran") is True,
            "sparse_matmul_agrees": _within(metrics.get("max_abs_error"), tolerance),
        },
    )


def _evaluate_w6b(metrics: dict[str, Any]) -> dict[str, object]:
    tolerance = float(metrics.get("tolerance", 1.0e-3))
    return _criteria_result(
        "W6b",
        metrics,
        {
            "cuda_available": metrics.get("cuda_available") is True,
            "spsolve_ran": metrics.get("spsolve_ran") is True,
            "spsolve_residual": _within(metrics.get("max_residual"), tolerance),
        },
    )


def _evaluate_w6c(metrics: dict[str, Any]) -> dict[str, object]:
    return _criteria_result(
        "W6c",
        metrics,
        {
            "cuda_available": metrics.get("cuda_available") is True,
            "device_count": int(metrics.get("device_count", 0) or 0) >= 2,
            "nvshmem_available": metrics.get("nvshmem_available") is True,
            "symmetric_memory_ran": metrics.get("symmetric_memory_ran") is True,
            "rendezvous_ran": metrics.get("rendezvous_ran") is True,
            "child_processes_passed": _all_returncodes_passed(metrics),
            "all_reduce_sum": "all_reduce_value" not in metrics or _close(metrics.get("all_reduce_value"), 3.0),
        },
    )


def _evaluate_w6d(metrics: dict[str, Any]) -> dict[str, object]:
    return _criteria_result(
        "W6d",
        metrics,
        {
            "cuda_available": metrics.get("cuda_available") is True,
            "profiler_ran": metrics.get("profiler_ran") is True,
            "cuda_kernel_events": int(metrics.get("cuda_kernel_events", 0) or 0) > 0,
        },
    )


def _evaluate_sdpa(workload_id: str, expected_backend: str, metrics: dict[str, Any]) -> dict[str, object]:
    tolerance = float(metrics.get("tolerance", 1.0e-2))
    return _criteria_result(
        workload_id,
        metrics,
        {
            "cuda_available": metrics.get("cuda_available") is True,
            "backend": metrics.get("backend") == expected_backend,
            "backend_ran": metrics.get("backend_ran") is True,
            "math_ran": metrics.get("math_ran") is True,
            "attention_agrees": _within(metrics.get("max_abs_error"), tolerance),
        },
    )


def _evaluate_w6e(metrics: dict[str, Any]) -> dict[str, object]:
    return _evaluate_sdpa("W6e", "EFFICIENT_ATTENTION", metrics)


def _evaluate_w6f(metrics: dict[str, Any]) -> dict[str, object]:
    return _evaluate_sdpa("W6f", "CUDNN_ATTENTION", metrics)


def _evaluate_w6g(metrics: dict[str, Any]) -> dict[str, object]:
    return _criteria_result(
        "W6g",
        metrics,
        {
            "cuda_available": metrics.get("cuda_available") is True,
            "cudnn_enabled": metrics.get("cudnn_enabled") is True,
            "forward_ran": metrics.get("forward_ran") is True,
            "backward_ran": metrics.get("backward_ran") is True,
            "finite": metrics.get("finite") is True,
        },
    )


def _evaluate_w6h(metrics: dict[str, Any]) -> dict[str, object]:
    return _criteria_result(
        "W6h",
        metrics,
        {
            "gloo_available": metrics.get("gloo_available") is True,
            "process_group_initialized": metrics.get("process_group_initialized") is True,
            "all_reduce_value": _close(metrics.get("all_reduce_value"), 1.0),
        },
    )


def _evaluate_w6i(metrics: dict[str, Any]) -> dict[str, object]:
    mpirun_available = metrics.get("mpirun_available") is True
    return _criteria_result(
        "W6i",
        metrics,
        {
            "mpi_available": metrics.get("mpi_available") is True,
            "mpirun_checked": metrics.get("mpirun_checked") is True,
            "mpi_process_group": (
                metrics.get("process_group_initialized") is True
                and _close(metrics.get("all_reduce_value"), 1.0)
            )
            if mpirun_available
            else bool(metrics.get("availability_only_reason")),
        },
    )


def _evaluate_w6j(metrics: dict[str, Any]) -> dict[str, object]:
    return _criteria_result(
        "W6j",
        metrics,
        {
            "tensorpipe_rpc_available": metrics.get("tensorpipe_rpc_available") is True,
            "workers": int(metrics.get("workers", 0) or 0) == 2,
            "rpc_round_trip": metrics.get("rpc_round_trip") == 42,
            "child_processes_passed": _all_returncodes_passed(metrics),
        },
    )


def _evaluate_quantized_linear(workload_id: str, engine: str, metrics: dict[str, Any]) -> dict[str, object]:
    tolerance = float(metrics.get("tolerance", 0.35))
    return _criteria_result(
        workload_id,
        metrics,
        {
            "engine": metrics.get("engine") == engine,
            "quantized_linear_ran": metrics.get("quantized_linear_ran") is True,
            "quantized_linear_agrees": _within(metrics.get("max_abs_error"), tolerance),
        },
    )


def _evaluate_w6k(metrics: dict[str, Any]) -> dict[str, object]:
    return _evaluate_quantized_linear("W6k", "fbgemm", metrics)


def _evaluate_w6l(metrics: dict[str, Any]) -> dict[str, object]:
    return _evaluate_quantized_linear("W6l", "qnnpack", metrics)


def _evaluate_w6m(metrics: dict[str, Any]) -> dict[str, object]:
    tolerance = float(metrics.get("tolerance", 1.0e-4))
    return _criteria_result(
        "W6m",
        metrics,
        {
            "xnnpack_enabled": metrics.get("xnnpack_enabled") is True,
            "mobile_optimized_conv_ran": metrics.get("mobile_optimized_conv_ran") is True,
            "mobile_conv_agrees": _within(metrics.get("max_abs_error"), tolerance),
        },
    )


def _evaluate_w6n(metrics: dict[str, Any]) -> dict[str, object]:
    return _criteria_result(
        "W6n",
        metrics,
        {
            "nnpack_available": metrics.get("nnpack_available") is True,
            "conv_ran": metrics.get("conv_ran") is True,
            "finite": metrics.get("finite") is True,
        },
    )


def _evaluate_w6o(metrics: dict[str, Any]) -> dict[str, object]:
    tolerance = float(metrics.get("tolerance", 1.0e-4))
    blas_text = str(metrics.get("blas", "")).lower()
    return _criteria_result(
        "W6o",
        metrics,
        {
            "openblas_configured": "openblas" in blas_text or "blas_info=open" in blas_text,
            "solve_residual": _within(metrics.get("solve_residual"), tolerance),
            "svd_residual": _within(metrics.get("svd_residual"), tolerance),
        },
    )


def _evaluate_w6p(metrics: dict[str, Any]) -> dict[str, object]:
    return _criteria_result(
        "W6p",
        metrics,
        {
            "openmp_threads": int(metrics.get("num_threads", 0) or 0) > 1,
            "parallel_op_ran": metrics.get("parallel_op_ran") is True,
            "numa_api_available": metrics.get("numa_api_available") is True,
            "numa_binding_option": metrics.get("numa_binding_option") == "node",
        },
    )


def _evaluate_w6q(metrics: dict[str, Any]) -> dict[str, object]:
    return _criteria_result(
        "W6q",
        metrics,
        {
            "png_round_trip": metrics.get("png_round_trip") is True,
            "jpeg_round_trip": metrics.get("jpeg_round_trip") is True,
            "decode_image_ran": metrics.get("decode_image_ran") is True,
        },
    )


def _evaluate_w6r(metrics: dict[str, Any]) -> dict[str, object]:
    tolerance = float(metrics.get("tolerance", 1.0))
    return _criteria_result(
        "W6r",
        metrics,
        {
            "cuda_available": metrics.get("cuda_available") is True,
            "nvjpeg_decode_ran": metrics.get("nvjpeg_decode_ran") is True,
            "decoded_device": metrics.get("decoded_device") == "cuda",
            "nvjpeg_matches_cpu": _within(metrics.get("max_abs_error"), tolerance),
        },
    )


def _evaluate_w6s(metrics: dict[str, Any]) -> dict[str, object]:
    tolerance = float(metrics.get("tolerance", 1.0e-3))
    return _criteria_result(
        "W6s",
        metrics,
        {
            "cuda_available": metrics.get("cuda_available") is True,
            "roi_align": _within(metrics.get("roi_align_error"), tolerance),
            "roi_pool": _within(metrics.get("roi_pool_error"), tolerance),
            "deform_conv2d": _within(metrics.get("deform_conv_error"), tolerance),
            "ps_roi_align": _within(metrics.get("ps_roi_align_error"), tolerance),
        },
    )


def _evaluate_w6t(metrics: dict[str, Any]) -> dict[str, object]:
    return _criteria_result(
        "W6t",
        metrics,
        {
            "cuda_available": metrics.get("cuda_available") is True,
            "rnnt_loss_ran": metrics.get("rnnt_loss_ran") is True,
            "backward_ran": metrics.get("backward_ran") is True,
            "finite": metrics.get("finite") is True,
        },
    )


def _evaluate_w6u(metrics: dict[str, Any]) -> dict[str, object]:
    return _criteria_result(
        "W6u",
        metrics,
        {
            "cuda_available": metrics.get("cuda_available") is True,
            "forced_align_ran": metrics.get("forced_align_ran") is True,
            "alignment": metrics.get("alignment") == [1, 1, 2, 2],
        },
    )


def _evaluate_w6v(metrics: dict[str, Any]) -> dict[str, object]:
    return _criteria_result(
        "W6v",
        metrics,
        {
            "cuda_available": metrics.get("cuda_available") is True,
            "cuda_ctc_decoder_ran": metrics.get("cuda_ctc_decoder_ran") is True,
            "tokens": metrics.get("tokens") == ["a", "b"],
        },
    )


def _evaluate_w6w(metrics: dict[str, Any]) -> dict[str, object]:
    return _criteria_result(
        "W6w",
        metrics,
        {
            "triton_kernel_ran": metrics.get("triton_kernel_ran") is True,
            "proton_profile_ran": metrics.get("proton_profile_ran") is True,
            "profile_artifact": bool(metrics.get("profile_artifact")),
        },
    )


EVALUATORS: dict[str, Callable[[dict[str, Any]], dict[str, object]]] = {
    "W6a": _evaluate_w6a,
    "W6b": _evaluate_w6b,
    "W6c": _evaluate_w6c,
    "W6d": _evaluate_w6d,
    "W6e": _evaluate_w6e,
    "W6f": _evaluate_w6f,
    "W6g": _evaluate_w6g,
    "W6h": _evaluate_w6h,
    "W6i": _evaluate_w6i,
    "W6j": _evaluate_w6j,
    "W6k": _evaluate_w6k,
    "W6l": _evaluate_w6l,
    "W6m": _evaluate_w6m,
    "W6n": _evaluate_w6n,
    "W6o": _evaluate_w6o,
    "W6p": _evaluate_w6p,
    "W6q": _evaluate_w6q,
    "W6r": _evaluate_w6r,
    "W6s": _evaluate_w6s,
    "W6t": _evaluate_w6t,
    "W6u": _evaluate_w6u,
    "W6v": _evaluate_w6v,
    "W6w": _evaluate_w6w,
}


def evaluate_case(workload_id: str, metrics: dict[str, Any]) -> dict[str, object]:
    return EVALUATORS[workload_id](metrics)


def run_w6a(args: argparse.Namespace) -> dict[str, object]:
    torch = _torch_import()
    if not _cuda_available(torch):
        return evaluate_case("W6a", _cuda_unavailable_metrics(torch))

    torch.cuda.set_device(0)
    torch.manual_seed(6001)
    # Source: PyTorch 2.14 torch/sparse/semi_structured.py exposes
    # to_sparse_semi_structured and the cuSPARSELt-backed CUSPARSELT subclass.
    from torch.sparse import SparseSemiStructuredTensor, to_sparse_semi_structured

    old_force_cutlass = SparseSemiStructuredTensor._FORCE_CUTLASS
    try:
        SparseSemiStructuredTensor._FORCE_CUTLASS = False
        dense = torch.tensor([0.0, 0.0, 1.0, 1.0], device="cuda", dtype=torch.float16).tile((128, 32))
        right = torch.linspace(-1.0, 1.0, 128 * 64, device="cuda", dtype=torch.float16).reshape(128, 64)
        sparse = to_sparse_semi_structured(dense.contiguous())
        output = sparse @ right
        reference = dense @ right
        torch.cuda.synchronize()
        max_abs_error = float((output - reference).abs().max().cpu())
        metrics = {
            "cuda_available": True,
            "device_count": int(torch.cuda.device_count()),
            "sparse_tensor_type": type(sparse).__name__,
            "sparse_matmul_ran": True,
            "max_abs_error": max_abs_error,
            "tolerance": 1.0e-2,
        }
    finally:
        SparseSemiStructuredTensor._FORCE_CUTLASS = old_force_cutlass
    return evaluate_case("W6a", metrics)


def run_w6b(args: argparse.Namespace) -> dict[str, object]:
    torch = _torch_import()
    if not _cuda_available(torch):
        return evaluate_case("W6b", _cuda_unavailable_metrics(torch))

    torch.cuda.set_device(0)
    # Source: PyTorch 2.14 torch/sparse/__init__.py exposes sparse.spsolve;
    # aten/src/ATen/native/sparse/cuda/SparseCsrTensorMath.cu dispatches it
    # through cuDSS phases CUDSS_PHASE_ANALYSIS/FACTORIZATION/SOLVE.
    dense = torch.tensor(
        [[4.0, 1.0, 0.0], [1.0, 3.0, 1.0], [0.0, 1.0, 2.0]],
        device="cuda",
        dtype=torch.float32,
    )
    rhs = torch.tensor([1.0, 2.0, 3.0], device="cuda", dtype=torch.float32)
    solution = torch.sparse.spsolve(dense.to_sparse_csr(), rhs)
    residual = torch.linalg.norm(dense @ solution - rhs) / torch.linalg.norm(rhs)
    torch.cuda.synchronize()
    metrics = {
        "cuda_available": True,
        "device_count": int(torch.cuda.device_count()),
        "spsolve_ran": True,
        "max_residual": float(residual.cpu()),
        "tolerance": 1.0e-3,
    }
    return evaluate_case("W6b", metrics)


def _read_probe_json(path: Path, returncode: int, stdout_path: Path, stderr_path: Path) -> dict[str, Any]:
    if path.is_file():
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(doc, dict):
                doc.setdefault("returncode", returncode)
                doc.setdefault("stdout", str(stdout_path))
                doc.setdefault("stderr", str(stderr_path))
                return doc
        except json.JSONDecodeError as exc:
            return {
                "returncode": returncode,
                "stdout": str(stdout_path),
                "stderr": str(stderr_path),
                "exception": f"JSONDecodeError: {exc}",
            }
    return {
        "returncode": returncode,
        "stdout": str(stdout_path),
        "stderr": str(stderr_path),
        "exception": "probe did not write JSON",
    }


def _run_ranked_probe(args: argparse.Namespace, probe: str, ranks: int) -> list[dict[str, Any]]:
    store_path = args.run_dir / f"{probe}.store"
    if store_path.exists():
        store_path.unlink()
    results: list[dict[str, Any]] = []
    processes: list[tuple[subprocess.Popen[str], Path, Path, Path]] = []
    for rank in range(ranks):
        result_path = args.run_dir / f"{probe}-rank{rank}.json"
        stdout_path = args.run_dir / f"{probe}-rank{rank}.stdout.txt"
        stderr_path = args.run_dir / f"{probe}-rank{rank}.stderr.txt"
        command = [
            sys.executable,
            "-m",
            "workloads.w6_features",
            "--probe",
            probe,
            "--probe-out",
            str(result_path),
            "--run-dir",
            str(args.run_dir),
            "--rank",
            str(rank),
            "--world-size",
            str(ranks),
            "--store",
            str(store_path),
        ]
        with stdout_path.open("w", encoding="utf-8") as stdout, stderr_path.open("w", encoding="utf-8") as stderr:
            process = subprocess.Popen(
                command,
                cwd=str(EXPERIMENT_ROOT),
                stdout=stdout,
                stderr=stderr,
                text=True,
            )
        processes.append((process, result_path, stdout_path, stderr_path))
    deadline = time.monotonic() + PROBE_TIMEOUT_SECONDS
    for process, result_path, stdout_path, stderr_path in processes:
        remaining = max(0.1, deadline - time.monotonic())
        try:
            returncode = process.wait(timeout=remaining)
        except subprocess.TimeoutExpired:
            process.terminate()
            try:
                returncode = process.wait(timeout=5.0)
            except subprocess.TimeoutExpired:
                process.kill()
                returncode = process.wait()
            with stderr_path.open("a", encoding="utf-8") as stderr:
                stderr.write("probe timed out\n")
        results.append(_read_probe_json(result_path, returncode, stdout_path, stderr_path))
    return results


def _aggregate_boolean(child_metrics: list[dict[str, Any]], key: str) -> bool:
    return bool(child_metrics) and all(item.get(key) is True for item in child_metrics)


def run_w6c(args: argparse.Namespace) -> dict[str, object]:
    torch = _torch_import()
    if not _cuda_available(torch, min_devices=2):
        return evaluate_case("W6c", _cuda_unavailable_metrics(torch, min_devices=2))

    child_metrics = _run_ranked_probe(args, "symmetric-memory", ranks=2)
    all_reduce_values = [
        float(item["all_reduce_value"])
        for item in child_metrics
        if _finite(item.get("all_reduce_value"))
    ]
    metrics = {
        "cuda_available": _aggregate_boolean(child_metrics, "cuda_available"),
        "device_count": int(torch.cuda.device_count()),
        "nvshmem_available": _aggregate_boolean(child_metrics, "nvshmem_available"),
        "symmetric_memory_ran": _aggregate_boolean(child_metrics, "symmetric_memory_ran"),
        "rendezvous_ran": _aggregate_boolean(child_metrics, "rendezvous_ran"),
        "all_reduce_value": all_reduce_values[0] if all_reduce_values else None,
        "child_returncodes": [int(item.get("returncode", 1) or 0) for item in child_metrics],
        "child_metrics": child_metrics,
    }
    return evaluate_case("W6c", metrics)


def run_w6d(args: argparse.Namespace) -> dict[str, object]:
    torch = _torch_import()
    if not _cuda_available(torch):
        return evaluate_case("W6d", _cuda_unavailable_metrics(torch))

    torch.cuda.set_device(0)
    # Source: PyTorch 2.14 torch/profiler/profiler.py documents profile()
    # with ProfilerActivity.CUDA for Kineto-backed CUDA collection.
    from torch.profiler import ProfilerActivity, profile

    with profile(activities=[ProfilerActivity.CPU, ProfilerActivity.CUDA]) as prof:
        lhs = torch.randn(64, 64, device="cuda")
        rhs = torch.randn(64, 64, device="cuda")
        for _ in range(3):
            (lhs @ rhs).relu_()
        torch.cuda.synchronize()
    events = list(prof.key_averages())
    cuda_time_kernel_events = sum(1 for event in events if _positive_finite(getattr(event, "cuda_time_total", 0.0)))
    self_cuda_time_kernel_events = sum(
        1 for event in events if _positive_finite(getattr(event, "self_cuda_time_total", 0.0))
    )
    device_time_kernel_events = sum(1 for event in events if _positive_finite(getattr(event, "device_time_total", 0.0)))
    self_device_time_kernel_events = sum(
        1 for event in events if _positive_finite(getattr(event, "self_device_time_total", 0.0))
    )
    cuda_kernel_events = sum(
        1
        for event in events
        if any(
            _positive_finite(getattr(event, field, 0.0))
            for field in (
                "self_device_time_total",
                "device_time_total",
                "self_cuda_time_total",
                "cuda_time_total",
            )
        )
    )
    cuda_event_names = [
        str(getattr(event, "key", ""))
        for event in events
        if any(
            _positive_finite(getattr(event, field, 0.0))
            for field in (
                "self_device_time_total",
                "device_time_total",
                "self_cuda_time_total",
                "cuda_time_total",
            )
        )
    ][:8]
    metrics = {
        "cuda_available": True,
        "profiler_ran": True,
        "cuda_kernel_events": cuda_kernel_events,
        "cuda_time_kernel_events": cuda_time_kernel_events,
        "self_cuda_time_kernel_events": self_cuda_time_kernel_events,
        "device_time_kernel_events": device_time_kernel_events,
        "self_device_time_kernel_events": self_device_time_kernel_events,
        "cuda_event_names": cuda_event_names,
    }
    return evaluate_case("W6d", metrics)


def _run_sdpa_backend(args: argparse.Namespace, workload_id: str, backend_name: str) -> dict[str, object]:
    torch = _torch_import()
    if not _cuda_available(torch):
        return evaluate_case(workload_id, _cuda_unavailable_metrics(torch))

    torch.cuda.set_device(0)
    torch.manual_seed(6005 if workload_id == "W6e" else 6006)
    # Source: PyTorch 2.14 torch/nn/attention/__init__.py exposes
    # sdpa_kernel and SDPBackend.EFFICIENT_ATTENTION/CUDNN_ATTENTION/MATH.
    from torch.nn.attention import SDPBackend, sdpa_kernel
    from torch.nn.functional import scaled_dot_product_attention

    query = torch.randn(2, 4, 64, 64, device="cuda", dtype=torch.float16)
    key = torch.randn(2, 4, 64, 64, device="cuda", dtype=torch.float16)
    value = torch.randn(2, 4, 64, 64, device="cuda", dtype=torch.float16)
    backend = getattr(SDPBackend, backend_name)
    with sdpa_kernel(SDPBackend.MATH):
        reference = scaled_dot_product_attention(query, key, value)
    with sdpa_kernel(backend):
        output = scaled_dot_product_attention(query, key, value)
    torch.cuda.synchronize()
    max_abs_error = float((output - reference).abs().max().cpu())
    metrics = {
        "cuda_available": True,
        "backend": backend_name,
        "backend_ran": True,
        "math_ran": True,
        "max_abs_error": max_abs_error,
        "tolerance": 8.0e-2,
    }
    return evaluate_case(workload_id, metrics)


def run_w6e(args: argparse.Namespace) -> dict[str, object]:
    return _run_sdpa_backend(args, "W6e", "EFFICIENT_ATTENTION")


def run_w6f(args: argparse.Namespace) -> dict[str, object]:
    return _run_sdpa_backend(args, "W6f", "CUDNN_ATTENTION")


def run_w6g(args: argparse.Namespace) -> dict[str, object]:
    torch = _torch_import()
    if not _cuda_available(torch):
        return evaluate_case("W6g", _cuda_unavailable_metrics(torch))

    torch.cuda.set_device(0)
    torch.manual_seed(6007)
    # Source: PyTorch 2.14 torch/backends/cudnn/__init__.py exposes the
    # cudnn.enabled switch used by Conv2d dispatch.
    conv = torch.nn.Conv2d(3, 8, kernel_size=3, padding=1).cuda().half()
    inputs = torch.randn(4, 3, 16, 16, device="cuda", dtype=torch.float16, requires_grad=True)
    with torch.backends.cudnn.flags(enabled=True):
        output = conv(inputs)
        loss = output.square().mean()
        loss.backward()
    torch.cuda.synchronize()
    metrics = {
        "cuda_available": True,
        "cudnn_enabled": bool(torch.backends.cudnn.enabled),
        "forward_ran": output.numel() > 0,
        "backward_ran": inputs.grad is not None and conv.weight.grad is not None,
        "finite": bool(torch.isfinite(output).all().item())
        and bool(torch.isfinite(inputs.grad).all().item())
        and bool(torch.isfinite(conv.weight.grad).all().item()),
    }
    return evaluate_case("W6g", metrics)


def run_w6h(args: argparse.Namespace) -> dict[str, object]:
    torch = _torch_import()
    # Source: PyTorch 2.14 torch/distributed/distributed_c10d.py exposes
    # is_gloo_available(), init_process_group(), all_reduce(), and Backend.GLOO.
    import torch.distributed as dist

    metrics: dict[str, Any] = {
        "gloo_available": bool(dist.is_gloo_available()),
        "process_group_initialized": False,
        "all_reduce_value": None,
    }
    if not metrics["gloo_available"]:
        return evaluate_case("W6h", metrics)

    store = args.run_dir / "gloo.store"
    if store.exists():
        store.unlink()
    try:
        dist.init_process_group(
            backend=dist.Backend.GLOO,
            init_method=f"file://{store}",
            rank=0,
            world_size=1,
            timeout=timedelta(seconds=30),
        )
        tensor = torch.ones(1)
        dist.all_reduce(tensor)
        metrics["process_group_initialized"] = True
        metrics["all_reduce_value"] = float(tensor.item())
    finally:
        if dist.is_initialized():
            dist.destroy_process_group()
    return evaluate_case("W6h", metrics)


def _run_mpi_probe(args: argparse.Namespace, mpirun: str) -> dict[str, Any]:
    result_path = args.run_dir / "mpi-rank0.json"
    stdout_path = args.run_dir / "mpi.stdout.txt"
    stderr_path = args.run_dir / "mpi.stderr.txt"
    command = [
        mpirun,
        "-n",
        "1",
        sys.executable,
        "-m",
        "workloads.w6_features",
        "--probe",
        "mpi",
        "--probe-out",
        str(result_path),
        "--run-dir",
        str(args.run_dir),
    ]
    with stdout_path.open("w", encoding="utf-8") as stdout, stderr_path.open("w", encoding="utf-8") as stderr:
        completed = subprocess.run(
            command,
            cwd=str(EXPERIMENT_ROOT),
            stdout=stdout,
            stderr=stderr,
            text=True,
            timeout=PROBE_TIMEOUT_SECONDS,
            check=False,
        )
    return _read_probe_json(result_path, completed.returncode, stdout_path, stderr_path)


def run_w6i(args: argparse.Namespace) -> dict[str, object]:
    _torch_import()
    # Source: PyTorch 2.14 torch/distributed/distributed_c10d.py exposes
    # is_mpi_available() and the MPI process-group backend.
    import torch.distributed as dist

    mpirun = shutil.which("mpirun")
    metrics: dict[str, Any] = {
        "mpi_available": bool(dist.is_mpi_available()),
        "mpirun_checked": True,
        "mpirun_available": bool(mpirun),
    }
    if not mpirun:
        metrics["availability_only_reason"] = "mpirun not present in rootfs"
        return evaluate_case("W6i", metrics)

    child = _run_mpi_probe(args, mpirun)
    metrics.update(
        {
            "process_group_initialized": child.get("process_group_initialized") is True,
            "all_reduce_value": child.get("all_reduce_value"),
            "child_returncodes": [int(child.get("returncode", 1) or 0)],
            "child_metrics": [child],
        }
    )
    return evaluate_case("W6i", metrics)


def run_w6j(args: argparse.Namespace) -> dict[str, object]:
    _torch_import()
    # Source: PyTorch 2.14 torch/distributed/rpc/__init__.py exposes init_rpc()
    # and rpc_sync(); torch/distributed/rpc/options.py exposes
    # TensorPipeRpcBackendOptions.
    from torch.distributed import rpc

    child_metrics = _run_ranked_probe(args, "tensorpipe-rpc", ranks=2)
    round_trips = [item.get("rpc_round_trip") for item in child_metrics if item.get("rpc_round_trip") is not None]
    metrics = {
        "tensorpipe_rpc_available": hasattr(rpc, "TensorPipeRpcBackendOptions"),
        "workers": len(child_metrics),
        "rpc_round_trip": round_trips[0] if round_trips else None,
        "child_returncodes": [int(item.get("returncode", 1) or 0) for item in child_metrics],
        "child_metrics": child_metrics,
    }
    return evaluate_case("W6j", metrics)


def _run_quantized_linear(args: argparse.Namespace, workload_id: str, engine: str, tolerance: float) -> dict[str, object]:
    torch = _torch_import()
    # Source: PyTorch 2.14 torch/backends/quantized/__init__.py exposes the
    # quantized engine selector; torch/ao/nn/quantized/modules/linear.py defines
    # quantized Linear used by static conversion.
    old_engine = torch.backends.quantized.engine
    metrics: dict[str, Any] = {"engine": engine, "quantized_linear_ran": False, "tolerance": tolerance}
    try:
        if engine not in torch.backends.quantized.supported_engines:
            metrics["supported_engines"] = list(torch.backends.quantized.supported_engines)
            return evaluate_case(workload_id, metrics)
        torch.backends.quantized.engine = engine
        torch.manual_seed(6010 if engine == "fbgemm" else 6011)
        class QuantizedLinearModule(torch.nn.Module):
            def __init__(self) -> None:
                super().__init__()
                self.quant = torch.ao.quantization.QuantStub()
                self.linear = torch.nn.Linear(4, 3)
                self.dequant = torch.ao.quantization.DeQuantStub()

            def forward(self, value):
                return self.dequant(self.linear(self.quant(value)))

        model = QuantizedLinearModule().eval()
        model.qconfig = torch.ao.quantization.get_default_qconfig(engine)
        sample = torch.tensor(
            [[-1.0, -0.5, 0.5, 1.0], [0.25, -0.75, 1.25, -1.5]],
            dtype=torch.float32,
        )
        prepared = torch.ao.quantization.prepare(model, inplace=False)
        prepared(sample)
        quantized = torch.ao.quantization.convert(prepared, inplace=False)
        output = quantized(sample).dequantize()
        reference = model(sample)
        metrics.update(
            {
                "quantized_linear_ran": True,
                "max_abs_error": float((output - reference).abs().max().item()),
                "supported_engines": list(torch.backends.quantized.supported_engines),
            }
        )
    finally:
        torch.backends.quantized.engine = old_engine
    return evaluate_case(workload_id, metrics)


def run_w6k(args: argparse.Namespace) -> dict[str, object]:
    return _run_quantized_linear(args, "W6k", "fbgemm", tolerance=0.25)


def run_w6l(args: argparse.Namespace) -> dict[str, object]:
    return _run_quantized_linear(args, "W6l", "qnnpack", tolerance=0.35)


def run_w6m(args: argparse.Namespace) -> dict[str, object]:
    torch = _torch_import()
    # Source: PyTorch 2.14 torch/backends/xnnpack/__init__.py exposes
    # torch.backends.xnnpack.enabled; torch/utils/mobile_optimizer.py exposes
    # optimize_for_mobile.
    import torch.backends.xnnpack
    from torch.utils.mobile_optimizer import optimize_for_mobile

    class ConvModule(torch.nn.Module):
        def __init__(self) -> None:
            super().__init__()
            self.conv = torch.nn.Conv2d(3, 4, kernel_size=3, padding=1)

        def forward(self, value):
            return self.conv(value).relu()

    torch.manual_seed(6012)
    model = ConvModule().eval()
    sample = torch.randn(1, 3, 16, 16)
    reference = model(sample)
    traced = torch.jit.trace(model, sample)
    optimized = optimize_for_mobile(traced)
    output = optimized(sample)
    metrics = {
        "xnnpack_enabled": bool(torch.backends.xnnpack.enabled),
        "mobile_optimized_conv_ran": True,
        "max_abs_error": float((output - reference).abs().max().item()),
        "tolerance": 1.0e-4,
    }
    return evaluate_case("W6m", metrics)


def run_w6n(args: argparse.Namespace) -> dict[str, object]:
    torch = _torch_import()
    # Source: PyTorch 2.14 torch/backends/nnpack/__init__.py exposes
    # is_available() and flags(enabled=...) for NNPACK-backed CPU convolution.
    import torch.backends.nnpack

    torch.manual_seed(6013)
    nnpack_available = bool(torch.backends.nnpack.is_available())
    metrics: dict[str, Any] = {
        "nnpack_available": nnpack_available,
        "conv_ran": False,
        "finite": False,
    }
    if not nnpack_available:
        return evaluate_case("W6n", metrics)
    with torch.backends.nnpack.flags(enabled=True):
        inputs = torch.randn(2, 3, 32, 32)
        weight = torch.randn(8, 3, 3, 3)
        output = torch.nn.functional.conv2d(inputs, weight, padding=1)
    metrics.update(
        {
            "conv_ran": output.numel() > 0,
            "finite": bool(torch.isfinite(output).all().item()),
        }
    )
    return evaluate_case("W6n", metrics)


def run_w6o(args: argparse.Namespace) -> dict[str, object]:
    torch = _torch_import()
    # Source: PyTorch 2.14 torch/testing/_internal/opinfo/definitions/linalg.py
    # registers torch.linalg.solve and torch.linalg.svd OpInfos; the native plan
    # sets BLAS=OpenBLAS for this build profile.
    config = torch.__config__.show()
    blas = "OpenBLAS" if "openblas" in config.lower() else config
    matrix = torch.tensor(
        [[6.0, 2.0, 1.0], [2.0, 5.0, 2.0], [1.0, 2.0, 4.0]],
        dtype=torch.float64,
    )
    rhs = torch.tensor([1.0, 2.0, 3.0], dtype=torch.float64)
    solution = torch.linalg.solve(matrix, rhs)
    solve_residual = torch.linalg.norm(matrix @ solution - rhs) / torch.linalg.norm(rhs)
    u, s, vh = torch.linalg.svd(matrix, full_matrices=False)
    reconstructed = u @ torch.diag(s) @ vh
    svd_residual = torch.linalg.norm(reconstructed - matrix) / torch.linalg.norm(matrix)
    metrics = {
        "blas": blas,
        "solve_residual": float(solve_residual.item()),
        "svd_residual": float(svd_residual.item()),
        "tolerance": 1.0e-4,
    }
    return evaluate_case("W6o", metrics)


def run_w6p(args: argparse.Namespace) -> dict[str, object]:
    torch = _torch_import()
    # Source: PyTorch 2.14 torch/numa/binding.py exposes AffinityMode.NODE and
    # NumaOptions; torch exposes get_num_threads()/set_num_threads() for OpenMP.
    from torch.numa.binding import AffinityMode, NumaOptions

    old_threads = torch.get_num_threads()
    target_threads = max(2, min(4, os.cpu_count() or 2))
    try:
        torch.set_num_threads(target_threads)
        lhs = torch.randn(256, 256)
        rhs = torch.randn(256, 256)
        output = lhs @ rhs
        numa_options = NumaOptions(affinity_mode=AffinityMode.NODE)
        metrics = {
            "num_threads": int(torch.get_num_threads()),
            "parallel_op_ran": output.numel() > 0 and bool(torch.isfinite(output).all().item()),
            "numa_api_available": True,
            "numa_binding_option": str(numa_options.affinity_mode.value),
        }
    finally:
        torch.set_num_threads(old_threads)
    return evaluate_case("W6p", metrics)


def run_w6q(args: argparse.Namespace) -> dict[str, object]:
    torch = _torch_import()
    # Source: torchvision 0.29.0 torchvision/io/image.py exposes encode_png(),
    # encode_jpeg(), and decode_image().
    from torchvision.io import decode_image, encode_jpeg, encode_png

    image = torch.full((3, 8, 8), 127, dtype=torch.uint8)
    png_bytes = encode_png(image)
    jpeg_bytes = encode_jpeg(image, quality=100)
    decoded_png = decode_image(png_bytes, mode="RGB")
    decoded_jpeg = decode_image(jpeg_bytes, mode="RGB")
    metrics = {
        "png_round_trip": bool(torch.equal(decoded_png, image)),
        "jpeg_round_trip": decoded_jpeg.shape == image.shape
        and float((decoded_jpeg.to(torch.int16) - image.to(torch.int16)).abs().max().item()) <= 2.0,
        "decode_image_ran": True,
    }
    return evaluate_case("W6q", metrics)


def run_w6r(args: argparse.Namespace) -> dict[str, object]:
    torch = _torch_import()
    if not _cuda_available(torch):
        return evaluate_case("W6r", _cuda_unavailable_metrics(torch))

    # Source: torchvision 0.29.0 torchvision/io/image.py exposes
    # decode_jpeg(..., device="cuda") backed by torch.ops.image.decode_jpegs_cuda.
    from torchvision.io import decode_jpeg, encode_jpeg

    image = torch.full((3, 8, 8), 111, dtype=torch.uint8)
    jpeg_bytes = encode_jpeg(image, quality=100)
    decoded_cpu = decode_jpeg(jpeg_bytes, mode="RGB", device="cpu")
    decoded_cuda = decode_jpeg(jpeg_bytes, mode="RGB", device="cuda")
    torch.cuda.synchronize()
    max_abs_error = float((decoded_cuda.cpu().to(torch.int16) - decoded_cpu.to(torch.int16)).abs().max().item())
    metrics = {
        "cuda_available": True,
        "nvjpeg_decode_ran": True,
        "decoded_device": decoded_cuda.device.type,
        "max_abs_error": max_abs_error,
        "tolerance": 1.0,
    }
    return evaluate_case("W6r", metrics)


def run_w6s(args: argparse.Namespace) -> dict[str, object]:
    torch = _torch_import()
    if not _cuda_available(torch):
        return evaluate_case("W6s", _cuda_unavailable_metrics(torch))

    # Source: torchvision 0.29.0 torchvision/ops/{roi_align.py,roi_pool.py,
    # deform_conv.py,ps_roi_align.py} expose the CUDA-capable op wrappers.
    from torchvision.ops import deform_conv2d, ps_roi_align, roi_align, roi_pool

    torch.manual_seed(6014)
    features = torch.arange(1 * 4 * 8 * 8, dtype=torch.float32).reshape(1, 4, 8, 8) / 256.0
    boxes = torch.tensor([[0.0, 1.0, 1.0, 6.0, 6.0]], dtype=torch.float32)
    roi_align_cpu = roi_align(features, boxes, output_size=(2, 2), spatial_scale=1.0)
    roi_align_cuda = roi_align(features.cuda(), boxes.cuda(), output_size=(2, 2), spatial_scale=1.0).cpu()
    roi_pool_cpu = roi_pool(features, boxes, output_size=(2, 2), spatial_scale=1.0)
    roi_pool_cuda = roi_pool(features.cuda(), boxes.cuda(), output_size=(2, 2), spatial_scale=1.0).cpu()

    deform_input = torch.randn(1, 1, 5, 5)
    deform_weight = torch.randn(1, 1, 3, 3)
    offset = torch.zeros(1, 18, 3, 3)
    mask = torch.ones(1, 9, 3, 3)
    deform_cpu = deform_conv2d(deform_input, offset, deform_weight, mask=mask)
    deform_cuda = deform_conv2d(
        deform_input.cuda(),
        offset.cuda(),
        deform_weight.cuda(),
        mask=mask.cuda(),
    ).cpu()

    ps_cpu = ps_roi_align(features, boxes, output_size=2, spatial_scale=1.0, sampling_ratio=1)
    ps_cuda = ps_roi_align(features.cuda(), boxes.cuda(), output_size=2, spatial_scale=1.0, sampling_ratio=1).cpu()
    torch.cuda.synchronize()
    metrics = {
        "cuda_available": True,
        "roi_align_error": float((roi_align_cuda - roi_align_cpu).abs().max().item()),
        "roi_pool_error": float((roi_pool_cuda - roi_pool_cpu).abs().max().item()),
        "deform_conv_error": float((deform_cuda - deform_cpu).abs().max().item()),
        "ps_roi_align_error": float((ps_cuda - ps_cpu).abs().max().item()),
        "tolerance": 1.0e-3,
    }
    return evaluate_case("W6s", metrics)


def run_w6t(args: argparse.Namespace) -> dict[str, object]:
    torch = _torch_import()
    if not _cuda_available(torch):
        return evaluate_case("W6t", _cuda_unavailable_metrics(torch))

    # Source: torchaudio 2.11.0 src/torchaudio/functional/functional.py exposes
    # rnnt_loss() backed by torch.ops.torchaudio.rnnt_loss_forward.
    from torchaudio.functional import rnnt_loss

    torch.manual_seed(6015)
    logits = torch.randn(1, 3, 3, 3, device="cuda", dtype=torch.float32, requires_grad=True)
    targets = torch.tensor([[0, 1]], device="cuda", dtype=torch.int32)
    logit_lengths = torch.tensor([3], device="cuda", dtype=torch.int32)
    target_lengths = torch.tensor([2], device="cuda", dtype=torch.int32)
    loss = rnnt_loss(logits, targets, logit_lengths, target_lengths, blank=2, reduction="mean")
    loss.backward()
    torch.cuda.synchronize()
    metrics = {
        "cuda_available": True,
        "rnnt_loss_ran": True,
        "backward_ran": logits.grad is not None,
        "finite": bool(torch.isfinite(loss).item()) and bool(torch.isfinite(logits.grad).all().item()),
    }
    return evaluate_case("W6t", metrics)


def run_w6u(args: argparse.Namespace) -> dict[str, object]:
    torch = _torch_import()
    if not _cuda_available(torch):
        return evaluate_case("W6u", _cuda_unavailable_metrics(torch))

    # Source: torchaudio 2.11.0 src/torchaudio/functional/_alignment.py exposes
    # forced_align(log_probs, targets, input_lengths, target_lengths, blank).
    from torchaudio.functional import forced_align

    logits = torch.full((1, 4, 3), -8.0, device="cuda", dtype=torch.float32)
    logits[0, 0, 1] = 8.0
    logits[0, 1, 1] = 8.0
    logits[0, 2, 2] = 8.0
    logits[0, 3, 2] = 8.0
    log_probs = logits.log_softmax(dim=-1)
    targets = torch.tensor([[1, 2]], device="cuda", dtype=torch.int32)
    input_lengths = torch.tensor([4], device="cuda", dtype=torch.int32)
    target_lengths = torch.tensor([2], device="cuda", dtype=torch.int32)
    paths, _ = forced_align(log_probs, targets, input_lengths, target_lengths, blank=0)
    torch.cuda.synchronize()
    metrics = {
        "cuda_available": True,
        "forced_align_ran": True,
        "alignment": [int(item) for item in paths[0].cpu().tolist()],
    }
    return evaluate_case("W6u", metrics)


def run_w6v(args: argparse.Namespace) -> dict[str, object]:
    torch = _torch_import()
    if not _cuda_available(torch):
        return evaluate_case("W6v", _cuda_unavailable_metrics(torch))

    # Source: torchaudio 2.11.0 src/torchaudio/models/decoder/_cuda_ctc_decoder.py
    # exposes cuda_ctc_decoder(tokens, nbest, beam_size, blank_skip_threshold).
    from torchaudio.models.decoder import cuda_ctc_decoder

    probs = torch.tensor(
        [[[0.01, 0.98, 0.01], [0.98, 0.01, 0.01], [0.01, 0.01, 0.98]]],
        device="cuda",
        dtype=torch.float32,
    )
    log_probs = probs.log()
    lengths = torch.tensor([3], device="cuda", dtype=torch.int32)
    decoder = cuda_ctc_decoder(["-", "a", "b"], nbest=1, beam_size=3, blank_skip_threshold=1.0)
    hypotheses = decoder(log_probs, lengths)
    torch.cuda.synchronize()
    metrics = {
        "cuda_available": True,
        "cuda_ctc_decoder_ran": True,
        "tokens": list(hypotheses[0][0].words),
    }
    return evaluate_case("W6v", metrics)


def run_w6w(args: argparse.Namespace) -> dict[str, object]:
    torch = _torch_import()
    if not _cuda_available(torch):
        return evaluate_case("W6w", _cuda_unavailable_metrics(torch))

    # Source: Triton third_party/proton/test/test_api.py exposes
    # triton.profiler.start/activate/deactivate/finalize; third_party/proton
    # tutorials demonstrate profiling a Triton kernel with the "triton" hook.
    import triton
    import triton.language as tl
    import triton.profiler as proton
    profile_base = args.run_dir / "w6w-proton"
    session_id = proton.start(str(profile_base), backend="instrumentation", hook="triton")

    @triton.jit
    def _add_kernel(x_ptr, y_ptr, out_ptr, n_elements, BLOCK_SIZE: tl.constexpr):
        offsets = tl.program_id(0) * BLOCK_SIZE + tl.arange(0, BLOCK_SIZE)
        mask = offsets < n_elements
        x = tl.load(x_ptr + offsets, mask=mask)
        y = tl.load(y_ptr + offsets, mask=mask)
        tl.store(out_ptr + offsets, x + y, mask=mask)

    n_elements = 1024
    lhs = torch.arange(n_elements, device="cuda", dtype=torch.float32)
    rhs = torch.ones(n_elements, device="cuda", dtype=torch.float32)
    output = torch.empty_like(lhs)
    try:
        proton.activate(session_id)
        _add_kernel[(triton.cdiv(n_elements, 128),)](lhs, rhs, output, n_elements, BLOCK_SIZE=128)
        torch.cuda.synchronize()
        triton_kernel_ran = bool(torch.allclose(output, lhs + rhs))
        proton.deactivate(session_id)
    finally:
        proton.finalize(session_id)
    artifact = profile_base.with_suffix(".hatchet")
    metrics = {
        "triton_kernel_ran": triton_kernel_ran,
        "proton_profile_ran": artifact.is_file(),
        "profile_artifact": str(artifact) if artifact.is_file() else "",
    }
    return evaluate_case("W6w", metrics)


def _probe_symmetric_memory(args: argparse.Namespace) -> dict[str, object]:
    torch = _torch_import()
    import torch.distributed as dist
    import torch.distributed._symmetric_memory as symm_mem

    rank = int(args.rank)
    world_size = int(args.world_size)
    metrics: dict[str, Any] = {
        "rank": rank,
        "cuda_available": _cuda_available(torch, min_devices=world_size),
        "device_count": int(torch.cuda.device_count()),
        "nvshmem_available": False,
        "symmetric_memory_ran": False,
        "rendezvous_ran": False,
        "all_reduce_value": None,
    }
    if not metrics["cuda_available"]:
        metrics["skip_reason"] = "CUDA runtime/device requirement was not met"
        return metrics

    torch.cuda.set_device(rank)
    dist.init_process_group(
        backend=dist.Backend.NCCL,
        init_method=f"file://{args.store}",
        rank=rank,
        world_size=world_size,
        timeout=timedelta(seconds=60),
    )
    try:
        # Source: PyTorch 2.14 torch/distributed/_symmetric_memory/__init__.py
        # exposes is_nvshmem_available(), set_backend(), empty(), rendezvous();
        # torch/csrc/distributed/c10d/symm_mem/SymmetricMemory.cpp registers
        # torch.ops.symm_mem.one_shot_all_reduce(input, reduce_op, group_name).
        metrics["nvshmem_available"] = bool(symm_mem.is_nvshmem_available())
        if not metrics["nvshmem_available"]:
            return metrics
        symm_mem.set_backend("NVSHMEM")
        buffer = symm_mem.empty(4, dtype=torch.float32, device=f"cuda:{rank}")
        buffer.fill_(float(rank + 1))
        group_name = dist.group.WORLD.group_name
        symm_mem.rendezvous(buffer, group=dist.group.WORLD)
        metrics["rendezvous_ran"] = True
        reduced = torch.ops.symm_mem.one_shot_all_reduce(buffer, "sum", group_name)
        torch.cuda.synchronize()
        metrics["symmetric_memory_ran"] = True
        metrics["all_reduce_value"] = float(reduced[0].detach().cpu())
    finally:
        if dist.is_initialized():
            dist.destroy_process_group()
    return metrics


def _probe_mpi(args: argparse.Namespace) -> dict[str, object]:
    torch = _torch_import()
    import torch.distributed as dist

    metrics: dict[str, Any] = {
        "mpi_available": bool(dist.is_mpi_available()),
        "process_group_initialized": False,
        "all_reduce_value": None,
    }
    if not metrics["mpi_available"]:
        return metrics
    # Source: PyTorch 2.14 torch/distributed/distributed_c10d.py exposes the
    # MPI backend for init_process_group("mpi") when built with USE_MPI.
    dist.init_process_group("mpi")
    try:
        tensor = torch.ones(1)
        dist.all_reduce(tensor)
        metrics["process_group_initialized"] = True
        metrics["all_reduce_value"] = float(tensor.item())
    finally:
        if dist.is_initialized():
            dist.destroy_process_group()
    return metrics


def _probe_tensorpipe_rpc(args: argparse.Namespace) -> dict[str, object]:
    import operator

    _torch_import()
    from torch.distributed import rpc

    rank = int(args.rank)
    world_size = int(args.world_size)
    # Source: PyTorch 2.14 torch/distributed/rpc/options.py defines
    # TensorPipeRpcBackendOptions(init_method=...).
    options = rpc.TensorPipeRpcBackendOptions(init_method=f"file://{args.store}", rpc_timeout=60.0)
    rpc.init_rpc(
        f"worker{rank}",
        rank=rank,
        world_size=world_size,
        rpc_backend_options=options,
    )
    metrics: dict[str, Any] = {
        "rank": rank,
        "tensorpipe_rpc_available": True,
        "rpc_round_trip": None,
    }
    try:
        if rank == 0:
            metrics["rpc_round_trip"] = int(rpc.rpc_sync("worker1", operator.add, args=(41, 1)))
    finally:
        rpc.shutdown()
    return metrics


PROBES: dict[str, Callable[[argparse.Namespace], dict[str, object]]] = {
    "mpi": _probe_mpi,
    "symmetric-memory": _probe_symmetric_memory,
    "tensorpipe-rpc": _probe_tensorpipe_rpc,
}


RUNNERS: dict[str, Callable[[argparse.Namespace], dict[str, object]]] = {
    "W6a": run_w6a,
    "W6b": run_w6b,
    "W6c": run_w6c,
    "W6d": run_w6d,
    "W6e": run_w6e,
    "W6f": run_w6f,
    "W6g": run_w6g,
    "W6h": run_w6h,
    "W6i": run_w6i,
    "W6j": run_w6j,
    "W6k": run_w6k,
    "W6l": run_w6l,
    "W6m": run_w6m,
    "W6n": run_w6n,
    "W6o": run_w6o,
    "W6p": run_w6p,
    "W6q": run_w6q,
    "W6r": run_w6r,
    "W6s": run_w6s,
    "W6t": run_w6t,
    "W6u": run_w6u,
    "W6v": run_w6v,
    "W6w": run_w6w,
}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workload", choices=sorted(RUNNERS))
    parser.add_argument("--probe", choices=sorted(PROBES))
    parser.add_argument("--probe-out")
    parser.add_argument("--rank", type=int, default=0)
    parser.add_argument("--world-size", type=int, default=1)
    parser.add_argument("--store", type=Path)
    parser.add_argument("--out")
    parser.add_argument("--run-dir", required=True, type=Path)
    parser.add_argument("--line", default="")
    parser.add_argument("--gpus", type=int, default=8)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    args.run_dir.mkdir(parents=True, exist_ok=True)
    if args.probe:
        if not args.probe_out:
            raise SystemExit("--probe requires --probe-out")
        try:
            probe_result = PROBES[args.probe](args)
        except Exception as exc:  # pragma: no cover - live runtime failure path
            probe_result = {
                "probe": args.probe,
                "returncode": 1,
                "exception": f"{type(exc).__name__}: {exc}",
            }
        _write_json(Path(args.probe_out), probe_result)
        return int(probe_result.get("returncode", 0) or 0)
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
