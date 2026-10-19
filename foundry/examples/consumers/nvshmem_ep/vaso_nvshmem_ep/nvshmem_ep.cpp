#include <ATen/cuda/CUDAContext.h>
#include <c10/core/ScalarType.h>
#include <c10/cuda/CUDAGuard.h>
#include <pybind11/stl.h>
#include <torch/extension.h>

#include <atomic>
#include <cstdint>
#include <mutex>
#include <vector>

void nvshmem_ep_dispatch_cuda(
    const at::Tensor& tokens,
    const at::Tensor& expert_for_token,
    const at::Tensor& output,
    int64_t world_size);

void nvshmem_ep_combine_cuda(
    const at::Tensor& dispatched,
    const at::Tensor& owner_for_row,
    const at::Tensor& slot_for_row,
    const at::Tensor& output);
int64_t nvshmem_ep_unique_id_size_cuda();
void nvshmem_ep_create_unique_id_cuda(void* out, int64_t bytes);
void nvshmem_ep_initialize_cuda(int64_t rank, int64_t world_size, const void* unique_id, int64_t bytes);
void nvshmem_ep_finalize_cuda();
int64_t nvshmem_ep_my_pe_cuda();
int64_t nvshmem_ep_n_pes_cuda();
void* nvshmem_ep_malloc_cuda(size_t bytes);
void nvshmem_ep_free_cuda(void* ptr);

