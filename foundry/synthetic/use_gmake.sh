#!/usr/bin/env bash
set -euo pipefail

find_native_prefix() {
  local base hit
  for base in "${RUNFILES_DIR:-}" "$PWD" "$PWD/.." "$0.runfiles"; do
    [[ -n "$base" && -d "$base" ]] || continue
    hit="$(find "$base" -type d -path "*gmake_native*/prefix" 2>/dev/null | head -1)"
    if [[ -n "$hit" ]]; then echo "$hit"; return 0; fi
  done
  return 1
}

PREFIX="$(find_native_prefix)"
makefile="$(mktemp)"
trap 'rm -f "$makefile"' EXIT
cat > "$makefile" <<'EOF'
all:
	@printf 'gmake:ok\n'
EOF

"$PREFIX/bin/make" -f "$makefile"
