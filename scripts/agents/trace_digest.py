#!/usr/bin/env python3
"""Summarize TraeCode rollout traces and headless worker runs.

The digest intentionally records metadata only: counts, timestamps, token
totals, statuses, and redacted command summaries. It never copies prompts,
agent messages, stdout, stderr, or worker briefs into the output.
"""
from __future__ import annotations

import argparse
import heapq
import json
import os
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


OLD_TRAE_SESSION_ID = "01a0d267-c5e5-7963-8886-662443861a1a"
APP_THREAD_SESSION_ID = "01a0f3c6-274a-79c3-a0ac-11bde36a3189"
LONGEST_LIMIT = 10
COMMAND_LIMIT = 160
NOTABLE_PATTERNS = (
    ("run.sh", re.compile(r"(?:^|[\s;&|])(?:bash\s+(?:-x\s+)?)?\./run\.sh(?:\s|$)")),
    ("bazel", re.compile(r"(?:^|[\s;&|])(?:\S*/)?bazel(?:-real|-[0-9.]+)?\s+(?:--\S+\s+)*(?:build|test|fetch|query|aquery|info|run|cquery|clean)\b")),
    ("triumvirate-gate", re.compile(r"(?:^|[\s;&|])(?:bash\s+)?(?:\S*/)?triumvirate-gate\.sh(?:\s|$)")),
    ("run_workloads", re.compile(r"(?:^|[\s;&|])(?:python3?\s+)(?:\S*/)?run_workloads\.py(?:\s|$)")),
    ("gates.py", re.compile(r"(?:^|[\s;&|])(?:python3?\s+)(?:\S*/)?gates\.py(?:\s|$)")),
    ("git commit", re.compile(r"\bgit\s+(?:-[CS]\s+\S+\s+)*commit\b")),
    ("verified-commit", re.compile(r"verified-commit\.sh")),
    ("relay land", re.compile(r"relay\.py\s+land")),
)
INSPECTION_PREFIXES = (
    "sed ",
    "nl ",
    "rg ",
    "grep ",
    "find ",
    "ls ",
    "cat ",
    "git diff",
    "git show",
)

SECRET_PATTERNS = (
    re.compile(r"(?i)(Authorization:\s*(?:Bearer|Basic)\s+)[A-Za-z0-9._~+/=-]+"),
    re.compile(r"(?i)\b([A-Z0-9_]*(?:TOKEN|SECRET|PASSWORD|PASSWD|API_KEY|ACCESS_KEY)[A-Z0-9_]*=)([^\s'\"]+)"),
    re.compile(r"(?i)\b((?:token|secret|password|api[_-]?key)=)([^\s&'\"]+)"),
    re.compile(r"\b[A-Za-z0-9+/]{80,}={0,2}\b"),
)


def parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    text = str(value)
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def format_time(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def in_window(value: datetime | None, since: datetime | None, until: datetime | None) -> bool:
    if value is None:
        return False
    if since is not None and value < since:
        return False
    if until is not None and value > until:
        return False
    return True


def safe_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            return None
    return None


def duration_seconds(value: Any) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    if isinstance(value, dict):
        secs = safe_int(value.get("secs")) or 0
        nanos = safe_int(value.get("nanos")) or 0
        return secs + nanos / 1_000_000_000
    if isinstance(value, str):
        match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*(ms|s|m|h)?\s*", value)
        if not match:
            return None
        amount = float(match.group(1))
        unit = match.group(2) or "s"
        return amount * {"ms": 0.001, "s": 1.0, "m": 60.0, "h": 3600.0}[unit]
    return None


def ms_to_seconds(value: Any) -> float | None:
    parsed = safe_int(value)
    if parsed is None:
        return None
    return parsed / 1000.0


def path_under(path: str | None, prefix: str | None) -> bool:
    if not prefix:
        return True
    if not path:
        return False
    normalized_path = os.path.abspath(path)
    normalized_prefix = os.path.abspath(prefix)
    return normalized_path == normalized_prefix or normalized_path.startswith(normalized_prefix + os.sep)


def redacted_command(command: Any, limit: int = COMMAND_LIMIT) -> str:
    if isinstance(command, list):
        text = " ".join(str(part) for part in command)
    elif command is None:
        text = ""
    else:
        text = str(command)
    text = " ".join(text.split())
    for pattern in SECRET_PATTERNS:
        def repl(match: re.Match[str]) -> str:
            if match.lastindex and match.lastindex >= 1:
                return match.group(1) + "<redacted>"
            return "<redacted>"

        text = pattern.sub(repl, text)
    if len(text) > limit:
        return text[: max(0, limit - 1)] + "..."
    return text


def command_for_matching(command: Any) -> str:
    if isinstance(command, list):
        parts = [str(part) for part in command]
        if len(parts) >= 3 and os.path.basename(parts[0]) in {"bash", "sh"} and parts[1] in {"-c", "-lc"}:
            return parts[2]
        return " ".join(parts)
    return "" if command is None else str(command)


def classify_notable(command: Any) -> str | None:
    semantic = " ".join(command_for_matching(command).split())
    lowered = semantic.lower()
    if lowered.startswith(INSPECTION_PREFIXES) or " python3 - <<'" in lowered or " python3 - <<" in lowered:
        return None
    for label, pattern in NOTABLE_PATTERNS:
        if pattern.search(semantic):
            return label
    return None


def failure_key(exit_code: Any, status: Any = None) -> str | None:
    code = safe_int(exit_code)
    if code is not None:
        return None if code == 0 else str(code)
    if status and str(status) not in {"completed", "success", "ok"}:
        return "unknown"
    return None


@dataclass(order=True)
class DurationEntry:
    duration_seconds: float
    timestamp: str = field(compare=False)
    exit_code: int | None = field(compare=False)
    command: str = field(compare=False)
    cwd: str | None = field(compare=False, default=None)


@dataclass
class CommandStats:
    count: int = 0
    failures_by_exit_code: dict[str, int] = field(default_factory=dict)
    longest_heap: list[DurationEntry] = field(default_factory=list)

    def add(self, timestamp: str, command: Any, exit_code: Any, status: Any, duration: Any, cwd: str | None) -> None:
        self.count += 1
        key = failure_key(exit_code, status)
        if key is not None:
            self.failures_by_exit_code[key] = self.failures_by_exit_code.get(key, 0) + 1
        seconds = duration_seconds(duration)
        if seconds is None:
            seconds = ms_to_seconds(duration)
        if seconds is None:
            return
        entry = DurationEntry(
            duration_seconds=round(seconds, 3),
            timestamp=timestamp,
            exit_code=safe_int(exit_code),
            command=redacted_command(command),
            cwd=cwd,
        )
        if len(self.longest_heap) < LONGEST_LIMIT:
            heapq.heappush(self.longest_heap, entry)
        elif entry.duration_seconds > self.longest_heap[0].duration_seconds:
            heapq.heapreplace(self.longest_heap, entry)

    def as_dict(self) -> dict[str, Any]:
        return {
            "count": self.count,
            "failures_by_exit_code": dict(sorted(self.failures_by_exit_code.items())),
            "longest": [
                {
                    "timestamp": item.timestamp,
                    "duration_seconds": item.duration_seconds,
                    "exit_code": item.exit_code,
                    "cwd": item.cwd,
                    "command": item.command,
                }
                for item in sorted(self.longest_heap, reverse=True)
            ],
        }


@dataclass
class PatchStats:
    ok: int = 0
    failed: int = 0

    def add(self, success: Any, status: Any) -> None:
        if success is True or str(status).lower() in {"ok", "success", "completed"}:
            self.ok += 1
        else:
            self.failed += 1

    def as_dict(self) -> dict[str, int]:
        return {"ok": self.ok, "failed": self.failed}


@dataclass
class TokenStats:
    events: int = 0
    peak_context_tokens: int = 0
    peak_context_timestamp: str | None = None
    first_total_tokens: int | None = None
    first_total_timestamp: str | None = None
    last_total_tokens: int | None = None
    last_total_timestamp: str | None = None
    last_total_usage: dict[str, int] = field(default_factory=dict)
    model_context_window: int | None = None
    auto_compact_token_limit: int | None = None

    def add(self, timestamp: str, info: dict[str, Any]) -> None:
        self.events += 1
        last = info.get("last_token_usage") or {}
        total = info.get("total_token_usage") or {}
        context_tokens = safe_int(last.get("total_tokens"))
        if context_tokens is not None and context_tokens > self.peak_context_tokens:
            self.peak_context_tokens = context_tokens
            self.peak_context_timestamp = timestamp
        total_tokens = safe_int(total.get("total_tokens"))
        if total_tokens is not None:
            if self.first_total_tokens is None:
                self.first_total_tokens = total_tokens
                self.first_total_timestamp = timestamp
            self.last_total_tokens = total_tokens
            self.last_total_timestamp = timestamp
        self.last_total_usage = {
            key: value
            for key, raw in total.items()
            if (value := safe_int(raw)) is not None
        }
        self.model_context_window = safe_int(info.get("model_context_window")) or self.model_context_window
        self.auto_compact_token_limit = safe_int(info.get("auto_compact_token_limit")) or self.auto_compact_token_limit

    def as_dict(self) -> dict[str, Any]:
        delta = None
        if self.first_total_tokens is not None and self.last_total_tokens is not None:
            delta = max(0, self.last_total_tokens - self.first_total_tokens)
        return {
            "events": self.events,
            "peak_context_tokens": self.peak_context_tokens,
            "peak_context_timestamp": self.peak_context_timestamp,
            "model_context_window": self.model_context_window,
            "auto_compact_token_limit": self.auto_compact_token_limit,
            "first_total_tokens": self.first_total_tokens,
            "first_total_timestamp": self.first_total_timestamp,
            "last_total_tokens": self.last_total_tokens,
            "last_total_timestamp": self.last_total_timestamp,
            "total_token_delta_in_window": delta,
            "last_total_usage": self.last_total_usage,
        }


@dataclass
class RolloutDigest:
    path: str
    id: str
    parent_session_id: str | None = None
    role: str = "subagent"
    cwd: str | None = None
    session_start: str | None = None
    window_start: str | None = None
    window_end: str | None = None
    line_count: int = 0
    malformed_lines: int = 0
    task_started: int = 0
    task_complete: int = 0
    task_duration_seconds: float = 0.0
    turn_ids: set[str] = field(default_factory=set)
    exec_commands: CommandStats = field(default_factory=CommandStats)
    patch_applies: PatchStats = field(default_factory=PatchStats)
    context_compacted_timestamps: list[str] = field(default_factory=list)
    tokens: TokenStats = field(default_factory=TokenStats)
    subagent_spawns: list[dict[str, Any]] = field(default_factory=list)

    def mark_time(self, timestamp: str) -> None:
        if self.window_start is None:
            self.window_start = timestamp
        self.window_end = timestamp

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "parent_session_id": self.parent_session_id,
            "role": self.role,
            "path": self.path,
            "cwd": self.cwd,
            "session_start": self.session_start,
            "start": self.window_start,
            "end": self.window_end,
            "window_start": self.window_start,
            "window_end": self.window_end,
            "turns": len(self.turn_ids),
            "task_started": self.task_started,
            "task_complete": self.task_complete,
            "task_duration_seconds": round(self.task_duration_seconds, 3),
            "exec_commands": self.exec_commands.as_dict(),
            "patch_applies": self.patch_applies.as_dict(),
            "context_compacted": {
                "count": len(self.context_compacted_timestamps),
                "timestamps": self.context_compacted_timestamps,
            },
            "tokens": self.tokens.as_dict(),
            "subagent_spawns": self.subagent_spawns,
            "line_count": self.line_count,
            "malformed_lines": self.malformed_lines,
        }


