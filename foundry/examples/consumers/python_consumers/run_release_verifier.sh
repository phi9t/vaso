#!/usr/bin/env bash
set -euo pipefail

profile="${1:?usage: run_release_verifier.sh <torch|jax>}"
case "$profile" in
  torch|jax) ;;
  *) echo "unknown Python consumer profile: $profile" >&2; exit 2 ;;
esac

: "${VASO_RELEASE_VENV:?run inside foundry/insula.sh run --release}"
export PYTHONPATH="/workspace/experiment/examples/consumers${PYTHONPATH:+:$PYTHONPATH}"
exec python3 -m "python_consumers.verify_$profile"
