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

prefix="$(find_prefix py_python_dateutil_native)"
python_prefix="$(find_prefix python_native)"
venv_prefix="$(find_prefix python_venv_native)"
pip_prefix="$(find_prefix py_pip_native)"
setuptools_prefix="$(find_prefix py_setuptools_native)"
setuptools_scm_prefix="$(find_prefix py_setuptools_scm_native)"
six_prefix="$(find_prefix py_six_native)"
wheel_prefix="$(find_prefix py_wheel_native)"

for name in prefix python_prefix venv_prefix pip_prefix setuptools_prefix setuptools_scm_prefix six_prefix wheel_prefix; do
  value="${!name:-}"
  [[ -n "$value" && -d "$value" ]] || { echo "missing prefix discovery for $name" >&2; exit 1; }
done

site_packages="$prefix/lib/python3.14/site-packages"
[[ -f "$site_packages/dateutil/__init__.py" ]] || { echo "missing dateutil package" >&2; exit 1; }
[[ -f "$site_packages/dateutil/parser/isoparser.py" ]] || { echo "missing dateutil parser" >&2; exit 1; }
[[ -f "$site_packages/dateutil/relativedelta.py" ]] || { echo "missing dateutil relativedelta" >&2; exit 1; }
[[ -f "$site_packages/dateutil/tz/tz.py" ]] || { echo "missing dateutil tz" >&2; exit 1; }
[[ -f "$site_packages/python_dateutil-2.9.0.post0.dist-info/METADATA" ]] || { echo "missing dateutil metadata" >&2; exit 1; }
[[ -f "$site_packages/python_dateutil-2.9.0.post0.dist-info/WHEEL" ]] || { echo "missing dateutil wheel metadata" >&2; exit 1; }

export LD_LIBRARY_PATH="$python_prefix/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
export PYTHONPATH="$site_packages:$six_prefix/lib/python3.14/site-packages:$pip_prefix/lib/python3.14/site-packages:$setuptools_prefix/lib/python3.14/site-packages:$setuptools_scm_prefix/lib/python3.14/site-packages:$wheel_prefix/lib/python3.14/site-packages:$venv_prefix/lib/python3.14/site-packages"
out="$("$venv_prefix/bin/python3" - <<'PY'
import importlib.metadata

from dateutil import parser, relativedelta, tz

dt = parser.isoparse("2026-09-26T20:40:13+00:00")
later = dt + relativedelta.relativedelta(months=+1, days=+2)
zone = tz.gettz("UTC")
print("py-python-dateutil:%s:%s:%s:%02d" % (
    importlib.metadata.version("python-dateutil"),
    dt.tzinfo.tzname(dt),
    zone.tzname(dt),
    later.day,
))
PY
)"
case "$out" in
  py-python-dateutil:2.9.0.post0:UTC:UTC:28) ;;
  *)
    echo "unexpected dateutil output: $out" >&2
    exit 1
    ;;
esac

echo "$out"
