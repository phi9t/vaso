#include <fstream>
#include <iostream>
#include <memory>
#include <string>

#include "google/protobuf/descriptor.h"
#include "google/protobuf/descriptor.pb.h"
#include "google/protobuf/dynamic_message.h"
#include "google/protobuf/message.h"

int main(int argc, char** argv) {
  if (argc != 3) {
    std::cerr << "usage: write_message <descriptor.pb> <out.pb>\n";
    return 2;
  }

  std::ifstream descriptor_in(argv[1], std::ios::binary);
  google::protobuf::FileDescriptorSet file_set;
  if (!file_set.ParseFromIstream(&descriptor_in)) {
    std::cerr << "failed to parse descriptor set\n";
    return 1;
  }

  google::protobuf::DescriptorPool pool;
  for (const google::protobuf::FileDescriptorProto& file_proto : file_set.file()) {
    if (pool.BuildFile(file_proto) == nullptr) {
      std::cerr << "failed to build descriptor: " << file_proto.name() << "\n";
      return 1;
    }
  }

  const google::protobuf::Descriptor* descriptor =
      pool.FindMessageTypeByName("vaso.synthetic.RoundTrip");
  if (descriptor == nullptr) {
    std::cerr << "missing vaso.synthetic.RoundTrip descriptor\n";
    return 1;
  }

  google::protobuf::DynamicMessageFactory factory(&pool);
  const google::protobuf::Message* prototype = factory.GetPrototype(descriptor);
  if (prototype == nullptr) {
    std::cerr << "failed to create RoundTrip prototype\n";
    return 1;
  }
  std::unique_ptr<google::protobuf::Message> msg(prototype->New());

  const google::protobuf::Reflection* reflection = msg->GetReflection();
  const google::protobuf::FieldDescriptor* label = descriptor->FindFieldByName("label");
  const google::protobuf::FieldDescriptor* values = descriptor->FindFieldByName("values");
  const google::protobuf::FieldDescriptor* nested = descriptor->FindFieldByName("nested");
  if (label == nullptr || values == nullptr || nested == nullptr) {
    std::cerr << "missing RoundTrip field descriptor\n";
    return 1;
  }

  reflection->SetString(msg.get(), label, "native-protobuf");
  reflection->AddInt32(msg.get(), values, 3);
  reflection->AddInt32(msg.get(), values, 21);
  reflection->AddInt32(msg.get(), values, 12);

  google::protobuf::Message* nested_msg = reflection->MutableMessage(msg.get(), nested);
  const google::protobuf::Descriptor* nested_descriptor = nested_msg->GetDescriptor();
  const google::protobuf::Reflection* nested_reflection = nested_msg->GetReflection();
  const google::protobuf::FieldDescriptor* nested_name =
      nested_descriptor->FindFieldByName("name");
  const google::protobuf::FieldDescriptor* nested_score =
      nested_descriptor->FindFieldByName("score");
  if (nested_name == nullptr || nested_score == nullptr) {
    std::cerr << "missing Nested field descriptor\n";
    return 1;
  }
  nested_reflection->SetString(nested_msg, nested_name, "cpp-writer");
  nested_reflection->SetInt32(nested_msg, nested_score, 42);

  std::ofstream out(argv[2], std::ios::binary);
  if (!msg->SerializeToOstream(&out)) {
    std::cerr << "failed to serialize roundtrip message\n";
    return 1;
  }
  return 0;
}
