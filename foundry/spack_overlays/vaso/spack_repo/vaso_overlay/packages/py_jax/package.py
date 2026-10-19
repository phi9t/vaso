# Copyright Spack Project Developers. See COPYRIGHT file for details.
#
# SPDX-License-Identifier: (Apache-2.0 OR MIT)
#
# Vaso overlay note:
# Spack v1.2.2's builtin py-jax recipe currently tops out at 0.10.1. The
# one-LLVM plan moves JAX to 0.10.2 so that XLA, Triton, and the rootfs clang
# all share llvm-project 35901313. Keep the builtin recipe behavior and add the
# missing 0.10.2 metadata explicitly.

from spack.package import *
from spack_repo.builtin.packages.py_jax.package import PyJax as BuiltinPyJax


class PyJax(BuiltinPyJax):
    version("0.10.2", sha256="bf77428a8c2e6904c4f46d5ab12aa5cfc6cad2179f07f7e4c0fc75ac86ef0639")

    # PyPI metadata for jax 0.10.2: Requires-Python >=3.11 and
    # Requires-Dist numpy>=2.0, scipy>=1.14, ml_dtypes>=0.5.0. Mosaic GPU's
    # Pallas runtime imports absl.logging, so keep absl in the Spack-owned DAG.
    depends_on("python@3.11:", when="@0.10.2", type=("build", "run"))
    depends_on("py-absl-py@1:", when="@0.10.2", type=("build", "run"))
    depends_on("py-numpy@2:", when="@0.10.2", type=("build", "run"))
    depends_on("py-scipy@1.14:", when="@0.10.2", type=("build", "run"))
    depends_on("py-ml-dtypes@0.5:", when="@0.10.2", type=("build", "run"))

    # PyPI metadata allows jaxlib 0.10.1 through 0.10.2. The worker proof spec
    # pins the graph input to ^py-jaxlib@0.10.2, and this upper bound prevents a
    # future newer jaxlib from satisfying jax 0.10.2 by accident.
    depends_on("py-jaxlib@0.10.1:0.10.2", when="@0.10.2", type=("build", "run"))