def session_id_from_path(path: Path) -> str:
    match = re.search(r"rollout-[^.]*-([0-9a-f-]{36})\.jsonl$", path.name)
    if match:
        return match.group(1)
    return path.stem


def classify_rollout(path: Path, session_id: str, cwd: str | None) -> str:
    if session_id == OLD_TRAE_SESSION_ID:
        return "TRAE"
    if session_id == APP_THREAD_SESSION_ID:
        return "TRAE-thread"
    if cwd and "/.worktrees/" in cwd:
        return "worker"
    if "agents" in path.parts or ".vaso-estate" in str(path):
        return "worker"
    return "subagent"


def update_rollout_metadata(digest: RolloutDigest, payload: dict[str, Any], path: Path) -> None:
    parent_session_id = payload.get("session_id")
    if parent_session_id:
        digest.parent_session_id = str(parent_session_id)
    session_id = payload.get("id") or payload.get("session_id")
    if session_id:
        digest.id = str(session_id)
    cwd = payload.get("cwd")
    if cwd and digest.cwd is None:
        digest.cwd = str(cwd)
    started = payload.get("timestamp")
    if started and digest.session_start is None:
        digest.session_start = str(started)
    digest.role = classify_rollout(path, digest.id, digest.cwd)


def add_timeline_event(
    timeline: list[dict[str, Any]],
    *,
    timestamp: str,
    source_id: str,
    source_role: str,
    category: str,
    command: Any,
    exit_code: Any,
    duration: Any,
    cwd: str | None,
) -> None:
    seconds = duration_seconds(duration)
    if seconds is None:
        seconds = ms_to_seconds(duration)
    timeline.append(
        {
            "timestamp": timestamp,
            "source_id": source_id,
            "source_role": source_role,
            "category": category,
            "duration_seconds": round(seconds, 3) if seconds is not None else None,
            "exit_code": safe_int(exit_code),
            "cwd": cwd,
            "command": redacted_command(command),
        }
    )


