#!/bin/bash
set -euo pipefail
if (( $# > 1 )) || [[ $# == 1 && $1 != --prepare-only ]]; then
  echo "Usage: build-triton [--prepare-only]" >&2
  exit 2
fi
P=/opt/vaso/pascal
CUDA_TOOLS="$P/cuda-tools/12.6.77/bin"
export PATH="$CUDA_TOOLS:$P/build-python/bin:/opt/vaso/cmake/bin:/opt/vaso/python-deps/bin:/opt/vaso/cuda/bin:/usr/local/bin:/usr/bin:/bin"
export PYTHONPATH="$P/build-python:/opt/vaso/python-deps"
export LD_LIBRARY_PATH=/opt/vaso/cuda/lib64:/usr/local/lib
export CUDA_HOME=/opt/vaso/cuda CC=gcc CXX=g++ TRITON_OFFLINE_BUILD=1 TRITON_BUILD_PROTON=OFF PIP_NO_INDEX=1 PYTHONNOUSERSITE=1
export LLVM_SYSPATH="$P/llvm" JSON_SYSPATH="$P/json"
export TRITON_PTXAS_PATH=/opt/vaso/cuda/bin/ptxas TRITON_PTXAS_BLACKWELL_PATH="$P/vendor/bin/ptxas-blackwell"
export TRITON_CUOBJDUMP_PATH="$CUDA_TOOLS/cuobjdump" TRITON_NVDISASM_PATH="$CUDA_TOOLS/nvdisasm"
export TRITON_CUDACRT_PATH="$CUDA_HOME/include" TRITON_CUDART_PATH="$CUDA_HOME/include" TRITON_CUPTI_INCLUDE_PATH="$CUDA_HOME/include" TRITON_CUPTI_LIB_PATH="$CUDA_HOME/lib64"
export TRITON_LIBDEVICE_PATH="$CUDA_HOME/nvvm/libdevice/libdevice.10.bc"
export C_INCLUDE_PATH="$CUDA_HOME/include" CPLUS_INCLUDE_PATH="$CUDA_HOME/include"
export TRITON_APPEND_CMAKE_ARGS='-DTRITON_BUILD_UT=OFF -DTRITON_BUILD_PROTON=OFF -DZLIB_LIBRARY=/usr/lib/x86_64-linux-gnu/libz.so -DZLIB_INCLUDE_DIR=/usr/include'
export MAX_JOBS="${MAX_JOBS:-2}"
export CMAKE_BUILD_PARALLEL_LEVEL="$MAX_JOBS"
export TRITON_HOME=/workspace/triton-home TMPDIR=/workspace/build-tmp TRITON_CACHE_DIR=/workspace/triton-cache
mkdir -p "$TRITON_HOME" "$TMPDIR" "$TRITON_CACHE_DIR" /workspace/wheelhouse
source_name=triton-675c59878aa2280b31f722aaf42b825fcee21de8
archive="$P/$source_name.tar.gz"
patch_file="$P/sm61-signed-int8-dot.patch"
# Serialize preparation and building in this Estate. Keep historical, unmarked
# source trees untouched; a directory alone is not a completion receipt.
exec 9>/workspace/.triton-build.lock
flock -x 9
identity=$(sha256sum "$archive" "$patch_file" | awk '{print $1}' | sha256sum | awk '{print $1}')
src="/workspace/$source_name.prepared-$identity"
marker="$src/.vaso-prepared"
if [[ ! -e "$src" ]]; then
  stage=$(mktemp -d /workspace/.triton-prepare.XXXXXXXX)
  trap 'rm -rf -- "$stage"' EXIT
  tar -xzf "$archive" -C "$stage"
  patch -d "$stage/$source_name" -p1 --batch --forward --fuzz=0 -i "$patch_file"
  # A successful reverse dry-run confirms the required patch is present.
  patch -d "$stage/$source_name" -p1 --force --reverse --dry-run --fuzz=0 -i "$patch_file"
  printf '%s\n' "$identity" > "$stage/$source_name/.vaso-prepared"
  mv -T -- "$stage/$source_name" "$src"
  rmdir -- "$stage"
  trap - EXIT
fi
if [[ ! -d "$src" || -L "$src" || ! -f "$marker" || $(cat "$marker") != "$identity" ]]; then
  echo "Prepared Triton source identity missing or invalid; refusing to build" >&2
  exit 1
fi
patch -d "$src" -p1 --force --reverse --dry-run --fuzz=0 -i "$patch_file"
if [[ ${1:-} == --prepare-only && $# == 1 ]]; then
  printf "Prepared Triton source: %s\n" "$src"
  exit 0
fi
cd "$src"
exec "$P/bin/uv" build --python /usr/local/bin/python3 --no-python-downloads \
  --no-build-isolation --offline --no-index --no-config --no-cache \
  --wheel --out-dir /workspace/wheelhouse .
