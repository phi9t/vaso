#include <ATen/Dispatch.h>
#include <ATen/Functions.h>
#include <ATen/cuda/CUDAContext.h>
#include <c10/cuda/CUDAException.h>
#include <c10/cuda/CUDAGuard.h>

#include <cmath>
#include <cstdint>
#include <limits>
#include <tuple>

namespace {

struct GridSpec {
  double voxel_size[3];
  double grid_min[3];
  int64_t dims[3];
  int64_t num_voxels;
};

GridSpec make_grid(c10::List<double> voxel_size, c10::List<double> grid_min, c10::List<double> grid_max) {
  TORCH_CHECK(voxel_size.size() == 3, "voxel_size must contain exactly three values");
  TORCH_CHECK(grid_min.size() == 3, "grid_min must contain exactly three values");
  TORCH_CHECK(grid_max.size() == 3, "grid_max must contain exactly three values");
  GridSpec spec;
  spec.num_voxels = 1;
  for (int axis = 0; axis < 3; ++axis) {
    const double size = voxel_size.get(axis);
    const double low = grid_min.get(axis);
    const double high = grid_max.get(axis);
    TORCH_CHECK(size > 0.0, "voxel_size values must be positive");
    const auto dim = static_cast<int64_t>(std::floor((high - low) / size));
    TORCH_CHECK(dim > 0, "grid bounds must contain at least one voxel on every axis");
    TORCH_CHECK(
        spec.num_voxels <= std::numeric_limits<int64_t>::max() / dim,
        "voxel grid is too large");
    spec.voxel_size[axis] = size;
    spec.grid_min[axis] = low;
    spec.dims[axis] = dim;
    spec.num_voxels *= dim;
  }
  return spec;
}

void check_forward_inputs(const at::Tensor& points, const at::Tensor& features) {
  TORCH_CHECK(points.is_cuda(), "voxel_scatter_forward_cuda received CPU points");
  TORCH_CHECK(features.is_cuda(), "voxel_scatter_forward_cuda received CPU features");
  TORCH_CHECK(points.dim() == 2 && points.size(1) == 3, "points must have shape (N, 3)");
  TORCH_CHECK(features.dim() == 2, "features must have shape (N, C)");
  TORCH_CHECK(points.size(0) == features.size(0), "points and features must have the same N");
  TORCH_CHECK(points.device() == features.device(), "points and features must be on the same device");
  TORCH_CHECK(points.scalar_type() == features.scalar_type(), "points and features must have the same dtype");
  TORCH_CHECK(features.is_floating_point(), "features must be floating point");
}

__device__ int64_t point_to_voxel_device(const double x, const double y, const double z, GridSpec grid) {
  const auto ix = static_cast<int64_t>(floor((x - grid.grid_min[0]) / grid.voxel_size[0]));
  const auto iy = static_cast<int64_t>(floor((y - grid.grid_min[1]) / grid.voxel_size[1]));
  const auto iz = static_cast<int64_t>(floor((z - grid.grid_min[2]) / grid.voxel_size[2]));
  if (ix < 0 || iy < 0 || iz < 0 || ix >= grid.dims[0] || iy >= grid.dims[1] || iz >= grid.dims[2]) {
    return -1;
  }
  return ix + grid.dims[0] * (iy + grid.dims[1] * iz);
}

template <typename scalar_t>
__device__ void atomic_max_scalar(scalar_t* address, scalar_t value);

template <>
__device__ void atomic_max_scalar<float>(float* address, float value) {
  int* address_as_int = reinterpret_cast<int*>(address);
  int old = *address_as_int;
  int assumed;
  do {
    assumed = old;
    if (__int_as_float(assumed) >= value) {
      break;
    }
    old = atomicCAS(address_as_int, assumed, __float_as_int(value));
  } while (assumed != old);
}

template <>
__device__ void atomic_max_scalar<double>(double* address, double value) {
  auto* address_as_ull = reinterpret_cast<unsigned long long*>(address);
  unsigned long long old = *address_as_ull;
  unsigned long long assumed;
  do {
    assumed = old;
    if (__longlong_as_double(static_cast<long long>(assumed)) >= value) {
      break;
    }
    old = atomicCAS(address_as_ull, assumed, static_cast<unsigned long long>(__double_as_longlong(value)));
  } while (assumed != old);
}

template <typename scalar_t>
__global__ void assign_points_kernel(
    const scalar_t* points,
    int64_t n_points,
    GridSpec grid,
    int64_t* point_to_voxel,
    int64_t* counts) {
  const int64_t point = blockIdx.x * blockDim.x + threadIdx.x;
  if (point >= n_points) {
    return;
  }
  const int64_t voxel = point_to_voxel_device(
      static_cast<double>(points[point * 3 + 0]),
      static_cast<double>(points[point * 3 + 1]),
      static_cast<double>(points[point * 3 + 2]),
      grid);
  point_to_voxel[point] = voxel;
  if (voxel >= 0) {
    atomicAdd(reinterpret_cast<unsigned long long*>(&counts[voxel]), 1ULL);
  }
}

template <typename scalar_t>
__global__ void scatter_features_kernel(
    const scalar_t* features,
    const int64_t* point_to_voxel,
    int64_t n_points,
    int64_t channels,
    scalar_t* sums,
    scalar_t* max_values) {
  const int64_t idx = blockIdx.x * blockDim.x + threadIdx.x;
  const int64_t total = n_points * channels;
  if (idx >= total) {
    return;
  }
  const int64_t point = idx / channels;
  const int64_t channel = idx - point * channels;
  const int64_t voxel = point_to_voxel[point];
  if (voxel < 0) {
    return;
  }
  const scalar_t value = features[idx];
  const int64_t out_offset = voxel * channels + channel;
  atomicAdd(&sums[out_offset], value);
  atomic_max_scalar(&max_values[out_offset], value);
}

template <typename scalar_t>
__global__ void assign_max_indices_kernel(
    const scalar_t* features,
    const int64_t* point_to_voxel,
    const scalar_t* max_values,
    int64_t n_points,
    int64_t channels,
    int64_t* max_indices) {
  const int64_t idx = blockIdx.x * blockDim.x + threadIdx.x;
  const int64_t total = n_points * channels;
  if (idx >= total) {
    return;
  }
  const int64_t point = idx / channels;
  const int64_t channel = idx - point * channels;
  const int64_t voxel = point_to_voxel[point];
  if (voxel < 0) {
    return;
  }
  const int64_t out_offset = voxel * channels + channel;
  if (features[idx] == max_values[out_offset]) {
    atomicMin(reinterpret_cast<unsigned long long*>(&max_indices[out_offset]), static_cast<unsigned long long>(point));
  }
}

template <typename scalar_t>
__global__ void finalize_forward_kernel(
    scalar_t* mean,
    scalar_t* max_values,
    int64_t* max_indices,
    const int64_t* counts,
    int64_t num_voxels,
    int64_t channels) {
  const int64_t idx = blockIdx.x * blockDim.x + threadIdx.x;
  const int64_t total = num_voxels * channels;
  if (idx >= total) {
    return;
  }
  const int64_t voxel = idx / channels;
  const int64_t count = counts[voxel];
  if (count > 0) {
    mean[idx] = mean[idx] / static_cast<scalar_t>(count);
  } else {
    max_values[idx] = static_cast<scalar_t>(0);
    max_indices[idx] = -1;
  }
}

template <typename scalar_t>
__global__ void backward_kernel(
    const scalar_t* grad_mean,
    const scalar_t* grad_max,
    const int64_t* point_to_voxel,
    const int64_t* counts,
    const int64_t* max_indices,
    int64_t n_points,
    int64_t channels,
    scalar_t* grad_features) {
  const int64_t idx = blockIdx.x * blockDim.x + threadIdx.x;
  const int64_t total = n_points * channels;
  if (idx >= total) {
    return;
  }
  const int64_t point = idx / channels;
  const int64_t channel = idx - point * channels;
  const int64_t voxel = point_to_voxel[point];
  if (voxel < 0) {
    grad_features[idx] = static_cast<scalar_t>(0);
    return;
  }
  const int64_t out_offset = voxel * channels + channel;
  scalar_t grad = grad_mean[out_offset] / static_cast<scalar_t>(counts[voxel]);
  if (max_indices[out_offset] == point) {
    grad += grad_max[out_offset];
  }
  grad_features[idx] = grad;
}

int blocks_for(int64_t n, int threads) {
  return static_cast<int>((n + threads - 1) / threads);
}

}  // namespace

