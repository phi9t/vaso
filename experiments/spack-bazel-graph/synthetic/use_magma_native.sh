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

PREFIX_PATH="$(find_runfile '*magma_native*/prefix_path.txt')"
MANIFEST_JSON="$(find_runfile '*magma_native*/source_build.json')"
CUDA_PREFIX="$(dirname "$(find_runfile '*cuda_native*/prefix/bin/nvcc')")/.."
OPENBLAS_PREFIX="$(dirname "$(find_runfile '*openblas_native*/prefix/include/openblas_config.h')")/.."
PREFIX="$(tr -d '\n' < "$PREFIX_PATH")"

[[ -f "$PREFIX/include/magma_v2.h" ]] || {
  echo "MAGMA native prefix lacks include/magma_v2.h: $PREFIX" >&2
  exit 1
}
[[ -f "$PREFIX/lib/libmagma.a" ]] || {
  echo "MAGMA native prefix lacks static lib/libmagma.a: $PREFIX" >&2
  exit 1
}
[[ -f "$MANIFEST_JSON" ]] || {
  echo "MAGMA source build manifest not staged" >&2
  exit 1
}

python3 - "$PREFIX/include/magma_types.h" "$MANIFEST_JSON" <<'PY'
import json
import re
import sys

header, manifest = sys.argv[1:3]
text = open(header, encoding="utf-8").read()
defs = dict(
    re.findall(
        r"^#define\s+(MAGMA_VERSION_(?:MAJOR|MINOR|MICRO))\s+([0-9]+)\b",
        text,
        re.M,
    )
)
version = ".".join(defs[k] for k in (
    "MAGMA_VERSION_MAJOR",
    "MAGMA_VERSION_MINOR",
    "MAGMA_VERSION_MICRO",
))
data = json.load(open(manifest, encoding="utf-8"))
if data["mechanism"] != "cmake":
    raise SystemExit("wrong MAGMA native mechanism")
if data["package"] != "magma":
    raise SystemExit("wrong package in MAGMA native manifest")
if data["expected_version"] != "2.6.1":
    raise SystemExit("wrong expected MAGMA package version")
if version != "2.6.1":
    raise SystemExit(f"wrong MAGMA header version: {version}")
for dep in ("cuda", "openblas", "cmake", "ninja"):
    if not data.get("dependency_prefixes", {}).get(dep):
        raise SystemExit(f"MAGMA native manifest lacks {dep} dependency prefix")
PY

if [[ -n "${TEST_TMPDIR:-}" ]]; then
  TMPDIR="$TEST_TMPDIR"
elif [[ -z "${TMPDIR:-}" ]]; then
  TMPDIR="$PWD"
fi
export TMPDIR
mkdir -p "$TMPDIR"
work="$(mktemp -d -p "$TMPDIR" use-magma.XXXXXX)"
cat > "$work/use_magma.cc" <<'CC'
#include <cstdio>
#include <magma_v2.h>

int main() {
  int major = 0;
  int minor = 0;
  int micro = 0;
  magma_version(&major, &minor, &micro);
  std::printf("magma:%d.%d.%d\n", major, minor, micro);
  return major == 2 && minor == 6 && micro == 1 ? 0 : 1;
}
CC

libdir="$PREFIX/lib"
cuda_libdir="$CUDA_PREFIX/lib64"
if [[ ! -d "$cuda_libdir" ]]; then
  cuda_libdir="$CUDA_PREFIX/lib"
fi
openblas_libdir="$OPENBLAS_PREFIX/lib"
if [[ ! -d "$openblas_libdir" ]]; then
  openblas_libdir="$OPENBLAS_PREFIX/lib64"
fi

/usr/bin/c++ -std=c++17 "$work/use_magma.cc" -o "$work/use_magma" \
  -I"$PREFIX/include" -I"$CUDA_PREFIX/include" -I"$OPENBLAS_PREFIX/include" \
  "$libdir/libmagma.a" \
  -L"$cuda_libdir" -L"$openblas_libdir" \
  -lopenblas -lcusparse -lcublas -lcudart -lm -ldl -lpthread \
  -Wl,-rpath,"$cuda_libdir" -Wl,-rpath,"$openblas_libdir"

LD_LIBRARY_PATH="$cuda_libdir:$openblas_libdir${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}" \
  "$work/use_magma"
