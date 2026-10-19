#!/usr/bin/env bash
set -euo pipefail

find_runfiles_prefix() {
  local marker="$1"
  local base hit
  for base in "${RUNFILES_DIR:-}" "$PWD" "$PWD/.." "$0.runfiles"; do
    [[ -n "$base" && -d "$base" ]] || continue
    hit="$(find "$base" -type d \( \
      -path "*/+${marker}+${marker}/prefix" -o \
      -path "*/${marker}/prefix" \
    \) -print -quit 2>/dev/null)"
    if [[ -n "$hit" ]]; then echo "$hit"; return 0; fi
  done
  return 1
}

require_path() {
  local kind="$1"
  local path="$2"
  case "$kind" in
    dir) [[ -d "$path" ]] ;;
    file) [[ -f "$path" ]] ;;
    exec) [[ -x "$path" ]] ;;
    symlink) [[ -L "$path" ]] ;;
    *) echo "unknown path kind: $kind" >&2; return 2 ;;
  esac || {
    echo "missing ${kind}: ${path}" >&2
    return 1
  }
}

PREFIX="$(find_runfiles_prefix swig_native)" || {
  echo "could not locate swig_native prefix in runfiles" >&2
  exit 1
}
PCRE2_PREFIX="$(find_runfiles_prefix pcre2_native)" || {
  echo "could not locate pcre2_native prefix in runfiles" >&2
  exit 1
}
ZLIB_PREFIX="$(find_runfiles_prefix zlib_ng_native)" || {
  echo "could not locate zlib_ng_native prefix in runfiles" >&2
  exit 1
}
export LD_LIBRARY_PATH="${PCRE2_PREFIX}/lib:${ZLIB_PREFIX}/lib:${PREFIX}/lib${LD_LIBRARY_PATH:+:${LD_LIBRARY_PATH}}"

require_path exec "$PREFIX/bin/swig"
require_path exec "$PREFIX/bin/ccache-swig"
require_path symlink "$PREFIX/bin/swig4.0"
require_path file "$PREFIX/share/swig/4.4.1/swig.swg"
require_path file "$PREFIX/share/swig/4.4.1/python/python.swg"
require_path file "$PREFIX/share/swig/4.4.1/std/std_string.i"

version_out="$("$PREFIX/bin/swig" -version)" || {
  echo "swig -version failed" >&2
  exit 1
}
printf '%s\n' "$version_out" | grep -q "SWIG Version 4.4.1" || {
  echo "unexpected swig version output:" >&2
  printf '%s\n' "$version_out" >&2
  exit 1
}

swiglib_out="$("$PREFIX/bin/swig" -swiglib)" || {
  echo "swig -swiglib failed" >&2
  exit 1
}
expected_swiglib="$PREFIX/share/swig/4.4.1"
case "$swiglib_out" in
  "$expected_swiglib"|*/+swig_native+swig_native/prefix/share/swig/4.4.1) ;;
  *)
  echo "unexpected swig library path: $swiglib_out" >&2
  echo "expected runfiles path: $expected_swiglib" >&2
  exit 1
  ;;
esac

cat > /tmp/use_swig.i <<'EOF'
%module demo
%inline %{
int add(int a, int b) { return a + b; }
%}
EOF
"$PREFIX/bin/swig" -python -o /tmp/use_swig_wrap.c /tmp/use_swig.i
if ! grep -Eq "SWIG_init|PyInit__demo" /tmp/use_swig_wrap.c; then
  echo "generated wrapper did not contain an expected Python init symbol" >&2
  sed -n '1,80p' /tmp/use_swig_wrap.c >&2
  exit 1
fi

echo "swig:4.4.1:ok"
