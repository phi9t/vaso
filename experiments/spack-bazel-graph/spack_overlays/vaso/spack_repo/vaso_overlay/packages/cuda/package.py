# Copyright Spack Project Developers. See COPYRIGHT file for details.
#
# SPDX-License-Identifier: (Apache-2.0 OR MIT)
#
# Vaso overlay note:
# The CUDA ecosystem rootfs lock uses cuda@13.0.3 from the PyTorch 2.14 wheel
# metadata. Spack v1.2.2's builtin CUDA recipe stops at 13.0.2, so this overlay
# adds only the missing version label needed for the non-buildable rootfs
# external.

from spack.package import *
from spack_repo.builtin.packages.cuda.package import Cuda as BuiltinCuda


class Cuda(BuiltinCuda):
    version("13.0.3")
