"""Exercise deadlines, exit propagation, and process-group cleanup with real children."""

import os
from pathlib import Path
import sys
import time

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
