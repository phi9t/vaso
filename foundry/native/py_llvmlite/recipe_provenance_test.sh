#!/usr/bin/env bash
set -euo pipefail

if [[ "${VASO_IN_INSULA:-0}" != "1" ]]; then
  echo "run me inside the insula: run.sh --insula-cmd bazel test //native/py_llvmlite:recipe_provenance_test" >&2
  exit 2
fi

TOOL_RUNFILE="$1"
SPACK_RUNFILE="$2"

runfile_path() {
  local rel="$1"
  if [[ "$rel" = /* && -e "$rel" ]]; then
    echo "$rel"
    return 0
  fi
  for base in "${RUNFILES_DIR:-}" "${0}.runfiles" "$PWD"; do
    [[ -n "$base" && -d "$base" ]] || continue
    if [[ -e "$base/$rel" ]]; then
      echo "$base/$rel"
      return 0
    fi
  done
  echo "could not locate runfile: $rel" >&2
  return 1
}

TOOL="$(runfile_path "$TOOL_RUNFILE")"
SPACK_BIN="$(runfile_path "$SPACK_RUNFILE")"
OUT="${TEST_TMPDIR:?TEST_TMPDIR must be set by Bazel}/py_llvmlite_recipe_provenance.json"

python3 "$TOOL" \
  --spack "$SPACK_BIN" \
  --out "$OUT" \
  --query llvmlite \
  --require-present py-llvmlite \
  --require-present llvm \
  --require-version py-llvmlite@0.47.0 \
  --require-version llvm@20.1.8

python3 - "$OUT" <<'PY'
import json
import sys

data = json.load(open(sys.argv[1], encoding="utf-8"))
assert data["spack"]["version"].startswith("1.2."), data["spack"]
assert data["spack"]["executable"].endswith("/spack"), data["spack"]
assert "+spack_toolchain+spack_dist" in data["spack"]["executable"], data["spack"]
assert data["spack"]["user_cache"] == "/vaso/cache/spack/user", data["spack"]
llvmlite = data["packages"]["py-llvmlite"]
llvm = data["packages"]["llvm"]
assert llvmlite["class"] == "PyLlvmlite", llvmlite
assert "python" in llvmlite["build_systems"], llvmlite
assert "0.47.0" in llvmlite["versions"], llvmlite["versions"]
assert "llvm@20" in llvmlite["dependencies"], llvmlite["dependencies"]
assert "python@3.10:3.14" in llvmlite["dependencies"], llvmlite["dependencies"]
assert llvm["class"] == "LlvmDetection", llvm
assert "cmake" in llvm["build_systems"], llvm
assert "20.1.8" in llvm["versions"], llvm["versions"]
print(json.dumps({
    "package_root": data["package_root"],
    "spack": data["spack"],
    "py_llvmlite_recipe": llvmlite["recipe"],
    "llvm_recipe": llvm["recipe"],
}, sort_keys=True))
PY
