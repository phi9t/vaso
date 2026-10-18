"""scripts/agents/worker-wait.sh: the zero-token light supervisor."""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "agents" / "worker-wait.sh"


def make_run(root: Path, agent: str) -> Path:
    run = root / "agents" / agent / "runs" / "20260929T000000Z"
    run.mkdir(parents=True)
    (run / "events.jsonl").write_text("{}\n")
    return run


def wait(*args: str, timeout: float = 20) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["bash", str(SCRIPT), "--interval", "0.1", *args],
                          capture_output=True, text=True, timeout=timeout)


def test_reports_the_first_run_to_exit(tmp_path: Path) -> None:
    a, b = make_run(tmp_path, "wa"), make_run(tmp_path, "wb")
    (b / "exit_code").write_text("0\n")
    out = wait(str(a), str(b)).stdout
    assert out.startswith("EXITED wb rc=0")


def test_reports_a_stalled_run(tmp_path: Path) -> None:
    run = make_run(tmp_path, "wa")
    old = time.time() - 120
    os.utime(run / "events.jsonl", (old, old))
    assert wait("--stall-seconds", "60", str(run)).stdout.startswith("STALLED wa ")


def test_wakes_on_new_loop_attention_but_not_on_old_or_routine_lines(tmp_path: Path) -> None:
    run = make_run(tmp_path, "wa")
    log = tmp_path / "autoland.log"
    log.write_text("2026-09-29T00:00:00Z RED old failure\n")
    proc = subprocess.Popen(["bash", str(SCRIPT), "--interval", "0.1", "--loop-log", str(log), str(run)],
                            stdout=subprocess.PIPE, text=True)
    time.sleep(0.5)
    with log.open("a") as fh:
        fh.write("2026-09-29T00:01:00Z LANDED x -> y\n")
    time.sleep(0.5)
    assert proc.poll() is None
    with log.open("a") as fh:
        fh.write("2026-09-29T00:02:00Z STALL no target move\n")
    out, _ = proc.communicate(timeout=10)
    assert out.startswith("LOOP 2026-09-29T00:02:00Z STALL")


def test_missing_run_dir_is_an_error(tmp_path: Path) -> None:
    assert wait(str(tmp_path / "nope")).returncode == 2
