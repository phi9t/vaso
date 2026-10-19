#!/usr/bin/env python3
"""Write a content manifest for jaxlib's nested Bazel caches."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def archive_entries(pins: dict[str, Any], distdir: Path, repository_cache: Path) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    archives = pins.get("archives", [])
    if not isinstance(archives, list):
        raise ValueError("nested archive pins must contain an archives list")
    for archive in sorted(archives, key=lambda item: (str(item.get("sha256", "")), str(item.get("filename", "")))):
        if not isinstance(archive, dict):
            raise ValueError("nested archive pins contain a non-object archive entry")
        filename = str(archive.get("filename", ""))
        expected_sha = str(archive.get("sha256", ""))
        if filename:
            path = distdir / filename
            if not path.is_file():
                raise FileNotFoundError(f"required jaxlib nested distdir archive is missing: {path}")
            actual_sha = sha256_file(path)
            if actual_sha != expected_sha:
                raise ValueError(
                    f"jaxlib nested distdir archive sha256 mismatch for {path}: "
                    f"expected {expected_sha}, got {actual_sha}"
                )
        if expected_sha:
            path = repository_cache / "content_addressable" / "sha256" / expected_sha / "file"
            if not path.is_file():
                raise FileNotFoundError(f"required jaxlib nested repository-cache archive is missing: {path}")
            actual_sha = sha256_file(path)
            if actual_sha != expected_sha:
                raise ValueError(
                    f"jaxlib nested repository-cache archive sha256 mismatch for {path}: "
                    f"expected {expected_sha}, got {actual_sha}"
                )
        entries.append(
            {
                "expected_sha256": expected_sha,
                "filename": filename,
                "kind": "archive",
            }
        )
    return entries


def manifest_payload(
    pins: dict[str, Any],
    *,
    distdir: Path,
    repository_cache: Path,
) -> dict[str, Any]:
    payload = {
        "entries": archive_entries(pins, distdir, repository_cache),
        "jaxlib_version": pins.get("jaxlib_version"),
        "package": pins.get("package", "py-jaxlib"),
        "schema_version": 1,
    }
    stable = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    payload["tree_sha256"] = hashlib.sha256(stable).hexdigest()
    return payload


def write_manifest(
    pins_path: Path,
    out_path: Path,
    *,
    distdir: Path | None = None,
    repository_cache: Path | None = None,
) -> None:
    pins = read_json(pins_path)
    distdir = distdir or Path(str(pins.get("distdir", "")))
    repository_cache = repository_cache or Path(str(pins.get("repository_cache", "")))
    payload = manifest_payload(pins, distdir=distdir, repository_cache=repository_cache)
    out_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pins", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--distdir", type=Path)
    parser.add_argument("--repository-cache", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    write_manifest(
        args.pins,
        args.out,
        distdir=args.distdir,
        repository_cache=args.repository_cache,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
