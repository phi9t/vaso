#!/usr/bin/env bash
set -euo pipefail

find_native_prefix() {
  local base hit
  for base in "${RUNFILES_DIR:-}" "$PWD" "$PWD/.." "$0.runfiles"; do
    [[ -n "$base" && -d "$base" ]] || continue
    hit="$(find "$base" -type d -path "*xxd_standalone_native*/prefix" 2>/dev/null | head -1)"
    if [[ -n "$hit" ]]; then echo "$hit"; return 0; fi
  done
  return 1
}

PREFIX="$(find_native_prefix)"
plain="$(mktemp)"
dump="$(mktemp)"
roundtrip="$(mktemp)"
trap 'rm -f "$plain" "$dump" "$roundtrip"' EXIT

printf 'alpha\nbeta\ngamma\n' > "$plain"
"$PREFIX/bin/xxd" "$plain" > "$dump"
"$PREFIX/bin/xxd" -r "$dump" "$roundtrip"
cmp -s "$plain" "$roundtrip"
echo "xxd-standalone:ok"
