"""Shared data, result, and comparison helpers for speedrun backends."""

from __future__ import annotations

import hashlib
import json
import math
import os
import subprocess
import time
from pathlib import Path
from typing import Any

from .spec import FamilySpec, RunSpec


SCHEMA_VERSION = 1


def token_values(spec: RunSpec, step: int) -> list[int]:
    """Generate a deterministic, learnable stream without ambient RNG state."""

    count = spec.batch_size * (spec.seq_len + 1)
    offset = (spec.seed + step * spec.tokens_per_step) % spec.active_vocab_size
    return [
        (offset + index + 7 * (index // (spec.seq_len + 1))) % spec.active_vocab_size
        for index in range(count)
    ]


def source_identity() -> dict[str, str]:
    root = Path(__file__).resolve().parents[2]
    files = sorted((root / "workloads" / "nanogpt_speedrun").glob("*.py"))
    digest = hashlib.sha256()
    for path in files:
        digest.update(path.name.encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    commit = os.environ.get("VASO_SOURCE_GIT_COMMIT", "")
    if not commit:
        try:
            commit = subprocess.run(
                ["git", "-C", str(root.parent), "rev-parse", "HEAD"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
        except (OSError, subprocess.CalledProcessError):
            commit = "unknown"
    return {"git_commit": commit, "workload_sha256": digest.hexdigest()}


def module_identity(module: Any) -> dict[str, str]:
    return {
        "version": str(getattr(module, "__version__", "unknown")),
        "module_file": str(Path(getattr(module, "__file__", "")).resolve()),
    }


def base_result(
    *,
    backend: str,
    arm: str,
    family: FamilySpec,
    run: RunSpec,
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "backend": backend,
        "arm": arm,
        "family": family.name,
        "mode": run.mode,
        "cuda_line": os.environ.get("VASO_CUDA_LINE", "unknown"),
        "started_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "family_spec": family.as_dict(),
        "run_spec": run.as_dict(),
        "source": source_identity(),
    }


def measured_step_stats(step_seconds: list[float], run: RunSpec) -> dict[str, float]:
    measured = step_seconds[run.warmup_steps :]
    if not measured:
        raise ValueError("no measured steps remain after warmup")
    ordered = sorted(measured)
    median = ordered[len(ordered) // 2]
    mean = sum(measured) / len(measured)
    return {
        "measured_steps": float(len(measured)),
        "mean_step_seconds": mean,
        "median_step_seconds": median,
        "tokens_per_second": run.tokens_per_step / mean,
    }


def case_checks(case: dict[str, Any]) -> dict[str, bool]:
    losses = case.get("losses")
    timings = case.get("timing")
    runtime = case.get("runtime")
    if not isinstance(losses, list):
        losses = []
    finite_losses = bool(losses) and all(
        isinstance(value, (int, float)) and math.isfinite(float(value))
        for value in losses
    )
    loss_bounded = finite_losses and float(losses[-1]) <= float(losses[0]) * 1.25
    positive_throughput = (
        isinstance(timings, dict)
        and isinstance(timings.get("tokens_per_second"), (int, float))
        and math.isfinite(float(timings["tokens_per_second"]))
        and float(timings["tokens_per_second"]) > 0.0
    )
    package_owned = False
    if isinstance(runtime, dict):
        module_file = str(runtime.get("module_file", ""))
        package_owned = bool(module_file) and (
            "/vaso-bazel-ob/" in module_file or "/run/vaso/run/venv/" in module_file
        )
    checks = {
        "finite_losses": finite_losses,
        "bounded_loss": loss_bounded,
        "positive_throughput": positive_throughput,
        "selected_vaso_package": package_owned,
    }
    if case.get("arm") == "triton":
        optimization = case.get("optimization")
        checks["optimization_path_executed"] = (
            isinstance(optimization, dict) and optimization.get("executed") is True
        )
        max_error = (
            optimization.get("max_abs_error")
            if isinstance(optimization, dict)
            else None
        )
        tolerance = (
            optimization.get("tolerance") if isinstance(optimization, dict) else None
        )
        top_level_numerics = (
            isinstance(max_error, (int, float))
            and isinstance(tolerance, (int, float))
            and math.isfinite(float(max_error))
            and math.isfinite(float(tolerance))
            and 0.0 <= float(max_error) <= float(tolerance)
        )
        component_numerics = True
        if isinstance(optimization, dict):
            for name in ("activation", "attention"):
                component = optimization.get(name)
                if (
                    not isinstance(component, dict)
                    or component.get("executed") is not True
                ):
                    continue
                component_error = component.get("max_abs_error")
                component_tolerance = component.get("tolerance")
                component_numerics = component_numerics and (
                    isinstance(component_error, (int, float))
                    and isinstance(component_tolerance, (int, float))
                    and math.isfinite(float(component_error))
                    and math.isfinite(float(component_tolerance))
                    and 0.0 <= float(component_error) <= float(component_tolerance)
                )
        checks["optimization_numerics"] = top_level_numerics and component_numerics
    return checks


def finalize_case(case: dict[str, Any]) -> dict[str, Any]:
    checks = case_checks(case)
    case["checks"] = checks
    case["verdict"] = "passed" if all(checks.values()) else "failed"
    case["finished_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    return case


def compare_cases(
    baseline: dict[str, Any], optimized: dict[str, Any]
) -> dict[str, Any]:
    baseline_timing = baseline.get("timing", {})
    optimized_timing = optimized.get("timing", {})
    baseline_tps = float(baseline_timing.get("tokens_per_second", 0.0))
    optimized_tps = float(optimized_timing.get("tokens_per_second", 0.0))
    matched = all(
        baseline.get(key) == optimized.get(key)
        for key in (
            "backend", "family", "mode", "run_spec", "family_spec", "source",
            "cuda_line", "execution", "runtime", "parameter_count",
        )
    )
    valid = all(
        math.isfinite(value) and value > 0 for value in (baseline_tps, optimized_tps)
    )
    faster = matched and valid and optimized_tps > baseline_tps
    return {
        "matched_inputs": matched,
        "baseline_tokens_per_second": baseline_tps,
        "optimized_tokens_per_second": optimized_tps,
        "speedup": optimized_tps / baseline_tps if valid else None,
        "faster": faster,
        "claim": (
            "matched steady-state speedup"
            if faster and baseline.get("mode") == "benchmark"
            else "smoke validation; no speedup claim"
            if baseline.get("mode") == "smoke"
            else "optimization candidate; no speedup claim"
        ),
    }


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + f".tmp-{os.getpid()}")
    temporary.write_text(
        json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value
