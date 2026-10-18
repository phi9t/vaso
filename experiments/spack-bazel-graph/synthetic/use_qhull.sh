#!/usr/bin/env bash
set -euo pipefail

find_native_prefix() {
  local base hit
  for base in "${RUNFILES_DIR:-}" "$PWD" "$PWD/.." "$0.runfiles"; do
    [[ -n "$base" && -d "$base" ]] || continue
    hit="$(find "$base" -type d -path "*qhull_native*/prefix" 2>/dev/null | head -1)"
    if [[ -n "$hit" ]]; then echo "$hit"; return 0; fi
  done
  return 1
}

PREFIX="$(find_native_prefix)"

test -x "$PREFIX/bin/qhull"
test -x "$PREFIX/bin/rbox"
test -x "$PREFIX/bin/qconvex"
test -x "$PREFIX/bin/qdelaunay"
test -x "$PREFIX/bin/qhalf"
test -x "$PREFIX/bin/qvoronoi"
test -f "$PREFIX/include/libqhull_r/libqhull_r.h"
test -f "$PREFIX/include/libqhullcpp/Qhull.h"
test -e "$PREFIX/lib/libqhull_r.so"
test -f "$PREFIX/lib/libqhullstatic.a"
test -f "$PREFIX/lib/libqhullstatic_r.a"
test -f "$PREFIX/lib/libqhullcpp.a"
test -f "$PREFIX/lib/pkgconfig/qhull_r.pc"
test -f "$PREFIX/lib/pkgconfig/qhullcpp.pc"
test -f "$PREFIX/lib/cmake/Qhull/QhullConfig.cmake"
test -f "$PREFIX/lib/cmake/Qhull/QhullTargets.cmake"

grep -q "Version: 8.0.2" "$PREFIX/lib/pkgconfig/qhull_r.pc"
"$PREFIX/bin/rbox" D2 4 >/tmp/qhull-rbox.out
grep -q '^2 rbox' /tmp/qhull-rbox.out

echo "qhull:2020.2:ok"
