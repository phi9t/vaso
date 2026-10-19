#!/usr/bin/env bash
set -euo pipefail

find_prefix() {
  local base hit
  for base in "${RUNFILES_DIR:-}" "$PWD" "$PWD/.." "$0.runfiles"; do
    [[ -n "$base" && -d "$base" ]] || continue
    hit="$(find "$base" -type d -path "*cmake_native*/prefix" 2>/dev/null | head -1)"
    if [[ -n "$hit" ]]; then
      echo "$hit"
      return 0
    fi
  done
  return 1
}

prefix="$(find_prefix)"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

"$prefix/bin/cmake" --version | grep -q 'cmake version 3.31.11'
"$prefix/bin/ctest" --version | grep -q 'ctest version 3.31.11'
"$prefix/bin/cpack" --version | grep -q 'cpack version 3.31.11'
"$prefix/bin/ccmake" --version | grep -q 'ccmake version 3.31.11'

cat > "$work/CMakeLists.txt" <<'CMAKE'
cmake_minimum_required(VERSION 3.16)
project(vaso_cmake_smoke C)
add_executable(hello main.c)
enable_testing()
add_test(NAME hello COMMAND hello)
CMAKE
cat > "$work/main.c" <<'C'
#include <stdio.h>
int main(void) {
  puts("cmake:3.31.11:ok");
  return 0;
}
C

"$prefix/bin/cmake" -S "$work" -B "$work/build" -G "Unix Makefiles" >/dev/null
"$prefix/bin/cmake" --build "$work/build" >/dev/null
"$prefix/bin/ctest" --test-dir "$work/build" --output-on-failure >/dev/null
"$work/build/hello"
