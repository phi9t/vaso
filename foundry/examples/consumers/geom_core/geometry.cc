#include "geom_core/geometry.h"

#include <algorithm>
#include <cmath>

namespace vaso_consumers {
namespace geom {
namespace {

constexpr double kSmallAngle = 1e-10;

Eigen::Matrix3d LeftJacobianSO3(const Eigen::Vector3d& omega) {
  const double theta = omega.norm();
  const Eigen::Matrix3d omega_hat = Hat(omega);
  const Eigen::Matrix3d omega_hat_sq = omega_hat * omega_hat;
  if (theta < kSmallAngle) {
    return Eigen::Matrix3d::Identity() + 0.5 * omega_hat + (1.0 / 6.0) * omega_hat_sq;
  }
  const double theta_sq = theta * theta;
  return Eigen::Matrix3d::Identity() +
         ((1.0 - std::cos(theta)) / theta_sq) * omega_hat +
         ((theta - std::sin(theta)) / (theta_sq * theta)) * omega_hat_sq;
}

Eigen::Matrix3d LeftJacobianSO3Inverse(const Eigen::Vector3d& omega) {
  const double theta = omega.norm();
  const Eigen::Matrix3d omega_hat = Hat(omega);
  const Eigen::Matrix3d omega_hat_sq = omega_hat * omega_hat;
  if (theta < kSmallAngle) {
    return Eigen::Matrix3d::Identity() - 0.5 * omega_hat + (1.0 / 12.0) * omega_hat_sq;
  }
  const double theta_sq = theta * theta;
  return Eigen::Matrix3d::Identity() - 0.5 * omega_hat +
         ((1.0 / theta_sq) - ((1.0 + std::cos(theta)) / (2.0 * theta * std::sin(theta)))) *
             omega_hat_sq;
}

}  // namespace

Eigen::Matrix3d Hat(const Eigen::Vector3d& vector) {
  Eigen::Matrix3d matrix;
  matrix << 0.0, -vector.z(), vector.y(),
      vector.z(), 0.0, -vector.x(),
      -vector.y(), vector.x(), 0.0;
  return matrix;
}

Eigen::Vector3d Vee(const Eigen::Matrix3d& matrix) {
  return Eigen::Vector3d(matrix(2, 1), matrix(0, 2), matrix(1, 0));
}

SO3::SO3() : rotation_(Eigen::Matrix3d::Identity()) {}

SO3::SO3(const Eigen::Matrix3d& rotation) : rotation_(rotation) {}

SO3 SO3::Exp(const Eigen::Vector3d& tangent) {
  const double theta = tangent.norm();
  const Eigen::Matrix3d tangent_hat = Hat(tangent);
  const Eigen::Matrix3d tangent_hat_sq = tangent_hat * tangent_hat;
  if (theta < kSmallAngle) {
    return SO3(Eigen::Matrix3d::Identity() + tangent_hat + 0.5 * tangent_hat_sq);
  }
  const double theta_sq = theta * theta;
  return SO3(Eigen::Matrix3d::Identity() +
             (std::sin(theta) / theta) * tangent_hat +
             ((1.0 - std::cos(theta)) / theta_sq) * tangent_hat_sq);
}

Eigen::Vector3d SO3::Log() const {
  const double cos_theta = std::clamp((rotation_.trace() - 1.0) * 0.5, -1.0, 1.0);
  const double theta = std::acos(cos_theta);
  if (theta < kSmallAngle) {
    return Vee(0.5 * (rotation_ - rotation_.transpose()));
  }
  return Vee((theta / (2.0 * std::sin(theta))) * (rotation_ - rotation_.transpose()));
}

SO3 SO3::Compose(const SO3& other) const {
  return SO3(rotation_ * other.rotation_);
}

SO3 SO3::Inverse() const {
  return SO3(rotation_.transpose());
}

SE3::SE3() : rotation_(Eigen::Matrix3d::Identity()), translation_(Eigen::Vector3d::Zero()) {}

SE3::SE3(const Eigen::Matrix3d& rotation, const Eigen::Vector3d& translation)
    : rotation_(rotation), translation_(translation) {}

SE3 SE3::Exp(const Vec6& tangent) {
  const Eigen::Vector3d rho = tangent.head<3>();
  const Eigen::Vector3d omega = tangent.tail<3>();
  const Eigen::Matrix3d rotation = SO3::Exp(omega).Matrix();
  const Eigen::Vector3d translation = LeftJacobianSO3(omega) * rho;
  return SE3(rotation, translation);
}

Vec6 SE3::Log() const {
  Vec6 tangent;
  const Eigen::Vector3d omega = SO3(rotation_).Log();
  tangent.head<3>() = LeftJacobianSO3Inverse(omega) * translation_;
  tangent.tail<3>() = omega;
  return tangent;
}

SE3 SE3::Compose(const SE3& other) const {
  return SE3(rotation_ * other.rotation_, translation_ + rotation_ * other.translation_);
}

SE3 SE3::Inverse() const {
  const Eigen::Matrix3d inverse_rotation = rotation_.transpose();
  return SE3(inverse_rotation, -(inverse_rotation * translation_));
}

Matrix6d SE3::Adjoint() const {
  Matrix6d adjoint = Matrix6d::Zero();
  adjoint.topLeftCorner<3, 3>() = rotation_;
  adjoint.topRightCorner<3, 3>() = Hat(translation_) * rotation_;
  adjoint.bottomRightCorner<3, 3>() = rotation_;
  return adjoint;
}

Eigen::Matrix4d SE3::Matrix() const {
  Eigen::Matrix4d matrix = Eigen::Matrix4d::Identity();
  matrix.topLeftCorner<3, 3>() = rotation_;
  matrix.topRightCorner<3, 1>() = translation_;
  return matrix;
}

}  // namespace geom
}  // namespace vaso_consumers
