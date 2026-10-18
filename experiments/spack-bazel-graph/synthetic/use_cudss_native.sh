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

PREFIX_PATH="$(find_runfile '*/+cudss_native+cudss_native/prefix_path.txt')"
MANIFEST_JSON="$(find_runfile '*/+cudss_native+cudss_native/sdk_boundary.json')"
CUDA_PREFIX="$(dirname "$(find_runfile '*/+cuda_native+cuda_native/prefix/bin/nvcc')")/.."
PREFIX="$(tr -d '\n' < "$PREFIX_PATH")"

[[ -f "$PREFIX/include/cudss.h" ]] || {
  echo "cuDSS native prefix lacks include/cudss.h: $PREFIX" >&2
  exit 1
}
[[ -e "$PREFIX/lib/libcudss.so" || -e "$PREFIX/lib64/libcudss.so" ]] || {
  echo "cuDSS native prefix lacks libcudss.so: $PREFIX" >&2
  exit 1
}
[[ -f "$MANIFEST_JSON" ]] || { echo "cuDSS sdk-boundary manifest not staged" >&2; exit 1; }

python3 - "$PREFIX/include/cudss.h" "$MANIFEST_JSON" <<'PY'
import json
import re
import sys

header, manifest = sys.argv[1:3]
text = open(header, encoding="utf-8").read()
defs = dict(re.findall(r"^#define\s+(CUDSS_(?:VER|VERSION)_(?:MAJOR|MINOR|PATCH))\s+([0-9]+)\b", text, re.M))
values = []
for key in ("MAJOR", "MINOR", "PATCH"):
    for prefix in ("CUDSS_VERSION_", "CUDSS_VER_"):
        value = defs.get(prefix + key)
        if value is not None:
            values.append(value)
            break
    else:
        raise SystemExit(f"missing cuDSS version macro for {key}")
version = ".".join(values)
data = json.load(open(manifest, encoding="utf-8"))
if data["mechanism"] != "sdk-boundary":
    raise SystemExit("wrong cuDSS native mechanism")
if data["package"] != "cudss":
    raise SystemExit("wrong package in cuDSS native manifest")
if data["expected_version"] != "0.7.1.4":
    raise SystemExit("wrong expected cuDSS package version")
if data["actual_header_version"] != version:
    raise SystemExit("cuDSS header version and manifest disagree")
if version != "0.7.1":
    raise SystemExit(f"wrong cuDSS header version: {version}")
cuda = data.get("dependency_prefixes", {}).get("cuda", "")
if not cuda:
    raise SystemExit("cuDSS native manifest lacks CUDA dependency prefix")
print(f"cudss:{data['expected_version']}:cuda={cuda}:ok")
PY

if [[ -n "${TEST_TMPDIR:-}" ]]; then
  TMPDIR="$TEST_TMPDIR"
elif [[ -z "${TMPDIR:-}" ]]; then
  TMPDIR="$PWD"
fi
export TMPDIR
mkdir -p "$TMPDIR"
work="$(mktemp -d -p "$TMPDIR" use-cudss.XXXXXX)"
cat > "$work/use_cudss.cc" <<'CC'
#include <cstdio>
#include <cudss.h>

int main() {
  void *symbol = reinterpret_cast<void *>(&cudssCreate);
  if (symbol == nullptr) {
    return 1;
  }
  std::printf("cudss:%d.%d.%d\n", CUDSS_VERSION_MAJOR, CUDSS_VERSION_MINOR, CUDSS_VERSION_PATCH);
  return CUDSS_VERSION_MAJOR == 0 && CUDSS_VERSION_MINOR == 7 && CUDSS_VERSION_PATCH == 1 ? 0 : 1;
}
CC

libdir="$PREFIX/lib"
if [[ ! -d "$libdir" ]]; then
  libdir="$PREFIX/lib64"
fi
cuda_libdir="$CUDA_PREFIX/lib64"
if [[ ! -d "$cuda_libdir" ]]; then
  cuda_libdir="$CUDA_PREFIX/lib"
fi

/usr/bin/c++ -std=c++17 "$work/use_cudss.cc" -o "$work/use_cudss" \
  -I"$PREFIX/include" -I"$CUDA_PREFIX/include" \
  -L"$libdir" -L"$cuda_libdir" \
  -lcudss -lcusparse -lcublas -lcudart \
  -Wl,-rpath,"$libdir" -Wl,-rpath,"$cuda_libdir"

LD_LIBRARY_PATH="$libdir:$cuda_libdir${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}" \
  "$work/use_cudss"
