"""GPU observations validate query data before qualification consumes it."""

from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import subprocess
import sys

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import gpu_monitor as gpu
from process_runner import run_command


def test_snapshot_parser_preserves_device_and_process_identity():
    snapshot = gpu.parse_snapshot(
        "0, private-device-a, NVIDIA B200, 0, 0\n"
        '7, private-device-b, "fixture, GPU", 25, 100\n',
        "private-device-b, 123\nprivate-device-a, 123\nprivate-device-b, 456\n",
    )
    assert snapshot == {
        "devices": [
            {"index": 0, "name": "NVIDIA B200", "utilization": 0, "memory_mib": 0},
            {"index": 7, "name": "fixture, GPU", "utilization": 25, "memory_mib": 100},
        ],
        "processes": [
            {"gpu_index": 7, "pid": 123},
            {"gpu_index": 0, "pid": 123},
            {"gpu_index": 7, "pid": 456},
        ],
        "compute_pids": [123, 456],
    }
    assert "private-device" not in str(snapshot)


@pytest.mark.parametrize(
    "devices,processes,query,row",
    [
        ("0, private-device-a, NVIDIA B200, 0\n", "", "device", 1),
        ("0, private-device-a, NVIDIA B200, N/A, 0\n", "", "device", 1),
        ("-1, private-device-a, NVIDIA B200, 0, 0\n", "", "device", 1),
        ("0, private-device-a, NVIDIA B200, 101, 0\n", "", "device", 1),
        ("0, private-device-a, NVIDIA B200, 0, -1\n", "", "device", 1),
        ("0, , NVIDIA B200, 0, 0\n", "", "device", 1),
        (
            "0, private-device-a, NVIDIA B200, 0, 0\n0, private-device-b, NVIDIA B200, 0, 0\n",
            "",
            "device",
            2,
        ),
        (
            "0, private-device-a, NVIDIA B200, 0, 0\n1, private-device-a, NVIDIA B200, 0, 0\n",
            "",
            "device",
            2,
        ),
        (
            "0, private-device-a, NVIDIA B200, 0, 0\n",
            "private-device-b, 123\n",
            "process",
            1,
        ),
        (
            "0, private-device-a, NVIDIA B200, 0, 0\n",
            "private-device-a, 0\n",
            "process",
            1,
        ),
        (
            "0, private-device-a, NVIDIA B200, 0, 0\n",
            "private-device-a, N/A\n",
            "process",
            1,
        ),
        (
            "0, private-device-a, NVIDIA B200, 0, 0\n",
            "private-device-a\n",
            "process",
            1,
        ),
        ('0, private-device-a, "unfinished', "", "device", 1),
    ],
    ids=[
        "truncated-device",
        "unknown-utilization",
        "negative-index",
        "excess-utilization",
        "negative-memory",
        "missing-identity",
        "duplicate-index",
        "duplicate-uuid",
        "unseen-process-device",
        "zero-pid",
        "unknown-pid",
        "truncated-process",
        "malformed-csv",
    ],
)
def test_invalid_snapshot_identifies_query_and_row_without_raw_identifiers(
    devices, processes, query, row, monkeypatch
):
    outputs = iter((devices, processes))
    monkeypatch.setattr(
        gpu.subprocess, "check_output", lambda *args, **kwargs: next(outputs)
    )
    with pytest.raises(ValueError) as raised:
        gpu.gpu_snapshot()
    message = str(raised.value)
    assert f"GPU {query} query row {row}" in message
    assert "private-device" not in message


def test_acquisition_records_observation_time_and_preserves_empty_queries(monkeypatch):
    outputs = iter(("", ""))
    monkeypatch.setattr(
        gpu.subprocess, "check_output", lambda *args, **kwargs: next(outputs)
    )
    snapshot = gpu.gpu_snapshot()
    assert snapshot["observed_at"]
    assert snapshot["devices"] == []
    assert snapshot["processes"] == []
    assert snapshot["compute_pids"] == []
    assert gpu.resource_blockers(snapshot, None)


def idle():
    return {
        "devices": [
            {"name": "NVIDIA B200", "utilization": 0, "index": i, "memory_mib": 0}
            for i in range(8)
        ],
        "compute_pids": [],
    }



def reservation():
    now = datetime.now(timezone.utc)
    return {
        "exclusive": True,
        "gpu_count": 8,
        "reference": "test-reservation",
        "starts_at": (now - timedelta(minutes=1)).isoformat(),
        "ends_at": (now + timedelta(minutes=30)).isoformat(),
    }



def test_idle_devices_do_not_establish_reservation():
    assert gpu.resource_blockers(idle(), None)
    assert not gpu.resource_blockers(idle(), reservation())



def test_occupied_expired_and_wrong_hardware_block():
    snapshot = idle()
    snapshot["compute_pids"] = [123]
    assert gpu.resource_blockers(snapshot, reservation())
    snapshot = idle()
    snapshot["devices"][0]["name"] = "other"
    assert gpu.resource_blockers(snapshot, reservation())
    r = reservation()
    r["ends_at"] = r["starts_at"]
    assert gpu.resource_blockers(idle(), r)



def test_foreign_gpu_process_stops_owned_stage_only(tmp_path, monkeypatch):
    foreign = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        monkeypatch.setattr(
            gpu, "gpu_snapshot", lambda: {**idle(), "compute_pids": [foreign.pid]}
        )
        r = run_command(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            dict(os.environ),
            tmp_path / "stage.log",
            60,
            monitor=gpu.Monitor(
                tmp_path / "gpu-observations.jsonl", reservation(), None
            ),
        )
        assert r["status"] == "failed" and "contaminated" in r["error"]
        assert foreign.poll() is None
        assert (tmp_path / "stage.log").exists()
    finally:
        foreign.terminate()
        foreign.wait()



def test_single_gpu_ignores_other_devices_but_rejects_selected_occupancy():
    snapshot = {
        **idle(),
        "processes": [{"gpu_index": 0, "pid": 123}],
        "compute_pids": [123],
    }
    snapshot["devices"][0]["memory_mib"] = 100
    assert not gpu.resource_blockers(snapshot, None, 7)
    snapshot["processes"].append({"gpu_index": 7, "pid": 456})
    assert gpu.resource_blockers(snapshot, None, 7)
    assert gpu.resource_blockers(snapshot, None, 9)



def test_exited_owned_gpu_pid_is_not_foreign_and_reused_pid_is_not_owned(monkeypatch):
    known = {}
    monkeypatch.setattr(gpu, "process_start", lambda pid: 100)
    monkeypatch.setattr(gpu, "descendant", lambda pid, ancestor: True)
    assert gpu.attribute_gpu_processes([123], 1, known) == {123: "owned"}
    monkeypatch.setattr(gpu, "process_start", lambda pid: None)
    monkeypatch.setattr(gpu, "descendant", lambda pid, ancestor: False)
    assert gpu.attribute_gpu_processes([123, 456], 1, known) == {
        123: "exited-owned",
        456: "unattributed-exited",
    }
    monkeypatch.setattr(gpu, "process_start", lambda pid: 200)
    assert gpu.attribute_gpu_processes([123], 1, known) == {123: "foreign"}
