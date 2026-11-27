"""Durable report storage and selection for unattended Foundry builds.

The attempt directory owns summary.json, events.jsonl, and result.txt. Persisted
absolute paths describe placement; they do not grant ownership of another run.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timezone
import json
import os
import tempfile
from pathlib import Path
from typing import Any


def utc_now() -> str:
    """Return an ISO-8601 UTC timestamp with stable second precision."""

    return (
        datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    )


def report_paths(output: Path) -> dict[str, str]:
    """Locate the files owned by one attempt directory."""
    return {
        "summary_path": str(output / "summary.json"),
        "events_path": str(output / "events.jsonl"),
        "result_path": str(output / "result.txt"),
    }


def atomic_text(path: Path, value: str) -> None:
    """Publish complete text, preserving the previous file on a write failure."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        dir=path.parent, prefix=f".{path.name}."
    ) as directory:
        temporary = Path(directory) / "report"
        with temporary.open("w", encoding="utf-8") as handle:
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    """Atomically replace a JSON object on the same filesystem."""
    atomic_text(path, json.dumps(value, indent=2, sort_keys=True) + "\n")


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"{path}: invalid JSON at line {exc.lineno}, column {exc.colno}: {exc.msg}"
        ) from exc
    if not isinstance(value, dict):
        raise ValueError(f"{path}: expected a JSON object")
    return value


def _object_list(value: Any, path: Path, field: str) -> list[dict[str, Any]]:
    """Validate report containers before rendering or mutating their records."""
    if not isinstance(value, list):
        raise ValueError(f"{path}: {field}: expected a list of objects")
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            raise ValueError(f"{path}: {field}[{index}]: expected an object")
    return value


def read_summary(path: Path) -> dict[str, Any]:
    """Load attempt records; omitted legacy lists remain supported."""
    report = read_json(path)
    _object_list(report.get("stages", []), path, "stages")
    variants = _object_list(report.get("variants", []), path, "variants")
    for index, variant in enumerate(variants):
        _object_list(
            variant.get("artifacts", []), path, f"variants[{index}].artifacts"
        )
    return report


def _summary_candidates(state_root: Path) -> Iterator[Path]:
    """Try the selected summary before inspecting legacy run directories."""
    pointer = state_root / "latest.json"
    if pointer.is_file():
        value = read_json(pointer).get("summary")
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{pointer}: expected a nonempty summary path")
        yield Path(value)
    # The pointer is written by the estate lock owner. File modification times
    # are only a legacy fallback: inspecting or restoring an older summary must
    # not redirect status or stale-run recovery away from the selected run.
    yield from sorted(
        state_root.glob("*/summary.json"),
        key=lambda path: path.stat().st_mtime if path.is_file() else -1,
        reverse=True,
    )


def latest_report(state_root: Path) -> tuple[dict[str, Any], Path] | None:
    """Load the selected report, scanning legacy history only if it is missing."""

    seen = set()
    for summary in _summary_candidates(state_root):
        try:
            resolved = summary.expanduser().resolve()
        except OSError:
            continue
        if resolved in seen or not resolved.is_file():
            continue
        seen.add(resolved)
        report = read_summary(resolved)
        report.update(report_paths(resolved.parent), summary_path=str(resolved))
        report.setdefault("run_id", resolved.parent.name)
        return report, resolved
    return None


def latest_pointer(report: dict[str, Any]) -> dict[str, Any]:
    variants = report.get("variants", [])
    counts: dict[str, int] = {}
    if isinstance(variants, list):
        for variant in variants:
            if isinstance(variant, dict):
                state = str(variant.get("status", "unknown"))
                counts[state] = counts.get(state, 0) + 1
    return {
        "schema_version": 1,
        "run_id": report.get("run_id"),
        "status": report.get("status"),
        "phase": report.get("phase"),
        "updated_at": report.get("updated_at"),
        "current_stage": report.get("current_stage"),
        "variant_counts": counts,
        "summary": report.get("summary_path"),
        "result": report.get("result_path"),
    }


