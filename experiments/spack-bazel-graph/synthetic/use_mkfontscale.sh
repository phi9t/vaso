#!/usr/bin/env bash
set -euo pipefail

find_native_prefix() {
  local base hit
  for base in "${RUNFILES_DIR:-}" "$PWD" "$PWD/.." "$0.runfiles"; do
    [[ -n "$base" && -d "$base" ]] || continue
    hit="$(find "$base" -type d -path "*mkfontscale_native*/prefix" 2>/dev/null | head -1)"
    if [[ -n "$hit" ]]; then echo "$hit"; return 0; fi
  done
  return 1
}

PREFIX="$(find_native_prefix)"

test -x "$PREFIX/bin/mkfontscale"
test -x "$PREFIX/bin/mkfontdir"
test -f "$PREFIX/share/man/man1/mkfontscale.1"
test -f "$PREFIX/share/man/man1/mkfontdir.1"

version="$("$PREFIX/bin/mkfontscale" -v)"
[[ "$version" == "mkfontscale 1.2.3" ]]

workdir="$(mktemp -d)"
trap 'rm -rf "$workdir"' EXIT

"$PREFIX/bin/mkfontscale" "$workdir"
test -f "$workdir/fonts.scale"
[[ "$(cat "$workdir/fonts.scale")" == "0" ]]

"$PREFIX/bin/mkfontdir" "$workdir"
test -f "$workdir/fonts.dir"
[[ "$(cat "$workdir/fonts.dir")" == "0" ]]

echo "mkfontscale:1.2.3:ok"
