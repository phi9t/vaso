#!/usr/bin/env python3
"""Run matched Torch/JAX decoder families and their optimized kernel arms."""

from __future__ import annotations

import argparse
import importlib
import os
import time
import traceback
from pathlib import Path
from typing import Any

from workloads.nanogpt_speedrun.common import compare_cases, write_json
from workloads.nanogpt_speedrun.spec import FAMILIES, family_spec, run_spec


WORKLOAD_CONFIG = {
    "W7a": ("torch", "baseline"),
    "W7b": ("torch", "triton"),
    "W7c": ("jax", "baseline"),
    "W7d": ("jax", "triton"),
}


def _configured_families(raw: str) -> tuple[str, ...]:
    if not raw:
        return FAMILIES
    selected = tuple(item.strip() for item in raw.split(",") if item.strip())
    unknown = sorted(set(selected) - set(FAMILIES))
    if unknown:
        raise ValueError(
            f"unknown family or families: {', '.join(unknown)}; "
            f"expected a subset of {', '.join(FAMILIES)}"
        )
    if not selected:
        raise ValueError("at least one family must be selected")
    return selected


def _failed_case(
    *,
    backend: str,
    arm: str,
    family: str,
    mode: str,
    exc: Exception,
) -> dict[str, Any]:
    return {
        "backend": backend,
        "arm": arm,
        "family": family,
        "mode": mode,
        "verdict": "failed",
        "checks": {"completed": False},
        "exception": f"{type(exc).__name__}: {exc}",
        "traceback": traceback.format_exc(),
    }


def _checkpoint(
    path: Path,
    *,
    workload_id: str,
    backend: str,
    arm: str,
    mode: str,
    families: tuple[str, ...],
    cases: list[dict[str, Any]],
    comparisons: list[dict[str, Any]],
    current: str,
) -> None:
    write_json(
        path,
        {
            "schema_version": 1,
            "id": workload_id,
            "state": "running",
            "backend": backend,
            "arm": arm,
            "mode": mode,
            "families": list(families),
            "current": current,
            "completed_cases": len(cases),
            "cases": cases,
            "comparisons": comparisons,
            "updated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        },
    )


def run_matrix(
    workload_id: str,
    *,
    mode: str,
    families: tuple[str, ...],
    progress_path: Path,
) -> dict[str, Any]:
    backend, arm = WORKLOAD_CONFIG[workload_id]
    module = importlib.import_module(f"workloads.nanogpt_speedrun.{backend}_backend")
    run = run_spec(mode)
    cases: list[dict[str, Any]] = []
    comparisons: list[dict[str, Any]] = []
    started = time.monotonic()
    for family_name in families:
        family = family_spec(
            family_name,
            num_heads=run.num_heads,
            num_layers=run.num_layers,
        )
        baseline: dict[str, Any] | None = None
        if arm == "triton":
            _checkpoint(
                progress_path,
                workload_id=workload_id,
                backend=backend,
                arm=arm,
                mode=mode,
                families=families,
                cases=cases,
                comparisons=comparisons,
                current=f"{family_name}:baseline",
            )
            try:
                baseline = module.run_case(family, run, arm="baseline")
            except Exception as exc:  # pragma: no cover - live runtime failure path
                baseline = _failed_case(
                    backend=backend,
                    arm="baseline",
                    family=family_name,
                    mode=mode,
                    exc=exc,
                )
            cases.append(baseline)
            if baseline.get("verdict") != "passed":
                comparisons.append(
                    {
                        "family": family_name,
                        "matched_inputs": False,
                        "claim": "optimized case skipped because matched baseline failed",
                    }
                )
                continue
        _checkpoint(
            progress_path,
            workload_id=workload_id,
            backend=backend,
            arm=arm,
            mode=mode,
            families=families,
            cases=cases,
            comparisons=comparisons,
            current=f"{family_name}:{arm}",
        )
        try:
            candidate = module.run_case(family, run, arm=arm)
        except Exception as exc:  # pragma: no cover - live runtime failure path
            candidate = _failed_case(
                backend=backend,
                arm=arm,
                family=family_name,
                mode=mode,
                exc=exc,
            )
        cases.append(candidate)
        if baseline is not None:
            comparison = compare_cases(baseline, candidate)
            comparison["family"] = family_name
            comparisons.append(comparison)

    all_cases_passed = bool(cases) and all(
        case.get("verdict") == "passed" for case in cases
    )
    matched_comparisons = arm == "baseline" or (
        len(comparisons) == len(families)
        and all(comparison.get("matched_inputs") is True for comparison in comparisons)
    )
    checks = {
        "all_families_completed": len(
            {str(case.get("family")) for case in cases if case.get("arm") == arm}
        )
        == len(families),
        "all_cases_passed": all_cases_passed,
        "optimized_comparisons_matched": matched_comparisons,
    }
    faster = [
        str(comparison["family"])
        for comparison in comparisons
        if comparison.get("faster") is True
    ]
    return {
        "schema_version": 1,
        "id": workload_id,
        "state": "completed",
        "backend": backend,
        "arm": arm,
        "mode": mode,
        "families": list(families),
        "verdict": "passed" if all(checks.values()) else "failed",
        "criteria": checks,
        "failed_criteria": [name for name, passed in checks.items() if not passed],
        "metrics": {
            "family_count": len(families),
            "case_count": len(cases),
            "optimized_faster_families": faster,
            "duration_seconds": time.monotonic() - started,
        },
        "cases": cases,
        "comparisons": comparisons,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workload", required=True, choices=sorted(WORKLOAD_CONFIG))
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--run-dir", required=True, type=Path)
    parser.add_argument("--line", required=True)
    parser.add_argument("--gpus", type=int, default=1)
    parser.add_argument(
        "--mode",
        choices=("smoke", "benchmark"),
        default=os.environ.get("VASO_NANOGPT_MODE", "smoke"),
    )
    parser.add_argument(
        "--families",
        default=os.environ.get("VASO_NANOGPT_FAMILIES", ""),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    args.run_dir.mkdir(parents=True, exist_ok=True)
    progress_path = args.run_dir / "progress.json"
    started = time.monotonic()
    try:
        result = run_matrix(
            args.workload,
            mode=args.mode,
            families=_configured_families(args.families),
            progress_path=progress_path,
        )
    except Exception as exc:  # pragma: no cover - live runtime failure path
        result = {
            "schema_version": 1,
            "id": args.workload,
            "state": "failed",
            "verdict": "failed",
            "criteria": {"exception": False},
            "failed_criteria": ["exception"],
            "metrics": {},
            "exception": f"{type(exc).__name__}: {exc}",
            "traceback": traceback.format_exc(),
        }
    result["line"] = args.line
    result["duration_seconds"] = time.monotonic() - started
    write_json(args.out, result)
    write_json(progress_path, result)
    return 0 if result.get("verdict") == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
