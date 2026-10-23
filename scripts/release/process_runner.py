"""Run one unattended command with a deadline, retained log, and owned cleanup.

An optional monitor receives the owned process-group leader PID. Raising stops
that group and records failure. This keeps resource policy in the caller while
sharing cancellation, stdin, logging, and deadline behavior across workflows.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from datetime import datetime, timezone
import math
import os
from pathlib import Path
import signal
import subprocess
import time


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def interrupt_on_sigterm(message: str) -> Iterator[None]:
    """Make termination unwind an attempt, then restore the caller's handler."""

    def interrupted(_signum, _frame):
        raise KeyboardInterrupt(message)

    previous = signal.signal(signal.SIGTERM, interrupted)
    try:
        yield
    finally:
        signal.signal(signal.SIGTERM, previous)


def _stop_group(process: subprocess.Popen) -> None:
    # The group belongs to this invocation even if its leader just exited.
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        process.wait(timeout=15)
    except subprocess.TimeoutExpired:
        pass
    finally:
        # Terminate remaining children, including ones that ignored SIGTERM.
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait()


def run_command(
    command: Sequence[str],
    env: Mapping[str, str],
    log: Path,
    timeout: float,
    *,
    monitor: Callable[[int], None] | None = None,
) -> dict:
    """Return a terminal stage record once a new log has been created.

    Launch failures have no child exit code (``returncode`` is None). Invalid
    deadlines and log-creation failures raise before execution; existing logs
    are never replaced.
    """
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("command timeout must be finite and positive")
    record = {"started_at": now(), "command": list(command), "status": "running"}
    with log.open("xb") as stream:
        process = None
        try:
            process = subprocess.Popen(
                command,
                stdin=subprocess.DEVNULL,
                stdout=stream,
                stderr=subprocess.STDOUT,
                env=env,
                start_new_session=True,
            )
            started = heartbeat = time.monotonic()
            while True:
                if monitor:
                    monitor(process.pid)
                remaining = timeout - (time.monotonic() - started)
                if remaining <= 0:
                    raise TimeoutError("stage deadline exceeded")
                if time.monotonic() - heartbeat >= 30:
                    print(
                        f"stage running: {int(time.monotonic() - started)}s; log={log}",
                        flush=True,
                    )
                    heartbeat = time.monotonic()
                if process.poll() is not None:
                    break
                try:
                    process.wait(timeout=min(2, remaining))
                except subprocess.TimeoutExpired:
                    pass
            record.update(
                returncode=process.returncode,
                status="passed" if process.returncode == 0 else "failed",
            )
            if process.returncode:
                _stop_group(process)
        except (Exception, KeyboardInterrupt) as exc:
            if process is not None:
                _stop_group(process)
            record.update(
                status="failed",
                error=str(exc),
                returncode=process.returncode if process is not None else None,
            )
            stream.write(f"\nstage failed: {exc}\n".encode("utf-8", errors="replace"))
    record["finished_at"] = now()
    return record
