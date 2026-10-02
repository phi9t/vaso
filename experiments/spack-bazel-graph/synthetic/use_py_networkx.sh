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

prefix="$(find_prefix py_networkx_native)"
python_prefix="$(find_prefix python_313_native)"
venv_prefix="$(find_prefix python_venv_native)"

for name in prefix python_prefix venv_prefix; do
  value="${!name:-}"
  [[ -n "$value" && -d "$value" ]] || { echo "missing prefix discovery for $name" >&2; exit 1; }
done

python_abi="$("$python_prefix/bin/python3" -c 'import sys; print("%d.%d" % sys.version_info[:2])')"

site_packages="$prefix/lib/python${python_abi}/site-packages"
[[ -f "$site_packages/networkx/__init__.py" ]] || { echo "missing networkx package" >&2; exit 1; }
[[ -f "$site_packages/networkx/classes/graph.py" ]] || { echo "missing networkx graph module" >&2; exit 1; }
[[ -f "$site_packages/networkx/classes/digraph.py" ]] || { echo "missing networkx digraph module" >&2; exit 1; }
[[ -f "$site_packages/networkx/convert.py" ]] || { echo "missing networkx convert module" >&2; exit 1; }
[[ -f "$site_packages/networkx/readwrite/json_graph/node_link.py" ]] || { echo "missing networkx json graph module" >&2; exit 1; }
[[ -f "$site_packages/networkx-3.6.1.dist-info/METADATA" ]] || { echo "missing networkx metadata" >&2; exit 1; }
[[ -f "$site_packages/networkx-3.6.1.dist-info/WHEEL" ]] || { echo "missing networkx wheel metadata" >&2; exit 1; }
[[ -f "$site_packages/networkx-3.6.1.dist-info/licenses/LICENSE.txt" ]] || { echo "missing networkx license" >&2; exit 1; }

export LD_LIBRARY_PATH="$python_prefix/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
export PYTHONPATH="$site_packages:$venv_prefix/lib/python${python_abi}/site-packages"
out="$("$venv_prefix/bin/python${python_abi}" - <<'PY'
import importlib.metadata
import networkx as nx

graph = nx.path_graph(4)
path = "-".join(str(node) for node in nx.shortest_path(graph, 0, 3))
degree_sum = sum(dict(graph.degree()).values())
print(
    "py-networkx:%s:%s:%s:%s:%s"
    % (
        importlib.metadata.version("networkx"),
        nx.__version__,
        graph.number_of_nodes(),
        degree_sum,
        path,
    )
)
PY
)"
[[ "$out" == "py-networkx:3.6.1:3.6.1:4:6:0-1-2-3" ]] || {
  echo "unexpected networkx output: $out" >&2
  exit 1
}

echo "$out"
