#!/usr/bin/env bash
set -euo pipefail

tmp_parent="${TEST_TMPDIR:-${VASO_AGENT_IO_ROOT:?VASO_AGENT_IO_ROOT or TEST_TMPDIR must be set for scratch I/O}/hermetic-spack-guard-scratch}"
mkdir -p "$tmp_parent"
tmp="$(mktemp -d "$tmp_parent/hermetic-spack-guard.XXXXXX")"
trap 'rm -rf "$tmp"' EXIT

for tool in tools/spack_to_bazel.py tools/build_graph.py; do
  out="$tmp/$(basename "$tool").stderr"
  if python3 "$tool" \
      --root zlib-ng \
      --out "$tmp/out.json" \
      --from-installed "$tmp/prefix" \
      >"$tmp/stdout" 2>"$out"; then
    echo "$tool accepted --from-installed outside the Bazel-vendored Spack path" >&2
    exit 1
  fi
  if ! grep -q "installed-prefix Spack snapshots are disabled" "$out"; then
    echo "$tool did not explain the hermetic Spack requirement" >&2
    cat "$out" >&2
    exit 1
  fi
done

python3 - <<'PY'
from pathlib import Path

rootfs = Path("rootfs/build_rootfs.sh").read_text()
spack_dist = Path("tools/spack_dist.bzl").read_text()

assert "gfortran" in rootfs, "CUDA insula rootfs must include gfortran for PyTorch BLAS/LAPACK concretization"
assert "command -v gfortran" in spack_dist, "Spack wrapper must discover gfortran inside the insula"
assert 'compiler_languages="c,c++,fortran"' in spack_dist, "Spack compiler external must advertise Fortran only when gfortran exists"
assert "fc: /usr/bin/gfortran" in spack_dist, "Spack compiler external must pin fc to the insula gfortran"
assert "f77: /usr/bin/gfortran" in spack_dist, "Spack compiler external must pin f77 to the insula gfortran"
assert "fortran: /usr/bin/gfortran" in spack_dist, "Spack compiler external must expose the key Spack's compiler package API reads"
assert '  python:\n    require: "@3.13.13"\n' in spack_dist, "Spack wrapper must pin all solves to the settled Python 3.13.13 line"
assert "VASO_CUDA_HOME" in spack_dist, "Spack wrapper must derive CUDA external from the insula CUDA SDK"
assert "VASO_ROOTFS_BUNDLE_MANIFEST" in spack_dist, "Spack wrapper must derive CUDA version from rootfs provenance"
assert "rootfs_spack_externals.py" in spack_dist, "Spack wrapper must generate CUDA-family externals from the rootfs manifest"
assert "cuda_ecosystem.lock.json" in spack_dist, "Spack wrapper must check rootfs external coverage against the CUDA ecosystem lock"
assert "--manifest" in spack_dist and "--lock" in spack_dist, "Spack wrapper must pass manifest and lock into the external generator"
assert 'docker run --rm "$LLVM_DOCKER_IMAGE" "$LLVM_INSTALL_PREFIX/bin/clang" --version' in rootfs, "LLVM cache validation must run clang inside its Ubuntu build image"
assert "subprocess.check_output([str(clang)" not in rootfs, "LLVM cache validation must not execute Ubuntu clang on the host"
assert "VASO_SPACK_OVERLAY_ROOTS" in spack_dist, "Spack wrapper must expose repo-owned overlay roots to hermetic Spack"
assert "spack_overlays/vaso/spack_repo/vaso_overlay/repo.yaml" in spack_dist, "Spack wrapper must locate the repo-owned Spack overlay from Bazel runfiles"
assert "repos.yaml" in spack_dist and "vaso_overlay" in spack_dist, "Spack wrapper must generate repos.yaml with the Vaso overlay before builtin"
assert "if [[ ! -x \"$DIST/bin/spack\" ]]" in spack_dist, "Spack wrapper must not assume dist is next to Bazel's symlinked launcher"
assert "+spack_toolchain+spack_dist/dist/bin/spack" in spack_dist, "Spack wrapper must locate the vendored dist through Bazel runfiles"
assert '"${0}.runfiles"' in spack_dist, "Spack wrapper must search the Bazel launcher runfiles tree"
PY

python3 - <<'PY'
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

