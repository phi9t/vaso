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
  hit="$(find_runfile_by_pattern "*/+${marker}+${marker}/prefix_path.txt" || true)"
  if [[ -z "$hit" ]]; then
    hit="$(find_runfile_by_pattern "*${marker}*/prefix_path.txt" || true)"
  fi
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
  hit="$(find_runfile_by_pattern "*/+${marker}+${marker}/prefix" || true)"
  if [[ -z "$hit" ]]; then
    hit="$(find_runfile_by_pattern "*${marker}*/prefix" || true)"
  fi
  if [[ -z "$hit" ]]; then
    hit="$(find_runfile_by_pattern "*/${marker}/prefix" || true)"
  fi
  if [[ -n "$hit" && -d "$hit" ]]; then
    echo "$hit"
    return 0
  fi
  return 1
}

prefix="$(find_prefix py_scikit_build_core_native)"
python_prefix="$(find_prefix python_313_native)"
venv_prefix="$(find_prefix python_venv_native)"
cmake_prefix="$(find_prefix cmake_native)"
packaging_prefix="$(find_prefix py_packaging_native)"
pathspec_prefix="$(find_prefix py_pathspec_native)"

for name in prefix python_prefix venv_prefix cmake_prefix packaging_prefix pathspec_prefix; do
  value="${!name:-}"
  [[ -n "$value" && -d "$value" ]] || { echo "missing prefix discovery for $name" >&2; exit 1; }
done

python_abi="$("$python_prefix/bin/python3" -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
[[ "$python_abi" == "3.13" ]] || { echo "unexpected Python ABI: $python_abi" >&2; exit 1; }

site_packages="$prefix/lib/python${python_abi}/site-packages"
[[ -f "$site_packages/scikit_build_core/__init__.py" ]] || { echo "missing scikit_build_core package" >&2; exit 1; }
[[ -f "$site_packages/scikit_build_core/_version.py" ]] || { echo "missing scikit_build_core version module" >&2; exit 1; }
[[ -f "$site_packages/scikit_build_core/cmake.py" ]] || { echo "missing scikit_build_core cmake module" >&2; exit 1; }
[[ -f "$site_packages/scikit_build_core/program_search.py" ]] || { echo "missing scikit_build_core program search module" >&2; exit 1; }
[[ -f "$site_packages/scikit_build_core/build/wheel.py" ]] || { echo "missing scikit_build_core wheel builder" >&2; exit 1; }
[[ -f "$site_packages/scikit_build_core/resources/scikit-build.schema.json" ]] || { echo "missing scikit-build schema" >&2; exit 1; }
[[ -f "$site_packages/scikit_build_core/py.typed" ]] || { echo "missing scikit_build_core py.typed" >&2; exit 1; }
[[ -f "$site_packages/scikit_build_core-1.0.0.dist-info/METADATA" ]] || { echo "missing scikit_build_core metadata" >&2; exit 1; }
[[ -f "$site_packages/scikit_build_core-1.0.0.dist-info/WHEEL" ]] || { echo "missing scikit_build_core wheel metadata" >&2; exit 1; }
[[ -f "$site_packages/scikit_build_core-1.0.0.dist-info/entry_points.txt" ]] || { echo "missing scikit_build_core entry points" >&2; exit 1; }
[[ -f "$site_packages/scikit_build_core-1.0.0.dist-info/licenses/LICENSE" ]] || { echo "missing scikit_build_core license" >&2; exit 1; }

export LD_LIBRARY_PATH="$python_prefix/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
export PATH="$cmake_prefix/bin:$PATH"
export CMAKE_EXECUTABLE="$cmake_prefix/bin/cmake"
export PYTHONPATH="$site_packages:$packaging_prefix/lib/python${python_abi}/site-packages:$pathspec_prefix/lib/python${python_abi}/site-packages:$venv_prefix/lib/python${python_abi}/site-packages"
out="$("$venv_prefix/bin/python${python_abi}" - <<'PY'
import importlib.metadata
from pathlib import Path

import pathspec
import scikit_build_core
from scikit_build_core.cmake import CMake

cmake = CMake.default_search(env={"CMAKE_EXECUTABLE": str(Path(__import__("os").environ["CMAKE_EXECUTABLE"]))})
print(
    "py-scikit-build-core:%s:%s:%s:%s"
    % (
        importlib.metadata.version("scikit_build_core"),
        scikit_build_core.__version__,
        pathspec.__version__,
        cmake.version,
    )
)
PY
)"
case "$out" in
  py-scikit-build-core:1.0.0:1.0.0:1.1.1:*)
    ;;
  *)
    echo "unexpected scikit-build-core output: $out" >&2
    exit 1
    ;;
esac

echo "$out"
