#!/usr/bin/env python3
"""Materialize a hash-verified JAX runtime prefix from the pinned wheel cache."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import zipfile
from pathlib import Path
from typing import Any


REQUIRED_WHEEL_PREFIXES = (
    "absl_py-",
    "ml_dtypes-",
    "opt_einsum-",
    "scipy-",
)


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def _wheel_contract(manifest: Path) -> dict[str, str]:
    document = _load_json(manifest)
    archives = document.get("archives")
    if not isinstance(archives, list):
        raise ValueError(f"manifest has no archives list: {manifest}")
    selected: dict[str, str] = {}
    for prefix in REQUIRED_WHEEL_PREFIXES:
        matches = [
            item
            for item in archives
            if isinstance(item, dict)
            and str(item.get("filename", "")).startswith(prefix)
            and str(item.get("filename", "")).endswith(".whl")
        ]
        if len(matches) != 1:
            raise ValueError(
                f"manifest must contain one {prefix} wheel, found {len(matches)}"
            )
        filename = str(matches[0]["filename"])
        sha256 = str(matches[0].get("sha256", ""))
        if len(sha256) != 64:
            raise ValueError(f"invalid sha256 for {filename}: {sha256!r}")
        selected[filename] = sha256
    return selected


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _safe_extract(wheel: Path, destination: Path) -> None:
    destination = destination.resolve()
    with zipfile.ZipFile(wheel) as archive:
        for member in archive.infolist():
            resolved = (destination / member.filename).resolve()
            try:
                resolved.relative_to(destination)
            except ValueError as exc:
                raise ValueError(
                    f"wheel member escapes destination: {wheel}:{member.filename}"
                ) from exc
        archive.extractall(destination)


def materialize(
    *,
    manifest: Path,
    distdir: Path,
    prefix: Path,
    python_version: str,
) -> dict[str, Any]:
    contract = _wheel_contract(manifest)
    wheels: list[dict[str, str]] = []
    for filename, expected in contract.items():
        path = distdir / filename
        if not path.is_file():
            raise ValueError(
                f"missing pinned runtime wheel: {path}; run foundry/run.sh "
                "--jaxlib-prefetch for this estate line"
            )
        actual = _sha256(path)
        if actual != expected:
            raise ValueError(
                f"runtime wheel sha256 mismatch: {path} expected={expected} "
                f"actual={actual}"
            )
        wheels.append({"filename": filename, "sha256": actual})
    identity = {
        "schema_version": 1,
        "manifest": str(manifest),
        "manifest_sha256": _sha256(manifest),
        "wheels": wheels,
        "python_version": python_version,
    }
    identity_path = prefix / "runtime-wheels.json"
    if identity_path.is_file() and _load_json(identity_path) == identity:
        return identity

    temporary = prefix.with_name(prefix.name + f".tmp-{os.getpid()}")
    if temporary.exists():
        shutil.rmtree(temporary)
    site_packages = temporary / "lib" / f"python{python_version}" / "site-packages"
    site_packages.mkdir(parents=True)
    try:
        for item in wheels:
            _safe_extract(distdir / item["filename"], site_packages)
        (temporary / "runtime-wheels.json").write_text(
            json.dumps(identity, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        if prefix.exists():
            shutil.rmtree(prefix)
        os.replace(temporary, prefix)
    except BaseException:
        if temporary.exists():
            shutil.rmtree(temporary)
        raise
    return identity


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--distdir", required=True, type=Path)
    parser.add_argument("--prefix", required=True, type=Path)
    parser.add_argument(
        "--python-version",
        default=f"{sys.version_info.major}.{sys.version_info.minor}",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        identity = materialize(
            manifest=args.manifest,
            distdir=args.distdir,
            prefix=args.prefix,
            python_version=args.python_version,
        )
    except (OSError, ValueError, zipfile.BadZipFile) as exc:
        print(f"JAX runtime materialization failed: {exc}", file=sys.stderr)
        return 2
    print(
        f"JAX runtime wheels: ok count={len(identity['wheels'])} prefix={args.prefix}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
