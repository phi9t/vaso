#!/usr/bin/env bash
set -euo pipefail

find_runfile() {
  local pattern="$1"
  local base hit
  for base in "${RUNFILES_DIR:-}" "$PWD" "$PWD/.." "$0.runfiles"; do
    [[ -n "$base" && -d "$base" ]] || continue
    hit="$(find "$base" -path "$pattern" 2>/dev/null | head -1)"
    if [[ -n "$hit" ]]; then echo "$hit"; return 0; fi
  done
  return 1
}

PREFIX_PATH="$(find_runfile '*/+nvshmem_native+nvshmem_native/prefix_path.txt')"
MANIFEST_JSON="$(find_runfile '*/+nvshmem_native+nvshmem_native/sdk_boundary.json')"
CUDA_PREFIX="$(dirname "$(find_runfile '*/+cuda_native+cuda_native/prefix/bin/nvcc')")/.."
PREFIX="$(tr -d '\n' < "$PREFIX_PATH")"

[[ -f "$PREFIX/include/nvshmem.h" ]] || {
  echo "NVSHMEM native prefix lacks include/nvshmem.h: $PREFIX" >&2
  exit 1
}
[[ -f "$PREFIX/include/non_abi/nvshmem_version.h" ]] || {
  echo "NVSHMEM native prefix lacks include/non_abi/nvshmem_version.h: $PREFIX" >&2
  exit 1
}
[[ -e "$PREFIX/lib/libnvshmem_host.so.3" ]] || {
  echo "NVSHMEM native prefix lacks lib/libnvshmem_host.so.3: $PREFIX" >&2
  exit 1
}
[[ -e "$PREFIX/lib/libnvshmem_device.a" ]] || {
  echo "NVSHMEM native prefix lacks lib/libnvshmem_device.a: $PREFIX" >&2
  exit 1
}
[[ -f "$MANIFEST_JSON" ]] || {
  echo "NVSHMEM sdk-boundary manifest not staged" >&2
  exit 1
}

python3 - "$PREFIX/include/non_abi/nvshmem_version.h" "$MANIFEST_JSON" <<'PY'
import json
import re
import sys

header, manifest = sys.argv[1:3]
text = open(header, encoding="utf-8").read()
defs = dict(re.findall(r"^#define\s+(NVSHMEM_VENDOR_(?:MAJOR|MINOR|PATCH)_VERSION)\s+([0-9]+)\b", text, re.M))
version = ".".join(defs[k] for k in (
    "NVSHMEM_VENDOR_MAJOR_VERSION",
    "NVSHMEM_VENDOR_MINOR_VERSION",
    "NVSHMEM_VENDOR_PATCH_VERSION",
))
data = json.load(open(manifest, encoding="utf-8"))
if data["mechanism"] != "sdk-boundary":
    raise SystemExit("wrong NVSHMEM native mechanism")
if data["package"] != "nvshmem":
    raise SystemExit("wrong package in NVSHMEM native manifest")
if data["expected_version"] != "3.4.5":
    raise SystemExit("wrong expected NVSHMEM package version")
if data["actual_header_version"] != version:
    raise SystemExit("NVSHMEM header version and manifest disagree")
if version != "3.4.5":
    raise SystemExit(f"wrong NVSHMEM header version: {version}")
cuda = data.get("dependency_prefixes", {}).get("cuda", "")
if not cuda:
    raise SystemExit("NVSHMEM native manifest lacks CUDA dependency prefix")
print(f"nvshmem:{data['expected_version']}:cuda={cuda}:ok")
PY

if [[ -n "${TEST_TMPDIR:-}" ]]; then
  TMPDIR="$TEST_TMPDIR"
elif [[ -z "${TMPDIR:-}" ]]; then
  TMPDIR="$PWD"
fi
export TMPDIR
mkdir -p "$TMPDIR"
work="$(mktemp -d -p "$TMPDIR" use-nvshmem.XXXXXX)"
cat > "$work/use_nvshmem.cc" <<'CC'
#include <cstdio>
#include <nvshmem.h>

int main() {
  void *symbol = reinterpret_cast<void *>(&nvshmem_malloc);
  if (symbol == nullptr) {
    return 1;
  }
  std::puts("nvshmem:link-ok");
  return 0;
}
CC

cuda_libdir="$CUDA_PREFIX/lib64"
if [[ ! -d "$cuda_libdir" ]]; then
  cuda_libdir="$CUDA_PREFIX/lib"
fi

/usr/bin/c++ -std=c++17 "$work/use_nvshmem.cc" -o "$work/use_nvshmem" \
  -I"$PREFIX/include" -I"$PREFIX/include/cccl" \
  -I"$CUDA_PREFIX/include" -I"$CUDA_PREFIX/include/cccl" \
  -L"$PREFIX/lib" -L"$cuda_libdir" \
  "$PREFIX/lib/libnvshmem_host.so.3" -lcudart \
  -Wl,-rpath,"$PREFIX/lib" -Wl,-rpath,"$cuda_libdir"

readelf -d "$work/use_nvshmem" | grep -q 'libnvshmem_host.so.3'
printf 'nvshmem:link-ok\n'
