"""Foundry context setup returns prerequisite errors to its sourcing caller."""

from pathlib import Path
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("arguments", [["--help"], ["-h"], ["run", "--help"]])
def test_help_describes_execution_without_preparing_an_estate(tmp_path, arguments):
    estate = tmp_path / "unprepared"
    result = subprocess.run(
        [str(ROOT / "foundry/insula.sh"), *arguments],
        cwd=tmp_path,
        env={"PATH": "/usr/bin:/bin", "VASO_ESTATE_ROOT": str(estate)},
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "usage:" in result.stdout
    for option in (
        "--line",
        "--phase",
        "--release",
        "--bundle",
        "--gpus",
        "--print-argv",
    ):
        assert option in result.stdout
    assert "VASO_ESTATE_ROOT" in result.stdout
    assert "foundry/build.sh --help" in result.stdout
    assert "context preparation still runs" in result.stdout
    assert "BASH_SOURCE" not in result.stdout
    assert result.stderr == ""
    assert not estate.exists()


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        ([], "requires --line or VASO_CUDA_LINE"),
        (["--line", "cu129"], "requires a command after --"),
    ],
)
def test_missing_request_fields_print_usage_before_preparation(
    tmp_path, arguments, message
):
    estate = tmp_path / "unprepared"
    result = subprocess.run(
        [str(ROOT / "foundry/insula.sh"), *arguments],
        cwd=tmp_path,
        env={"PATH": "/usr/bin:/bin", "VASO_ESTATE_ROOT": str(estate)},
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 2
    assert message in result.stderr
    assert "usage:" in result.stderr
    assert "--release DIR" in result.stderr
    assert not estate.exists()


@pytest.mark.parametrize(
    ("line", "configured_estate", "has_workspace", "message"),
    [
        ("cu129", False, True, "VASO_ESTATE_ROOT"),
        ("", True, True, "line is required"),
        ("cu129", True, False, "experiment directory is required"),
    ],
)
def test_context_prerequisite_error_does_not_exit_the_caller(
    tmp_path, line, configured_estate, has_workspace, message
):
    estate = tmp_path / "estate"
    env = {
        "PATH": "/usr/bin:/bin",
        "INSULA_REPO_ROOT": str(ROOT),
        "HOME": str(tmp_path),
    }
    if configured_estate:
        env["VASO_ESTATE_ROOT"] = str(estate)
    result = subprocess.run(
        [
            "bash",
            "-c",
            'source "$1/foundry/tools/insula_context.sh"; '
            'insula_prepare_experiment_context "$3" "$2" 0; '
            'status=$?; printf "returned:%s\\n" "$status"; test "$status" -eq 2',
            "bash",
            str(ROOT),
            line,
            str(ROOT / "foundry") if has_workspace else "",
        ],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == "returned:2"
    assert message in result.stderr
    assert not estate.exists()
