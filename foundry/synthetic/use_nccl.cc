#include <nccl.h>
#include <cstdio>

int main() {
  int version = 0;
  ncclResult_t result = ncclGetVersion(&version);
  if (result != ncclSuccess) {
    std::fprintf(stderr, "ncclGetVersion failed: %s\n", ncclGetErrorString(result));
    return 1;
  }
  std::printf("nccl:%d\n", version);
  return 0;
}
