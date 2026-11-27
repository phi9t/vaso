#!/usr/bin/env bash
# Prepare pinned inputs, then build selected wheel variants inside insula.
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Canonical options may appear in any order before a payload separator.
for arg in "$@"; do
  case "$arg" in
    --) break ;;
    --target|--target=*|--list-targets)
      exec uv run --offline --no-project --no-python-downloads --no-config -- python -B "$DIR/tools/build_contract.py" "$@"
      ;;
  esac
done
if [[ "${1:-}" == "--inkling-packed-inference" ]]; then
  shift
  exec uv run --offline --no-project --no-python-downloads --no-config -- python -B "$DIR/native/triton/pascal/capsule/run_packed_inference.py" "$@"
fi
if [[ "${1:-}" == "--inkling-inference" ]]; then
  shift
  exec uv run --offline --no-project --no-python-downloads --no-config -- python -B "$DIR/native/triton/pascal/capsule/run_inference.py" "$@"
fi
if [[ "${1:-}" == "--pascal-capsule" ]]; then
  shift
  exec uv run --offline --no-project --no-python-downloads --no-config -- python -B "$DIR/native/triton/pascal/capsule/launch.py" "$@"
fi
if [[ "${1:-}" == "--pascal-triton" ]]; then
  shift
  exec uv run --offline --no-project --no-python-downloads --no-config -- python -B "$DIR/native/triton/pascal/driver.py" "$@"
fi
if [[ "${1:-}" == "--source" ]]; then
  shift
  exec "$DIR/source-build.sh" "$@"
fi
exec uv run --offline --no-project --no-python-downloads --no-config -- python -B "$DIR/tools/build_variants.py" "$@"
