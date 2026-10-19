#!/usr/bin/env bash
set -euo pipefail

find_native_prefix() {
  local base hit
  for base in "${RUNFILES_DIR:-}" "$PWD" "$PWD/.." "$0.runfiles"; do
    [[ -n "$base" && -d "$base" ]] || continue
    hit="$(find "$base" -type d -path "*fontsproto_native*/prefix" 2>/dev/null | head -1)"
    if [[ -n "$hit" ]]; then echo "$hit"; return 0; fi
  done
  return 1
}

PREFIX="$(find_native_prefix)"

for rel in \
  include/X11/fonts/FS.h \
  include/X11/fonts/FSproto.h \
  include/X11/fonts/font.h \
  include/X11/fonts/fontproto.h \
  include/X11/fonts/fontstruct.h \
  include/X11/fonts/fsmasks.h \
  lib/pkgconfig/fontsproto.pc \
  share/doc/fontsproto/fsproto.xml; do
  test -f "$PREFIX/$rel"
done

grep -q "Name: FontsProto" "$PREFIX/lib/pkgconfig/fontsproto.pc"
grep -q "Version: 2.1.3" "$PREFIX/lib/pkgconfig/fontsproto.pc"
grep -q "#define.*FontLoadAll" "$PREFIX/include/X11/fonts/font.h"
grep -q "The X Font Service Protocol" "$PREFIX/share/doc/fontsproto/fsproto.xml"

echo "fontsproto:2.1.3:ok"
