#!/usr/bin/env bash
set -euo pipefail

find_native_prefix() {
  local base hit
  for base in "${RUNFILES_DIR:-}" "$PWD" "$PWD/.." "$0.runfiles"; do
    [[ -n "$base" && -d "$base" ]] || continue
    hit="$(find "$base" -type d -path "*font_util_native*/prefix" 2>/dev/null | head -1)"
    if [[ -n "$hit" ]]; then echo "$hit"; return 0; fi
  done
  return 1
}

PREFIX="$(find_native_prefix)"

test -x "$PREFIX/bin/bdftruncate"
test -x "$PREFIX/bin/ucs2any"
test -f "$PREFIX/lib/pkgconfig/fontutil.pc"
test -f "$PREFIX/share/aclocal/fontutil.m4"
test -f "$PREFIX/share/fonts/X11/encodings/encodings.dir"
test -f "$PREFIX/share/fonts/X11/encodings/large/encodings.dir"
test -f "$PREFIX/share/fonts/X11/util/map-ISO8859-1"
test -f "$PREFIX/share/man/man1/bdftruncate.1"
test -f "$PREFIX/share/man/man1/ucs2any.1"

mapfile -t font_dirs < <(find "$PREFIX/share/fonts/X11" -mindepth 1 -maxdepth 1 -type d -printf '%f\n' | sort)
[[ "${font_dirs[*]}" == "encodings util" ]]

grep -q "Version: 1.4.1" "$PREFIX/lib/pkgconfig/fontutil.pc"
grep -q "m4_defun(\\[XORG_FONT_MACROS_VERSION\\]" "$PREFIX/share/aclocal/fontutil.m4"
"$PREFIX/bin/bdftruncate" 0x3200 </dev/null >/tmp/font-util-bdftruncate.out
[[ ! -s /tmp/font-util-bdftruncate.out ]]
"$PREFIX/bin/ucs2any" >/tmp/font-util-ucs2any.out 2>&1
grep -q "Usage: ucs2any" /tmp/font-util-ucs2any.out

echo "font-util:1.4.1:fonts=encodings:ok"
