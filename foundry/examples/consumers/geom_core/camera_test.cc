#include "geom_core/camera.h"

#include <string>

#include "gtest/gtest.h"

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

TEST(PinholeCameraTest, HandComputedProjectionFixture) {
  const PinholeCamera camera(CalibrationFixture());
  const Eigen::Vector2d pixel = camera.Project(Eigen::Vector3d(0.2, -0.1, 2.0));

  EXPECT_NEAR(pixel.x(), 370.0246103515625, 1e-12);
  EXPECT_NEAR(pixel.y(), 213.9872026171875, 1e-12);
}

TEST(PinholeCameraTest, ProjectThenUnprojectRoundTripsPixelGrid) {
  const PinholeCamera camera(CalibrationFixture());

  for (double y : {32.0, 128.0, 240.0, 352.0, 448.0}) {
    for (double x : {24.0, 160.0, 320.0, 480.0, 616.0}) {
      const Eigen::Vector2d pixel(x, y);
      const Eigen::Vector3d point = camera.Unproject(pixel, 3.5);
      const Eigen::Vector2d round_trip = camera.Project(point);

      EXPECT_NEAR(round_trip.x(), pixel.x(), 2e-8) << "x=" << x << " y=" << y;
      EXPECT_NEAR(round_trip.y(), pixel.y(), 2e-8) << "x=" << x << " y=" << y;
    }
  }
}

TEST(PinholeCameraTest, CalibrationCompressesThroughZstdAndProtobuf) {
  const PinholeCamera camera(CalibrationFixture());
  const std::string compressed = camera.CompressCalibration();

  ASSERT_FALSE(compressed.empty());
  const auto restored = PinholeCamera::FromCompressedCalibration(compressed);
  ASSERT_TRUE(restored.ok()) << restored.status();

  const Eigen::Vector2d pixel = restored->Project(Eigen::Vector3d(0.2, -0.1, 2.0));
  EXPECT_NEAR(pixel.x(), 370.0246103515625, 1e-12);
  EXPECT_NEAR(pixel.y(), 213.9872026171875, 1e-12);
}

}  // namespace
