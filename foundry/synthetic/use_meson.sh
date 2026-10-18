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

prefix="$(find_prefix meson_native)"
python_prefix="$(find_prefix python_313_native)"
venv_prefix="$(find_prefix python_venv_native)"
pip_prefix="$(find_prefix py_pip_native)"
setuptools_prefix="$(find_prefix py_setuptools_native)"
wheel_prefix="$(find_prefix py_wheel_native)"
ninja_prefix="$(find_prefix ninja_native)"

python_abi="$("$python_prefix/bin/python3" -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
[[ "$python_abi" == "3.13" ]] || { echo "unexpected Python ABI: $python_abi" >&2; exit 1; }

site_packages="$prefix/lib/python${python_abi}/site-packages"
[[ -x "$prefix/bin/meson" ]] || { echo "missing executable bin/meson" >&2; exit 1; }
[[ -d "$site_packages/mesonbuild" ]] || { echo "missing mesonbuild package" >&2; exit 1; }
[[ -f "$site_packages/mesonbuild/mesonmain.py" ]] || { echo "missing mesonmain" >&2; exit 1; }
[[ -f "$site_packages/mesonbuild/backend/ninjabackend.py" ]] || { echo "missing ninjabackend" >&2; exit 1; }
[[ -f "$site_packages/meson-1.11.1.dist-info/METADATA" ]] || { echo "missing meson metadata" >&2; exit 1; }
[[ -f "$prefix/share/man/man1/meson.1" ]] || { echo "missing meson manpage" >&2; exit 1; }

export LD_LIBRARY_PATH="$python_prefix/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
export PATH="$ninja_prefix/bin:$prefix/bin:$PATH"
export PYTHONHOME=
export PYTHONPATH="$site_packages:$pip_prefix/lib/python${python_abi}/site-packages:$setuptools_prefix/lib/python${python_abi}/site-packages:$wheel_prefix/lib/python${python_abi}/site-packages:$venv_prefix/lib/python${python_abi}/site-packages"

out="$("$venv_prefix/bin/python${python_abi}" - <<'PY'
import importlib.metadata
import mesonbuild.mesonmain

print(
    "meson:%s:%s"
    % (
        importlib.metadata.version("meson"),
        mesonbuild.mesonmain.__name__,
    )
)
PY
)"
[[ "$out" == "meson:1.11.1:mesonbuild.mesonmain" ]] || {
  echo "unexpected import output: $out" >&2
  exit 1
}

version="$("$prefix/bin/meson" --version)"
[[ "$version" == "1.11.1" ]] || {
  echo "unexpected meson version: $version" >&2
  exit 1
}

tmp="$(mktemp -d "${TEST_TMPDIR:-/tmp}/meson-smoke.XXXXXX")"
trap 'rm -rf "$tmp"' EXIT
cat > "$tmp/meson.build" <<'MESON'
project('vaso-meson-smoke', 'c')
executable('hello', 'hello.c')
MESON
cat > "$tmp/hello.c" <<'C'
#include <stdio.h>
int main(void) {
  puts("meson-native-smoke");
  return 0;
}
C
"$prefix/bin/meson" setup "$tmp/build" "$tmp" >/dev/null
"$prefix/bin/meson" compile -C "$tmp/build" >/dev/null
hello="$("$tmp/build/hello")"
[[ "$hello" == "meson-native-smoke" ]] || {
  echo "unexpected smoke binary output: $hello" >&2
  exit 1
}

echo "$out:$version:$hello"
