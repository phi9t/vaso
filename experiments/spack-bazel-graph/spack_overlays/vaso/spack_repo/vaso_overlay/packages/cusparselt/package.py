# Copyright Spack Project Developers. See COPYRIGHT file for details.
#
# SPDX-License-Identifier: (Apache-2.0 OR MIT)
#
# Vaso overlay note:
# Spack v1.2.2 carries the CUDA 12 cuSPARSELt 0.8.1 recipe entry but leaves the
# CUDA 13 entry commented out. The cu130 rootfs carries NVIDIA's matching
# 0.8.1.1 CUDA 13 archive, represented by Spack's recipe naming convention as
# 0.8.1-cuda130.

from spack.package import *
from spack_repo.builtin.packages.cusparselt.package import Cusparselt as BuiltinCusparselt


class Cusparselt(BuiltinCusparselt):
    version("0.8.1-cuda130")

    depends_on("cuda@13", when="@0.8.1-cuda130", type=("build", "run"))
