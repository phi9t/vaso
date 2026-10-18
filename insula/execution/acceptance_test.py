"""Behavioral tests for the operational acceptance command (no shared estate)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


SCRIPT = Path(__file__).with_name("acceptance.py")


def run_acceptance(rootfs: Path, evidence: Path, **env: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--rootfs", str(rootfs), "--evidence-dir", str(evidence)],
        env={**os.environ, **env}, capture_output=True, text=True, timeout=90,
    )


def test_missing_rootfs_fails_and_preserves_evidence_after_cleanup(tmp_path: Path) -> None:
    evidence = tmp_path / "evidence"
    result = run_acceptance(tmp_path / "absent-rootfs", evidence)
    assert result.returncode == 1, result.stderr
    report = json.loads((evidence / "result.json").read_text())
    assert report["verdict"] == "failed"
    assert "rootfs" in report["error"]
    assert report["cleanup"]["state_removed"] is True
    assert not Path(report["state_dir"]).exists()


def test_existing_evidence_is_never_overwritten(tmp_path: Path) -> None:
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    original = evidence / "result.json"
    original.write_text("original evidence\n")
    result = run_acceptance(tmp_path / "absent", evidence)
    assert result.returncode != 0
    assert "exists" in result.stderr
    assert original.read_text() == "original evidence\n"


def test_unusable_rootfs_cannot_be_a_green_skipped_run(tmp_path: Path) -> None:
    rootfs = tmp_path / "rootfs"
    (rootfs / "bin").mkdir(parents=True)
    (rootfs / "bin/sh").write_text("not an executable shell\n")
    evidence = tmp_path / "evidence"
    result = run_acceptance(rootfs, evidence, INSULA_BWRAP_BIN="/bin/true", INSULA_SKIP_PREFLIGHT="1")
    assert result.returncode == 1
    report = json.loads((evidence / "result.json").read_text())
    assert report["verdict"] == "failed"
    assert report["features"]["readiness"] is False
    assert report["cleanup"]["state_removed"] is True
    assert "error" in report


@pytest.mark.skipif(
    os.environ.get("VASO_RUN_INSULA_ACCEPTANCE") != "1",
    reason="real namespaces require an explicit VASO_RUN_INSULA_ACCEPTANCE=1; the command itself never skips",
)
def test_real_insula_acceptance_ignores_inherited_test_and_estate_overrides(tmp_path: Path) -> None:
    evidence = tmp_path / "evidence"
    foreign = tmp_path / "unrelated-estate"
    foreign.mkdir()
    sentinel = foreign / "keep"
    sentinel.write_text("untouched\n")
    rootfs = Path(os.environ.get("VASO_ACCEPTANCE_ROOTFS", "/"))
    result = run_acceptance(
        rootfs, evidence,
        VASO_ESTATE_ROOT=str(foreign), INSULA_BWRAP_BIN="/bin/true",
        INSULA_SKIP_PREFLIGHT="1", VASO_LEASES_HELD="insula:cu129:fake",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads((evidence / "result.json").read_text())
    assert report["verdict"] == "passed"
    assert set(report["features"]) == {"readiness", "mounts-and-argv", "exit-status", "lease-recovery", "cleanup"}
    assert all(report["features"].values())
    assert Path(report["bwrap"]["path"]).name == "bwrap"
    assert report["cleanup"]["state_removed"] is True
    assert (evidence / "mounts.stdout").read_text() == "mounts-ok\n"
    assert (evidence / "exit-status.stderr").is_file()
    assert not Path(report["state_dir"]).exists()
    assert sentinel.read_text() == "untouched\n"
    assert list(foreign.iterdir()) == [sentinel]
