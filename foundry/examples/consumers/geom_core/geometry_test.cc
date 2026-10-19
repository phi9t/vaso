#include "geom_core/geometry.h"

#include <cmath>

#include "gtest/gtest.h"

namespace {

using vaso_consumers::geom::Matrix6d;
using vaso_consumers::geom::SE3;
using vaso_consumers::geom::Vec6;

void ExpectMatrixNear(const Eigen::MatrixXd& actual, const Eigen::MatrixXd& expected, double tolerance) {
  ASSERT_EQ(actual.rows(), expected.rows());
  ASSERT_EQ(actual.cols(), expected.cols());
  for (int row = 0; row < actual.rows(); ++row) {
    for (int col = 0; col < actual.cols(); ++col) {
      EXPECT_NEAR(actual(row, col), expected(row, col), tolerance) << "at (" << row << ", " << col << ")";
    }
  }
}

TEST(SE3Test, ExpOfLogRoundTripsTransform) {
  Vec6 tangent;
  tangent << 0.35, -0.20, 0.10, 0.04, -0.03, 0.08;

  const SE3 transform = SE3::Exp(tangent);
  const SE3 recovered = SE3::Exp(transform.Log());

  ExpectMatrixNear(recovered.Matrix(), transform.Matrix(), 1e-10);
}

TEST(SE3Test, ComposeWithInverseProducesIdentity) {
  Vec6 tangent;
  tangent << -0.15, 0.25, 0.40, -0.02, 0.07, -0.05;

  const SE3 transform = SE3::Exp(tangent);
  const SE3 identity = transform.Compose(transform.Inverse());

  ExpectMatrixNear(identity.Matrix(), Eigen::Matrix4d::Identity(), 1e-10);
}

TEST(SE3Test, AdjointMatchesHandComputedBlockForm) {
  Vec6 tangent;
  tangent << 0.5, -0.25, 0.75, 0.0, 0.0, 0.0;
  const SE3 transform = SE3::Exp(tangent);

  Matrix6d expected = Matrix6d::Zero();
  expected.topLeftCorner<3, 3>() = Eigen::Matrix3d::Identity();
  expected.topRightCorner<3, 3>() << 0.0, -0.75, -0.25,
      0.75, 0.0, -0.5,
      0.25, 0.5, 0.0;
  expected.bottomRightCorner<3, 3>() = Eigen::Matrix3d::Identity();

  ExpectMatrixNear(transform.Adjoint(), expected, 1e-12);
}

}  // namespace
