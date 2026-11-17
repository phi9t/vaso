#!/usr/bin/env python3
"""Write a prefix presence receipt, never an integrity or content manifest.

Build success comes from the producer action. This receipt checks only that its
prefix directory exists. It does not inventory files, detect in-place changes,
or certify relocation, contents, completeness, or reproducibility.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path

def write_manifest(prefix: Path, out: Path, *, name: str) -> None:
    if not prefix.is_dir():
        raise SystemExit(f"prefix manifest input is not a directory: {prefix}")
    payload = {"schema_version": 2, "kind": "prefix_presence_receipt",
               "prefix_present": True, "integrity": "not_checked", "name": name}
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

def parse_args(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--prefix", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--name", required=True)
    return parser.parse_args(argv)

def main(argv=None):
    args = parse_args(argv)
    write_manifest(args.prefix, args.out, name=args.name)
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
