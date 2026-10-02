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

prefix="$(find_prefix py_trove_classifiers_native)"
python_prefix="$(find_prefix python_313_native)"
venv_prefix="$(find_prefix python_venv_native)"
calver_prefix="$(find_prefix py_calver_native)"
pip_prefix="$(find_prefix py_pip_native)"
setuptools_prefix="$(find_prefix py_setuptools_native)"
wheel_prefix="$(find_prefix py_wheel_native)"

for name in prefix python_prefix venv_prefix calver_prefix pip_prefix setuptools_prefix wheel_prefix; do
  value="${!name:-}"
  [[ -n "$value" && -d "$value" ]] || { echo "missing prefix discovery for $name" >&2; exit 1; }
done

python_abi="$("$python_prefix/bin/python3" -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
[[ "$python_abi" == "3.13" ]] || { echo "unexpected Python ABI: $python_abi" >&2; exit 1; }

site_packages="$prefix/lib/python${python_abi}/site-packages"
[[ -x "$prefix/bin/trove-classifiers" ]] || { echo "missing trove-classifiers executable" >&2; exit 1; }
[[ -f "$site_packages/trove_classifiers/__init__.py" ]] || { echo "missing trove_classifiers package" >&2; exit 1; }
[[ -f "$site_packages/trove_classifiers/__main__.py" ]] || { echo "missing trove_classifiers cli module" >&2; exit 1; }
[[ -f "$site_packages/trove_classifiers/py.typed" ]] || { echo "missing trove_classifiers py.typed" >&2; exit 1; }
[[ -f "$site_packages/trove_classifiers-2026.6.1.19.dist-info/METADATA" ]] || { echo "missing trove_classifiers metadata" >&2; exit 1; }
[[ -f "$site_packages/trove_classifiers-2026.6.1.19.dist-info/WHEEL" ]] || { echo "missing trove_classifiers wheel metadata" >&2; exit 1; }

export LD_LIBRARY_PATH="$python_prefix/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
export PYTHONPATH="$site_packages:$calver_prefix/lib/python${python_abi}/site-packages:$pip_prefix/lib/python${python_abi}/site-packages:$setuptools_prefix/lib/python${python_abi}/site-packages:$wheel_prefix/lib/python${python_abi}/site-packages:$venv_prefix/lib/python${python_abi}/site-packages"
out="$("$venv_prefix/bin/python${python_abi}" - <<'PY'
import importlib.metadata

from trove_classifiers import classifiers, sorted_classifiers

print(
    "py-trove-classifiers:%s:%d:%s:%s"
    % (
        importlib.metadata.version("trove-classifiers"),
        len(sorted_classifiers),
        "Environment :: GPU :: NVIDIA CUDA :: 13" in classifiers,
        sorted_classifiers[-1],
    )
)
PY
)"
[[ "$out" == "py-trove-classifiers:2026.6.1.19:895:True:Typing :: Typed" ]] || {
  echo "unexpected trove-classifiers output: $out" >&2
  exit 1
}

echo "$out"