def event_timestamp(obj: dict[str, Any]) -> tuple[datetime | None, str | None]:
    raw = obj.get("timestamp")
    parsed = parse_time(raw)
    return parsed, str(raw) if raw else None


def digest_rollout(path: Path, since: datetime | None, until: datetime | None, cwd_prefix: str | None) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    digest = RolloutDigest(path=str(path), id=session_id_from_path(path))
    timeline: list[dict[str, Any]] = []
    included_by_cwd = False
    saw_cwd = False

    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            digest.line_count += 1
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                digest.malformed_lines += 1
                continue
            typ = obj.get("type")
            payload = obj.get("payload") if isinstance(obj.get("payload"), dict) else {}
            parsed, raw_ts = event_timestamp(obj)
            if parsed is not None and until is not None and parsed > until:
                break
            if typ == "session_meta":
                update_rollout_metadata(digest, payload, path)
                if digest.cwd:
                    saw_cwd = True
                    included_by_cwd = included_by_cwd or path_under(digest.cwd, cwd_prefix)
                continue
            if typ == "turn_context":
                cwd = payload.get("cwd")
                if cwd and digest.cwd is None:
                    digest.cwd = str(cwd)
                    digest.role = classify_rollout(path, digest.id, digest.cwd)
                if cwd:
                    saw_cwd = True
                    included_by_cwd = included_by_cwd or path_under(str(cwd), cwd_prefix)
                turn_id = payload.get("turn_id")
                parsed, raw_ts = event_timestamp(obj)
                if turn_id and raw_ts and in_window(parsed, since, until) and path_under(str(cwd or digest.cwd or ""), cwd_prefix):
                    digest.turn_ids.add(str(turn_id))
                    digest.mark_time(raw_ts)
                continue

            if not raw_ts or not in_window(parsed, since, until):
                continue

            event_cwd = payload.get("cwd")
            if event_cwd:
                saw_cwd = True
                included_by_cwd = included_by_cwd or path_under(str(event_cwd), cwd_prefix)
            if cwd_prefix and saw_cwd and not (included_by_cwd or path_under(str(event_cwd or ""), cwd_prefix)):
                continue
            if cwd_prefix and not saw_cwd and digest.cwd and not path_under(digest.cwd, cwd_prefix):
                continue

            ptype = payload.get("type")
            if typ != "event_msg":
                continue
            digest.mark_time(raw_ts)
            turn_id = payload.get("turn_id") or payload.get("root_turn_id")
            if turn_id:
                digest.turn_ids.add(str(turn_id))

            if ptype == "task_started":
                digest.task_started += 1
            elif ptype == "task_complete":
                digest.task_complete += 1
                seconds = ms_to_seconds(payload.get("duration_ms"))
                if seconds is not None:
                    digest.task_duration_seconds += seconds
            elif ptype == "exec_command_end":
                command = payload.get("command")
                cwd = str(payload.get("cwd")) if payload.get("cwd") else None
                digest.exec_commands.add(raw_ts, command, payload.get("exit_code"), payload.get("status"), payload.get("duration"), cwd)
                category = classify_notable(command)
                if category:
                    add_timeline_event(
                        timeline,
                        timestamp=raw_ts,
                        source_id=digest.id,
                        source_role=digest.role,
                        category=category,
                        command=command,
                        exit_code=payload.get("exit_code"),
                        duration=payload.get("duration"),
                        cwd=cwd,
                    )
            elif ptype == "patch_apply_end":
                digest.patch_applies.add(payload.get("success"), payload.get("status"))
            elif ptype == "context_compacted":
                digest.context_compacted_timestamps.append(raw_ts)
            elif ptype == "token_count":
                info = payload.get("info")
                if isinstance(info, dict):
                    digest.tokens.add(raw_ts, info)
            elif ptype == "collab_agent_spawn_end":
                digest.subagent_spawns.append(
                    {
                        "timestamp": raw_ts,
                        "thread_id": payload.get("new_thread_id"),
                        "nickname": payload.get("new_agent_nickname"),
                        "role": payload.get("new_agent_role"),
                        "status": payload.get("status"),
                        "model": payload.get("model"),
                    }
                )

    if cwd_prefix and digest.cwd and not path_under(digest.cwd, cwd_prefix) and not included_by_cwd:
        return None, []
    if cwd_prefix and not digest.cwd and not included_by_cwd:
        return None, []
    if digest.window_start is None:
        return None, []
    return digest.as_dict(), timeline


