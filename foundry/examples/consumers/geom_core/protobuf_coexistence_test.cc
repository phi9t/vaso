#include <string>

#include "geom_core/calibration.pb.h"
#include "google/protobuf/descriptor.h"
#include "gtest/gtest.h"
#include "torch/torch.h"

namespace {

TEST(ProtobufCoexistenceTest, TorchAndConsumerProtobufLoadInOneProcess) {
  vaso::consumers::geom::CameraCalibration calibration;
  calibration.set_fx(500.0);
  calibration.set_fy(520.0);
  calibration.set_cx(320.0);
  calibration.set_cy(240.0);
  calibration.set_width(640);
  calibration.set_height(480);

  const std::string serialized = calibration.SerializeAsString();
  vaso::consumers::geom::CameraCalibration parsed;
  ASSERT_TRUE(parsed.ParseFromString(serialized));

  const google::protobuf::Descriptor* descriptor =
      google::protobuf::DescriptorPool::generated_pool()->FindMessageTypeByName(
          "vaso.consumers.geom.CameraCalibration");
  ASSERT_NE(descriptor, nullptr);
  EXPECT_EQ(descriptor->field_count(), 11);

  const torch::Tensor tensor = torch::ones({2, 2}, torch::TensorOptions().dtype(torch::kFloat64));
  EXPECT_DOUBLE_EQ(tensor.sum().item<double>(), 4.0);
}

}  // namespace
