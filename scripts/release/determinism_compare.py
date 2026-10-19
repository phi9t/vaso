#!/usr/bin/env python3
"""Compare two release wheelhouses for deterministic wheel contents."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any


NORMALIZED_RECORD_SHA = "sha256:normalized-record"


@dataclass(frozen=True)
class AllowlistEntry:
    profile: str
    line: str
    wheel: str
    path: str
    reason: str


@dataclass(frozen=True)
class DependencyAllowlistEntry:
    profile: str
    line: str
    dependency: str
    manifest: str
    reason: str


@dataclass(frozen=True)
class PrefixManifest:
    key: str
    dependency: str
    path: Path
    tree_sha256: str
    entries: list[dict[str, Any]]


def _sha256(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _is_record_path(path: str) -> bool:
    return path.endswith(".dist-info/RECORD") or ".dist-info/RECORD/" in path


def _wheel_files(path: Path) -> dict[str, str]:
    files: dict[str, str] = {}
    with zipfile.ZipFile(path) as zf:
        for info in sorted(zf.infolist(), key=lambda item: item.filename):
            name = info.filename
            if name.endswith("/"):
                continue
            if name in files:
                raise ValueError(f"{path.name} contains duplicate wheel member: {name}")
            if _is_record_path(name):
                files[name] = NORMALIZED_RECORD_SHA
            else:
                files[name] = _sha256(zf.read(info))
    return files


def _load_allowlist(path: Path) -> tuple[list[AllowlistEntry], list[DependencyAllowlistEntry]]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"allowlist is missing: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"allowlist is not valid JSON: {path}: {exc}") from exc
    if not isinstance(raw, dict) or raw.get("schema_version") != 1:
        raise ValueError("allowlist must be an object with schema_version=1")
    entries = raw.get("entries", [])
    if not isinstance(entries, list):
        raise ValueError("allowlist entries must be a list")
    dep_entries = raw.get("dep_entries", [])
    if not isinstance(dep_entries, list):
        raise ValueError("allowlist dep_entries must be a list")

    wheel_allowlist: list[AllowlistEntry] = []
    for idx, item in enumerate(entries):
        if not isinstance(item, dict):
            raise ValueError(f"allowlist entry {idx} must be an object")
        missing = [key for key in ("profile", "line", "wheel", "path", "reason") if not item.get(key)]
        if missing:
            raise ValueError(f"allowlist entry {idx} is missing non-empty field(s): {', '.join(missing)}")
        wheel_allowlist.append(
            AllowlistEntry(
                profile=str(item["profile"]),
                line=str(item["line"]),
                wheel=str(item["wheel"]),
                path=str(item["path"]),
                reason=str(item["reason"]),
            )
        )
    dep_allowlist: list[DependencyAllowlistEntry] = []
    for idx, item in enumerate(dep_entries):
        if not isinstance(item, dict):
            raise ValueError(f"allowlist dep_entries entry {idx} must be an object")
        missing = [key for key in ("profile", "line", "dependency", "reason") if not item.get(key)]
        if missing:
            raise ValueError(
                f"allowlist dep_entries entry {idx} is missing non-empty field(s): {', '.join(missing)}"
            )
        dep_allowlist.append(
            DependencyAllowlistEntry(
                profile=str(item["profile"]),
                line=str(item["line"]),
                dependency=str(item["dependency"]),
                manifest=str(item.get("manifest", "")),
                reason=str(item["reason"]),
            )
        )
    return wheel_allowlist, dep_allowlist


def _allowlist_index(entries: list[AllowlistEntry]) -> dict[tuple[str, str, str, str], str]:
    return {
        (entry.profile, entry.line, entry.wheel, entry.path): entry.reason
        for entry in entries
    }


def _dependency_allowlist_index(entries: list[DependencyAllowlistEntry]) -> dict[tuple[str, str, str, str], str]:
    return {
        (entry.profile, entry.line, entry.dependency, entry.manifest): entry.reason
        for entry in entries
    }


def _wheelhouse(path: Path) -> dict[str, Path]:
    if not path.is_dir():
        raise ValueError(f"wheelhouse is not a directory: {path}")
    wheels = {wheel.name: wheel for wheel in sorted(path.glob("*.whl"))}
    if not wheels:
        raise ValueError(f"wheelhouse contains no wheels: {path}")
    return wheels


def _normalize_dependency_name(value: str) -> str:
    name = value.strip("@")
    if name.startswith("+"):
        parts = [part for part in name.split("+") if part]
        if parts:
            name = parts[-1]
    for prefix in ("vaso_", "spack_"):
        if name.startswith(prefix):
            name = name[len(prefix):]
    if name.endswith("_native"):
        name = name[: -len("_native")]
    return name.replace("_", "-")


def _manifest_relative_path(path: Path, output_base: Path) -> str:
    try:
        return path.relative_to(output_base).as_posix()
    except ValueError:
        return path.as_posix()


def _dependency_name(path: Path, output_base: Path, document: dict[str, Any]) -> str:
    name = document.get("name")
    if isinstance(name, str) and name:
        return name
    rel_parts = Path(_manifest_relative_path(path, output_base)).parts
    if len(rel_parts) >= 3 and rel_parts[0] == "external":
        return _normalize_dependency_name(rel_parts[1])
    filename = path.name
    if filename == "prefix_manifest.json":
        return _normalize_dependency_name(path.parent.name)
    if filename.endswith("_prefix_manifest.json"):
        return _normalize_dependency_name(filename[: -len("_prefix_manifest.json")])
    return _normalize_dependency_name(path.stem)


def _load_prefix_manifest(path: Path, output_base: Path) -> PrefixManifest:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"prefix manifest is not valid JSON: {path}: {exc}") from exc
    if not isinstance(raw, dict) or raw.get("schema_version") != 1:
        raise ValueError(f"prefix manifest must be an object with schema_version=1: {path}")
    tree_sha256 = raw.get("tree_sha256")
    if not isinstance(tree_sha256, str) or not tree_sha256:
        raise ValueError(f"prefix manifest is missing tree_sha256: {path}")
    entries = raw.get("entries")
    if not isinstance(entries, list):
        raise ValueError(f"prefix manifest entries must be a list: {path}")
    for idx, entry in enumerate(entries):
        if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
            raise ValueError(f"prefix manifest entry {idx} is missing a path: {path}")
    return PrefixManifest(
        key=_manifest_relative_path(path, output_base),
        dependency=_dependency_name(path, output_base, raw),
        path=path,
        tree_sha256=tree_sha256,
        entries=entries,
    )


def _iter_action_prefix_manifest_paths(output_base: Path) -> list[Path]:
    bazel_out = output_base / "execroot" / "_main" / "bazel-out"
    if not bazel_out.is_dir():
        return []
    manifests: list[Path] = []
    for root, dirnames, filenames in os.walk(bazel_out, followlinks=False):
        dirnames[:] = sorted(dirnames)
        for filename in sorted(filenames):
            if filename.endswith("prefix_manifest.json"):
                manifests.append(Path(root) / filename)
    return manifests


def _prefix_manifests(output_base: Path) -> dict[str, PrefixManifest]:
    if not output_base.is_dir():
        raise ValueError(f"output base is not a directory: {output_base}")
    manifests: dict[str, PrefixManifest] = {}
    external = output_base / "external"
    if external.is_dir():
        for path in sorted(external.glob("*/prefix_manifest.json")):
            manifest = _load_prefix_manifest(path, output_base)
            manifests[manifest.key] = manifest
    for path in _iter_action_prefix_manifest_paths(output_base):
        manifest = _load_prefix_manifest(path, output_base)
        manifests[manifest.key] = manifest
    return manifests


def _entry_by_path(entries: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    by_path: dict[str, dict[str, Any]] = {}
    for entry in entries:
        path = str(entry["path"])
        if path in by_path:
            raise ValueError(f"prefix manifest contains duplicate entry path: {path}")
        by_path[path] = entry
    return by_path


def _entry_summary(entry: dict[str, Any] | None) -> dict[str, Any] | None:
    if entry is None:
        return None
    return {
        key: entry[key]
        for key in ("mode", "sha256", "size", "target", "type")
        if key in entry
    }


def _first_differing_entries(
    first: PrefixManifest | None,
    second: PrefixManifest | None,
    *,
    limit: int = 5,
) -> list[dict[str, Any]]:
    if first is None or second is None:
        return [
            {
                "first": "missing" if first is None else "present",
                "path": "<manifest>",
                "second": "missing" if second is None else "present",
            }
        ]

    first_entries = _entry_by_path(first.entries)
    second_entries = _entry_by_path(second.entries)
    differing: list[dict[str, Any]] = []
    for path in sorted(set(first_entries) | set(second_entries)):
        first_entry = first_entries.get(path)
        second_entry = second_entries.get(path)
        if first_entry == second_entry:
            continue
        differing.append(
            {
                "first": _entry_summary(first_entry),
                "path": path,
                "second": _entry_summary(second_entry),
            }
        )
        if len(differing) >= limit:
            break
    return differing


def _dependency_allowlist_reason(
    allowlist: dict[tuple[str, str, str, str], str],
    *,
    profile: str,
    line: str,
    dependency: str,
    manifest: str,
) -> str | None:
    return allowlist.get((profile, line, dependency, manifest)) or allowlist.get((profile, line, dependency, ""))


def _compare_dep_manifests(
    *,
    profile: str,
    line: str,
    first_output_base: Path,
    second_output_base: Path,
    allowlist: dict[tuple[str, str, str, str], str],
) -> dict[str, Any]:
    first_manifests = _prefix_manifests(first_output_base)
    second_manifests = _prefix_manifests(second_output_base)
    mismatched_deps: list[dict[str, Any]] = []
    unmaterialized_manifests: list[dict[str, Any]] = []

    for key in sorted(set(first_manifests) | set(second_manifests)):
        first_manifest = first_manifests.get(key)
        second_manifest = second_manifests.get(key)
        # A disk-cache hit does not write non-top-level outputs, so a manifest
        # can exist in one output base only: missing evidence, not a mismatch.
        if first_manifest is None or second_manifest is None:
            present = first_manifest or second_manifest
            unmaterialized_manifests.append(
                {
                    "dependency": present.dependency,
                    "manifest": key,
                    "present_in": "first" if first_manifest is not None else "second",
                }
            )
            continue
        first_tree = first_manifest.tree_sha256 if first_manifest else None
        second_tree = second_manifest.tree_sha256 if second_manifest else None
        if first_tree == second_tree:
            continue
        if first_manifest is not None:
            dependency = first_manifest.dependency
        elif second_manifest is not None:
            dependency = second_manifest.dependency
        else:
            dependency = _normalize_dependency_name(Path(key).parent.name)
        reason = _dependency_allowlist_reason(
            allowlist,
            profile=profile,
            line=line,
            dependency=dependency,
            manifest=key,
        )
        mismatched_deps.append(
            {
                "allowed": reason is not None,
                "dependency": dependency,
                "differing_files": _first_differing_entries(first_manifest, second_manifest),
                "first_manifest": first_manifest.key if first_manifest else None,
                "first_tree_sha256": first_tree,
                "reason": reason,
                "second_manifest": second_manifest.key if second_manifest else None,
                "second_tree_sha256": second_tree,
            }
        )

    return {
        "first_output_base": str(first_output_base),
        "identical": not mismatched_deps and not unmaterialized_manifests,
        "mismatched_deps": mismatched_deps,
        "second_output_base": str(second_output_base),
        "unmaterialized_manifests": unmaterialized_manifests,
    }


def compare_wheelhouses(
    *,
    profile: str,
    line: str,
    first: Path,
    second: Path,
    allowlist_path: Path,
    first_output_base: Path | None = None,
    second_output_base: Path | None = None,
) -> dict[str, Any]:
    wheel_entries, dep_entries = _load_allowlist(allowlist_path)
    allowlist = _allowlist_index(wheel_entries)
    dep_allowlist = _dependency_allowlist_index(dep_entries)
    first_wheels = _wheelhouse(first)
    second_wheels = _wheelhouse(second)
    differing_files: list[dict[str, Any]] = []

    for wheel_name in sorted(set(first_wheels) | set(second_wheels)):
        first_path = first_wheels.get(wheel_name)
        second_path = second_wheels.get(wheel_name)
        if first_path is None or second_path is None:
            path = "<wheel>"
            reason = allowlist.get((profile, line, wheel_name, path))
            differing_files.append(
                {
                    "allowed": reason is not None,
                    "first_sha256": _sha256(first_path.read_bytes()) if first_path else None,
                    "path": path,
                    "reason": reason,
                    "second_sha256": _sha256(second_path.read_bytes()) if second_path else None,
                    "wheel": wheel_name,
                }
            )
            continue

        first_files = _wheel_files(first_path)
        second_files = _wheel_files(second_path)
        for member in sorted(set(first_files) | set(second_files)):
            first_hash = first_files.get(member)
            second_hash = second_files.get(member)
            if first_hash == second_hash:
                continue
            reason = allowlist.get((profile, line, wheel_name, member))
            differing_files.append(
                {
                    "allowed": reason is not None,
                    "first_sha256": first_hash,
                    "path": member,
                    "reason": reason,
                    "second_sha256": second_hash,
                    "wheel": wheel_name,
                }
            )

    dep_determinism = {
        "first_output_base": str(first_output_base) if first_output_base else None,
        "identical": True,
        "mismatched_deps": [],
        "second_output_base": str(second_output_base) if second_output_base else None,
    }
    if (first_output_base is None) != (second_output_base is None):
        raise ValueError("--first-output-base and --second-output-base must be provided together")
    if first_output_base is not None and second_output_base is not None:
        dep_determinism = _compare_dep_manifests(
            profile=profile,
            line=line,
            first_output_base=first_output_base,
            second_output_base=second_output_base,
            allowlist=dep_allowlist,
        )

    return {
        "schema_version": 1,
        "profile": profile,
        "line": line,
        "first": str(first),
        "second": str(second),
        "identical": not differing_files and dep_determinism["identical"],
        "differing_files": differing_files,
        "dep_determinism": dep_determinism,
    }


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--line", required=True)
    parser.add_argument("--first", required=True, type=Path)
    parser.add_argument("--second", required=True, type=Path)
    parser.add_argument("--first-output-base", type=Path)
    parser.add_argument("--second-output-base", type=Path)
    parser.add_argument("--allowlist", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    if not os.environ.get("VASO_ESTATE_ROOT"):
        print("set VASO_ESTATE_ROOT", file=sys.stderr)
        return 2
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    try:
        result = compare_wheelhouses(
            profile=args.profile,
            line=args.line,
            first=args.first,
            second=args.second,
            allowlist_path=args.allowlist,
            first_output_base=args.first_output_base,
            second_output_base=args.second_output_base,
        )
    except (OSError, ValueError, zipfile.BadZipFile) as exc:
        print(f"determinism_compare: {exc}", file=sys.stderr)
        return 2

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    unallowed = [entry for entry in result["differing_files"] if not entry["allowed"]]
    unallowed.extend(entry for entry in result["dep_determinism"]["mismatched_deps"] if not entry["allowed"])
    missing = result["dep_determinism"].get("unmaterialized_manifests", [])
    if missing:
        print(f"determinism_compare: {len(missing)} dependency manifest(s) not materialized", file=sys.stderr)
    if unallowed:
        print(f"determinism_compare: {len(unallowed)} unallowlisted diff(s)", file=sys.stderr)
        return 1
    return 1 if missing else 0


if __name__ == "__main__":
    raise SystemExit(main())
