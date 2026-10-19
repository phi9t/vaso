#!/usr/bin/env python3
"""Write a stable content manifest for a native prefix tree."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
from pathlib import Path
from typing import Any


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def stable_json(data: object) -> str:
    return json.dumps(data, indent=2, sort_keys=True) + "\n"


def prefix_entries(prefix: Path) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for root, dirnames, filenames in os.walk(prefix, topdown=True, followlinks=False):
        root_path = Path(root)
        rel_root = root_path.relative_to(prefix)
        dirnames[:] = sorted(name for name in dirnames if name != "__pycache__")
        for name in list(dirnames):
            path = root_path / name
            rel = (rel_root / name).as_posix()
            if path.is_symlink():
                entries.append({"path": rel, "target": os.readlink(path), "type": "symlink"})
            else:
                entries.append({
                    "mode": format(stat.S_IMODE(path.lstat().st_mode), "04o"),
                    "path": rel,
                    "type": "directory",
                })
        dirnames[:] = [name for name in dirnames if not (root_path / name).is_symlink()]
        for name in sorted(filenames):
            path = root_path / name
            rel = (rel_root / name).as_posix()
            if name.endswith(".pyc"):
                continue
            if path.is_symlink():
                entries.append({"path": rel, "target": os.readlink(path), "type": "symlink"})
                continue
            entries.append({
                "mode": format(stat.S_IMODE(path.stat().st_mode), "04o"),
                "path": rel,
                "sha256": sha256_file(path),
                "size": path.stat().st_size,
                "type": "file",
            })
    return entries


def write_manifest(prefix: Path, out: Path, *, name: str) -> None:
    if not prefix.is_dir():
        raise SystemExit(f"prefix manifest input is not a directory: {prefix}")
    payload: dict[str, Any] = {
        "entries": prefix_entries(prefix),
        "name": name,
        "schema_version": 1,
    }
    tree_bytes = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    payload["tree_sha256"] = hashlib.sha256(tree_bytes).hexdigest()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(stable_json(payload), encoding="utf-8")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--prefix", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--name", required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    write_manifest(args.prefix, args.out, name=args.name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
