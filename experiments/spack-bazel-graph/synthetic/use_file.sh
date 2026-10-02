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

PREFIX="$(find_runfiles_prefix file_native)" || {
  echo "could not locate file_native prefix in runfiles" >&2
  exit 1
}
BZIP2_PREFIX="$(find_runfiles_prefix bzip2_native)" || {
  echo "could not locate bzip2_native prefix in runfiles" >&2
  exit 1
}
XZ_PREFIX="$(find_runfiles_prefix xz_native)" || {
  echo "could not locate xz_native prefix in runfiles" >&2
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
export LD_LIBRARY_PATH="${PREFIX}/lib:${ZSTD_PREFIX}/lib:${XZ_PREFIX}/lib:${BZIP2_PREFIX}/lib:${ZLIB_PREFIX}/lib${LD_LIBRARY_PATH:+:${LD_LIBRARY_PATH}}"

test -x "$PREFIX/bin/file"
test -f "$PREFIX/include/magic.h"
test -f "$PREFIX/lib/libmagic.so.1.0.0"
test -L "$PREFIX/lib/libmagic.so"
test -f "$PREFIX/lib/libmagic.a"
test -f "$PREFIX/lib/pkgconfig/libmagic.pc"
test -f "$PREFIX/share/misc/magic.mgc"

"$PREFIX/bin/file" --version | grep -q "file-5.46"
printf 'hello file\n' > /tmp/file-native-smoke.txt
"$PREFIX/bin/file" /tmp/file-native-smoke.txt | grep -q "ASCII text"

echo "file:5.46:ok"
