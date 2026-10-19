#!/usr/bin/env bash
# Foundry execution and release contexts over the generic insula launcher.
_FOUNDRY_INSULA_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
. "$_FOUNDRY_INSULA_ROOT/insula/execution/insula.sh"
. "$_FOUNDRY_INSULA_ROOT/foundry/tools/insula_context.sh"
if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  set -euo pipefail
  foundry_insula_main "$@"
fi
