# Copyright Spack Project Developers. See COPYRIGHT file for details.
#
# SPDX-License-Identifier: (Apache-2.0 OR MIT)
#
# Vaso overlay note:
# PyTorch 2.14 pins NCCL v2.30.7-1. Spack v1.2.2's builtin recipe tops out at
# 2.29.7-1, so this overlay adds only the missing version label for the
# non-buildable rootfs external.

from spack.package import *
from spack_repo.builtin.packages.nccl.package import Nccl as BuiltinNccl


class Nccl(BuiltinNccl):
    version("2.30.7-1")
