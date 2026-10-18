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

prefix="$(require_prefix py_numpy_native)"
openblas_prefix="$(require_prefix openblas_native)"
python_prefix="$(require_prefix python_313_native)"
venv_prefix="$(require_prefix python_venv_native)"

python_abi="$("$python_prefix/bin/python3" -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
[[ "$python_abi" == "3.13" ]] || { echo "unexpected Python ABI: $python_abi" >&2; exit 1; }
ext_suffix="$("$python_prefix/bin/python${python_abi}" - <<'PY'
import sysconfig
print(sysconfig.get_config_var("EXT_SUFFIX") or "")
PY
)"
[[ "$ext_suffix" == *"cpython-313"* ]] || { echo "unexpected extension suffix: $ext_suffix" >&2; exit 1; }

site_packages="$prefix/lib/python${python_abi}/site-packages"
[[ -x "$prefix/bin/f2py" ]] || { echo "missing f2py executable" >&2; exit 1; }
[[ -x "$prefix/bin/numpy-config" ]] || { echo "missing numpy-config executable" >&2; exit 1; }
[[ -f "$site_packages/numpy/__init__.py" ]] || { echo "missing numpy package" >&2; exit 1; }
[[ -f "$site_packages/numpy/_core/_multiarray_umath$ext_suffix" ]] || { echo "missing _multiarray_umath extension" >&2; exit 1; }
[[ -f "$site_packages/numpy/linalg/_umath_linalg$ext_suffix" ]] || { echo "missing _umath_linalg extension" >&2; exit 1; }
[[ -f "$site_packages/numpy/random/mtrand$ext_suffix" ]] || { echo "missing mtrand extension" >&2; exit 1; }
[[ -f "$site_packages/numpy-2.4.6.dist-info/METADATA" ]] || { echo "missing metadata" >&2; exit 1; }

openblas_libdir="$openblas_prefix/lib"
if [[ ! -d "$openblas_libdir" && -d "$openblas_prefix/lib64" ]]; then
  openblas_libdir="$openblas_prefix/lib64"
fi
[[ -d "$openblas_libdir" ]] || { echo "missing OpenBLAS library dir" >&2; exit 1; }

export LD_LIBRARY_PATH="$openblas_libdir:$python_prefix/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
export PYTHONPATH="$site_packages:$venv_prefix/lib/python${python_abi}/site-packages"
out="$("$venv_prefix/bin/python${python_abi}" - <<'PY'
import importlib.metadata
import numpy as np
from numpy._core import _multiarray_umath

a = np.array([[1.0, 2.0], [3.0, 4.0]])
b = np.array([[5.0, 6.0], [7.0, 8.0]])
print(
    "py-numpy:%s:%s:%.1f:%.1f:%s"
    % (
        importlib.metadata.version("numpy"),
        _multiarray_umath.__name__,
        float((a @ b)[0, 0]),
        float(np.linalg.det(a)),
        np.__config__.CONFIG["Build Dependencies"]["blas"]["name"],
    )
)
PY
)"
[[ "$out" == "py-numpy:2.4.6:numpy._core._multiarray_umath:19.0:-2.0:openblas" ]] || {
  echo "unexpected import output: $out" >&2
  exit 1
}

echo "$out"
