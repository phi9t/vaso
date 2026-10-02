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

prefix="$(find_prefix py_urllib3_native)"
python_prefix="$(find_prefix python_313_native)"
venv_prefix="$(find_prefix python_venv_native)"

for name in prefix python_prefix venv_prefix; do
  value="${!name:-}"
  [[ -n "$value" && -d "$value" ]] || { echo "missing prefix discovery for $name" >&2; exit 1; }
done

python_abi="$("$python_prefix/bin/python3" -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
[[ "$python_abi" == "3.13" ]] || { echo "unexpected Python ABI: $python_abi" >&2; exit 1; }

site_packages="$prefix/lib/python${python_abi}/site-packages"
[[ -f "$site_packages/urllib3/__init__.py" ]] || { echo "missing urllib3 package" >&2; exit 1; }
[[ -f "$site_packages/urllib3/_version.py" ]] || { echo "missing urllib3 version module" >&2; exit 1; }
[[ -f "$site_packages/urllib3/poolmanager.py" ]] || { echo "missing urllib3 poolmanager module" >&2; exit 1; }
[[ -f "$site_packages/urllib3/util/retry.py" ]] || { echo "missing urllib3 retry module" >&2; exit 1; }
[[ -f "$site_packages/urllib3-2.6.3.dist-info/METADATA" ]] || { echo "missing urllib3 metadata" >&2; exit 1; }
[[ -f "$site_packages/urllib3-2.6.3.dist-info/WHEEL" ]] || { echo "missing urllib3 wheel metadata" >&2; exit 1; }
[[ -f "$site_packages/urllib3-2.6.3.dist-info/licenses/LICENSE.txt" ]] || { echo "missing urllib3 license" >&2; exit 1; }

export LD_LIBRARY_PATH="$python_prefix/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
export PYTHONPATH="$site_packages:$venv_prefix/lib/python${python_abi}/site-packages"
out="$("$venv_prefix/bin/python${python_abi}" - <<'PY'
import importlib.metadata

import urllib3
from urllib3.util import Retry, Timeout, parse_url

retry = Retry(total=3, redirect=1)
timeout = Timeout(connect=1.0, read=2.0)
url = parse_url("https://example.com:443/path?q=1")
print(
    "py-urllib3:%s:%s:%s:%s:%s"
    % (
        importlib.metadata.version("urllib3"),
        urllib3.__version__,
        retry.total,
        timeout.connect_timeout,
        url.host,
    )
)
PY
)"
[[ "$out" == "py-urllib3:2.6.3:2.6.3:3:1.0:example.com" ]] || {
  echo "unexpected urllib3 output: $out" >&2
  exit 1
}

echo "$out"
