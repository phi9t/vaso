#!/usr/bin/env bash
set -euo pipefail

find_native_prefix() {
  local base hit
  for base in "${RUNFILES_DIR:-}" "$PWD" "$PWD/.." "$0.runfiles"; do
    [[ -n "$base" && -d "$base" ]] || continue
    hit="$(find "$base" -type d -path "*flex_native*/prefix" 2>/dev/null | head -1)"
    if [[ -n "$hit" ]]; then echo "$hit"; return 0; fi
  done
  return 1
}

PREFIX="$(find_native_prefix)"
scanner="$(mktemp)"
out="$(mktemp)"
trap 'rm -f "$scanner" "$out"' EXIT
cat > "$scanner" <<'EOF'
%%
[0-9]+  ECHO;
[[:space:]]+ ;
.       ECHO;
%%
EOF

"$PREFIX/bin/flex" -o "$out" "$scanner"
grep -q 'yylex' "$out"
"$PREFIX/bin/lex" --version | grep -q '2.6.3'
echo "flex:2.6.3:ok"
