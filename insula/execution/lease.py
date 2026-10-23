#!/usr/bin/env python3
"""Lease shared insula resources across agents.

The lease state is deliberately plain JSON under
``$VASO_ESTATE_ROOT/agents/leases``.  A short flock guards every state
transition; long-running holders are represented by records containing holder,
pid, host, start and expiry, so a later acquire can reclaim expired or dead
holders.
"""

from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import errno
import fcntl
import json
import math
import os
import re
import secrets
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Iterator


EX_UNAVAILABLE = 75
DEFAULT_TTL_SECONDS = 24 * 60 * 60
LEASE_ENV_KEYS = ("CUDA_VISIBLE_DEVICES", "VASO_GPU_SET", "VASO_HOST_CPU_JOBS")


def _now() -> float:
    return time.time()


def _stamp(value: float) -> str:
    return dt.datetime.fromtimestamp(value, dt.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


def _estate_root() -> Path:
    raw = os.environ.get("VASO_ESTATE_ROOT")
    if not raw:
        raise SystemExit("VASO_ESTATE_ROOT must be set")
    return Path(raw)


def lease_dir() -> Path:
    path = _estate_root() / "agents" / "leases"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _safe_name(resource: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", resource)


def _state_path(resource: str) -> Path:
    return lease_dir() / f"{_safe_name(resource)}.json"


def _lock_path(resource: str) -> Path:
    return lease_dir() / f"{_safe_name(resource)}.lock"


@contextlib.contextmanager
def _locked(resource: str) -> Iterator[None]:
    path = _lock_path(resource)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _valid_pid(value: Any) -> bool:
    return type(value) is int and 0 < value < 2**31


def _load_state(resource: str) -> dict[str, Any]:
    path = _state_path(resource)
    if not path.exists():
        return {"schema_version": 1, "resource": resource, "leases": []}
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(state, dict) or state.get("schema_version") != 1:
            raise ValueError("expected a schema-version-1 lease object")
        recorded_resource = state.get("resource")
        if (
            not isinstance(recorded_resource, str)
            or _safe_name(recorded_resource) != path.stem
        ):
            raise ValueError("resource does not match the state filename")
        leases = state.get("leases")
        if not isinstance(leases, list):
            raise ValueError("expected a leases list")
        for index, lease in enumerate(leases):
            if not isinstance(lease, dict):
                raise ValueError(f"lease {index}: expected an object")
            for field in ("id", "host", "resource"):
                if not isinstance(lease.get(field), str) or not lease[field]:
                    raise ValueError(f"lease {index}: missing {field}")
            if _safe_name(lease["resource"]) != path.stem:
                raise ValueError(
                    f"lease {index}: resource does not match the state filename"
                )
            if not _valid_pid(lease.get("pid")):
                raise ValueError(f"lease {index}: invalid holder PID")
            expiry = lease.get("expiry_time")
            if type(expiry) not in (int, float) or not math.isfinite(expiry):
                raise ValueError(f"lease {index}: expiry_time must be finite")
            if recorded_resource == "gpu":
                devices = lease.get("devices")
                if (
                    not isinstance(devices, list)
                    or not devices
                    or any(type(device) is not int or device < 0 for device in devices)
                    or len(set(devices)) != len(devices)
                ):
                    raise ValueError(f"lease {index}: invalid GPU devices")
    except (ValueError, TypeError, KeyError, OverflowError) as exc:
        raise ValueError(f"invalid lease state {path}: {exc}") from exc
    return state


def _resources(resource: str | None) -> list[str]:
    """Resolve canonical resources without hiding unreadable state files."""
    if resource is not None:
        return [resource]
    return [
        _load_state(path.stem)["resource"]
        for path in sorted(lease_dir().glob("*.json"))
    ]


def _write_state(resource: str, state: dict[str, Any]) -> None:
    state["updated_at"] = _stamp(_now())
    path = _state_path(resource)
    tmp = path.with_name(f"{path.name}.tmp.{os.getpid()}")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _pid_alive(pid: int, host: str) -> bool:
    if pid <= 0:
        return False
    if host and host != socket.gethostname():
        return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError as exc:
        return exc.errno != errno.ESRCH
    return True


def _active_leases(leases: list[dict[str, Any]], now: float) -> list[dict[str, Any]]:
    active: list[dict[str, Any]] = []
    for lease in leases:
        pid = int(lease["pid"])
        expiry = float(lease["expiry_time"])
        if expiry <= now:
            continue
        if not _pid_alive(pid, str(lease.get("host", ""))):
            continue
        active.append(lease)
    return active


def _new_record(args: argparse.Namespace, resource: str, ttl: float) -> dict[str, Any]:
    now = _now()
    lease_id = f"{_safe_name(resource)}-{secrets.token_hex(8)}"
    holder = (
        args.holder
        or os.environ.get("VASO_AGENT")
        or os.environ.get("USER")
        or "unknown"
    )
    pid = args.pid if args.pid is not None else os.getppid()
    expiry = now + ttl
    return {
        "id": lease_id,
        "resource": resource,
        "holder": holder,
        "pid": int(pid),
        "host": socket.gethostname(),
        "start_time": now,
        "started_at": _stamp(now),
        "expiry_time": expiry,
        "expires_at": _stamp(expiry),
    }


def _gpu_capacity(override: int | None) -> int:
    if override is not None:
        if override < 0:
            raise ValueError("--capacity must be nonnegative")
        return override
    raw = os.environ.get("VASO_GPU_COUNT")
    if raw is not None:
        try:
            capacity = int(raw)
        except ValueError:
            raise ValueError("VASO_GPU_COUNT must be a nonnegative integer") from None
        if capacity < 0:
            raise ValueError("VASO_GPU_COUNT must be a nonnegative integer")
        return capacity
    hint = "cannot determine GPU capacity with nvidia-smi; supply --capacity or VASO_GPU_COUNT"
    try:
        result = subprocess.run(
            ["nvidia-smi", "-L"],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ValueError(hint) from exc
    if result.returncode != 0:
        raise ValueError(hint)
    return sum(
        1 for line in result.stdout.splitlines() if line.strip().startswith("GPU ")
    )


def _cpu_budget(override: int | None) -> int:
    if override is not None:
        return max(1, override)
    raw = os.environ.get("VASO_HOST_CPU_BUDGET")
    if raw:
        try:
            return max(1, int(raw))
        except ValueError:
            pass
    return max(1, os.cpu_count() or 1)


def _recompute_cpu_jobs(state: dict[str, Any], budget: int) -> None:
    leases = state.get("leases", [])
    share = max(1, budget // max(1, len(leases)))
    state["budget"] = budget
    for lease in leases:
        lease["jobs"] = share
        lease["env"] = {"VASO_HOST_CPU_JOBS": str(share)}


def _try_acquire(args: argparse.Namespace) -> tuple[dict[str, Any] | None, str | None]:
    resource = args.resource
    if args.devices is not None and resource != "gpu":
        return None, "--devices applies only to gpu leases"
    ttl = args.ttl if args.ttl is not None else DEFAULT_TTL_SECONDS
    if not math.isfinite(ttl) or ttl <= 0:
        return None, "--ttl must be finite and positive"
    if args.pid is not None and not _valid_pid(args.pid):
        return None, "--pid must be a positive process ID below 2**31"

    if resource == "gpu":
        amount = args.amount if args.amount is not None else 1
        if amount <= 0:
            return None, "--amount must be positive for gpu leases"
        capacity = _gpu_capacity(args.capacity)
        if amount > capacity:
            return None, f"requested {amount} GPUs but capacity is {capacity}"
        requested = None
        if args.devices is not None:
            try:
                requested = [int(value) for value in args.devices.split(",")]
            except ValueError:
                return None, "--devices must contain comma-separated GPU indices"
            if (
                len(requested) != amount
                or len(set(requested)) != amount
                or any(device < 0 or device >= capacity for device in requested)
            ):
                return (
                    None,
                    "--devices must name --amount distinct indices within GPU capacity",
                )

    with _locked(resource):
        state = _load_state(resource)
        state["leases"] = _active_leases(list(state.get("leases", [])), _now())

        if resource.startswith("insula:") or resource == "network-fetch":
            if state["leases"]:
                _write_state(resource, state)
                return None, None
            lease = _new_record(args, resource, ttl)
            state["leases"].append(lease)
            _write_state(resource, state)
            return lease, None

        if resource == "gpu":
            state["capacity"] = capacity
            used = {
                int(device)
                for lease in state["leases"]
                for device in lease.get("devices", [])
            }
            free = [
                device
                for device in (requested if requested is not None else range(capacity))
                if device not in used
            ]
            if len(free) < amount:
                _write_state(resource, state)
                return None, None
            devices = free[:amount]
            visible = ",".join(str(device) for device in devices)
            lease = _new_record(args, resource, ttl)
            lease["amount"] = amount
            lease["devices"] = devices
            lease["env"] = {
                "CUDA_VISIBLE_DEVICES": visible,
                "VASO_GPU_SET": visible,
            }
            state["leases"].append(lease)
            _write_state(resource, state)
            return lease, None

        if resource == "host-cpu":
            budget = _cpu_budget(args.budget)
            lease = _new_record(args, resource, ttl)
            state["leases"].append(lease)
            _recompute_cpu_jobs(state, budget)
            _write_state(resource, state)
            return lease, None

        return None, f"unknown resource: {resource}"


def acquire(args: argparse.Namespace) -> int:
    if not math.isfinite(args.timeout):
        print("--timeout must be finite", file=sys.stderr)
        return 2
    start = time.monotonic()
    timeout = max(0.0, args.timeout)
    while True:
        lease, error = _try_acquire(args)
        if error is not None:
            print(error, file=sys.stderr)
            return 2
        if lease is not None:
            print(json.dumps(lease, sort_keys=True))
            return 0
        if time.monotonic() - start >= timeout:
            print(f"timed out acquiring {args.resource}", file=sys.stderr)
            return EX_UNAVAILABLE
        time.sleep(min(0.1, max(0.01, timeout - (time.monotonic() - start))))


def release(args: argparse.Namespace) -> int:
    released: list[dict[str, Any]] = []
    for resource in _resources(args.resource):
        with _locked(resource):
            state = _load_state(resource)
            before = list(state.get("leases", []))
            kept = [lease for lease in before if lease.get("id") != args.id]
            removed = [lease for lease in before if lease.get("id") == args.id]
            if removed:
                state["leases"] = kept
                if resource == "host-cpu":
                    _recompute_cpu_jobs(
                        state, int(state.get("budget") or _cpu_budget(args.budget))
                    )
                _write_state(resource, state)
                released.extend(removed)

    print(json.dumps({"released": bool(released), "leases": released}, sort_keys=True))
    return 0


def check(args: argparse.Namespace) -> int:
    resources = _resources(args.resource)

    found: dict[str, Any] | None = None
    for resource in resources:
        with _locked(resource):
            state = _load_state(resource)
            state["leases"] = _active_leases(list(state.get("leases", [])), _now())
            if resource == "host-cpu":
                _recompute_cpu_jobs(
                    state, int(state.get("budget") or _cpu_budget(args.budget))
                )
            _write_state(resource, state)
            for lease in state.get("leases", []):
                if lease.get("id") == args.id:
                    found = lease
                    break
        if found is not None:
            break

    doc: dict[str, Any] = {
        "id": args.id,
        "live": found is not None,
    }
    if args.resource:
        doc["resource"] = args.resource
    if found is not None:
        doc["lease"] = found
    print(json.dumps(doc, sort_keys=True))
    return 0 if found is not None else EX_UNAVAILABLE


def list_leases(args: argparse.Namespace) -> int:
    resources = _resources(args.resource)

    leases: list[dict[str, Any]] = []
    for resource in resources:
        with _locked(resource):
            state = _load_state(resource)
            state["leases"] = _active_leases(list(state.get("leases", [])), _now())
            if resource == "host-cpu":
                _recompute_cpu_jobs(
                    state, int(state.get("budget") or _cpu_budget(args.budget))
                )
            _write_state(resource, state)
            leases.extend(state.get("leases", []))
    print(json.dumps({"leases": leases}, sort_keys=True))
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    acquire_parser = sub.add_parser("acquire")
    acquire_parser.add_argument("--resource", required=True)
    acquire_parser.add_argument("--holder")
    acquire_parser.add_argument("--pid", type=int)
    acquire_parser.add_argument("--ttl", type=float)
    acquire_parser.add_argument("--timeout", type=float, default=0.0)
    acquire_parser.add_argument("--amount", type=int)
    acquire_parser.add_argument("--capacity", type=int)
    acquire_parser.add_argument(
        "--devices", help="exact comma-separated physical GPU indices; no fallback"
    )
    acquire_parser.add_argument("--budget", type=int)
    acquire_parser.set_defaults(func=acquire)

    release_parser = sub.add_parser("release")
    release_parser.add_argument("--id", required=True)
    release_parser.add_argument("--resource")
    release_parser.add_argument("--budget", type=int)
    release_parser.set_defaults(func=release)

    check_parser = sub.add_parser("check")
    check_parser.add_argument("--id", required=True)
    check_parser.add_argument("--resource")
    check_parser.add_argument("--budget", type=int)
    check_parser.set_defaults(func=check)

    list_parser = sub.add_parser("list")
    list_parser.add_argument("--resource")
    list_parser.add_argument("--budget", type=int)
    list_parser.set_defaults(func=list_leases)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        return int(args.func(args))
    except (OSError, ValueError) as exc:
        print(f"lease: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
