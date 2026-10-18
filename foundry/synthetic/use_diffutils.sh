#!/usr/bin/env bash
set -euo pipefail

find_native_prefix() {
  local base hit
  for base in "${RUNFILES_DIR:-}" "$PWD" "$PWD/.." "$0.runfiles"; do
    [[ -n "$base" && -d "$base" ]] || continue
    hit="$(find "$base" -type d -path "*diffutils_native*/prefix" 2>/dev/null | head -1)"
    if [[ -n "$hit" ]]; then echo "$hit"; return 0; fi
  done
  return 1
}

PREFIX="$(find_native_prefix)"
left="$(mktemp)"
right="$(mktemp)"
trap 'rm -f "$left" "$right"' EXIT
printf 'alpha\nbeta\n' > "$left"
printf 'alpha\ngamma\n' > "$right"

set +e
out="$("$PREFIX/bin/diff" "$left" "$right")"
rc=$?
set -e
[[ "$rc" -eq 1 ]]
printf '%s\n' "$out"
