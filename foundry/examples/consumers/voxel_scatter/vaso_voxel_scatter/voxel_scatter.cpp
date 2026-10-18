#include <ATen/Dispatch.h>
#include <ATen/Functions.h>
#include <c10/core/ScalarType.h>
#include <torch/extension.h>

#include <cmath>
#include <cstdint>
#include <limits>
#include <tuple>

std::tuple<at::Tensor, at::Tensor, at::Tensor, at::Tensor, at::Tensor> voxel_scatter_forward_cuda(
    const at::Tensor& points,
    const at::Tensor& features,
    c10::List<double> voxel_size,
    c10::List<double> grid_min,
    c10::List<double> grid_max);

at::Tensor voxel_scatter_backward_cuda(
    const at::Tensor& grad_mean,
    const at::Tensor& grad_max,
    const at::Tensor& point_to_voxel,
    const at::Tensor& counts,
    const at::Tensor& max_indices);

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
  TORCH_CHECK(points.dim() == 2 && points.size(1) == 3, "points must have shape (N, 3)");
  TORCH_CHECK(features.dim() == 2, "features must have shape (N, C)");
  TORCH_CHECK(points.size(0) == features.size(0), "points and features must have the same N");
  TORCH_CHECK(points.device() == features.device(), "points and features must be on the same device");
  TORCH_CHECK(points.scalar_type() == features.scalar_type(), "points and features must have the same dtype");
  TORCH_CHECK(features.is_floating_point(), "features must be floating point");
}

int64_t point_to_voxel_cpu(const double x, const double y, const double z, const GridSpec& grid) {
  const auto ix = static_cast<int64_t>(std::floor((x - grid.grid_min[0]) / grid.voxel_size[0]));
  const auto iy = static_cast<int64_t>(std::floor((y - grid.grid_min[1]) / grid.voxel_size[1]));
  const auto iz = static_cast<int64_t>(std::floor((z - grid.grid_min[2]) / grid.voxel_size[2]));
  if (ix < 0 || iy < 0 || iz < 0 || ix >= grid.dims[0] || iy >= grid.dims[1] || iz >= grid.dims[2]) {
    return -1;
  }
  return ix + grid.dims[0] * (iy + grid.dims[1] * iz);
}

std::tuple<at::Tensor, at::Tensor, at::Tensor, at::Tensor, at::Tensor> voxel_scatter_forward_cpu(
    const at::Tensor& points,
    const at::Tensor& features,
    c10::List<double> voxel_size,
    c10::List<double> grid_min,
    c10::List<double> grid_max) {
  TORCH_CHECK(!points.is_cuda(), "voxel_scatter_forward_cpu received CUDA points");
  check_forward_inputs(points, features);
  const GridSpec grid = make_grid(voxel_size, grid_min, grid_max);
  const auto points_contig = points.contiguous();
  const auto features_contig = features.contiguous();
  const int64_t n_points = points_contig.size(0);
  const int64_t channels = features_contig.size(1);

  auto mean = at::zeros({grid.num_voxels, channels}, features_contig.options());
  auto max_features = at::empty({grid.num_voxels, channels}, features_contig.options());
  auto point_to_voxel = at::full({n_points}, -1, points_contig.options().dtype(at::kLong));
  auto counts = at::zeros({grid.num_voxels}, points_contig.options().dtype(at::kLong));
  auto max_indices = at::full({grid.num_voxels, channels}, -1, points_contig.options().dtype(at::kLong));

  AT_DISPATCH_FLOATING_TYPES(features_contig.scalar_type(), "voxel_scatter_forward_cpu", [&] {
    const auto* points_data = points_contig.data_ptr<scalar_t>();
    const auto* features_data = features_contig.data_ptr<scalar_t>();
    auto* mean_data = mean.data_ptr<scalar_t>();
    auto* max_data = max_features.data_ptr<scalar_t>();
    auto* point_to_voxel_data = point_to_voxel.data_ptr<int64_t>();
    auto* counts_data = counts.data_ptr<int64_t>();
    auto* max_indices_data = max_indices.data_ptr<int64_t>();
    const scalar_t neg_inf = -std::numeric_limits<scalar_t>::infinity();

    for (int64_t i = 0; i < grid.num_voxels * channels; ++i) {
      max_data[i] = neg_inf;
    }

    for (int64_t point = 0; point < n_points; ++point) {
      const int64_t voxel = point_to_voxel_cpu(
          static_cast<double>(points_data[point * 3 + 0]),
          static_cast<double>(points_data[point * 3 + 1]),
          static_cast<double>(points_data[point * 3 + 2]),
          grid);
      point_to_voxel_data[point] = voxel;
      if (voxel < 0) {
        continue;
      }
      counts_data[voxel] += 1;
      for (int64_t channel = 0; channel < channels; ++channel) {
        const int64_t offset = voxel * channels + channel;
        const scalar_t value = features_data[point * channels + channel];
        mean_data[offset] += value;
        if (value > max_data[offset]) {
          max_data[offset] = value;
          max_indices_data[offset] = point;
        }
      }
    }

    for (int64_t voxel = 0; voxel < grid.num_voxels; ++voxel) {
      const int64_t count = counts_data[voxel];
      for (int64_t channel = 0; channel < channels; ++channel) {
        const int64_t offset = voxel * channels + channel;
        if (count > 0) {
          mean_data[offset] = mean_data[offset] / static_cast<scalar_t>(count);
        } else {
          max_data[offset] = static_cast<scalar_t>(0);
        }
      }
    }
  });

  return std::make_tuple(mean, max_features, point_to_voxel, counts, max_indices);
}

