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

PREFIX_PATH="$(find_runfile '*/+cusparselt_native+cusparselt_native/prefix_path.txt')"
MANIFEST_JSON="$(find_runfile '*/+cusparselt_native+cusparselt_native/sdk_boundary.json')"
PREFIX="$(tr -d '\n' < "$PREFIX_PATH")"

[[ -f "$PREFIX/include/cusparseLt.h" ]] || {
  echo "cuSPARSELt native prefix lacks include/cusparseLt.h: $PREFIX" >&2
  exit 1
}
[[ -L "$PREFIX/lib/libcusparseLt.so" ]] || {
  echo "cuSPARSELt native prefix lacks lib/libcusparseLt.so symlink: $PREFIX" >&2
  exit 1
}
[[ -L "$PREFIX/lib/libcusparseLt.so.0" ]] || {
  echo "cuSPARSELt native prefix lacks lib/libcusparseLt.so.0 symlink: $PREFIX" >&2
  exit 1
}
[[ -f "$PREFIX/lib/libcusparseLt.so.0.8.1.1" ]] || {
  echo "cuSPARSELt native prefix lacks lib/libcusparseLt.so.0.8.1.1: $PREFIX" >&2
  exit 1
}
[[ -f "$PREFIX/lib/libcusparseLt_static.a" ]] || {
  echo "cuSPARSELt native prefix lacks lib/libcusparseLt_static.a: $PREFIX" >&2
  exit 1
}
[[ -f "$MANIFEST_JSON" ]] || { echo "cuSPARSELt sdk-boundary manifest not staged" >&2; exit 1; }

python3 - "$PREFIX/include/cusparseLt.h" "$MANIFEST_JSON" <<'PY'
import json
import re
import sys

header, manifest = sys.argv[1:3]
text = open(header, encoding="utf-8").read()
defs = dict(
    re.findall(
        r"^#define\s+(CUSPARSELT_VER_(?:MAJOR|MINOR|PATCH|BUILD))\s+([0-9]+)\b",
        text,
        re.M,
    )
)
version = ".".join(defs[k] for k in (
    "CUSPARSELT_VER_MAJOR",
    "CUSPARSELT_VER_MINOR",
    "CUSPARSELT_VER_PATCH",
    "CUSPARSELT_VER_BUILD",
))
data = json.load(open(manifest, encoding="utf-8"))
if data["mechanism"] != "sdk-boundary":
    raise SystemExit("wrong cuSPARSELt native mechanism")
if data["package"] != "cusparselt":
    raise SystemExit("wrong package in cuSPARSELt native manifest")
if data["expected_version"] != "0.8.1.1":
    raise SystemExit("wrong expected cuSPARSELt package version")
if data["actual_header_version"] != version:
    raise SystemExit("cuSPARSELt header version and manifest disagree")
if version != "0.8.1.1":
    raise SystemExit(f"wrong cuSPARSELt header version: {version}")
cuda = data.get("dependency_prefixes", {}).get("cuda", "")
if not cuda:
    raise SystemExit("cuSPARSELt native manifest lacks CUDA dependency prefix")
print(f"cusparselt:{data['expected_version']}:cuda={cuda}:ok")
PY
