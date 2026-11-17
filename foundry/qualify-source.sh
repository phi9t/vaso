#!/usr/bin/env bash
# Verify the produced native wheel before accepting it as a runtime.
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec python3 -B "$DIR/native/pytorch215/qualification.py" "$@"
