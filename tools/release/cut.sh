#!/usr/bin/env bash
# Cut a production-build release from an already-green nightly.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
: "${VASO_ESTATE_ROOT:?set VASO_ESTATE_ROOT}"
exec python3 "$ROOT/tools/release/cut.py" "$@"
