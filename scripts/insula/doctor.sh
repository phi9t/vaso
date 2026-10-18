#!/usr/bin/env bash
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$DIR/../.." && pwd)"
export PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}"

cmd="${1:-}"
case "$cmd" in
  preflight)
    shift
    exec /usr/bin/python3 -m vaso.doctor insula "$@"
    ;;
  -h|--help)
    exec /usr/bin/python3 -m vaso.doctor --help
    ;;
  *)
    echo "usage: $0 preflight [doctor-options]" >&2
    exit 2
    ;;
esac