def worker_end_time(run_dir: Path) -> datetime | None:
    candidates = [run_dir / "exit_code", run_dir / "last-message.md", run_dir / "events.jsonl"]
    existing = [path for path in candidates if path.exists()]
    if not existing:
        return None
    return datetime.fromtimestamp(max(path.stat().st_mtime for path in existing), timezone.utc)


def parse_exit_code(run_dir: Path) -> int | None:
    path = run_dir / "exit_code"
    if not path.exists():
        return None
    try:
        return int(path.read_text(encoding="utf-8", errors="replace").strip())
    except ValueError:
        return None


def digest_worker_run(run_dir: Path, since: datetime | None, until: datetime | None, cwd_prefix: str | None) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    meta_path = run_dir / "meta.json"
    if not meta_path.exists():
        return None, []
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8", errors="replace"))
    except json.JSONDecodeError:
        return None, []
    worktree = meta.get("worktree")
    if not path_under(str(worktree) if worktree else None, cwd_prefix):
        return None, []
    started = parse_time(meta.get("started_utc"))
    ended = worker_end_time(run_dir)
    if started and until and started >= until:
        return None, []
    if ended and since and ended < since:
        return None, []
    window_started = max([value for value in (started, since) if value is not None], default=started)
    window_ended = min([value for value in (ended, until) if value is not None], default=ended)

    command_stats = CommandStats()
    patch_stats = PatchStats()
    turns = 0
    thread_id = None
    token_usage: dict[str, int] = {}
    timeline: list[dict[str, Any]] = []
    events_path = run_dir / "events.jsonl"
    if events_path.exists():
        with events_path.open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                typ = event.get("type")
                item = event.get("item") if isinstance(event.get("item"), dict) else {}
                if typ == "thread.started":
                    thread_id = event.get("thread_id")
                elif typ == "turn.started":
                    turns += 1
                elif typ == "item.completed" and item.get("type") == "command_execution":
                    command = item.get("command")
                    command_stats.add(
                        format_time(started) or "",
                        command,
                        item.get("exit_code"),
                        item.get("status"),
                        None,
                        str(worktree) if worktree else None,
                    )
                    category = classify_notable(command)
                    if category:
                        add_timeline_event(
                            timeline,
                            timestamp=format_time(started) or "",
                            source_id=f"{meta.get('agent', run_dir.parent.parent.name)}/{run_dir.name}",
                            source_role="worker",
                            category=category,
                            command=command,
                            exit_code=item.get("exit_code"),
                            duration=None,
                            cwd=str(worktree) if worktree else None,
                        )
                elif typ == "item.completed" and item.get("type") == "file_change":
                    patch_stats.add(True, "completed")
                elif typ == "item.completed" and item.get("type") == "error":
                    patch_stats.add(False, "failed")
                elif typ == "turn.completed":
                    usage = event.get("usage")
                    if isinstance(usage, dict):
                        token_usage = {
                            key: value
                            for key, raw in usage.items()
                            if (value := safe_int(raw)) is not None
                        }

    exit_code = parse_exit_code(run_dir)
    runtime = None
    if window_started and window_ended:
        runtime = max(0.0, (window_ended - window_started).total_seconds())
    return {
        "id": f"{meta.get('agent', run_dir.parent.parent.name)}/{run_dir.name}",
        "role": "worker",
        "path": str(run_dir),
        "agent": meta.get("agent"),
        "thread_id": thread_id,
        "branch": meta.get("branch"),
        "base": meta.get("base"),
        "cwd": worktree,
        "start": format_time(started),
        "end": format_time(ended),
        "window_start": format_time(window_started),
        "window_end": format_time(window_ended),
        "runtime_seconds": round(runtime, 3) if runtime is not None else None,
        "outcome": "running" if exit_code is None else ("success" if exit_code == 0 else "failed"),
        "exit_code": exit_code,
        "brief_present": (run_dir / "brief.md").exists(),
        "turns": turns,
        "exec_commands": command_stats.as_dict(),
        "patch_applies": patch_stats.as_dict(),
        "context_compacted": {"count": 0, "timestamps": []},
        "subagent_spawns": [],
        "tokens": token_usage,
    }, timeline


