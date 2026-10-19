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

prefix="$(find_prefix py_beniget_native)"
python_prefix="$(find_prefix python_313_native)"
venv_prefix="$(find_prefix python_venv_native)"
pip_prefix="$(find_prefix py_pip_native)"
setuptools_prefix="$(find_prefix py_setuptools_native)"
wheel_prefix="$(find_prefix py_wheel_native)"
gast_prefix="$(find_prefix py_gast_native)"
python_abi="$("$python_prefix/bin/python3" -c 'import sys; print("%d.%d" % sys.version_info[:2])')"

[[ "$python_abi" == "3.13" ]] || { echo "unexpected Python ABI: $python_abi" >&2; exit 1; }
site_packages="$prefix/lib/python${python_abi}/site-packages"
[[ -d "$site_packages/beniget" ]] || { echo "missing beniget package" >&2; exit 1; }
[[ -f "$site_packages/beniget/__init__.py" ]] || { echo "missing beniget __init__" >&2; exit 1; }
[[ -f "$site_packages/beniget/__main__.py" ]] || { echo "missing beniget __main__" >&2; exit 1; }
[[ -f "$site_packages/beniget/beniget.py" ]] || { echo "missing beniget core module" >&2; exit 1; }
[[ -f "$site_packages/beniget/ordered_set.py" ]] || { echo "missing beniget ordered_set" >&2; exit 1; }
[[ -f "$site_packages/beniget/version.py" ]] || { echo "missing beniget version" >&2; exit 1; }
[[ -f "$site_packages/beniget-0.4.2.post1.dist-info/METADATA" ]] || { echo "missing beniget metadata" >&2; exit 1; }

export LD_LIBRARY_PATH="$python_prefix/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
export PYTHONPATH="$site_packages:$gast_prefix/lib/python${python_abi}/site-packages:$pip_prefix/lib/python${python_abi}/site-packages:$setuptools_prefix/lib/python${python_abi}/site-packages:$wheel_prefix/lib/python${python_abi}/site-packages:$venv_prefix/lib/python${python_abi}/site-packages"
out="$("$venv_prefix/bin/python${python_abi}" - <<'PY'
import importlib.metadata

import gast
from beniget import Ancestors, DefUseChains

module = gast.parse("def f(x):\n    y = x + 1\n    return y\n")
ancestors = Ancestors()
ancestors.visit(module)
return_node = module.body[0].body[1]
chain = DefUseChains("sample.py")
chain.visit(module)
parents = ">".join(type(node).__name__ for node in ancestors.parents(return_node))
local_names = ",".join(sorted(definition.name() for definition in chain.locals[module.body[0]]))
print(
    "py-beniget:%s:%s:%s"
    % (
        importlib.metadata.version("beniget"),
        parents,
        local_names,
    )
)
PY
)"
[[ "$out" == "py-beniget:0.4.2.post1:Module>FunctionDef:x,y" ]] || {
  echo "unexpected import output: $out" >&2
  exit 1
}

echo "$out"
