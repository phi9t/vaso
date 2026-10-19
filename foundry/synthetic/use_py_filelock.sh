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

prefix="$(find_prefix py_filelock_native)"
python_prefix="$(find_prefix python_313_native)"
venv_prefix="$(find_prefix python_venv_native)"

for name in prefix python_prefix venv_prefix; do
  value="${!name:-}"
  [[ -n "$value" && -d "$value" ]] || { echo "missing prefix discovery for $name" >&2; exit 1; }
done

python_abi="$("$python_prefix/bin/python3" -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
[[ "$python_abi" == "3.13" ]] || { echo "unexpected Python ABI: $python_abi" >&2; exit 1; }

site_packages="$prefix/lib/python${python_abi}/site-packages"
[[ -f "$site_packages/filelock/__init__.py" ]] || { echo "missing filelock package" >&2; exit 1; }
[[ -f "$site_packages/filelock/_api.py" ]] || { echo "missing filelock API module" >&2; exit 1; }
[[ -f "$site_packages/filelock/_soft.py" ]] || { echo "missing soft filelock module" >&2; exit 1; }
[[ -f "$site_packages/filelock/_unix.py" ]] || { echo "missing unix filelock module" >&2; exit 1; }
[[ -f "$site_packages/filelock/asyncio.py" ]] || { echo "missing asyncio filelock module" >&2; exit 1; }
[[ -f "$site_packages/filelock/py.typed" ]] || { echo "missing filelock py.typed" >&2; exit 1; }
[[ -f "$site_packages/filelock-3.29.1.dist-info/METADATA" ]] || { echo "missing filelock metadata" >&2; exit 1; }
[[ -f "$site_packages/filelock-3.29.1.dist-info/WHEEL" ]] || { echo "missing filelock wheel metadata" >&2; exit 1; }
[[ -f "$site_packages/filelock-3.29.1.dist-info/licenses/LICENSE" ]] || { echo "missing filelock license" >&2; exit 1; }

export LD_LIBRARY_PATH="$python_prefix/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
export PYTHONPATH="$site_packages:$venv_prefix/lib/python${python_abi}/site-packages"
out="$("$venv_prefix/bin/python${python_abi}" - <<'PY'
import importlib
import importlib.metadata
import tempfile
from pathlib import Path

import filelock
from filelock import FileLock, SoftFileLock, Timeout

with tempfile.TemporaryDirectory() as td:
    lock_path = Path(td) / "guard.lock"
    payload_path = Path(td) / "payload.txt"
    lock = FileLock(lock_path)
    with lock.acquire(timeout=1):
        payload_path.write_text("native-filelock", encoding="utf-8")
    try:
        with FileLock(lock_path).acquire(timeout=0):
            pass
    except Timeout:
        pass
    soft_name = SoftFileLock.__name__

version_module = importlib.import_module("filelock.version")
print(
    "py-filelock:%s:%s:%s:%s:%s"
    % (
        importlib.metadata.version("filelock"),
        filelock.__version__,
        version_module.version,
        FileLock.__name__,
        soft_name,
    )
)
PY
)"
[[ "$out" == "py-filelock:3.29.1:3.29.1:3.29.1:UnixFileLock:SoftFileLock" ]] || {
  echo "unexpected filelock output: $out" >&2
  exit 1
}

echo "$out"
