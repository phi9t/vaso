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

prefix="$(find_prefix py_sympy_native)"
python_prefix="$(find_prefix python_313_native)"
venv_prefix="$(find_prefix python_venv_native)"
mpmath_prefix="$(find_prefix py_mpmath_native)"
pip_prefix="$(find_prefix py_pip_native)"
setuptools_prefix="$(find_prefix py_setuptools_native)"
wheel_prefix="$(find_prefix py_wheel_native)"

for name in prefix python_prefix venv_prefix mpmath_prefix pip_prefix setuptools_prefix wheel_prefix; do
  value="${!name:-}"
  [[ -n "$value" && -d "$value" ]] || { echo "missing prefix discovery for $name" >&2; exit 1; }
done

python_abi="$("$python_prefix/bin/python3" -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
[[ "$python_abi" == "3.13" ]] || { echo "unexpected Python ABI: $python_abi" >&2; exit 1; }

site_packages="$prefix/lib/python${python_abi}/site-packages"
[[ -x "$prefix/bin/isympy" ]] || { echo "missing executable isympy" >&2; exit 1; }
[[ -f "$prefix/share/man/man1/isympy.1" ]] || { echo "missing isympy manpage" >&2; exit 1; }
[[ -f "$site_packages/isympy.py" ]] || { echo "missing isympy module" >&2; exit 1; }
[[ -f "$site_packages/sympy/__init__.py" ]] || { echo "missing sympy package" >&2; exit 1; }
[[ -f "$site_packages/sympy/core/basic.py" ]] || { echo "missing sympy core" >&2; exit 1; }
[[ -f "$site_packages/sympy/integrals/integrals.py" ]] || { echo "missing sympy integrals" >&2; exit 1; }
[[ -f "$site_packages/sympy/parsing/sympy_parser.py" ]] || { echo "missing sympy parser" >&2; exit 1; }
[[ -f "$site_packages/sympy/polys/polytools.py" ]] || { echo "missing sympy polys" >&2; exit 1; }
[[ -f "$site_packages/sympy-1.14.0.dist-info/METADATA" ]] || { echo "missing sympy metadata" >&2; exit 1; }
[[ -f "$site_packages/sympy-1.14.0.dist-info/WHEEL" ]] || { echo "missing sympy wheel metadata" >&2; exit 1; }

export LD_LIBRARY_PATH="$python_prefix/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
export PYTHONPATH="$site_packages:$mpmath_prefix/lib/python${python_abi}/site-packages:$pip_prefix/lib/python${python_abi}/site-packages:$setuptools_prefix/lib/python${python_abi}/site-packages:$wheel_prefix/lib/python${python_abi}/site-packages:$venv_prefix/lib/python${python_abi}/site-packages"
out="$("$venv_prefix/bin/python${python_abi}" - <<'PY'
import importlib.metadata

import sympy as sp

x = sp.symbols("x")
expr = sp.integrate(sp.sin(x) ** 2, (x, 0, sp.pi))
poly = sp.factor(x ** 4 - 1)
print("py-sympy:%s:%s:%s" % (
    importlib.metadata.version("sympy"),
    expr,
    poly,
))
PY
)"
[[ "$out" == "py-sympy:1.14.0:pi/2:(x - 1)*(x + 1)*(x**2 + 1)" ]] || {
  echo "unexpected sympy output: $out" >&2
  exit 1
}

echo "$out"
