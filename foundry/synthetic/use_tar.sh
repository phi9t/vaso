#!/usr/bin/env bash
set -euo pipefail

find_native_prefix() {
  local base hit
  for base in "${RUNFILES_DIR:-}" "$PWD" "$PWD/.." "$0.runfiles"; do
    [[ -n "$base" && -d "$base" ]] || continue
    hit="$(find "$base" -type d -path "*tar_native*/prefix" 2>/dev/null | head -1)"
    if [[ -n "$hit" ]]; then echo "$hit"; return 0; fi
  done
  return 1
}

PREFIX="$(find_native_prefix)"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

mkdir -p "$work/src" "$work/out"
printf 'alpha\n' > "$work/src/alpha.txt"
printf 'beta\n' > "$work/src/beta.txt"

"$PREFIX/bin/tar" -cf "$work/plain.tar" -C "$work/src" .
"$PREFIX/bin/tar" -tf "$work/plain.tar" | sort > "$work/list.txt"
grep -Fx ./alpha.txt "$work/list.txt" >/dev/null
grep -Fx ./beta.txt "$work/list.txt" >/dev/null
"$PREFIX/bin/tar" -xf "$work/plain.tar" -C "$work/out"
cmp -s "$work/src/alpha.txt" "$work/out/alpha.txt"
cmp -s "$work/src/beta.txt" "$work/out/beta.txt"

"$PREFIX/bin/tar" -czf "$work/pigz.tar.gz" -C "$work/src" .
"$PREFIX/bin/tar" -cjf "$work/bzip2.tar.bz2" -C "$work/src" .
"$PREFIX/bin/tar" -cJf "$work/xz.tar.xz" -C "$work/src" .
"$PREFIX/bin/tar" --zstd -cf "$work/zstd.tar.zst" -C "$work/src" .

printf 'tar %s roundtrip compressors ok\n' "$("$PREFIX/bin/tar" --version | sed -n '1s/^tar (GNU tar) //p')"
