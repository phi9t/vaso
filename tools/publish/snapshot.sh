#!/usr/bin/env bash
# Compatibility wrapper for the official sanitized snapshot publisher.
set -euo pipefail
script_dir=$(cd "$(dirname "$0")" && pwd)
exec python3 "$script_dir/publish_snapshot.py" "$@"
