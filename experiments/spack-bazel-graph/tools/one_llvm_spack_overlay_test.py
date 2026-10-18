#!/usr/bin/env python3
"""Contract tests for the one-LLVM Spack overlay recipes."""

from __future__ import annotations

import re
import unittest
from pathlib import Path


EXPERIMENT_ROOT = Path(__file__).parents[1]
SPACK_DIST = EXPERIMENT_ROOT / "tools" / "spack_dist.bzl"
OVERLAY_PACKAGES = (
    EXPERIMENT_ROOT
    / "spack_overlays"
    / "vaso"
    / "spack_repo"
    / "vaso_overlay"
    / "packages"
)
LLVM_COMMIT = "35901313800ea6e6cbeb9226e51c7c4b29bfc40e"
TRITON_COMMIT = "675c59878aa2280b31f722aaf42b825fcee21de8"
TRITON_VERSION = "3.8.0"
TRITON_LLVM_PATCH_SHA256 = "8a0405802e0f45fae5aa9bd0a6921d81f16a1f50224a705a4f450fe7c03a90d3"


def read_overlay(package: str) -> str:
    return (OVERLAY_PACKAGES / package / "package.py").read_text(encoding="utf-8")


class OneLlvmSpackOverlayTest(unittest.TestCase):
    def test_jax_0102_overlay_declares_source_and_runtime_bounds(self) -> None:
        text = read_overlay("py_jax")

        self.assertIn(
            'version("0.10.2", sha256="bf77428a8c2e6904c4f46d5ab12aa5cfc6cad2179f07f7e4c0fc75ac86ef0639")',
            text,
        )
        self.assertRegex(text, r'depends_on\("python@3\.11:",\s*when="@0\.10\.2"')
        self.assertRegex(text, r'depends_on\("py-numpy@2:",\s*when="@0\.10\.2"')
        self.assertRegex(text, r'depends_on\("py-ml-dtypes@0\.5:",\s*when="@0\.10\.2"')
        self.assertRegex(text, r'depends_on\("py-scipy@1\.14:",\s*when="@0\.10\.2"')
        self.assertRegex(text, r'depends_on\("py-jaxlib@0\.10\.1:0\.10\.2",\s*when="@0\.10\.2"')

    def test_jaxlib_0102_overlay_uses_rootfs_toolchains_and_cuda_externals(self) -> None:
        text = read_overlay("py_jaxlib")

        self.assertIn(
            'version("0.10.2", sha256="fa7214ab31ed1cd418b4305807e9c4f3f175c783eeea40c28e0f77c3f4c24bc7")',
            text,
        )
        self.assertRegex(text, r'depends_on\("bazel@7\.7\.0",\s*when="@0\.10\.2"')
        self.assertRegex(text, r'depends_on\("python@3\.11:",\s*when="@0\.10\.2"')
        self.assertRegex(text, r'depends_on\("py-numpy@2:",\s*when="@0\.10\.2"')
        self.assertRegex(text, r'depends_on\("py-ml-dtypes@0\.5:",\s*when="@0\.10\.2"')
        self.assertRegex(text, r'depends_on\("py-scipy@1\.14:",\s*when="@0\.10\.2"')
        self.assertRegex(text, r'depends_on\(\s*"llvm@23\.0\.0 \+clang \+lld \+mlir",\s*when="@0\.10\.2"')
        self.assertNotIn("llvm@git.", text)
        self.assertIn('depends_on("cuda+allow-unsupported-compilers", when="@0.10.2+cuda"', text)
        self.assertIn('depends_on("nvshmem", when="@0.10.2+cuda"', text)
        self.assertRegex(text, r'variant\(\s*"build_cuda_with_clang",\s*default=False')

        for required in (
            "--python_version=",
            "--clang_path={self.compiler.cc}",
            "--bazel_options=--repo_env=USE_HERMETIC_CC_TOOLCHAIN=0",
            "--bazel_options=--@rules_ml_toolchain//common:enable_hermetic_cc=False",
            "--bazel_options=--repo_env=LOCAL_CUDA_PATH=",
            "--bazel_options=--repo_env=LOCAL_CUDNN_PATH=",
            "--bazel_options=--repo_env=LOCAL_NCCL_PATH=",
            "--bazel_options=--repo_env=LOCAL_NVSHMEM_PATH=",
            "--bazel_options=--repo_env=HERMETIC_CUDA_VERSION=",
            "--bazel_options=--config=cuda_libraries_from_stubs",
            "--build_cuda_with_clang",
        ):
            self.assertIn(required, text)

        self.assertRegex(text, r'spec\.satisfies\("\+build_cuda_with_clang"\)')
        self.assertRegex(text, r"spec\[['\"]cuda['\"]\]\.prefix")
        self.assertRegex(text, r"spec\[['\"]cudnn['\"]\]\.prefix")
        self.assertRegex(text, r"spec\[['\"]nccl['\"]\]\.prefix")
        self.assertRegex(text, r"spec\[['\"]nvshmem['\"]\]\.prefix")

    def test_triton_pytorch_pin_overlay_uses_rootfs_llvm_and_cuda_tools(self) -> None:
        text = read_overlay("py_triton")

        self.assertIn(f'version("{TRITON_VERSION}", commit="{TRITON_COMMIT}")', text)
        self.assertIn(f'depends_on("llvm@23.0.0 +clang +lld +mlir", when="@{TRITON_VERSION}"', text)
        self.assertNotIn("llvm@git.", text)
        self.assertRegex(text, rf'depends_on\("cuda",\s*when="@{TRITON_VERSION}"')
        self.assertRegex(text, rf'depends_on\("nlohmann-json@3\.11\.3",\s*when="@{TRITON_VERSION}"')
        self.assertNotIn(f'version("{TRITON_COMMIT}"', text)
        self.assertNotIn(f'when="commit={TRITON_COMMIT}"', text)
        self.assertNotIn(f'when="@{TRITON_COMMIT}"', text)
        self.assertIn("0001-llvm-35901313.patch", text)
        self.assertIn(TRITON_LLVM_PATCH_SHA256, text)
        self.assertIn("hashlib.sha256", text)
        self.assertIn("digest != self.llvm_patch_sha256", text)

        for required in (
            'env.set("LLVM_SYSPATH", spec["llvm"].prefix)',
            'env.set("TRITON_OFFLINE_BUILD", "1")',
            'env.set("TRITON_BUILD_WITH_CLANG_LLD", "1")',
            'env.set("TRITON_PTXAS_PATH", join_path(cuda_prefix, "bin", "ptxas"))',
            'env.set("TRITON_PTXAS_BLACKWELL_PATH", join_path(cuda_prefix, "bin", "ptxas"))',
            'env.set("TRITON_CUOBJDUMP_PATH", join_path(cuda_prefix, "bin", "cuobjdump"))',
            'env.set("TRITON_NVDISASM_PATH", join_path(cuda_prefix, "bin", "nvdisasm"))',
            'env.set("TRITON_CUDACRT_PATH", join_path(cuda_prefix, "include"))',
            'env.set("TRITON_CUDART_PATH", join_path(cuda_prefix, "include"))',
            'env.set("TRITON_CUPTI_PATH", cuda_prefix)',
            'env.set("TRITON_CUPTI_INCLUDE_PATH", join_path(cuda_prefix, "include"))',
            'env.set("TRITON_CUPTI_LIB_PATH", join_path(cuda_prefix, "lib64"))',
            'env.set("TRITON_CUPTI_LIB_BLACKWELL_PATH", join_path(cuda_prefix, "lib64"))',
            'zlib_prefix = spec["zlib-api"].prefix',
            'env.prepend_path("CPATH", join_path(zlib_prefix, "include"))',
            'env.prepend_path("LIBRARY_PATH", join_path(zlib_prefix, "lib"))',
            'env.prepend_path("LD_LIBRARY_PATH", join_path(zlib_prefix, "lib"))',
            'env.prepend_path("CMAKE_PREFIX_PATH", zlib_prefix)',
        ):
            self.assertIn(required, text)

        forbidden = (
            "llvm-info.json",
            "oaitriton.blob.core.windows.net",
            "nvidia-toolchain-version.json",
            "download_and_extract",
        )
        for marker in forbidden:
            self.assertNotIn(marker, text)

    def test_spack_dist_runfiles_include_triton_llvm_patch(self) -> None:
        text = SPACK_DIST.read_text(encoding="utf-8")

        self.assertIn(
            '"@//native/triton:patches/0001-llvm-35901313.patch"',
            text,
        )


if __name__ == "__main__":
    unittest.main()
