#!/usr/bin/env bash
# Generic ABI-parity gate for a native package flip.
#
# Compares a native repo's install tree (the candidate, staged into runfiles)
# against a Spack reference prefix along layout / soname+symbol / link-and-run
# axes via tools/abi_parity.py. Green only when the native tree is prefix- and
# ABI-identical to Spack's AND a downstream consumer linked against each prints
# identical output. This drives every per-package flip gate (zlib-ng, utf8proc,
# ...), replacing the per-package copies.
#
# Args:
#   $1 = path to abi_parity.py
#   $2 = path to the consumer C source
#   $3 = native repo dir marker (e.g. "zlib_ng_native", "utf8proc_native"):
#        the @<repo>//:prefix filegroup stages the tree under a bzlmod-mangled
#        runfiles dir matching *<marker>*/prefix
#   $4 = link-lib stem (e.g. "z", "utf8proc") or a comma-separated list
#        (e.g. "iconv,charset"); use "-" for no C consumer/link axis
#   $5 = env var name holding the Spack reference prefix (host absolute path)
#   $6... = optional extra args passed through to abi_parity.py
# The reference prefix is a host path outside the Bazel sandbox, so it arrives
# via --test_env=<VAR>=<prefix>; if unset the test SKIPS loudly.
set -euo pipefail

ABI_PARITY="$1"
CONSUMER="$2"
NATIVE_MARKER="$3"
LINK_LIB="$4"
REF_ENV="$5"

REF="${!REF_ENV:-}"
if [[ -z "$REF" ]]; then
  echo "SKIP: $REF_ENV not set (Spack reference prefix). run.sh injects it via --test_env." >&2
  exit 0
fi
if [[ ! -d "$REF" ]]; then
  echo "FAIL: $REF_ENV=$REF is not a directory" >&2
  exit 1
fi

