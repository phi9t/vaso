"""Execution ledgers reject invalid stages before publishing run results."""

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

SCRIPT = Path(__file__).with_name("run_result.py")


def finalize(tmp_path):
    return [
        sys.executable,
        str(SCRIPT),
        "finalize",
        "--out",
        str(tmp_path / "result.json"),
        "--stages-jsonl",
        str(tmp_path / "stages.jsonl"),
        "--mode",
        "gate",
        "--line",
        "cu129",
        "--agent",
        "fixture",
        "--args-json",
        "[]",
        "--log-path",
        str(tmp_path),
        "--commit",
        "revision",
        "--exit-code",
        "0",
        "--started-utc",
        "2026-01-01T00:00:00Z",
    ]


@pytest.mark.parametrize(
    "record",
    [
        "invalid JSON",
        [],
        {},
        {"name": "stage", "exit_code": True, "duration_seconds": 1, "log_path": "/log"},
        {"name": "stage", "exit_code": "1", "duration_seconds": 1, "log_path": "/log"},
        {
            "name": "stage",
            "exit_code": 0,
            "duration_seconds": float("inf"),
            "log_path": "/log",
        },
        {"name": "stage", "exit_code": 0, "duration_seconds": -1, "log_path": "/log"},
    ],
    ids=[
        "json",
        "list",
        "missing-exit",
        "boolean-exit",
        "string-exit",
        "infinite-duration",
        "negative-duration",
    ],
)
def test_invalid_stage_preserves_ledger_and_previous_result(tmp_path, record):
    ledger = tmp_path / "stages.jsonl"
    value = record if isinstance(record, str) else json.dumps(record)
    ledger.write_text("\n" + value + "\n")
    previous = tmp_path / "result.json"
    previous.write_bytes(b"previous result\n")
    result = subprocess.run(
        finalize(tmp_path),
        env={**os.environ, "VASO_ESTATE_ROOT": str(tmp_path)},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2
    assert f"{ledger}:2" in result.stderr
    assert "Traceback" not in result.stderr
    assert ledger.read_text() == "\n" + value + "\n"
    assert previous.read_bytes() == b"previous result\n"
    assert not (tmp_path / "result.json.tmp").exists()


@pytest.mark.parametrize("duration", ["nan", "inf", "-inf", "-0.0001"])
def test_invalid_duration_does_not_create_a_ledger(tmp_path, duration):
    ledger = tmp_path / "new/stages.jsonl"
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "stage",
            "--stages-jsonl",
            str(ledger),
            "--name",
            "stage",
            "--exit-code",
            "0",
            "--duration-seconds=" + duration,
            "--log-path",
            str(tmp_path / "log"),
        ],
        env={**os.environ, "VASO_ESTATE_ROOT": str(tmp_path)},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2
    assert "duration" in result.stderr
    assert "Traceback" not in result.stderr
    assert not ledger.parent.exists()


def test_valid_ledger_round_trip_preserves_stage_failure(tmp_path):
    env = {**os.environ, "VASO_ESTATE_ROOT": str(tmp_path)}
    ledger = tmp_path / "stages.jsonl"
    for name, code in (("prepare", 0), ("build", 17)):
        log = tmp_path / (name + ".log")
        log.write_text("ERROR: build stopped\n" if code else "ready\n")
        subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "stage",
                "--stages-jsonl",
                str(ledger),
                "--name",
                name,
                "--exit-code",
                str(code),
                "--duration-seconds",
                "1.2345",
                "--log-path",
                str(log),
            ],
            env=env,
            capture_output=True,
            text=True,
            check=True,
        )
    subprocess.run(
        finalize(tmp_path), env=env, capture_output=True, text=True, check=True
    )
    result = json.loads((tmp_path / "result.json").read_text())
    assert result["verdict"] == "failed"
    assert [stage["exit_code"] for stage in result["stages"]] == [0, 17]
    assert result["stages"][0]["duration_seconds"] == 1.234
    assert result["first_error_line"] == "ERROR: build stopped"


@pytest.mark.parametrize("existing", [False, True])
@pytest.mark.parametrize("failure", ["flush", "replace"])
def test_failed_result_publication_preserves_previous_bytes_and_can_retry(
    tmp_path, monkeypatch, capsys, existing, failure
):
    monkeypatch.syspath_prepend(str(SCRIPT.parent))
    import run_result

    output = tmp_path / "result.json"
    if existing:
        output.write_bytes(b"previous result\n")
    monkeypatch.setenv("VASO_ESTATE_ROOT", str(tmp_path))

    def fail(*args):
        raise OSError("result publication failed")

    with monkeypatch.context() as scoped:
        scoped.setattr(os, "fsync" if failure == "flush" else "replace", fail)
        assert run_result.main(finalize(tmp_path)[2:]) == 2
    if existing:
        assert output.read_bytes() == b"previous result\n"
    else:
        assert not output.exists()
    assert list(tmp_path.iterdir()) == ([output] if existing else [])
    assert "result publication failed" in capsys.readouterr().err
    assert run_result.main(finalize(tmp_path)[2:]) == 0
    assert json.loads(output.read_text())["verdict"] == "passed"
    assert list(tmp_path.iterdir()) == [output]


@pytest.mark.parametrize("failure", ["command", "stage"])
def test_pass_override_cannot_hide_a_recorded_failure(tmp_path, failure):
    ledger = tmp_path / "stages.jsonl"
    ledger.write_text(
        json.dumps(
            {
                "name": "build",
                "exit_code": 17 if failure == "stage" else 0,
                "duration_seconds": 1.0,
                "log_path": str(tmp_path / "build.log"),
            }
        )
        + "\n"
    )
    output = tmp_path / "result.json"
    output.write_bytes(b"previous result\n")
    command = finalize(tmp_path)
    if failure == "command":
        command[command.index("--exit-code") + 1] = "17"
    result = subprocess.run(
        [*command, "--verdict", "passed"],
        env={**os.environ, "VASO_ESTATE_ROOT": str(tmp_path)},
        capture_output=True,
        text=True,
        check=False,
    )
    actual_status = result.returncode
    assert actual_status == 2
    assert "passed verdict" in result.stderr
    assert "exit" in result.stderr
    assert "Traceback" not in result.stderr
    assert output.read_bytes() == b"previous result\n"


@pytest.mark.parametrize("verdict", ["passed", "failed", "blocked"])
def test_consistent_explicit_outcomes_remain_recordable(tmp_path, verdict):
    command = finalize(tmp_path)
    exit_code = 0 if verdict == "passed" else 75
    command[command.index("--exit-code") + 1] = str(exit_code)
    options = ["--verdict", verdict]
    if verdict != "passed":
        options += ["--blocked-reason", "fixture prerequisite"]
    subprocess.run(
        [*command, *options],
        env={**os.environ, "VASO_ESTATE_ROOT": str(tmp_path)},
        capture_output=True,
        text=True,
        check=True,
    )
    result = json.loads((tmp_path / "result.json").read_text())
    assert result["verdict"] == verdict
    assert result["exit_code"] == exit_code
    if verdict != "passed":
        assert result["blocked_reason"] == "fixture prerequisite"
