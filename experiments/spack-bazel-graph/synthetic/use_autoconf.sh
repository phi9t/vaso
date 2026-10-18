#!/usr/bin/env bash
set -euo pipefail

find_native_prefix() {
  local base hit
  for base in "${RUNFILES_DIR:-}" "$PWD" "$PWD/.." "$0.runfiles"; do
    [[ -n "$base" && -d "$base" ]] || continue
    hit="$(find "$base" -type d -path "*autoconf_native*/prefix" 2>/dev/null | head -1)"
    if [[ -n "$hit" ]]; then echo "$hit"; return 0; fi
  done
  return 1
}

PREFIX="$(find_native_prefix)"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

cat > "$work/configure.ac" <<'EOF'
AC_INIT([vaso-autoconf-smoke], [1.0])
AC_CONFIG_FILES([config.status])
AC_OUTPUT
EOF

(cd "$work" && "$PREFIX/bin/autoconf" --force)
grep -q 'vaso-autoconf-smoke' "$work/configure"
"$PREFIX/bin/autoconf" --version | grep -q 'autoconf (GNU Autoconf) 2.72'
echo "autoconf:2.72:ok"
