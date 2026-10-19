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

PREFIX_PATH="$(find_runfile '*/+nccl_native+nccl_native/prefix_path.txt')"
MANIFEST_JSON="$(find_runfile '*/+nccl_native+nccl_native/sdk_boundary.json')"
PREFIX="$(tr -d '\n' < "$PREFIX_PATH")"

[[ -f "$PREFIX/include/nccl.h" ]] || {
  echo "NCCL native prefix lacks include/nccl.h: $PREFIX" >&2
  exit 1
}
[[ -e "$PREFIX/lib/libnccl.so" ]] || {
  echo "NCCL native prefix lacks lib/libnccl.so: $PREFIX" >&2
  exit 1
}
[[ -f "$MANIFEST_JSON" ]] || { echo "NCCL sdk-boundary manifest not staged" >&2; exit 1; }

python3 - "$PREFIX/include/nccl.h" "$MANIFEST_JSON" <<'PY'
import json
import re
import sys

header, manifest = sys.argv[1:3]
text = open(header, encoding="utf-8").read()
defs = dict(re.findall(r"^#define\s+(NCCL_(?:MAJOR|MINOR|PATCH))\s+([0-9]+)\b", text, re.M))
version = ".".join(defs[k] for k in ("NCCL_MAJOR", "NCCL_MINOR", "NCCL_PATCH"))
data = json.load(open(manifest, encoding="utf-8"))
if data["mechanism"] != "sdk-boundary":
    raise SystemExit("wrong NCCL native mechanism")
if data["package"] != "nccl":
    raise SystemExit("wrong package in NCCL native manifest")
if data["expected_version"] != "2.30.7":
    raise SystemExit("wrong expected NCCL package version")
if data["actual_header_version"] != version:
    raise SystemExit("NCCL header version and manifest disagree")
if version != "2.30.7":
    raise SystemExit(f"wrong NCCL header version: {version}")
cuda = data.get("dependency_prefixes", {}).get("cuda", "")
if not cuda:
    raise SystemExit("NCCL native manifest lacks CUDA dependency prefix")
print(f"nccl:{data['expected_version']}:cuda={cuda}:ok")
PY
