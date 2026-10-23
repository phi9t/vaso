from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
LEASE = ROOT / "insula" / "execution" / "lease.py"


def _lease_cmd(estate: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "VASO_ESTATE_ROOT": str(estate)}
    return subprocess.run(
        ["python3", str(LEASE), *args],
        cwd=ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )


def _acquire(estate: Path, *args: str) -> dict[str, object]:
    result = _lease_cmd(
        estate,
        "acquire",
        "--holder",
        "pytest",
        "--pid",
        str(os.getpid()),
        "--ttl",
        "60",
        "--timeout",
        "0",
        *args,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def _release(estate: Path, lease_id: object) -> None:
    result = _lease_cmd(estate, "release", "--id", str(lease_id))
    assert result.returncode == 0, result.stderr


def test_two_holders_of_one_line_time_out(tmp_path: Path) -> None:
    estate = tmp_path / "estate"
    held = _acquire(estate, "--resource", "insula:cu129")
    try:
        blocked = _lease_cmd(
            estate,
            "acquire",
            "--resource",
            "insula:cu129",
            "--holder",
            "blocked",
            "--pid",
            str(os.getpid()),
            "--timeout",
            "0.05",
        )
        assert blocked.returncode == 75
        assert "timed out acquiring insula:cu129" in blocked.stderr
    finally:
        _release(estate, held["id"])


def test_different_lines_proceed_concurrently(tmp_path: Path) -> None:
    estate = tmp_path / "estate"
    first = _acquire(estate, "--resource", "insula:cu129")
    second = _acquire(estate, "--resource", "insula:cu130")
    try:
        assert first["resource"] == "insula:cu129"
        assert second["resource"] == "insula:cu130"
    finally:
        _release(estate, first["id"])
        _release(estate, second["id"])


def test_gpu_sets_are_disjoint(tmp_path: Path) -> None:
    estate = tmp_path / "estate"
    first = _acquire(estate, "--resource", "gpu", "--amount", "3", "--capacity", "8")
    second = _acquire(estate, "--resource", "gpu", "--amount", "4", "--capacity", "8")
    try:
        assert first["devices"] == [0, 1, 2]
        assert second["devices"] == [3, 4, 5, 6]
        assert set(first["devices"]).isdisjoint(set(second["devices"]))
        assert first["env"]["CUDA_VISIBLE_DEVICES"] == "0,1,2"
        assert first["env"]["VASO_GPU_SET"] == "0,1,2"
    finally:
        _release(estate, first["id"])
        _release(estate, second["id"])


def test_exact_gpu_selection_never_falls_back(tmp_path: Path) -> None:
    estate = tmp_path / "estate"
    selected = _acquire(
        estate,
        "--resource",
        "gpu",
        "--amount",
        "1",
        "--capacity",
        "8",
        "--devices",
        "7",
    )
    try:
        assert selected["devices"] == [7]
        assert selected["env"]["CUDA_VISIBLE_DEVICES"] == "7"
        conflict = _lease_cmd(
            estate,
            "acquire",
            "--resource",
            "gpu",
            "--amount",
            "1",
            "--capacity",
            "8",
            "--devices",
            "7",
        )
        assert conflict.returncode == 75
        for devices in ("8", "-1", "0,0", "bad", "0,1"):
            invalid = _lease_cmd(
                estate,
                "acquire",
                "--resource",
                "gpu",
                "--amount",
                "1",
                "--capacity",
                "8",
                "--devices",
                devices,
            )
            assert invalid.returncode == 2
    finally:
        _release(estate, selected["id"])


def test_check_requires_live_matching_lease(tmp_path: Path) -> None:
    estate = tmp_path / "estate"
    held = _acquire(estate, "--resource", "insula:cu129")
    try:
        live = _lease_cmd(
            estate, "check", "--id", str(held["id"]), "--resource", "insula:cu129"
        )
        assert live.returncode == 0, live.stderr
        assert json.loads(live.stdout)["live"] is True

        live_any_resource = _lease_cmd(estate, "check", "--id", str(held["id"]))
        assert (
            json.loads((estate / "agents/leases/insula_cu129.json").read_text())[
                "resource"
            ]
            == "insula:cu129"
        )
        assert live_any_resource.returncode == 0, live_any_resource.stderr
        assert (
            json.loads(live_any_resource.stdout)["lease"]["resource"] == "insula:cu129"
        )

        foreign_resource = _lease_cmd(
            estate,
            "check",
            "--id",
            str(held["id"]),
            "--resource",
            "insula:cu130",
        )
        assert foreign_resource.returncode == 75
        assert json.loads(foreign_resource.stdout)["live"] is False
    finally:
        _release(estate, held["id"])

    stale = _lease_cmd(
        estate, "check", "--id", str(held["id"]), "--resource", "insula:cu129"
    )
    assert stale.returncode == 75
    assert json.loads(stale.stdout)["live"] is False


def test_expired_or_dead_holder_is_reclaimed(tmp_path: Path) -> None:
    estate = tmp_path / "estate"
    expired = _lease_cmd(
        estate,
        "acquire",
        "--resource",
        "insula:cu129",
        "--holder",
        "expired",
        "--pid",
        str(os.getpid()),
        "--ttl",
        "0.01",
        "--timeout",
        "0",
    )
    assert expired.returncode == 0, expired.stderr
    time.sleep(0.03)
    replacement = _acquire(estate, "--resource", "insula:cu129")
    _release(estate, replacement["id"])

    child = subprocess.Popen(["true"])
    child.wait(timeout=5)
    dead_pid = child.pid
    dead = _lease_cmd(
        estate,
        "acquire",
        "--resource",
        "insula:cu130",
        "--holder",
        "dead",
        "--pid",
        str(dead_pid),
        "--ttl",
        "60",
        "--timeout",
        "0",
    )
    assert dead.returncode == 0, dead.stderr
    replacement = _acquire(estate, "--resource", "insula:cu130")
    _release(estate, replacement["id"])


def test_cpu_budget_splits_between_holders(tmp_path: Path) -> None:
    estate = tmp_path / "estate"
    first = _acquire(estate, "--resource", "host-cpu", "--budget", "16")
    second = _acquire(estate, "--resource", "host-cpu", "--budget", "16")
    try:
        assert first["jobs"] == 16
        assert second["jobs"] == 8
        listed = _lease_cmd(estate, "list", "--resource", "host-cpu")
        assert listed.returncode == 0, listed.stderr
        leases = json.loads(listed.stdout)["leases"]
        assert sorted(lease["jobs"] for lease in leases) == [8, 8]
    finally:
        _release(estate, first["id"])
        _release(estate, second["id"])


def test_network_fetch_lease_is_exclusive(tmp_path: Path) -> None:
    estate = tmp_path / "estate"
    held = _acquire(estate, "--resource", "network-fetch")
    try:
        blocked = _lease_cmd(
            estate,
            "acquire",
            "--resource",
            "network-fetch",
            "--holder",
            "blocked",
            "--pid",
            str(os.getpid()),
            "--timeout",
            "0.05",
        )
        assert blocked.returncode == 75
        assert "timed out acquiring network-fetch" in blocked.stderr
    finally:
        _release(estate, held["id"])


def test_corrupt_lease_state_is_preserved_and_cannot_grant_a_new_holder(
    tmp_path,
) -> None:
    estate = tmp_path / "estate"
    states = estate / "agents/leases"
    states.mkdir(parents=True)
    state = states / "insula_cu129.json"
    for invalid in (
        "not JSON",
        "[]",
        '{"schema_version": 99, "resource": "insula:cu129", "leases": []}',
    ):
        state.write_text(invalid)
        for command in (
            ("acquire", "--resource", "insula:cu129", "--timeout", "0"),
            ("list",),
            ("check", "--id", "unknown"),
            ("release", "--id", "unknown"),
        ):
            result = _lease_cmd(estate, *command)
            assert result.returncode == 2, result.stdout + result.stderr
            assert str(state) in result.stderr
            assert "Traceback" not in result.stderr
            assert state.read_text() == invalid


def test_invalid_holder_metadata_is_not_silently_reclaimed(tmp_path) -> None:
    estate = tmp_path / "estate"
    states = estate / "agents/leases"
    states.mkdir(parents=True)
    state = states / "insula_cu129.json"
    holder = {
        "id": "unknown-holder",
        "pid": os.getpid(),
        "host": "fixture.invalid",
        "resource": "insula:cu129",
        "expiry_time": 9999999999,
    }
    for changed in (
        {"pid": "damaged"},
        {"pid": 1.5},
        {"expiry_time": "damaged"},
        {"expiry_time": float("nan")},
        {"host": None},
    ):
        state.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "resource": "insula:cu129",
                    "leases": [holder | changed],
                }
            )
        )
        before = state.read_bytes()
        result = _lease_cmd(
            estate, "acquire", "--resource", "insula:cu129", "--timeout", "0"
        )
        assert result.returncode == 2, result.stdout + result.stderr
        assert str(state) in result.stderr
        assert state.read_bytes() == before


