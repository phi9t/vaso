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

requests_prefix="$(find_prefix py_requests_native)"
certifi_prefix="$(find_prefix py_certifi_native)"
charset_prefix="$(find_prefix py_charset_normalizer_native)"
idna_prefix="$(find_prefix py_idna_native)"
python_prefix="$(find_prefix python_313_native)"
urllib3_prefix="$(find_prefix py_urllib3_native)"
venv_prefix="$(find_prefix python_venv_native)"

for name in requests_prefix certifi_prefix charset_prefix idna_prefix python_prefix urllib3_prefix venv_prefix; do
  value="${!name:-}"
  [[ -n "$value" && -d "$value" ]] || { echo "missing prefix discovery for $name" >&2; exit 1; }
done

python_abi="$("$python_prefix/bin/python3" -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
[[ "$python_abi" == "3.13" ]] || { echo "unexpected Python ABI: $python_abi" >&2; exit 1; }

site_packages="$requests_prefix/lib/python${python_abi}/site-packages"
[[ -f "$site_packages/requests/__init__.py" ]] || { echo "missing requests package" >&2; exit 1; }
[[ -f "$site_packages/requests/api.py" ]] || { echo "missing requests api" >&2; exit 1; }
[[ -f "$site_packages/requests/models.py" ]] || { echo "missing requests models" >&2; exit 1; }
[[ -f "$site_packages/requests/sessions.py" ]] || { echo "missing requests sessions" >&2; exit 1; }
[[ -f "$site_packages/requests-2.33.1.dist-info/METADATA" ]] || { echo "missing requests metadata" >&2; exit 1; }
[[ -f "$site_packages/requests-2.33.1.dist-info/WHEEL" ]] || { echo "missing requests wheel metadata" >&2; exit 1; }
[[ -f "$site_packages/requests-2.33.1.dist-info/licenses/LICENSE" ]] || { echo "missing requests license" >&2; exit 1; }
[[ -f "$site_packages/requests-2.33.1.dist-info/licenses/NOTICE" ]] || { echo "missing requests notice" >&2; exit 1; }

export LD_LIBRARY_PATH="$python_prefix/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
export PYTHONPATH="$site_packages:$certifi_prefix/lib/python${python_abi}/site-packages:$charset_prefix/lib/python${python_abi}/site-packages:$idna_prefix/lib/python${python_abi}/site-packages:$urllib3_prefix/lib/python${python_abi}/site-packages:$venv_prefix/lib/python${python_abi}/site-packages"
out="$("$venv_prefix/bin/python${python_abi}" - <<'PY'
import importlib.metadata

import requests

req = requests.Request("GET", "https://example.com/path", params={"q": "1"})
prepared = requests.Session().prepare_request(req)
print(
    "py-requests:%s:%s:%s:%s:%s"
    % (
        requests.__version__,
        importlib.metadata.version("requests"),
        prepared.method,
        prepared.url,
        requests.codes.ok,
    )
)
PY
)"
[[ "$out" == "py-requests:2.33.1:2.33.1:GET:https://example.com/path?q=1:200" ]] || {
  echo "unexpected requests output: $out" >&2
  exit 1
}

echo "$out"
