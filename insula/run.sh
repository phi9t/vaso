#!/usr/bin/env bash
# Canonical generic execution; context and execution stay with the shell owner.
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$DIR/execution/insula.sh"
insula_main "$@"
