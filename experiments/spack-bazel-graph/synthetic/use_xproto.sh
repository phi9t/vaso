#!/usr/bin/env bash
set -euo pipefail

find_prefix() {
  local base hit
  for base in "${RUNFILES_DIR:-}" "$PWD" "$PWD/.." "$0.runfiles"; do
    [[ -n "$base" && -d "$base" ]] || continue
    hit="$(find "$base" -type d -path "*xproto_native*/prefix" 2>/dev/null | head -1)"
    if [[ -n "$hit" ]]; then echo "$hit"; return 0; fi
  done
  return 1
}

PREFIX="$(find_prefix)"

for path in \
  include/X11/X.h \
  include/X11/Xproto.h \
  include/X11/keysymdef.h \
  lib/pkgconfig/xproto.pc \
  share/doc/xproto/x11protocol.xml; do
  test -f "$PREFIX/$path"
done

grep -q "Name: Xproto" "$PREFIX/lib/pkgconfig/xproto.pc"
grep -q "Version: 7.0.31" "$PREFIX/lib/pkgconfig/xproto.pc"
grep -q "#define X_PROTOCOL" "$PREFIX/include/X11/X.h"
grep -q "#define sz_xSegment 8" "$PREFIX/include/X11/Xproto.h"
grep -q "X Window System Protocol" "$PREFIX/share/doc/xproto/x11protocol.xml"

echo "xproto:7.0.31:ok"
