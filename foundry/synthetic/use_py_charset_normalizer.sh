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

prefix="$(find_prefix py_charset_normalizer_native)"
python_prefix="$(find_prefix python_313_native)"
venv_prefix="$(find_prefix python_venv_native)"
pip_prefix="$(find_prefix py_pip_native)"
setuptools_prefix="$(find_prefix py_setuptools_native)"
wheel_prefix="$(find_prefix py_wheel_native)"
python_abi="$("$python_prefix/bin/python3" -c 'import sys; print("%d.%d" % sys.version_info[:2])')"

[[ "$python_abi" == "3.13" ]] || { echo "unexpected Python ABI: $python_abi" >&2; exit 1; }
site_packages="$prefix/lib/python${python_abi}/site-packages"
[[ -x "$prefix/bin/normalizer" ]] || { echo "missing normalizer executable" >&2; exit 1; }
[[ -d "$site_packages/charset_normalizer" ]] || { echo "missing charset_normalizer package" >&2; exit 1; }
[[ -f "$site_packages/charset_normalizer/__init__.py" ]] || { echo "missing charset_normalizer __init__" >&2; exit 1; }
[[ -f "$site_packages/charset_normalizer/api.py" ]] || { echo "missing charset_normalizer api" >&2; exit 1; }
[[ -f "$site_packages/charset_normalizer/cli/__main__.py" ]] || { echo "missing charset_normalizer cli" >&2; exit 1; }
[[ -f "$site_packages/charset_normalizer/version.py" ]] || { echo "missing charset_normalizer version" >&2; exit 1; }
[[ -f "$site_packages/charset_normalizer-3.4.4.dist-info/METADATA" ]] || { echo "missing charset-normalizer metadata" >&2; exit 1; }

export LD_LIBRARY_PATH="$python_prefix/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
export PYTHONPATH="$site_packages:$pip_prefix/lib/python${python_abi}/site-packages:$setuptools_prefix/lib/python${python_abi}/site-packages:$wheel_prefix/lib/python${python_abi}/site-packages:$venv_prefix/lib/python${python_abi}/site-packages"
out="$("$venv_prefix/bin/python${python_abi}" - <<'PY'
import importlib.metadata
import charset_normalizer
from charset_normalizer import from_bytes

best = from_bytes(b"hello").best()
print(
    "py-charset-normalizer:%s:%s:%s"
    % (
        charset_normalizer.__version__,
        importlib.metadata.version("charset-normalizer"),
        str(best),
    )
)
PY
)"
[[ "$out" == "py-charset-normalizer:3.4.4:3.4.4:hello" ]] || {
  echo "unexpected import output: $out" >&2
  exit 1
}

tmp_root="${TEST_TMPDIR:-${TMPDIR:-$PWD}}"
normalizer_version_out="$tmp_root/normalizer-version.txt"
"$prefix/bin/normalizer" --version >"$normalizer_version_out"
if ! grep -q '3\.4\.4' "$normalizer_version_out"; then
  echo "unexpected normalizer --version output:" >&2
  cat "$normalizer_version_out" >&2
  exit 1
fi

echo "$out"
