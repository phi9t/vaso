#!/usr/bin/env python3
"""Run the live torch and Triton workload suite inside the insula."""

from __future__ import annotations

import argparse
import json
import math
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


EXPERIMENT_ROOT = Path(__file__).resolve().parents[1]
HOST_TMP_ROOTS = (
    Path(os.sep) / "tmp",
    Path(os.sep) / "var" / "tmp",
)


@dataclass(frozen=True)
class Workload:
    id: str
    module: str
    min_gpus: int
    description: str


WORKLOADS: dict[str, Workload] = {
    "W1a": Workload("W1a", "workloads.w1_torch_train", 1, "torch eager GPT trainer"),
    "W1b": Workload("W1b", "workloads.w1_torch_train", 1, "torch.compile GPT trainer"),
    "W1c": Workload("W1c", "workloads.w1_torch_train", 8, "DDP GPT trainer"),
    "W1d": Workload("W1d", "workloads.w1_torch_train", 8, "FSDP2 GPT trainer with DCP"),
    "W2a": Workload("W2a", "workloads.w2_triton", 1, "standalone Triton kernels"),
    "W2b": Workload("W2b", "workloads.w2_triton", 1, "Triton torch.library custom op"),
    "W2c": Workload("W2c", "workloads.w2_triton", 1, "torch.compile max-autotune MLP"),
    "W3a": Workload("W3a", "workloads.w3_jax", 1, "JAX jit/grad MLP trainer"),
    "W3b": Workload("W3b", "workloads.w3_jax", 8, "JAX data-parallel trainer"),
    "W3c": Workload("W3c", "workloads.w3_jax", 1, "JAX Pallas GPU kernel"),
    "W4": Workload("W4", "workloads.w4_coresidence", 1, "torch and JAX co-residence"),
    "W5a": Workload("W5a", "workloads.w5_jax_model", 8, "JAX GPT FSDP-style trainer"),
    "W5b": Workload("W5b", "workloads.w5_jax_model", 8, "JAX GPT 2D tensor-parallel trainer"),
    "W5c": Workload("W5c", "workloads.w5_jax_model", 1, "JAX CPU/GPU block numerics"),
    "W5d": Workload("W5d", "workloads.w5_jax_model", 1, "JAX cudnn/xla attention kernels"),
    "W5e": Workload("W5e", "workloads.w5_jax_model", 1, "JAX Triton GEMM and Pallas matmul paths"),
    "W5f": Workload("W5f", "workloads.w5_jax_model", 8, "JAX GPT FSDP determinism"),
}

WORKLOAD_GROUPS: dict[str, tuple[str, ...]] = {
    "jax_model": ("W5a", "W5b", "W5c", "W5d", "W5e", "W5f"),
}


def _write_json(path: Path, doc: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_json_safe(doc), indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")


def _json_safe(value: object) -> object:
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, tuple):
        return [_json_safe(item) for item in value]
    return value


def _is_under(path: Path, root: Path) -> bool:
    try:
        path.resolve(strict=False).relative_to(root)
    except ValueError:
        return False
    return True


def _validate_out_dir(path: Path) -> str | None:
    resolved = path.expanduser().resolve(strict=False)
    for tmp_root in HOST_TMP_ROOTS:
        if resolved == tmp_root or _is_under(resolved, tmp_root):
            return f"workload output directory must not be under host {tmp_root}: {path}"
    return None


def _site_packages(prefix: Path) -> Path:
    matches = sorted((prefix / "lib").glob("python*/site-packages"))
    if len(matches) != 1:
        raise ValueError(f"{prefix} must provide exactly one lib/pythonX.Y/site-packages tree")
    return matches[0]


def _parse_prefixed_paths(items: list[str]) -> tuple[list[tuple[str, Path]], str | None]:
    parsed: list[tuple[str, Path]] = []
    for item in items:
        if "=" not in item:
            return [], f"--runtime-prefix must be NAME=PATH, got {item!r}"
        name, raw_path = item.split("=", 1)
        if not name or not raw_path:
            return [], f"--runtime-prefix must be NAME=PATH, got {item!r}"
        parsed.append((name, Path(raw_path)))
    return parsed, None


def _dedupe(items: Iterable[str]) -> list[str]:
    result: list[str] = []
    for item in items:
        if item and item not in result:
            result.append(item)
    return result


