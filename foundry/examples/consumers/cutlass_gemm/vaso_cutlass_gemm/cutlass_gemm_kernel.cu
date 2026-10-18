#include <ATen/Functions.h>
#include <ATen/cuda/CUDAContext.h>
#include <c10/cuda/CUDAException.h>
#include <c10/cuda/CUDAGuard.h>
#include <cute/tensor.hpp>

#include <cstdint>

namespace {

constexpr int kTile = 16;

void check_gemm_inputs_cuda(const at::Tensor& a, const at::Tensor& b) {
  TORCH_CHECK(a.is_cuda(), "cutlass_gemm_cuda received CPU tensor a");
  TORCH_CHECK(b.is_cuda(), "cutlass_gemm_cuda received CPU tensor b");
  TORCH_CHECK(a.dim() == 2, "a must have shape (M, K)");
  TORCH_CHECK(b.dim() == 2, "b must have shape (K, N)");
  TORCH_CHECK(a.size(1) == b.size(0), "a and b must have compatible K dimensions");
  TORCH_CHECK(a.device() == b.device(), "a and b must be on the same device");
  TORCH_CHECK(a.scalar_type() == at::kFloat && b.scalar_type() == at::kFloat, "cutlass_gemm supports float32 tensors");
}

__global__ void cutlass_gemm_kernel(
    const float* __restrict__ a,
    const float* __restrict__ b,
    float* __restrict__ out,
    int64_t m,
    int64_t n,
    int64_t k) {
  __shared__ float a_tile[kTile * kTile];
  __shared__ float b_tile[kTile * kTile];

  auto tile_layout = cute::make_layout(
      cute::make_shape(cute::Int<kTile>{}, cute::Int<kTile>{}),
      cute::make_stride(cute::Int<kTile>{}, cute::Int<1>{}));
  const int local_m = threadIdx.y;
  const int local_n = threadIdx.x;
  const int64_t row = static_cast<int64_t>(blockIdx.y) * kTile + local_m;
  const int64_t col = static_cast<int64_t>(blockIdx.x) * kTile + local_n;
  float acc = 0.0f;

  for (int64_t k0 = 0; k0 < k; k0 += kTile) {
    const int64_t a_col = k0 + local_n;
    const int64_t b_row = k0 + local_m;
    const int tile_offset = tile_layout(local_m, local_n);
    a_tile[tile_offset] = (row < m && a_col < k) ? a[row * k + a_col] : 0.0f;
    b_tile[tile_offset] = (b_row < k && col < n) ? b[b_row * n + col] : 0.0f;
    __syncthreads();

    for (int p = 0; p < kTile; ++p) {
      acc += a_tile[tile_layout(local_m, p)] * b_tile[tile_layout(p, local_n)];
    }
    __syncthreads();
  }

  if (row < m && col < n) {
    out[row * n + col] = acc;
  }
}

int blocks_for(int64_t n) {
  return static_cast<int>((n + kTile - 1) / kTile);
}

}  // namespace

at::Tensor cutlass_gemm_cuda(const at::Tensor& a, const at::Tensor& b) {
  check_gemm_inputs_cuda(a, b);
  const c10::cuda::OptionalCUDAGuard device_guard(a.device());
  const auto a_contig = a.contiguous();
  const auto b_contig = b.contiguous();
  const int64_t m = a_contig.size(0);
  const int64_t k = a_contig.size(1);
  const int64_t n = b_contig.size(1);
  auto out = at::empty({m, n}, a_contig.options());
  if (out.numel() == 0) {
    return out;
  }

  const dim3 threads(kTile, kTile);
  const dim3 blocks(blocks_for(n), blocks_for(m));
  const cudaStream_t stream = at::cuda::getCurrentCUDAStream();
  cutlass_gemm_kernel<<<blocks, threads, 0, stream>>>(
      a_contig.data_ptr<float>(),
      b_contig.data_ptr<float>(),
      out.data_ptr<float>(),
      m,
      n,
      k);
  C10_CUDA_KERNEL_LAUNCH_CHECK();
  return out;
}
