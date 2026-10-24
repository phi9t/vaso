"""Read-only lease-file validation shared by reservation and readiness checks."""

from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Any


def safe_name(resource: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", resource)


def valid_pid(value: Any) -> bool:
    return type(value) is int and 0 < value < 2**31


def read_state(path: Path) -> dict[str, Any]:
    """Read and validate an existing lease file without changing coordination state."""
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(state, dict) or state.get("schema_version") != 1:
            raise ValueError("expected a schema-version-1 lease object")
        recorded_resource = state.get("resource")
        if (
            not isinstance(recorded_resource, str)
            or safe_name(recorded_resource) != path.stem
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
            if safe_name(lease["resource"]) != path.stem:
                raise ValueError(
                    f"lease {index}: resource does not match the state filename"
                )
            if not valid_pid(lease.get("pid")):
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
