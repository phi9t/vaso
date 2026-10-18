#!/usr/bin/env bash
# Compatibility entrypoint for existing Foundry/release callers.
_INSULA_COMPAT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
. "$_INSULA_COMPAT_ROOT/foundry/insula.sh"
if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  set -euo pipefail
  foundry_insula_main "$@"
fi
