#!/usr/bin/env bash
set -euo pipefail

find_native_prefix() {
  local base hit
  for base in "${RUNFILES_DIR:-}" "$PWD" "$PWD/.." "$0.runfiles"; do
    [[ -n "$base" && -d "$base" ]] || continue
    hit="$(find "$base" -type d -path "*protobuf_native*/prefix" 2>/dev/null | head -1)"
    if [[ -n "$hit" ]]; then echo "$hit"; return 0; fi
  done
  return 1
}

PREFIX="$(find_native_prefix)"

test -x "$PREFIX/bin/protoc"
test -x "$PREFIX/bin/protoc-3.21.12.0"
test -f "$PREFIX/include/google/protobuf/message.h"
test -f "$PREFIX/include/google/protobuf/descriptor.h"
test -e "$PREFIX/lib/libprotobuf.so"
test -e "$PREFIX/lib/libprotobuf-lite.so"
test -e "$PREFIX/lib/libprotoc.so"
test -f "$PREFIX/lib/pkgconfig/protobuf.pc"
test -f "$PREFIX/lib/pkgconfig/protobuf-lite.pc"
test -f "$PREFIX/lib/cmake/protobuf/protobuf-config.cmake"
test -f "$PREFIX/lib/cmake/protobuf/protobuf-targets.cmake"

grep -q "Version: 3.21.12.0" "$PREFIX/lib/pkgconfig/protobuf.pc"
"$PREFIX/bin/protoc" --version | grep -q "libprotoc 3.21.12"

echo "protobuf:3.21.12:ok"
