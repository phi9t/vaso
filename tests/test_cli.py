import os
import subprocess
import sys

from vaso.cli import main


def test_main_help_returns_zero(capsys):
    assert main(["--help"]) == 0
    out = capsys.readouterr().out
    assert "vaso" in out
    assert "doctor" in out
    assert "validate" in out


def test_unknown_command_returns_two(capsys):
    assert main(["missing-command"]) == 2
    err = capsys.readouterr().err
    assert "invalid choice" in err


def test_module_entrypoint_runs_help_from_checkout():
    env = {**os.environ, "PYTHONPATH": "src"}
    result = subprocess.run(
        [sys.executable, "-m", "vaso.cli", "--help"],
        check=False,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert result.returncode == 0
    assert "doctor" in result.stdout