def workload_env(
    out_dir: Path,
    base_environ: dict[str, str],
    *,
    python_prefix: Path | None = None,
    torch_prefix: Path | None = None,
    triton_prefix: Path | None = None,
    jax_prefix: Path | None = None,
    jaxlib_prefix: Path | None = None,
    runtime_prefixes: list[tuple[str, Path]] | None = None,
    line: str | None = None,
    gpus: int | None = None,
) -> dict[str, str]:
    out_dir = out_dir.resolve(strict=False)
    tmp_dir = out_dir / "tmp"
    torchelastic_dir = out_dir / "torchelastic"
    cache_home = out_dir / "cache"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    torchelastic_dir.mkdir(parents=True, exist_ok=True)
    cache_home.mkdir(parents=True, exist_ok=True)

    runtime_prefixes = runtime_prefixes or []
    site_packages: list[str] = [str(EXPERIMENT_ROOT)]
    prefix_libs: list[str] = []
    path_entries: list[str] = []

    if python_prefix is not None:
        path_entries.append(str(python_prefix / "bin"))
    for prefix in (torch_prefix, triton_prefix, jax_prefix, jaxlib_prefix):
        if prefix is not None:
            site_packages.append(str(_site_packages(prefix)))
            prefix_libs.append(str(prefix / "lib"))
            path_entries.append(str(prefix / "bin"))
    if torch_prefix is not None:
        prefix_libs.append(str(_site_packages(torch_prefix) / "torch" / "lib"))
    for _, prefix in runtime_prefixes:
        site_packages.append(str(_site_packages(prefix)))
        prefix_libs.append(str(prefix / "lib"))
        path_entries.append(str(prefix / "bin"))

    cuda_home = base_environ.get("VASO_CUDA_HOME") or str(Path(os.sep) / "usr" / "local" / "cuda")
    cuda_bin = Path(cuda_home) / "bin"
    existing_pythonpath = base_environ.get("PYTHONPATH")
    if existing_pythonpath:
        site_packages.extend(entry for entry in existing_pythonpath.split(os.pathsep) if entry)
    existing_ld = base_environ.get("LD_LIBRARY_PATH")
    ld_entries = [
        str(Path(os.sep) / "run" / "nvidia-driver" / "lib"),
        *prefix_libs,
        str(Path(cuda_home) / "lib64"),
    ]
    if existing_ld:
        ld_entries.extend(entry for entry in existing_ld.split(os.pathsep) if entry)
    existing_path = base_environ.get("PATH")
    if existing_path:
        path_entries.append(existing_path)
    xla_flags = [
        f"--xla_gpu_cuda_data_dir={cuda_home}",
        *(flag for flag in base_environ.get("XLA_FLAGS", "").split() if flag),
    ]

    env = {
        **base_environ,
        "PYTHONPATH": os.pathsep.join(_dedupe(site_packages)),
        "LD_LIBRARY_PATH": os.pathsep.join(_dedupe(ld_entries)),
        "PATH": os.pathsep.join(_dedupe(path_entries)),
        "CC": "/usr/bin/gcc",
        "CXX": "/usr/bin/g++",
        "TMPDIR": str(tmp_dir),
        "TEMP": str(tmp_dir),
        "TMP": str(tmp_dir),
        "XDG_CACHE_HOME": str(cache_home / "xdg"),
        "CUDA_CACHE_PATH": str(cache_home / "cuda"),
        "TORCH_EXTENSIONS_DIR": str(out_dir / "torch_extensions"),
        "TORCH_HOME": str(cache_home / "torch"),
        "TORCHINDUCTOR_CACHE_DIR": str(out_dir / "torchinductor"),
        "TRITON_CACHE_DIR": str(out_dir / "triton"),
        "JAX_COMPILATION_CACHE_DIR": str(out_dir / "jax_cache"),
        "JAX_PERSISTENT_CACHE_MIN_COMPILE_TIME_SECS": base_environ.get(
            "JAX_PERSISTENT_CACHE_MIN_COMPILE_TIME_SECS",
            "0",
        ),
        "TRITON_CUDACRT_PATH": str(Path(cuda_home) / "include"),
        "TRITON_CUDART_PATH": str(Path(cuda_home) / "include"),
        "TRITON_CUOBJDUMP_PATH": str(cuda_bin / "cuobjdump"),
        "TRITON_LIBCUDA_PATH": str(Path(os.sep) / "run" / "nvidia-driver" / "lib"),
        "TRITON_LIBDEVICE_PATH": str(Path(cuda_home) / "nvvm" / "libdevice" / "libdevice.10.bc"),
        "TRITON_NVDISASM_PATH": str(cuda_bin / "nvdisasm"),
        "TRITON_PTXAS_BLACKWELL_PATH": str(cuda_bin / "ptxas"),
        "TRITON_PTXAS_PATH": str(cuda_bin / "ptxas"),
        "MOSAIC_GPU_NVSHMEM_BC_PATH": str(Path(cuda_home) / "lib64" / "libnvshmem_device.bc"),
        "MOSAIC_GPU_NVSHMEM_SO_PATH": str(Path(cuda_home) / "lib64" / "libnvshmem_host.so.3"),
        "TORCHELASTIC_LOG_DIR": str(torchelastic_dir),
        "XLA_FLAGS": " ".join(_dedupe(xla_flags)),
        "XLA_PYTHON_CLIENT_PREALLOCATE": base_environ.get("XLA_PYTHON_CLIENT_PREALLOCATE", "false"),
        "JAX_PLATFORMS": base_environ.get("JAX_PLATFORMS", "cuda"),
        "PYTHONNOUSERSITE": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "OMP_NUM_THREADS": base_environ.get("OMP_NUM_THREADS", "1"),
    }
    if python_prefix is not None:
        env["PYTHON"] = str(python_prefix / "bin" / "python3")
    if torch_prefix is not None:
        env["TORCH_PREFIX"] = str(torch_prefix)
    if triton_prefix is not None:
        env["TRITON_PREFIX"] = str(triton_prefix)
    if jax_prefix is not None:
        env["JAX_PREFIX"] = str(jax_prefix)
    if jaxlib_prefix is not None:
        env["JAXLIB_PREFIX"] = str(jaxlib_prefix)
    if line is not None:
        env["VASO_CUDA_LINE"] = line
    leased_gpu_set = base_environ.get("VASO_GPU_SET")
    visible_devices = base_environ.get("CUDA_VISIBLE_DEVICES")
    if leased_gpu_set:
        env["VASO_GPU_SET"] = leased_gpu_set
        env["CUDA_VISIBLE_DEVICES"] = visible_devices or leased_gpu_set
    elif visible_devices:
        env["CUDA_VISIBLE_DEVICES"] = visible_devices
        env["VASO_GPU_SET"] = visible_devices
    elif gpus is not None and gpus > 0:
        default_devices = ",".join(str(i) for i in range(gpus))
        env["CUDA_VISIBLE_DEVICES"] = default_devices
        env["VASO_GPU_SET"] = default_devices
    return env


