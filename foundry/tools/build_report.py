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


def _summary_candidates(state_root: Path) -> Iterator[Path]:
    """Try the selected summary before inspecting legacy run directories."""
    pointer = state_root / "latest.json"
    if pointer.is_file():
        value = read_json(pointer).get("summary")
        if isinstance(value, str):
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
        report = read_json(resolved)
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