def collect_totals(sessions: list[dict[str, Any]], workers: list[dict[str, Any]]) -> dict[str, Any]:
    by_role: dict[str, dict[str, Any]] = {}
    for session in sessions:
        role = session["role"]
        role_totals = by_role.setdefault(
            role,
            {
                "sessions": 0,
                "turns": 0,
                "task_duration_seconds": 0.0,
                "exec_commands": 0,
                "failed_commands_by_exit_code": {},
                "patch_applies": {"ok": 0, "failed": 0},
                "context_compactions": 0,
                "peak_context_tokens": 0,
                "peak_context_source": None,
                "total_token_delta_in_window": 0,
            },
        )
        role_totals["sessions"] += 1
        role_totals["turns"] += session["turns"]
        role_totals["task_duration_seconds"] += session["task_duration_seconds"]
        exec_stats = session["exec_commands"]
        role_totals["exec_commands"] += exec_stats["count"]
        for code, count in exec_stats["failures_by_exit_code"].items():
            failures = role_totals["failed_commands_by_exit_code"]
            failures[code] = failures.get(code, 0) + count
        role_totals["patch_applies"]["ok"] += session["patch_applies"]["ok"]
        role_totals["patch_applies"]["failed"] += session["patch_applies"]["failed"]
        role_totals["context_compactions"] += session["context_compacted"]["count"]
        tokens = session["tokens"]
        if tokens["peak_context_tokens"] > role_totals["peak_context_tokens"]:
            role_totals["peak_context_tokens"] = tokens["peak_context_tokens"]
            role_totals["peak_context_source"] = session["id"]
        role_totals["total_token_delta_in_window"] += tokens["total_token_delta_in_window"] or 0
    for role_totals in by_role.values():
        role_totals["task_duration_seconds"] = round(role_totals["task_duration_seconds"], 3)
        role_totals["failed_commands_by_exit_code"] = dict(sorted(role_totals["failed_commands_by_exit_code"].items()))
    interactive_roles = {"TRAE", "TRAE-thread"}
    interactive_sessions = [session for session in sessions if session["role"] in interactive_roles]
    interactive_role_totals = [by_role[role] for role in interactive_roles if role in by_role]
    worker_runtime = sum(run["runtime_seconds"] or 0 for run in workers)
    worker_outcomes: dict[str, int] = {}
    for run in workers:
        outcome = run["outcome"]
        worker_outcomes[outcome] = worker_outcomes.get(outcome, 0) + 1
    return {
        "sessions": len(sessions),
        "sessions_by_role": dict(sorted((role, totals["sessions"]) for role, totals in by_role.items())),
        "session_totals_by_role": dict(sorted(by_role.items())),
        "interactive_trae_sessions": len(interactive_sessions),
        "interactive_trae_turns": sum(totals["turns"] for totals in interactive_role_totals),
        "interactive_trae_task_duration_seconds": round(sum(totals["task_duration_seconds"] for totals in interactive_role_totals), 3),
        "interactive_trae_exec_commands": sum(totals["exec_commands"] for totals in interactive_role_totals),
        "interactive_trae_failed_commands": sum(
            sum(totals["failed_commands_by_exit_code"].values()) for totals in interactive_role_totals
        ),
        "interactive_trae_patch_failures": sum(totals["patch_applies"]["failed"] for totals in interactive_role_totals),
        "interactive_trae_context_compactions": sum(totals["context_compactions"] for totals in interactive_role_totals),
        "interactive_trae_peak_context_tokens": max(
            (totals["peak_context_tokens"] for totals in interactive_role_totals),
            default=0,
        ),
        "interactive_trae_total_token_delta_in_window": sum(
            totals["total_token_delta_in_window"] for totals in interactive_role_totals
        ),
        "workers": len(workers),
        "worker_briefs": sum(1 for run in workers if run["brief_present"]),
        "worker_runtime_seconds": round(worker_runtime, 3),
        "worker_outcomes": dict(sorted(worker_outcomes.items())),
    }


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="*", type=Path, help="Rollout .jsonl files or worker run directories.")
    parser.add_argument("--rollout", action="append", type=Path, default=[], help="Rollout JSONL file. May be repeated.")
    parser.add_argument("--rollout-list", action="append", type=Path, default=[], help="File containing rollout JSONL paths, one per line.")
    parser.add_argument("--worker-run", action="append", type=Path, default=[], help="Worker run directory. May be repeated.")
    parser.add_argument("--worker-run-list", action="append", type=Path, default=[], help="File containing worker run directories, one per line.")
    parser.add_argument("--since", required=True, help="Inclusive UTC start timestamp, e.g. 2026-09-29T00:00:00Z.")
    parser.add_argument("--until", required=True, help="Inclusive UTC end timestamp.")
    parser.add_argument("--cwd-prefix", required=True, help="Only include sessions/runs whose cwd is under this path.")
    parser.add_argument("--output", type=Path, default=Path("trace-digest.json"), help="Output JSON path.")
    parser.add_argument("--timeline-limit", type=int, default=0, help="Maximum notable events to emit; 0 means no limit.")
    return parser.parse_args(argv)


