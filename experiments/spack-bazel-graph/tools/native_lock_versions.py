#!/usr/bin/env python3
"""Check committed Spack locks for native flips at another version.

A lock records `build: native` plus the native rule label for each flipped node.
If that rule builds a different version than the node (per native_overrides.json
provided_versions), the lock silently substitutes one version for another.

Tolerated substitutions:
- the live ones in native_overrides.json known_version_substitutions;
- STALE_EVIDENCE_LOCKS: historical capture locks generated before a native
  provider moved to another version. They stay as evidence of that capture, but
  must not be regenerated or consumed as-is.

An allowlist entry that no longer matches a lock is itself an error.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


# (lock file, package, locked version) -> why the substitution is historical.
STALE_EVIDENCE_LOCKS: dict[tuple[str, str, str], str] = {
    ("hwloc_spack_graph.lock.json", "libxml2", "2.15.3"): (
        "captured in 24ada0a before 05731a8 aligned @libxml2_native to hermetic Spack libxml2@2.13.9"
    ),
    ("protobuf_native_spack_graph.lock.json", "protobuf", "3.13.0"): (
        "captured in f9ebec2 before 21682c9 moved @protobuf_native to the PyTorch-aligned protobuf@21.12"
    ),
}


def lock_version_errors(
    overrides_path: Path,
    lock_paths: list[Path],
    *,
    stale_evidence: dict[tuple[str, str, str], str] = STALE_EVIDENCE_LOCKS,
) -> list[str]:
    overrides = json.loads(overrides_path.read_text(encoding="utf-8"))
    provided_versions: dict[str, str] = overrides.get("provided_versions", {})
    keys_by_label: dict[str, list[str]] = {}
    for key, label in overrides.get("native", {}).items():
        keys_by_label.setdefault(label, []).append(key)
    live_substitutions = {
        (entry.get("key"), entry.get("graph_version"), entry.get("provided"))
        for entry in overrides.get("known_version_substitutions", [])
    }

    errors: list[str] = []
    used_stale: set[tuple[str, str, str]] = set()
    for lock_path in sorted(lock_paths, key=lambda path: path.name):
        lock = json.loads(lock_path.read_text(encoding="utf-8"))
        for node in lock.get("packages", {}).values():
            if node.get("build") != "native":
                continue
            package = node.get("package", "")
            version = node.get("version", "")
            label = node.get("native_prefix", "")
            keys = keys_by_label.get(label)
            if not keys:
                errors.append(f"{lock_path.name}: {package}@{version} uses unknown native rule {label}")
                continue
            if any(provided_versions.get(key) == version for key in keys):
                continue
            if any((key, version, provided_versions.get(key)) in live_substitutions for key in keys):
                continue
            stale_key = (lock_path.name, package, version)
            if stale_key in stale_evidence:
                used_stale.add(stale_key)
                continue
            provided = ", ".join(sorted({provided_versions.get(key, "?") for key in keys}))
            errors.append(
                f"{lock_path.name}: {package}@{version} is flipped to {label}, "
                f"which builds {package}@{provided}"
            )
    for lock_name, package, version in sorted(set(stale_evidence) - used_stale):
        errors.append(
            f"stale STALE_EVIDENCE_LOCKS entry: {lock_name} {package}@{version} "
            "no longer matches a native flip"
        )
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--native-overrides", type=Path, required=True)
    parser.add_argument("locks", type=Path, nargs="+")
    args = parser.parse_args(argv)

    errors = lock_version_errors(args.native_overrides, args.locks)
    for error in errors:
        print(error, file=sys.stderr)
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
