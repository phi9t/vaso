#!/usr/bin/env python3
"""Merkle snapshot for the miniature N1 plan pattern."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


MERKLE_INPUTS = {
    "dep-prefix": [
        "plans/dep.plan.json",
        "pkg/dep.c",
        "tools/executor.py",
        "toolchains/gcc_driver.sh",
        "toolchains/host_c_toolchain.bzl",
    ],
    "native-pkg": [
        "plans/native.plan.json",
        "pkg/pkg.c",
        "pkg/dep.txt",
        "tools/executor.py",
        "toolchains/gcc_driver.sh",
        "toolchains/host_c_toolchain.bzl",
    ],
}


def file_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def snapshot(root: Path) -> dict:
    entries = {}
    for name, rels in sorted(MERKLE_INPUTS.items()):
        h = hashlib.sha256()
        file_entries = []
        for rel in rels:
            digest = file_digest(root / rel)
            h.update(rel.encode("utf-8"))
            h.update(b"\0")
            h.update(digest.encode("ascii"))
            h.update(b"\0")
            file_entries.append({"path": rel, "sha256": digest})
        entries[name] = {
            "merkle": h.hexdigest(),
            "inputs": file_entries,
        }
    return {"schema": 1, "entries": entries}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--check", type=Path)
    parser.add_argument("--write", type=Path)
    args = parser.parse_args()
    data = snapshot(args.root)
    text = json.dumps(data, indent=2, sort_keys=True) + "\n"
    if args.write:
        args.write.write_text(text, encoding="utf-8")
    if args.check:
        expected = args.check.read_text(encoding="utf-8")
        if expected != text:
            print("key snapshot drift", flush=True)
            return 1
    if not args.check and not args.write:
        print(text, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
