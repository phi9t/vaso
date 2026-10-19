#!/usr/bin/env bash
set -euo pipefail

find_native_prefix() {
  local marker="$1"
  local base hit
  for base in "${RUNFILES_DIR:-}" "$PWD" "$PWD/.." "$0.runfiles"; do
    [[ -n "$base" && -d "$base" ]] || continue
    hit="$(find "$base" -type d -path "*${marker}*/prefix" 2>/dev/null | head -1)"
    if [[ -n "$hit" ]]; then echo "$hit"; return 0; fi
  done
  return 1
}

PREFIX="$(find_native_prefix mkfontdir_native)"
MKFONTSCALE_PREFIX="$(find_native_prefix mkfontscale_native)"

test -x "$PREFIX/bin/mkfontdir"
test -f "$PREFIX/share/man/man1/mkfontdir.1"
test -x "$MKFONTSCALE_PREFIX/bin/mkfontscale"

version="$(PATH="$MKFONTSCALE_PREFIX/bin:$PATH" "$PREFIX/bin/mkfontdir" -v)"
[[ "$version" == "mkfontscale 1.2.3" ]]

workdir="$(mktemp -d)"
trap 'rm -rf "$workdir"' EXIT

PATH="$MKFONTSCALE_PREFIX/bin:$PATH" "$PREFIX/bin/mkfontdir" "$workdir"
test -f "$workdir/fonts.dir"
[[ "$(cat "$workdir/fonts.dir")" == "0" ]]

echo "mkfontdir:1.0.7:ok"
