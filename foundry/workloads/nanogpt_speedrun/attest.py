#!/usr/bin/env python3
"""Validate a decoder attempt and retain a portable, hash-bound evidence bundle.

This is a local workload attestation, not a signature or native-build proof.
Only standard-library modules are needed; replay never imports Torch or JAX.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import shutil
import sys
from pathlib import Path
from typing import Any

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from workloads.nanogpt_speedrun.common import case_checks, compare_cases, measured_step_stats
from workloads.nanogpt_speedrun.spec import FAMILIES, family_spec, run_spec

WORKLOADS = {"W7a": ("torch", "baseline"), "W7b": ("torch", "triton"),
             "W7c": ("jax", "baseline"), "W7d": ("jax", "triton")}
VERIFIER_FILES = ("attest.py", "common.py", "spec.py", "__init__.py")
LIMITS = [
    "Local unsigned evidence; hashes detect changes against a trusted manifest, not forgery.",
    "Workload acceptance does not prove cold native execution, hermeticity, or artifact reproducibility.",
    "Single-run throughput is observational; no statistical significance or idle-GPU guarantee.",
    "Torch eager and JAX whole-step JIT use different execution strategies.",
    "Mini families retain selected architecture seams, not released-model checkpoint compatibility.",
]


def read_json(path: Path) -> dict[str, Any]:
    def reject(value: str) -> None:
        raise ValueError(f"non-finite JSON constant: {value}")

    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON key: {key}")
            result[key] = value
        return result

    value = json.loads(path.read_text(), parse_constant=reject, object_pairs_hook=unique)
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def expected_workloads(configuration: dict[str, Any]) -> list[str]:
    backend, arm = configuration["backend"], configuration["arm"]
    if backend not in ("all", "torch", "jax") or arm not in ("all", "baseline", "optimized"):
        raise ValueError("invalid backend/arm selection")
    return [key for key, (profile, candidate) in WORKLOADS.items()
            if backend in ("all", profile)
            and (arm == "all" or (candidate == "baseline") == (arm == "baseline"))]


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _positive(value: Any) -> bool:
    return type(value) in (int, float) and math.isfinite(value) and value > 0


def _normalized(value: Any) -> Any:
    return json.loads(json.dumps(value, allow_nan=False))


def _case(case: dict[str, Any], *, family: str, backend: str, arm: str,
          configuration: dict[str, Any], checkout: dict[str, Any]) -> None:
    label = f"{backend}/{family}/{arm}"
    run = run_spec(configuration["mode"])
    _require(case["backend"] == backend and case["family"] == family and case["arm"] == arm,
             f"{label}: case identity mismatch")
    _require(case["mode"] == run.mode and case["cuda_line"] == configuration["line"],
             f"{label}: mode/line mismatch")
    _require(case["run_spec"] == run.as_dict(), f"{label}: unexpected run specification")
    expected_family = family_spec(family, num_heads=run.num_heads, num_layers=run.num_layers)
    _require(case["family_spec"] == _normalized(expected_family.as_dict()),
             f"{label}: unexpected family specification")
    source = case["source"]
    _require(source["git_commit"] == checkout["git_commit"], f"{label}: source commit mismatch")
    _require(len(source["workload_sha256"]) == 64
             and all(c in "0123456789abcdef" for c in source["workload_sha256"]),
             f"{label}: missing workload digest")
    _require(_positive(case["parameter_count"]), f"{label}: invalid parameter count")
    _require(isinstance(case["losses"], list) and len(case["losses"]) == run.steps,
             f"{label}: incomplete losses")
    _require(all(type(x) in (int, float) and math.isfinite(x) and x >= 0 for x in case["losses"]),
             f"{label}: invalid losses")
    runtime = case["runtime"]
    _require(runtime.get("version") not in (None, "", "unknown")
             and runtime["module_file"].startswith(("/vaso-bazel-ob/", "/run/vaso/run/venv/")),
             f"{label}: missing Vaso runtime identity")
    checks = case_checks(case)
    _require(case["verdict"] == "passed" and all(checks.values()), f"{label}: failed case checks")
    _require(all(case["checks"].get(key) is True for key in checks), f"{label}: recorded checks disagree")
    timing = case["timing"]
    samples = timing["step_seconds"]
    _require(isinstance(samples, list) and len(samples) == run.steps and all(_positive(x) for x in samples),
             f"{label}: incomplete or invalid timing samples")
    for name, expected in measured_step_stats(samples, run).items():
        _require(_positive(timing[name]) and math.isclose(timing[name], expected, rel_tol=1e-9),
                 f"{label}: timing {name} disagrees with samples")
    _require(_positive(timing["wall_time_seconds"]), f"{label}: missing wall time")
    if arm != "triton":
        return
    optimization = case["optimization"]
    for component in ("activation", "attention"):
        evidence = optimization[component]
        needed = component == "activation" or "softmax" in expected_family.attention_pattern
        _require(evidence.get("executed") is needed, f"{label}: {component} execution mismatch")
        if not needed:
            _require(bool(evidence.get("reason")), f"{label}: missing attention skip reason")
            continue
        ceiling = 0.25 if component == "attention" else (
            2e-5 if backend == "torch" else (1e-4 if family == "mini_kimi_k3" else 5e-5))
        tolerance = evidence["tolerance"]
        _require(_positive(tolerance) and tolerance <= ceiling, f"{label}: inflated {component} tolerance")
        for key in ("forward_max_abs_error", "backward_max_abs_error", "max_abs_error"):
            error = evidence[key]
            _require(type(error) in (int, float) and math.isfinite(error) and 0 <= error <= tolerance,
                     f"{label}: {component}/{key} fails parity")
    if backend == "jax":
        activation = optimization["activation"]
        _require(len(activation["compiled_text_sha256"]) == 64
                 and bool(set(activation["lowering_markers"]) & {"pallas", "custom_call", "triton"}),
                 f"{label}: missing Pallas lowering evidence")


def evaluate(attempt: Path, *, require_full: bool = True) -> dict[str, Any]:
    errors: list[str] = []
    rows: list[dict[str, Any]] = []
    configuration: dict[str, Any] = {}
    checkout: dict[str, Any] = {}
    complete = False
    try:
        state = read_json(attempt / "state.json")
        summary = read_json(attempt / "summary.json")
        configuration = state["configuration"]
        checkout = state["checkout"]
        _require(len(checkout["git_commit"]) == 40 and len(checkout["worktree_sha256"]) == 64
                 and all(c in "0123456789abcdef" for value in checkout.values() for c in value),
                 "invalid checkout identity")
        families = configuration["families"]
        _require(isinstance(families, list) and bool(families)
                 and len(set(families)) == len(families) and set(families) <= set(FAMILIES),
                 "invalid, duplicate, or empty family selection")
        _require(configuration["line"] in ("cu129", "cu130") and configuration["gpus"] == 1,
                 "invalid CUDA line/GPU count")
        run_spec(configuration["mode"])
        expected = expected_workloads(configuration)
        complete = set(expected) == set(WORKLOADS) and set(families) == set(FAMILIES)
        _require(not require_full or complete, "full matrix required; selected attempt is a subset")
        _require(state["state"] == summary["state"] == "passed",
                 f"attempt did not pass: {state.get('failure') or state.get('verification_errors') or state.get('state')}")
        _require(state["run_id"] == summary["run_id"], "run identity mismatch")
        _require(summary["failed_workloads"] == [], "summary contains failed workloads")
        _require(len(summary["results"]) == len(expected)
                 and {r["id"] for r in summary["results"]} == set(expected),
                 "summary workload coverage mismatch")
        source_digests: set[str] = set()
        for workload_id in expected:
            backend, arm = WORKLOADS[workload_id]
            try:
                stage = state["stages"][backend]
                _require(stage["state"] == "passed" and stage["exit_code"] == 0,
                         f"{backend}: stage failed")
                for relative in (Path("logs") / f"{backend}.log",
                                 Path(backend) / workload_id / "stdout.txt",
                                 Path(backend) / workload_id / "stderr.txt"):
                    _require((attempt / relative).is_file(), f"missing execution log: {relative}")
                result = read_json(attempt / backend / f"{workload_id}.json")
                embedded = next(r for r in summary["results"] if r["id"] == workload_id)
                _require(result == embedded, f"{workload_id}: summary differs from workload report")
                _require(result["id"] == workload_id and result["backend"] == backend and result["arm"] == arm,
                         f"{workload_id}: workload identity mismatch")
                _require(result["mode"] == configuration["mode"] and result["line"] == configuration["line"],
                         f"{workload_id}: workload mode/line mismatch")
                _require(result["families"] == families, f"{workload_id}: family coverage mismatch")
                _require(result["verdict"] == "passed" and result["state"] == "completed"
                         and result["returncode"] == 0 and result["failed_criteria"] == []
                         and bool(result["criteria"]) and all(v is True for v in result["criteria"].values()),
                         f"{workload_id}: workload failed")
                arms = ("baseline",) if arm == "baseline" else ("baseline", "triton")
                cases = result["cases"]
                keys = [(c["family"], c["arm"]) for c in cases]
                _require(len(keys) == len(families) * len(arms)
                         and set(keys) == {(f, a) for f in families for a in arms},
                         f"{workload_id}: missing or duplicate cases")
                for case in cases:
                    _case(case, family=case["family"], backend=backend, arm=case["arm"],
                          configuration=configuration, checkout=checkout)
                    source_digests.add(case["source"]["workload_sha256"])
                comparisons = result["comparisons"]
                _require(len(comparisons) == (len(families) if arm == "triton" else 0),
                         f"{workload_id}: comparison coverage mismatch")
                _require(len({c["family"] for c in comparisons}) == len(comparisons),
                         f"{workload_id}: duplicate comparisons")
                for family in families if arm == "triton" else ():
                    baseline = next(c for c in cases if c["family"] == family and c["arm"] == "baseline")
                    candidate = next(c for c in cases if c["family"] == family and c["arm"] == "triton")
                    derived = compare_cases(baseline, candidate)
                    recorded = next(c for c in comparisons if c["family"] == family)
                    _require(derived["matched_inputs"], f"{workload_id}/{family}: unmatched inputs")
                    for key in ("matched_inputs", "baseline_tokens_per_second", "optimized_tokens_per_second", "speedup", "faster"):
                        _require(recorded[key] == derived[key], f"{workload_id}/{family}: comparison {key} disagrees")
                    rows.append({"backend": backend, "family": family, **derived,
                                 "baseline_median_ms": baseline["timing"]["median_step_seconds"] * 1000,
                                 "optimized_median_ms": candidate["timing"]["median_step_seconds"] * 1000,
                                 "baseline_wall_seconds": baseline["timing"]["wall_time_seconds"],
                                 "optimized_wall_seconds": candidate["timing"]["wall_time_seconds"]})
            except (OSError, ValueError, KeyError, TypeError, AttributeError, OverflowError, StopIteration) as exc:
                errors.append(f"{workload_id}: {exc}")
        _require(len(source_digests) == 1, "workload source digest differs across cases or is absent")
    except (OSError, ValueError, KeyError, TypeError, AttributeError, OverflowError) as exc:
        errors.append(str(exc))
    if not isinstance(configuration, dict):
        configuration = {}
    if not isinstance(checkout, dict):
        checkout = {}
    providers = {}
    try:
        if (attempt / "inputs").exists():
            read_json(attempt / "inputs/rootfs-bundle.json")
            for profile in {WORKLOADS[key][0] for key in expected_workloads(configuration)}:
                for package in (("pytorch", "triton") if profile == "torch" else ("jaxlib",)):
                    read_json(attempt / "inputs" / profile / f"{package}_action_build_plan.json")
                    metadata = read_json(attempt / "inputs" / profile / f"{package}_action_provider_metadata.json")
                    digest = metadata["action_input_digest"]
                    _require(len(digest) == 64 and all(c in "0123456789abcdef" for c in digest),
                             f"invalid native action input digest: {package}")
        for path in sorted((attempt / "inputs").glob("*/*_provider_metadata.json")):
            metadata = read_json(path)
            providers[str(path.relative_to(attempt))] = metadata["action_input_digest"]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        errors.append(f"invalid retained native input: {exc}")
    return {"schema_version": 1, "verdict": "failed" if errors else "passed",
            "verifier_sha256": {name: sha256(Path(__file__).with_name(name)) for name in VERIFIER_FILES},
            "scope": "full-matrix" if complete else "selected-subset",
            "configuration": configuration, "checkout": checkout, "errors": errors,
            "performance": "single-run-observation" if configuration.get("mode") == "benchmark" else "smoke-only",
            "comparisons": rows, "limits": LIMITS,
            "native_action_inputs": providers,
            "prepared_inputs_recorded": (attempt / "inputs/rootfs-bundle.json").is_file()}


def render(report: dict[str, Any]) -> str:
    lines = ["# Vaso decoder workload attestation", "",
             f"Verdict: **{report['verdict']}** · {report['scope']} · {report['performance']}", "",
             f"Source commit: `{report['checkout'].get('git_commit', 'unavailable')}`", "",
             f"Prepared rootfs manifest retained: **{'yes' if report['prepared_inputs_recorded'] else 'no (legacy evidence)'}**", "",
             "Throughput below excludes warmup; total wall time includes startup.", "",
             "| Backend | Family | Base tok/s | Opt tok/s | Ratio | Median ms base → opt | Wall s base → opt |",
             "|---|---|---:|---:|---:|---:|---:|"]
    for row in report["comparisons"]:
        lines.append(f"| {row['backend']} | {row['family']} | {row['baseline_tokens_per_second']:,.0f} | "
                     f"{row['optimized_tokens_per_second']:,.0f} | {row['speedup']:.3f}× | "
                     f"{row['baseline_median_ms']:.2f} → {row['optimized_median_ms']:.2f} | "
                     f"{row['baseline_wall_seconds']:.2f} → {row['optimized_wall_seconds']:.2f} |")
    lines += ["", "Acceptance checks numerical parity and execution, not an optimization speedup.", ""]
    lines += [f"- Native action input `{path}`: `{digest}`" for path, digest in report["native_action_inputs"].items()]
    lines += [f"- Failure: {error}" for error in report["errors"]]
    lines += [f"- {limit}" for limit in report["limits"]]
    return "\n".join(lines) + "\n"


def _write(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n")


def create_bundle(attempt: Path, output: Path, *, require_full: bool = True) -> dict[str, Any]:
    # Never overwrite historical evidence, including failed attestations.
    output.mkdir(parents=True, exist_ok=False)
    evidence = output / "evidence"
    evidence.mkdir()
    verifier = output / "verifier/workloads/nanogpt_speedrun"
    verifier.mkdir(parents=True)
    (verifier.parent / "__init__.py").write_text("")
    for name in VERIFIER_FILES:
        shutil.copyfile(Path(__file__).with_name(name), verifier / name)
    paths = [Path("state.json"), Path("summary.json")]
    for backend in ("torch", "jax"):
        paths += [Path("logs") / f"{backend}.log", Path(backend) / "summary.json"]
    for key, (backend, _) in WORKLOADS.items():
        paths.append(Path(backend) / f"{key}.json")
        paths += [Path(backend) / key / name for name in ("stdout.txt", "stderr.txt", "progress.json")]
    # Prepared-input records are optional for replay of older attempts.
    if (attempt / "inputs").is_dir():
        paths += [p.relative_to(attempt) for p in sorted((attempt / "inputs").rglob("*.json"))]
    for relative in paths:
        source = attempt / relative
        if not source.exists():
            continue
        _require(source.resolve().is_relative_to(attempt.resolve()), f"evidence escapes attempt: {relative}")
        target = evidence / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    report = evaluate(evidence, require_full=require_full)
    report["policy"] = {"require_full": require_full}
    _write(output / "report.json", report)
    (output / "report.md").write_text(render(report))
    manifest = {str(p.relative_to(output)): sha256(p) for p in sorted(output.rglob("*")) if p.is_file()}
    _write(output / "manifest.json", {"schema_version": 1, "sha256": manifest})
    return report


def verify_bundle(bundle: Path) -> dict[str, Any]:
    manifest = read_json(bundle / "manifest.json")["sha256"]
    _require(bool(manifest), "empty evidence manifest")
    actual = {str(p.relative_to(bundle)) for p in bundle.rglob("*") if p.is_file() and p != bundle / "manifest.json"}
    _require(actual == set(manifest), "bundle file coverage differs from manifest")
    for relative, digest in manifest.items():
        path = bundle / relative
        _require(path.resolve().is_relative_to(bundle.resolve()), f"bundle path escapes root: {relative}")
        _require(sha256(path) == digest, f"bundle digest mismatch: {relative}")
    report = read_json(bundle / "report.json")
    identity = {name: sha256(Path(__file__).with_name(name)) for name in VERIFIER_FILES}
    _require(report["verifier_sha256"] == identity,
             "verifier differs; replay with the verifier retained in this bundle")
    policy = report["policy"]
    derived = evaluate(bundle / "evidence", require_full=policy["require_full"])
    derived["policy"] = policy
    _require(report == derived, "report disagrees with replayed evidence")
    _require((bundle / "report.md").read_text() == render(report), "rendered report disagrees")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--attempt-dir", type=Path)
    source.add_argument("--verify-bundle", type=Path)
    parser.add_argument("--out", type=Path, help="new evidence directory; existing paths are refused")
    parser.add_argument("--allow-subset", action="store_true", help="label a selected subset explicitly")
    args = parser.parse_args(argv)
    if args.attempt_dir and not args.out:
        parser.error("--attempt-dir requires --out")
    if args.verify_bundle and (args.out or args.allow_subset):
        parser.error("--verify-bundle uses the retained policy and takes no output options")
    try:
        report = (verify_bundle(args.verify_bundle) if args.verify_bundle else
                  create_bundle(args.attempt_dir, args.out, require_full=not args.allow_subset))
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"attestation failed: {exc}", file=sys.stderr)
        return 1
    print(f"attestation {report['verdict']}: {report['scope']} ({report['performance']})")
    for error in report["errors"]:
        print(f"  {error}", file=sys.stderr)
    return 0 if report["verdict"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
