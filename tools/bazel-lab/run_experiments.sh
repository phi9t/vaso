#!/usr/bin/env bash
set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
if [[ "${VASO_IN_INSULA:-0}" != 1 ]]; then
  : "${VASO_CUDA_LINE:?set VASO_CUDA_LINE to cu129 or cu130}"
  REPO_ROOT=$(cd "$ROOT/../.." && pwd)
  exec bash "$REPO_ROOT/foundry/insula.sh" --line "$VASO_CUDA_LINE" -- \
    /bin/bash /workspace/repo/tools/bazel-lab/run_experiments.sh "$@"
fi
exec /usr/bin/python3 "$ROOT/tools/lab_runner.py" "$@"
