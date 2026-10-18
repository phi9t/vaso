#include <cuda.h>
#include <nccl.h>

extern "C" __global__ void vaso_extension_fixture_kernel(int* out) {
  if (threadIdx.x == 0 && blockIdx.x == 0) {
    *out = 1;
  }
}
