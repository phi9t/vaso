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

PREFIX_PATH="$(find_runfile '*/+cudnn_native+cudnn_native/prefix_path.txt')"
MANIFEST_JSON="$(find_runfile '*/+cudnn_native+cudnn_native/sdk_boundary.json')"
PREFIX="$(tr -d '\n' < "$PREFIX_PATH")"

[[ -f "$PREFIX/include/cudnn_version.h" ]] || {
  echo "cuDNN native prefix lacks include/cudnn_version.h: $PREFIX" >&2
  exit 1
}
[[ -e "$PREFIX/lib/libcudnn.so" || -e "$PREFIX/lib64/libcudnn.so" ]] || {
  echo "cuDNN native prefix lacks libcudnn.so: $PREFIX" >&2
  exit 1
}
[[ -f "$MANIFEST_JSON" ]] || { echo "cuDNN sdk-boundary manifest not staged" >&2; exit 1; }

python3 - "$PREFIX/include/cudnn_version.h" "$MANIFEST_JSON" <<'PY'
import json
import re
import sys

header, manifest = sys.argv[1:3]
text = open(header, encoding="utf-8").read()
defs = dict(re.findall(r"^#define\s+(CUDNN_(?:MAJOR|MINOR|PATCHLEVEL))\s+([0-9]+)\b", text, re.M))
version = ".".join(defs[k] for k in ("CUDNN_MAJOR", "CUDNN_MINOR", "CUDNN_PATCHLEVEL"))
data = json.load(open(manifest, encoding="utf-8"))
if data["mechanism"] != "sdk-boundary":
    raise SystemExit("wrong cuDNN native mechanism")
if data["package"] != "cudnn":
    raise SystemExit("wrong package in cuDNN native manifest")
if data["expected_version"] != "9.24.0.43":
    raise SystemExit("wrong expected cuDNN package version")
if data["actual_header_version"] != version:
    raise SystemExit("cuDNN header version and manifest disagree")
if version != "9.24.0":
    raise SystemExit(f"wrong cuDNN header version: {version}")
cuda = data.get("dependency_prefixes", {}).get("cuda", "")
if not cuda:
    raise SystemExit("cuDNN native manifest lacks CUDA dependency prefix")
print(f"cudnn:{data['expected_version']}:cuda={cuda}:ok")
PY
