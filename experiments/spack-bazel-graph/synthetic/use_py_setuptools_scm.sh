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

setuptools_scm_marker="${1:-py_setuptools_scm_native}"
expected_version="${2:-8.2.1}"
setuptools_marker="${3:-py_setuptools_native}"

prefix="$(find_prefix "$setuptools_scm_marker")"
git_prefix="$(find_prefix git_native)"
python_prefix="$(find_prefix python_313_native)"
venv_prefix="$(find_prefix python_venv_native)"
packaging_prefix="$(find_prefix py_packaging_native)"
pip_prefix="$(find_prefix py_pip_native)"
setuptools_prefix="$(find_prefix "$setuptools_marker")"
wheel_prefix="$(find_prefix py_wheel_native)"
python_abi="$("$python_prefix/bin/python3" -c 'import sys; print("%d.%d" % sys.version_info[:2])')"

[[ "$python_abi" == "3.13" ]] || { echo "unexpected Python ABI: $python_abi" >&2; exit 1; }
site_packages="$prefix/lib/python${python_abi}/site-packages"
[[ -d "$site_packages/setuptools_scm" ]] || { echo "missing setuptools_scm package" >&2; exit 1; }
[[ -f "$site_packages/setuptools_scm/__init__.py" ]] || { echo "missing setuptools_scm __init__" >&2; exit 1; }
[[ -f "$site_packages/setuptools_scm/git.py" ]] || { echo "missing setuptools_scm git module" >&2; exit 1; }
[[ -f "$site_packages/setuptools_scm/_integration/toml.py" ]] || { echo "missing setuptools_scm toml integration" >&2; exit 1; }
[[ -f "$site_packages/setuptools_scm-${expected_version}.dist-info/METADATA" ]] || { echo "missing setuptools_scm metadata" >&2; exit 1; }
[[ -f "$site_packages/setuptools_scm-${expected_version}.dist-info/WHEEL" ]] || { echo "missing setuptools_scm wheel metadata" >&2; exit 1; }
[[ -f "$site_packages/setuptools_scm-${expected_version}.dist-info/entry_points.txt" ]] || { echo "missing setuptools_scm entry points" >&2; exit 1; }

export PATH="$git_prefix/bin:$venv_prefix/bin:$python_prefix/bin:/usr/bin:/bin"
export LD_LIBRARY_PATH="$python_prefix/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
export PYTHONPATH="$site_packages:$packaging_prefix/lib/python${python_abi}/site-packages:$pip_prefix/lib/python${python_abi}/site-packages:$setuptools_prefix/lib/python${python_abi}/site-packages:$wheel_prefix/lib/python${python_abi}/site-packages:$venv_prefix/lib/python${python_abi}/site-packages"
out="$("$venv_prefix/bin/python${python_abi}" - "$expected_version" <<'PY'
import importlib.metadata
import os
import pathlib
import subprocess
import tempfile
import sys

from setuptools_scm import get_version
import setuptools_scm.git
import setuptools_scm._integration.toml

expected_version = sys.argv[1]
with tempfile.TemporaryDirectory() as tmp:
    root = pathlib.Path(tmp)
    subprocess.run(["git", "init"], cwd=root, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    subprocess.run(["git", "config", "user.email", "insula@example.invalid"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "Insula"], cwd=root, check=True)
    (root / "module.py").write_text("VALUE = 7\n", encoding="utf-8")
    subprocess.run(["git", "add", "module.py"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=root, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    subprocess.run(["git", "tag", "v1.2.3"], cwd=root, check=True)
    version = get_version(
        root=str(root),
        version_scheme="no-guess-dev",
        local_scheme="no-local-version",
    )

print(
    "py-setuptools-scm:%s:%s:%s"
    % (
        importlib.metadata.version("setuptools-scm"),
        version,
        os.path.basename(setuptools_scm.git.DEFAULT_DESCRIBE[0]),
    )
)
PY
)"
[[ "$out" == "py-setuptools-scm:${expected_version}:1.2.3:git" ]] || {
  echo "unexpected import output: $out" >&2
  exit 1
}

echo "$out"
