#!/usr/bin/env bash
set -euo pipefail

find_native_prefix() {
  local base hit
  for base in "${RUNFILES_DIR:-}" "$PWD" "$PWD/.." "$0.runfiles"; do
    [[ -n "$base" && -d "$base" ]] || continue
    hit="$(find "$base" -type d -path "*unzip_native*/prefix" 2>/dev/null | head -1)"
    if [[ -n "$hit" ]]; then echo "$hit"; return 0; fi
  done
  return 1
}

PREFIX="$(find_native_prefix)"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

export PATH="$PREFIX/bin:$PATH"

python3 - "$work/sample.zip" <<'PY'
import sys
import zipfile

with zipfile.ZipFile(sys.argv[1], "w", compression=zipfile.ZIP_DEFLATED) as zf:
    zf.writestr("alpha.txt", "alpha\n")
    zf.writestr("nested/beta.txt", "beta\n")
PY

"$PREFIX/bin/unzip" -qq "$work/sample.zip" -d "$work/out"
cmp -s "$work/out/alpha.txt" <(printf 'alpha\n')
cmp -s "$work/out/nested/beta.txt" <(printf 'beta\n')
"$PREFIX/bin/zipinfo" -1 "$work/sample.zip" | grep -Fx nested/beta.txt >/dev/null
"$PREFIX/bin/zipgrep" -h beta "$work/sample.zip" nested/beta.txt | grep -Fx beta >/dev/null
"$PREFIX/bin/unzip" -v | grep -q "UnZip 6\\.00"

echo "unzip:6.0:ok"
