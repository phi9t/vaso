#!/usr/bin/env bash
set -euo pipefail

# This driver is intentionally listed as an input by the bad E1 target.
exec "$@"