std::tuple<at::Tensor, at::Tensor, at::Tensor, at::Tensor, at::Tensor> voxel_scatter_forward_cuda(
    const at::Tensor& points,
    const at::Tensor& features,
    c10::List<double> voxel_size,
    c10::List<double> grid_min,
    c10::List<double> grid_max) {
  check_forward_inputs(points, features);
  const c10::cuda::OptionalCUDAGuard device_guard(features.device());
  const GridSpec grid = make_grid(voxel_size, grid_min, grid_max);
  const auto points_contig = points.contiguous();
  const auto features_contig = features.contiguous();
  const int64_t n_points = points_contig.size(0);
  const int64_t channels = features_contig.size(1);

  auto mean = at::zeros({grid.num_voxels, channels}, features_contig.options());
  auto max_values = at::full({grid.num_voxels, channels}, -std::numeric_limits<double>::infinity(), features_contig.options());
  auto point_to_voxel = at::full({n_points}, -1, points_contig.options().dtype(at::kLong));
  auto counts = at::zeros({grid.num_voxels}, points_contig.options().dtype(at::kLong));
  auto max_indices = at::full({grid.num_voxels, channels}, n_points, points_contig.options().dtype(at::kLong));

  const int threads = 256;
  const cudaStream_t stream = at::cuda::getCurrentCUDAStream();
  AT_DISPATCH_FLOATING_TYPES(features_contig.scalar_type(), "voxel_scatter_forward_cuda", [&] {
    if (n_points > 0) {
      assign_points_kernel<scalar_t><<<blocks_for(n_points, threads), threads, 0, stream>>>(
          points_contig.data_ptr<scalar_t>(),
          n_points,
          grid,
          point_to_voxel.data_ptr<int64_t>(),
          counts.data_ptr<int64_t>());
      C10_CUDA_KERNEL_LAUNCH_CHECK();
    }
    const int64_t feature_total = n_points * channels;
    if (feature_total > 0) {
      scatter_features_kernel<scalar_t><<<blocks_for(feature_total, threads), threads, 0, stream>>>(
          features_contig.data_ptr<scalar_t>(),
          point_to_voxel.data_ptr<int64_t>(),
          n_points,
          channels,
          mean.data_ptr<scalar_t>(),
          max_values.data_ptr<scalar_t>());
      C10_CUDA_KERNEL_LAUNCH_CHECK();
      assign_max_indices_kernel<scalar_t><<<blocks_for(feature_total, threads), threads, 0, stream>>>(
          features_contig.data_ptr<scalar_t>(),
          point_to_voxel.data_ptr<int64_t>(),
          max_values.data_ptr<scalar_t>(),
          n_points,
          channels,
          max_indices.data_ptr<int64_t>());
      C10_CUDA_KERNEL_LAUNCH_CHECK();
    }
    const int64_t output_total = grid.num_voxels * channels;
    if (output_total > 0) {
      finalize_forward_kernel<scalar_t><<<blocks_for(output_total, threads), threads, 0, stream>>>(
          mean.data_ptr<scalar_t>(),
          max_values.data_ptr<scalar_t>(),
          max_indices.data_ptr<int64_t>(),
          counts.data_ptr<int64_t>(),
          grid.num_voxels,
          channels);
      C10_CUDA_KERNEL_LAUNCH_CHECK();
    }
  });

  return std::make_tuple(mean, max_values, point_to_voxel, counts, max_indices);
}

