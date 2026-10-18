#!/usr/bin/env bash
set -euo pipefail

find_native_prefix() {
  local base hit
  for base in "${RUNFILES_DIR:-}" "$PWD" "$PWD/.." "$0.runfiles"; do
    [[ -n "$base" && -d "$base" ]] || continue
    hit="$(find "$base" -type d -path "*nasm_native*/prefix" 2>/dev/null | head -1)"
    if [[ -n "$hit" ]]; then echo "$hit"; return 0; fi
  done
  return 1
}

PREFIX="$(find_native_prefix)"
asm="$(mktemp)"
obj="$(mktemp)"
trap 'rm -f "$asm" "$obj"' EXIT

cat > "$asm" <<'EOF'
BITS 64
GLOBAL _start
SECTION .text
_start:
    mov eax, 60
    xor edi, edi
    syscall
EOF

"$PREFIX/bin/nasm" -f elf64 -o "$obj" "$asm"
"$PREFIX/bin/ndisasm" -b 64 "$obj" | grep -q "B83C000000"
"$PREFIX/bin/nasm" -v | grep -q "NASM version 2\\.16\\.03"
echo "nasm:2.16.03:ok"
