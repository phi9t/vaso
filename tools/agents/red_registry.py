"""Track standing-verification failures and shared/stale escalation locally.

The verifier owns stage execution and log interpretation. This module owns the
locked registry update, retained first-seen time, and optional attention log.
"""

from __future__ import annotations

import datetime as dt
import fcntl
import hashlib
import json
import math
import os
import re
import sys
import tempfile
import time
from pathlib import Path


def stamp(epoch: float) -> str:
    return dt.datetime.fromtimestamp(epoch, dt.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


def safe_name(value: str) -> str:
    base = re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("_") or "red"
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:8]
    return f"{base}-{digest}.json"


def read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"{path}: invalid JSON at line {exc.lineno}, column {exc.colno}"
        ) from exc
    except UnicodeError as exc:
        raise ValueError(f"{path}: registry record must be UTF-8") from exc
    if (
        not isinstance(value, dict)
        or type(value.get("schema_version")) is not int
        or value["schema_version"] != 1
    ):
        raise ValueError(f"{path}: expected a registry object with schema_version=1")
    for field in ("target", "stage", "sha", "first_seen", "last_seen"):
        if not isinstance(value.get(field), str) or not value[field]:
            raise ValueError(f"{path}: {field} must be a nonempty string")
    agents = value.get("agents")
    if (
        not isinstance(agents, list)
        or not agents
        or any(not isinstance(agent, str) or not agent for agent in agents)
    ):
        raise ValueError(f"{path}: agents must be a nonempty list of names")
    for field in ("first_seen_epoch", "last_seen_epoch"):
        epoch = value.get(field)
        try:
            valid = type(epoch) in (int, float) and math.isfinite(epoch) and epoch >= 0
        except OverflowError:
            valid = False
        if not valid:
            raise ValueError(f"{path}: {field} must be a finite nonnegative number")
    for field in ("shared_escalated", "stale_escalated"):
        if type(value.get(field)) is not bool:
            raise ValueError(f"{path}: {field} must be boolean")
    return value


def write_json(path: Path, value: dict) -> None:
    """Publish a flushed replacement and remove partial files on failure."""
    data = json.dumps(value, indent=2, sort_keys=True) + "\n"
    with tempfile.TemporaryDirectory(
        dir=path.parent, prefix=f".{path.name}."
    ) as directory:
        temporary = Path(directory) / "record.json"
        with temporary.open("w", encoding="utf-8") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)


def main(argv: list[str] | None = None) -> int:
    action, red_root_s, attention_log_s, agent, sha, stage, target = (
        sys.argv[1:] if argv is None else argv
    )
    red_root = Path(red_root_s)
    attention_log = Path(attention_log_s)
    if action == "clear-stage" and not red_root.exists():
        return 0
    red_root.mkdir(parents=True, exist_ok=True)
    lock_path = red_root / ".lock"

    with lock_path.open("a", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        now = time.time()
        now_s = stamp(now)
        try:
            if action == "record":
                path = red_root / safe_name(target)
                existing = read_json(path)
                previous_agents = existing.get("agents", [])
                agents = list(previous_agents)
                if agent not in agents:
                    agents.append(agent)
                first_epoch = float(existing.get("first_seen_epoch", now))
                age = max(0, int(now - first_epoch))
                second_distinct = (
                    agent not in previous_agents and len(previous_agents) >= 1
                )
                stale = age >= 3600 and not existing.get("stale_escalated")
                shared = second_distinct and not existing.get("shared_escalated")
                doc = {
                    "schema_version": 1,
                    "target": target,
                    "stage": stage,
                    "agents": agents,
                    "first_seen": existing.get("first_seen", now_s),
                    "first_seen_epoch": first_epoch,
                    "last_seen": now_s,
                    "last_seen_epoch": now,
                    "sha": sha,
                    "shared_escalated": bool(existing.get("shared_escalated"))
                    or shared,
                    "stale_escalated": bool(existing.get("stale_escalated")) or stale,
                }
                write_json(path, doc)
                if shared or stale:
                    attention_log.parent.mkdir(parents=True, exist_ok=True)
                    with attention_log.open("a", encoding="utf-8") as out:
                        out.write(
                            f"{now_s} SHARED_RED {target} agents={','.join(agents)} age={age}s\n"
                        )
            elif action == "clear-stage":
                # Validate the whole selection before clearing any history.
                records = [
                    (path, read_json(path)) for path in sorted(red_root.glob("*.json"))
                ]
                for path, doc in records:
                    if doc.get("stage") == stage:
                        path.unlink(missing_ok=True)
            else:
                raise SystemExit(f"unknown red registry action: {action}")
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError) as exc:
        print(f"red registry: {exc}", file=sys.stderr)
        raise SystemExit(2)
