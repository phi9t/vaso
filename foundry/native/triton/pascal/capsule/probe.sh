#!/bin/bash
set -euo pipefail
P=/opt/vaso/pascal
export PATH="$P/bin:$P/build-python/bin:/opt/vaso/cmake/bin:/opt/vaso/python-deps/bin:/opt/vaso/cuda/bin:/usr/local/bin:/usr/bin:/bin"
export PYTHONPATH="$P/python:$P/reference-python:$P/build-python:/opt/vaso/python-deps"
export LD_LIBRARY_PATH=/opt/vaso/cuda/lib64:/usr/local/lib
export PYTHONDONTWRITEBYTECODE=1 PYTHONNOUSERSITE=1 HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
uv --version
gcc --version | head -1
cmake --version | head -1
ninja --version
"$P/llvm/bin/llvm-config" --version
ptxas --version
ptxas --help > /workspace/ptxas-help.txt
grep -m1 sm_61 /workspace/ptxas-help.txt
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
