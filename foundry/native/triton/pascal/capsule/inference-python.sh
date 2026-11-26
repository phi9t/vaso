#!/bin/bash
set -euo pipefail
P=/opt/vaso/pascal
CUDA_TOOLS="$P/cuda-tools/12.6.77/bin"
export PYTHONPATH="/workspace/inputs:$P/qualification:$P/python:$P/reference-python:/opt/vaso/python-deps"
export PATH="$CUDA_TOOLS:$P/bin:/opt/vaso/cuda/bin:/opt/vaso/cmake/bin:/opt/vaso/python-deps/bin:/usr/local/bin:/usr/bin:/bin"
export LD_LIBRARY_PATH="/run/insula/nvidia:/opt/vaso/cuda/lib64:/usr/local/lib"
export TRITON_CUOBJDUMP_PATH="$CUDA_TOOLS/cuobjdump" TRITON_NVDISASM_PATH="$CUDA_TOOLS/nvdisasm"
export CUDA_HOME=/opt/vaso/cuda TRITON_PTXAS_PATH=/opt/vaso/cuda/bin/ptxas TRITON_LIBDEVICE_PATH=/opt/vaso/cuda/nvvm/libdevice/libdevice.10.bc TRITON_LIBCUDA_PATH=/run/insula/nvidia
export C_INCLUDE_PATH=/opt/vaso/cuda/include CPLUS_INCLUDE_PATH=/opt/vaso/cuda/include CC=gcc CXX=g++
export TRITON_CACHE_DIR=/workspace/triton-cache PYTHONDONTWRITEBYTECODE=1 PYTHONNOUSERSITE=1 OMP_NUM_THREADS=1 MKL_NUM_THREADS=1
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 TOKENIZERS_PARALLELISM=false
mkdir -p /workspace/receipts /workspace/triton-cache
exec /usr/local/bin/python3 "$@"
