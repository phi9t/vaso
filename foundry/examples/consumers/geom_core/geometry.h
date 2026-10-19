#ifndef EXAMPLES_CONSUMERS_GEOM_CORE_GEOMETRY_H_
#define EXAMPLES_CONSUMERS_GEOM_CORE_GEOMETRY_H_

#include "Eigen/Core"
#include "Eigen/Geometry"

namespace vaso_consumers {
namespace geom {

using Vec6 = Eigen::Matrix<double, 6, 1>;
using Matrix6d = Eigen::Matrix<double, 6, 6>;

Eigen::Matrix3d Hat(const Eigen::Vector3d& vector);
Eigen::Vector3d Vee(const Eigen::Matrix3d& matrix);

class SO3 {
 public:
  SO3();
  explicit SO3(const Eigen::Matrix3d& rotation);

  static SO3 Exp(const Eigen::Vector3d& tangent);

  Eigen::Vector3d Log() const;
  SO3 Compose(const SO3& other) const;
  SO3 Inverse() const;

  const Eigen::Matrix3d& Matrix() const { return rotation_; }

 private:
  Eigen::Matrix3d rotation_;
};

class SE3 {
 public:
  SE3();
  SE3(const Eigen::Matrix3d& rotation, const Eigen::Vector3d& translation);

  static SE3 Exp(const Vec6& tangent);

  Vec6 Log() const;
  SE3 Compose(const SE3& other) const;
  SE3 Inverse() const;
  Matrix6d Adjoint() const;
  Eigen::Matrix4d Matrix() const;

  const Eigen::Matrix3d& Rotation() const { return rotation_; }
  const Eigen::Vector3d& Translation() const { return translation_; }

 private:
  Eigen::Matrix3d rotation_;
  Eigen::Vector3d translation_;
};

}  // namespace geom
}  // namespace vaso_consumers

#endif  // EXAMPLES_CONSUMERS_GEOM_CORE_GEOMETRY_H_
