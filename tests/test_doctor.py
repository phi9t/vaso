import json
import hashlib
import os
import subprocess
from pathlib import Path

from vaso.cli import main
from vaso.config import default_config
from vaso.doctor import InsulaDoctorConfig, doctor_passed, run_doctor, run_insula_doctor
from vaso.paths import ensure_host_layout


ROOT = Path(__file__).resolve().parents[1]
INSULA_SH = ROOT / "scripts" / "insula" / "insula.sh"


def _make_fake_insula_tree(root: Path, *, line: str = "cu129") -> tuple[Path, Path, Path]:
    estate = root / "estate"
    writable = [
        "vaso",
        "vaso/cache/bazel/repository-cache",
        "vaso/tmp",
        "home/kvothe",
        "sources",
        "agents/leases",
        f"vaso/lines/{line}/cache/bazel/output-base",
        f"vaso/lines/{line}/cache/bazel/disk-cache",
        f"vaso/lines/{line}/state/native",
        f"vaso/lines/{line}/state/stamps",
    ]
    for rel in writable:
        (estate / rel).mkdir(parents=True, exist_ok=True)
    rootfs = estate / "rootfs-lines" / line / "rootfs"
    (rootfs / "bin").mkdir(parents=True)
    (rootfs / "bin" / "bash").write_text("#!/usr/bin/env bash\n", encoding="utf-8")

    lock = root / "cuda_ecosystem.lock.json"
    lock.write_text(json.dumps({"schema_version": 1, "lines": {line: {"components": {}}}}), encoding="utf-8")
    digest = hashlib.sha256(lock.read_bytes()).hexdigest()
    manifest = estate / "rootfs-lines" / line / "rootfs-bundle.json"
    manifest.write_text(
        json.dumps({"schema_version": 2, "line": line, "lock": {"sha256": digest}}),
        encoding="utf-8",
    )

    driver = root / "driver"
    driver.mkdir()
    (driver / "libcuda.so.1").write_text("", encoding="utf-8")
    (driver / "libnvidia-ml.so.1").write_text("", encoding="utf-8")
    dev = root / "dev"
    dev.mkdir()
    (dev / "nvidia0").write_text("", encoding="utf-8")
    return estate, lock, driver


def _insula_config(estate: Path, lock: Path, driver: Path, *, line: str = "cu129") -> InsulaDoctorConfig:
    return InsulaDoctorConfig(
        estate_root=estate,
        line=line,
        rootfs_lock=lock,
        min_estate_free_gib=0,
        min_docker_free_gib=10_000_000,
        docker_root=estate / "missing-docker-root",
        driver_library_dirs=(driver,),
        device_globs=(str(driver.parent / "dev" / "nvidia*"),),
        require_driver=True,
    )


def test_doctor_reports_unpopulated_rootfs(tmp_path):
    repo = tmp_path / "workspace" / "vaso"
    repo.mkdir(parents=True)
    config = default_config(repo)
    ensure_host_layout(config)
    checks = run_doctor(config)
    rootfs = [check for check in checks if check.name == "rootfs_has_shell"][0]
    assert not rootfs.ok
    assert "bin/sh" in rootfs.detail


def test_cli_doctor_outputs_json(tmp_path, monkeypatch, capsys):
    repo = tmp_path / "workspace" / "vaso"
    repo.mkdir(parents=True)
    monkeypatch.chdir(repo)
    code = main(["doctor"])
    assert code == 1
    data = json.loads(capsys.readouterr().out)
    assert "checks" in data


def test_insula_doctor_accepts_fake_healthy_estate(tmp_path: Path) -> None:
    estate, lock, driver = _make_fake_insula_tree(tmp_path)
    checks = run_insula_doctor(_insula_config(estate, lock, driver))
    by_name = {check.name: check for check in checks}

    assert by_name["writable:vaso"].ok
    assert by_name["driver:libcuda.so.1"].ok
    assert by_name["driver:libnvidia-ml.so.1"].ok
    assert by_name["driver:devices"].ok
    assert by_name["rootfs_manifest:line"].ok
    assert by_name["rootfs_manifest:lock_sha256"].ok
    assert by_name["lease_dir:json"].ok
    assert not by_name["disk:docker_root_headroom"].ok
    assert not by_name["disk:docker_root_headroom"].required
    assert doctor_passed(checks)


def test_insula_doctor_reports_missing_driver_sources(tmp_path: Path) -> None:
    estate, lock, driver = _make_fake_insula_tree(tmp_path)
    (driver / "libcuda.so.1").unlink()

    checks = run_insula_doctor(_insula_config(estate, lock, driver))
    libcuda = {check.name: check for check in checks}["driver:libcuda.so.1"]

    assert not libcuda.ok
    assert libcuda.required
    assert "install NVIDIA driver libraries" in libcuda.detail
    assert not doctor_passed(checks)


def test_insula_doctor_reports_manifest_lock_mismatch(tmp_path: Path) -> None:
    estate, lock, driver = _make_fake_insula_tree(tmp_path)
    manifest = estate / "rootfs-lines" / "cu129" / "rootfs-bundle.json"
    manifest.write_text(
        json.dumps({"schema_version": 2, "line": "cu129", "lock": {"sha256": "0" * 64}}),
        encoding="utf-8",
    )

    checks = run_insula_doctor(_insula_config(estate, lock, driver))
    mismatch = {check.name: check for check in checks}["rootfs_manifest:lock_sha256"]

    assert not mismatch.ok
    assert "rebuild the rootfs line" in mismatch.detail
    assert not doctor_passed(checks)


def test_insula_doctor_reports_corrupt_lease_state(tmp_path: Path) -> None:
    estate, lock, driver = _make_fake_insula_tree(tmp_path)
    (estate / "agents" / "leases" / "gpu.json").write_text("{not-json", encoding="utf-8")

    checks = run_insula_doctor(_insula_config(estate, lock, driver))
    lease_json = {check.name: check for check in checks}["lease_dir:json"]

    assert not lease_json.ok
    assert "remove or repair corrupt lease state" in lease_json.detail
    assert not doctor_passed(checks)


def test_insula_function_runs_preflight_before_bwrap(tmp_path: Path) -> None:
    doctor = tmp_path / "doctor.sh"
    doctor.write_text("#!/usr/bin/env bash\necho preflight refused >&2\nexit 42\n", encoding="utf-8")
    doctor.chmod(0o755)
    harness = f"""
set -euo pipefail
source {str(INSULA_SH)!r}
ROOT_PLAN=()
INSULA_DIR_ARGS=()
INSULA_BIND_ARGS=()
INSULA_ENV_ARGS=()
INSULA_DOCTOR={str(doctor)!r}
INSULA_BWRAP_BIN=/bin/false
insula -- bash -lc 'echo unreachable'
"""
    result = subprocess.run(
        ["bash", "-c", harness],
        cwd=ROOT,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 42
    assert "preflight refused" in result.stderr
    assert "unreachable" not in result.stdout
