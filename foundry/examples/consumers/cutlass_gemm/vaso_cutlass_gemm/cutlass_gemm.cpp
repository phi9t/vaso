#include <ATen/core/Tensor.h>
#include <ATen/ops/matmul.h>
#include <c10/core/ScalarType.h>
#include <c10/util/Exception.h>
#include <torch/library.h>

at::Tensor cutlass_gemm_cuda(const at::Tensor& a, const at::Tensor& b);

namespace {

void check_gemm_inputs(const at::Tensor& a, const at::Tensor& b) {
  TORCH_CHECK(a.dim() == 2, "a must have shape (M, K)");
  TORCH_CHECK(b.dim() == 2, "b must have shape (K, N)");
  TORCH_CHECK(a.size(1) == b.size(0), "a and b must have compatible K dimensions");
  TORCH_CHECK(a.device() == b.device(), "a and b must be on the same device");
  TORCH_CHECK(a.scalar_type() == at::kFloat && b.scalar_type() == at::kFloat, "cutlass_gemm supports float32 tensors");
}

at::Tensor cutlass_gemm_cpu(const at::Tensor& a, const at::Tensor& b) {
  TORCH_CHECK(!a.is_cuda(), "cutlass_gemm_cpu received CUDA tensor a");
  TORCH_CHECK(!b.is_cuda(), "cutlass_gemm_cpu received CUDA tensor b");
  check_gemm_inputs(a, b);
  return at::matmul(a.contiguous(), b.contiguous());
}

}  // namespace

TORCH_LIBRARY_FRAGMENT(vaso_graph, m) {
  m.def("cutlass_gemm(Tensor a, Tensor b) -> Tensor");
}

TORCH_LIBRARY_IMPL(vaso_graph, CPU, m) {
  m.impl("cutlass_gemm", cutlass_gemm_cpu);
}

TORCH_LIBRARY_IMPL(vaso_graph, CUDA, m) {
  m.impl("cutlass_gemm", cutlass_gemm_cuda);
}

