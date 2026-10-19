#!/usr/bin/env bash
set -euo pipefail

find_native_prefix() {
  local base hit
  for base in "${RUNFILES_DIR:-}" "$PWD" "$PWD/.." "$0.runfiles"; do
    [[ -n "$base" && -d "$base" ]] || continue
    hit="$(find "$base" -type d -path "*coreutils_native*/prefix" 2>/dev/null | head -1)"
    if [[ -n "$hit" ]]; then echo "$hit"; return 0; fi
  done
  return 1
}

PREFIX="$(find_native_prefix)"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

printf 'gamma\nalpha\nbeta\n' > "$work/words.txt"
"$PREFIX/bin/sort" "$work/words.txt" > "$work/sorted.txt"
printf 'alpha\nbeta\ngamma\n' > "$work/expected.txt"
cmp -s "$work/sorted.txt" "$work/expected.txt"

digest="$("$PREFIX/bin/sha256sum" "$work/expected.txt" | "$PREFIX/bin/cut" -d ' ' -f 1)"
path="$("$PREFIX/bin/realpath" "$work/expected.txt")"
env_out="$("$PREFIX/bin/env" VASO_COREUTILS_SMOKE=ok "$PREFIX/bin/printenv" VASO_COREUTILS_SMOKE)"

printf 'coreutils:9.10:%s:%s:%s\n' "${#digest}" "${path##*/}" "$env_out"
