#!/usr/bin/env python3
"""Retired: publication must not eagerly rehash vendored source files."""
from __future__ import annotations

def main() -> None:
    raise SystemExit(
        "Vendor checksum regeneration is disabled: preserve the vendor tree and "
        "its existing Cargo manifests unchanged. If sanitization needs vendor "
        "changes, stop publication and resolve the source/scrub scope explicitly."
    )

if __name__ == "__main__":
    main()
