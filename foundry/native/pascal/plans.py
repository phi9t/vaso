"""Build the additive Pascal plans from existing authoritative package profiles."""
from __future__ import annotations
import copy
import json
from pathlib import Path

PREFIX = "/opt/vaso/pascal"
WORKSPACE = PREFIX + "/foundry"
TORCH_ARCHIVE = "pytorch-v2.15.0-recursive-pascal.tar.zst"
TRITON_ARCHIVE = "triton-675c59878aa2280b31f722aaf42b825fcee21de8.tar.gz"
WHEEL_NAMES = {
    "torch": "torch-2.15.0+vaso.cu126.sm61-cp312-cp312-linux_x86_64.whl",
    "triton": "triton-3.8.0-cp312-cp312-linux_x86_64.whl",
}
# These are the complete existing Pascal provider boundaries, not new claims
# that CPython/LLVM/vendor SDKs were built by Bazel. Recursive third-party Torch
# sources (Gloo/Kineto/ONNX/SLEEF/etc.) remain in the pinned recursive archive.
PROVIDERS = {
    "system": {"prefix": "/usr", "scope": "Debian compiler, linker, libc, libstdc++, zlib and shell utilities"},
    "python": {"prefix": "/usr/local", "scope": "CPython 3.12 interpreter, headers and complete standard library"},
    "cuda": {"prefix": "/opt/vaso/cuda", "scope": "CUDA 12.6 compiler, headers, libraries, CUPTI, cuDNN 9.10.2.21 and NCCL 2.29.3 recursive headers"},
    "cmake": {"prefix": "/opt/vaso/cmake", "scope": "CMake 3.31 and complete modules"},
    "python-build": {"prefix": "/opt/vaso/python-deps", "scope": "complete qualified Python build/runtime dependency prefix including Ninja"},
    "triton-python": {"prefix": PREFIX + "/build-python", "scope": "pybind11 3.0.2, lit 18.1.8 and complete Triton Python build dependency prefix"},
    "llvm": {"prefix": PREFIX + "/llvm", "scope": "complete pinned LLVM/MLIR prefix at 4611156032e1ebac68b2b8f6107b1475a7c60800"},
    "json": {"prefix": PREFIX + "/json", "scope": "nlohmann JSON 3.11.3 complete header prefix"},
    "cuda-diagnostics": {"prefix": PREFIX + "/cuda-tools/12.6.77", "scope": "CUDA 12.6.77 cuobjdump and nvdisasm"},
    "vendor": {"prefix": PREFIX + "/vendor", "scope": "explicit Triton NVIDIA vendor tools including unused Blackwell provider"},
    "zstd": {"prefix": PREFIX + "/zstd", "scope": "pinned zstd 1.5.5+dfsg2-2build1.1 CLI; runtime libraries supplied by complete base capsule", "archive_sha256": "d7667f83e3a2caeaaecaca464d542ee6ccf537259a479622796c84afc96d6032"},
    "uv": {"prefix": PREFIX + "/bin", "scope": "pinned uv 0.9.27 build frontend and Python environment selection"},
}


def stable(value):
    return json.dumps(value, indent=2, sort_keys=True) + "\n"


def provider_contract(base_capsule: str) -> dict:
    if len(base_capsule) != 64 or any(c not in "0123456789abcdef" for c in base_capsule):
        raise ValueError("base capsule must have a SHA256 identity")
    return {
        "schema": "vaso.pascal-capsule-providers.v1",
        "base_capsule_sha256": base_capsule,
        "boundary": "complete immutable standalone Insula capsule; no external dependency mounts",
        "host": "linux-x86_64", "cuda": "12.6", "arch": "sm_61", "python_abi": "cp312",
        "providers": PROVIDERS,
        "source_dependency_policy": "preserve recursive package sources and existing Pascal profile features",
    }


def rootfs_identity(contract: dict) -> dict:
    return {
        "schema": "vaso.pascal-native-rootfs.v1",
        "provider_contract": contract,
        "components": {"cuda_toolkit": {"install_layout": {"prefix": "/opt/vaso/cuda"}}},
        "live_checks": {"commands": [
            {"name": "gcc", "argv": ["/usr/bin/gcc", "-dumpfullversion"], "expect_stdout": "12.2.0"},
            {"name": "python-abi", "argv": ["/usr/local/bin/python3", "-c", "import sys;print('%s.%s'%sys.version_info[:2])"], "expect_stdout": "3.12"},
        ]},
    }


def pascal_schema(modern: dict) -> dict:
    result = copy.deepcopy(modern)
    result["properties"]["line"]["enum"] = ["cu126"]
    return result


