"""Exercise deadlines, exit propagation, and process-group cleanup with real children."""

import os
from pathlib import Path
import sys
import time

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from process_runner import run_command


def test_stdin_is_closed_and_nonzero_exit_is_retained(tmp_path):
    result = run_command(
        [
            sys.executable,
            "-c",
            'import sys; assert sys.stdin.read() == ""; print("retained"); sys.exit(19)',
        ],
        dict(os.environ),
        tmp_path / "stage.log",
        10,
    )
    assert result["status"] == "failed" and result["returncode"] == 19
    assert (tmp_path / "stage.log").read_text().strip() == "retained"


def test_deadline_stops_a_command_and_records_failure(tmp_path):
    result = run_command(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        dict(os.environ),
        tmp_path / "stage.log",
        0.05,
    )
    assert result["status"] == "failed" and result["returncode"] != 0
    assert "deadline" in result["error"]


def test_monitor_failure_stops_the_owned_process_group(tmp_path):
    observed = []
    child_pid_file = tmp_path / "child.pid"

    def reject(pid):
        observed.append(pid)
        if child_pid_file.exists():
            raise RuntimeError("resource unavailable")

    result = run_command(
        [
            sys.executable,
            "-c",
            "\n".join(
                [
                    "import subprocess, sys",
                    "from pathlib import Path",
                    "child = subprocess.Popen([sys.executable, '-c', 'import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(30)'])",
                    "Path(sys.argv[1]).write_text(str(child.pid))",
                    "child.wait()",
                ]
            ),
            str(child_pid_file),
        ],
        dict(os.environ),
        tmp_path / "stage.log",
        10,
        monitor=reject,
    )
    assert result["status"] == "failed" and result["error"] == "resource unavailable"
    assert observed and not Path(f"/proc/{observed[0]}").exists()
    child_stat = Path(f"/proc/{child_pid_file.read_text()}/stat")
    # SIGKILL delivery to an orphaned child is asynchronous; bound observation.
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        try:
            state = child_stat.read_text().rsplit(")", 1)[1].split()[0]
        except FileNotFoundError:
            return
        if state == "Z":
            return
        time.sleep(0.01)
    raise AssertionError("owned child survived process-group cleanup")


@pytest.mark.parametrize("problem", ["missing", "not-executable"])
def test_launch_failure_returns_a_complete_failed_stage(tmp_path, problem):
    executable = tmp_path / "command"
    if problem == "not-executable":
        executable.write_text("#!/bin/sh\nexit 0\n")
        executable.chmod(0o600)
    log = tmp_path / "stage.log"

    result = run_command([str(executable)], dict(os.environ), log, 10)

    assert result["status"] == "failed"
    assert result["returncode"] is None  # No child executed.
    assert result["command"] == [str(executable)]
    assert result["started_at"] <= result["finished_at"]
    assert str(executable) in result["error"]
    assert result["error"] in log.read_text()


def test_existing_log_is_preserved_and_prevents_execution(tmp_path):
    log = tmp_path / "stage.log"
    log.write_text("previous attempt\n")
    marker = tmp_path / "started"

    with pytest.raises(FileExistsError):
        run_command(
            [
                sys.executable,
                "-c",
                "import sys; from pathlib import Path; Path(sys.argv[1]).touch()",
                str(marker),
            ],
            dict(os.environ),
            log,
            10,
        )

    assert log.read_text() == "previous attempt\n"
    assert not marker.exists()
