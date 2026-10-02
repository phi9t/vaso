#include <Eigen/Dense>
#include <Eigen/Geometry>
#include <Eigen/Version>

#include <iostream>

int main() {
  static_assert(EIGEN_WORLD_VERSION == 3, "Eigen world version drifted");
  static_assert(EIGEN_MAJOR_VERSION == 5, "Eigen major version drifted");
  static_assert(EIGEN_MINOR_VERSION == 0, "Eigen minor version drifted");
  static_assert(EIGEN_PATCH_VERSION == 1, "Eigen patch version drifted");

  Eigen::Matrix3d m;
  m << 1.0, 2.0, 3.0,
       0.0, 1.0, 4.0,
       5.0, 6.0, 0.0;
  Eigen::Vector3d v(2.0, -1.0, 0.5);
  Eigen::Vector3d out = m * v;
  Eigen::Quaterniond q(Eigen::AngleAxisd(0.0, Eigen::Vector3d::UnitZ()));

  std::cout << "eigen:"
            << EIGEN_VERSION_STRING << ":"
            << out.transpose() << ":"
            << q.norm()
            << std::endl;
  return 0;
}