def build_plan(package: str, torch_profile: dict, triton_profile: dict, identity: dict,
               profile_text: str, contract_text: str, patch_text: str) -> dict:
    if package not in WHEEL_NAMES:
        raise ValueError("unknown Pascal package")
    if (torch_profile["toolchain"]["python_abi"] != "cp312" or torch_profile["toolchain"]["cuda_arch_list"] != "6.1"
            or torch_profile["toolchain"]["cuda_major_minor"] != "12.6"):
        raise ValueError("Pascal Torch profile must retain cp312/SM61")
    env = {
        "CC": "gcc", "CXX": "g++", "CFLAGS": "", "CXXFLAGS": "", "CUDAFLAGS": "", "NVCC_FLAGS": "",
        "PATH": PREFIX + "/bin:" + PREFIX + "/cuda-tools/12.6.77/bin:" + PREFIX + "/build-python/bin:/opt/vaso/cmake/bin:/opt/vaso/python-deps/bin:/opt/vaso/cuda/bin:/usr/local/bin:/usr/bin:/bin",
        "LD_LIBRARY_PATH": "/opt/vaso/cuda/lib64:/usr/local/lib",
        "PYTHONDONTWRITEBYTECODE": "1", "PYTHONNOUSERSITE": "1", "PIP_NO_INDEX": "1",
        "UV_OFFLINE": "1", "UV_CACHE_DIR": "{work}/uv-cache", "MAX_JOBS": "{jobs}", "CMAKE_BUILD_PARALLEL_LEVEL": "{jobs}",
    }
    if package == "torch":
        env.update(torch_profile["environment"])
        env.update({
            # scikit-build-core otherwise probes the Python cmake module before
            # PATH and may choose a different bundled version after a timeout.
            # An explicit executable is its sole candidate: failure stays closed.
            "CMAKE_EXECUTABLE": "/opt/vaso/cmake/bin/cmake",
            "MAX_JOBS": "{jobs}", "CMAKE_BUILD_PARALLEL_LEVEL": "{jobs}",
            "TORCH_CUDA_ARCH_LIST": torch_profile["toolchain"]["cuda_arch_list"],
            "PYTORCH_BUILD_VERSION": torch_profile["torch"]["version"],
            "VASO_SOURCE_GIT_SHA": torch_profile["torch"]["upstream_commit"],
        })
        archive_sha = torch_profile["torch"]["archive_sha256"]
        strip_prefix = torch_profile["torch"]["archive_prefix"]
        version = torch_profile["torch"]["version"]
        patches = []
    else:
        env.update({
            "PYTHONPATH": PREFIX + "/build-python:/opt/vaso/python-deps",
            "CUDA_HOME": "/opt/vaso/cuda", "TRITON_OFFLINE_BUILD": "1",
            "TRITON_BUILD_PROTON": "OFF", "LLVM_SYSPATH": PREFIX + "/llvm",
            "JSON_SYSPATH": PREFIX + "/json",
            "TRITON_PTXAS_PATH": "/opt/vaso/cuda/bin/ptxas",
            "TRITON_PTXAS_BLACKWELL_PATH": PREFIX + "/vendor/bin/ptxas-blackwell",
            "TRITON_CUOBJDUMP_PATH": PREFIX + "/cuda-tools/12.6.77/bin/cuobjdump",
            "TRITON_NVDISASM_PATH": PREFIX + "/cuda-tools/12.6.77/bin/nvdisasm",
            "TRITON_CUDACRT_PATH": "/opt/vaso/cuda/include",
            "TRITON_CUDART_PATH": "/opt/vaso/cuda/include",
            "TRITON_CUPTI_INCLUDE_PATH": "/opt/vaso/cuda/include",
            "TRITON_CUPTI_LIB_PATH": "/opt/vaso/cuda/lib64",
            "TRITON_LIBDEVICE_PATH": "/opt/vaso/cuda/nvvm/libdevice/libdevice.10.bc",
            "C_INCLUDE_PATH": "/opt/vaso/cuda/include", "CPLUS_INCLUDE_PATH": "/opt/vaso/cuda/include",
            "TRITON_APPEND_CMAKE_ARGS": "-DTRITON_BUILD_UT=OFF -DTRITON_BUILD_PROTON=OFF -DZLIB_LIBRARY=/usr/lib/x86_64-linux-gnu/libz.so -DZLIB_INCLUDE_DIR=/usr/include",
            "TRITON_HOME": "{work}/triton-home", "TRITON_CACHE_DIR": "{work}/triton-cache",
        })
        archive_sha = triton_profile["source_sha256"]
        strip_prefix = triton_profile["source_archive"].removesuffix(".tar.gz")
        version = "3.8.0"
        patches = [{"path": "signed-int8-dot.patch", "expected_text": patch_text}]
    # CPU count remains an unkeyed admission choice read by runtime_jobs_executor.
    phases = {}
    for phase in ("configure", "build", "install"):
        phases[phase] = {
            "argv": ["/opt/vaso/pascal/bin/uv", "run", "--offline", "--no-project",
                     "--no-python-downloads", "--no-config", "--python", "/usr/local/bin/python3",
                     "--", "python", "-B", WORKSPACE + "/native_step.py", phase,
                     "--package", package, "--source", "{src}", "--work", "{work}",
                     "--wheel", "{wheel}", "--profile", WORKSPACE + "/" + package + ".profile.json"],
            "cwd": ".", "env": {},
        }
    return {
        "schema": 1, "profile": "torch", "line": "cu126",
        "package": {"name": package, "version": version},
        "sources": [{"name": "main", "archive": "{source_archive}", "sha256": archive_sha,
                     "strip_prefix": strip_prefix, "urls": [], "patches": patches}],
        "toolchain": {
            "cc": "rootfs:gcc@12.2.0", "cxx": "rootfs:gcc@12.2.0", "cuda": "rootfs:cuda@12.6",
            "python": "rootfs:cpython@3.12", "env": env,
            "rootfs_identity": {"label": ":rootfs.identity.json", "expected_text": stable(identity)},
        },
        "deps": {name: {"label": ":capsule.providers.json", "key": "capsule:" + name} for name in PROVIDERS},
        "inputs": [
            {"path": package + ".profile.json", "expected_text": profile_text},
            {"path": "capsule.providers.json", "expected_text": contract_text},
        ],
        "outputs": [{"kind": "wheel", "path": "{wheel}"}],
        "gpu_archs": ["sm_61"],
        "determinism": {"prefix_map": {"{work}": ".", "{out}": "."},
                        "seed": "sha256:", "source_date_epoch": 1704067200},
        "live_checks": copy.deepcopy(identity["live_checks"]),
        **phases,
    }
