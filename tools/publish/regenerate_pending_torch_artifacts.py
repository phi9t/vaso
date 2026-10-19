#!/usr/bin/env python3
"""Refresh pending torch artifact manifests after public plan regeneration."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


LINES = ("cu129", "cu130")
ZERO_SHA256 = "0" * 64


def stable_json(value: object) -> str:
    return json.dumps(value, indent=2, sort_keys=True) + "\n"


def read_json(path: Path) -> dict[str, Any]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"{path}: expected JSON object")
    return raw


def plan_key(keys: dict[str, Any], line: str) -> str:
    packages = keys.get("packages")
    if not isinstance(packages, dict):
        raise ValueError("plans/keys.json: missing packages object")
    package = packages.get(f"torch/{line}/py-torch")
    if not isinstance(package, dict) or not isinstance(package.get("merkle_key"), str):
        raise ValueError(f"plans/keys.json: missing torch/{line}/py-torch merkle_key")
    return package["merkle_key"]


def is_pending_manifest(manifest: dict[str, Any]) -> bool:
    archive = manifest.get("archive")
    sha256 = manifest.get("sha256")
    return archive == "" or (
        isinstance(archive, str)
        and archive.startswith("step-b-pending://")
        and (sha256 == ZERO_SHA256 or sha256 == "")
    )


def refresh_manifest(repo_root: Path, line: str, keys: dict[str, Any]) -> bool:
    path = repo_root / "plans" / "torch" / line / "torch-artifact.json"
    manifest = read_json(path)
    key = plan_key(keys, line)
    if not is_pending_manifest(manifest) and manifest.get("plan_key") == key:
        return False
    manifest["archive"] = f"step-b-pending://torch/{line}/{key}"
    manifest["plan_key"] = key
    manifest["sha256"] = ZERO_SHA256
    path.write_text(stable_json(manifest), encoding="utf-8")
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=Path("foundry"))
    args = parser.parse_args(argv)
    repo_root = args.repo_root
    keys = read_json(repo_root / "plans" / "keys.json")
    changed = sum(1 for line in LINES if refresh_manifest(repo_root, line, keys))
    print(f"pending torch artifact manifests refreshed: {changed}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
