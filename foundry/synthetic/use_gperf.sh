#!/usr/bin/env bash
set -euo pipefail

find_native_prefix() {
  local base hit
  for base in "${RUNFILES_DIR:-}" "$PWD" "$PWD/.." "$0.runfiles"; do
    [[ -n "$base" && -d "$base" ]] || continue
    hit="$(find "$base" -type d -path "*gperf_native*/prefix" 2>/dev/null | head -1)"
    if [[ -n "$hit" ]]; then echo "$hit"; return 0; fi
  done
  return 1
}

PREFIX="$(find_native_prefix)"
input="$(mktemp)"
trap 'rm -f "$input"' EXIT
cat > "$input" <<'EOF'
%language=C
%readonly-tables
struct keyword { const char *name; int token; };
%%
alpha, 1
beta, 2
gamma, 3
%%
EOF

"$PREFIX/bin/gperf" "$input" | grep -q "hash"
echo "gperf:ok"
