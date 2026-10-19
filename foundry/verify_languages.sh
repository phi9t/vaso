#!/usr/bin/env bash
# Exercise compiler and runtime smoke programs using the prepared rootfs only.
set -euo pipefail
EXP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$EXP_DIR/.." && pwd)"
INSULA_SH="$REPO_ROOT/foundry/insula.sh"
. "$INSULA_SH"
: "${VASO_ESTATE_ROOT:?set VASO_ESTATE_ROOT}"
: "${VASO_CUDA_LINE:?set VASO_CUDA_LINE to cu129 or cu130}"
INSULA_REQUIRE_DRIVER=1
insula_prepare_experiment_context "$EXP_DIR" "$VASO_CUDA_LINE"
python3 "$REPO_ROOT/insula/rootfs/foundry_tools.py" \
  --rootfs "$ESTATE_ROOT/rootfs-lines/$VASO_CUDA_LINE/rootfs" --verify >/dev/null
INSULA_UNSHARE_NET=1
gpu_lease=""
cleanup_languages() {
  [[ -z "$gpu_lease" ]] || insula_release_lease "$gpu_lease"
  cleanup_insula_io
}
trap cleanup_languages EXIT
insula_acquire_lease gpu_lease gpu --amount 1
insula_export_lease_env "$INSULA_LAST_LEASE_JSON"
_insula_append_setenv INSULA_ENV_ARGS CUDA_VISIBLE_DEVICES "$CUDA_VISIBLE_DEVICES"
_insula_append_setenv INSULA_ENV_ARGS VASO_GPU_SET "$VASO_GPU_SET"
insula /bin/bash -c '
set -euo pipefail
for tool in g++ python3 rustc nvcc; do
  command -v "$tool" >/dev/null || { echo "missing rootfs language tool: $tool" >&2; exit 2; }
done
printf "{\"lang\":\"bash\",\"sum\":%s,\"ok\":true}\n" "$((20+22))"
g++ -O2 -o "$TMPDIR/hello_cpp" lang/cpp/hello.cc
"$TMPDIR/hello_cpp"
python3 lang/python/hello.py
rustc -O -o "$TMPDIR/hello_rust" lang/rust/hello.rs
"$TMPDIR/hello_rust"
nvcc -arch=native -o "$TMPDIR/hello_cuda" lang/cuda/hello.cu
"$TMPDIR/hello_cuda"
'
