"""Durable execution of a Foundry build run.

A BuildSession owns stage processes, source-edit checks, progress, and terminal
results. The caller holds the estate lock and chooses commands and variants.
The report is the versioned JSON data contract consumed by status and release
verification; workflow-specific fields stay in that report, not a second model.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import time
import uuid

from build_inputs import digest
from build_progress import (
    Console,
    atomic_json,
    latest_pointer,
    latest_report,
    report_text,
    tail_lines,
    utc_now,
)


CANCEL_GRACE_SECONDS = 15.0


class StageFailure(RuntimeError):
    """A failed command; acquisition may try another declared mirror."""


def source_identity(repo_root: Path) -> str:
    """Detect source edits between stages, including uncommitted runner changes."""
    names = subprocess.check_output(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        cwd=repo_root,
    ).split(b"\0")
    result = hashlib.sha256()
    for raw in sorted(set(names) - {b""}):
        path = repo_root / os.fsdecode(raw)
        result.update(raw + b"\0")
        if path.is_symlink():
            result.update(b"link\0" + os.fsencode(os.readlink(path)))
        elif path.is_file():
            result.update(str(path.stat().st_mode & 0o777).encode() + b"\0")
            result.update(bytes.fromhex(digest(path)))
        else:
            result.update(b"missing")
    return result.hexdigest()


def planned_variants(
    estate: Path,
    output: Path,
    lines: list[str],
    profiles: list[str],
    targets: dict[str, list[str]],
) -> list[dict]:
    """Describe the complete requested matrix before the first build starts."""

    return [
        {
            "line": line,
            "profile": profile,
            "status": "pending",
            "targets": targets[profile],
            "build_events": str(output / f"{line}-{profile}-events.jsonl"),
            "output_base": str(
                estate / f"vaso/lines/{line}/cache/bazel/output-base-{profile}"
            ),
            "artifacts": [],
        }
        for line in lines
        for profile in profiles
    ]


def cache_start_state(
    estate: Path, lines: list[str], profiles: list[str]
) -> dict[str, object]:
    """Describe selected native cache state before materialization or preparation."""

    paths = {
        estate / f"vaso/lines/{line}/cache/bazel/output-base-{profile}"
        for line in lines
        for profile in profiles
    }
    paths.update(estate / f"vaso/lines/{line}/cache/bazel/disk-cache" for line in lines)
    selected = sorted(paths)
    populated = []
    for path in selected:
        try:
            next(path.iterdir())
        except (FileNotFoundError, NotADirectoryError, StopIteration):
            continue
        populated.append(path)
    return {
        "mode": "warm" if populated else "cold",
        "selected_paths": [str(path) for path in selected],
        "populated_paths": [str(path) for path in populated],
    }


def abandon_stale_run(state_root: Path) -> dict | None:
    """Close a running record after its estate lock has become available."""

    loaded = latest_report(state_root)
    if loaded is None:
        return None
    report, summary = loaded
    if report.get("status") != "running":
        return None
    try:
        summary.parent.relative_to(state_root.resolve())
    except ValueError as exc:
        raise RuntimeError(
            f"refusing stale summary outside state root: {summary}"
        ) from exc
    now = utc_now()
    reason = "previous runner stopped without recording a terminal state"
    for stage in report.get("stages", []):
        if isinstance(stage, dict) and stage.get("status") == "running":
            stage.update(status="abandoned", finished_at=now)
            if stage.get("log"):
                stage["tail"] = tail_lines(Path(stage["log"]), count=8)
    for variant in report.get("variants", []):
        if isinstance(variant, dict) and variant.get("status") == "running":
            variant.update(status="abandoned", finished_at=now)
    report.update(
        status="abandoned",
        phase="complete",
        current_stage=None,
        finished_at=now,
        updated_at=now,
        error=reason,
    )
    report.setdefault("summary_path", str(summary))
    report.setdefault("events_path", str(summary.parent / "events.jsonl"))
    report.setdefault("result_path", str(summary.parent / "result.txt"))
    events = Path(report["events_path"])
    events.parent.mkdir(parents=True, exist_ok=True)
    with events.open("a", encoding="utf-8") as handle:
        handle.write(
            json.dumps({"at": now, "event": "run_abandoned", "reason": reason}) + "\n"
        )
        handle.flush()
        os.fsync(handle.fileno())
    atomic_json(summary, report)
    Path(report["result_path"]).write_text(report_text(report), encoding="utf-8")
    atomic_json(state_root / "latest.json", latest_pointer(report))
    return report


class BuildSession:
    def __init__(
        self,
        repo_root: Path,
        estate: Path,
        lines: list[str],
        profiles: list[str],
        targets: dict[str, list[str]],
        *,
        status_interval: float = 30.0,
        console: Console | None = None,
        lock_fd: int | None = None,
    ):
        self.root, self.estate = repo_root, estate
        self.lines, self.profiles = lines, profiles
        self.status_interval = status_interval
        run_id = (
            datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            + "-"
            + uuid.uuid4().hex[:8]
        )
        self.state_root = self.estate / "agents/build-variants"
        self.output = self.state_root / run_id
        self.output.mkdir(parents=True)
        self.console = console if console is not None else Console()
        self.lock_fd = lock_fd
        self.started_monotonic = time.monotonic()
        self.event_sequence = 0
        self.identity = source_identity(self.root)
        summary = self.output / "summary.json"
        self.report = {
            "schema_version": 2,
            "run_id": run_id,
            "status": "running",
            "phase": "starting",
            "pid": os.getpid(),
            "started_at": utc_now(),
            "updated_at": utc_now(),
            "duration_seconds": 0.0,
            "lines": self.lines,
            "profiles": self.profiles,
            "source_identity": self.identity,
            "estate_root": str(self.estate),
            "summary_path": str(summary),
            "events_path": str(self.output / "events.jsonl"),
            "result_path": str(self.output / "result.txt"),
            "console_mode": self.console.mode,
            "cache_start": cache_start_state(self.estate, self.lines, self.profiles),
            "current_stage": None,
            "stages": [],
            "variants": planned_variants(
                self.estate, self.output, self.lines, self.profiles, targets
            ),
            "qualification": "not performed",
        }
        self.event("run_started", lines=self.lines, profiles=self.profiles)
        self.save()

    def event(self, name: str, **fields):
        self.event_sequence += 1
        record = {
            "sequence": self.event_sequence,
            "at": utc_now(),
            "event": name,
            **fields,
        }
        path = Path(self.report["events_path"])
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())

    def save(self):
        self.report["updated_at"] = utc_now()
        if "finished_at" not in self.report:
            self.report["duration_seconds"] = round(
                max(0.0, time.monotonic() - self.started_monotonic), 3
            )
        atomic_json(Path(self.report["summary_path"]), self.report)
        atomic_json(self.state_root / "latest.json", latest_pointer(self.report))

    def set_phase(self, phase: str):
        if self.report.get("phase") == phase:
            return
        self.report["phase"] = phase
        self.event("phase_changed", phase=phase)
        self.save()
        self.console.phase(phase)

    def finish(self, status: str, error: str | None = None):
        now = utc_now()
        if status == "prepared":
            for variant in self.report["variants"]:
                if variant["status"] == "pending":
                    variant["status"] = "skipped"
        elif status in {"failed", "interrupted"}:
            for variant in self.report["variants"]:
                if variant["status"] == "running":
                    variant["status"] = status
                    variant["finished_at"] = now
        self.report.update(
            status=status,
            phase="complete",
            current_stage=None,
            finished_at=now,
            duration_seconds=round(
                max(0.0, time.monotonic() - self.started_monotonic), 3
            ),
        )
        if error:
            self.report["error"] = error
        self.event("run_finished", status=status, error=error)
        self.save()
        Path(self.report["result_path"]).write_text(
            report_text(self.report), encoding="utf-8"
        )

    def check_source(self):
        if source_identity(self.root) != self.identity:
            raise RuntimeError(
                "checkout changed during execution; stop and restart with a fixed checkout"
            )

    def stage(self, name: str, command: list[str], line: str, profile: str):
        self.check_source()
        log = self.output / f"{len(self.report['stages']) + 1:03d}-{name}.log"
        env = dict(
            os.environ,
            VASO_ESTATE_ROOT=str(self.estate),
            VASO_CUDA_LINE=line,
            VASO_PROFILE=profile,
            VASO_AGENT="build-variants",
            VASO_GPUS="0",
        )
        # Each variant owns outputs; retain constant sandbox paths and canonical flags.
        env["VASO_BAZEL_OB"] = str(
            self.estate / f"vaso/lines/{line}/cache/bazel/output-base-{profile}"
        )
        env["VASO_BAZEL_DISK_CACHE"] = str(
            self.estate / f"vaso/lines/{line}/cache/bazel/disk-cache"
        )
        started_at = utc_now()
        started_monotonic = time.monotonic()
        entry = {
            "name": name,
            "line": line,
            "profile": profile,
            "log": str(log),
            "status": "running",
            "started_at": started_at,
            "duration_seconds": 0.0,
            "log_bytes": 0,
        }
        self.report["stages"].append(entry)
        self.report["current_stage"] = name
        self.event(
            "stage_started",
            stage=name,
            line=line,
            profile=profile,
            log=str(log),
        )
        self.save()
        self.console.stage_started(entry)
        interrupted = False
        try:
            rc, interrupted = self._execute(command, env, log, entry, started_monotonic)
        except OSError as exc:
            entry.update(status="failed", error=str(exc))
            rc = getattr(exc, "errno", 1) or 1
        status = (
            "interrupted"
            if interrupted or rc < 0
            else "passed"
            if rc == 0
            else "failed"
        )
        tail = tail_lines(log, count=1 if status == "passed" else 8)
        entry.update(
            returncode=rc,
            status=status,
            finished_at=utc_now(),
            duration_seconds=round(max(0.0, time.monotonic() - started_monotonic), 3),
            log_bytes=log.stat().st_size if log.exists() else 0,
            tail=tail,
        )
        if tail:
            entry["progress"] = tail[-1]
        self.report["current_stage"] = None
        self.event(
            "stage_finished",
            stage=name,
            status=status,
            returncode=rc,
            duration_seconds=entry["duration_seconds"],
            log=str(log),
        )
        self.save()
        self.console.stage_finished(entry)
        if status == "interrupted":
            raise InterruptedError(f"{name} interrupted; see {log}")
        if rc:
            raise StageFailure(f"{name} failed (exit {rc}); see {log}")
        self.check_source()

    def _execute(self, command, env, log, entry, started_monotonic):
        """Drain a command, with bounded cancellation and cleanup on runner errors."""
        with (
            log.open("wb") as handle,
            subprocess.Popen(
                command,
                cwd=self.root,
                env=env,
                stdout=handle,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                start_new_session=True,
                pass_fds=(self.lock_fd,) if self.lock_fd is not None else (),
            ) as proc,
        ):
            previous = {}
            cancel_deadline = None

            def signal_group(signum):
                try:
                    os.killpg(proc.pid, signum)
                except ProcessLookupError:
                    pass

            def forward(signum, _frame):
                nonlocal cancel_deadline
                if cancel_deadline is None:
                    cancel_deadline = time.monotonic() + CANCEL_GRACE_SECONDS
                signal_group(signum)

            try:
                for signum in (signal.SIGINT, signal.SIGTERM):
                    previous[signum] = signal.signal(signum, forward)
                entry["pid"] = proc.pid
                self.save()
                interval = max(0.1, float(self.status_interval))
                next_update = time.monotonic() + interval
                while True:
                    try:
                        rc = proc.wait(timeout=min(1.0, interval))
                        return rc, cancel_deadline is not None
                    except subprocess.TimeoutExpired:
                        pass
                    now = time.monotonic()
                    if cancel_deadline is not None and now >= cancel_deadline:
                        signal_group(signal.SIGKILL)
                    if now < next_update:
                        continue
                    progress = tail_lines(log, count=1)
                    entry.update(
                        duration_seconds=round(max(0.0, now - started_monotonic), 3),
                        log_bytes=log.stat().st_size if log.exists() else 0,
                        progress=progress[-1] if progress else "working",
                    )
                    self.save()
                    self.console.stage_progress(entry)
                    next_update = now + interval
            finally:
                # A reporting error must not leave a process running with the
                # inherited estate lock. Cancellation also stops descendants
                # that outlive their immediate parent.
                if proc.poll() is None or cancel_deadline is not None:
                    signal_group(signal.SIGKILL)
                proc.wait()
                for signum, handler in previous.items():
                    signal.signal(signum, handler)
