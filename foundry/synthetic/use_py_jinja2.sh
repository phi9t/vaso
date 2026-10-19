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

prefix="$(find_prefix py_jinja2_native)"
python_prefix="$(find_prefix python_313_native)"
venv_prefix="$(find_prefix python_venv_native)"
pip_prefix="$(find_prefix py_pip_native)"
flit_core_prefix="$(find_prefix py_flit_core_native)"
markupsafe_prefix="$(find_prefix py_markupsafe_native)"
wheel_prefix="$(find_prefix py_wheel_native)"
python_abi="$("$python_prefix/bin/python3" -c 'import sys; print("%d.%d" % sys.version_info[:2])')"

[[ "$python_abi" == "3.13" ]] || { echo "unexpected Python ABI: $python_abi" >&2; exit 1; }
site_packages="$prefix/lib/python${python_abi}/site-packages"
[[ -d "$site_packages/jinja2" ]] || { echo "missing jinja2 package" >&2; exit 1; }
[[ -f "$site_packages/jinja2/__init__.py" ]] || { echo "missing jinja2 __init__" >&2; exit 1; }
[[ -f "$site_packages/jinja2/environment.py" ]] || { echo "missing jinja2 environment" >&2; exit 1; }
[[ -f "$site_packages/jinja2/loaders.py" ]] || { echo "missing jinja2 loaders" >&2; exit 1; }
[[ -f "$site_packages/jinja2/sandbox.py" ]] || { echo "missing jinja2 sandbox" >&2; exit 1; }
[[ -f "$site_packages/jinja2-3.1.6.dist-info/METADATA" ]] || { echo "missing jinja2 metadata" >&2; exit 1; }
[[ -f "$site_packages/jinja2-3.1.6.dist-info/entry_points.txt" ]] || { echo "missing jinja2 entry points" >&2; exit 1; }

export LD_LIBRARY_PATH="$python_prefix/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
export PYTHONPATH="$site_packages:$markupsafe_prefix/lib/python${python_abi}/site-packages:$pip_prefix/lib/python${python_abi}/site-packages:$flit_core_prefix/lib/python${python_abi}/site-packages:$wheel_prefix/lib/python${python_abi}/site-packages:$venv_prefix/lib/python${python_abi}/site-packages"
out="$("$venv_prefix/bin/python${python_abi}" - <<'PY'
import importlib.metadata

from jinja2 import DictLoader, Environment, select_autoescape
from jinja2.sandbox import SandboxedEnvironment

env = Environment(
    loader=DictLoader({"hello.html": "Hello {{ name }}: {{ body }}"}),
    autoescape=select_autoescape(["html"]),
)
rendered = env.get_template("hello.html").render(
    name="World",
    body='<strong>"safe"</strong>',
)
sandboxed = SandboxedEnvironment().from_string("{{ [1, 2, 3]|sum }}").render()
print(
    "py-jinja2:%s:%s:%s"
    % (
        importlib.metadata.version("jinja2"),
        rendered,
        sandboxed,
    )
)
PY
)"
[[ "$out" == "py-jinja2:3.1.6:Hello World: &lt;strong&gt;&#34;safe&#34;&lt;/strong&gt;:6" ]] || {
  echo "unexpected import output: $out" >&2
  exit 1
}

echo "$out"
