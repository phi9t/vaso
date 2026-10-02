#!/usr/bin/env bash
set -euo pipefail

if [[ "${VASO_IN_INSULA:-0}" != "1" ]]; then
  echo "run me inside the insula: run.sh --insula-cmd bazel test //formal/lean:lean_abi_parity_test" >&2
  exit 2
fi

LAKE_ROOTPATH="${1:?lake rootpath missing}"
LEAN_ROOTPATH="${2:?lean rootpath missing}"

RUNFILES_ROOT="${TEST_SRCDIR:?TEST_SRCDIR missing}"
WORKSPACE_NAME="${TEST_WORKSPACE:?TEST_WORKSPACE missing}"

resolve_runfile() {
  local rel="$1"
  local candidate
  for candidate in \
    "$RUNFILES_ROOT/$WORKSPACE_NAME/$rel" \
    "$RUNFILES_ROOT/$rel"; do
    if [[ -e "$candidate" ]]; then
      printf '%s\n' "$candidate"
      return 0
    fi
  done
  echo "runfile not found: $rel" >&2
  return 1
}

LAKE_BIN="$(resolve_runfile "$LAKE_ROOTPATH")"
LEAN_BIN="$(resolve_runfile "$LEAN_ROOTPATH")"
LEAN_HOME="$(cd "$(dirname "$LEAN_BIN")/.." && pwd)"

WORKDIR="$TEST_TMPDIR/formal-lean"
mkdir -p "$WORKDIR/AbiParity"
cp "$(resolve_runfile formal/lean/AbiParity.lean)" "$WORKDIR/AbiParity.lean"
cp "$(resolve_runfile formal/lean/AbiParity/Basic.lean)" "$WORKDIR/AbiParity/Basic.lean"
cp "$(resolve_runfile formal/lean/lakefile.toml)" "$WORKDIR/lakefile.toml"
cp "$(resolve_runfile formal/lean/lake-manifest.json)" "$WORKDIR/lake-manifest.json"
cp "$(resolve_runfile formal/lean/lean-toolchain)" "$WORKDIR/lean-toolchain"

export PATH="$LEAN_HOME/bin:/usr/bin:/bin"
export LEAN_SYSROOT="$LEAN_HOME"
export LD_LIBRARY_PATH="$LEAN_HOME/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
export HOME="$TEST_TMPDIR/home"
mkdir -p "$HOME"

echo "lake: $("$LAKE_BIN" --version)"
echo "lean: $("$LEAN_BIN" --version)"
cd "$WORKDIR"
"$LAKE_BIN" build
