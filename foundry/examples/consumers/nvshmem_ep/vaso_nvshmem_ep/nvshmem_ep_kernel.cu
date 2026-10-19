#include <ATen/cuda/CUDAContext.h>
#include <cuda_runtime.h>
#include <nvshmem.h>
#include <nvshmemx.h>
#include <torch/extension.h>

#include <cstdint>
#include <cstring>

namespace vaso_nvshmem_ep_kernel {

constexpr int kThreads = 256;

}  // namespace vaso_nvshmem_ep_kernel

extern "C" __global__ void nvshmem_ep_dispatch_kernel(
    const float* tokens,
    const int64_t* expert_for_token,
    float* output,
    int64_t num_tokens,
    int64_t channels,
    int world_size) {
  const int64_t index = blockIdx.x * blockDim.x + threadIdx.x;
  if (index >= num_tokens) {
    return;
  }
  const int64_t peer = expert_for_token[index];
  if (peer < 0 || peer >= world_size) {
    return;
  }
  const int64_t remote_row = static_cast<int64_t>(nvshmem_my_pe());
  nvshmem_putmem(
      output + remote_row * channels,
      tokens + index * channels,
      static_cast<size_t>(channels) * sizeof(float),
      static_cast<int>(peer));
  nvshmem_quiet();
}

extern "C" __global__ void nvshmem_ep_combine_kernel(
    const float* dispatched,
    const int64_t* owner_for_row,
    const int64_t* slot_for_row,
    float* output,
    int64_t rows,
    int64_t channels) {
  const int64_t index = blockIdx.x * blockDim.x + threadIdx.x;
  if (index >= rows) {
    return;
  }
  const int64_t owner = owner_for_row[index];
  const int64_t slot = slot_for_row[index];
  if (owner < 0 || owner >= nvshmem_n_pes() || slot < 0) {
    return;
  }
  nvshmem_putmem(
      output + slot * channels,
      dispatched + index * channels,
      static_cast<size_t>(channels) * sizeof(float),
      static_cast<int>(owner));
  nvshmem_quiet();
}

namespace vaso_nvshmem_ep_kernel {

int blocks_for(int64_t n) {
  return static_cast<int>((n + kThreads - 1) / kThreads);
}

void clear_last_kernel_error() {
  (void)cudaGetLastError();
}

void check_last_kernel(const char* name) {
  const cudaError_t status = cudaGetLastError();
  TORCH_CHECK(status == cudaSuccess, name, " launch failed: ", cudaGetErrorString(status));
}

void check_collective_launch(int status, const char* name) {
  TORCH_CHECK(status == 0, name, " collective launch failed with status ", status);
  check_last_kernel(name);
}

}  // namespace vaso_nvshmem_ep_kernel

using namespace vaso_nvshmem_ep_kernel;

int64_t nvshmem_ep_unique_id_size_cuda() {
  return static_cast<int64_t>(sizeof(nvshmemx_uniqueid_t));
}

void nvshmem_ep_create_unique_id_cuda(void* out, int64_t bytes) {
  TORCH_CHECK(bytes == nvshmem_ep_unique_id_size_cuda(),
              "unique_id buffer must be exactly ", nvshmem_ep_unique_id_size_cuda(), " bytes");
  nvshmemx_uniqueid_t unique_id;
  const int status = nvshmemx_get_uniqueid(&unique_id);
  TORCH_CHECK(status == 0, "nvshmemx_get_uniqueid failed with status ", status);
  std::memcpy(out, &unique_id, sizeof(unique_id));
}

void nvshmem_ep_initialize_cuda(int64_t rank, int64_t world_size, const void* unique_id, int64_t bytes) {
  TORCH_CHECK(bytes == nvshmem_ep_unique_id_size_cuda(),
              "unique_id must be exactly ", nvshmem_ep_unique_id_size_cuda(), " bytes");
  nvshmemx_uniqueid_t copied_unique_id;
  std::memcpy(&copied_unique_id, unique_id, sizeof(copied_unique_id));

  nvshmemx_init_attr_t attr{};
  int status = nvshmemx_set_attr_uniqueid_args(
      static_cast<int>(rank),
      static_cast<int>(world_size),
      &copied_unique_id,
      &attr);
  TORCH_CHECK(status == 0, "nvshmemx_set_attr_uniqueid_args failed with status ", status);
  status = nvshmemx_init_attr(NVSHMEMX_INIT_WITH_UNIQUEID, &attr);
  TORCH_CHECK(status == 0, "nvshmemx_init_attr failed with status ", status);
}

void nvshmem_ep_finalize_cuda() {
  nvshmem_finalize();
}

int64_t nvshmem_ep_my_pe_cuda() {
  return static_cast<int64_t>(nvshmem_my_pe());
}

int64_t nvshmem_ep_n_pes_cuda() {
  return static_cast<int64_t>(nvshmem_n_pes());
}

void* nvshmem_ep_malloc_cuda(size_t bytes) {
  return nvshmem_malloc(bytes);
}

void nvshmem_ep_free_cuda(void* ptr) {
  nvshmem_free(ptr);
}

void nvshmem_ep_dispatch_cuda(
    const at::Tensor& tokens,
    const at::Tensor& expert_for_token,
    const at::Tensor& output,
    int64_t world_size) {
  int64_t num_tokens = tokens.size(0);
  if (num_tokens == 0) {
    return;
  }
  const float* tokens_ptr = tokens.data_ptr<float>();
  const int64_t* expert_ptr = expert_for_token.data_ptr<int64_t>();
  float* output_ptr = output.data_ptr<float>();
  int64_t channels = tokens.size(1);
  int world_size_int = static_cast<int>(world_size);
  void* args[] = {
      &tokens_ptr,
      &expert_ptr,
      &output_ptr,
      &num_tokens,
      &channels,
      &world_size_int,
  };
  clear_last_kernel_error();
  const int status = nvshmemx_collective_launch(
      reinterpret_cast<const void*>(nvshmem_ep_dispatch_kernel),
      dim3(blocks_for(num_tokens)),
      dim3(kThreads),
      args,
      0,
      at::cuda::getCurrentCUDAStream());
  check_collective_launch(status, "dispatch_kernel");
}

void nvshmem_ep_combine_cuda(
    const at::Tensor& dispatched,
    const at::Tensor& owner_for_row,
    const at::Tensor& slot_for_row,
    const at::Tensor& output) {
  int64_t rows = dispatched.size(0);
  if (rows == 0) {
    return;
  }
  const float* dispatched_ptr = dispatched.data_ptr<float>();
  const int64_t* owner_ptr = owner_for_row.data_ptr<int64_t>();
  const int64_t* slot_ptr = slot_for_row.data_ptr<int64_t>();
  float* output_ptr = output.data_ptr<float>();
  int64_t channels = dispatched.size(1);
  void* args[] = {
      &dispatched_ptr,
      &owner_ptr,
      &slot_ptr,
      &output_ptr,
      &rows,
      &channels,
  };
  clear_last_kernel_error();
  const int status = nvshmemx_collective_launch(
      reinterpret_cast<const void*>(nvshmem_ep_combine_kernel),
      dim3(blocks_for(rows)),
      dim3(kThreads),
      args,
      0,
      at::cuda::getCurrentCUDAStream());
  check_collective_launch(status, "combine_kernel");
}
