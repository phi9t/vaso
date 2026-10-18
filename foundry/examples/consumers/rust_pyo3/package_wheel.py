#!/usr/bin/env python3
"""Build a small repaired wheel for the Rust/PyO3 consumer example."""

from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import json
import os
import stat
import zipfile
from pathlib import Path, PurePosixPath


FORBIDDEN_MARKERS = (
    b"<data-volume>/",
    b"/workspace/",
    b"/vaso-bazel-ob/",
    b"output-base",
    b"output_base",
    b".vaso-estate",
)


def _record_digest(data: bytes) -> str:
    digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).decode("ascii").rstrip("=")
    return f"sha256={digest}"


def _wheel_info(name: str, data: bytes) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name)
    info.date_time = (1980, 1, 1, 0, 0, 0)
    mode = 0o755 if name.endswith(".so") else 0o644
    info.external_attr = (stat.S_IFREG | mode) << 16
    return info


def _scan_relocatable(rel: str, data: bytes) -> None:
    for marker in FORBIDDEN_MARKERS:
        if marker in data:
            raise ValueError(f"{rel} contains non-relocatable marker {marker!r}")


def _write_wheel(out: Path, files: dict[str, bytes], record_path: str) -> None:
    rows: list[list[str]] = []
    for rel, data in files.items():
        rows.append([rel, _record_digest(data), str(len(data))])
    rows.append([record_path, "", ""])

    import io

    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerows(rows)
    files[record_path] = buf.getvalue().encode("utf-8")

    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(out.suffix + ".tmp")
    with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for rel in sorted(files):
            zf.writestr(_wheel_info(rel, files[rel]), files[rel])
    os.replace(tmp, out)


def build_wheel(args: argparse.Namespace) -> None:
    distribution = "vaso_rust_pyo3"
    version = "0.1.0"
    dist_info = f"{distribution}-{version}.dist-info"
    package = distribution

    so_data = args.extension.read_bytes()
    init_data = args.init.read_bytes()
    provenance = {
        "schema_version": 1,
        "abi": "abi3-py313",
        "cargo_sha256": args.cargo_sha256,
        "crate_vendor_manifest_sha256": hashlib.sha256(args.vendor_manifest.read_bytes()).hexdigest(),
        "python": "3.13.13",
        "rootfs_compiler": "gcc-13.3",
        "rust_target": "x86_64-unknown-linux-gnu",
        "rust_toolchain": args.rust_toolchain,
        "rust_toolchain_archive_sha256": args.rust_toolchain_archive_sha256,
        "rustc_sha256": args.rustc_sha256,
        "wheel_repair": "vaso-rust-pyo3-packager-v1",
    }
    provenance_data = (json.dumps(provenance, indent=2, sort_keys=True) + "\n").encode("utf-8")

    files = {
        f"{package}/__init__.py": init_data,
        f"{package}/_rust_pyo3.abi3.so": so_data,
        f"{dist_info}/METADATA": (
            "Metadata-Version: 2.3\n"
            "Name: vaso-rust-pyo3\n"
            f"Version: {version}\n"
            "Summary: Vaso Rust/PyO3 consumer verifier fixture\n"
        ).encode("utf-8"),
        f"{dist_info}/WHEEL": (
            "Wheel-Version: 1.0\n"
            "Generator: vaso-rust-pyo3-packager\n"
            "Root-Is-Purelib: false\n"
            "Tag: cp313-abi3-linux_x86_64\n"
        ).encode("utf-8"),
        f"{dist_info}/vaso_provenance.json": provenance_data,
    }

    for rel, data in files.items():
        _scan_relocatable(rel, data)

    _write_wheel(args.out, files, f"{dist_info}/RECORD")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--extension", type=Path, required=True)
    parser.add_argument("--init", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--vendor-manifest", type=Path, required=True)
    parser.add_argument("--rust-toolchain", required=True)
    parser.add_argument("--rust-toolchain-archive-sha256", required=True)
    parser.add_argument("--rustc-sha256", required=True)
    parser.add_argument("--cargo-sha256", required=True)
    args = parser.parse_args()
    build_wheel(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