runfile_path() {
  local rel="$1"
  local base manifest_key manifest_value
  if [[ "$rel" = /* && -e "$rel" ]]; then
    echo "$rel"
    return 0
  fi
  if [[ -n "${RUNFILES_MANIFEST_FILE:-}" && -f "$RUNFILES_MANIFEST_FILE" ]]; then
    while IFS= read -r line; do
      manifest_key="${line%% *}"
      manifest_value="${line#* }"
      [[ "$manifest_key" == "$line" ]] && manifest_value="$manifest_key"
      if [[ "$manifest_key" == "$rel" && ( -e "$manifest_value" || -L "$manifest_value" ) ]]; then
        echo "$manifest_value"
        return 0
      fi
    done < "$RUNFILES_MANIFEST_FILE"
  fi
  for base in "${RUNFILES_DIR:-}" "$PWD" "$PWD/.." "$0.runfiles"; do
    [[ -n "$base" && -d "$base" ]] || continue
    if [[ -e "$base/$rel" || -L "$base/$rel" ]]; then
      echo "$base/$rel"
      return 0
    fi
  done
  return 1
}

find_runfile_by_pattern() {
  local pattern="$1"
  local base hit manifest_key manifest_value
  if [[ -n "${RUNFILES_MANIFEST_FILE:-}" && -f "$RUNFILES_MANIFEST_FILE" ]]; then
    while IFS= read -r line; do
      manifest_key="${line%% *}"
      manifest_value="${line#* }"
      [[ "$manifest_key" == "$line" ]] && manifest_value="$manifest_key"
      case "$manifest_key" in
        $pattern)
          if [[ -e "$manifest_value" || -L "$manifest_value" ]]; then
            echo "$manifest_value"
            return 0
          fi
          ;;
      esac
    done < "$RUNFILES_MANIFEST_FILE"
  fi
  for base in "${RUNFILES_DIR:-}" "$PWD" "$PWD/.." "$0.runfiles"; do
    [[ -n "$base" && -d "$base" ]] || continue
    hit="$(find "$base" -path "$pattern" -print -quit 2>/dev/null)"
    if [[ -n "$hit" && ( -e "$hit" || -L "$hit" ) ]]; then
      echo "$hit"
      return 0
    fi
  done
  return 1
}

# Locate the native candidate prefix in runfiles by its bzlmod-mangled marker.
find_native_prefix() {
  local hit
  hit="$(find_runfile_by_pattern "*${NATIVE_MARKER}*/prefix_path.txt" || true)"
  if [[ -n "$hit" ]]; then
    if [[ -r "$hit" ]]; then
      hit="$(tr -d '\n' < "$hit")"
      if [[ -d "$hit" ]]; then echo "$hit"; return 0; fi
    fi
  fi
  hit="$(find_runfile_by_pattern "*${NATIVE_MARKER}*/prefix" || true)"
  if [[ -n "$hit" && -d "$hit" ]]; then echo "$hit"; return 0; fi
  return 1
}

shift 5
prefix_file=""
pass_args=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --candidate-prefix-file)
      [[ $# -ge 2 ]] || { echo "FAIL: --candidate-prefix-file requires a runfile path" >&2; exit 1; }
      prefix_file="$(runfile_path "$2" || true)"
      [[ -n "$prefix_file" ]] || { echo "FAIL: could not locate candidate prefix file: $2" >&2; exit 1; }
      shift 2
      ;;
    *)
      pass_args+=("$1")
      shift
      ;;
  esac
done
set -- "${pass_args[@]}"

CAND=""
if [[ -n "$prefix_file" ]]; then
  CAND="$(tr -d '\n' < "$prefix_file")"
  [[ -d "$CAND" ]] || { echo "FAIL: candidate prefix from $prefix_file is not a directory: $CAND" >&2; exit 1; }
fi
if [[ -z "$CAND" ]]; then
  CAND="$(find_native_prefix || true)"
fi
if [[ -z "$CAND" ]]; then
  echo "FAIL: could not locate native prefix (marker=$NATIVE_MARKER) in runfiles" >&2
  exit 1
fi
echo "reference (spack):  $REF"
echo "candidate (native): $CAND"

# binutils/gcc are on PATH inside the sealed insula. abi_parity honors
# NM/READELF/CC overrides; default PATH resolution is fine here.
case "$CONSUMER" in
  *.cc|*.cpp|*.cxx|*.C) export CC="${CC:-${CXX:-c++}}" ;;
esac
cmd=(python3 "$ABI_PARITY" --reference "$REF" --candidate "$CAND" --consumer "$CONSUMER")
if [[ "$CONSUMER" == "-" ]]; then
  cmd=(python3 "$ABI_PARITY" --reference "$REF" --candidate "$CAND")
else
  cmd=(python3 "$ABI_PARITY" --reference "$REF" --candidate "$CAND" --consumer "$CONSUMER")
  for lib in ${LINK_LIB//,/ }; do
    [[ -n "$lib" && "$lib" != "-" ]] || continue
    cmd+=(--link-lib "$lib")
  done
fi
while [[ $# -gt 0 ]]; do
  case "$1" in
    --reference-link-prefix-env)
      [[ $# -ge 2 ]] || { echo "FAIL: --reference-link-prefix-env requires an env var name" >&2; exit 1; }
      dep_ref="${!2:-}"
      [[ -n "$dep_ref" ]] || { echo "FAIL: $2 not set for --reference-link-prefix-env" >&2; exit 1; }
      [[ -d "$dep_ref" ]] || { echo "FAIL: $2=$dep_ref is not a directory" >&2; exit 1; }
      cmd+=(--reference-link-prefix "$dep_ref")
      shift 2
      ;;
    --candidate-link-prefix-marker)
      [[ $# -ge 2 ]] || { echo "FAIL: --candidate-link-prefix-marker requires a marker" >&2; exit 1; }
      dep_cand="$(NATIVE_MARKER="$2" find_native_prefix || true)"
      [[ -n "$dep_cand" ]] || { echo "FAIL: could not locate native dependency prefix (marker=$2) in runfiles" >&2; exit 1; }
      cmd+=(--candidate-link-prefix "$dep_cand")
      shift 2
      ;;
    *)
      cmd+=("$1")
      shift
      ;;
  esac
done
exec "${cmd[@]}"
