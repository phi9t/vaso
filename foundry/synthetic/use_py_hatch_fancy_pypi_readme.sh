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

prefix="$(find_prefix py_hatch_fancy_pypi_readme_native)"
python_prefix="$(find_prefix python_313_native)"
venv_prefix="$(find_prefix python_venv_native)"
hatchling_prefix="$(find_prefix py_hatchling_native)"
packaging_prefix="$(find_prefix py_packaging_native)"
pathspec_prefix="$(find_prefix py_pathspec_native)"
pip_prefix="$(find_prefix py_pip_native)"
pluggy_prefix="$(find_prefix py_pluggy_native)"
trove_prefix="$(find_prefix py_trove_classifiers_native)"
wheel_prefix="$(find_prefix py_wheel_native)"

for name in prefix python_prefix venv_prefix hatchling_prefix packaging_prefix pathspec_prefix pip_prefix pluggy_prefix trove_prefix wheel_prefix; do
  value="${!name:-}"
  [[ -n "$value" && -d "$value" ]] || { echo "missing prefix discovery for $name" >&2; exit 1; }
done

python_abi="$("$python_prefix/bin/python3" -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
[[ "$python_abi" == "3.13" ]] || { echo "unexpected Python ABI: $python_abi" >&2; exit 1; }

site_packages="$prefix/lib/python${python_abi}/site-packages"
[[ -x "$prefix/bin/hatch-fancy-pypi-readme" ]] || { echo "missing hatch-fancy-pypi-readme executable" >&2; exit 1; }
[[ -f "$site_packages/hatch_fancy_pypi_readme/__init__.py" ]] || { echo "missing hatch_fancy_pypi_readme package" >&2; exit 1; }
[[ -f "$site_packages/hatch_fancy_pypi_readme/_builder.py" ]] || { echo "missing hatch_fancy_pypi_readme builder" >&2; exit 1; }
[[ -f "$site_packages/hatch_fancy_pypi_readme/_config.py" ]] || { echo "missing hatch_fancy_pypi_readme config" >&2; exit 1; }
[[ -f "$site_packages/hatch_fancy_pypi_readme/hooks.py" ]] || { echo "missing hatch_fancy_pypi_readme hooks" >&2; exit 1; }
[[ -f "$site_packages/hatch_fancy_pypi_readme/py.typed" ]] || { echo "missing hatch_fancy_pypi_readme py.typed" >&2; exit 1; }
[[ -f "$site_packages/hatch_fancy_pypi_readme-25.1.0.dist-info/METADATA" ]] || { echo "missing hatch-fancy-pypi-readme metadata" >&2; exit 1; }
[[ -f "$site_packages/hatch_fancy_pypi_readme-25.1.0.dist-info/WHEEL" ]] || { echo "missing hatch-fancy-pypi-readme wheel metadata" >&2; exit 1; }
[[ -f "$site_packages/hatch_fancy_pypi_readme-25.1.0.dist-info/entry_points.txt" ]] || { echo "missing hatch-fancy-pypi-readme entry points" >&2; exit 1; }

export LD_LIBRARY_PATH="$python_prefix/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
export PYTHONPATH="$site_packages:$hatchling_prefix/lib/python${python_abi}/site-packages:$packaging_prefix/lib/python${python_abi}/site-packages:$pathspec_prefix/lib/python${python_abi}/site-packages:$pip_prefix/lib/python${python_abi}/site-packages:$pluggy_prefix/lib/python${python_abi}/site-packages:$trove_prefix/lib/python${python_abi}/site-packages:$wheel_prefix/lib/python${python_abi}/site-packages:$venv_prefix/lib/python${python_abi}/site-packages"
out="$("$venv_prefix/bin/python${python_abi}" - <<'PY'
import importlib.metadata

from hatch_fancy_pypi_readme import hooks

hook = hooks.hatch_register_metadata_hook()
print(
    "py-hatch-fancy-pypi-readme:%s:%s:%s"
    % (
        importlib.metadata.version("hatch-fancy-pypi-readme"),
        hooks.__name__,
        hook.__name__,
    )
)
PY
)"
[[ "$out" == "py-hatch-fancy-pypi-readme:25.1.0:hatch_fancy_pypi_readme.hooks:FancyReadmeMetadataHook" ]] || {
  echo "unexpected hatch-fancy-pypi-readme output: $out" >&2
  exit 1
}

echo "$out"