def test_gpu_holder_without_device_metadata_does_not_free_capacity(tmp_path) -> None:
    estate = tmp_path / "estate"
    state = estate / "agents/leases/gpu.json"
    state.parent.mkdir(parents=True)
    state.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "resource": "gpu",
                "leases": [
                    {
                        "id": "unknown-holder",
                        "pid": os.getpid(),
                        "host": "fixture.invalid",
                        "resource": "gpu",
                        "expiry_time": 9999999999,
                    }
                ],
            }
        )
    )
    before = state.read_bytes()
    result = _lease_cmd(
        estate, "acquire", "--resource", "gpu", "--capacity", "1", "--amount", "1"
    )
    assert result.returncode == 2, result.stdout + result.stderr
    assert "invalid GPU devices" in result.stderr
    assert state.read_bytes() == before


def test_invalid_lease_configuration_fails_before_state_creation(tmp_path) -> None:
    estate = tmp_path / "estate"
    for flags in (
        ("--ttl", "nan"),
        ("--ttl", "inf"),
        ("--timeout", "nan"),
        ("--timeout", "inf"),
        ("--pid", "0"),
        ("--pid", str(2**63)),
    ):
        result = _lease_cmd(estate, "acquire", "--resource", "insula:cu129", *flags)
        assert result.returncode == 2, result.stdout + result.stderr
        assert "Traceback" not in result.stderr
        assert not estate.exists()


