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

prefix="$(find_prefix py_protobuf_native)"
python_prefix="$(find_prefix python_313_native)"
venv_prefix="$(find_prefix python_venv_native)"
pip_prefix="$(find_prefix py_pip_native)"
setuptools_prefix="$(find_prefix py_setuptools_native)"
wheel_prefix="$(find_prefix py_wheel_native)"
protobuf_prefix="$(find_prefix protobuf_native)"

for name in prefix python_prefix venv_prefix pip_prefix setuptools_prefix wheel_prefix protobuf_prefix; do
  value="${!name:-}"
  [[ -n "$value" && -d "$value" ]] || { echo "missing prefix discovery for $name" >&2; exit 1; }
done

python_abi="$("$python_prefix/bin/python3" -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
python_ext_suffix="$("$python_prefix/bin/python3" -c 'import sysconfig; print(sysconfig.get_config_var("EXT_SUFFIX") or "")')"
protoc_version="$("$protobuf_prefix/bin/protoc" --version)"

[[ "$python_abi" == "3.13" ]] || { echo "unexpected Python ABI: $python_abi" >&2; exit 1; }
[[ "$python_ext_suffix" == *"cpython-313"* ]] || { echo "unexpected extension suffix: $python_ext_suffix" >&2; exit 1; }
[[ "$protoc_version" == "libprotoc 3.21.12" ]] || { echo "unexpected protoc version: $protoc_version" >&2; exit 1; }

site_packages="$prefix/lib/python${python_abi}/site-packages"
[[ -f "$site_packages/google/protobuf/__init__.py" ]] || { echo "missing google.protobuf" >&2; exit 1; }
[[ -f "$site_packages/google/protobuf/struct_pb2.py" ]] || { echo "missing struct_pb2" >&2; exit 1; }
[[ -f "$site_packages/google/protobuf/pyext/_message${python_ext_suffix}" ]] || { echo "missing pyext extension" >&2; exit 1; }
[[ -f "$site_packages/google/protobuf/internal/_api_implementation${python_ext_suffix}" ]] || { echo "missing api implementation extension" >&2; exit 1; }
[[ -f "$site_packages/protobuf-4.21.12.dist-info/METADATA" ]] || { echo "missing protobuf metadata" >&2; exit 1; }
[[ -f "$site_packages/protobuf-4.21.12.dist-info/WHEEL" ]] || { echo "missing protobuf wheel metadata" >&2; exit 1; }

export LD_LIBRARY_PATH="$python_prefix/lib:$protobuf_prefix/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
export PYTHONPATH="$site_packages:$pip_prefix/lib/python${python_abi}/site-packages:$setuptools_prefix/lib/python${python_abi}/site-packages:$wheel_prefix/lib/python${python_abi}/site-packages:$venv_prefix/lib/python${python_abi}/site-packages"
export PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION=cpp
out="$("$venv_prefix/bin/python${python_abi}" - <<'PY'
import importlib.metadata

from google.protobuf import json_format, struct_pb2
from google.protobuf.internal import api_implementation

msg = struct_pb2.Struct()
msg.update({"answer": 7, "ok": True})
round_trip = struct_pb2.Struct()
json_format.Parse(json_format.MessageToJson(msg, sort_keys=True), round_trip)
print("py-protobuf:%s:%s:%d" % (
    importlib.metadata.version("protobuf"),
    api_implementation.Type(),
    int(round_trip["answer"]),
))
PY
)"
case "$out" in
  py-protobuf:4.21.12:cpp:7) ;;
  *)
    echo "unexpected protobuf output: $out" >&2
    exit 1
    ;;
esac

echo "$out"
