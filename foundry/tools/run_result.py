#!/usr/bin/env python3
"""Stage ledgers, invocation results, and solve diagnostics for Foundry and CI.

The generic insula launcher does not load this workflow adapter. The previous
insula/execution/result.py path remains a compatibility link for callers.
"""

from __future__ import annotations

import argparse
from collections.abc import Iterable
import json
import math
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

from build_report import atomic_json


ERROR_RE = re.compile(r"ERROR|error:|FAILED|Traceback")
SENSITIVE_RE = re.compile(r"(token|password|passwd|secret|credential|key)", re.IGNORECASE)
CONCRETIZE_SPEC_RE = re.compile(r"failed to concretize `([^`]+)`")
ENUMERATED_REASON_RE = re.compile(r"^\s*\d+\.\s+(?P<reason>.*\S)\s*$")
SPACK_ERROR_PREFIX_RE = re.compile(r"^(?:==>\s*)?Error:\s+")


def _redact_args(args: list[str]) -> list[str]:
    redacted: list[str] = []
    redact_next = False
    for arg in args:
        if redact_next:
            redacted.append("<redacted>")
            redact_next = False
            continue
        if "=" in arg:
            key, value = arg.split("=", 1)
            redacted.append(f"{key}=<redacted>" if SENSITIVE_RE.search(key) else arg)
            continue
        redacted.append("<redacted>" if SENSITIVE_RE.search(arg) else arg)
        if SENSITIVE_RE.search(arg):
            redact_next = True
    return redacted


def _first_error_line(path: Path) -> str | None:
    try:
        with path.open(encoding="utf-8", errors="replace") as handle:
            for line in handle:
                line = line.rstrip("\n")
                if ERROR_RE.search(line):
                    return line
    except OSError:
        return None
    return None


def _validate_stage(value: Any) -> None:
    """Require the same stage data when appending and reading a ledger."""
    if not isinstance(value, dict):
        raise ValueError("expected a stage object")
    for field in ("name", "log_path"):
        if not isinstance(value.get(field), str) or not value[field]:
            raise ValueError(f"stage {field} must be a nonempty string")
    if type(value.get("exit_code")) is not int:
        raise ValueError("stage exit_code must be an integer")
    duration = value.get("duration_seconds")
    try:
        valid_duration = (
            type(duration) in (int, float)
            and math.isfinite(duration)
            and duration >= 0
        )
    except OverflowError:
        valid_duration = False
    if not valid_duration:
        raise ValueError("stage duration_seconds must be finite and nonnegative")
    if value.get("first_error_line") is not None and not isinstance(
        value["first_error_line"], str
    ):
        raise ValueError("stage first_error_line must be a string or null")


def _load_stages(path: Path) -> list[dict[str, Any]]:
    stages: list[dict[str, Any]] = []
    if not path.exists():
        return stages
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
                _validate_stage(record)
            except ValueError as exc:
                raise ValueError(
                    f"invalid stage ledger {path}:{line_number}: {exc}"
                ) from exc
            stages.append(record)
    return stages


def _clean_spack_reason(line: str) -> str:
    reason = line.strip()
    numbered = ENUMERATED_REASON_RE.match(reason)
    if numbered:
        reason = numbered.group("reason")
    reason = SPACK_ERROR_PREFIX_RE.sub("", reason).strip()
    if reason.endswith(":") and reason.startswith("No version exists "):
        reason = reason[:-1]
    return reason


def _add_unique(items: list[str], item: str) -> None:
    if item and item not in items:
        items.append(item)


def _hint_for_solve_kinds(kinds: list[str]) -> str:
    if "compiler_external" in kinds:
        return (
            "Spack rejected compiler externals; check rootfs compiler packages.yaml "
            "for this CUDA line."
        )
    if "conflict" in kinds:
        return (
            "The requested spec conflicts with package constraints; try a "
            "compatible CUDA/package version or remove the conflicting pin."
        )
    if "no_version" in kinds:
        return (
            "No package version satisfies the requested version pins; check package "
            "recipes, mirrors, and the selected package versions."
        )
    if "gitversion" in kinds:
        return (
            "Spack version parser failed on a GitVersion object; pin a released "
            "version or update the Spack tool snapshot."
        )
    return "Spack concretization failed; inspect the listed constraints and selected package versions."


def parse_spack_solve_diagnostic(text: str, fallback_spec: str = "") -> dict[str, Any] | None:
    """Extract a concise Spack concretization diagnostic from captured output."""
    return parse_spack_solve_lines(text.splitlines(), fallback_spec)


def parse_spack_solve_lines(
    lines: Iterable[str], fallback_spec: str = ""
) -> dict[str, Any] | None:
    """Consume a log once, retaining only the diagnostic fields."""
    spec = ""
    constraints: list[str] = []
    kinds: list[str] = []

    awaiting_input_spec = False
    for line in lines:
        # A no-version header names its input on the next nonblank line. Keep
        # parsing that line too: it may contain another diagnostic reason.
        if awaiting_input_spec and line.strip():
            input_specs = _clean_spack_reason(line)
            if input_specs:
                _add_unique(constraints, input_specs)
                if not spec:
                    spec = input_specs
            awaiting_input_spec = False
        match = CONCRETIZE_SPEC_RE.search(line)
        if match and not spec:
            spec = match.group(1).strip()

        reason = _clean_spack_reason(line)
        if not reason:
            continue

        if "Only external, or concrete, compilers are allowed" in reason:
            _add_unique(constraints, reason)
            _add_unique(kinds, "compiler_external")
        elif "conflicts with" in reason:
            _add_unique(constraints, reason)
            _add_unique(kinds, "conflict")
        elif reason.startswith("No version exists that satisfies these input specs"):
            _add_unique(constraints, "No version exists that satisfies these input specs")
            _add_unique(kinds, "no_version")
            awaiting_input_spec = True
        elif "GitVersion" in reason and "dotted_numeric_string" in reason:
            _add_unique(constraints, reason)
            _add_unique(kinds, "gitversion")

    if not constraints:
        return None

    return {
        "spec": spec or fallback_spec.strip() or "unknown",
        "conflicting_constraints": constraints,
        "hint": _hint_for_solve_kinds(kinds),
    }


