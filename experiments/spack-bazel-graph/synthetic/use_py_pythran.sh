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

prefix="$(find_prefix py_pythran_native)"
beniget_prefix="$(find_prefix py_beniget_native)"
gast_prefix="$(find_prefix py_gast_native)"
numpy_prefix="$(find_prefix py_numpy_native)"
openblas_prefix="$(find_prefix openblas_native)"
ply_prefix="$(find_prefix py_ply_native)"
python_prefix="$(find_prefix python_313_native)"
venv_prefix="$(find_prefix python_venv_native)"

for name in prefix beniget_prefix gast_prefix numpy_prefix openblas_prefix ply_prefix python_prefix venv_prefix; do
  value="${!name:-}"
  [[ -n "$value" && -d "$value" ]] || { echo "missing prefix discovery for $name" >&2; exit 1; }
done

python_abi="$("$python_prefix/bin/python3" -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
[[ "$python_abi" == "3.13" ]] || { echo "unexpected Python ABI: $python_abi" >&2; exit 1; }

site_packages="$prefix/lib/python${python_abi}/site-packages"
[[ -x "$prefix/bin/pythran" ]] || { echo "missing pythran executable" >&2; exit 1; }
[[ -x "$prefix/bin/pythran-config" ]] || { echo "missing pythran-config executable" >&2; exit 1; }
[[ -f "$site_packages/pythran/__init__.py" ]] || { echo "missing pythran package" >&2; exit 1; }
[[ -f "$site_packages/pythran/config.py" ]] || { echo "missing pythran config" >&2; exit 1; }
[[ -f "$site_packages/pythran/spec.py" ]] || { echo "missing pythran spec" >&2; exit 1; }
[[ -f "$site_packages/pythran/toolchain.py" ]] || { echo "missing pythran toolchain" >&2; exit 1; }
[[ -f "$site_packages/pythran/pythonic/core.hpp" ]] || { echo "missing pythran pythonic headers" >&2; exit 1; }
[[ -f "$site_packages/pythran/xsimd/xsimd.hpp" ]] || { echo "missing pythran xsimd headers" >&2; exit 1; }
[[ -f "$site_packages/pythran-0.18.1.dist-info/METADATA" ]] || { echo "missing pythran metadata" >&2; exit 1; }
[[ -f "$site_packages/pythran-0.18.1.dist-info/WHEEL" ]] || { echo "missing pythran wheel metadata" >&2; exit 1; }

openblas_libdir="$openblas_prefix/lib"
if [[ ! -d "$openblas_libdir" && -d "$openblas_prefix/lib64" ]]; then
  openblas_libdir="$openblas_prefix/lib64"
fi
[[ -d "$openblas_libdir" ]] || { echo "missing OpenBLAS library dir" >&2; exit 1; }

export LD_LIBRARY_PATH="$openblas_libdir:$python_prefix/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
export PYTHONPATH="$site_packages:$beniget_prefix/lib/python${python_abi}/site-packages:$gast_prefix/lib/python${python_abi}/site-packages:$numpy_prefix/lib/python${python_abi}/site-packages:$ply_prefix/lib/python${python_abi}/site-packages:$venv_prefix/lib/python${python_abi}/site-packages"
out="$("$venv_prefix/bin/python${python_abi}" - <<'PY'
import importlib.metadata

import pythran

spec = pythran.spec_parser("#pythran export foo(int)")
print(
    "py-pythran:%s:%s:%s:%s"
    % (
        importlib.metadata.version("pythran"),
        pythran.__version__,
        "foo" in spec.functions,
        pythran.get_include().endswith("/pythran"),
    )
)
PY
)"
[[ "$out" == "py-pythran:0.18.1:0.18.1:True:True" ]] || {
  echo "unexpected pythran output: $out" >&2
  exit 1
}

echo "$out"
