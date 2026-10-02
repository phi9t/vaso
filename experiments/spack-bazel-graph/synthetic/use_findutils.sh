#!/usr/bin/env bash
set -euo pipefail

find_native_prefix() {
  local base hit
  for base in "${RUNFILES_DIR:-}" "$PWD" "$PWD/.." "$0.runfiles"; do
    [[ -n "$base" && -d "$base" ]] || continue
    hit="$(find "$base" -type d -path "*findutils_native*/prefix" 2>/dev/null | head -1)"
    if [[ -n "$hit" ]]; then echo "$hit"; return 0; fi
  done
  return 1
}

PREFIX="$(find_native_prefix)"
root="$(mktemp -d)"
trap 'rm -rf "$root"' EXIT
mkdir -p "$root/a" "$root/b"
printf 'alpha\n' > "$root/a/alpha.txt"
printf 'beta\n' > "$root/b/beta.txt"

"$PREFIX/bin/find" "$root" -name alpha.txt -print | grep -q '/a/alpha.txt$'
printf 'one two\nthree\n' | "$PREFIX/bin/xargs" -n1 printf '<%s>\n' | grep -q '<three>'
"$PREFIX/bin/locate" --version | grep -q 'GNU findutils'

echo "findutils:ok"
