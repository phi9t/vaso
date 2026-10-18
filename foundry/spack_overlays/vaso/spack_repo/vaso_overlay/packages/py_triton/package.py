# Copyright Spack Project Developers. See COPYRIGHT file for details.
#
# SPDX-License-Identifier: (Apache-2.0 OR MIT)
#
# Vaso overlay note:
# The torch 2.14 graph uses Triton commit 675c598. This overlay keeps the
# hermetic Spack recipe shape but forces that Triton build onto the one rootfs
# LLVM and CUDA toolchain instead of Triton's bundled tool downloads.

import hashlib
import os

from spack.package import *
from spack_repo.builtin.packages.py_triton.package import PyTriton as BuiltinPyTriton


class PyTriton(BuiltinPyTriton):
    triton_version = "3.8.0"
    triton_commit = "675c59878aa2280b31f722aaf42b825fcee21de8"
    llvm_patch = "../../../../../../native/triton/patches/0001-llvm-35901313.patch"
    llvm_patch_sha256 = "8a0405802e0f45fae5aa9bd0a6921d81f16a1f50224a705a4f450fe7c03a90d3"

    version("3.8.0", commit="675c59878aa2280b31f722aaf42b825fcee21de8")

    depends_on("llvm@23.0.0 +clang +lld +mlir", when="@3.8.0", type=("build", "link"))
    depends_on("cuda", when="@3.8.0", type=("build", "link", "run"))
    depends_on("nlohmann-json@3.11.3", when="@3.8.0", type="build")

    def _is_torch_pin(self, spec):
        return spec.satisfies(f"@{self.triton_version}")

    def patch(self):
        if not self._is_torch_pin(self.spec):
            return

        patch_path = os.path.abspath(join_path(os.path.dirname(__file__), self.llvm_patch))
        with open(patch_path, "rb") as patch_file:
            digest = hashlib.sha256(patch_file.read()).hexdigest()
        if digest != self.llvm_patch_sha256:
            raise InstallError(
                "unexpected 0001-llvm-35901313.patch sha256: "
                f"got {digest}, expected {self.llvm_patch_sha256}"
            )

        which("patch")("-p1", "-i", patch_path)

    def setup_build_environment(self, env: EnvironmentModifications) -> None:
        super().setup_build_environment(env)
        spec = self.spec
        if not self._is_torch_pin(spec):
            return

        cuda_prefix = spec["cuda"].prefix
        zlib_prefix = spec["zlib-api"].prefix
        env.set("LLVM_SYSPATH", spec["llvm"].prefix)
        env.set("JSON_SYSPATH", spec["nlohmann-json"].prefix)
        env.set("PYBIND11_SYSPATH", spec["py-pybind11"].prefix)
        env.prepend_path("CPATH", join_path(zlib_prefix, "include"))
        env.prepend_path("LIBRARY_PATH", join_path(zlib_prefix, "lib"))
        env.prepend_path("LD_LIBRARY_PATH", join_path(zlib_prefix, "lib"))
        env.prepend_path("CMAKE_PREFIX_PATH", zlib_prefix)

        # Offline mode makes setup.py fail instead of fetching Triton's LLVM or
        # NVIDIA tool bundles. Every tool path below comes from the rootfs CUDA
        # external for the selected line.
        env.set("TRITON_OFFLINE_BUILD", "1")
        env.set("TRITON_BUILD_WITH_CLANG_LLD", "1")
        env.set("TRITON_PTXAS_PATH", join_path(cuda_prefix, "bin", "ptxas"))
        env.set("TRITON_PTXAS_BLACKWELL_PATH", join_path(cuda_prefix, "bin", "ptxas"))
        env.set("TRITON_CUOBJDUMP_PATH", join_path(cuda_prefix, "bin", "cuobjdump"))
        env.set("TRITON_NVDISASM_PATH", join_path(cuda_prefix, "bin", "nvdisasm"))
        env.set("TRITON_CUDACRT_PATH", join_path(cuda_prefix, "include"))
        env.set("TRITON_CUDART_PATH", join_path(cuda_prefix, "include"))
        env.set("TRITON_CUPTI_PATH", cuda_prefix)
        env.set("TRITON_CUPTI_INCLUDE_PATH", join_path(cuda_prefix, "include"))
        env.set("TRITON_CUPTI_LIB_PATH", join_path(cuda_prefix, "lib64"))
        env.set("TRITON_CUPTI_LIB_BLACKWELL_PATH", join_path(cuda_prefix, "lib64"))
        env.set("TRITON_LIBDEVICE_PATH", join_path(cuda_prefix, "nvvm", "libdevice", "libdevice.10.bc"))
