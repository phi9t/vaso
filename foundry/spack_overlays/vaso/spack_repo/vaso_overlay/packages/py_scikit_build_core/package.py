# Copyright Spack Project Developers. See COPYRIGHT file for details.
#
# SPDX-License-Identifier: (Apache-2.0 OR MIT)
#
# Vaso overlay note:
# Spack v1.2.2's builtin py-scikit-build-core recipe currently tops out at
# 0.12.2. PyTorch v2.14.0's wheel frontend requires scikit-build-core >=1.0, so
# this overlay adds only the smallest stable version satisfying that backend.

from spack.package import *
from spack_repo.builtin.packages.py_scikit_build_core.package import (
    PyScikitBuildCore as BuiltinPyScikitBuildCore,
)


class PyScikitBuildCore(BuiltinPyScikitBuildCore):
    version("1.0.0", sha256="b82a8b41dd66926b96096a61e8fc8df22214bbec437d251c0fda1bfb9d7df558")
