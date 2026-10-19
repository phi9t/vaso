#!/usr/bin/env python3
"""Validate that first-party release wheels belong to one git commit."""

from __future__ import annotations

import argparse
import json
import re
import zipfile
from pathlib import Path
from typing import Any, Iterable


_GIT_SUFFIX = re.compile(r"(?:^|\.)g([0-9a-fA-F]{8})(?:\.|$)")


def is_vaso_release_wheel_name(name: str) -> bool:
    if not name.endswith(".whl"):
        return False
    try:
        version = _wheel_version(name)
    except ValueError:
        return False
    return "+vaso" in version


def _wheel_version(name: str) -> str:
    if not name.endswith(".whl"):
        raise ValueError(f"release artifact is not a wheel: {name}")
    parts = name[:-4].split("-")
    if len(parts) < 5:
        raise ValueError(f"invalid wheel filename: {name}")
    return parts[1]


def _version_git_suffix(name: str) -> str:
    version = _wheel_version(name)
    if "+vaso" not in version:
        raise ValueError(f"release wheel {name} is not a vaso release version")
    match = _GIT_SUFFIX.search(version)
    if match is None:
        raise ValueError(f"release wheel {name} is missing g<sha8> version suffix")
    return match.group(1).lower()


def _wheel_provenance(path: Path) -> dict[str, Any]:
    try:
        with zipfile.ZipFile(path) as wheel:
            names = sorted(name for name in wheel.namelist() if name.endswith(".dist-info/vaso_provenance.json"))
            if not names:
                raise ValueError(f"release wheel {path.name} has no vaso_provenance.json")
            if len(names) > 1:
                raise ValueError(f"release wheel {path.name} has multiple vaso_provenance.json files")
            with wheel.open(names[0]) as fh:
                doc = json.loads(fh.read().decode("utf-8"))
    except zipfile.BadZipFile as exc:
        raise ValueError(f"release wheel {path.name} is not a valid wheel archive") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"release wheel {path.name} has invalid vaso_provenance.json: {exc}") from exc
    if not isinstance(doc, dict):
        raise ValueError(f"release wheel {path.name} vaso_provenance.json must be an object")
    return doc


def validate_release_wheel_commit(path: Path, commit: str, *, wheel_name: str | None = None) -> None:
    if not path.is_file():
        raise ValueError(f"release wheel is missing: {path}")
    expected_sha8 = commit[:8].lower()
    name = wheel_name or path.name
    errors = []
    try:
        suffix = _version_git_suffix(name)
    except ValueError as exc:
        errors.append(str(exc))
    else:
        if suffix != expected_sha8:
            errors.append(f"version suffix g{suffix}")
    try:
        provenance = _wheel_provenance(path)
    except ValueError as exc:
        errors.append(str(exc))
    else:
        provenance_sha = provenance.get("git_sha")
        if not isinstance(provenance_sha, str) or not provenance_sha:
            errors.append("missing provenance git_sha")
        elif provenance_sha.lower() != commit.lower():
            errors.append(f"provenance git_sha {provenance_sha}")
    if errors:
        raise ValueError(
            f"release wheel {name} does not match commit {commit}: " + "; ".join(errors)
        )


def _manifest_items(items: Iterable[str]) -> list[tuple[str, Path]]:
    parsed: list[tuple[str, Path]] = []
    for item in items:
        package, sep, raw_path = item.partition("=")
        if not sep or not package or not raw_path:
            raise ValueError(f"release wheel manifest must be PACKAGE=PATH, got {item!r}")
        parsed.append((package, Path(raw_path)))
    return parsed


def _wheelhouse_for_manifest(manifest: Path) -> Path | None:
    name = manifest.name
    suffix = "_manifest.json"
    if not name.endswith(suffix):
        return None
    return manifest.with_name(name[: -len(suffix)] + "_wheelhouse")


def validate_manifest_wheels(
    manifests: Iterable[tuple[str, Path]],
    commit: str,
    *,
    require_manifest_wheelhouse: bool = False,
) -> int:
    count = 0
    for package, manifest in manifests:
        if not manifest.is_file():
            raise ValueError(f"release wheel manifest is missing for {package}: {manifest}")
        doc = json.loads(manifest.read_text(encoding="utf-8"))
        wheels = doc.get("wheels", []) if isinstance(doc, dict) else []
        if not isinstance(wheels, list) or not wheels:
            raise ValueError(f"release wheel manifest for {package} contains no wheels: {manifest}")
        expected_wheelhouse = _wheelhouse_for_manifest(manifest)
        for wheel in wheels:
            if not isinstance(wheel, dict) or not wheel.get("path"):
                raise ValueError(f"invalid release wheel entry for {package}: {manifest}")
            source = Path(str(wheel["path"]))
            if require_manifest_wheelhouse:
                if expected_wheelhouse is None:
                    raise ValueError(f"cannot infer release wheelhouse for manifest: {manifest}")
                try:
                    source.resolve().relative_to(expected_wheelhouse.resolve())
                except ValueError as exc:
                    raise ValueError(
                        f"release wheel {source.name} is outside this run's wheelhouse {expected_wheelhouse}"
                    ) from exc
            validate_release_wheel_commit(source, commit, wheel_name=str(wheel.get("name") or source.name))
            count += 1
    return count


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--require-manifest-wheelhouse", action="store_true")
    parser.add_argument("wheel_manifest", nargs="+")
    args = parser.parse_args(argv)
    try:
        count = validate_manifest_wheels(
            _manifest_items(args.wheel_manifest),
            args.commit,
            require_manifest_wheelhouse=args.require_manifest_wheelhouse,
        )
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    print(f"release wheel identity verified: {count} wheels for {args.commit}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
