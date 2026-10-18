#!/bin/bash
set -euo pipefail

wheel="$1"
export CARGO_HOME="/does-not-exist/host-cargo"
export PATH="/does-not-exist/host-path"
export RUSTUP_HOME="/does-not-exist/host-rustup"

/usr/bin/python3 - "$wheel" <<'PY'
import json
import sys
import zipfile

wheel = sys.argv[1]
with zipfile.ZipFile(wheel) as zf:
    names = [name for name in zf.namelist() if name.endswith(".dist-info/vaso_provenance.json")]
    if len(names) != 1:
        raise SystemExit("missing vaso_provenance.json")
    provenance = json.loads(zf.read(names[0]).decode("utf-8"))

toolchain = provenance.get("rust_toolchain", "")
if not toolchain.startswith("/vaso/sources/rust/toolchains/"):
    raise SystemExit(f"host Rust toolchain leaked into wheel provenance: {toolchain!r}")
for key in ("rustc_sha256", "cargo_sha256", "rust_toolchain_archive_sha256"):
    value = provenance.get(key, "")
    if not isinstance(value, str) or len(value) != 64:
        raise SystemExit(f"invalid {key}: {value!r}")

print("rust_pyo3 poison proof: wheel provenance names the pinned estate Rust toolchain")
PY
