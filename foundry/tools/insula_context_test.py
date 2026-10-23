"""Foundry context setup returns prerequisite errors to its sourcing caller."""

from pathlib import Path
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[2]


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
