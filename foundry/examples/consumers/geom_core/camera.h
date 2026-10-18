#ifndef EXAMPLES_CONSUMERS_GEOM_CORE_CAMERA_H_
#define EXAMPLES_CONSUMERS_GEOM_CORE_CAMERA_H_

#include <string>

#include "Eigen/Core"
#include "absl/status/statusor.h"
#include "geom_core/calibration.pb.h"

namespace vaso_consumers {
namespace geom {

using CalibrationProto = ::vaso::consumers::geom::CameraCalibration;

struct CameraIntrinsics {
  double fx = 1.0;
  double fy = 1.0;
  double cx = 0.0;
  double cy = 0.0;
  int width = 0;
  int height = 0;
  double k1 = 0.0;
  double k2 = 0.0;
  double p1 = 0.0;
  double p2 = 0.0;
  double k3 = 0.0;
};

class PinholeCamera {
 public:
  explicit PinholeCamera(const CameraIntrinsics& intrinsics);

  Eigen::Vector2d Project(const Eigen::Vector3d& point_camera) const;
  Eigen::Vector3d Unproject(const Eigen::Vector2d& pixel, double depth) const;
  Eigen::Vector2d Undistort(const Eigen::Vector2d& distorted_normalized) const;

  CalibrationProto ToProto() const;
  std::string CompressCalibration() const;

  static absl::StatusOr<PinholeCamera> FromProto(const CalibrationProto& proto);
  static absl::StatusOr<PinholeCamera> FromCompressedCalibration(const std::string& compressed);

  const CameraIntrinsics& intrinsics() const { return intrinsics_; }

 private:
  Eigen::Vector2d Distort(const Eigen::Vector2d& normalized) const;

  CameraIntrinsics intrinsics_;
};

}  // namespace geom
}  // namespace vaso_consumers

#endif  // EXAMPLES_CONSUMERS_GEOM_CORE_CAMERA_H_
