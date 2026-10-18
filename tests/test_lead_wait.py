"""Behavioral tests for scripts/agents/lead-wait.sh."""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "agents" / "lead-wait.sh"


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def env_for(tmp_path: Path, log: Path) -> dict[str, str]:
    return {
        **os.environ,
        "VASO_ESTATE_ROOT": str(tmp_path / "estate"),
        "LOG": str(log),
        "AGENT": "claude",
        "FOLLOWER_PID": "",
        "FOLLOWER_PANE": "none",
    }


def run_wait(tmp_path: Path, log: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(SCRIPT), "--log", str(log), "--max-seconds", "1", "--interval", "0.05", *args],
        cwd=ROOT,
        env=env_for(tmp_path, log),
        capture_output=True,
        text=True,
        check=False,
    )


def test_lead_wait_ignores_non_attention_and_exits_on_attention(tmp_path: Path) -> None:
    log = tmp_path / "autoland.log"
    write(
        log,
        "\n".join(
            [
                "2026-09-29T12:00:00Z START target=a landing=a",
                "2026-09-29T12:01:00Z MOVED a -> b: work",
                "2026-09-29T12:02:00Z LANDED landing -> b",
                "2026-09-29T12:03:00Z RED VERIFY_FAILED tests",
            ]
        )
        + "\n",
    )
    offset = tmp_path / "offset"

    result = run_wait(tmp_path, log, "--from-start", "--offset-file", str(offset))

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "2026-09-29T12:03:00Z RED VERIFY_FAILED tests"
    assert int(offset.read_text(encoding="utf-8")) == log.stat().st_size


def test_lead_wait_rearm_does_not_refire_old_attention(tmp_path: Path) -> None:
    log = tmp_path / "autoland.log"
    write(log, "2026-09-29T12:03:00Z RED first\n")
    offset = tmp_path / "offset"

    first = run_wait(tmp_path, log, "--from-start", "--offset-file", str(offset))
    second = run_wait(tmp_path, log, "--offset-file", str(offset))

    assert first.returncode == 0, first.stderr
    assert second.returncode == 3, second.stdout
    assert "QUIET" in second.stdout


def test_lead_wait_exits_on_each_attention_kind(tmp_path: Path) -> None:
    kinds = ["RED", "REGRESSION", "STALL", "REVIEW", "GONE", "IO", "REWRITTEN"]
    for kind in kinds:
        log = tmp_path / f"{kind}.log"
        write(log, f"2026-09-29T12:00:00Z {kind} detail\n")
        result = run_wait(tmp_path, log, "--from-start", "--offset-file", str(tmp_path / f"{kind}.offset"))
        assert result.returncode == 0, result.stderr
        assert f" {kind} " in result.stdout


def test_lead_wait_does_not_wake_on_backlog(tmp_path: Path) -> None:
    # BACKLOG is for the human's cockpit; a chronic backlog must not wake the lead.
    log = tmp_path / "backlog.log"
    write(log, "2026-09-29T12:00:00Z BACKLOG 20 commits, oldest 90 min\n")
    result = run_wait(tmp_path, log, "--from-start", "--offset-file", str(tmp_path / "backlog.offset"))
    assert result.returncode == 3 and "BACKLOG" not in result.stdout


def test_lead_wait_does_not_wake_on_uncommitted_age(tmp_path: Path) -> None:
    log = tmp_path / "uncommitted.log"
    write(log, "2026-09-29T12:00:00Z UNCOMMITTED 2 files, oldest 190 min\n")
    result = run_wait(
        tmp_path,
        log,
        "--from-start",
        "--offset-file",
        str(tmp_path / "uncommitted.offset"),
    )
    assert result.returncode == 3 and "UNCOMMITTED" not in result.stdout


def test_lead_wait_defaults_to_end_when_no_offset_then_wakes_on_new_line(tmp_path: Path) -> None:
    log = tmp_path / "autoland.log"
    write(log, "2026-09-29T12:03:00Z RED old\n")
    offset = tmp_path / "offset"

    proc = subprocess.Popen(
        [
            "bash",
            str(SCRIPT),
            "--log",
            str(log),
            "--max-seconds",
            "3",
            "--interval",
            "0.05",
            "--offset-file",
            str(offset),
        ],
        cwd=ROOT,
        env=env_for(tmp_path, log),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    # Wait until the script has recorded its starting offset (end of file);
    # a fixed sleep raced with slow startup on a loaded host.
    deadline = time.monotonic() + 5
    while not offset.exists() and time.monotonic() < deadline:
        time.sleep(0.02)
    with log.open("a", encoding="utf-8") as handle:
        handle.write("2026-09-29T12:04:00Z LANDED landing -> b\n")
        handle.write("2026-09-29T12:05:00Z STALL no activity\n")
    out, err = proc.communicate(timeout=5)

    assert proc.returncode == 0, err
    assert out.strip() == "2026-09-29T12:05:00Z STALL no activity"
