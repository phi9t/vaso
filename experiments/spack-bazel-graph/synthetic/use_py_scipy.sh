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

prefix="$(find_prefix py_scipy_native)"
numpy_prefix="$(find_prefix py_numpy_native)"
openblas_prefix="$(find_prefix openblas_native)"
python_prefix="$(find_prefix python_313_native)"
venv_prefix="$(find_prefix python_venv_native)"

for name in prefix numpy_prefix openblas_prefix python_prefix venv_prefix; do
  value="${!name:-}"
  [[ -n "$value" && -d "$value" ]] || { echo "missing prefix discovery for $name" >&2; exit 1; }
done

python_abi="$("$python_prefix/bin/python3" -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
[[ "$python_abi" == "3.13" ]] || { echo "unexpected Python ABI: $python_abi" >&2; exit 1; }
ext_suffix="$("$python_prefix/bin/python${python_abi}" - <<'PY'
import sysconfig
print(sysconfig.get_config_var("EXT_SUFFIX") or "")
PY
)"
[[ "$ext_suffix" == *"cpython-313"* ]] || { echo "unexpected extension suffix: $ext_suffix" >&2; exit 1; }

site_packages="$prefix/lib/python${python_abi}/site-packages"
[[ -f "$site_packages/scipy/__init__.py" ]] || { echo "missing scipy package" >&2; exit 1; }
[[ -f "$site_packages/scipy/linalg/_fblas$ext_suffix" ]] || { echo "missing _fblas extension" >&2; exit 1; }
[[ -f "$site_packages/scipy/linalg/_flapack$ext_suffix" ]] || { echo "missing _flapack extension" >&2; exit 1; }
[[ -f "$site_packages/scipy/special/_ufuncs$ext_suffix" ]] || { echo "missing _ufuncs extension" >&2; exit 1; }
[[ -f "$site_packages/scipy/sparse/_sparsetools$ext_suffix" ]] || { echo "missing _sparsetools extension" >&2; exit 1; }
[[ -f "$site_packages/scipy/stats/_stats$ext_suffix" ]] || { echo "missing _stats extension" >&2; exit 1; }
[[ -f "$site_packages/scipy-1.17.1.dist-info/METADATA" ]] || { echo "missing scipy metadata" >&2; exit 1; }

openblas_libdir="$openblas_prefix/lib"
if [[ ! -d "$openblas_libdir" && -d "$openblas_prefix/lib64" ]]; then
  openblas_libdir="$openblas_prefix/lib64"
fi
[[ -d "$openblas_libdir" ]] || { echo "missing OpenBLAS library dir" >&2; exit 1; }
if ! readelf -d "$site_packages/scipy/linalg/_fblas$ext_suffix" | grep -q 'Shared library: \[libopenblas'; then
  echo "scipy linalg extension is not linked against OpenBLAS" >&2
  exit 1
fi

export LD_LIBRARY_PATH="$openblas_libdir:$python_prefix/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
export PYTHONPATH="$site_packages:$numpy_prefix/lib/python${python_abi}/site-packages:$venv_prefix/lib/python${python_abi}/site-packages"
out="$("$venv_prefix/bin/python${python_abi}" - <<'PY'
import importlib.metadata

import numpy as np
import scipy
from scipy import linalg, special

a = np.array([[1.0, 2.0], [3.0, 4.0]])
print(
    "py-scipy:%s:%s:%.1f:%.6f"
    % (
        importlib.metadata.version("scipy"),
        scipy.__version__,
        float(linalg.det(a)),
        float(special.expit(0.0)),
    )
)
PY
)"
[[ "$out" == "py-scipy:1.17.1:1.17.1:-2.0:0.500000" ]] || {
  echo "unexpected scipy output: $out" >&2
  exit 1
}

echo "$out"
