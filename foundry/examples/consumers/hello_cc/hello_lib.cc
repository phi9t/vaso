#include "hello_cc/hello_lib.h"

#include "absl/strings/str_cat.h"

namespace vaso_consumers {

std::string Greeting(const std::string& name) {
  return absl::StrCat("hello, ", name, " from vaso_graph");
}

}  // namespace vaso_consumers
