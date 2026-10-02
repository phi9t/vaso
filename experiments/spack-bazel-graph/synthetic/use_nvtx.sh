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

prefix="$(find_prefix nvtx_native)"
python_prefix="$(find_prefix python_313_native)"
venv_prefix="$(find_prefix python_venv_native)"
pip_prefix="$(find_prefix py_pip_native)"
setuptools_prefix="$(find_prefix py_setuptools_native)"
wheel_prefix="$(find_prefix py_wheel_native)"
cython_prefix="$(find_prefix py_cython_native)"
python_abi="$("$python_prefix/bin/python3" -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
python_ext_suffix="$("$python_prefix/bin/python${python_abi}" - <<'PY'
import importlib.machinery

print(importlib.machinery.EXTENSION_SUFFIXES[0])
PY
)"

[[ "$python_abi" == "3.13" ]] || { echo "unexpected Python ABI: $python_abi" >&2; exit 1; }
[[ "$python_ext_suffix" == *"cpython-313"* ]] || { echo "unexpected extension suffix: $python_ext_suffix" >&2; exit 1; }
site_packages="$prefix/lib/python${python_abi}/site-packages"
[[ -f "$prefix/include/nvtx3/nvToolsExt.h" ]] || { echo "missing nvToolsExt.h" >&2; exit 1; }
[[ -f "$prefix/include/nvtx3/nvtx3.hpp" ]] || { echo "missing nvtx3.hpp" >&2; exit 1; }
[[ -f "$prefix/nvtx-config.cmake" ]] || { echo "missing nvtx-config.cmake" >&2; exit 1; }
[[ -f "$site_packages/nvtx/_lib/lib${python_ext_suffix}" ]] || { echo "missing lib extension" >&2; exit 1; }
[[ -f "$site_packages/nvtx/_lib/profiler${python_ext_suffix}" ]] || { echo "missing profiler extension" >&2; exit 1; }

export LD_LIBRARY_PATH="$python_prefix/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
export PYTHONPATH="$site_packages:$cython_prefix/lib/python${python_abi}/site-packages:$pip_prefix/lib/python${python_abi}/site-packages:$setuptools_prefix/lib/python${python_abi}/site-packages:$wheel_prefix/lib/python${python_abi}/site-packages:$venv_prefix/lib/python${python_abi}/site-packages"
out="$("$venv_prefix/bin/python${python_abi}" - <<'PY'
import importlib.metadata
import importlib.machinery
import nvtx
from nvtx._lib import lib, profiler

suffix = importlib.machinery.EXTENSION_SUFFIXES[0]
if "cpython-313" not in suffix:
    raise SystemExit("unexpected extension suffix: %s" % suffix)
with nvtx.annotate("vaso", color="blue"):
    payload = lib.__name__
print(
    "nvtx:%s:%s:%s:%s:%s"
    % (
        importlib.metadata.version("nvtx"),
        suffix,
        nvtx.annotate.__name__,
        payload,
        profiler.__name__,
    )
)
PY
)"
[[ "$out" == "nvtx:0.2.14a1:.cpython-313-x86_64-linux-gnu.so:annotate:nvtx._lib.lib:nvtx._lib.profiler" ]] || {
  echo "unexpected import output: $out" >&2
  exit 1
}

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
cat > "$tmp/use_nvtx.c" <<'C'
#include <nvtx3/nvToolsExt.h>

int main(void) {
  nvtxEventAttributes_t event = {0};
  event.version = NVTX_VERSION;
  event.size = NVTX_EVENT_ATTRIB_STRUCT_SIZE;
  return event.version == NVTX_VERSION ? 0 : 1;
}
C
/usr/bin/gcc -I"$prefix/include" "$tmp/use_nvtx.c" -o "$tmp/use_nvtx"
"$tmp/use_nvtx"

echo "$out"
