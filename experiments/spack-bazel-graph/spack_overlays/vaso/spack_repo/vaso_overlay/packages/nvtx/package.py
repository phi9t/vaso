# Copyright Spack Project Developers. See COPYRIGHT file for details.
#
# SPDX-License-Identifier: (Apache-2.0 OR MIT)
#
# Vaso overlay note:
# The CUDA rootfs exposes only NVTX C/C++ headers under the CUDA target prefix.
# Spack v1.2.2 models nvtx as a PythonExtension even when concretized
# `~python`, which attaches a Python dependency to the non-buildable rootfs
# external. This overlay keeps the same C/C++ header package surface but does
# not inherit PythonExtension.

from spack_repo.builtin.build_systems.generic import Package

from spack.package import *


class Nvtx(Package):
    homepage = "https://github.com/NVIDIA/NVTX"
    git = "https://github.com/NVIDIA/NVTX.git"
    url = "https://github.com/NVIDIA/NVTX/archive/refs/tags/v3.1.0.tar.gz"

    maintainers("thomas-bouvier")

    license("Apache-2.0")

    version("develop", branch="dev")
    version("3.4.0", sha256="99a3e97d7fe90d5195e87256492bf9cd42476d72cbc79ba477011a2384b88f92")
    version("3.3.0", sha256="67d0cda2f9d19a89684592dab40c0bf2c2b13d5d588e51392076c0890a64b6c0")
    version("3.2.1", sha256="737c3035f0e43a2252e7cd94c3f26e11e169f624236efe31794f044ce44a70af")
    version("3.1.0", sha256="dc4e4a227d04d3da46ad920dfee5f7599ac8d6b2ee1809c9067110fb1cc71ced")

    depends_on("c", type="build")
    depends_on("cxx", type="build")

    variant("python", default=False, description="Install Python bindings.")

    patch("nvtx-config.patch")

    def install(self, spec, prefix):
        install_tree("c/include", prefix.include)
        install("c/CMakeLists.txt", prefix)
        install("c/nvtxImportedTargets.cmake", prefix)
        install("./LICENSE.txt", prefix)
        install("./nvtx-config.cmake", prefix)