def _python_executable(args: argparse.Namespace) -> str:
    if args.python_prefix:
        return str(Path(args.python_prefix) / "bin" / "python3")
    return os.environ.get("PYTHON") or sys.executable


def _select_workloads(raw_only: list[str]) -> tuple[list[Workload], str | None]:
    if not raw_only:
        return list(WORKLOADS.values()), None
    requested: list[str] = []
    for raw in raw_only:
        for item in (part for part in raw.split(",") if part):
            requested.extend(WORKLOAD_GROUPS.get(item, (item,)))
    unknown = sorted(set(requested) - set(WORKLOADS))
    if unknown:
        return [], "unknown workload(s): " + ", ".join(unknown)
    selected: list[Workload] = []
    seen: set[str] = set()
    for item in requested:
        if item not in seen:
            selected.append(WORKLOADS[item])
            seen.add(item)
    return selected, None


def aggregate_results(results: list[dict[str, object]]) -> dict[str, object]:
    failed = [
        str(result.get("id", result.get("workload", "unknown")))
        for result in results
        if result.get("verdict") != "passed"
    ]
    return {
        "verdict": "failed" if failed else "passed",
        "failed_workloads": failed,
        "passed_workloads": [
            str(result.get("id", result.get("workload", "unknown")))
            for result in results
            if result.get("verdict") == "passed"
        ],
        "workload_count": len(results),
        "duration_seconds": sum(float(result.get("duration_seconds", 0.0)) for result in results),
    }


