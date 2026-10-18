#include <google/protobuf/stubs/common.h>

#include <cstdio>

int main() {
  std::printf("protobuf:%d:%s\n", GOOGLE_PROTOBUF_VERSION,
              google::protobuf::internal::VersionString(GOOGLE_PROTOBUF_VERSION).c_str());
  google::protobuf::ShutdownProtobufLibrary();
  return 0;
}
