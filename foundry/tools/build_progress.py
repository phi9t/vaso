#!/usr/bin/env python3
"""Console rendering and bounded log excerpts for unattended Foundry builds."""

from __future__ import annotations

import math
import os
from pathlib import Path
import re
import shlex
import sys
from typing import Any, TextIO


ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
SUCCESS_STATES = {"built", "passed", "prepared"}
FAILURE_STATES = {"abandoned", "failed", "interrupted"}


def format_duration(seconds: Any) -> str:
    try:
        value = float(seconds)
        if not math.isfinite(value):
            return "-"
        total = max(0, int(value))
    except (TypeError, ValueError, OverflowError):
        return "-"
    hours, remainder = divmod(total, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours:d}:{minutes:02d}:{seconds:02d}"
    return f"{minutes:02d}:{seconds:02d}"


def human_size(size: Any) -> str:
    try:
        value = float(size)
    except (TypeError, ValueError, OverflowError):
        return "-"
    if not math.isfinite(value) or value < 0:
        return "-"
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if value < 1024 or unit == "TiB":
            return f"{int(value)} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return "-"


def _clean_line(value: str, width: int) -> str:
    value = ANSI_RE.sub("", value).replace("\x00", "")
    value = " ".join(value.split())
    if len(value) <= width:
        return value
    return value[: max(1, width - 1)] + "…"


def tail_lines(path: Path, *, count: int = 1, width: int = 180) -> list[str]:
    """Read a bounded, sanitized tail suitable for state files and terminals."""

    try:
        with path.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(0, size - 128 * 1024))
            text = handle.read().decode("utf-8", errors="replace")
    except OSError:
        return []
    lines = []
    for raw in text.replace("\r", "\n").splitlines():
        line = _clean_line(raw, width)
        if line and (not lines or line != lines[-1]):
            lines.append(line)
    return lines[-count:]


def _table(headers: list[str], rows: list[list[str]]) -> list[str]:
    widths = [len(header) for header in headers]
    for row in rows:
        for index, value in enumerate(row):
            widths[index] = max(widths[index], len(value))
    result = [
        "  ".join(value.ljust(widths[index]) for index, value in enumerate(headers))
    ]
    result.append("  ".join("─" * width for width in widths))
    result.extend(
        "  ".join(value.ljust(widths[index]) for index, value in enumerate(row))
        for row in rows
    )
    return result


def report_text(report: dict[str, Any]) -> str:
    """Render a stable plain-text run result for terminals and result.txt."""

    status = str(report.get("status", "unknown"))
    lines = [f"Vaso Foundry build — {status.upper()}"]
    lines.append(f"Run       {report.get('run_id', '-')}")
    if report.get("pid"):
        lines.append(f"PID       {report['pid']}")
    lines.append(f"Phase     {report.get('phase', '-')}")
    cache_start = report.get("cache_start")
    if isinstance(cache_start, dict) and cache_start.get("mode"):
        lines.append(f"Cache     {str(cache_start['mode']).upper()} at launch")
    lines.append(f"Elapsed   {format_duration(report.get('duration_seconds'))}")
    current = report.get("current_stage")
    if current:
        lines.append(f"Current   {current}")
        stages = report.get("stages", [])
        if isinstance(stages, list):
            matching = [
                stage
                for stage in stages
                if isinstance(stage, dict) and stage.get("name") == current
            ]
            if matching:
                stage = matching[-1]
                if stage.get("progress"):
                    lines.append(f"Progress  {stage['progress']}")
                if stage.get("log"):
                    lines.append(f"Log       {stage['log']}")
    elif report.get("current_input"):
        lines.append(f"Input     {report['current_input']}")
    updated = report.get("updated_at")
    if updated:
        lines.append(f"Updated   {updated}")

    variants = report.get("variants", [])
    if isinstance(variants, list) and variants:
        rows = []
        for variant in variants:
            if not isinstance(variant, dict):
                continue
            artifacts = variant.get("artifacts", [])
            count = len(artifacts) if isinstance(artifacts, list) else 0
            rows.append(
                [
                    str(variant.get("line", "-")),
                    str(variant.get("profile", "-")),
                    str(variant.get("status", "pending")).upper(),
                    format_duration(variant.get("duration_seconds")),
                    str(count),
                ]
            )
        lines.extend(
            [
                "",
                "Matrix",
                *_table(["CUDA", "PROFILE", "STATE", "TIME", "WHEELS"], rows),
            ]
        )

        artifact_rows = []
        for variant in variants:
            if not isinstance(variant, dict):
                continue
            for artifact in variant.get("artifacts", []):
                if not isinstance(artifact, dict):
                    continue
                availability = "-"
                if artifact.get("exists") is True:
                    availability = "PRESENT"
                elif artifact.get("exists") is False:
                    availability = "MISSING"
                artifact_rows.append(
                    [
                        f"{variant.get('line', '-')}/{variant.get('profile', '-')}",
                        str(artifact.get("name", "-")),
                        availability,
                        human_size(artifact.get("size_bytes")),
                        str(artifact.get("digest", ""))[:12] or "-",
                    ]
                )
        if artifact_rows:
            lines.extend(
                [
                    "",
                    "Artifacts",
                    *_table(["VARIANT", "WHEEL", "FILE", "SIZE", "DIGEST"], artifact_rows),
                ]
            )

    if report.get("error"):
        lines.extend(["", f"Error     {report['error']}"])
    stages = report.get("stages", [])
    if isinstance(stages, list):
        failed = [
            stage
            for stage in stages
            if isinstance(stage, dict) and stage.get("status") in FAILURE_STATES
        ]
        if failed:
            stage = failed[-1]
            lines.append(f"Failed    {stage.get('name', '-')}")
            if stage.get("log"):
                lines.append(f"Log       {stage['log']}")
            excerpt = stage.get("tail", [])
            if isinstance(excerpt, list) and excerpt:
                lines.append("Log tail")
                lines.extend(f"  {line}" for line in excerpt)

    lines.extend(
        [
            "",
            f"Summary   {report.get('summary_path', '-')}",
            f"Events    {report.get('events_path', '-')}",
            f"Result    {report.get('result_path', '-')}",
        ]
    )
    if report.get("qualification"):
        lines.append(f"Qualify   {report['qualification']}")
    return "\n".join(lines) + "\n"


