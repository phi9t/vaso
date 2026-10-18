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
  hit="$(find_runfile_by_pattern "*/+*+${marker}/prefix_path.txt" || true)"
  if [[ -n "$hit" && -r "$hit" ]]; then
    hit="$(tr -d '\n' < "$hit")"
    if [[ -d "$hit" ]]; then
      echo "$hit"
      return 0
    fi
  fi
  hit="$(find_runfile_by_pattern "*/+*+${marker}/prefix" || true)"
  if [[ -n "$hit" && -d "$hit" ]]; then
    echo "$hit"
    return 0
  fi
  return 1
}

setuptools_marker="${1:-py_setuptools_native}"
expected_version="${2:-79.0.1}"
expect_pkg_resources="${3:-1}"

prefix="$(find_prefix "$setuptools_marker")"
python_prefix="$(find_prefix python_313_native)"
venv_prefix="$(find_prefix python_venv_native)"
pip_prefix="$(find_prefix py_pip_native)"
python_abi="$("$python_prefix/bin/python3" -c 'import sys; print("%d.%d" % sys.version_info[:2])')"

[[ "$python_abi" == "3.13" ]] || { echo "unexpected Python ABI: $python_abi" >&2; exit 1; }
site_packages="$prefix/lib/python${python_abi}/site-packages"
[[ -d "$site_packages/setuptools" ]] || { echo "missing setuptools package" >&2; exit 1; }
if [[ "$expect_pkg_resources" == "1" ]]; then
  [[ -d "$site_packages/pkg_resources" ]] || { echo "missing pkg_resources package" >&2; exit 1; }
else
  [[ ! -e "$site_packages/pkg_resources" ]] || { echo "unexpected pkg_resources package" >&2; exit 1; }
fi
[[ -d "$site_packages/_distutils_hack" ]] || { echo "missing _distutils_hack package" >&2; exit 1; }
[[ -f "$site_packages/distutils-precedence.pth" ]] || { echo "missing distutils-precedence.pth" >&2; exit 1; }
[[ -f "$site_packages/setuptools-${expected_version}.dist-info/METADATA" ]] || { echo "missing setuptools metadata" >&2; exit 1; }

export LD_LIBRARY_PATH="$python_prefix/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
export PYTHONPATH="$site_packages:$pip_prefix/lib/python${python_abi}/site-packages:$venv_prefix/lib/python${python_abi}/site-packages"
out="$("$venv_prefix/bin/python${python_abi}" -W ignore - "$expected_version" "$expect_pkg_resources" <<'PY'
import importlib.metadata
import importlib.util
import sys
import setuptools
import _distutils_hack

expected_version = sys.argv[1]
expect_pkg_resources = sys.argv[2] == "1"
has_pkg_resources = importlib.util.find_spec("pkg_resources") is not None
if has_pkg_resources != expect_pkg_resources:
    raise SystemExit("pkg_resources expectation failed")
print(
    "py-setuptools:%s:%s:%s:%s"
    % (
        setuptools.__version__,
        importlib.metadata.version("setuptools"),
        "pkg_resources" if has_pkg_resources else "no-pkg-resources",
        _distutils_hack.__name__,
    )
)
PY
)"
[[ "$out" == "py-setuptools:${expected_version}:${expected_version}:$([[ "$expect_pkg_resources" == "1" ]] && echo pkg_resources || echo no-pkg-resources):_distutils_hack" ]] || {
  echo "unexpected import output: $out" >&2
  exit 1
}

echo "$out"
