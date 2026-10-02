#!/usr/bin/env bash
set -euo pipefail

find_native_prefix() {
  local base hit
  for base in "${RUNFILES_DIR:-}" "$PWD" "$PWD/.." "$0.runfiles"; do
    [[ -n "$base" && -d "$base" ]] || continue
    hit="$(find "$base" -type d -path "*m4_native*/prefix" 2>/dev/null | head -1)"
    if [[ -n "$hit" ]]; then echo "$hit"; return 0; fi
  done
  return 1
}

PREFIX="$(find_native_prefix)"
input="$(mktemp)"
trap 'rm -f "$input"' EXIT
cat > "$input" <<'EOF'
define(`greet', `hello, $1')dnl
greet(`insula')
EOF

[[ "$("$PREFIX/bin/m4" "$input")" == "hello, insula" ]]
"$PREFIX/bin/m4" --version | grep -Eq 'GNU M4\)? 1\.4\.21'
echo "m4:1.4.21:ok"
