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

prefix="$(find_prefix py_pyyaml_native)"
python_prefix="$(find_prefix python_313_native)"
venv_prefix="$(find_prefix python_venv_native)"
pip_prefix="$(find_prefix py_pip_native)"
setuptools_prefix="$(find_prefix py_setuptools_native)"
wheel_prefix="$(find_prefix py_wheel_native)"
cython_prefix="$(find_prefix py_cython_native)"
libyaml_prefix="$(find_prefix libyaml_native)"

python_abi="$("$python_prefix/bin/python3" -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
python_ext_suffix="$("$python_prefix/bin/python3" -c 'import sysconfig; print(sysconfig.get_config_var("EXT_SUFFIX") or "")')"

[[ "$python_abi" == "3.13" ]] || { echo "unexpected Python ABI: $python_abi" >&2; exit 1; }
[[ "$python_ext_suffix" == *"cpython-313"* ]] || { echo "unexpected extension suffix: $python_ext_suffix" >&2; exit 1; }
site_packages="$prefix/lib/python${python_abi}/site-packages"
[[ -d "$site_packages/yaml" ]] || { echo "missing yaml package" >&2; exit 1; }
[[ -d "$site_packages/_yaml" ]] || { echo "missing _yaml package" >&2; exit 1; }
[[ -f "$site_packages/yaml/__init__.py" ]] || { echo "missing yaml __init__" >&2; exit 1; }
[[ -f "$site_packages/yaml/cyaml.py" ]] || { echo "missing cyaml module" >&2; exit 1; }
[[ -f "$site_packages/yaml/_yaml${python_ext_suffix}" ]] || { echo "missing yaml C extension" >&2; exit 1; }
[[ -f "$site_packages/pyyaml-6.0.3.dist-info/METADATA" ]] || { echo "missing pyyaml metadata" >&2; exit 1; }
[[ -f "$site_packages/pyyaml-6.0.3.dist-info/WHEEL" ]] || { echo "missing pyyaml wheel metadata" >&2; exit 1; }

export LD_LIBRARY_PATH="$python_prefix/lib:$libyaml_prefix/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
export PYTHONPATH="$site_packages:$pip_prefix/lib/python${python_abi}/site-packages:$setuptools_prefix/lib/python${python_abi}/site-packages:$wheel_prefix/lib/python${python_abi}/site-packages:$cython_prefix/lib/python${python_abi}/site-packages:$venv_prefix/lib/python${python_abi}/site-packages"
out="$("$venv_prefix/bin/python${python_abi}" - <<'PY'
import importlib.metadata
import yaml
import yaml.cyaml

document = yaml.load("items:\n  - one\n  - two\n", Loader=yaml.CLoader)
dumped = yaml.dump(document, Dumper=yaml.CDumper)
print(
    "py-pyyaml:%s:%s:%s:%d"
    % (
        importlib.metadata.version("pyyaml"),
        yaml.__with_libyaml__,
        yaml.CDumper.__name__,
        len(document["items"]) + int("items:" in dumped),
    )
)
PY
)"
[[ "$out" == "py-pyyaml:6.0.3:True:CDumper:3" ]] || {
  echo "unexpected import output: $out" >&2
  exit 1
}

echo "$out"
