#!/usr/bin/env bash
# Prepare pinned inputs, then build selected wheel variants inside insula.
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec python3 -B "$DIR/tools/build_variants.py" "$@"
