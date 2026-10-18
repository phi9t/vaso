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

prefix="$(find_prefix py_certifi_native)"
python_prefix="$(find_prefix python_313_native)"
venv_prefix="$(find_prefix python_venv_native)"
pip_prefix="$(find_prefix py_pip_native)"
setuptools_prefix="$(find_prefix py_setuptools_native)"
wheel_prefix="$(find_prefix py_wheel_native)"
python_abi="$("$python_prefix/bin/python3" -c 'import sys; print("%d.%d" % sys.version_info[:2])')"

[[ "$python_abi" == "3.13" ]] || { echo "unexpected Python ABI: $python_abi" >&2; exit 1; }
site_packages="$prefix/lib/python${python_abi}/site-packages"
cacert="$site_packages/certifi/cacert.pem"
[[ -d "$site_packages/certifi" ]] || { echo "missing certifi package" >&2; exit 1; }
[[ -f "$site_packages/certifi/__init__.py" ]] || { echo "missing certifi __init__" >&2; exit 1; }
[[ -f "$site_packages/certifi/core.py" ]] || { echo "missing certifi core" >&2; exit 1; }
[[ -f "$cacert" ]] || { echo "missing certifi cacert.pem" >&2; exit 1; }
[[ -f "$site_packages/certifi-2026.2.25.dist-info/METADATA" ]] || { echo "missing certifi metadata" >&2; exit 1; }

export LD_LIBRARY_PATH="$python_prefix/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
export PYTHONPATH="$site_packages:$pip_prefix/lib/python${python_abi}/site-packages:$setuptools_prefix/lib/python${python_abi}/site-packages:$wheel_prefix/lib/python${python_abi}/site-packages:$venv_prefix/lib/python${python_abi}/site-packages"
out="$("$venv_prefix/bin/python${python_abi}" - "$prefix" <<'PY'
import importlib.metadata
import pathlib
import sys
import certifi

prefix = pathlib.Path(sys.argv[1])
where = pathlib.Path(certifi.where())
print(
    "py-certifi:%s:%s:%s"
    % (
        certifi.__version__,
        importlib.metadata.version("certifi"),
        where.is_file() and where.is_relative_to(prefix),
    )
)
PY
)"
[[ "$out" == "py-certifi:2026.02.25:2026.2.25:True" ]] || {
  echo "unexpected import output: $out" >&2
  exit 1
}

echo "$out"
