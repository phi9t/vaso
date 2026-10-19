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
  hit="$(find_runfile_by_pattern "*+*${marker}*/prefix_path.txt" || true)"
  if [[ -n "$hit" ]]; then
    if [[ -r "$hit" ]]; then
      hit="$(tr -d '\n' < "$hit")"
      if [[ -d "$hit" ]]; then
        echo "$hit"
        return 0
      fi
    fi
  fi
  hit="$(find_runfile_by_pattern "*+*${marker}*/prefix" || true)"
  if [[ -n "$hit" && -d "$hit" ]]; then
    echo "$hit"
    return 0
  fi
  return 1
}

prefix="$(find_prefix python_venv_native)"
python_prefix="$(find_prefix python_313_native)"
python_abi="$("$python_prefix/bin/python3" -c 'import sys; print("%d.%d" % sys.version_info[:2])')"

[[ "$python_abi" == "3.13" ]] || { echo "unexpected Python ABI: $python_abi" >&2; exit 1; }
[[ -x "$prefix/bin/python${python_abi}" ]] || { echo "missing venv python" >&2; exit 1; }
[[ -d "$prefix/lib/python${python_abi}/site-packages" ]] || { echo "missing site-packages" >&2; exit 1; }

export LD_LIBRARY_PATH="$python_prefix/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
out="$("$prefix/bin/python${python_abi}" -c 'import pathlib, sys; print(f"python-venv:{sys.prefix != sys.base_prefix}:{sys.version_info[0]}.{sys.version_info[1]}:{pathlib.Path(sys.prefix).name}")')"
case "$out" in
  python-venv:True:3.13:prefix|python-venv:True:3.13:python-venv-*) ;;
  *) echo "unexpected venv output: $out" >&2; exit 1 ;;
esac
echo "$out"