def _target_variant(report: dict, line: str, profile: str) -> dict | None:
    matches = [v for v in report.get("variants", [])
               if v.get("line") == line and v.get("profile") == profile]
    if len(matches) > 1:
        raise ValueError("report has duplicate entries for the selected target")
    return matches[0] if matches else None


def latest_target_report(state_root: Path, line: str, profile: str):
    """Select a matching attempt, preserving the authoritative latest pointer.

    A newer run of another target cannot hide this target's current attempt.
    Recorded start times order historical matching attempts; editing an older
    report does not advance it merely by changing its filesystem timestamp.
    """
    selected = latest_report(state_root)
    pointer = state_root / "latest.json"
    authoritative = (selected is not None and pointer.is_file()
                     and Path(read_json(pointer)["summary"]).expanduser().resolve() == selected[1])
    if authoritative and _target_variant(selected[0], line, profile) is not None:
        return selected
    candidates = []
    seen = set()
    for summary in state_root.glob("*/summary.json"):
        resolved = summary.resolve()
        if resolved in seen or not resolved.is_file():
            continue
        report = read_summary(resolved)
        if _target_variant(report, line, profile) is None:
            continue
        report.update(report_paths(resolved.parent), summary_path=str(resolved))
        report.setdefault("run_id", resolved.parent.name)
        candidates.append((str(report.get("started_at", "")), resolved.parent.name,
                           resolved.stat().st_mtime, report, resolved))
    if not candidates:
        return None
    selected = max(candidates, key=lambda item: item[:3])
    return selected[3], selected[4]


def target_result(report: dict, target: str) -> dict:
    """Normalize one modern matrix variant to the canonical result schema."""
    line, profile = target.split("/")
    variant = _target_variant(report, line, profile)
    if variant is None:
        raise ValueError("report does not contain selected target " + target)
    state = variant.get("status", "unknown")
    if report.get("status") == "prepared" and state in ("pending", "skipped"):
        state = "prepared"
    elif state in ("pending", "running") and report.get("status") in ("failed", "interrupted", "abandoned"):
        state = report["status"]
    terminal = state in ("built", "prepared", "failed", "interrupted", "abandoned")
    relevant = [stage for stage in report.get("stages", [])
                if stage.get("line") == line and stage.get("profile") == profile]
    rc = 0 if state in ("built", "prepared") else None
    if terminal and rc is None:
        rc = next((s["returncode"] for s in reversed(relevant) if s.get("returncode")), 1)
    phase = report.get("requested_phase")
    if phase is not None and phase not in ("prepare", "build"):
        raise ValueError("report has an invalid requested_phase")
    if phase is None:
        if state == "prepared":
            phase = "prepare"
        elif state == "built" or any(stage.get("name", "").startswith("build-") for stage in relevant):
            phase = "build"
        else:
            # Historical reports did not persist request intent. Early failed
            # preparation and build requests cannot be distinguished honestly.
            phase = "unknown"
    artifacts = [{**item, "bytes": item.get("size_bytes"), "sha256": item.get("digest")}
                 for item in variant.get("artifacts", [])]
    return {
        "schema": "vaso.foundry-build-result.v1", "target": target,
        "phase": phase, "state": state,
        "backend": "bazel", "runtime": "insula", "request_id": report.get("run_id"),
        "exit_code": rc, "started_at": report.get("started_at"),
        "finished_at": variant.get("finished_at", report.get("finished_at")),
        "artifacts": artifacts, "runtime_qualified": False,
        "execution": {"owner": "build-session", "summary_path": report["summary_path"],
                      "output_base": variant.get("output_base"), "build_events": variant.get("build_events")},
        "lifecycle": {"state": report.get("status"), "cleanup_complete": None,
                      "evidence": "legacy BuildSession process lifecycle; no standalone owner cleanup receipt"},
        "error": variant.get("error", report.get("error")),
    }
