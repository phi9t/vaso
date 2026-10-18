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

prefix="$(find_prefix py_versioneer_native)"
python_prefix="$(find_prefix python_native)"
venv_prefix="$(find_prefix python_venv_native)"
pip_prefix="$(find_prefix py_pip_native)"
setuptools_prefix="$(find_prefix py_setuptools_native)"
wheel_prefix="$(find_prefix py_wheel_native)"

for name in prefix python_prefix venv_prefix pip_prefix setuptools_prefix wheel_prefix; do
  value="${!name:-}"
  [[ -n "$value" && -d "$value" ]] || { echo "missing prefix discovery for $name" >&2; exit 1; }
done

site_packages="$prefix/lib/python3.14/site-packages"
[[ -x "$prefix/bin/versioneer" ]] || { echo "missing versioneer script" >&2; exit 1; }
[[ -f "$site_packages/versioneer.py" ]] || { echo "missing versioneer module" >&2; exit 1; }
[[ -f "$site_packages/versioneer-0.29.dist-info/METADATA" ]] || { echo "missing versioneer metadata" >&2; exit 1; }
[[ -f "$site_packages/versioneer-0.29.dist-info/WHEEL" ]] || { echo "missing versioneer wheel metadata" >&2; exit 1; }
[[ -f "$site_packages/versioneer-0.29.dist-info/entry_points.txt" ]] || { echo "missing versioneer entry points" >&2; exit 1; }
[[ -f "$site_packages/versioneer-0.29.dist-info/licenses/LICENSE" ]] || { echo "missing versioneer license" >&2; exit 1; }

export LD_LIBRARY_PATH="$python_prefix/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
export PYTHONPATH="$site_packages:$pip_prefix/lib/python3.14/site-packages:$setuptools_prefix/lib/python3.14/site-packages:$wheel_prefix/lib/python3.14/site-packages:$venv_prefix/lib/python3.14/site-packages"
import_out="$("$venv_prefix/bin/python3" - <<'PY'
import importlib.metadata

import versioneer

print(
    "py-versioneer:%s:%s:%s"
    % (
        importlib.metadata.version("versioneer"),
        versioneer.__name__,
        callable(versioneer.main),
    )
)
PY
)"
script_out="$("$prefix/bin/versioneer" --version)"
out="$import_out:$script_out"
[[ "$out" == "py-versioneer:0.29:versioneer:True:versioneer (installer) 0.29" ]] || {
  echo "unexpected versioneer output: $out" >&2
  exit 1
}

echo "$out"
