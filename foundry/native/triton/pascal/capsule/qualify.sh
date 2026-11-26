#!/bin/bash
set -euo pipefail
P=/opt/vaso/pascal
CUDA_TOOLS="$P/cuda-tools/12.6.77/bin"
export PYTHONPATH="$P/python:$P/reference-python:/opt/vaso/python-deps"
export PATH="$CUDA_TOOLS:$P/bin:/opt/vaso/cuda/bin:/opt/vaso/cmake/bin:/opt/vaso/python-deps/bin:/usr/local/bin:/usr/bin:/bin"
export LD_LIBRARY_PATH="/run/insula/nvidia:/opt/vaso/cuda/lib64:/usr/local/lib"
export TRITON_CUOBJDUMP_PATH="$CUDA_TOOLS/cuobjdump" TRITON_NVDISASM_PATH="$CUDA_TOOLS/nvdisasm"
export CUDA_HOME=/opt/vaso/cuda TRITON_PTXAS_PATH=/opt/vaso/cuda/bin/ptxas TRITON_LIBDEVICE_PATH=/opt/vaso/cuda/nvvm/libdevice/libdevice.10.bc TRITON_LIBCUDA_PATH=/run/insula/nvidia
export C_INCLUDE_PATH=/opt/vaso/cuda/include CPLUS_INCLUDE_PATH=/opt/vaso/cuda/include CC=gcc CXX=g++
export TRITON_CACHE_DIR=/workspace/triton-cache HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 PYTHONDONTWRITEBYTECODE=1 PYTHONNOUSERSITE=1 OMP_NUM_THREADS=1 MKL_NUM_THREADS=1
mkdir -p /workspace/receipts /workspace/triton-cache
export INKLING_ACTIVATION_RESULT=/workspace/receipts/activation-result.json
case "${1:-compiler}" in
  compiler) exec /usr/local/bin/python3 "$P/qualification/compiler_probe.py" --install "$P/python" --output /workspace/receipts/compiler-result.json;;
  operators) exec /usr/local/bin/python3 "$P/qualification/inkling_triton_suite.py";;
  activation) exec /usr/local/bin/python3 "$P/qualification/activation_quant_v3.py";;
  model) exec /usr/local/bin/python3 "$P/qualification/model_triton_test_v2.py";;
  *) exit 64;;
esac
