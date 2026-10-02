#!/usr/bin/env bash
# Generate spack_graph.lock.json using the Bazel-vendored Spack executable.
#
# This script is intended to run via `bazel run //tools:spack_lock` inside the
# insula. Bazel supplies @spack_dist//:spack and spack_to_bazel.py in runfiles;
# Spack state is rooted under /vaso by tools/spack_dist.bzl.
set -euo pipefail

if [[ "${VASO_IN_INSULA:-0}" != "1" ]]; then
  echo "spack_lock must run inside the hermetic insula (VASO_IN_INSULA=1)" >&2
  exit 2
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ -f "$SCRIPT_DIR/lean_font_resources.sh" ]]; then
  . "$SCRIPT_DIR/lean_font_resources.sh"
else
  . "tools/lean_font_resources.sh"
fi

export PATH="/usr/bin:/bin:${PATH:-}"
export CC="${CC:-/usr/bin/gcc}"
export CXX="${CXX:-/usr/bin/g++}"
if command -v gfortran >/dev/null 2>&1; then
  export FC="${FC:-/usr/bin/gfortran}"
  export F77="${F77:-/usr/bin/gfortran}"
fi

SPACK_RUNFILE="$1"
LOCK_TOOL_RUNFILE="$2"
BUILD_GRAPH_RUNFILE="$3"
shift 3

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

SPACK_BIN="$(runfile_path "$SPACK_RUNFILE")"
LOCK_TOOL="$(runfile_path "$LOCK_TOOL_RUNFILE")"
BUILD_GRAPH_TOOL="$(runfile_path "$BUILD_GRAPH_RUNFILE")"

SPACK_MISC_CACHE="${SPACK_MISC_CACHE:-/vaso/cache/spack/misc-cache}"
export SPACK_MISC_CACHE

invalidate_vaso_overlay_patch_cache() {
  local patch_cache_dir="$SPACK_MISC_CACHE/patches"
  [[ -d "$patch_cache_dir" ]] || return 0

  local removed=0
  local index
  shopt -s nullglob
  for index in "$patch_cache_dir"/vaso_overlay-*-index.json; do
    rm -f "$index"
    removed=1
  done
  shopt -u nullglob

  if [[ "$removed" == "1" ]]; then
    echo "spack_lock: invalidated vaso_overlay patch cache under $patch_cache_dir" >&2
  fi
}

INSTALL=1
BUILD_GRAPH_OUT=""
GRAPH_ONLY=0
GRAPH_NO_PREFIX=0
FRESH=0
REQUIRE_LEAN_FONT_RESOURCES="${VASO_LEAN_FONT_RESOURCES:-0}"
ARGS=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --graph-only)
      GRAPH_ONLY=1
      INSTALL=0
      shift
      ;;
    --graph-no-prefix)
      GRAPH_NO_PREFIX=1
      shift
      ;;
    --fresh)
      FRESH=1
      shift
      ;;
    --no-install)
      INSTALL=0
      shift
      ;;
    --build-graph-out)
      BUILD_GRAPH_OUT="$2"
      shift 2
      ;;
    --native-overrides)
      if [[ $# -lt 2 ]]; then
        echo "spack_lock: --native-overrides requires a path" >&2
        exit 2
      fi
      p="$2"
      if [[ "$p" != /* && ! -e "$p" && -n "${BUILD_WORKING_DIRECTORY:-}" && -e "$BUILD_WORKING_DIRECTORY/$p" ]]; then
        p="$BUILD_WORKING_DIRECTORY/$p"
      fi
      ARGS+=("$1" "$p")
      shift 2
      ;;
    *)
      ARGS+=("$1")
      shift
      ;;
  esac
done

ROOT=""
for ((i = 0; i < ${#ARGS[@]}; i++)); do
  if [[ "${ARGS[$i]}" == "--root" && $((i + 1)) -lt ${#ARGS[@]} ]]; then
    ROOT="${ARGS[$((i + 1))]}"
    break
  fi
done
if [[ -z "$ROOT" ]]; then
  echo "spack_lock requires --root <spec>" >&2
  exit 2
fi

ROOT="$(normalize_lean_font_resources "$ROOT")"
for ((i = 0; i < ${#ARGS[@]}; i++)); do
  if [[ "${ARGS[$i]}" == "--root" && $((i + 1)) -lt ${#ARGS[@]} ]]; then
    ARGS[$((i + 1))]="$ROOT"
    break
  fi
done

invalidate_vaso_overlay_patch_cache

"$SPACK_BIN" compiler find

FRESH_ARGS=()
INSTALL_SOLVER_ARGS=(--reuse)
if [[ "${VASO_SPACK_FRESH:-0}" == "1" || "$FRESH" == "1" ]]; then
  FRESH_ARGS=(--fresh)
  INSTALL_SOLVER_ARGS=(--fresh)
fi

if [[ "$GRAPH_ONLY" == "1" ]]; then
  if [[ -z "$BUILD_GRAPH_OUT" ]]; then
    echo "spack_lock: --graph-only requires --build-graph-out <path>" >&2
    exit 2
  fi
  graph_args=()
  for ((i = 0; i < ${#ARGS[@]}; i++)); do
    case "${ARGS[$i]}" in
      --reuse-if-valid)
        ;;
      --out)
        i=$((i + 1))
        ;;
      *)
        graph_args+=("${ARGS[$i]}")
        ;;
    esac
  done
  if [[ "$GRAPH_NO_PREFIX" == "1" ]]; then
    graph_args+=(--no-prefix)
  fi
  if [[ "$REQUIRE_LEAN_FONT_RESOURCES" == "1" ]]; then
    graph_args+=(--require-lean-font-resources)
  fi
  graph_args=("${FRESH_ARGS[@]}" "${graph_args[@]}")
  python3 "$BUILD_GRAPH_TOOL" --spack "$SPACK_BIN" --out "$BUILD_GRAPH_OUT" "${graph_args[@]}"
  exit 0
fi

if [[ "$INSTALL" == "1" ]]; then
  read -r -a INSTALL_ARGS <<< "${VASO_SPACK_INSTALL_ARGS:-}"
  "$SPACK_BIN" install "${INSTALL_SOLVER_ARGS[@]}" "${INSTALL_ARGS[@]}" "$ROOT"
fi

python3 "$LOCK_TOOL" --spack "$SPACK_BIN" "${FRESH_ARGS[@]}" "${ARGS[@]}"

if [[ -n "$BUILD_GRAPH_OUT" ]]; then
  graph_args=()
  for ((i = 0; i < ${#ARGS[@]}; i++)); do
    case "${ARGS[$i]}" in
      --reuse-if-valid)
        ;;
      --out)
        i=$((i + 1))
        ;;
      *)
        graph_args+=("${ARGS[$i]}")
        ;;
    esac
  done
  if [[ "$GRAPH_NO_PREFIX" == "1" ]]; then
    graph_args+=(--no-prefix)
  fi
  if [[ "$REQUIRE_LEAN_FONT_RESOURCES" == "1" ]]; then
    graph_args+=(--require-lean-font-resources)
  fi
  graph_args=("${FRESH_ARGS[@]}" "${graph_args[@]}")
  python3 "$BUILD_GRAPH_TOOL" --spack "$SPACK_BIN" --out "$BUILD_GRAPH_OUT" "${graph_args[@]}"
fi
