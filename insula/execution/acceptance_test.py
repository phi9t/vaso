"""Behavioral tests for the operational acceptance command (no shared estate)."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
from pathlib import Path

import pytest
import acceptance as acceptance_module
from vaso import atomic_files
from acceptance import source_hashes


SCRIPT = Path(__file__).with_name("acceptance.py")


def test_failed_acceptance_save_preserves_report_and_cleans_staging(tmp_path, monkeypatch):
    run = acceptance_module.AcceptanceRun(tmp_path, tmp_path / "evidence")
    path = run.evidence / "result.json"
    original = path.read_bytes()
    run.report["error"] = "new diagnostic"

    def fail(*args):
        raise OSError("cannot publish acceptance report")

    with monkeypatch.context() as scoped:
        scoped.setattr(atomic_files.os, "replace", fail)
        with pytest.raises(OSError, match="cannot publish acceptance report"):
            run.save()
    assert path.read_bytes() == original
    assert list(run.evidence.iterdir()) == [path]
    run.save()
    assert json.loads(path.read_text())["error"] == "new diagnostic"


@pytest.mark.parametrize("creation_fails", [False, True])
def test_acceptance_restores_the_callers_sigterm_handler(
    tmp_path, monkeypatch, creation_fails
):
    previous = signal.getsignal(signal.SIGTERM)

    def run(_self):
        handler = signal.getsignal(signal.SIGTERM)
        assert callable(handler)
        with pytest.raises(KeyboardInterrupt, match="SIGTERM"):
            handler(signal.SIGTERM, None)
        if creation_fails:
            raise OSError("fixture setup error")
        return 1

    monkeypatch.setattr(acceptance_module.AcceptanceRun, "run", run)
    result = acceptance_module.main(
        ["--rootfs", str(tmp_path), "--evidence-dir", str(tmp_path / "evidence")]
    )
    assert result == (2 if creation_fails else 1)
    assert signal.getsignal(signal.SIGTERM) == previous


@pytest.mark.parametrize(
    "failure, diagnostic",
    [
        (KeyboardInterrupt("fixture cancellation"), "fixture cancellation"),
        (OSError("fixture communication failure"), "fixture communication failure"),
        (subprocess.TimeoutExpired("fixture", 30), "timed out"),
    ],
)
def test_interrupted_command_stops_child_and_retains_failed_evidence(
    tmp_path, monkeypatch, failure, diagnostic
):
    run = acceptance_module.AcceptanceRun(tmp_path, tmp_path / "evidence")
    processes = []
    communicate = subprocess.Popen.communicate

    def interrupt(process, *args, **kwargs):
        if not processes:
            processes.append(process)
            raise failure
        return communicate(process, *args, **kwargs)

    def prepare():
        run.env = {"PATH": "/usr/bin:/bin"}
        run.command(
            "interrupted", [sys.executable, "-c", "import time; time.sleep(60)"]
        )

    monkeypatch.setattr(subprocess.Popen, "communicate", interrupt)
    monkeypatch.setattr(run, "prepare", prepare)
    try:
        escaped = False
        try:
            result = run.run()
        except KeyboardInterrupt:
            escaped = True
        assert not escaped, "cancellation escaped without a failed acceptance result"
        assert result == 1
        assert processes[0].poll() is not None, "owned child survived the failed run"
        report = json.loads((run.evidence / "result.json").read_text())
        assert report["verdict"] == "failed"
        assert diagnostic in report["error"]
        assert report["commands"][0]["exit_code"] != 0
        assert (run.evidence / "interrupted.stdout").is_file()
        assert (run.evidence / "interrupted.stderr").is_file()
        assert report["cleanup"]["state_removed"]
    finally:
        for process in processes:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
            process.communicate(timeout=5)


def test_source_manifest_includes_extracted_runtime_helpers_and_excludes_tests(
    tmp_path,
):
    runtime = [
        "insula/run.sh",
        "insula/execution/acceptance.py",
        "insula/execution/insula.sh",
        "insula/execution/doctor.sh",
        "insula/execution/lease.py",
        "insula/estate/overlay_root.py",
        "insula/estate/estate.py",
        "insula/rootfs/verify_cuda_ecosystem.py",
        "insula/execution/vaso/doctor.py",
        "insula/execution/vaso/lease_state.py",
        "insula/execution/vaso/__init__.py",
        "insula/execution/vaso/nested/helper.py",
    ]
    tests = [
        "insula/execution/vaso/doctor_test.py",
        "insula/execution/vaso/test_helper.py",
        "insula/execution/vaso/conftest.py",
    ]
    for relative in runtime + tests:
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("original\n")
    before = source_hashes(tmp_path)
    assert set(before) == set(runtime)
    for changed in ("insula/execution/vaso/lease_state.py", "insula/rootfs/verify_cuda_ecosystem.py"):
        before = source_hashes(tmp_path)
        (tmp_path / changed).write_text("changed implementation\n")
        after = source_hashes(tmp_path)
        assert {path for path in before if before[path] != after[path]} == {changed}


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
    assert set(report["features"]) == {"readiness", "mounts-and-argv", "exit-status", "interruption-and-retry", "lease-recovery", "cleanup"}
    assert all(report["features"].values())
    assert Path(report["bwrap"]["path"]).name == "bwrap"
    assert report["cleanup"]["state_removed"] is True
    assert (evidence / "mounts.stdout").read_text() == "mounts-ok\n"
    assert (evidence / "exit-status.stderr").is_file()
    assert not Path(report["state_dir"]).exists()
    assert sentinel.read_text() == "untouched\n"
    assert list(foreign.iterdir()) == [sentinel]