def _load_result(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def _run_workload(
    workload: Workload,
    args: argparse.Namespace,
    env: dict[str, str],
    out_dir: Path,
) -> dict[str, object]:
    workload_dir = out_dir / workload.id
    workload_dir.mkdir(parents=True, exist_ok=True)
    result_path = out_dir / f"{workload.id}.json"
    stdout_path = workload_dir / "stdout.txt"
    stderr_path = workload_dir / "stderr.txt"
    command = [
        _python_executable(args),
        "-m",
        workload.module,
        "--workload",
        workload.id,
        "--out",
        str(result_path),
        "--run-dir",
        str(workload_dir),
        "--line",
        args.line,
        "--gpus",
        str(args.gpus),
    ]
    started = time.monotonic()
    exec_error: str | None = None
    returncode = 0
    with stdout_path.open("w", encoding="utf-8") as stdout, stderr_path.open("w", encoding="utf-8") as stderr:
        try:
            completed = subprocess.run(
                command,
                cwd=str(EXPERIMENT_ROOT),
                env=env,
                stdout=stdout,
                stderr=stderr,
                check=False,
                text=True,
                timeout=args.timeout_seconds,
            )
            returncode = completed.returncode
        except subprocess.TimeoutExpired as exc:
            exec_error = f"TimeoutExpired after {exc.timeout} seconds"
            stderr.write(exec_error + "\n")
            returncode = 124
        except OSError as exc:
            exec_error = f"{type(exc).__name__}: {exc}"
            stderr.write(exec_error + "\n")
            returncode = 127
    duration = time.monotonic() - started

    if result_path.exists():
        result = _load_result(result_path)
    else:
        result = {
            "id": workload.id,
            "verdict": "failed",
            "failed_criteria": ["runner_result_json"],
            "metrics": {},
        }
    result.setdefault("id", workload.id)
    result.setdefault("description", workload.description)
    result["duration_seconds"] = duration
    result["returncode"] = returncode
    result["stdout"] = str(stdout_path)
    result["stderr"] = str(stderr_path)
    result["command"] = command
    if exec_error is not None:
        result["exec_error"] = exec_error
    if returncode != 0:
        result["verdict"] = "failed"
        failed_criteria = list(result.get("failed_criteria", []))
        if "subprocess_returncode" not in failed_criteria:
            failed_criteria.append("subprocess_returncode")
        result["failed_criteria"] = failed_criteria
    _write_json(result_path, result)
    return result


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--only", action="append", default=[], help="Workload id or comma list")
    parser.add_argument("--out", required=True, help="Output directory for workload JSON")
    parser.add_argument("--line", default=os.environ.get("VASO_CUDA_LINE", ""))
    parser.add_argument("--gpus", type=int, default=8)
    parser.add_argument("--python-prefix")
    parser.add_argument("--torch-prefix")
    parser.add_argument("--triton-prefix")
    parser.add_argument("--jax-prefix")
    parser.add_argument("--jaxlib-prefix")
    parser.add_argument("--runtime-prefix", action="append", default=[])
    parser.add_argument("--timeout-seconds", type=float, default=1800.0)
    parser.add_argument("--list", action="store_true")
    return parser


def main(argv: list[str] | None = None, environ: dict[str, str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if args.list:
        for workload in WORKLOADS.values():
            print(f"{workload.id}\t{workload.description}")
        return 0
    if not args.line:
        print("--line is required unless VASO_CUDA_LINE is set", file=sys.stderr)
        return 2
    if args.gpus < 1:
        print("--gpus must be positive", file=sys.stderr)
        return 2

    out_dir = Path(args.out)
    error = _validate_out_dir(out_dir)
    if error:
        print(error, file=sys.stderr)
        return 2
    selected, error = _select_workloads(args.only)
    if error:
        print(error, file=sys.stderr)
        return 2
    too_large = [workload.id for workload in selected if workload.min_gpus > args.gpus]
    if too_large:
        print(
            f"--gpus={args.gpus} is below the requirement for: {', '.join(too_large)}",
            file=sys.stderr,
        )
        return 2
    runtime_prefixes, error = _parse_prefixed_paths(args.runtime_prefix)
    if error:
        print(error, file=sys.stderr)
        return 2

    out_dir.mkdir(parents=True, exist_ok=True)
    base_environ = dict(os.environ)
    if environ:
        base_environ.update(environ)
    env = workload_env(
        out_dir,
        base_environ,
        python_prefix=Path(args.python_prefix) if args.python_prefix else None,
        torch_prefix=Path(args.torch_prefix) if args.torch_prefix else None,
        triton_prefix=Path(args.triton_prefix) if args.triton_prefix else None,
        jax_prefix=Path(args.jax_prefix) if args.jax_prefix else None,
        jaxlib_prefix=Path(args.jaxlib_prefix) if args.jaxlib_prefix else None,
        runtime_prefixes=runtime_prefixes,
        line=args.line,
        gpus=args.gpus,
    )

    started_wall = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    results = [_run_workload(workload, args, env, out_dir) for workload in selected]
    summary = aggregate_results(results)
    summary.update(
        {
            "line": args.line,
            "gpus": args.gpus,
            "started_utc": started_wall,
            "finished_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "results": results,
        }
    )
    _write_json(out_dir / "summary.json", summary)
    print(
        f"workloads: verdict={summary['verdict']} line={args.line} "
        f"count={summary['workload_count']} failed={','.join(summary['failed_workloads']) or '-'} "
        f"out={out_dir}"
    )
    return 1 if summary["verdict"] != "passed" else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