at::Tensor voxel_scatter_backward_cpu(
    const at::Tensor& grad_mean,
    const at::Tensor& grad_max,
    const at::Tensor& point_to_voxel,
    const at::Tensor& counts,
    const at::Tensor& max_indices) {
  TORCH_CHECK(!grad_mean.is_cuda(), "voxel_scatter_backward_cpu received CUDA gradients");
  TORCH_CHECK(grad_mean.dim() == 2 && grad_max.sizes() == grad_mean.sizes(), "grad_mean and grad_max must have shape (V, C)");
  TORCH_CHECK(point_to_voxel.dim() == 1, "point_to_voxel must have shape (N)");
  TORCH_CHECK(counts.dim() == 1 && counts.size(0) == grad_mean.size(0), "counts must have shape (V)");
  TORCH_CHECK(max_indices.sizes() == grad_mean.sizes(), "max_indices must have shape (V, C)");
  TORCH_CHECK(point_to_voxel.scalar_type() == at::kLong, "point_to_voxel must be int64");
  TORCH_CHECK(counts.scalar_type() == at::kLong, "counts must be int64");
  TORCH_CHECK(max_indices.scalar_type() == at::kLong, "max_indices must be int64");

  const auto grad_mean_contig = grad_mean.contiguous();
  const auto grad_max_contig = grad_max.contiguous();
  const auto point_to_voxel_contig = point_to_voxel.contiguous();
  const auto counts_contig = counts.contiguous();
  const auto max_indices_contig = max_indices.contiguous();
  const int64_t n_points = point_to_voxel_contig.size(0);
  const int64_t channels = grad_mean_contig.size(1);
  auto grad_features = at::zeros({n_points, channels}, grad_mean_contig.options());

  AT_DISPATCH_FLOATING_TYPES(grad_mean_contig.scalar_type(), "voxel_scatter_backward_cpu", [&] {
    const auto* grad_mean_data = grad_mean_contig.data_ptr<scalar_t>();
    const auto* grad_max_data = grad_max_contig.data_ptr<scalar_t>();
    const auto* point_to_voxel_data = point_to_voxel_contig.data_ptr<int64_t>();
    const auto* counts_data = counts_contig.data_ptr<int64_t>();
    const auto* max_indices_data = max_indices_contig.data_ptr<int64_t>();
    auto* grad_features_data = grad_features.data_ptr<scalar_t>();

    for (int64_t point = 0; point < n_points; ++point) {
      const int64_t voxel = point_to_voxel_data[point];
      if (voxel < 0) {
        continue;
      }
      const auto count = static_cast<scalar_t>(counts_data[voxel]);
      for (int64_t channel = 0; channel < channels; ++channel) {
        const int64_t voxel_offset = voxel * channels + channel;
        scalar_t grad = grad_mean_data[voxel_offset] / count;
        if (max_indices_data[voxel_offset] == point) {
          grad += grad_max_data[voxel_offset];
        }
        grad_features_data[point * channels + channel] = grad;
      }
    }
  });

  return grad_features;
}

}  // namespace

TORCH_LIBRARY(vaso_graph, m) {
  m.def("voxel_scatter_forward(Tensor points, Tensor features, float[] voxel_size, float[] grid_min, float[] grid_max) -> (Tensor, Tensor, Tensor, Tensor, Tensor)");
  m.def("voxel_scatter_backward(Tensor grad_mean, Tensor grad_max, Tensor point_to_voxel, Tensor counts, Tensor max_indices) -> Tensor");
}

TORCH_LIBRARY_IMPL(vaso_graph, CPU, m) {
  m.impl("voxel_scatter_forward", voxel_scatter_forward_cpu);
  m.impl("voxel_scatter_backward", voxel_scatter_backward_cpu);
}

TORCH_LIBRARY_IMPL(vaso_graph, CUDA, m) {
  m.impl("voxel_scatter_forward", voxel_scatter_forward_cuda);
  m.impl("voxel_scatter_backward", voxel_scatter_backward_cuda);
}

PYBIND11_MODULE(_C, m) {
  m.def("extension_name", []() { return "vaso_voxel_scatter"; });
  m.def("has_dispatcher_ops", []() { return true; });
}
