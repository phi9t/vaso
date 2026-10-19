#include <iostream>
#include <string>
#include <vector>

#include "geom_core/camera.h"
#include "torch/profile_config.h"
#include "torch/cuda.h"
#include "torch/torch.h"

namespace {

using vaso_consumers::geom::CameraIntrinsics;
using vaso_consumers::geom::PinholeCamera;

CameraIntrinsics CalibrationFixture() {
  CameraIntrinsics intrinsics;
  intrinsics.fx = 500.0;
  intrinsics.fy = 520.0;
  intrinsics.cx = 320.0;
  intrinsics.cy = 240.0;
  intrinsics.width = 640;
  intrinsics.height = 480;
  intrinsics.k1 = 0.1;
  intrinsics.k2 = -0.05;
  intrinsics.p1 = 0.001;
  intrinsics.p2 = -0.002;
  intrinsics.k3 = 0.01;
  return intrinsics;
}

torch::Tensor ProjectWithTorch(const torch::Tensor& points, const CameraIntrinsics& intrinsics) {
  const torch::Tensor x = points.select(1, 0) / points.select(1, 2);
  const torch::Tensor y = points.select(1, 1) / points.select(1, 2);
  const torch::Tensor r2 = x * x + y * y;
  const torch::Tensor r4 = r2 * r2;
  const torch::Tensor r6 = r4 * r2;
  const torch::Tensor radial = 1.0 + intrinsics.k1 * r2 + intrinsics.k2 * r4 + intrinsics.k3 * r6;
  const torch::Tensor xy = x * y;
  const torch::Tensor x_tangential = 2.0 * intrinsics.p1 * xy + intrinsics.p2 * (r2 + 2.0 * x * x);
  const torch::Tensor y_tangential = intrinsics.p1 * (r2 + 2.0 * y * y) + 2.0 * intrinsics.p2 * xy;
  const torch::Tensor u = intrinsics.fx * (x * radial + x_tangential) + intrinsics.cx;
  const torch::Tensor v = intrinsics.fy * (y * radial + y_tangential) + intrinsics.cy;
  return torch::stack({u, v}, 1);
}

bool ArgEquals(const char* arg, const char* prefix, std::string* value) {
  const std::string text(arg);
  const std::string key(prefix);
  if (text.rfind(key, 0) != 0) {
    return false;
  }
  *value = text.substr(key.size());
  return true;
}

}  // namespace

int main(int argc, char** argv) {
  std::string device_name = "cpu";
  for (int i = 1; i < argc; ++i) {
    if (!ArgEquals(argv[i], "--device=", &device_name)) {
      std::cerr << "unknown argument: " << argv[i] << "\n";
      return 2;
    }
  }

  torch::Device device(torch::kCPU);
  if (device_name == "cuda") {
    if (!torch::cuda::is_available()) {
      std::cerr << "CUDA requested but torch reports no CUDA device\n";
      return 1;
    }
    device = torch::Device(torch::kCUDA);
  } else if (device_name != "cpu") {
    std::cerr << "unknown device: " << device_name << "\n";
    return 2;
  }

  const CameraIntrinsics intrinsics = CalibrationFixture();
  const PinholeCamera camera(intrinsics);
  const std::vector<Eigen::Vector3d> points = {
      Eigen::Vector3d(0.2, -0.1, 2.0),
      Eigen::Vector3d(-0.3, 0.4, 4.0),
      Eigen::Vector3d(0.0, 0.0, 1.5),
      Eigen::Vector3d(0.5, 0.25, 3.0),
  };

  torch::Tensor point_tensor = torch::empty({static_cast<long>(points.size()), 3},
                                            torch::TensorOptions().dtype(torch::kFloat64).device(device));
  {
    torch::Tensor cpu_points = torch::empty({static_cast<long>(points.size()), 3}, torch::kFloat64);
    auto accessor = cpu_points.accessor<double, 2>();
    for (std::size_t i = 0; i < points.size(); ++i) {
      accessor[i][0] = points[i].x();
      accessor[i][1] = points[i].y();
      accessor[i][2] = points[i].z();
    }
    point_tensor.copy_(cpu_points.to(device));
  }

  const torch::Tensor projected = ProjectWithTorch(point_tensor, intrinsics).to(torch::kCPU);
  const auto accessor = projected.accessor<double, 2>();
  for (std::size_t i = 0; i < points.size(); ++i) {
    const Eigen::Vector2d expected = camera.Project(points[i]);
    const double du = std::abs(accessor[i][0] - expected.x());
    const double dv = std::abs(accessor[i][1] - expected.y());
    if (du > 1e-9 || dv > 1e-9) {
      std::cerr << "projection mismatch at row " << i << ": got (" << accessor[i][0] << ", "
                << accessor[i][1] << "), expected (" << expected.x() << ", " << expected.y() << ")\n";
      return 1;
    }
  }

  std::cout << "libtorch_cli " << device_name << " projected " << points.size() << " points\n";
  return 0;
}
