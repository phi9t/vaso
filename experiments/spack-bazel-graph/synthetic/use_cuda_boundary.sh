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

PREFIX_PATH="$(find_runfile '*/+cuda_native+cuda_native/prefix_path.txt')"
BOUNDARY_JSON="$(find_runfile '*/+cuda_native+cuda_native/sdk_boundary.json')"
PREFIX="$(tr -d '\n' < "$PREFIX_PATH")"
[[ -x "$PREFIX/bin/nvcc" ]] || { echo "cuda boundary prefix lacks bin/nvcc: $PREFIX" >&2; exit 1; }
[[ -f "$BOUNDARY_JSON" ]] || { echo "cuda boundary manifest not staged" >&2; exit 1; }

version="$("$PREFIX/bin/nvcc" --version | sed -n 's/.*release [^,]*, V\([^[:space:]]*\).*/\1/p' | tail -1)"
python3 - "$BOUNDARY_JSON" "$version" <<'PY'
import json
import sys

manifest, version = sys.argv[1:3]
data = json.load(open(manifest))
if data["mechanism"] != "sdk-boundary":
    raise SystemExit("wrong boundary mechanism")
if data["actual_nvcc_version"] != version:
    raise SystemExit("nvcc version and boundary manifest disagree")
deps = data.get("installer_build_deps", {})
for key in ("coreutils", "gzip", "libxml2"):
    if key not in deps or not deps[key]:
        raise SystemExit(f"missing installer build dep {key}")
print(f"cuda-boundary:{version}:{data['rootfs_bundle']['ubuntu_version']}:ok")
PY