class Console:
    """A scrollback-friendly renderer with color when attached to tmux or a TTY."""

    COLORS = {
        "cyan": "\x1b[36m",
        "green": "\x1b[32m",
        "red": "\x1b[31m",
        "yellow": "\x1b[33m",
        "bold": "\x1b[1m",
        "reset": "\x1b[0m",
    }

    def __init__(
        self,
        *,
        stream: TextIO | None = None,
        color: str = "auto",
        environ: dict[str, str] | None = None,
        interactive: bool | None = None,
    ):
        self.stream = stream if stream is not None else sys.stdout
        env = os.environ if environ is None else environ
        is_tty = self.stream.isatty() if interactive is None else interactive
        self.tmux = bool(env.get("TMUX")) and is_tty
        self.interactive = is_tty and env.get("TERM") != "dumb"
        self.color = color == "always" or (
            color == "auto" and self.interactive and "NO_COLOR" not in env
        )
        self.available = True

    @property
    def mode(self) -> str:
        if self.tmux:
            return "tmux"
        return "terminal" if self.interactive else "plain"

    def _style(self, value: str, *styles: str) -> str:
        if not self.color:
            return value
        return (
            "".join(self.COLORS[style] for style in styles)
            + value
            + self.COLORS["reset"]
        )

    def _write(self, value: str = "") -> None:
        if not self.available:
            return
        try:
            print(value, file=self.stream, flush=True)
        except (BrokenPipeError, OSError):
            self.available = False

    def banner(self, report: dict[str, Any], estate: Path) -> None:
        self._write(self._style("Vaso Foundry build matrix", "bold", "cyan"))
        self._write(f"  run      {report['run_id']}")
        self._write(
            f"  matrix   {', '.join(report['lines'])} × {', '.join(report['profiles'])}"
        )
        self._write(f"  summary  {report['summary_path']}")
        cache_start = report.get("cache_start", {})
        cache_mode = cache_start.get("mode") if isinstance(cache_start, dict) else None
        if cache_mode == "cold":
            self._write(
                "  cache    cold; selected output bases and action caches were empty"
            )
        elif cache_mode == "warm":
            self._write(
                "  cache    warm; reuse still depends on matching Bazel action keys"
            )
        self._write(
            "  bazel    local=executing here; disk-cache=enabled, not a cache hit"
        )
        status_cmd = (
            f"foundry/build.sh --estate-root {shlex.quote(str(estate))} --status"
        )
        self._write(f"  inspect  {status_cmd}")
        if self.tmux:
            self._write("  tmux     safe to detach; progress and state continue")
        self._write()

    def phase(self, name: str) -> None:
        self._write(self._style(f"── {name.replace('_', ' ').title()} ──", "bold"))

    def stage_started(self, entry: dict[str, Any]) -> None:
        self._write(self._style(f"▶ {entry['name']}", "cyan") + f"  log={entry['log']}")

    def stage_progress(self, entry: dict[str, Any]) -> None:
        elapsed = format_duration(entry.get("duration_seconds"))
        detail = str(entry.get("progress", "working"))
        self._write(
            self._style(f"… {entry['name']} {elapsed}", "yellow") + f"  {detail}"
        )

    def stage_finished(self, entry: dict[str, Any]) -> None:
        status = str(entry.get("status", "failed"))
        marker = "✓" if status == "passed" else "✗"
        color = "green" if status == "passed" else "red"
        elapsed = format_duration(entry.get("duration_seconds"))
        self._write(self._style(f"{marker} {entry['name']} {status} {elapsed}", color))

    def final(self, report: dict[str, Any]) -> None:
        status = str(report.get("status", "unknown"))
        color = (
            "green"
            if status in SUCCESS_STATES
            else "red"
            if status in FAILURE_STATES
            else "yellow"
        )
        rendered = report_text(report).rstrip("\n").splitlines()
        self._write()
        for index, line in enumerate(rendered):
            self._write(self._style(line, "bold", color) if index == 0 else line)
