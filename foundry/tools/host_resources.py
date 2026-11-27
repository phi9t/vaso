#!/usr/bin/env python3
"""Read a Linux owner's visible resource limits; this is not an admission decision.

Only mounted cgroup ancestors are read. No namespaces are entered, no cgroups
are changed, and absent or inaccessible limits are never called unlimited.
The JSON omits host names, process IDs, cgroup paths, and environment contents.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import errno
import json
import os
from pathlib import Path, PurePosixPath
import re
import sys

SCHEMA = "vaso.host-resources.v1"
CONTROLLERS = {"cpu", "cpuacct", "cpuset", "memory"}
V2_FILES = (
    "cpu.max", "cpu.stat", "cpuset.cpus.effective",
    "memory.max", "memory.high", "memory.current", "memory.stat", "memory.events",
)
V1_FILES = {
    "cpu": ("cpu.cfs_quota_us", "cpu.cfs_period_us", "cpu.stat"),
    "cpuset": ("cpuset.effective_cpus", "cpuset.cpus"),
    "memory": ("memory.limit_in_bytes", "memory.usage_in_bytes", "memory.use_hierarchy",
               "memory.soft_limit_in_bytes", "memory.stat", "memory.failcnt", "memory.oom_control"),
}
MAX_FILE_BYTES = 1 << 16


def unescape(value: str) -> str:
    return re.sub(r"\\([0-7]{3})", lambda m: chr(int(m[1], 8)), value)


def absolute_parts(value: str) -> PurePosixPath:
    path = PurePosixPath(value)
    if not path.is_absolute() or ".." in path.parts or "\0" in value:
        raise ValueError("unsafe-cgroup-path")
    return path


def memberships(text: str) -> list[dict]:
    result = []
    for line in text.splitlines():
        hierarchy, controllers, path = line.split(":", 2)
        if not hierarchy.isdecimal():
            raise ValueError("invalid-membership")
        result.append({"controllers": set(filter(None, controllers.split(","))), "path": unescape(path)})
    return result


def mounts(text: str) -> list[dict]:
    result = []
    for line in text.splitlines():
        left, right = line.split(" - ", 1)
        fields, fs = left.split(), right.split()
        if len(fields) < 6 or len(fs) < 3:
            raise ValueError("invalid-mountinfo")
        if fs[0] not in ("cgroup", "cgroup2"):
            continue
        controllers = set(fs[2].split(",")) & CONTROLLERS
        if fs[0] == "cgroup" and not controllers:
            continue
        result.append({"version": 2 if fs[0] == "cgroup2" else 1,
                       "root": unescape(fields[3]), "point": unescape(fields[4]),
                       "controllers": controllers})
    return result


def map_membership(mount: dict, membership: dict) -> tuple[str, ...]:
    root = absolute_parts(mount["root"])
    absolute_parts(mount["point"])
    member = absolute_parts(membership["path"])
    try:
        relative = member.relative_to(root)
    except ValueError as exc:
        # A namespace-relative "/" is not sufficient evidence to guess a
        # mapping into an unrelated mounted subtree.
        raise ValueError("unmappable-membership") from exc
    return relative.parts


def error_state(exc: OSError) -> str:
    if isinstance(exc, PermissionError):
        return "denied"
    if isinstance(exc, FileNotFoundError):
        return "missing"
    if exc.errno in (errno.ELOOP, errno.ENOTDIR):
        return "symlink-or-nondirectory-rejected"
    return "unreadable"


def read_ancestor(point: str, parts: tuple[str, ...], names: tuple[str, ...]) -> dict:
    """Open descendants relative to one mount fd, without following symlinks."""
    if any(part in ("..", ".", "") or "/" in part for part in parts):
        return {"directory_status": "unsafe-path", "files": {}}
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW
    directory = None
    try:
        directory = os.open(point, flags)
        for part in parts:
            child = os.open(part, flags, dir_fd=directory)
            os.close(directory)
            directory = child
    except OSError as exc:
        if directory is not None:
            os.close(directory)
        return {"directory_status": error_state(exc), "files": {}}
    files = {}
    try:
        for name in names:
            try:
                fd = os.open(name, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW, dir_fd=directory)
                with os.fdopen(fd, "r", encoding="ascii") as stream:
                    value = stream.read(MAX_FILE_BYTES + 1)
                if len(value) > MAX_FILE_BYTES:
                    files[name] = {"status": "too-large"}
                else:
                    files[name] = {"status": "ok", "value": value.strip()}
            except (UnicodeError, ValueError):
                files[name] = {"status": "invalid"}
            except OSError as exc:
                files[name] = {"status": error_state(exc)}
    finally:
        os.close(directory)
    return {"directory_status": "ok", "files": files}


def cpu_set(value: str) -> set[int]:
    result = set()
    if not value:
        return result
    for item in value.split(","):
        match = re.fullmatch(r"([0-9]+)(?:-([0-9]+))?", item)
        if not match:
            raise ValueError("invalid-cpuset")
        first, last = int(match[1]), int(match[2] or match[1])
        if last < first or last - first + 1 > 65536 or len(result) + last - first + 1 > 65536:
            raise ValueError("invalid-or-too-large-cpuset")
        result.update(range(first, last + 1))
    return result


def nonnegative(value: str) -> int:
    if not re.fullmatch(r"[0-9]+", value):
        raise ValueError("invalid-integer")
    return int(value)


def limit(value: str, *, legacy: bool = False) -> int | None:
    if value == "max":
        return None
    number = nonnegative(value)
    # v1 commonly uses a page-rounded LONG_MAX encoding. Retain the raw
    # observation; a value this large is not usable physical capacity.
    return None if legacy and number >= 1 << 60 else number


def counters(value: str) -> dict[str, int]:
    result = {}
    for line in value.splitlines():
        key, number = line.split()
        if key in result:
            raise ValueError("duplicate-counter")
        result[key] = nonnegative(number)
    return result


def minimum(values: list[int | float]) -> int | float | None:
    return min(values) if values else None


def inspect_visible(member_text: str, mount_text: str, affinity: set[int] | None,
                    memory: dict, *, reader=read_ancestor) -> dict:
    result = {
        "schema": SCHEMA, "status": "partial", "admission": "not-evaluated",
        "scope": {"ancestors": "visible-mounts-only", "complete": False,
                  "hidden_ancestors": "unknown"},
        "affinity_cpus": len(affinity) if affinity is not None else None,
        "memory": memory, "hierarchies": [], "issues": [],
    }
    quota_values, max_values, hard_headrooms, high_values, high_headrooms = [], [], [], [], []
    cpus = set(affinity) if affinity is not None else None
    quota_observed = max_observed = cpuset_observed = False
    hard_headroom_unknown = False
    try:
        member_list, mount_list = memberships(member_text), mounts(mount_text)
    except ValueError:
        member_list, mount_list = [], []
        result["issues"].append("invalid-proc-cgroup-metadata")
    for index, mount in enumerate(mount_list):
        match = next((member for member in member_list
                      if (mount["version"] == 2 and not member["controllers"]) or
                      (mount["version"] == 1 and member["controllers"] & mount["controllers"])), None)
        row = {"index": index, "version": mount["version"], "mapping": "unknown",
               "controllers": sorted(mount["controllers"]), "ancestors": []}
        result["hierarchies"].append(row)
        if match is None:
            row["mapping"] = "membership-unavailable"
            result["issues"].append("membership-unavailable")
            continue
        try:
            relative = map_membership(mount, match)
        except ValueError as exc:
            row["mapping"] = str(exc)
            result["issues"].append(str(exc))
            continue
        row["mapping"] = "mapped"
        row["hidden_ancestors"] = "present" if mount["root"] != "/" else "unknown"
        names = V2_FILES if mount["version"] == 2 else tuple(
            name for controller, fields in V1_FILES.items()
            if controller in mount["controllers"] for name in fields)
        for depth in range(len(relative) + 1):
            parts = relative[:len(relative) - depth]
            record = reader(mount["point"], parts, names)
            record["depth"] = depth
            row["ancestors"].append(record)
            if record["directory_status"] != "ok":
                result["issues"].append("ancestor-" + record["directory_status"])
            files = record["files"]

            def parsed(name, parser):
                entry = files.get(name, {"status": "missing"})
                if entry["status"] != "ok":
                    return None, False
                try:
                    return parser(entry["value"]), True
                except (ValueError, OverflowError):
                    entry["status"] = "invalid"
                    entry.pop("value", None)
                    return None, False

            if mount["version"] == 2:
                def parse_quota(value):
                    quota, period = value.split()
                    period_number = nonnegative(period)
                    if period_number == 0:
                        raise ValueError("zero-period")
                    quota_number = limit(quota)
                    if quota_number == 0:
                        raise ValueError("zero-quota")
                    return None if quota_number is None else quota_number / period_number
                quota, known = parsed("cpu.max", parse_quota)
                cpuset, have_cpuset = parsed("cpuset.cpus.effective", cpu_set)
                cap, have_cap = parsed("memory.max", limit)
                used, have_used = parsed("memory.current", nonnegative)
                high, have_high = parsed("memory.high", limit)
                apply_memory = True
            else:
                q, have_q = parsed("cpu.cfs_quota_us", int)
                period, have_period = parsed("cpu.cfs_period_us", nonnegative)
                known = have_q and have_period and period > 0 and (q == -1 or q > 0)
                quota = q / period if known and q > 0 else None
                cpuset, have_cpuset = parsed("cpuset.effective_cpus", cpu_set)
                if not have_cpuset and files.get("cpuset.effective_cpus", {}).get("status") == "missing":
                    cpuset, have_cpuset = parsed("cpuset.cpus", cpu_set)
                    if have_cpuset and cpuset:
                        record["cpuset_source"] = "configured-v1-fallback"
                    elif have_cpuset:
                        # Ordinary v1 rejects empty task cpusets; v2-mode and
                        # hotplug can differ. A missing effective file cannot
                        # establish inheritance or a zero runnable-CPU mask.
                        record["cpuset_source"] = "empty-configured-v1-unknown"
                        result["issues"].append("empty-configured-v1-cpuset-unknown")
                        cpuset, have_cpuset = None, False
                cap, have_cap = parsed("memory.limit_in_bytes", lambda v: limit(v, legacy=True))
                used, have_used = parsed("memory.usage_in_bytes", nonnegative)
                hierarchy, have_hierarchy = parsed("memory.use_hierarchy", nonnegative)
                apply_memory = depth == 0 or not (have_hierarchy and hierarchy == 0)
                if depth and "memory" in mount["controllers"] and not have_hierarchy:
                    result["issues"].append("v1-memory-hierarchy-unknown")
                if not apply_memory:
                    record["memory_limit_applies_to_target"] = False
                high, have_high = None, False  # v1 soft limits are not v2 memory.high.
                parsed("memory.soft_limit_in_bytes", lambda v: limit(v, legacy=True))
                parsed("memory.failcnt", nonnegative)
                parsed("memory.oom_control", counters)

            quota_observed |= known
            if quota is not None:
                quota_values.append(quota)
            if have_cpuset:
                cpuset_observed = True
                cpus = set(cpuset) if cpus is None else cpus & cpuset
            if have_cap and apply_memory:
                max_observed = True
                if cap is not None:
                    max_values.append(cap)
                    if have_used:
                        hard_headrooms.append(max(0, cap - used))
                    else:
                        hard_headroom_unknown = True
            if have_high and high is not None:
                high_values.append(high)
                if have_used:
                    high_headrooms.append(max(0, high - used))
            for name in ("cpu.stat", "memory.stat", "memory.events"):
                value, known_stat = parsed(name, counters)
                if known_stat:
                    record[name] = value
            for name, entry in files.items():
                if entry["status"] not in ("ok", "missing"):
                    result["issues"].append(name + ":" + entry["status"])
            record["cpu_quota_cores"] = quota
            record["memory_hard_headroom_bytes"] = (
                max(0, cap - used) if apply_memory and have_cap and cap is not None and have_used else None)
            record["memory_high_advisory_headroom_bytes"] = (
                max(0, high - used) if have_high and high is not None and have_used else None)

    mapped = [row for row in result["hierarchies"] if row["mapping"] == "mapped"]
    if mapped and all(row["hidden_ancestors"] == "present" for row in mapped):
        result["scope"]["hidden_ancestors"] = "present"
    if not result["hierarchies"]:
        result["issues"].append("no-matching-visible-cgroup-mount")
    if not quota_observed:
        result["issues"].append("cpu-quota-unknown")
    if not max_observed:
        result["issues"].append("memory-limit-unknown")
    if not cpuset_observed:
        result["issues"].append("effective-cpuset-unknown")
    if affinity is None:
        result["issues"].append("affinity-unknown")
    if hard_headroom_unknown:
        result["issues"].append("memory-current-unknown-for-visible-limit")
    candidates = quota_values + ([len(cpus)] if cpus is not None else [])
    mem_caps = max_values + ([memory["MemTotal"]] if "MemTotal" in memory else [])
    mem_free = hard_headrooms + ([memory["MemAvailable"]] if "MemAvailable" in memory else [])
    result.update(
        effective_visible_cpus=minimum(candidates),
        affinity_cpuset_intersection_cpus=(len(cpus) if affinity is not None and cpuset_observed else None),
        minimum_visible_cpu_quota_cores=minimum(quota_values),
        effective_visible_memory_limit_bytes=minimum(mem_caps),
        effective_visible_memory_headroom_bytes=None if hard_headroom_unknown else minimum(mem_free),
        minimum_visible_memory_max_bytes=minimum(max_values),
        minimum_visible_memory_high_bytes=minimum(high_values),
        minimum_visible_memory_high_advisory_headroom_bytes=minimum(high_headrooms),
        limit_observation={"cpu_quota": "observed" if quota_observed else "unknown",
                           "memory_max": "observed" if max_observed else "unknown",
                           "cpuset": "observed" if cpuset_observed else "unknown"},
    )
    result["issues"] = sorted(set(result["issues"]))
    return result


def proc_text(path: Path) -> str:
    with path.open() as stream:
        text = stream.read((1 << 20) + 1)
    if len(text) > 1 << 20:
        raise ValueError("proc-file-too-large")
    return text


def process_identity(proc: Path, pid: int) -> tuple:
    ns = (proc / str(pid) / "ns/mnt").stat()
    # The comm field can contain spaces and closing parentheses.
    fields = proc_text(proc / str(pid) / "stat").rsplit(")", 1)[1].split()
    return ns.st_dev, ns.st_ino, int(fields[19])


def probe(pid: int | None = None, *, proc: Path = Path("/proc"),
          affinity_reader=os.sched_getaffinity) -> dict:
    target = os.getpid() if pid is None else pid
    unavailable = {"schema": SCHEMA, "status": "unavailable", "admission": "not-evaluated",
                   "scope": {"ancestors": "visible-mounts-only", "complete": False,
                             "hidden_ancestors": "unknown"}, "issues": []}
    try:
        current_ns = (proc / "self/ns/mnt").stat()
        before = process_identity(proc, target)
    except (OSError, ValueError, IndexError):
        unavailable["issues"] = ["process-or-mount-namespace-identity-unavailable"]
        return unavailable
    if before[:2] != (current_ns.st_dev, current_ns.st_ino):
        unavailable["issues"] = ["different-mount-namespace-rejected"]
        return unavailable
    try:
        member_text = proc_text(proc / str(target) / "cgroup")
        mount_text = proc_text(proc / "self/mountinfo")
        meminfo = {}
        for line in proc_text(proc / "meminfo").splitlines():
            name, _, text = line.partition(":")
            if name in ("MemTotal", "MemAvailable"):
                value, unit = text.split()
                if unit != "kB":
                    raise ValueError("unexpected-meminfo-unit")
                meminfo[name] = nonnegative(value) * 1024
    except (OSError, ValueError):
        unavailable["issues"] = ["proc-resource-metadata-unavailable"]
        return unavailable
    try:
        affinity = set(affinity_reader(target))
    except OSError:
        affinity = None
    result = inspect_visible(member_text, mount_text, affinity, meminfo)
    try:
        if (process_identity(proc, target) != before or
                proc_text(proc / str(target) / "cgroup") != member_text):
            unavailable["issues"] = ["target-changed-during-observation"]
            return unavailable
    except (OSError, ValueError, IndexError):
        unavailable["issues"] = ["target-disappeared-during-observation"]
        return unavailable
    result["observed_at"] = datetime.now(timezone.utc).isoformat()
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pid", type=int, help="host owner PID in the same visible mount namespace")
    args = parser.parse_args(argv)
    if args.pid is not None and args.pid <= 0:
        parser.error("--pid must be positive")
    result = probe(args.pid)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 2 if result["status"] == "unavailable" else 0


if __name__ == "__main__":
    raise SystemExit(main())
