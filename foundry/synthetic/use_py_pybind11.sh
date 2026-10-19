#!/usr/bin/env bash
set -euo pipefail

find_runfile_by_pattern() {
  local pattern="$1"
  local base hit manifest_key manifest_value
  if [[ -n "${RUNFILES_MANIFEST_FILE:-}" && -f "$RUNFILES_MANIFEST_FILE" ]]; then
    while IFS= read -r line; do
      manifest_key="${line%% *}"
      manifest_value="${line#* }"
      [[ "$manifest_key" == "$line" ]] && manifest_value="$manifest_key"
      case "$manifest_key" in
        $pattern)
          if [[ -e "$manifest_value" || -L "$manifest_value" ]]; then
            echo "$manifest_value"
            return 0
          fi
          ;;
      esac
    done < "$RUNFILES_MANIFEST_FILE"
  fi
  for base in "${RUNFILES_DIR:-}" "$PWD" "$PWD/.." "$0.runfiles"; do
    [[ -n "$base" && -d "$base" ]] || continue
    hit="$(find "$base" -path "$pattern" -print -quit 2>/dev/null)"
    if [[ -n "$hit" && ( -e "$hit" || -L "$hit" ) ]]; then
      echo "$hit"
      return 0
    fi
  done
  return 1
}

find_prefix() {
  local marker="$1"
  local hit
  hit="$(find_runfile_by_pattern "*/+*${marker}*/prefix_path.txt" || true)"
  if [[ -z "$hit" ]]; then
    hit="$(find_runfile_by_pattern "*/${marker}/prefix_path.txt" || true)"
  fi
  if [[ -n "$hit" && -r "$hit" ]]; then
    hit="$(tr -d '\n' < "$hit")"
    if [[ -d "$hit" ]]; then
      echo "$hit"
      return 0
    fi
  fi
  hit="$(find_runfile_by_pattern "*/+*${marker}*/prefix" || true)"
  if [[ -z "$hit" ]]; then
    hit="$(find_runfile_by_pattern "*/${marker}/prefix" || true)"
  fi
  if [[ -n "$hit" && -d "$hit" ]]; then
    echo "$hit"
    return 0
  fi
  return 1
}

require_prefix() {
  local marker="$1"
  local hit
  hit="$(find_prefix "$marker" || true)"
  [[ -n "$hit" && -d "$hit" ]] || { echo "missing prefix discovery for $marker" >&2; exit 1; }
  echo "$hit"
}

prefix="$(require_prefix py_pybind11_native)"
cmake_prefix="$(require_prefix cmake_native)"
ninja_prefix="$(require_prefix ninja_native)"
python_prefix="$(require_prefix python_313_native)"
venv_prefix="$(require_prefix python_venv_native)"

python_abi="$("$python_prefix/bin/python3" -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
[[ "$python_abi" == "3.13" ]] || { echo "unexpected Python ABI: $python_abi" >&2; exit 1; }

site_packages="$prefix/lib/python${python_abi}/site-packages"
[[ -f "$prefix/include/pybind11/pybind11.h" ]] || { echo "missing top-level pybind11 header" >&2; exit 1; }
[[ -f "$prefix/share/cmake/pybind11/pybind11Config.cmake" ]] || { echo "missing top-level pybind11 CMake config" >&2; exit 1; }
[[ -f "$prefix/share/pkgconfig/pybind11.pc" ]] || { echo "missing top-level pybind11 pkg-config file" >&2; exit 1; }
[[ -f "$prefix/bin/pybind11-config" ]] || { echo "missing pybind11-config" >&2; exit 1; }
[[ -f "$site_packages/pybind11/__init__.py" ]] || { echo "missing pybind11 package" >&2; exit 1; }
[[ -f "$site_packages/pybind11/commands.py" ]] || { echo "missing pybind11 commands module" >&2; exit 1; }
[[ -f "$site_packages/pybind11/share/cmake/pybind11/pybind11Config.cmake" ]] || { echo "missing Python pybind11 CMake config" >&2; exit 1; }
[[ -f "$site_packages/pybind11-3.0.2.dist-info/METADATA" ]] || { echo "missing pybind11 metadata" >&2; exit 1; }
[[ -f "$site_packages/pybind11-3.0.2.dist-info/WHEEL" ]] || { echo "missing pybind11 wheel metadata" >&2; exit 1; }

export LD_LIBRARY_PATH="$python_prefix/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
export PATH="$prefix/bin:$cmake_prefix/bin:$ninja_prefix/bin:$venv_prefix/bin:$python_prefix/bin:$PATH"
export PYTHONPATH="$site_packages:$venv_prefix/lib/python${python_abi}/site-packages"

include_dir="$("$venv_prefix/bin/python${python_abi}" - <<'PY'
import pybind11

print(pybind11.get_include())
PY
)"
expected_include="$site_packages/pybind11/include"
[[ "$include_dir" == "$expected_include" ]] || { echo "unexpected pybind11 include dir: $include_dir" >&2; exit 1; }

work="${TEST_TMPDIR:-${TMPDIR:-$PWD}}/pybind11-smoke.$$"
rm -rf "$work"
mkdir -p "$work"
trap 'rm -rf "$work"' EXIT
cat > "$work/CMakeLists.txt" <<'CMAKE'
cmake_minimum_required(VERSION 3.16)
project(vaso_pybind11_smoke LANGUAGES CXX)
find_package(Python3 COMPONENTS Interpreter Development.Module REQUIRED)
find_package(pybind11 CONFIG REQUIRED)
pybind11_add_module(_vaso_pybind11_smoke module.cc)
CMAKE
cat > "$work/module.cc" <<'CXX'
#include <pybind11/pybind11.h>

int answer() { return 42; }

PYBIND11_MODULE(_vaso_pybind11_smoke, m) {
  m.def("answer", &answer);
}
CXX
"$cmake_prefix/bin/cmake" \
  -S "$work" \
  -B "$work/build" \
  -G Ninja \
  -DCMAKE_MAKE_PROGRAM="$ninja_prefix/bin/ninja-build" \
  -DCMAKE_PREFIX_PATH="$prefix;$python_prefix;$venv_prefix" \
  -DPython3_EXECUTABLE="$venv_prefix/bin/python${python_abi}" \
  >/dev/null
"$ninja_prefix/bin/ninja-build" -C "$work/build" >/dev/null

out="$(PYTHONPATH="$work/build:$PYTHONPATH" "$venv_prefix/bin/python${python_abi}" - <<'PY'
import importlib.metadata
import pybind11
import _vaso_pybind11_smoke

print(
    "py-pybind11:%s:%s:%s"
    % (
        importlib.metadata.version("pybind11"),
        pybind11.get_include().rsplit("/", 1)[-1],
        _vaso_pybind11_smoke.answer(),
    )
)
PY
)"
[[ "$out" == "py-pybind11:3.0.2:include:42" ]] || {
  echo "unexpected pybind11 output: $out" >&2
  exit 1
}

echo "$out"
