#!/usr/bin/env bash
set -euo pipefail

find_native_prefix() {
  local base hit
  for base in "${RUNFILES_DIR:-}" "$PWD" "$PWD/.." "$0.runfiles"; do
    [[ -n "$base" && -d "$base" ]] || continue
    hit="$(find "$base" -type d -path "*bison_native*/prefix" 2>/dev/null | head -1)"
    if [[ -n "$hit" ]]; then echo "$hit"; return 0; fi
  done
  return 1
}

PREFIX="$(find_native_prefix)"
grammar="$(mktemp)"
out="$(mktemp)"
trap 'rm -f "$grammar" "$out"' EXIT
cat > "$grammar" <<'EOF'
%token NUM
%%
line: NUM ;
EOF

"$PREFIX/bin/bison" -o "$out" "$grammar"
grep -q 'yyparse' "$out"
"$PREFIX/bin/bison" --version | grep -q 'bison (GNU Bison) 3.8.2'
echo "bison:3.8.2:ok"
