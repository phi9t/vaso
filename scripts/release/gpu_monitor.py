"""Physical GPU preflight, exact device leases, and live process ownership.

These checks implement release qualification's reservation policy. The nightly
memory-threshold wait is a separate readiness check, not qualification proof.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def gpu_snapshot() -> dict:
    def query(args):
        return subprocess.check_output(
            ["nvidia-smi", *args, "--format=csv,noheader,nounits"],
            text=True,
            timeout=15,
        )

    devices = []
    uuid_indices = {}
    for row in query(
        ["--query-gpu=index,uuid,name,utilization.gpu,memory.used"]
    ).splitlines():
        index, device_uuid, name, utilization, memory = [
            s.strip() for s in row.split(",")
        ]
        uuid_indices[device_uuid] = int(index)
        devices.append(
            {
                "index": int(index),
                "name": name,
                "utilization": int(utilization),
                "memory_mib": int(memory),
            }
        )
    processes = []
    for row in query(["--query-compute-apps=gpu_uuid,pid"]).splitlines():
        device_uuid, pid = [value.strip() for value in row.split(",")]
        processes.append({"gpu_index": uuid_indices[device_uuid], "pid": int(pid)})
    return {
        "observed_at": now(),
        "devices": devices,
        "processes": processes,
        "compute_pids": sorted({item["pid"] for item in processes}),
    }


def selected_snapshot(snapshot: dict, gpu_index: int | None) -> dict:
    if gpu_index is None:
        return snapshot
    processes = [
        item for item in snapshot["processes"] if item["gpu_index"] == gpu_index
    ]
    return {
        **snapshot,
        "devices": [d for d in snapshot["devices"] if d["index"] == gpu_index],
        "processes": processes,
        "compute_pids": sorted({item["pid"] for item in processes}),
    }


def resource_blockers(
    snapshot: dict, reservation: dict | None, gpu_index: int | None = None
) -> list[str]:
    reasons = []
    snapshot = selected_snapshot(snapshot, gpu_index)
    devices = snapshot["devices"]
    if len(devices) != (8 if gpu_index is None else 1) or any(
        d["name"] != "NVIDIA B200" for d in devices
    ):
        reasons.append("selected B200 devices are missing or unsupported")
    if snapshot["compute_pids"] or any(
        d["utilization"] or d["memory_mib"] for d in devices
    ):
        reasons.append("selected physical GPUs occupied")
    if gpu_index is not None and reservation is None:
        # Explicit single-device execution uses a cooperative lease and continuous
        # occupancy monitoring; it is not an exclusive eight-GPU qualification.
        return reasons
    if (
        not reservation
        or reservation.get("exclusive") is not True
        or reservation.get("gpu_count") != 8
        or not reservation.get("reference")
    ):
        reasons.append(
            "missing reservation record with reference, exclusive=true and gpu_count=8"
        )
    else:
        try:
            start = datetime.fromisoformat(reservation["starts_at"])
            end = datetime.fromisoformat(reservation["ends_at"])
            if not start <= datetime.now(timezone.utc) < end:
                reasons.append("reservation is outside its declared time window")
        except (KeyError, ValueError, TypeError):
            reasons.append("reservation needs timezone-aware starts_at and ends_at")
    return reasons


@contextmanager
def gpu_lease(estate: Path, indices: list[int], ttl: float):
    lease_tool = Path(__file__).resolve().parents[2] / "insula/execution/lease.py"
    env = {**os.environ, "VASO_ESTATE_ROOT": str(estate)}
    env.pop("VASO_GPU_COUNT", None)
    result = subprocess.run(
        [
            sys.executable,
            str(lease_tool),
            "acquire",
            "--resource",
            "gpu",
            "--amount",
            str(len(indices)),
            "--devices",
            ",".join(map(str, indices)),
            "--pid",
            str(os.getpid()),
            "--holder",
            "release-qualification",
            "--ttl",
            str(ttl),
        ],
        env=env,
        text=True,
        capture_output=True,
        check=True,
    )
    lease = json.loads(result.stdout)
    try:
        yield lease
    finally:
        subprocess.run(
            [
                sys.executable,
                str(lease_tool),
                "release",
                "--resource",
                "gpu",
                "--id",
                lease["id"],
            ],
            env=env,
            text=True,
            capture_output=True,
            check=True,
        )


def descendant(pid: int, ancestor: int) -> bool:
    seen = set()
    while pid > 1 and pid not in seen:
        if pid == ancestor:
            return True
        seen.add(pid)
        try:
            # The command name can contain spaces or closing parentheses.
            pid = int(
                Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[1]
            )
        except (OSError, ValueError, IndexError):
            return False
    return False


def process_start(pid: int) -> int | None:
    try:
        return int(Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[19])
    except (FileNotFoundError, ProcessLookupError):
        return None


def attribute_gpu_processes(
    pids: list[int], ancestor: int, known: dict[int, int]
) -> dict[int, str]:
    """NVML can report a recently exited PID; retain previously verified ownership.

    Start ticks distinguish a live reused PID from the process we observed.
    Never accept an unobserved exited process as owned.
    """
    attribution = {}
    for pid in pids:
        started = process_start(pid)
        if started is None:
            attribution[pid] = "exited-owned" if pid in known else "unattributed-exited"
        elif descendant(pid, ancestor):
            known[pid] = started
            attribution[pid] = "owned"
        elif known.get(pid) == started:
            attribution[pid] = "owned-reparented"
        else:
            attribution[pid] = "foreign"
    return attribution


class Monitor:
    """Record each observation and reject unowned compute on selected devices."""

    def __init__(self, output: Path, reservation: dict | None, gpu_index: int | None):
        self.output = output
        self.reservation = reservation
        self.gpu_index = gpu_index
        self.owned_processes: dict[int, int] = {}

    def __call__(self, process_pid: int) -> None:
        observation = selected_snapshot(gpu_snapshot(), self.gpu_index)
        attribution = attribute_gpu_processes(
            observation["compute_pids"], process_pid, self.owned_processes
        )
        observation["attribution"] = attribution
        with self.output.open("a") as stream:
            stream.write(json.dumps(observation) + "\n")
        if "foreign" in attribution.values():
            raise RuntimeError("foreign GPU process observed; attempt contaminated")
        if "unattributed-exited" in attribution.values():
            raise RuntimeError(
                "GPU process exited before ownership could be established"
            )
        if self.reservation and datetime.now(timezone.utc) >= datetime.fromisoformat(
            self.reservation["ends_at"]
        ):
            raise RuntimeError("reservation expired during execution")