@pytest.mark.parametrize(
    "problem",
    [
        "missing-query",
        "failed-query",
        "empty-query",
        "invalid-env",
        "negative-capacity",
        "excess-amount",
        "invalid-devices",
    ],
)
def test_invalid_gpu_request_creates_no_coordination_state(
    tmp_path, monkeypatch, problem
):
    estate = tmp_path / "estate"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "python3").symlink_to(sys.executable)
    monkeypatch.setenv("PATH", str(bin_dir))
    monkeypatch.delenv("VASO_GPU_COUNT", raising=False)
    if problem != "missing-query":
        query = bin_dir / "nvidia-smi"
        query.write_text(
            "#!/bin/sh\n"
            + (
                "exit 9\n"
                if problem == "failed-query"
                else "exit 0\n"
                if problem == "empty-query"
                else "printf 'GPU 0: fixture\nGPU 1: fixture\n'\n"
            )
        )
        query.chmod(0o755)
    options = []
    if problem == "invalid-env":
        monkeypatch.setenv("VASO_GPU_COUNT", "invalid")
    elif problem == "negative-capacity":
        options = ["--capacity", "-1"]
    elif problem == "excess-amount":
        options = ["--amount", "3"]
    elif problem == "invalid-devices":
        options = ["--devices", "7"]
    result = _lease_cmd(estate, "acquire", "--resource", "gpu", *options)
    actual_status = result.returncode
    assert actual_status == 2, result.stderr
    assert "Traceback" not in result.stderr
    assert not (estate / "agents/leases").exists()


def test_explicit_gpu_capacity_does_not_need_query_and_zero_stays_zero(
    tmp_path, monkeypatch
):
    estate = tmp_path / "estate"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "python3").symlink_to(sys.executable)
    monkeypatch.setenv("PATH", str(bin_dir))
    monkeypatch.delenv("VASO_GPU_COUNT", raising=False)
    rejected = _lease_cmd(estate, "acquire", "--resource", "gpu", "--capacity", "0")
    assert rejected.returncode == 2
    assert not (estate / "agents/leases").exists()
    record = _acquire(estate, "--resource", "gpu", "--capacity", "1")
    assert record["devices"] == [0]
    _release(estate, record["id"])


def test_detected_gpu_capacity_grants_only_observed_devices(tmp_path, monkeypatch):
    estate = tmp_path / "estate"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "python3").symlink_to(sys.executable)
    query = bin_dir / "nvidia-smi"
    query.write_text("#!/bin/sh\nprintf 'GPU 0: fixture\nGPU 1: fixture\n'\n")
    query.chmod(0o755)
    monkeypatch.setenv("PATH", str(bin_dir))
    monkeypatch.delenv("VASO_GPU_COUNT", raising=False)
    record = _acquire(estate, "--resource", "gpu", "--amount", "2")
    try:
        assert record["devices"] == [0, 1]
        state = json.loads((estate / "agents/leases/gpu.json").read_text())
        assert state["capacity"] == 2
        blocked = _lease_cmd(estate, "acquire", "--resource", "gpu")
        assert blocked.returncode == 75
    finally:
        _release(estate, record["id"])
