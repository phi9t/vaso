#!/usr/bin/env bash
set -euo pipefail

find_native_prefix() {
  local base hit
  for base in "${RUNFILES_DIR:-}" "$PWD" "$PWD/.." "$0.runfiles"; do
    [[ -n "$base" && -d "$base" ]] || continue
    hit="$(find "$base" -type d -path "*bdftopcf_native*/prefix" 2>/dev/null | head -1)"
    if [[ -n "$hit" ]]; then echo "$hit"; return 0; fi
  done
  return 1
}

PREFIX="$(find_native_prefix)"

test -x "$PREFIX/bin/bdftopcf"
test -f "$PREFIX/share/man/man1/bdftopcf.1"

version="$("$PREFIX/bin/bdftopcf" -v)"
[[ "$version" == "bdftopcf 1.1.2" ]]

font="$(mktemp)"
pcf="$(mktemp)"
trap 'rm -f "$font" "$pcf"' EXIT
cat > "$font" <<'EOF'
STARTFONT 2.1
FONT -misc-fixed-medium-r-normal--13-120-75-75-C-70-iso10646-1
SIZE 13 75 75
FONTBOUNDINGBOX 7 13 0 -2
STARTPROPERTIES 2
FONT_ASCENT 11
FONT_DESCENT 2
ENDPROPERTIES
CHARS 1
STARTCHAR A
ENCODING 65
SWIDTH 500 0
DWIDTH 7 0
BBX 7 9 0 0
BITMAP
10
28
44
44
7C
82
82
00
00
ENDCHAR
ENDFONT
EOF

"$PREFIX/bin/bdftopcf" -o "$pcf" "$font"
test -s "$pcf"

echo "bdftopcf:1.1.2:ok"