def _solve_diagnostics_for_stages(
    stages: list[dict[str, Any]], fallback_spec: str
) -> list[dict[str, Any]]:
    diagnostics: list[dict[str, Any]] = []
    for stage in stages:
        if stage["exit_code"] == 0:
            continue
        log_raw = stage.get("log_path")
        if not log_raw:
            continue
        try:
            with Path(str(log_raw)).open(encoding="utf-8", errors="replace") as stream:
                diagnostic = parse_spack_solve_lines(stream, fallback_spec=fallback_spec)
        except OSError:
            continue
        if diagnostic is None:
            continue
        diagnostics.append(
            {
                "stage": str(stage.get("name", "")),
                "log_path": str(log_raw),
                **diagnostic,
            }
        )
    return diagnostics


def _parse_leases(raw: str) -> list[dict[str, str]]:
    leases: list[dict[str, str]] = []
    for item in raw.split(","):
        if not item:
            continue
        if ":" in item:
            resource, lease_id = item.rsplit(":", 1)
        else:
            resource, lease_id = item, ""
        leases.append({"resource": resource, "id": lease_id})
    return leases


def cmd_redact_args(args: argparse.Namespace) -> int:
    raw_args = list(args.args)
    if raw_args and raw_args[0] == "--":
        raw_args = raw_args[1:]
    print(json.dumps(_redact_args(raw_args)))
    return 0


def cmd_stage(args: argparse.Namespace) -> int:
    record = {
        "name": args.name,
        "exit_code": args.exit_code,
        "duration_seconds": args.duration_seconds,
        "log_path": args.log_path,
    }
    _validate_stage(record)
    record["duration_seconds"] = round(args.duration_seconds, 3)
    record["first_error_line"] = _first_error_line(Path(args.log_path))
    path = Path(args.stages_jsonl)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True) + "\n")
    return 0


def cmd_finalize(args: argparse.Namespace) -> int:
    stages = _load_stages(Path(args.stages_jsonl))
    first_error = next((stage.get("first_error_line") for stage in stages if stage.get("first_error_line")), None)
    failed_stage = any(stage["exit_code"] != 0 for stage in stages)
    exit_code = args.exit_code
    if args.verdict == "passed" and (exit_code != 0 or failed_stage):
        raise ValueError(
            "cannot record a passed verdict with a nonzero command or stage exit code"
        )
    solve_diagnostics = _solve_diagnostics_for_stages(stages, args.solve_spec)
    verdict = args.verdict or ("passed" if exit_code == 0 and not failed_stage else "failed")
    doc = {
        "schema_version": 1,
        "mode": args.mode,
        "line": args.line,
        "agent": args.agent,
        "args": json.loads(args.args_json),
        "stages": stages,
        "first_error_line": first_error,
        "log_path": args.log_path,
        "leases": _parse_leases(args.leases),
        "commit": args.commit,
        "exit_code": exit_code,
        "verdict": verdict,
        "started_utc": args.started_utc,
        "finished_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    if args.blocked_reason:
        doc["blocked_reason"] = args.blocked_reason
    if solve_diagnostics:
        doc["solve_diagnostics"] = solve_diagnostics
    atomic_json(Path(args.out), doc)
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    redact = sub.add_parser("redact-args")
    redact.add_argument("args", nargs=argparse.REMAINDER)
    redact.set_defaults(func=cmd_redact_args)

    stage = sub.add_parser("stage")
    stage.add_argument("--stages-jsonl", required=True)
    stage.add_argument("--name", required=True)
    stage.add_argument("--exit-code", type=int, required=True)
    stage.add_argument("--duration-seconds", type=float, required=True)
    stage.add_argument("--log-path", required=True)
    stage.set_defaults(func=cmd_stage)

    finalize = sub.add_parser("finalize")
    finalize.add_argument("--out", required=True)
    finalize.add_argument("--stages-jsonl", required=True)
    finalize.add_argument("--mode", required=True)
    finalize.add_argument("--line", required=True)
    finalize.add_argument("--agent", required=True)
    finalize.add_argument("--args-json", required=True)
    finalize.add_argument("--log-path", required=True)
    finalize.add_argument("--leases", default="")
    finalize.add_argument("--commit", required=True)
    finalize.add_argument("--exit-code", type=int, required=True)
    finalize.add_argument("--started-utc", required=True)
    finalize.add_argument("--solve-spec", default="")
    finalize.add_argument("--verdict", choices=("passed", "failed", "blocked"))
    finalize.add_argument("--blocked-reason", default="")
    finalize.set_defaults(func=cmd_finalize)

    return parser


def main(argv: list[str] | None = None) -> int:
    if not os.environ.get("VASO_ESTATE_ROOT"):
        print("set VASO_ESTATE_ROOT", file=sys.stderr)
        return 2
    args = _parser().parse_args(argv)
    try:
        return int(args.func(args))
    except (OSError, ValueError) as exc:
        print(f"run result: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
