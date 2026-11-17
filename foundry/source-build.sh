#!/usr/bin/env bash
# Build a pinned PyTorch source profile with the selected Insula rootfs.
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec python3 -B "$DIR/native/pytorch215/source_build.py" "$@"
