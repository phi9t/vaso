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

prefix="$(find_prefix py_cython_native)"
python_prefix="$(find_prefix python_313_native)"
venv_prefix="$(find_prefix python_venv_native)"
pip_prefix="$(find_prefix py_pip_native)"
setuptools_prefix="$(find_prefix py_setuptools_native)"
wheel_prefix="$(find_prefix py_wheel_native)"
python_abi="$("$python_prefix/bin/python3" -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
python_ext_suffix="$("$python_prefix/bin/python${python_abi}" - <<'PY'
import importlib.machinery

print(importlib.machinery.EXTENSION_SUFFIXES[0])
PY
)"

[[ "$python_abi" == "3.13" ]] || { echo "unexpected Python ABI: $python_abi" >&2; exit 1; }
[[ "$python_ext_suffix" == *"cpython-313"* ]] || { echo "unexpected extension suffix: $python_ext_suffix" >&2; exit 1; }
site_packages="$prefix/lib/python${python_abi}/site-packages"
[[ -x "$prefix/bin/cython" ]] || { echo "missing cython executable" >&2; exit 1; }
[[ -x "$prefix/bin/cythonize" ]] || { echo "missing cythonize executable" >&2; exit 1; }
[[ -x "$prefix/bin/cygdb" ]] || { echo "missing cygdb executable" >&2; exit 1; }
[[ -f "$site_packages/Cython/Compiler/Parsing${python_ext_suffix}" ]] || { echo "missing Parsing extension" >&2; exit 1; }
[[ -f "$site_packages/Cython/Compiler/Code${python_ext_suffix}" ]] || { echo "missing Code extension" >&2; exit 1; }
[[ -f "$site_packages/Cython/Includes/libc/stdio.pxd" ]] || { echo "missing libc pxd" >&2; exit 1; }
[[ -f "$site_packages/cython-3.2.4.dist-info/METADATA" ]] || { echo "missing Cython metadata" >&2; exit 1; }

export LD_LIBRARY_PATH="$python_prefix/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
export PYTHONPATH="$site_packages:$pip_prefix/lib/python${python_abi}/site-packages:$setuptools_prefix/lib/python${python_abi}/site-packages:$wheel_prefix/lib/python${python_abi}/site-packages:$venv_prefix/lib/python${python_abi}/site-packages"
out="$("$venv_prefix/bin/python${python_abi}" - <<'PY'
import importlib.metadata
import importlib.machinery
import Cython
from Cython.Compiler import Code, Parsing, Scanning, Visitor

suffix = importlib.machinery.EXTENSION_SUFFIXES[0]
if "cpython-313" not in suffix:
    raise SystemExit("unexpected extension suffix: %s" % suffix)
print(
    "py-cython:%s:%s:%s:%s:%s"
    % (
        Cython.__version__,
        importlib.metadata.version("cython"),
        suffix,
        Parsing.__name__,
        Visitor.__name__,
    )
)
PY
)"
[[ "$out" == "py-cython:3.2.4:3.2.4:.cpython-313-x86_64-linux-gnu.so:Cython.Compiler.Parsing:Cython.Compiler.Visitor" ]] || {
  echo "unexpected import output: $out" >&2
  exit 1
}

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
cat > "$tmp/demo.pyx" <<'PYX'
def square(int x):
    return x * x
PYX
"$prefix/bin/cython" -3 -o "$tmp/demo.c" "$tmp/demo.pyx"
grep -q "square" "$tmp/demo.c" || { echo "generated C did not contain square" >&2; exit 1; }
"$prefix/bin/cython" --version >"$tmp/cython-version.txt" 2>&1
grep -q 'Cython version 3\.2\.4' "$tmp/cython-version.txt" || {
  echo "unexpected cython --version output:" >&2
  cat "$tmp/cython-version.txt" >&2
  exit 1
}

echo "$out"
