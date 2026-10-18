#!/usr/bin/env bash
# Verify a prepared line with an offline Bazel action and persistent owned state.
set -euo pipefail
EXP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$EXP_DIR/.." && pwd)"
INSULA_SH="$REPO_ROOT/foundry/insula.sh"
: "${VASO_ESTATE_ROOT:?set VASO_ESTATE_ROOT}"
: "${VASO_CUDA_LINE:?set VASO_CUDA_LINE to cu129 or cu130}"
if [[ "$#" != 0 ]]; then
  echo 'usage: bootstrap_insula.sh (use a fresh owned estate for a clean bootstrap)' >&2
  exit 2
fi
exec bash "$INSULA_SH" --line "$VASO_CUDA_LINE" -- \
  /opt/vaso/bin/bazel-real --output_base=/vaso-bazel-ob --batch build \
  --repository_disable_download --lockfile_mode=off \
  --registry=file:///vaso/sources/bcr/registry/current \
  --repository_cache=/vaso/cache/bazel/repository-cache \
  --distdir=/opt/vaso/distdir --symlink_prefix=/vaso/bootstrap-links/ \
  --disk_cache="/vaso/lines/$VASO_CUDA_LINE/cache/bazel/disk-cache" \
  //bootstrap:touchstone
