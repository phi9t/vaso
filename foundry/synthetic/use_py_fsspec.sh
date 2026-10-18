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

prefix="$(find_prefix py_fsspec_native)"
python_prefix="$(find_prefix python_313_native)"
venv_prefix="$(find_prefix python_venv_native)"

for name in prefix python_prefix venv_prefix; do
  value="${!name:-}"
  [[ -n "$value" && -d "$value" ]] || { echo "missing prefix discovery for $name" >&2; exit 1; }
done

python_abi="$("$python_prefix/bin/python3" -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
[[ "$python_abi" == "3.13" ]] || { echo "unexpected Python ABI: $python_abi" >&2; exit 1; }

site_packages="$prefix/lib/python${python_abi}/site-packages"
[[ -f "$site_packages/fsspec/__init__.py" ]] || { echo "missing fsspec package" >&2; exit 1; }
[[ -f "$site_packages/fsspec/_version.py" ]] || { echo "missing fsspec version module" >&2; exit 1; }
[[ -f "$site_packages/fsspec/core.py" ]] || { echo "missing fsspec core module" >&2; exit 1; }
[[ -f "$site_packages/fsspec/spec.py" ]] || { echo "missing fsspec spec module" >&2; exit 1; }
[[ -f "$site_packages/fsspec/registry.py" ]] || { echo "missing fsspec registry module" >&2; exit 1; }
[[ -f "$site_packages/fsspec/implementations/local.py" ]] || { echo "missing fsspec local filesystem module" >&2; exit 1; }
[[ -f "$site_packages/fsspec/implementations/memory.py" ]] || { echo "missing fsspec memory filesystem module" >&2; exit 1; }
[[ -f "$site_packages/fsspec-2026.3.0.dist-info/METADATA" ]] || { echo "missing fsspec metadata" >&2; exit 1; }
[[ -f "$site_packages/fsspec-2026.3.0.dist-info/WHEEL" ]] || { echo "missing fsspec wheel metadata" >&2; exit 1; }
[[ -f "$site_packages/fsspec-2026.3.0.dist-info/licenses/LICENSE" ]] || { echo "missing fsspec license" >&2; exit 1; }

export LD_LIBRARY_PATH="$python_prefix/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
export PYTHONPATH="$site_packages:$venv_prefix/lib/python${python_abi}/site-packages"
out="$("$venv_prefix/bin/python${python_abi}" - <<'PY'
import importlib.metadata

import fsspec

fs = fsspec.filesystem("memory")
with fs.open("memory://vaso-py-fsspec.txt", "w") as handle:
    handle.write("native-fsspec")
with fs.open("memory://vaso-py-fsspec.txt", "r") as handle:
    payload = handle.read()

print(
    "py-fsspec:%s:%s:%s:%s"
    % (
        importlib.metadata.version("fsspec"),
        fsspec.__version__,
        fs.protocol,
        payload,
    )
)
PY
)"
[[ "$out" == "py-fsspec:2026.3.0:2026.3.0:memory:native-fsspec" ]] || {
  echo "unexpected fsspec output: $out" >&2
  exit 1
}

echo "$out"
