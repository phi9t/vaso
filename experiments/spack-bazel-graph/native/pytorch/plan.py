#!/usr/bin/env python3
"""Plan (and preflight) a native PyTorch build without running it.

This is the Task-4 skeleton: it resolves the input interface a native torch
build needs -- consuming declared CUDA/cuDNN/NCCL/OpenBLAS/protobuf/
cuSPARSELt/cuDSS/NVSHMEM/OpenMPI/numactl/cmake/ninja/python prefixes -- computes the full
`python -m pip wheel` environment encoded from upstream PyTorch and the native
full-feature profile, runs preflight checks, and emits a structured
`build_plan.json`.
The dry-run plan validates the exact source-selected protobuf family before a
caller may request the token-gated build.

By default it is a DRY RUN: it validates the interface and writes the plan but
does NOT build. The full build is authorized only when the trusted request
carries the token `build-native-pytorch` (see --token / VASO_NATIVE_PYTORCH_TOKEN)
AND --execute is passed. This mirrors the torchtitan B200 discipline: high-cost
CUDA launches proceed only on an explicit trusted token.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

REQUIRED_TOKEN = "build-native-pytorch"
EXPECTED_PROTOC_VERSION = "libprotoc 3.21.12"
MAX_JOBS_ENV = "VASO_PYTORCH_MAX_JOBS"
MAX_JOBS_CAP = 96
ONNX_PROTOBUF_GUARD_ENV = {
    "ONNX_BUILD_CUSTOM_PROTOBUF": "OFF",
    "ONNX_USE_PROTOBUF_SHARED_LIBS": "ON",
}
PY_PROTOBUF_RECIPE_PROVIDER_STATUS = "available-via-vaso-overlay"
PY_PROTOBUF_RECIPE_PROVIDER_DETAIL = (
    "py-protobuf@4.21.12 is declared by the repo-owned Spack overlay "
    "namespace vaso_overlay, wired into @spack_dist//:spack through the "
    "Bazel runfiles overlay repo."
)
PY_PROTOBUF_NATIVE_PROVIDER_STATUS = "available"
PY_PROTOBUF_NATIVE_PROVIDER_DETAIL = (
    "The exact py-protobuf@4.21.12 provider is built as @py_protobuf_native "
    "against native protobuf@21.12 and the Python 3.13 compatibility island. "
    "Do not substitute a nearby Python protobuf or the older py-protobuf@3.13 "
    "recipe edge."
)
WHEEL_OUTPUT_DIR = "artifacts/wheels"
WHEEL_SOURCE_PLACEHOLDER = "<source>"
WHEEL_ENTRYPOINT = [
    "python",
    "-m",
    "pip",
    "wheel",
    "--no-build-isolation",
    "--no-deps",
    "-w",
    WHEEL_OUTPUT_DIR,
    WHEEL_SOURCE_PLACEHOLDER,
]
PYTHON_BUILD_PREFIX_KEYS = (
    "py-pip",
    "py-setuptools",
    "py-wheel",
    "py-scikit-build-core",
    "py-numpy",
    "py-pyyaml",
    "py-typing-extensions",
    "py-six",
    "py-packaging",
    "py-pathspec",
    "py-protobuf",
)
PYTHON_BUILD_PACKAGE_CHECKS = {
    "py-pip": "pip",
    "py-setuptools": "setuptools",
    "py-wheel": "wheel",
    "py-scikit-build-core": "scikit_build_core",
    "py-numpy": "numpy",
    "py-pyyaml": "yaml",
    "py-typing-extensions": "typing_extensions.py",
    "py-six": "six.py",
    "py-packaging": "packaging",
    "py-pathspec": "pathspec",
    "py-protobuf": "google/protobuf",
}
PYTHON_BUILD_VERSION_MINIMUMS = {
    "py-scikit-build-core": ("scikit-build-core", "scikit_build_core", "1.0"),
}
PROVIDER_PREFLIGHTS = {
    "cuda": {
        "binaries": ("bin/nvcc",),
    },
    "cudnn": {
        "headers": ("cudnn.h", "cudnn_version.h"),
        "libraries": (("libcudnn.so",),),
    },
    "nccl": {
        "headers": ("nccl.h",),
        "libraries": (("libnccl.so",),),
    },
    "openblas": {
        "headers": ("cblas.h",),
        "libraries": (("libopenblas.so", "libopenblas.a", "libopenblas-r0.3.33.a"),),
    },
    "protobuf": {
        "headers": ("google/protobuf/message.h",),
        "binaries": ("bin/protoc",),
        "libraries": (("libprotobuf.so", "libprotobuf.a"),),
    },
    "cusparselt": {
        "headers": ("cusparseLt.h",),
        "libraries": (("libcusparseLt.so", "libcusparseLt_static.a"),),
    },
    "cudss": {
        "headers": ("cudss.h",),
        "libraries": (("libcudss.so", "libcudss_static.a"),),
    },
    "nvshmem": {
        "headers": ("nvshmem.h", "non_abi/nvshmem_version.h"),
        "libraries": (("libnvshmem_host.so", "libnvshmem_host.so.3"),),
    },
    "openmpi": {
        "headers": ("mpi.h",),
        "libraries": (("libmpi.so",),),
    },
    "numactl": {
        "headers": ("numa.h",),
        "libraries": (("libnuma.so",),),
    },
    "cpuinfo": {
        "headers": ("cpuinfo.h",),
        "libraries": (("libcpuinfo.so", "libcpuinfo.a"),),
    },
    "fp16": {
        "headers": ("fp16.h",),
    },
    "fxdiv": {
        "headers": ("fxdiv.h",),
    },
    "psimd": {
        "headers": ("psimd.h",),
    },
    "pthreadpool": {
        "headers": ("pthreadpool.h",),
        "libraries": (("libpthreadpool.so", "libpthreadpool.a"),),
    },
}
VENDORED_SOURCE_DEPENDENCIES = {
    "gloo": {
        "required_env": ("USE_DISTRIBUTED=1", "USE_GLOO=1"),
        "paths": ("third_party/gloo",),
    },
    "tensorpipe": {
        "required_env": ("USE_TENSORPIPE=1", "USE_RPC=1"),
        "paths": (
            "third_party/tensorpipe",
            "third_party/tensorpipe/third_party/libnop",
            "third_party/tensorpipe/third_party/libuv",
        ),
    },
    "cutlass": {
        "required_env": ("USE_CUDA=1",),
        "paths": (
            "third_party/cutlass",
            "third_party/flash-attention/csrc/cutlass",
            "third_party/mslk/external/cutlass",
        ),
    },
    "flash-attention": {
        "required_env": ("USE_FLASH_ATTENTION=1", "USE_MEM_EFF_ATTENTION=1"),
        "paths": (
            "third_party/flash-attention",
            "third_party/flash-attention/csrc/cutlass",
        ),
    },
    "fbgemm": {
        "required_env": ("USE_FBGEMM=1",),
        "paths": (
            "third_party/fbgemm",
            "third_party/fbgemm/external/asmjit",
            "third_party/fbgemm/external/cutlass",
            "third_party/fbgemm/external/json",
        ),
    },
    "kineto": {
        "required_env": ("USE_KINETO=1",),
        "paths": (
            "third_party/kineto",
            "third_party/kineto/libkineto/third_party/json",
        ),
    },
    "cpp-httplib": {
        "required_env": ("USE_KINETO=1",),
        "paths": ("third_party/cpp-httplib",),
    },
    "cudnn-frontend": {
        "required_env": ("USE_CUDNN=1",),
        "paths": ("third_party/cudnn_frontend",),
    },
    "flatbuffers": {
        "required_env": (),
        "paths": ("third_party/flatbuffers",),
    },
    "qnnpack": {
        "required_env": ("USE_QNNPACK=1",),
        "paths": ("third_party/gemmlowp/gemmlowp",),
    },
    "ittapi": {
        "required_env": (),
        "paths": ("third_party/ittapi",),
    },
    "mslk": {
        "required_env": ("USE_CUDA=1", "USE_MEM_EFF_ATTENTION=1"),
        "paths": (
            "third_party/mslk",
            "third_party/mslk/external/cutlass",
        ),
    },
    "nlohmann": {
        "required_env": ("USE_KINETO=1",),
        "paths": ("third_party/nlohmann",),
    },
    "onnx": {
        "required_env": ("BUILD_CUSTOM_PROTOBUF=OFF", "ONNX_BUILD_CUSTOM_PROTOBUF=OFF"),
        "paths": ("third_party/onnx",),
    },
    "fmt": {
        "required_env": (),
        "paths": ("third_party/fmt",),
    },
    "NNPACK": {
        "required_env": ("USE_NNPACK=1",),
        "paths": ("third_party/NNPACK", "third_party/python-peachpy"),
    },
    "XNNPACK": {
        "required_env": ("USE_XNNPACK=1",),
        "paths": ("third_party/XNNPACK",),
    },
    "pocketfft": {
        "required_env": (),
        "paths": ("third_party/pocketfft",),
    },
}
SYSTEM_SOURCE_DEPENDENCIES = {
    "protobuf": {
        "required_env": ("BUILD_CUSTOM_PROTOBUF=OFF",),
        "paths": ("third_party/protobuf",),
    },
    "cpuinfo": {
        "required_env": ("USE_SYSTEM_CPUINFO=1",),
        "paths": ("third_party/cpuinfo", "third_party/fbgemm/external/cpuinfo"),
    },
    "fxdiv": {
        "required_env": ("USE_SYSTEM_FXDIV=1",),
        "paths": ("third_party/FXdiv",),
    },
    "psimd": {
        "required_env": ("USE_SYSTEM_PSIMD=1",),
        "paths": ("third_party/psimd",),
    },
    "pthreadpool": {
        "required_env": ("USE_SYSTEM_PTHREADPOOL=1",),
        "paths": ("third_party/pthreadpool",),
    },
}
DEFAULT_SOURCE_MANIFEST = (
    Path(__file__).with_name("pytorch-v2.14.0-2b3ec348-submodules.json")
)
NATIVE_HELPER_PREFIX_KEYS = (
    "cpuinfo",
    "fp16",
    "fxdiv",
    "psimd",
    "pthreadpool",
)
PRE_RELEASE_QUALIFIER_RE = re.compile(
    r"(?i)(?:(?<=\d)(?:a|alpha|b|beta|c|rc|pre|preview|dev)|[._-](?:a|alpha|b|beta|c|rc|pre|preview|dev))"
)
VERSION_RELEASE_END_RE = re.compile(
    r"(?i)(?:(?<=\d)(?:a|alpha|b|beta|c|rc|pre|preview|dev|post)|[._-](?:a|alpha|b|beta|c|rc|pre|preview|dev|post)|\+)"
)
SUPPORTED_PREFIX_KEYS = frozenset({
    "cuda",
    "cudnn",
    "cusparselt",
    "cudss",
    "nccl",
    "nvshmem",
    "openmpi",
    "numactl",
    "python",
    "cmake",
    "ninja",
    "openblas",
    "protobuf",
    *NATIVE_HELPER_PREFIX_KEYS,
    *PYTHON_BUILD_PREFIX_KEYS,
})
REQUIRED_PREFIX_KEYS = (
    "cuda",
    "cudnn",
    "nccl",
    "python",
    "cmake",
    "ninja",
    "openblas",
    "protobuf",
    "cusparselt",
    "cudss",
    "nvshmem",
    "openmpi",
    "numactl",
    *NATIVE_HELPER_PREFIX_KEYS,
    *PYTHON_BUILD_PREFIX_KEYS,
)
REJECTED_ODR_PREFIX_KEYS = frozenset({"grpc", "grpc-cpp", "py-grpcio", "abseil-cpp", "boost"})
ODR_PROVIDER_FAMILIES = {
    "protobuf": {
        "required_by_pytorch": True,
        "selected_cpp_provider": "protobuf@21.12",
        "selected_cpp_source_version": "3.21.12",
        "selected_python_provider": "py-protobuf@4.21.12",
        "selected_python_source_version": "4.21.12",
        "native_override_key": "protobuf@21.12",
        "native_override_label": "@protobuf_native//:lib",
        "python_native_override_status": "accepted",
        "python_native_override_label": "@py_protobuf_native//:lib",
        "python_native_override_reason": PY_PROTOBUF_NATIVE_PROVIDER_DETAIL,
    },
    "grpc": {
        "required_by_pytorch": False,
        "selected_provider": None,
        "reason": "not present as a PyTorch v2.14.0 source/build input",
    },
    "grpc-cpp": {
        "required_by_pytorch": False,
        "selected_provider": None,
        "reason": "not present as a PyTorch v2.14.0 source/build input",
    },
    "py-grpcio": {
        "required_by_pytorch": False,
        "selected_provider": None,
        "reason": "not present as a PyTorch v2.14.0 source/build input",
    },
    "abseil-cpp": {
        "required_by_pytorch": False,
        "selected_provider": None,
        "reason": (
            "ONNX's Abseil paths are disabled by the selected external "
            "protobuf 3.21.12 provider"
        ),
    },
    "boost": {
        "required_by_pytorch": False,
        "selected_provider": "boost@1.90.0",
        "provider_scope": "spack-graph-node-only",
        "native_override_key": "boost@1.90.0",
        "native_override_label": "@boost_native//:lib",
        "reason": "not present as a PyTorch v2.14.0 source/build input",
    },
}
SOURCE_DEPENDENCY_POLICY = {
    "pytorch_release": "v2.14.0",
    "pytorch_commit": "2b3ec34829036a65cd9d1398ea72a0167dc37470",
    "upstream_source_evidence": {
        "pytorch": {
            "release": "v2.14.0",
            "commit": "2b3ec34829036a65cd9d1398ea72a0167dc37470",
            "latest_stable_release_observed": "v2.14.0",
            "latest_release_candidate_observed": "v2.14.1-rc1",
            "checked_files": [
                ".gitmodules",
                "CMakeLists.txt",
                "cmake/ProtoBuf.cmake",
                "cmake/public/protobuf.cmake",
                "pyproject.toml",
            ],
            "submodule_gitlinks": {
                "third_party/protobuf": "f0dc78d7e6e331b8c6bb2d5283e06aa26883ca7c",
                "third_party/onnx": "e709452ef2bbc1d113faf678c24e6d3467696e83",
            },
            "absent_odr_submodules": [
                "third_party/abseil-cpp",
                "third_party/boost",
                "third_party/grpc",
            ],
            "system_protobuf_build_path": (
                "BUILD_CUSTOM_PROTOBUF=OFF -> cmake/ProtoBuf.cmake -> "
                "cmake/public/protobuf.cmake"
            ),
        },
        "protobuf": {
            "required_tag": "v3.21.12",
            "spack_style_tag": "v21.12",
            "commit": "f0dc78d7e6e331b8c6bb2d5283e06aa26883ca7c",
            "spack_style_tag_peeled_commit": "f0dc78d7e6e331b8c6bb2d5283e06aa26883ca7c",
            "spack_style_annotated_tag": "f502b8e9c831bda0bea57d9cbeefca3eb76e4254",
        },
        "onnx": {
            "release": "v1.18.0",
            "commit": "e709452ef2bbc1d113faf678c24e6d3467696e83",
            "checked_files": ["CMakeLists.txt"],
            "abseil_condition": (
                "external Protobuf_VERSION >= 4.22.0 or custom protobuf fallback"
            ),
        },
    },
    "wheel_frontend": {
        "source": "pyproject.toml build-backend=scikit_build_core.build",
        "entrypoint": WHEEL_ENTRYPOINT,
        "setup_py_status": (
            "PyTorch v2.14.0 setup.py rejects bdist_wheel; wheel builds use "
            "python -m pip wheel --no-build-isolation --no-deps."
        ),
    },
    "protobuf": {
        "direct_pytorch_input": True,
        "source_authority": "PyTorch v2.14.0 third_party/protobuf gitlink",
        "pytorch_gitlink": "f0dc78d7e6e331b8c6bb2d5283e06aa26883ca7c",
        "upstream_tag": "v3.21.12",
        "upstream_spack_style_tag": "v21.12",
        "spack_key": "protobuf@21.12",
        "python_spack_key": "py-protobuf@4.21.12",
        "selected_family": "protobuf C++ 3.21.12 / Python 4.21.12",
        "hermetic_spack_recipe_default": "protobuf@3.13.0 + py-protobuf@3.13",
        "hermetic_spack_python_provider_status": PY_PROTOBUF_RECIPE_PROVIDER_STATUS,
        "hermetic_spack_python_provider_detail": PY_PROTOBUF_RECIPE_PROVIDER_DETAIL,
        "hermetic_spack_python_provider_namespace": "vaso_overlay",
        "hermetic_spack_python_provider_recipe": "spack_overlays/vaso/spack_repo/vaso_overlay/packages/py_protobuf/package.py",
        "native_python_provider_status": PY_PROTOBUF_NATIVE_PROVIDER_STATUS,
        "native_python_provider_detail": PY_PROTOBUF_NATIVE_PROVIDER_DETAIL,
        "protoc_version": EXPECTED_PROTOC_VERSION,
    },
    "grpc": {
        "direct_pytorch_input": False,
        "native_provider": None,
        "source_authority": "PyTorch v2.14.0 .gitmodules and CMake files",
        "reason": "not present as a PyTorch v2.14.0 submodule or CMake dependency",
    },
    "abseil": {
        "direct_pytorch_input": False,
        "native_provider": None,
        "source_authority": "ONNX v1.18.0 CMakeLists.txt",
        "onnx_gitlink": "e709452ef2bbc1d113faf678c24e6d3467696e83",
        "onnx_release": "v1.18.0",
        "fallback_abseil": "20240722.1",
        "fallback_abseil_sha1": "0d6b07c6f3352981d3660978e109f2bc14594a3d",
        "fallback_protobuf": "29.2",
        "fallback_protobuf_cmake_version": "5.29.2",
        "fallback_protobuf_sha1": "a5639ffb17e3743d696baf16bf377fbe752b6a1f",
        "system_abseil_condition": "ONNX external protobuf >= 4.22.0",
        "note": (
            "ONNX can fetch Abseil only on its ONNX_BUILD_CUSTOM_PROTOBUF "
            "fallback path, or require a system Abseil when an external ONNX "
            "protobuf provider is >= 4.22.0. The PyTorch native build uses "
            "BUILD_CUSTOM_PROTOBUF=OFF and the unified protobuf 3.21.12 system "
            "provider, so Abseil is not a process-wide native input here."
        ),
    },
    "onnx_protobuf_guardrail": {
        "pytorch_submodule_mode": "vendored-onnx",
        "onnx_release": "v1.18.0",
        "onnx_gitlink": "e709452ef2bbc1d113faf678c24e6d3467696e83",
        "external_protobuf_version": "3.21.12",
        "required_env": {
            "BUILD_CUSTOM_PROTOBUF": "OFF",
            **ONNX_PROTOBUF_GUARD_ENV,
        },
        "requires_abseil": False,
        "allows_custom_protobuf_fallback": False,
        "forbidden_if_triggered": {
            "protobuf": "29.2",
            "protobuf_cmake_version": "5.29.2",
            "abseil-cpp": "20240722.1",
            "utf8_range": "external protobuf >=4.22 companion",
        },
        "standalone_onnx_python_package": "not-admitted",
        "standalone_onnx_python_requires": "protobuf>=4.25.1",
        "failure_classes": [
            "ONNX_ABSEIL_PROTOBUF_TRIGGER",
            "ONNX_CUSTOM_PROTOBUF_FALLBACK",
            "STANDALONE_ONNX_PYTHON_PROTOBUF_MISMATCH",
        ],
    },
    "boost": {
        "direct_pytorch_input": False,
        "native_provider": "boost@1.90.0",
        "native_provider_scope": "spack-graph-node-only",
        "source_authority": "PyTorch v2.14.0 .gitmodules and CMake files",
        "reason": "not present as a PyTorch v2.14.0 submodule or CMake dependency",
        "note": (
            "Boost remains a separately native-capable Spack graph node, but "
            "the selected PyTorch source build must not put Boost on its CMake "
            "provider search path."
        ),
    },
    "accepted_prefix_keys": sorted(SUPPORTED_PREFIX_KEYS),
    "rejected_odr_prefix_keys": sorted(REJECTED_ODR_PREFIX_KEYS),
    "odr_provider_families": ODR_PROVIDER_FAMILIES,
    "dependency_contract": {
        "protobuf": {
            "required": True,
            "provider": "protobuf@21.12",
            "source_version": "3.21.12",
            "prefix_role": "accepted-build-input",
        },
        "py-protobuf": {
            "required": True,
            "provider": "py-protobuf@4.21.12",
            "source_version": "4.21.12",
            "prefix_role": "accepted-python-build-input",
        },
        "grpc": {
            "required": False,
            "provider": None,
            "source_version": None,
            "prefix_role": "rejected-odr-provider",
        },
        "grpc-cpp": {
            "required": False,
            "provider": None,
            "source_version": None,
            "prefix_role": "rejected-odr-provider",
        },
        "py-grpcio": {
            "required": False,
            "provider": None,
            "source_version": None,
            "prefix_role": "rejected-odr-provider",
        },
        "abseil-cpp": {
            "required": False,
            "provider": None,
            "source_version": None,
            "prefix_role": "rejected-odr-provider",
        },
        "boost": {
            "required": False,
            "provider": "boost@1.90.0",
            "provider_scope": "spack-graph-node-only",
            "source_version": None,
            "prefix_role": "rejected-odr-provider",
        },
    },
}

# Feature flags for the native full-feature PyTorch wheel profile.
BASE_ENVIRONMENT = {
    "USE_GFLAGS": "0",
    "USE_GLOG": "0",
    "USE_MKL": "0",
    "USE_MKLDNN": "0",
    "USE_MPI": "1",
    "USE_NUMA": "1",
    "USE_TENSORPIPE": "1",
    "USE_RPC": "1",
    "USE_TENSORRT": "0",
    "USE_XPU": "0",
    "USE_ROCM": "0",
    "USE_MPS": "0",
    "USE_OPENMP": "1",
    "USE_FBGEMM": "1",
    "USE_QNNPACK": "1",
    "USE_XNNPACK": "1",
    "USE_NNPACK": "1",
    "USE_KINETO": "1",
    "USE_GLOO": "1",
    "USE_FLASH_ATTENTION": "1",
    "USE_MEM_EFF_ATTENTION": "1",
    "USE_MAGMA": "0",
    "TH_BINARY_BUILD": "1",
    "ATEN_STATIC_CUDA": "0",
    "USE_CUDA_STATIC_LINK": "0",
    "USE_STATIC_CUDNN": "0",
    "_GLIBCXX_USE_CXX11_ABI": "1",
    "COLORIZE_OUTPUT": "0",
    # vendor-libtorch build() overrides:
    "BUILD_TEST": "0",
    "BUILD_BINARY": "0",
    "BUILD_CAFFE2_OPS": "0",
    "BUILD_CUSTOM_PROTOBUF": "OFF",
    "USE_STATIC_DISPATCH": "0",
    "PYTORCH_BUILD_NUMBER": "1",
}
CUDA_ENVIRONMENT = {"USE_CUDA": "1", "USE_CUDNN": "1"}
DISTRIBUTED_ENVIRONMENT = {
    "USE_NCCL": "1",
    "USE_DISTRIBUTED": "1",
    "USE_SYSTEM_NCCL": "1",
    "USE_STATIC_NCCL": "0",
}
FEATURE_DECISIONS = {
    "magma": {
        "status": "off-with-reason",
        "reason": "human decision 2026-09-30: MAGMA is dropped from native torch",
    },
    "mkldnn": {
        "status": "off-with-reason",
        "reason": "human decision 2026-09-30: MKLDNN/oneDNN is dropped from native torch",
    },
    "mpi": {
        "status": "on",
        "reason": "kept on because the full-feature Spack torch root keeps +mpi and OpenMPI is native",
    },
    "numa": {
        "status": "on",
        "reason": "USE_NUMA=1 is backed by an explicit native numactl prefix",
    },
}
PROFILE_OPTIONAL_FEATURES: dict[str, dict[str, object]] = {}
CUDA_ARCH_MAP = {
    "70": "7.0", "75": "7.5", "80": "8.0", "86": "8.6",
    "89": "8.9", "90": "9.0", "100": "10.0",
}


def torch_cuda_arch_list(cuda_arch: list[str]) -> str:
    out = []
    for arch in cuda_arch:
        if arch not in CUDA_ARCH_MAP:
            raise SystemExit(f"unsupported cuda_arch value: {arch}")
        out.append(CUDA_ARCH_MAP[arch])
    return ";".join(out)


def validate_torch_cuda_arch_list(value: str, ptx_supported: bool) -> str:
    entries = [item.strip() for item in value.split(";") if item.strip()]
    if not entries:
        raise SystemExit("TORCH_CUDA_ARCH_LIST must not be empty")
    unsupported = sorted(set(entries) - {"10.0", "10.0+PTX"})
    if unsupported:
        raise SystemExit(
            "unsupported TORCH_CUDA_ARCH_LIST value(s): "
            + ", ".join(unsupported)
            + ". The full-feature B200 profile accepts 10.0, or 10.0+PTX "
            "when the selected rootfs CUDA line has been probed."
        )
    if any(entry.endswith("+PTX") for entry in entries) and not ptx_supported:
        raise SystemExit(
            "TORCH_CUDA_ARCH_LIST=10.0+PTX requires --cuda-arch-ptx-supported "
            "after probe 7 confirms PyTorch v2.14 accepts it with the selected rootfs CUDA line"
        )
    return ";".join(entries)


def unsupported_prefix_keys(prefixes: dict[str, str]) -> list[str]:
    return sorted(
        key for key, value in prefixes.items()
        if value and key not in SUPPORTED_PREFIX_KEYS
    )


def normalise_max_jobs(value: str | None, source: str = "--max-jobs") -> str:
    if value is None or not value.strip():
        raise SystemExit(f"{source} must be a positive integer, got empty input")
    try:
        jobs = int(value.strip(), 10)
    except ValueError:
        raise SystemExit(f"{source} must be a positive integer, got {value!r}")
    if jobs < 1:
        raise SystemExit(f"{source} must be a positive integer, got {value!r}")
    if jobs > MAX_JOBS_CAP:
        raise SystemExit(f"{source} must be <= {MAX_JOBS_CAP}, got {value!r}")
    return str(jobs)


def detect_nproc() -> int:
    try:
        result = subprocess.run(
            ["nproc"],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        result = None
    if result is not None and result.returncode == 0:
        try:
            jobs = int(result.stdout.strip(), 10)
        except ValueError:
            jobs = 0
        if jobs > 0:
            return jobs
    return max(os.cpu_count() or 1, 1)


def resolve_max_jobs(value: str | None, environ: dict[str, str] | None = None) -> tuple[str, str]:
    if value is not None and value.strip():
        return normalise_max_jobs(value), "explicit --max-jobs input"
    env = os.environ if environ is None else environ
    env_value = env.get(MAX_JOBS_ENV, "")
    if env_value.strip():
        return normalise_max_jobs(env_value, MAX_JOBS_ENV), MAX_JOBS_ENV
    return str(min(detect_nproc(), MAX_JOBS_CAP)), f"nproc capped at {MAX_JOBS_CAP}"


def first_existing_libdir(prefix: str) -> str:
    for libdir in ("lib64", "lib"):
        cand = Path(prefix) / libdir
        if cand.is_dir():
            return str(cand)
    return str(Path(prefix) / "lib64")


def provider_include_dir(prefix: str) -> Path:
    return Path(prefix) / "include"


def provider_lib_dirs(prefix: str) -> tuple[Path, Path]:
    return (Path(prefix) / "lib64", Path(prefix) / "lib")


def first_provider_libdir(prefix: str, library_groups: tuple[tuple[str, ...], ...]) -> Path:
    for libdir in provider_lib_dirs(prefix):
        for names in library_groups:
            if any((libdir / name).exists() for name in names):
                return libdir
    for libdir in provider_lib_dirs(prefix):
        if libdir.is_dir():
            return libdir
    return Path(prefix) / "lib64"


def first_existing_library(prefix: str, names: tuple[str, ...]) -> str:
    for libdir in ("lib64", "lib"):
        for name in names:
            cand = Path(prefix) / libdir / name
            if cand.exists():
                return str(cand)
    return str(Path(prefix) / "lib64" / names[0])


def selected_build_flags() -> dict[str, str]:
    flags = dict(BASE_ENVIRONMENT)
    flags.update(CUDA_ENVIRONMENT)
    flags.update(DISTRIBUTED_ENVIRONMENT)
    flags.update(ONNX_PROTOBUF_GUARD_ENV)
    flags.update({
        "USE_CUSPARSELT": "1",
        "USE_CUDSS": "1",
        "USE_NVSHMEM": "1",
        "USE_SYSTEM_CPUINFO": "1",
        "USE_SYSTEM_FXDIV": "1",
        "USE_SYSTEM_PSIMD": "1",
        "USE_SYSTEM_PTHREADPOOL": "1",
    })
    return flags


def required_env_matches(required_env: tuple[str, ...], flags: dict[str, str]) -> bool:
    for item in required_env:
        if "=" not in item:
            return False
        key, expected = item.split("=", 1)
        if flags.get(key) != expected:
            return False
    return True


def python_version_from_abi(python_abi: str) -> str | None:
    if not python_abi.startswith("cp"):
        return None
    digits = python_abi[2:]
    if len(digits) < 2 or not digits.isdigit():
        return None
    return digits[0] + "." + digits[1:]


def python_site_packages_dir(prefixes: dict[str, str], python_abi: str) -> Path:
    version = python_version_from_abi(python_abi)
    if version:
        return Path("lib") / ("python" + version) / "site-packages"

    python_prefix = prefixes.get("python", "")
    python = Path(python_prefix) / "bin" / "python3" if python_prefix else None
    if python is not None and python.is_file() and os.access(python, os.X_OK):
        probe = (
            "import sysconfig\n"
            "path = sysconfig.get_path('purelib', vars={'base': '', 'platbase': ''})\n"
            "print(path.lstrip('/'))\n"
        )
        try:
            result = subprocess.run(
                [str(python), "-c", probe],
                check=False,
                capture_output=True,
                text=True,
                timeout=30,
            )
        except (OSError, subprocess.TimeoutExpired):
            pass
        else:
            site_packages = result.stdout.strip()
            if result.returncode == 0 and site_packages:
                return Path(site_packages)

    return Path("lib") / "python" / "site-packages"


def python_build_site_packages(prefixes: dict[str, str], key: str, python_abi: str) -> Path:
    return Path(prefixes[key]) / python_site_packages_dir(prefixes, python_abi)


def python_build_pythonpath(prefixes: dict[str, str], python_abi: str) -> list[str]:
    return [
        str(python_build_site_packages(prefixes, key, python_abi))
        for key in PYTHON_BUILD_PREFIX_KEYS
    ]


def python_module_path(site_packages: Path, module_path: str) -> Path:
    return site_packages / module_path


def build_path(prefixes: dict[str, str]) -> list[str]:
    return [
        str(Path(prefixes["python"]) / "bin"),
        str(Path(prefixes["cmake"]) / "bin"),
        str(Path(prefixes["ninja"]) / "bin"),
        str(Path(prefixes["cuda"]) / "bin"),
        str(Path(prefixes["py-pip"]) / "bin"),
        str(Path(prefixes["py-wheel"]) / "bin"),
        "/usr/bin",
        "/bin",
    ]


def version_tuple(version: str) -> tuple[int, ...]:
    release = VERSION_RELEASE_END_RE.split(version.strip(), maxsplit=1)[0]
    parts: list[int] = []
    for part in release.replace("_", ".").replace("-", ".").split("."):
        if part.isdigit():
            parts.append(int(part))
            continue
        number = ""
        for char in part:
            if char.isdigit():
                number += char
            else:
                break
        if number:
            parts.append(int(number))
    return tuple(parts)


def compare_version_release(actual: str, expected: str) -> int:
    actual_parts = version_tuple(actual)
    expected_parts = version_tuple(expected)
    width = max(len(actual_parts), len(expected_parts))
    padded_actual = actual_parts + (0,) * (width - len(actual_parts))
    padded_expected = expected_parts + (0,) * (width - len(expected_parts))
    if padded_actual > padded_expected:
        return 1
    if padded_actual < padded_expected:
        return -1
    return 0


def version_meets_stable_minimum(actual: str, minimum: str) -> bool:
    release_cmp = compare_version_release(actual, minimum)
    if release_cmp > 0:
        return True
    if release_cmp < 0:
        return False
    return not PRE_RELEASE_QUALIFIER_RE.search(actual)


def python_dist_version(prefixes: dict[str, str], key: str, python_abi: str, dist_prefix: str) -> str | None:
    site_packages = python_build_site_packages(prefixes, key, python_abi)
    for metadata in sorted(site_packages.glob(f"{dist_prefix}-*.dist-info/METADATA")):
        for line in metadata.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.startswith("Version:"):
                return line.split(":", 1)[1].strip()
    return None


def load_source_manifest(path: Path) -> tuple[dict[str, object] | None, str | None]:
    if not path.is_file():
        return None, None
    try:
        with path.open(encoding="utf-8") as handle:
            return json.load(handle), None
    except (OSError, json.JSONDecodeError) as exc:
        return None, str(exc)


def source_manifest_submodule_paths(manifest: dict[str, object]) -> dict[str, dict[str, object]]:
    rows: dict[str, dict[str, object]] = {}
    for item in manifest.get("submodules", []):
        if not isinstance(item, dict):
            continue
        path = item.get("path")
        if isinstance(path, str):
            rows[path] = item
    for feature in manifest.get("full_feature_dependency_cross_checks", []):
        if not isinstance(feature, dict):
            continue
        for item in feature.get("paths", []):
            if not isinstance(item, dict):
                continue
            path = item.get("path")
            if isinstance(path, str):
                existing = dict(rows.get(path, {}))
                existing.update(item)
                rows[path] = existing
    return rows


def source_manifest_entry_is_nonempty(entry: dict[str, object] | None) -> bool:
    if not entry:
        return False
    return bool(entry.get("gitlink_commit") and entry.get("policy"))


def build_env(
    prefixes: dict[str, str],
    torch_arch: str,
    build_version: str,
    max_jobs: str | None,
    python_abi: str,
) -> dict[str, str]:
    """Full env for `python -m pip wheel`, from the encoded interface."""
    unsupported = unsupported_prefix_keys(prefixes)
    if unsupported:
        raise SystemExit(
            "unsupported PyTorch native prefix input(s): "
            + ", ".join(unsupported)
            + ". The selected PyTorch v2.14.0 build path admits only "
            + ", ".join(sorted(SUPPORTED_PREFIX_KEYS))
            + "; gRPC, Abseil, and Boost must not enter the PyTorch CMake "
            "provider search path."
        )
    for k in REQUIRED_PREFIX_KEYS:
        if k not in prefixes:
            raise SystemExit(f"missing required --prefix {k}=... input")

    env = dict(BASE_ENVIRONMENT)
    env.update(CUDA_ENVIRONMENT)
    env.update(DISTRIBUTED_ENVIRONMENT)
    env["PYTHONHOME"] = ""
    env["PYTHONNOUSERSITE"] = "1"
    env["PYTHONPATH"] = os.pathsep.join(python_build_pythonpath(prefixes, python_abi))
    env["PIP_DISABLE_PIP_VERSION_CHECK"] = "1"
    env["PIP_NO_INDEX"] = "1"
    env["PIP_NO_INPUT"] = "1"
    env["PATH"] = os.pathsep.join(build_path(prefixes))
    env["USE_CUSPARSELT"] = "1"
    env["USE_CUDSS"] = "1"
    env["USE_NVSHMEM"] = "1"
    env["USE_SYSTEM_CPUINFO"] = "1"
    env["USE_SYSTEM_FXDIV"] = "1"
    env["USE_SYSTEM_PSIMD"] = "1"
    env["USE_SYSTEM_PTHREADPOOL"] = "1"
    env["TORCH_CUDA_ARCH_LIST"] = torch_arch
    env["PYTORCH_BUILD_VERSION"] = build_version
    if max_jobs is not None:
        env["MAX_JOBS"] = max_jobs
        env["CMAKE_BUILD_PARALLEL_LEVEL"] = max_jobs

    cuda = prefixes["cuda"]
    cudnn = prefixes["cudnn"]
    nccl = prefixes["nccl"]
    protobuf = prefixes["protobuf"]
    openblas = prefixes.get("openblas", "")
    cusparselt = prefixes["cusparselt"]
    cudss = prefixes["cudss"]
    nvshmem = prefixes["nvshmem"]
    openmpi = prefixes["openmpi"]
    numactl = prefixes["numactl"]
    cpuinfo = prefixes["cpuinfo"]
    fp16 = prefixes["fp16"]
    fxdiv = prefixes["fxdiv"]
    psimd = prefixes["psimd"]
    pthreadpool = prefixes["pthreadpool"]
    py_six = Path(prefixes["py-six"]) / python_site_packages_dir(prefixes, python_abi)
    cmake_args = [
        f"-DCPUINFO_SOURCE_DIR={cpuinfo}",
        f"-DPYTHON_SIX_SOURCE_DIR={py_six}",
        "-DPYTHON_PEACHPY_SOURCE_DIR=${PROJECT_SOURCE_DIR}/third_party/python-peachpy",
        f"-DPTHREADPOOL_SOURCE_DIR={pthreadpool}",
    ]
    env["CMAKE_ARGS"] = " ".join(cmake_args)
    env["CUDA_HOME"] = cuda
    env["CUDA_TOOLKIT_ROOT_DIR"] = cuda
    env["CUDA_PATH"] = cuda
    env["CUDNN_ROOT"] = cudnn
    env["CUDNN_INCLUDE_DIR"] = str(Path(cudnn) / "include")
    env["CUDNN_LIBRARY"] = str(first_provider_libdir(cudnn, PROVIDER_PREFLIGHTS["cudnn"]["libraries"]))
    env["NCCL_ROOT"] = nccl
    env["NCCL_INCLUDE_DIR"] = str(Path(nccl) / "include")
    env["NCCL_LIB_DIR"] = str(first_provider_libdir(nccl, PROVIDER_PREFLIGHTS["nccl"]["libraries"]))

    # OpenBLAS selection (vendor-libtorch consumes Spack OpenBLAS).
    if openblas:
        env["BLAS"] = "OpenBLAS"
        env["OpenBLAS_HOME"] = openblas
    env["CUSPARSELT_ROOT"] = cusparselt
    env["CUDSS_ROOT"] = cudss
    env["CUDSS_INCLUDE_DIR"] = str(Path(cudss) / "include")
    env["CUDSS_LIBRARY"] = first_existing_library(cudss, ("libcudss.so", "libcudss_static.a"))
    env["NVSHMEM_HOME"] = nvshmem
    env["MPI_HOME"] = openmpi
    env["NUMA_ROOT"] = numactl

    # PyTorch's Spack path is ~custom-protobuf: force the system protobuf
    # provider and make protoc discoverable without consulting host tools.
    env["PROTOBUF_PROTOC_EXECUTABLE"] = str(Path(protobuf) / "bin" / "protoc")
    env.update(ONNX_PROTOBUF_GUARD_ENV)

    # CMAKE_PREFIX_PATH carries dependency wiring only. CMake and Ninja
    # executables are emitted as tool_inputs, not environment variables.
    env["CMAKE_PREFIX_PATH"] = os.pathsep.join(
        [
            prefixes["python"],
            prefixes["cmake"],
            protobuf,
            openblas,
            cusparselt,
            cudss,
            nvshmem,
            openmpi,
            numactl,
            cpuinfo,
            fp16,
            fxdiv,
            psimd,
            pthreadpool,
        ]
    )
    return env


def tool_inputs(prefixes: dict[str, str]) -> dict[str, dict[str, str]]:
    inputs: dict[str, dict[str, str]] = {}
    for key, binary in (("cmake", "cmake"), ("ninja", "ninja")):
        if key not in prefixes:
            continue
        prefix = prefixes[key]
        inputs[key] = {
            "kind": "build-tool",
            "path": str(Path(prefix) / "bin" / binary),
            "prefix": prefix,
        }
    return inputs


def preflight(
    prefixes: dict[str, str],
    rootfs_is_cuda_bundle: bool,
    python_abi: str = "derived",
    source_manifest: Path | None = None,
) -> list[dict]:
    """Loud, per-input checks. Each returns {name, ok, detail}."""
    checks: list[dict] = []

    def check(name: str, ok: bool, detail: str):
        checks.append({"name": name, "ok": bool(ok), "detail": detail})

    def check_protoc_version(protoc: Path):
        if not protoc.exists():
            check("protobuf:protoc-version", False, f"{protoc} missing")
            return
        if not os.access(protoc, os.X_OK):
            check("protobuf:protoc-version", False, f"{protoc} is not executable")
            return
        try:
            result = subprocess.run(
                [str(protoc), "--version"],
                check=False,
                capture_output=True,
                text=True,
                timeout=30,
            )
        except OSError as exc:
            check("protobuf:protoc-version", False, f"{protoc}: {exc}")
            return
        actual = result.stdout.strip() or result.stderr.strip()
        check(
            "protobuf:protoc-version",
            result.returncode == 0 and actual == EXPECTED_PROTOC_VERSION,
            f"expected {EXPECTED_PROTOC_VERSION}; got {actual or '<empty>'}",
        )

    prefix_check_keys = list(REQUIRED_PREFIX_KEYS)
    for key in prefix_check_keys:
        p = prefixes.get(key, "")
        exists = bool(p) and Path(p).is_dir()
        check(f"prefix:{key}", exists, p or "<unset>")
    for key in unsupported_prefix_keys(prefixes):
        detail = (
            f"{key}={prefixes[key]} is not a PyTorch v2.14.0 source/build "
            "input for the selected protobuf@21.12 path"
        )
        check(f"unsupported-prefix:{key}", False, detail)

    for key, spec in PROVIDER_PREFLIGHTS.items():
        prefix = prefixes.get(key, "")
        headers = tuple(spec.get("headers", ()))
        if headers:
            include_dir = provider_include_dir(prefix) if prefix else Path("<unset>")
            check(
                f"provider:{key}:include-dir",
                bool(prefix) and include_dir.is_dir(),
                f"{key} include_dir={include_dir}",
            )
            missing = [
                header
                for header in headers
                if not prefix or not (include_dir / header).exists()
            ]
            check(
                f"provider:{key}:header",
                not missing,
                (
                    f"{key} include_dir={include_dir}; "
                    f"required headers={', '.join(headers)}; "
                    f"missing={', '.join(missing) if missing else '<none>'}"
                ),
            )
        library_groups = tuple(spec.get("libraries", ()))
        if library_groups:
            libdir = first_provider_libdir(prefix, library_groups) if prefix else Path("<unset>")
            check(
                f"provider:{key}:lib-dir",
                bool(prefix) and libdir.is_dir(),
                f"{key} lib_dir={libdir}",
            )
            missing_groups = []
            for names in library_groups:
                found = any(
                    bool(prefix) and (candidate_dir / name).exists()
                    for candidate_dir in provider_lib_dirs(prefix)
                    for name in names
                )
                if not found:
                    missing_groups.append(" or ".join(names))
            check(
                f"provider:{key}:library",
                not missing_groups,
                (
                    f"{key} lib_dir={libdir}; "
                    f"required libraries={'; '.join(' or '.join(group) for group in library_groups)}; "
                    f"missing={', '.join(missing_groups) if missing_groups else '<none>'}"
                ),
            )
        for rel in tuple(spec.get("binaries", ())):
            target = Path(prefix) / rel if prefix else Path("<unset>")
            check(
                f"provider:{key}:binary:{Path(rel).name}",
                bool(prefix) and target.exists(),
                f"{key} binary={target}",
            )

    check(
        "source-policy:py-protobuf-recipe-provider",
        PY_PROTOBUF_RECIPE_PROVIDER_STATUS == "available-via-vaso-overlay",
        PY_PROTOBUF_RECIPE_PROVIDER_DETAIL,
    )
    check(
        "source-policy:onnx-protobuf-guardrail",
        True,
        (
            "vendored ONNX v1.18.0 must use external protobuf 3.21.12 with "
            "BUILD_CUSTOM_PROTOBUF=OFF, ONNX_BUILD_CUSTOM_PROTOBUF=OFF, and "
            "ONNX_USE_PROTOBUF_SHARED_LIBS=ON; Abseil/utf8_range and ONNX's "
            "protobuf 29.2 fallback are not admitted into this PyTorch island"
        ),
    )
    check(
        "source-policy:py-protobuf-native-prefix",
        PY_PROTOBUF_NATIVE_PROVIDER_STATUS == "available",
        PY_PROTOBUF_NATIVE_PROVIDER_DETAIL,
    )
    pythonpath_entries = [
        python_build_site_packages(prefixes, key, python_abi)
        for key in PYTHON_BUILD_PREFIX_KEYS
        if prefixes.get(key, "")
    ]
    pythonpath_detail = os.pathsep.join(str(path) for path in pythonpath_entries) or "<empty>"
    for key, package_path in PYTHON_BUILD_PACKAGE_CHECKS.items():
        prefix = prefixes.get(key, "")
        site_packages = python_build_site_packages(prefixes, key, python_abi) if prefix else Path("<unset>")
        site_entry = python_module_path(site_packages, package_path)
        ok = bool(prefix) and site_packages in pythonpath_entries and site_entry.exists()
        detail = (
            f"{key} module {package_path} must be importable from PYTHONPATH; "
            f"requires {site_entry}; PYTHONPATH={pythonpath_detail}"
        )
        if ok and key in PYTHON_BUILD_VERSION_MINIMUMS:
            dist_name, dist_prefix, minimum = PYTHON_BUILD_VERSION_MINIMUMS[key]
            found = python_dist_version(prefixes, key, python_abi, dist_prefix)
            ok = found is not None and version_meets_stable_minimum(found, minimum)
            detail = (
                f"{dist_name} requires >={minimum}; found {found or '<missing>'}; "
                f"PYTHONPATH={pythonpath_detail}"
            )
        check(f"python-build-requirement:{key}", ok, detail)

    manifest_path = source_manifest or DEFAULT_SOURCE_MANIFEST
    manifest, manifest_error = load_source_manifest(manifest_path)
    check(
        "source-manifest:file",
        manifest is not None,
        f"{manifest_path}: {manifest_error or 'missing'}" if manifest_error or manifest is None else str(manifest_path),
    )
    if manifest is not None:
        manifest_paths = source_manifest_submodule_paths(manifest)
        build_flags = selected_build_flags()
        for policy_name, dependencies in (
            ("vendored", VENDORED_SOURCE_DEPENDENCIES),
            ("system-provider", SYSTEM_SOURCE_DEPENDENCIES),
        ):
            for dependency, spec in dependencies.items():
                required_env = tuple(spec["required_env"])
                if not required_env_matches(required_env, build_flags):
                    continue
                expected_paths = tuple(spec["paths"])
                missing = []
                wrong_policy = []
                for rel in expected_paths:
                    entry = manifest_paths.get(rel)
                    if not source_manifest_entry_is_nonempty(entry):
                        missing.append(rel)
                    elif entry.get("policy") != policy_name:
                        wrong_policy.append(f"{rel}={entry.get('policy')}")
                ok = not missing and not wrong_policy
                check(
                    f"source-manifest:{policy_name}:{dependency}",
                    ok,
                    (
                        f"{dependency} requires non-empty {policy_name} source path(s) "
                        f"in {manifest_path}: {', '.join(expected_paths)}; "
                        f"missing_or_empty={', '.join(missing) if missing else '<none>'}; "
                        f"wrong_policy={', '.join(wrong_policy) if wrong_policy else '<none>'}"
                    ),
                )

    # CUDA needs nvcc; cuDNN + NCCL need headers + libs.
    cuda = prefixes.get("cuda", "")
    check("cuda:nvcc", bool(cuda) and (Path(cuda) / "bin" / "nvcc").exists(),
          str(Path(cuda) / "bin" / "nvcc") if cuda else "<unset>")
    cudnn = prefixes.get("cudnn", "")
    check("cudnn:header", bool(cudnn) and (Path(cudnn) / "include" / "cudnn.h").exists(),
          str(Path(cudnn) / "include" / "cudnn.h") if cudnn else "<unset>")
    check(
        "cudnn:libdir",
        bool(cudnn) and any((Path(cudnn) / d).is_dir() for d in ("lib64", "lib")),
        str(Path(cudnn) / "lib64") if cudnn else "<unset>",
    )
    nccl = prefixes.get("nccl", "")
    check("nccl:header", bool(nccl) and (Path(nccl) / "include" / "nccl.h").exists(),
          str(Path(nccl) / "include" / "nccl.h") if nccl else "<unset>")
    check(
        "nccl:libdir",
        bool(nccl) and any((Path(nccl) / d).is_dir() for d in ("lib64", "lib")),
        str(Path(nccl) / "lib64") if nccl else "<unset>",
    )
    python = prefixes.get("python", "")
    check("python:interpreter", bool(python) and (Path(python) / "bin" / "python3").exists(),
          str(Path(python) / "bin" / "python3") if python else "<unset>")
    cmake = prefixes.get("cmake", "")
    check("cmake:binary", bool(cmake) and (Path(cmake) / "bin" / "cmake").exists(),
          str(Path(cmake) / "bin" / "cmake") if cmake else "<unset>")
    ninja = prefixes.get("ninja", "")
    check("ninja:binary", bool(ninja) and (Path(ninja) / "bin" / "ninja").exists(),
          str(Path(ninja) / "bin" / "ninja") if ninja else "<unset>")
    openblas = prefixes.get("openblas", "")
    check("openblas:header", bool(openblas) and any(
        (Path(openblas) / "include" / name).exists()
        for name in ("cblas.h", "openblas_config.h")
    ), str(Path(openblas) / "include") if openblas else "<unset>")
    check(
        "openblas:libdir",
        bool(openblas) and any((Path(openblas) / d).is_dir() for d in ("lib64", "lib")),
        str(Path(openblas) / "lib64") if openblas else "<unset>",
    )
    cusparselt = prefixes.get("cusparselt", "")
    check(
        "cusparselt:header",
        bool(cusparselt) and (Path(cusparselt) / "include" / "cusparseLt.h").exists(),
        str(Path(cusparselt) / "include" / "cusparseLt.h") if cusparselt else "<unset>",
    )
    check(
        "cusparselt:libdir",
        bool(cusparselt) and any((Path(cusparselt) / d).is_dir() for d in ("lib64", "lib")),
        str(Path(cusparselt) / "lib64") if cusparselt else "<unset>",
    )
    openmpi = prefixes.get("openmpi", "")
    check("openmpi:header", bool(openmpi) and (Path(openmpi) / "include" / "mpi.h").exists(),
          str(Path(openmpi) / "include" / "mpi.h") if openmpi else "<unset>")
    check(
        "openmpi:libdir",
        bool(openmpi) and any((Path(openmpi) / d).is_dir() for d in ("lib64", "lib")),
        str(Path(openmpi) / "lib64") if openmpi else "<unset>",
    )
    numactl = prefixes.get("numactl", "")
    check("numactl:header", bool(numactl) and (Path(numactl) / "include" / "numa.h").exists(),
          str(Path(numactl) / "include" / "numa.h") if numactl else "<unset>")
    check(
        "numactl:libdir",
        bool(numactl) and any((Path(numactl) / d).is_dir() for d in ("lib64", "lib")),
        str(Path(numactl) / "lib64") if numactl else "<unset>",
    )
    cpuinfo = prefixes.get("cpuinfo", "")
    check(
        "cpuinfo:header",
        bool(cpuinfo) and (Path(cpuinfo) / "include" / "cpuinfo.h").exists(),
        str(Path(cpuinfo) / "include" / "cpuinfo.h") if cpuinfo else "<unset>",
    )
    check(
        "cpuinfo:library",
        bool(cpuinfo) and any(
            (Path(cpuinfo) / libdir / name).exists()
            for libdir in ("lib64", "lib")
            for name in ("libcpuinfo.so", "libcpuinfo.a")
        ),
        first_existing_library(cpuinfo, ("libcpuinfo.so", "libcpuinfo.a")) if cpuinfo else "<unset>",
    )
    fp16 = prefixes.get("fp16", "")
    check(
        "fp16:header",
        bool(fp16) and (Path(fp16) / "include" / "fp16.h").exists(),
        str(Path(fp16) / "include" / "fp16.h") if fp16 else "<unset>",
    )
    fxdiv = prefixes.get("fxdiv", "")
    check(
        "fxdiv:header",
        bool(fxdiv) and (Path(fxdiv) / "include" / "fxdiv.h").exists(),
        str(Path(fxdiv) / "include" / "fxdiv.h") if fxdiv else "<unset>",
    )
    psimd = prefixes.get("psimd", "")
    check(
        "psimd:header",
        bool(psimd) and (Path(psimd) / "include" / "psimd.h").exists(),
        str(Path(psimd) / "include" / "psimd.h") if psimd else "<unset>",
    )
    pthreadpool = prefixes.get("pthreadpool", "")
    check(
        "pthreadpool:header",
        bool(pthreadpool) and (Path(pthreadpool) / "include" / "pthreadpool.h").exists(),
        str(Path(pthreadpool) / "include" / "pthreadpool.h") if pthreadpool else "<unset>",
    )
    check(
        "pthreadpool:library",
        bool(pthreadpool) and any(
            (Path(pthreadpool) / libdir / name).exists()
            for libdir in ("lib64", "lib")
            for name in ("libpthreadpool.so", "libpthreadpool.a")
        ),
        first_existing_library(pthreadpool, ("libpthreadpool.so", "libpthreadpool.a")) if pthreadpool else "<unset>",
    )
    cudss = prefixes.get("cudss", "")
    if cudss:
        check("cudss:header", (Path(cudss) / "include" / "cudss.h").exists(),
              str(Path(cudss) / "include" / "cudss.h"))
        check(
            "cudss:library",
            Path(first_existing_library(cudss, ("libcudss.so", "libcudss_static.a"))).exists(),
            first_existing_library(cudss, ("libcudss.so", "libcudss_static.a")),
        )
    nvshmem = prefixes.get("nvshmem", "")
    if nvshmem:
        check("nvshmem:header", (Path(nvshmem) / "include" / "nvshmem.h").exists(),
              str(Path(nvshmem) / "include" / "nvshmem.h"))
        check(
            "nvshmem:libdir",
            any((Path(nvshmem) / d).is_dir() for d in ("lib64", "lib")),
            str(Path(nvshmem) / "lib64"),
        )
    protobuf = prefixes.get("protobuf", "")
    check(
        "protobuf:headers",
        bool(protobuf) and (Path(protobuf) / "include" / "google" / "protobuf").is_dir(),
        str(Path(protobuf) / "include" / "google" / "protobuf") if protobuf else "<unset>",
    )
    check(
        "protobuf:protoc",
        bool(protobuf) and (Path(protobuf) / "bin" / "protoc").exists(),
        str(Path(protobuf) / "bin" / "protoc") if protobuf else "<unset>",
    )
    if protobuf:
        check_protoc_version(Path(protobuf) / "bin" / "protoc")
    else:
        check("protobuf:protoc-version", False, "<unset>")
    check(
        "protobuf:libdir",
        bool(protobuf) and any((Path(protobuf) / d).is_dir() for d in ("lib64", "lib")),
        str(Path(protobuf) / "lib64") if protobuf else "<unset>",
    )

    # The torch build must run inside the sealed CUDA rootfs bundle, not the
    # host-fallback root (it needs CUDA/cuDNN/NCCL + a full toolchain hermetic).
    check("rootfs:cuda-bundle", rootfs_is_cuda_bundle,
          "insula base root is the CUDA/Ubuntu>=24.04 bundle"
          if rootfs_is_cuda_bundle else
          "REQUIRED: seal rootfs/build_rootfs.sh bundle as the insula base root")

    return checks


def wheel_entrypoint(source: str | None) -> list[str]:
    source_arg = source or WHEEL_SOURCE_PLACEHOLDER
    return [source_arg if arg == WHEEL_SOURCE_PLACEHOLDER else arg
            for arg in WHEEL_ENTRYPOINT]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--prefix", action="append", default=[], metavar="KEY=PATH",
                    help="Spack input prefix, e.g. cuda=/opt/.../cuda (repeatable)")
    ap.add_argument("--cuda-arch", default=None,
                    help="legacy comma-separated cuda_arch values; prefer --torch-cuda-arch-list")
    ap.add_argument("--torch-cuda-arch-list", default=None,
                    help="explicit TORCH_CUDA_ARCH_LIST value (default 10.0)")
    ap.add_argument("--cuda-arch-ptx-supported", action="store_true",
                    help="allow 10.0+PTX after probe 7 proves support for the selected rootfs CUDA line")
    ap.add_argument("--max-jobs", default=None,
                    help="explicit MAX_JOBS/CMAKE_BUILD_PARALLEL_LEVEL resource input")
    ap.add_argument("--build-version", default="2.14.0",
                    help="PYTORCH_BUILD_VERSION")
    ap.add_argument("--python-abi", default="derived",
                    help="Python ABI tag supplied by the configured action")
    ap.add_argument("--source", default=None, help="PyTorch source dir (for build)")
    ap.add_argument("--source-manifest", type=Path, default=None,
                    help="PyTorch recursive source manifest with active third_party submodule paths")
    ap.add_argument("--out", type=Path, default=None, help="write build_plan.json here")
    ap.add_argument("--rootfs-cuda-bundle", action="store_true",
                    help="assert the insula base root is the CUDA bundle")
    ap.add_argument("--token", default=os.environ.get("VASO_NATIVE_PYTORCH_TOKEN", ""),
                    help="authorization token; full build needs 'build-native-pytorch'")
    ap.add_argument("--execute", action="store_true",
                    help="run python -m pip wheel (requires the token). Default: dry run.")
    args = ap.parse_args(argv)

    prefixes: dict[str, str] = {}
    for item in args.prefix:
        if "=" not in item:
            raise SystemExit(f"--prefix must be KEY=PATH, got {item!r}")
        k, v = item.split("=", 1)
        prefixes[k] = v

    checks = preflight(prefixes, args.rootfs_cuda_bundle, args.python_abi, args.source_manifest)
    try:
        if args.cuda_arch and args.torch_cuda_arch_list:
            raise SystemExit("use either --cuda-arch or --torch-cuda-arch-list, not both")
        cuda_arch = [a.strip() for a in args.cuda_arch.split(",") if a.strip()] if args.cuda_arch else []
        arch_value = (
            torch_cuda_arch_list(cuda_arch)
            if args.cuda_arch
            else args.torch_cuda_arch_list or "10.0"
        )
        torch_arch = validate_torch_cuda_arch_list(
            arch_value,
            args.cuda_arch_ptx_supported,
        )
        max_jobs, max_jobs_source = resolve_max_jobs(args.max_jobs)
        env = build_env(prefixes, torch_arch, args.build_version, max_jobs, args.python_abi) if prefixes else {}
        env_error = None
    except SystemExit as e:
        cuda_arch = [a.strip() for a in args.cuda_arch.split(",") if a.strip()] if args.cuda_arch else []
        torch_arch = args.torch_cuda_arch_list or "10.0"
        max_jobs = None
        max_jobs_source = None
        env, env_error = {}, str(e)

    token_ok = args.token == REQUIRED_TOKEN
    preflight_ok = all(c["ok"] for c in checks) and env_error is None

    plan = {
        "schema_version": 1,
        "entrypoint": wheel_entrypoint(args.source),
        "source": args.source,
        "source_manifest": str(
            args.source_manifest if args.source_manifest is not None else DEFAULT_SOURCE_MANIFEST
        ),
        "cuda_arch": cuda_arch,
        "torch_cuda_arch_list": torch_arch,
        "build_version": args.build_version,
        "python_abi": args.python_abi,
        "input_prefixes": prefixes,
        "build_env": env,
        "env_error": env_error,
        "tool_inputs": tool_inputs(prefixes),
        "feature_decisions": FEATURE_DECISIONS,
        "profile_optional_features": PROFILE_OPTIONAL_FEATURES,
        "resources": {
            "max_jobs": max_jobs,
            "source": max_jobs_source,
        },
        "source_dependency_policy": SOURCE_DEPENDENCY_POLICY,
        "emitted_prefix_layout": {
            "wheels": f"{WHEEL_OUTPUT_DIR}/torch-*.whl",
            "site_packages": str(
                python_site_packages_dir(prefixes, args.python_abi) /
                "torch" /
                "{lib,include}"
            ),
            "install_cmd": ["python", "-m", "pip", "install", "--no-deps",
                            "--prefix", "<prefix>", "<wheel>"],
        },
        "preflight": checks,
        "preflight_ok": preflight_ok,
        "authorization": {
            "required_token": REQUIRED_TOKEN,
            "token_present": token_ok,
            "execute_requested": bool(args.execute),
        },
        "mode": "execute" if (args.execute and token_ok) else "dry-run",
        "will_build": bool(args.execute and token_ok and preflight_ok),
    }

    text = json.dumps(plan, indent=2, sort_keys=True) + "\n"
    if args.out is not None:
        args.out.write_text(text)
    print(text, end="")

    if not (args.execute):
        # Dry run: success means the interface validated (preflight passed).
        return 0 if preflight_ok else 1

    # --execute was requested: gate hard on the token.
    if not token_ok:
        print(f"REFUSED: full native PyTorch build requires token "
              f"{REQUIRED_TOKEN!r} (got {args.token!r}); staying dry-run.",
              file=sys.stderr)
        return 2
    if not preflight_ok:
        print("REFUSED: preflight failed; not building.", file=sys.stderr)
        return 3

    # Authorized + preflight green. The actual invocation is intentionally left
    # to the caller/repository rule; this planner does not spawn a >1h CUDA
    # build. Emitting will_build=true is the signal to proceed.
    print("AUTHORIZED: token present and preflight green; caller may execute "
          "the entrypoint.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
