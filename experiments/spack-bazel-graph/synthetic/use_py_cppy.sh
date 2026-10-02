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
    hit="$(find_runfile_by_pattern "*/${marker}/prefix" || true)"
  fi
  if [[ -n "$hit" && -d "$hit" ]]; then
    echo "$hit"
    return 0
  fi
  return 1
}

prefix="$(find_prefix py_cppy_native)"
python_prefix="$(find_prefix python_native)"
venv_prefix="$(find_prefix python_venv_native)"
pip_prefix="$(find_prefix py_pip_native)"
setuptools_prefix="$(find_prefix py_setuptools_native)"
setuptools_scm_prefix="$(find_prefix py_setuptools_scm_native)"
wheel_prefix="$(find_prefix py_wheel_native)"

site_packages="$prefix/lib/python3.14/site-packages"
[[ -d "$site_packages/cppy" ]] || { echo "missing cppy package" >&2; exit 1; }
[[ -f "$site_packages/cppy/__init__.py" ]] || { echo "missing cppy __init__" >&2; exit 1; }
[[ -f "$site_packages/cppy/version.py" ]] || { echo "missing cppy version module" >&2; exit 1; }
[[ -f "$site_packages/cppy/include/cppy/cppy.h" ]] || { echo "missing cppy umbrella header" >&2; exit 1; }
[[ -f "$site_packages/cppy/include/cppy/ptr.h" ]] || { echo "missing cppy ptr header" >&2; exit 1; }
[[ -f "$site_packages/cppy-1.3.1.dist-info/METADATA" ]] || { echo "missing cppy metadata" >&2; exit 1; }

export LD_LIBRARY_PATH="$python_prefix/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
export PYTHONPATH="$site_packages:$pip_prefix/lib/python3.14/site-packages:$setuptools_prefix/lib/python3.14/site-packages:$setuptools_scm_prefix/lib/python3.14/site-packages:$wheel_prefix/lib/python3.14/site-packages:$venv_prefix/lib/python3.14/site-packages"
out="$("$venv_prefix/bin/python3" - <<'PY'
import importlib.metadata
import pathlib

import cppy

include = pathlib.Path(cppy.get_include())
header = include / "cppy" / "cppy.h"
ptr = include / "cppy" / "ptr.h"

print(
    "py-cppy:%s:%s:%s:%s"
    % (
        importlib.metadata.version("cppy"),
        cppy.__version__,
        header.name if header.exists() else "missing",
        "class_ptr" if "class ptr" in ptr.read_text(encoding="utf-8") else "missing",
    )
)
PY
)"
[[ "$out" == "py-cppy:1.3.1:1.3.1:cppy.h:class_ptr" ]] || {
  echo "unexpected import output: $out" >&2
  exit 1
}

echo "$out"
