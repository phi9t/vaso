#!/usr/bin/env bash
set -euo pipefail

find_native_prefix() {
  local base hit
  for base in "${RUNFILES_DIR:-}" "$PWD" "$PWD/.." "$0.runfiles"; do
    [[ -n "$base" && -d "$base" ]] || continue
    hit="$(find "$base" -type d -path "*util_macros_native*/prefix" 2>/dev/null | head -1)"
    if [[ -n "$hit" ]]; then echo "$hit"; return 0; fi
  done
  return 1
}

PREFIX="$(find_native_prefix)"

test -f "$PREFIX/share/aclocal/xorg-macros.m4"
test -f "$PREFIX/share/pkgconfig/xorg-macros.pc"
test -f "$PREFIX/share/util-macros/INSTALL"

grep -q "m4_defun(\\[XORG_MACROS_VERSION\\]" "$PREFIX/share/aclocal/xorg-macros.m4"
grep -q "Name: X.Org Macros" "$PREFIX/share/pkgconfig/xorg-macros.pc"
grep -q "Version: 1.20.2" "$PREFIX/share/pkgconfig/xorg-macros.pc"

echo "util-macros:1.20.2:ok"
