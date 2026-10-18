#!/usr/bin/env python3
"""Refresh Cargo file checksums after publication redacts vendored text.

The upstream package checksums and Cargo.lock stay pinned. Only the per-file
checksums of the sanitized local source tree are regenerated.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path


def main() -> None:
    vendor = Path(__file__).resolve().parents[2] / "third_party/rust_pyo3_cargo/vendor"
    for manifest in sorted(vendor.glob("*/.cargo-checksum.json")):
        document = json.loads(manifest.read_text(encoding="utf-8"))
        for relative in document["files"]:
            path = (manifest.parent / relative).resolve()
            if not path.is_relative_to(manifest.parent.resolve()):
                raise ValueError("unsafe Cargo checksum path")
            document["files"][relative] = hashlib.sha256(path.read_bytes()).hexdigest()
        manifest.write_text(json.dumps(document, separators=(",", ":")) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
