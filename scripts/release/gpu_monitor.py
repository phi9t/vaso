"""Physical GPU preflight, exact device leases, and live process ownership.

These checks implement release qualification's reservation policy. The nightly
memory-threshold wait is a separate readiness check, not qualification proof.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
import csv
import io
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

    return {
        "observed_at": now(),
        **parse_snapshot(
            query(["--query-gpu=index,uuid,name,utilization.gpu,memory.used"]),
            query(["--query-compute-apps=gpu_uuid,pid"]),
        ),
    }


def _query_rows(text: str, query: str, columns: int) -> Iterator[tuple[int, list[str]]]:
    rows = csv.reader(io.StringIO(text), skipinitialspace=True, strict=True)
    try:
        for row in rows:
            if not row:
                continue
            if len(row) != columns:
                raise ValueError(
                    f"GPU {query} query row {rows.line_num}: expected {columns} columns"
                )
            yield rows.line_num, [value.strip() for value in row]
    except csv.Error:
        raise ValueError(
            f"GPU {query} query row {rows.line_num}: invalid CSV"
        ) from None


def parse_snapshot(devices_csv: str, processes_csv: str) -> dict:
    """Validate query data and replace private device UUIDs with physical indices."""
    devices = []
    uuid_indices = {}
    indices = set()
    for row, values in _query_rows(devices_csv, "device", 5):
        index_text, device_uuid, name, utilization_text, memory_text = values
        problem = f"GPU device query row {row}"
        try:
            index, utilization, memory = map(
                int, (index_text, utilization_text, memory_text)
            )
        except ValueError:
            raise ValueError(
                f"{problem}: index, utilization and memory must be integers"
            ) from None
        if index < 0 or not 0 <= utilization <= 100 or memory < 0:
            raise ValueError(f"{problem}: invalid index, utilization or memory range")
        if not device_uuid or not name:
            raise ValueError(f"{problem}: device identity is missing")
        if index in indices or device_uuid in uuid_indices:
            raise ValueError(f"{problem}: duplicate device identity")
        indices.add(index)
        uuid_indices[device_uuid] = index
        devices.append(
            {
                "index": index,
                "name": name,
                "utilization": utilization,
                "memory_mib": memory,
            }
        )
    processes = []
    for row, (device_uuid, pid_text) in _query_rows(processes_csv, "process", 2):
        problem = f"GPU process query row {row}"
        if device_uuid not in uuid_indices:
            raise ValueError(f"{problem}: device absent from device query")
        try:
            pid = int(pid_text)
        except ValueError:
            raise ValueError(f"{problem}: PID must be a positive integer") from None
        if pid <= 0:
            raise ValueError(f"{problem}: PID must be a positive integer")
        processes.append({"gpu_index": uuid_indices[device_uuid], "pid": pid})
    return {
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
