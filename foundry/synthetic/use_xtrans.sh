#!/usr/bin/env bash
set -euo pipefail

find_prefix() {
  local base hit
  for base in "${RUNFILES_DIR:-}" "$PWD" "$PWD/.." "$0.runfiles"; do
    [[ -n "$base" && -d "$base" ]] || continue
    hit="$(find "$base" -type d -path "*xtrans_native*/prefix" 2>/dev/null | head -1)"
    if [[ -n "$hit" ]]; then echo "$hit"; return 0; fi
  done
  return 1
}

PREFIX="$(find_prefix)"

for path in \
  include/X11/Xtrans/Xtrans.h \
  include/X11/Xtrans/Xtransint.h \
  include/X11/Xtrans/Xtranssock.c \
  share/aclocal/xtrans.m4 \
  share/pkgconfig/xtrans.pc \
  share/doc/xtrans/xtrans.xml; do
  test -f "$PREFIX/$path"
done

grep -q "Name: XTrans" "$PREFIX/share/pkgconfig/xtrans.pc"
grep -q "Version: 1.6.0" "$PREFIX/share/pkgconfig/xtrans.pc"
grep -q "Abstract network code for X" "$PREFIX/share/pkgconfig/xtrans.pc"
grep -q "XTRANS_CONNECTION_FLAGS" "$PREFIX/share/aclocal/xtrans.m4"
grep -q "TRANS_CLIENT" "$PREFIX/include/X11/Xtrans/Xtrans.h"
grep -q "X Transport Interface" "$PREFIX/share/doc/xtrans/xtrans.xml"

echo "xtrans:1.6.0:ok"
