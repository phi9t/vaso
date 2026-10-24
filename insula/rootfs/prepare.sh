#!/usr/bin/env bash
set -euo pipefail
ROOTFS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec python3 -B "$ROOTFS_DIR/prepare.py" "$@"
