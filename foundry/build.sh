#!/usr/bin/env bash
# Prepare pinned inputs, then build selected wheel variants inside insula.
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ "${1:-}" == "--inkling-packed-inference" ]]; then
  shift
  exec python3 -B "$DIR/native/triton/pascal/capsule/run_packed_inference.py" "$@"
fi
if [[ "${1:-}" == "--inkling-inference" ]]; then
  shift
  exec python3 -B "$DIR/native/triton/pascal/capsule/run_inference.py" "$@"
fi
if [[ "${1:-}" == "--pascal-capsule" ]]; then
  shift
  exec python3 -B "$DIR/native/triton/pascal/capsule/launch.py" "$@"
fi
if [[ "${1:-}" == "--pascal-triton" ]]; then
  shift
  exec python3 -B "$DIR/native/triton/pascal/driver.py" "$@"
fi
if [[ "${1:-}" == "--source" ]]; then
  shift
  exec "$DIR/source-build.sh" "$@"
fi
exec python3 -B "$DIR/tools/build_variants.py" "$@"
