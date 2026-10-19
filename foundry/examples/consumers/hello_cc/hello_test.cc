#include "hello_cc/hello_lib.h"

#include "gtest/gtest.h"

namespace {

TEST(HelloCcTest, UsesAbseilStringFormatting) {
  EXPECT_EQ(vaso_consumers::Greeting("consumer"), "hello, consumer from vaso_graph");
}

}  // namespace
