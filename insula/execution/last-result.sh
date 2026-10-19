#!/usr/bin/env bash
set -euo pipefail

AGENT="${VASO_AGENT:-trae}"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --agent)
      AGENT="${2:?}"
      shift 2
      ;;
    -h|--help)
      echo "usage: VASO_ESTATE_ROOT=<root> $0 [--agent AGENT]"
      exit 0
      ;;
    *)
      echo "unknown argument: $1" >&2
      exit 2
      ;;
  esac
done

: "${VASO_ESTATE_ROOT:?set VASO_ESTATE_ROOT}"
RESULT_DIR="$VASO_ESTATE_ROOT/agents/$AGENT/results"
if [[ ! -d "$RESULT_DIR" ]]; then
  echo "no results for agent $AGENT" >&2
  exit 1
fi

latest="$(find "$RESULT_DIR" -maxdepth 1 -type f -name '*.json' -printf '%T@ %p\n' | sort -n | tail -n 1 | sed 's/^[^ ]* //')"
if [[ -z "$latest" ]]; then
  echo "no results for agent $AGENT" >&2
  exit 1
fi
cat "$latest"
