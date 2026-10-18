"""C++ helpers for downstream consumers of the vaso torch profile."""

load("@rules_cc//cc:cc_binary.bzl", "cc_binary")
load("@rules_cc//cc:cc_library.bzl", "cc_library")
load("@rules_cc//cc:cc_test.bzl", "cc_test")

_TORCH_PREFIX = "$(VASO_TORCH_PREFIX)"
_TORCH_SITE_PACKAGES = _TORCH_PREFIX + "/lib/python3.13/site-packages"
_TORCH_INCLUDE = _TORCH_SITE_PACKAGES + "/torch/include"
_TORCH_API_INCLUDE = _TORCH_INCLUDE + "/torch/csrc/api/include"
_TORCH_LIB = _TORCH_SITE_PACKAGES + "/torch/lib"
_TORCH_PACKAGE_LIBS = _TORCH_SITE_PACKAGES + "/torch.libs"
_TORCH_CXX_STANDARD = "$(VASO_TORCH_CXX_STANDARD)"
_TORCH_CXX11_ABI = "$(VASO_TORCH_CXX11_ABI)"
_PROFILE_CONFIG = Label("//torch:profile_config")
_LIBTORCH_HEADERS = "@vaso_libtorch_profile//:headers"

_PROFILE_COPTS = [
    "-std=" + _TORCH_CXX_STANDARD,
    "-D_GLIBCXX_USE_CXX11_ABI=" + _TORCH_CXX11_ABI,
    "-DVASO_TORCH_EXPECTED_CXX11_ABI=" + _TORCH_CXX11_ABI,
]

_LIBTORCH_COPTS = _PROFILE_COPTS

_LIBTORCH_RUNTIME_LIB_DIRS = [
    _TORCH_LIB,
    _TORCH_PACKAGE_LIBS,
]

def _runtime_library_linkopts(lib_dirs):
    opts = []
    for lib_dir in lib_dirs:
        opts.extend([
            "-L" + lib_dir,
            "-Wl,-rpath," + lib_dir,
            "-Wl,-rpath-link," + lib_dir,
        ])
    return opts

def _libtorch_test_env(env):
    merged = dict(env)
    runtime_path = ":".join(_LIBTORCH_RUNTIME_LIB_DIRS + [
        "/run/nvidia-driver/lib",
        "/usr/local/cuda/lib64",
    ])
    existing = merged.get("LD_LIBRARY_PATH")
    if existing:
        merged["LD_LIBRARY_PATH"] = runtime_path + ":" + existing
    else:
        merged["LD_LIBRARY_PATH"] = runtime_path
    return merged

_LIBTORCH_LINKOPTS = _runtime_library_linkopts(_LIBTORCH_RUNTIME_LIB_DIRS) + [
    "-Wl,--no-as-needed",
    "-ltorch",
    "-ltorch_cpu",
    "-ltorch_cuda",
    "-lc10",
    "-lc10_cuda",
    "-Wl,--as-needed",
]

def _reject_consumer_profile_flags(name, copts):
    for flag in copts:
        if flag.startswith("-std=") or flag.startswith("-std:"):
            fail("{} must use the vaso torch profile C++ standard, not {}".format(name, flag))
        if "_GLIBCXX_USE_CXX11_ABI" in flag:
            fail("{} must use the vaso torch profile _GLIBCXX_USE_CXX11_ABI value, not {}".format(name, flag))

def _profile_deps(deps):
    return list(deps or []) + [_PROFILE_CONFIG]

def _libtorch_deps(deps):
    return _profile_deps(deps) + [_LIBTORCH_HEADERS]

def vaso_torch_profile_cc_library(name, copts = [], deps = [], **kwargs):
    _reject_consumer_profile_flags(name, copts)
    cc_library(
        name = name,
        copts = _PROFILE_COPTS + list(copts),
        deps = _profile_deps(deps),
        **kwargs
    )

def vaso_torch_profile_cc_test(name, copts = [], deps = [], **kwargs):
    _reject_consumer_profile_flags(name, copts)
    cc_test(
        name = name,
        copts = _PROFILE_COPTS + list(copts),
        deps = _profile_deps(deps),
        **kwargs
    )

def vaso_libtorch_cc_binary(name, copts = [], linkopts = [], deps = [], **kwargs):
    _reject_consumer_profile_flags(name, copts)
    cc_binary(
        name = name,
        copts = _LIBTORCH_COPTS + list(copts),
        linkopts = _LIBTORCH_LINKOPTS + list(linkopts),
        deps = _libtorch_deps(deps),
        **kwargs
    )

def vaso_libtorch_cc_test(name, copts = [], linkopts = [], deps = [], env = {}, **kwargs):
    _reject_consumer_profile_flags(name, copts)
    cc_test(
        name = name,
        copts = _LIBTORCH_COPTS + list(copts),
        env = _libtorch_test_env(env),
        linkopts = _LIBTORCH_LINKOPTS + list(linkopts),
        deps = _libtorch_deps(deps),
        **kwargs
    )
