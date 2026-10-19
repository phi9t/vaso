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

prefix="$(find_prefix py_fonttools_native)"
python_prefix="$(find_prefix python_313_native)"
venv_prefix="$(find_prefix python_venv_native)"
pip_prefix="$(find_prefix py_pip_native)"
setuptools_prefix="$(find_prefix py_setuptools_native)"
wheel_prefix="$(find_prefix py_wheel_native)"
python_abi="$("$python_prefix/bin/python3" -c 'import sys; print("%d.%d" % sys.version_info[:2])')"

[[ "$python_abi" == "3.13" ]] || { echo "unexpected Python ABI: $python_abi" >&2; exit 1; }
site_packages="$prefix/lib/python${python_abi}/site-packages"
[[ -d "$site_packages/fontTools" ]] || { echo "missing fontTools package" >&2; exit 1; }
[[ -f "$site_packages/fontTools/__init__.py" ]] || { echo "missing fontTools __init__" >&2; exit 1; }
[[ -f "$site_packages/fontTools/ttx.py" ]] || { echo "missing fontTools.ttx" >&2; exit 1; }
[[ -f "$site_packages/fontTools/ttLib/ttFont.py" ]] || { echo "missing fontTools.ttLib.ttFont" >&2; exit 1; }
[[ -f "$site_packages/fonttools-4.39.4.dist-info/METADATA" ]] || { echo "missing fonttools metadata" >&2; exit 1; }
[[ -x "$prefix/bin/ttx" ]] || { echo "missing ttx script" >&2; exit 1; }
[[ -x "$prefix/bin/pyftsubset" ]] || { echo "missing pyftsubset script" >&2; exit 1; }
[[ -f "$prefix/share/man/man1/ttx.1" ]] || { echo "missing ttx manpage" >&2; exit 1; }

export LD_LIBRARY_PATH="$python_prefix/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
export PYTHONPATH="$site_packages:$pip_prefix/lib/python${python_abi}/site-packages:$setuptools_prefix/lib/python${python_abi}/site-packages:$wheel_prefix/lib/python${python_abi}/site-packages:$venv_prefix/lib/python${python_abi}/site-packages"
out="$("$venv_prefix/bin/python${python_abi}" - <<'PY'
import importlib.metadata
from fontTools.misc.transform import Transform
from fontTools.ttLib import TTFont

version = importlib.metadata.version("fonttools")
matrix = Transform().scale(2).translate(3, 4)
print("py-fonttools:%s:%s:%s" % (version, TTFont.__name__, matrix))
PY
)"
[[ "$out" == "py-fonttools:4.39.4:TTFont:<Transform [2 0 0 2 6 8]>" ]] || {
  echo "unexpected import output: $out" >&2
  exit 1
}

ttx_out="$("$prefix/bin/ttx" --version 2>&1)"
[[ "$ttx_out" == "4.39.4" ]] || {
  echo "unexpected ttx --version output: $ttx_out" >&2
  exit 1
}

echo "$out"
