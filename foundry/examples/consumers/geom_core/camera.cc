#include "geom_core/camera.h"

#include <cstdint>
#include <limits>
#include <string>

#include "absl/status/status.h"
#include "zstd.h"

namespace vaso_consumers {
namespace geom {
namespace {

constexpr int kUndistortIterations = 12;
constexpr int kSizePrefixBytes = 8;

void AppendLittleEndianSize(std::uint64_t value, std::string* out) {
  for (int i = 0; i < kSizePrefixBytes; ++i) {
    out->push_back(static_cast<char>((value >> (8 * i)) & 0xff));
  }
}

absl::StatusOr<std::uint64_t> ReadLittleEndianSize(const std::string& bytes) {
  if (bytes.size() < kSizePrefixBytes) {
    return absl::InvalidArgumentError("compressed calibration is missing size prefix");
  }
  std::uint64_t value = 0;
  for (int i = 0; i < kSizePrefixBytes; ++i) {
    value |= static_cast<std::uint64_t>(static_cast<unsigned char>(bytes[i])) << (8 * i);
  }
  return value;
}

}  // namespace

PinholeCamera::PinholeCamera(const CameraIntrinsics& intrinsics) : intrinsics_(intrinsics) {}

Eigen::Vector2d PinholeCamera::Distort(const Eigen::Vector2d& normalized) const {
  const double x = normalized.x();
  const double y = normalized.y();
  const double r2 = x * x + y * y;
  const double r4 = r2 * r2;
  const double r6 = r4 * r2;
  const double radial = 1.0 + intrinsics_.k1 * r2 + intrinsics_.k2 * r4 + intrinsics_.k3 * r6;
  const double xy = x * y;
  const double x_tangential = 2.0 * intrinsics_.p1 * xy + intrinsics_.p2 * (r2 + 2.0 * x * x);
  const double y_tangential = intrinsics_.p1 * (r2 + 2.0 * y * y) + 2.0 * intrinsics_.p2 * xy;
  return Eigen::Vector2d(x * radial + x_tangential, y * radial + y_tangential);
}

Eigen::Vector2d PinholeCamera::Project(const Eigen::Vector3d& point_camera) const {
  const Eigen::Vector2d normalized(point_camera.x() / point_camera.z(), point_camera.y() / point_camera.z());
  const Eigen::Vector2d distorted = Distort(normalized);
  return Eigen::Vector2d(
      intrinsics_.fx * distorted.x() + intrinsics_.cx,
      intrinsics_.fy * distorted.y() + intrinsics_.cy);
}

Eigen::Vector3d PinholeCamera::Unproject(const Eigen::Vector2d& pixel, double depth) const {
  const Eigen::Vector2d distorted_normalized(
      (pixel.x() - intrinsics_.cx) / intrinsics_.fx,
      (pixel.y() - intrinsics_.cy) / intrinsics_.fy);
  const Eigen::Vector2d undistorted = Undistort(distorted_normalized);
  return Eigen::Vector3d(undistorted.x() * depth, undistorted.y() * depth, depth);
}

Eigen::Vector2d PinholeCamera::Undistort(const Eigen::Vector2d& distorted_normalized) const {
  Eigen::Vector2d estimate = distorted_normalized;
  for (int i = 0; i < kUndistortIterations; ++i) {
    const double x = estimate.x();
    const double y = estimate.y();
    const double r2 = x * x + y * y;
    const double r4 = r2 * r2;
    const double r6 = r4 * r2;
    const double radial = 1.0 + intrinsics_.k1 * r2 + intrinsics_.k2 * r4 + intrinsics_.k3 * r6;
    const double xy = x * y;
    const double x_tangential = 2.0 * intrinsics_.p1 * xy + intrinsics_.p2 * (r2 + 2.0 * x * x);
    const double y_tangential = intrinsics_.p1 * (r2 + 2.0 * y * y) + 2.0 * intrinsics_.p2 * xy;
    estimate.x() = (distorted_normalized.x() - x_tangential) / radial;
    estimate.y() = (distorted_normalized.y() - y_tangential) / radial;
  }
  return estimate;
}

CalibrationProto PinholeCamera::ToProto() const {
  CalibrationProto proto;
  proto.set_fx(intrinsics_.fx);
  proto.set_fy(intrinsics_.fy);
  proto.set_cx(intrinsics_.cx);
  proto.set_cy(intrinsics_.cy);
  proto.set_width(intrinsics_.width);
  proto.set_height(intrinsics_.height);
  proto.set_k1(intrinsics_.k1);
  proto.set_k2(intrinsics_.k2);
  proto.set_p1(intrinsics_.p1);
  proto.set_p2(intrinsics_.p2);
  proto.set_k3(intrinsics_.k3);
  return proto;
}

std::string PinholeCamera::CompressCalibration() const {
  const std::string serialized = ToProto().SerializeAsString();
  const std::size_t bound = ZSTD_compressBound(serialized.size());
  std::string payload;
  payload.reserve(kSizePrefixBytes + bound);
  AppendLittleEndianSize(static_cast<std::uint64_t>(serialized.size()), &payload);
  std::string compressed_payload(bound, '\0');
  const std::size_t compressed_size = ZSTD_compress(
      compressed_payload.data(), compressed_payload.size(), serialized.data(), serialized.size(), 1);
  if (ZSTD_isError(compressed_size)) {
    return std::string();
  }
  payload.append(compressed_payload.data(), compressed_size);
  return payload;
}

absl::StatusOr<PinholeCamera> PinholeCamera::FromProto(const CalibrationProto& proto) {
  CameraIntrinsics intrinsics;
  intrinsics.fx = proto.fx();
  intrinsics.fy = proto.fy();
  intrinsics.cx = proto.cx();
  intrinsics.cy = proto.cy();
  intrinsics.width = proto.width();
  intrinsics.height = proto.height();
  intrinsics.k1 = proto.k1();
  intrinsics.k2 = proto.k2();
  intrinsics.p1 = proto.p1();
  intrinsics.p2 = proto.p2();
  intrinsics.k3 = proto.k3();
  if (intrinsics.fx == 0.0 || intrinsics.fy == 0.0) {
    return absl::InvalidArgumentError("camera focal lengths must be non-zero");
  }
  return PinholeCamera(intrinsics);
}

absl::StatusOr<PinholeCamera> PinholeCamera::FromCompressedCalibration(const std::string& compressed) {
  absl::StatusOr<std::uint64_t> size_or = ReadLittleEndianSize(compressed);
  if (!size_or.ok()) {
    return size_or.status();
  }
  const std::uint64_t decompressed_size_u64 = *size_or;
  if (decompressed_size_u64 > static_cast<std::uint64_t>(std::numeric_limits<std::size_t>::max())) {
    return absl::InvalidArgumentError("compressed calibration is too large");
  }
  const std::size_t decompressed_size = static_cast<std::size_t>(decompressed_size_u64);
  std::string serialized(decompressed_size, '\0');
  const std::size_t result = ZSTD_decompress(
      serialized.data(), serialized.size(), compressed.data() + kSizePrefixBytes, compressed.size() - kSizePrefixBytes);
  if (ZSTD_isError(result)) {
    return absl::InvalidArgumentError(ZSTD_getErrorName(result));
  }
  if (result != decompressed_size) {
    return absl::InvalidArgumentError("compressed calibration size mismatch");
  }
  CalibrationProto proto;
  if (!proto.ParseFromString(serialized)) {
    return absl::InvalidArgumentError("compressed calibration protobuf parse failed");
  }
  return FromProto(proto);
}

}  // namespace geom
}  // namespace vaso_consumers