at::Tensor voxel_scatter_backward_cuda(
    const at::Tensor& grad_mean,
    const at::Tensor& grad_max,
    const at::Tensor& point_to_voxel,
    const at::Tensor& counts,
    const at::Tensor& max_indices) {
  TORCH_CHECK(grad_mean.is_cuda(), "voxel_scatter_backward_cuda received CPU gradients");
  TORCH_CHECK(grad_max.is_cuda() && point_to_voxel.is_cuda() && counts.is_cuda() && max_indices.is_cuda(), "all backward inputs must be CUDA tensors");
  TORCH_CHECK(grad_mean.dim() == 2 && grad_max.sizes() == grad_mean.sizes(), "grad_mean and grad_max must have shape (V, C)");
  TORCH_CHECK(point_to_voxel.dim() == 1, "point_to_voxel must have shape (N)");
  TORCH_CHECK(counts.dim() == 1 && counts.size(0) == grad_mean.size(0), "counts must have shape (V)");
  TORCH_CHECK(max_indices.sizes() == grad_mean.sizes(), "max_indices must have shape (V, C)");
  TORCH_CHECK(point_to_voxel.scalar_type() == at::kLong, "point_to_voxel must be int64");
  TORCH_CHECK(counts.scalar_type() == at::kLong, "counts must be int64");
  TORCH_CHECK(max_indices.scalar_type() == at::kLong, "max_indices must be int64");

  const c10::cuda::OptionalCUDAGuard device_guard(grad_mean.device());
  const auto grad_mean_contig = grad_mean.contiguous();
  const auto grad_max_contig = grad_max.contiguous();
  const auto point_to_voxel_contig = point_to_voxel.contiguous();
  const auto counts_contig = counts.contiguous();
  const auto max_indices_contig = max_indices.contiguous();
  const int64_t n_points = point_to_voxel_contig.size(0);
  const int64_t channels = grad_mean_contig.size(1);
  auto grad_features = at::empty({n_points, channels}, grad_mean_contig.options());

  const int64_t total = n_points * channels;
  if (total == 0) {
    return grad_features.zero_();
  }

  const int threads = 256;
  const cudaStream_t stream = at::cuda::getCurrentCUDAStream();
  AT_DISPATCH_FLOATING_TYPES(grad_mean_contig.scalar_type(), "voxel_scatter_backward_cuda", [&] {
    backward_kernel<scalar_t><<<blocks_for(total, threads), threads, 0, stream>>>(
        grad_mean_contig.data_ptr<scalar_t>(),
        grad_max_contig.data_ptr<scalar_t>(),
        point_to_voxel_contig.data_ptr<int64_t>(),
        counts_contig.data_ptr<int64_t>(),
        max_indices_contig.data_ptr<int64_t>(),
        n_points,
        channels,
        grad_features.data_ptr<scalar_t>());
    C10_CUDA_KERNEL_LAUNCH_CHECK();
  });
  return grad_features;
}
