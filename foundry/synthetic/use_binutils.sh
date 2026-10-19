#!/usr/bin/env bash
set -euo pipefail

find_runfiles_prefix() {
  local marker="$1"
  local base hit
  for base in "${RUNFILES_DIR:-}" "$PWD" "$PWD/.." "$0.runfiles"; do
    [[ -n "$base" && -d "$base" ]] || continue
    hit="$(find "$base" -type d \( \
      -path "*/+${marker}+${marker}/prefix" -o \
      -path "*/${marker}/prefix" \
    \) -print -quit 2>/dev/null)"
    if [[ -n "$hit" ]]; then echo "$hit"; return 0; fi
  done
  return 1
}

require_path() {
  local kind="$1"
  local path="$2"
  case "$kind" in
    dir) [[ -d "$path" ]] ;;
    file) [[ -f "$path" ]] ;;
    exec) [[ -x "$path" ]] ;;
    symlink) [[ -L "$path" ]] ;;
    *) echo "unknown path kind: $kind" >&2; return 2 ;;
  esac || {
    echo "missing ${kind}: ${path}" >&2
    return 1
  }
}

PREFIX="$(find_runfiles_prefix binutils_native)" || {
  echo "could not locate binutils_native prefix in runfiles" >&2
  exit 1
}
ZLIB_PREFIX="$(find_runfiles_prefix zlib_ng_native)" || {
  echo "could not locate zlib_ng_native prefix in runfiles" >&2
  exit 1
}
ZSTD_PREFIX="$(find_runfiles_prefix zstd_native)" || {
  echo "could not locate zstd_native prefix in runfiles" >&2
  exit 1
}
export LD_LIBRARY_PATH="${PREFIX}/lib:${ZSTD_PREFIX}/lib:${ZLIB_PREFIX}/lib${LD_LIBRARY_PATH:+:${LD_LIBRARY_PATH}}"

for tool in ar nm objdump readelf strings strip; do
  require_path exec "$PREFIX/bin/$tool"
  "$PREFIX/bin/$tool" --version >/tmp/binutils-"$tool".version
  grep -Eq "GNU|Binutils" /tmp/binutils-"$tool".version
done

require_path file "$PREFIX/include/bfd.h"
require_path file "$PREFIX/include/plugin-api.h"
require_path file "$PREFIX/lib/libbfd-2.46.1.so"
require_path symlink "$PREFIX/lib/libbfd.so"
require_path file "$PREFIX/lib/libopcodes-2.46.1.so"
require_path file "$PREFIX/lib/libctf.so.0.0.0"
require_path file "$PREFIX/lib/libsframe.so.3.0.0"

printf 'payload\n' > /tmp/binutils-payload.txt
"$PREFIX/bin/ar" cr /tmp/libpayload.a /tmp/binutils-payload.txt
"$PREFIX/bin/ar" t /tmp/libpayload.a | grep -q '^binutils-payload.txt$'

echo "binutils:2.46.1:ok"
