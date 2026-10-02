# Copyright Spack Project Developers. See COPYRIGHT file for details.
#
# SPDX-License-Identifier: (Apache-2.0 OR MIT)
#
# Vaso overlay note:
# PyTorch v2.14.0 needs scikit-build-core >=1.0, and the native provider is a
# single unversioned py-scikit-build-core@1.0.0 prefix. Keep pybind11's v3 build
# backend on the same provider version so the torch closure has one concrete
# scikit-build-core node.

from spack.package import *
from spack_repo.builtin.packages.py_pybind11.package import PyPybind11 as BuiltinPyPybind11


class PyPybind11(BuiltinPyPybind11):
    depends_on("py-scikit-build-core@1:", when="@3:", type="build")
