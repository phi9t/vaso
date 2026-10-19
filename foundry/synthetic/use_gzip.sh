#!/usr/bin/env bash
set -euo pipefail

find_native_prefix() {
  local base hit
  for base in "${RUNFILES_DIR:-}" "$PWD" "$PWD/.." "$0.runfiles"; do
    [[ -n "$base" && -d "$base" ]] || continue
    hit="$(find "$base" -type d -path "*gzip_native*/prefix" 2>/dev/null | head -1)"
    if [[ -n "$hit" ]]; then echo "$hit"; return 0; fi
  done
  return 1
}

PREFIX="$(find_native_prefix)"
plain="$(mktemp)"
compressed="$(mktemp)"
roundtrip="$(mktemp)"
trap 'rm -f "$plain" "$compressed" "$roundtrip"' EXIT
printf 'alpha\nbeta\ngamma\n' > "$plain"

"$PREFIX/bin/gzip" -n -c "$plain" > "$compressed"
"$PREFIX/bin/gzip" -dc "$compressed" > "$roundtrip"
cmp -s "$plain" "$roundtrip"
echo "gzip:ok"