namespace {

std::mutex g_nvshmem_mutex;
std::atomic<bool> g_nvshmem_initialized{false};

void require_initialized() {
  TORCH_CHECK(g_nvshmem_initialized.load(), "NVSHMEM has not been initialized");
}

void check_cuda_tensor(const at::Tensor& tensor, const char* name) {
  TORCH_CHECK(tensor.is_cuda(), name, " must be a CUDA tensor");
  TORCH_CHECK(tensor.is_contiguous(), name, " must be contiguous");
}

void initialize_nvshmem(int64_t rank, int64_t world_size, const at::Tensor& unique_id_tensor) {
  TORCH_CHECK(rank >= 0 && rank < world_size, "rank must be in [0, world_size)");
  TORCH_CHECK(world_size > 0, "world_size must be positive");
  TORCH_CHECK(!unique_id_tensor.is_cuda(), "unique_id must be a CPU uint8 tensor");
  TORCH_CHECK(unique_id_tensor.scalar_type() == at::kByte, "unique_id must be a uint8 tensor");
  const int64_t expected_bytes = nvshmem_ep_unique_id_size_cuda();
  TORCH_CHECK(unique_id_tensor.numel() == expected_bytes,
              "unique_id must be exactly ", expected_bytes, " bytes");

  std::lock_guard<std::mutex> guard(g_nvshmem_mutex);
  if (g_nvshmem_initialized.load()) {
    TORCH_CHECK(nvshmem_ep_my_pe_cuda() == rank, "NVSHMEM already initialized for a different rank");
    TORCH_CHECK(nvshmem_ep_n_pes_cuda() == world_size, "NVSHMEM already initialized for a different world size");
    return;
  }

  const at::Tensor unique_id_contiguous = unique_id_tensor.contiguous();
  nvshmem_ep_initialize_cuda(
      rank,
      world_size,
      unique_id_contiguous.data_ptr<uint8_t>(),
      expected_bytes);
  TORCH_CHECK(nvshmem_ep_my_pe_cuda() == rank, "NVSHMEM rank mismatch after initialization");
  TORCH_CHECK(nvshmem_ep_n_pes_cuda() == world_size, "NVSHMEM world size mismatch after initialization");
  g_nvshmem_initialized.store(true);
}

at::Tensor create_unique_id() {
  const int64_t bytes = nvshmem_ep_unique_id_size_cuda();
  auto out = at::empty({bytes}, at::TensorOptions().dtype(at::kByte).device(at::kCPU));
  nvshmem_ep_create_unique_id_cuda(out.data_ptr<uint8_t>(), bytes);
  return out;
}

int64_t unique_id_size() {
  return nvshmem_ep_unique_id_size_cuda();
}

void finalize_nvshmem() {
  std::lock_guard<std::mutex> guard(g_nvshmem_mutex);
  if (!g_nvshmem_initialized.load()) {
    return;
  }
  nvshmem_ep_finalize_cuda();
  g_nvshmem_initialized.store(false);
}

at::Tensor allocate_symmetric_float32(std::vector<int64_t> sizes) {
  require_initialized();
  int64_t numel = 1;
  for (const int64_t dim : sizes) {
    TORCH_CHECK(dim >= 0, "symmetric tensor dimensions must be non-negative");
    numel *= dim;
  }
  const size_t bytes = static_cast<size_t>(numel) * sizeof(float);
  void* ptr = nvshmem_ep_malloc_cuda(bytes == 0 ? 1 : bytes);
  TORCH_CHECK(ptr != nullptr, "nvshmem_malloc failed");
  auto options = at::TensorOptions().dtype(at::kFloat).device(at::kCUDA, at::cuda::current_device());
  auto deleter = [](void* data) {
    if (data != nullptr && g_nvshmem_initialized.load()) {
      nvshmem_ep_free_cuda(data);
    }
  };
  return at::from_blob(ptr, sizes, deleter, options);
}

void dispatch_checked(const at::Tensor& tokens, const at::Tensor& expert_for_token, const at::Tensor& output, int64_t world_size) {
  require_initialized();
  check_cuda_tensor(tokens, "tokens");
  check_cuda_tensor(expert_for_token, "expert_for_token");
  check_cuda_tensor(output, "output");
  c10::cuda::CUDAGuard guard(tokens.device());
  TORCH_CHECK(tokens.scalar_type() == at::kFloat, "tokens must be float32");
  TORCH_CHECK(output.scalar_type() == at::kFloat, "output must be float32");
  TORCH_CHECK(expert_for_token.scalar_type() == at::kLong, "expert_for_token must be int64");
  TORCH_CHECK(tokens.dim() == 2, "tokens must have shape (N, C)");
  TORCH_CHECK(tokens.size(0) == world_size, "this proof expects one token per peer");
  TORCH_CHECK(expert_for_token.dim() == 1 && expert_for_token.size(0) == tokens.size(0),
              "expert_for_token must have shape (N)");
  TORCH_CHECK(output.dim() == 2, "output must have shape (world_size, C)");
  TORCH_CHECK(output.size(0) == world_size,
              "output first dimension must equal world_size");
  TORCH_CHECK(output.size(1) == tokens.size(1), "output channels must match tokens");
  TORCH_CHECK(world_size == nvshmem_ep_n_pes_cuda(), "world_size must match initialized NVSHMEM PEs");
  nvshmem_ep_dispatch_cuda(tokens, expert_for_token, output, world_size);
}

void combine_checked(const at::Tensor& dispatched, const at::Tensor& owner_for_row, const at::Tensor& slot_for_row, const at::Tensor& output) {
  require_initialized();
  check_cuda_tensor(dispatched, "dispatched");
  check_cuda_tensor(owner_for_row, "owner_for_row");
  check_cuda_tensor(slot_for_row, "slot_for_row");
  check_cuda_tensor(output, "output");
  c10::cuda::CUDAGuard guard(dispatched.device());
  TORCH_CHECK(dispatched.scalar_type() == at::kFloat, "dispatched must be float32");
  TORCH_CHECK(output.scalar_type() == at::kFloat, "output must be float32");
  TORCH_CHECK(owner_for_row.scalar_type() == at::kLong, "owner_for_row must be int64");
  TORCH_CHECK(slot_for_row.scalar_type() == at::kLong, "slot_for_row must be int64");
  TORCH_CHECK(dispatched.dim() == 2, "dispatched must have shape (R, C)");
  TORCH_CHECK(output.dim() == 2, "output must have shape (N, C)");
  TORCH_CHECK(owner_for_row.dim() == 1 && owner_for_row.size(0) == dispatched.size(0),
              "owner_for_row must have shape (R)");
  TORCH_CHECK(slot_for_row.dim() == 1 && slot_for_row.size(0) == dispatched.size(0),
              "slot_for_row must have shape (R)");
  TORCH_CHECK(dispatched.size(1) == output.size(1), "output channels must match dispatched");
  nvshmem_ep_combine_cuda(dispatched, owner_for_row, slot_for_row, output);
}

}  // namespace

TORCH_LIBRARY_FRAGMENT(vaso_graph, m) {
  m.def("nvshmem_ep_dispatch(Tensor tokens, Tensor expert_for_token, Tensor output, int world_size) -> ()");
  m.def("nvshmem_ep_combine(Tensor dispatched, Tensor owner_for_row, Tensor slot_for_row, Tensor output) -> ()");
}

TORCH_LIBRARY_IMPL(vaso_graph, CUDA, m) {
  m.impl("nvshmem_ep_dispatch", dispatch_checked);
  m.impl("nvshmem_ep_combine", combine_checked);
}

PYBIND11_MODULE(_C, m) {
  m.def("unique_id_size", unique_id_size);
  m.def("create_unique_id", create_unique_id);
  m.def("initialize", initialize_nvshmem);
  m.def("finalize", finalize_nvshmem);
  m.def("allocate_symmetric_float32", allocate_symmetric_float32);
}
