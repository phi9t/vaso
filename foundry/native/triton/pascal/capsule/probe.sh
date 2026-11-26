#!/bin/bash
set -euo pipefail
P=/opt/vaso/pascal
CUDA_TOOLS="$P/cuda-tools/12.6.77/bin"
export PATH="$CUDA_TOOLS:$P/bin:$P/build-python/bin:/opt/vaso/cmake/bin:/opt/vaso/python-deps/bin:/opt/vaso/cuda/bin:/usr/local/bin:/usr/bin:/bin"
export PYTHONPATH="$P/python:$P/reference-python:$P/build-python:/opt/vaso/python-deps"
export LD_LIBRARY_PATH=/opt/vaso/cuda/lib64:/usr/local/lib
export TRITON_CUOBJDUMP_PATH="$CUDA_TOOLS/cuobjdump" TRITON_NVDISASM_PATH="$CUDA_TOOLS/nvdisasm"
export PYTHONDONTWRITEBYTECODE=1 PYTHONNOUSERSITE=1 HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
uv --version
gcc --version
cmake --version
ninja --version
"$P/llvm/bin/llvm-config" --version
ptxas --version
ptxas --help > /workspace/ptxas-help.txt
grep -m1 sm_61 /workspace/ptxas-help.txt
# Diagnose the actual Pascal machine code without requiring a GPU or driver.
mkdir -p /workspace/cuda-diagnostics
for tool in cuobjdump nvdisasm; do
  "$CUDA_TOOLS/$tool" --version > "/workspace/cuda-diagnostics/$tool-version.txt"
  cat "/workspace/cuda-diagnostics/$tool-version.txt"
  grep -q 'V12\.6\.77' "/workspace/cuda-diagnostics/$tool-version.txt"
done
cat > /workspace/cuda-diagnostics/sm61.ptx <<'PTX'
.version 6.0
.target sm_61
.address_size 64
.visible .entry capsule_sm61_probe(
  .param .u64 output,
  .param .u32 a,
  .param .u32 b
) {
  .reg .b64 %out;
  .reg .b32 %a, %b, %result;
  ld.param.u64 %out, [output];
  ld.param.u32 %a, [a];
  ld.param.u32 %b, [b];
  dp4a.s32.s32 %result, %a, %b, 0;
  st.global.u32 [%out], %result;
  ret;
}
PTX
ptxas -arch=sm_61 /workspace/cuda-diagnostics/sm61.ptx -o /workspace/cuda-diagnostics/sm61.cubin
"$TRITON_CUOBJDUMP_PATH" --dump-sass /workspace/cuda-diagnostics/sm61.cubin > /workspace/cuda-diagnostics/cuobjdump.sass
"$TRITON_NVDISASM_PATH" /workspace/cuda-diagnostics/sm61.cubin > /workspace/cuda-diagnostics/nvdisasm.sass
for tool in cuobjdump nvdisasm; do
  grep -q 'IDP\.4A' "/workspace/cuda-diagnostics/$tool.sass"
done
test -f "$P/llvm/lib/cmake/mlir/MLIRConfig.cmake"
test -f "$P/json/include/nlohmann/json.hpp"
test -f "$P/triton-675c59878aa2280b31f722aaf42b825fcee21de8.tar.gz"
test -x "$P/bin/build-triton"
command -v patch
command -v tar
printf '#include <zlib.h>\nint main(void){return zlibVersion()==0;}\n' > /workspace/z.c
gcc /workspace/z.c -lz -o /workspace/z
/workspace/z
/usr/local/bin/python3 - <<'PY'
import torch,triton,transformers,pybind11,lit
from transformers.models.inkling2_loop.modeling_inkling2_loop import Inkling2LoopForCausalLM
assert torch.__version__=="2.15.0+vaso.cu126.sm61"
assert triton.__version__=="3.8.0"
for m in (torch,triton,transformers,pybind11,lit):
    assert m.__file__.startswith("/opt/vaso/pascal/"),m.__file__
    print(m.__name__,getattr(m,"__version__",""),m.__file__)
PY
echo CAPSULE_DEPENDENCY_PROBE_PASSED
