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

runfile() {
  local rel="$1"
  find_runfile_by_pattern "*/${rel}" || find_runfile_by_pattern "$rel"
}

protobuf_prefix="$(find_prefix protobuf_native)"
py_protobuf_prefix="$(find_prefix py_protobuf_native)"
python_prefix="$(find_prefix python_313_native)"
venv_prefix="$(find_prefix python_venv_native)"
pip_prefix="$(find_prefix py_pip_native)"
setuptools_prefix="$(find_prefix py_setuptools_native)"
wheel_prefix="$(find_prefix py_wheel_native)"
proto="$(runfile "synthetic/protobuf_roundtrip/roundtrip.proto")"
writer_bin="$(runfile "synthetic/protobuf_roundtrip_write_message")"
reader_src="$(runfile "synthetic/protobuf_roundtrip/read_message.py")"

for name in protobuf_prefix py_protobuf_prefix python_prefix venv_prefix pip_prefix setuptools_prefix wheel_prefix proto writer_bin reader_src; do
  value="${!name:-}"
  [[ -n "$value" && ( -e "$value" || -L "$value" ) ]] || { echo "missing runfile/prefix for $name" >&2; exit 1; }
done

python_abi="$("$python_prefix/bin/python3" -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
protoc_version="$("$protobuf_prefix/bin/protoc" --version)"
[[ "$python_abi" == "3.13" ]] || { echo "unexpected Python ABI: $python_abi" >&2; exit 1; }
[[ "$protoc_version" == "libprotoc 3.21.12" ]] || { echo "unexpected protoc version: $protoc_version" >&2; exit 1; }

work="${TEST_TMPDIR:-${TMPDIR:-/tmp}}/protobuf-roundtrip.$$"
rm -rf "$work"
mkdir -p "$work"
trap 'rm -rf "$work"' EXIT

"$protobuf_prefix/bin/protoc" \
  --proto_path="$(dirname "$proto")" \
  --descriptor_set_out="$work/roundtrip.desc" \
  --include_imports \
  --python_out="$work" \
  "$proto"

message="$work/message.pb"
LD_LIBRARY_PATH="$protobuf_prefix/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}" "$writer_bin" "$work/roundtrip.desc" "$message"

export LD_LIBRARY_PATH="$python_prefix/lib:$protobuf_prefix/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
export PYTHONPATH="$work:$py_protobuf_prefix/lib/python${python_abi}/site-packages:$pip_prefix/lib/python${python_abi}/site-packages:$setuptools_prefix/lib/python${python_abi}/site-packages:$wheel_prefix/lib/python${python_abi}/site-packages:$venv_prefix/lib/python${python_abi}/site-packages"
export PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION=cpp
out="$("$venv_prefix/bin/python${python_abi}" "$reader_src" "$message")"
[[ "$out" == "protobuf-roundtrip:3.21.12:4.21.12:ok" ]] || {
  echo "unexpected roundtrip output: $out" >&2
  exit 1
}

echo "$out"