module_path = Path("tools/rootfs_spack_externals.py")
spec = importlib.util.spec_from_file_location("rootfs_spack_externals", module_path)
assert spec and spec.loader
mod = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = mod
spec.loader.exec_module(mod)

lock = json.loads(Path("rootfs/cuda_ecosystem.lock.json").read_text())
for line, line_data in sorted(lock["lines"].items()):
    components = {
        name: {"expected": data["version"]}
        for name, data in line_data["components"].items()
        if name in mod.ROOTFS_COMPONENTS
    }
    for name, data in lock.get("components", {}).items():
        if name in mod.GLOBAL_ROOTFS_COMPONENTS:
            components[name] = {
                "expected": data["version"],
                "commit": data.get("commit"),
                "prefix": data.get("install_layout", {}).get("prefix"),
            }
    manifest = {
        "schema_version": 2,
        "line": line,
        "verified_versions": {
            "components": components,
        },
    }
    entries = mod.externals_from_manifest(manifest)
    mod.check_lock_coverage(lock, entries)
    by_package = {entry.package: entry for entry in entries}
    expected_packages = mod.lock_components_requiring_externals(lock)
    assert expected_packages <= set(by_package), (line, expected_packages, by_package)
    fragment = mod.packages_yaml_fragment(entries)
    blocks = {}
    current = None
    current_lines = []
    for raw_line in fragment.splitlines():
        if raw_line.startswith("  ") and not raw_line.startswith("    "):
            if current is not None:
                blocks[current] = "\n".join(current_lines) + "\n"
            current = raw_line.strip()[:-1]
            current_lines = [raw_line]
        elif current is not None:
            current_lines.append(raw_line)
    if current is not None:
        blocks[current] = "\n".join(current_lines) + "\n"
    for package in sorted(expected_packages | {"nvtx"}):
        package_block = blocks.get(package)
        assert package_block, f"{line}: missing {package} external"
        assert "    externals:\n" in package_block, f"{line}: {package} has no externals"
        assert "    buildable: false\n" in package_block, f"{line}: {package} is buildable"
        assert "      prefix: " in package_block, f"{line}: {package} has no prefix"
PY

mkdir -p "$tmp/prefix"
cat > "$tmp/lock.json" <<JSON
{
  "schema_version": 1,
  "root": "spack_zlib_ng",
  "packages": {
    "spack_zlib_ng": {
      "package": "zlib-ng",
      "version": "2.3.3",
      "spack_hash": "abc123",
      "prefix": "$tmp/prefix",
      "build": "spack",
      "link_deps": [],
      "link_libs": [],
      "include_dirs": []
    }
  }
}
JSON
cat > "$tmp/overrides.json" <<'JSON'
{
  "native": {
    "zlib-ng": "@zlib_ng_native//:lib"
  },
  "provided_versions": {
    "zlib-ng": "2.3.3"
  }
}
JSON

VASO_IN_INSULA=1 python3 tools/spack_to_bazel.py \
  --spack /bin/false \
  --root zlib-ng \
  --out "$tmp/lock.json" \
  --native-overrides "$tmp/overrides.json" \
  --reuse-if-valid
python3 - "$tmp/lock.json" <<'PY'
import json
import sys
node = json.load(open(sys.argv[1]))["packages"]["spack_zlib_ng"]
assert node["build"] == "native", node
assert node["native_prefix"] == "@zlib_ng_native//:lib", node
PY

VASO_IN_INSULA=1 python3 tools/spack_to_bazel.py \
  --spack /bin/false \
  --root zlib-ng \
  --out "$tmp/lock.json" \
  --reuse-if-valid
python3 - "$tmp/lock.json" <<'PY'
import json
import sys
node = json.load(open(sys.argv[1]))["packages"]["spack_zlib_ng"]
assert node["build"] == "spack", node
assert "native_prefix" not in node, node
PY

python3 - <<'PY'
from pathlib import Path

missing = []
for path in sorted(Path("native").glob("*/*.bzl")):
    text = path.read_text()
    if "repository_rule(" not in text:
        continue
    if "VASO_IN_INSULA" not in text:
        missing.append(f"{path}: missing VASO_IN_INSULA guard")
        continue
    if 'environ = ' not in text or '"VASO_IN_INSULA"' not in text:
        missing.append(f"{path}: VASO_IN_INSULA is not a repository env input")

if missing:
    raise SystemExit(
        "native repository rules must refuse host-root builds:\n" +
        "\n".join(missing)
    )
PY
