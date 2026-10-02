# Copyright Spack Project Developers. See COPYRIGHT file for details.
#
# SPDX-License-Identifier: (Apache-2.0 OR MIT)
#
# Vaso overlay note:
# The rootfs carries cuDNN 9.24.0.43 for both CUDA 12 and CUDA 13 lines. Spack
# v1.2.2's builtin recipe tops out at 9.21, so this overlay adds only the exact
# external version labels and their CUDA-major constraints.

from spack.package import *
from spack_repo.builtin.packages.cudnn.package import Cudnn as BuiltinCudnn


class Cudnn(BuiltinCudnn):
    version("9.24.0.43-12")
    version("9.24.0.43-13")

    depends_on("cuda@12", when="@9.24.0.43-12")
    depends_on("cuda@13", when="@9.24.0.43-13")
