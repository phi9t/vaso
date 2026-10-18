#ifndef TORCH_PROFILE_CONFIG_H_
#define TORCH_PROFILE_CONFIG_H_

#if __cplusplus < 202002L
#error "vaso torch profile requires C++20"
#endif

#ifndef _GLIBCXX_USE_CXX11_ABI
#error "vaso torch profile must define _GLIBCXX_USE_CXX11_ABI"
#endif

#ifndef VASO_TORCH_EXPECTED_CXX11_ABI
#error "vaso torch profile expected ABI value is missing"
#endif

#if _GLIBCXX_USE_CXX11_ABI != VASO_TORCH_EXPECTED_CXX11_ABI
#error "vaso torch profile _GLIBCXX_USE_CXX11_ABI mismatch"
#endif

namespace vaso_graph {
namespace torch_profile {

inline constexpr int kGlibcxxUseCxx11Abi = VASO_TORCH_EXPECTED_CXX11_ABI;

}  // namespace torch_profile
}  // namespace vaso_graph

#endif  // TORCH_PROFILE_CONFIG_H_
