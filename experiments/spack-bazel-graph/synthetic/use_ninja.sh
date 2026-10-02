#!/usr/bin/env bash
set -euo pipefail

find_native_prefix() {
  local base hit
  for base in "${RUNFILES_DIR:-}" "$PWD" "$PWD/.." "$0.runfiles"; do
    [[ -n "$base" && -d "$base" ]] || continue
    hit="$(find "$base" -type d -path "*ninja_native*/prefix" 2>/dev/null | head -1)"
    if [[ -n "$hit" ]]; then echo "$hit"; return 0; fi
  done
  return 1
}

if [[ $# -gt 0 ]]; then
  PREFIX="$(<"$1")"
else
  PREFIX="$(readlink -f "$(find_native_prefix)")"
fi
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

version="$("$PREFIX/bin/ninja" --version)"
if [[ "$version" != "1.13.2" ]]; then
  echo "unexpected ninja version: $version" >&2
  exit 1
fi
link_target="$(readlink "$PREFIX/bin/ninja-build")"
if [[ "$link_target" != "ninja" ]]; then
  echo "unexpected ninja-build symlink target: $link_target" >&2
  exit 1
fi
for path in "$PREFIX/misc/ninja_syntax.py" "$PREFIX/misc/bash-completion"; do
  if [[ ! -r "$path" ]]; then
    echo "missing expected Ninja misc file: $path" >&2
    exit 1
  fi
done

cat > "$work/build.ninja" <<'EOF'
rule copy
  command = cp $in $out
build out.txt: copy in.txt
EOF
printf 'ninja-native\n' > "$work/in.txt"
"$PREFIX/bin/ninja" -C "$work" out.txt
grep -q '^ninja-native$' "$work/out.txt"
echo "ninja:1.13.2:ok"