def split_paths(args: argparse.Namespace) -> tuple[list[Path], list[Path]]:
    rollouts = list(args.rollout)
    worker_runs = list(args.worker_run)
    for list_path in args.rollout_list:
        rollouts.extend(read_path_list(list_path))
    for list_path in args.worker_run_list:
        worker_runs.extend(read_path_list(list_path))
    for path in args.paths:
        if path.is_dir():
            worker_runs.append(path)
        elif path.suffix == ".jsonl":
            rollouts.append(path)
        else:
            raise SystemExit(f"cannot classify input path: {path}")
    return rollouts, worker_runs


def read_path_list(path: Path) -> list[Path]:
    result: list[Path] = []
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            result.append(Path(stripped))
    return result


def build_digest(
    rollouts: Iterable[Path],
    worker_runs: Iterable[Path],
    since: datetime,
    until: datetime,
    cwd_prefix: str,
    timeline_limit: int = 0,
) -> dict[str, Any]:
    sessions: list[dict[str, Any]] = []
    workers: list[dict[str, Any]] = []
    timeline: list[dict[str, Any]] = []
    skipped: list[str] = []

    for path in rollouts:
        result, events = digest_rollout(path, since, until, cwd_prefix)
        if result is None:
            skipped.append(str(path))
            continue
        sessions.append(result)
        timeline.extend(events)

    for run_dir in worker_runs:
        result, events = digest_worker_run(run_dir, since, until, cwd_prefix)
        if result is None:
            skipped.append(str(run_dir))
            continue
        workers.append(result)
        timeline.extend(events)

    timeline.sort(key=lambda item: (item.get("timestamp") or "", item.get("source_id") or ""))
    if timeline_limit > 0 and len(timeline) > timeline_limit:
        timeline = timeline[:timeline_limit]
    sessions.sort(key=lambda item: (item.get("window_start") or "", item["id"]))
    workers.sort(key=lambda item: (item.get("window_start") or "", item["id"]))
    return {
        "generated_at": format_time(datetime.now(timezone.utc)),
        "window": {
            "since": format_time(since),
            "until": format_time(until),
            "cwd_prefix": cwd_prefix,
        },
        "sessions": sessions,
        "worker_runs": workers,
        "timeline": timeline,
        "totals": collect_totals(sessions, workers),
        "skipped_inputs": skipped,
        "redaction": {
            "command_truncation_chars": COMMAND_LIMIT,
            "message_bodies_included": False,
            "stdout_stderr_included": False,
        },
    }


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    since = parse_time(args.since)
    until = parse_time(args.until)
    if since is None or until is None:
        print("--since and --until must be ISO timestamps", file=sys.stderr)
        return 2
    if since > until:
        print("--since must be before --until", file=sys.stderr)
        return 2
    rollouts, worker_runs = split_paths(args)
    digest = build_digest(rollouts, worker_runs, since, until, args.cwd_prefix, args.timeline_limit)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(digest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
