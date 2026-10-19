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

prefix="$(find_prefix py_pyproject_metadata_native)"
python_prefix="$(find_prefix python_313_native)"
venv_prefix="$(find_prefix python_venv_native)"
pip_prefix="$(find_prefix py_pip_native)"
flit_core_prefix="$(find_prefix py_flit_core_native)"
packaging_prefix="$(find_prefix py_packaging_native)"
wheel_prefix="$(find_prefix py_wheel_native)"
python_abi="$("$python_prefix/bin/python3" -c 'import sys; print("%d.%d" % sys.version_info[:2])')"

[[ "$python_abi" == "3.13" ]] || { echo "unexpected Python ABI: $python_abi" >&2; exit 1; }
site_packages="$prefix/lib/python${python_abi}/site-packages"
[[ -d "$site_packages/pyproject_metadata" ]] || { echo "missing pyproject_metadata package" >&2; exit 1; }
[[ -f "$site_packages/pyproject_metadata/__init__.py" ]] || { echo "missing pyproject_metadata __init__" >&2; exit 1; }
[[ -f "$site_packages/pyproject_metadata/project_table.py" ]] || { echo "missing project_table module" >&2; exit 1; }
[[ -f "$site_packages/pyproject_metadata/pyproject.py" ]] || { echo "missing pyproject module" >&2; exit 1; }
[[ -f "$site_packages/pyproject_metadata-0.11.0.dist-info/METADATA" ]] || { echo "missing metadata" >&2; exit 1; }
[[ -f "$site_packages/pyproject_metadata-0.11.0.dist-info/WHEEL" ]] || { echo "missing wheel metadata" >&2; exit 1; }

export LD_LIBRARY_PATH="$python_prefix/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
export PYTHONPATH="$site_packages:$packaging_prefix/lib/python${python_abi}/site-packages:$pip_prefix/lib/python${python_abi}/site-packages:$flit_core_prefix/lib/python${python_abi}/site-packages:$wheel_prefix/lib/python${python_abi}/site-packages:$venv_prefix/lib/python${python_abi}/site-packages"
out="$("$venv_prefix/bin/python${python_abi}" - <<'PY'
import importlib.metadata

from pyproject_metadata import StandardMetadata

metadata = StandardMetadata.from_pyproject(
    {
        "project": {
            "name": "demo_pkg",
            "version": "1.2.3",
            "dependencies": ["packaging>=23.2"],
        }
    },
    allow_extra_keys=False,
)
print(
    "py-pyproject-metadata:%s:%s:%s:%s"
    % (
        importlib.metadata.version("pyproject-metadata"),
        metadata.canonical_name,
        metadata.version,
        metadata.dependencies[0].name,
    )
)
PY
)"
[[ "$out" == "py-pyproject-metadata:0.11.0:demo-pkg:1.2.3:packaging" ]] || {
  echo "unexpected import output: $out" >&2
  exit 1
}

echo "$out"
