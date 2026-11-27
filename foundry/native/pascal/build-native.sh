#!/bin/bash
set -euo pipefail
export PYTHONDONTWRITEBYTECODE=1 PYTHONNOUSERSITE=1
# Insula need not have a passwd entry for the mapped host UID. Bazel requires a name.
export USER=vaso
export UV_CACHE_DIR=/workspace/native-build/uv-cache TMPDIR=/workspace/native-build/tmp
mkdir -p "$UV_CACHE_DIR" "$TMPDIR"
exec /opt/vaso/pascal/bin/uv run --offline --no-project --no-python-downloads \
  --no-config --python /usr/local/bin/python3 -- python -B \
  /opt/vaso/pascal/foundry/build_native.py "$@"
