# Copyright Spack Project Developers. See COPYRIGHT file for details.
#
# SPDX-License-Identifier: (Apache-2.0 OR MIT)
#
# Vaso overlay note:
# PyTorch v2.14.0 builds MAGMA from upstream 2.6.1 plus PyTorch's package-file
# patch set. Hermetic Spack v1.2.2's builtin MAGMA recipe rejects that version
# for CUDA 12.6+ before those patches can matter, so the overlay keeps the
# builtin recipe but narrows that conflict around the patched 2.6.1 provider.

from spack.package import *
from spack_repo.builtin.packages.magma.package import Magma as BuiltinMagma


class Magma(BuiltinMagma):
    patch(
        "https://raw.githubusercontent.com/pytorch/pytorch/v2.14.0/.ci/magma/package_files/CMake.patch",
        sha256="c6ee9d0f4412e7c08b1fc15f60c26a35b32f6b67ddeb97ecb336a2991aaa28f0",
        level=0,
        when="@2.6.1+cuda ^cuda@12.6:",
    )
    patch(
        "https://raw.githubusercontent.com/pytorch/pytorch/v2.14.0/.ci/magma/package_files/cmakelists.patch",
        sha256="16cb9a7e647591fd4832843017d2e16ba0fa568028d606587c29076aaaef1abd",
        level=0,
        when="@2.6.1+cuda ^cuda@12.6:",
    )
    patch(
        "https://raw.githubusercontent.com/pytorch/pytorch/v2.14.0/.ci/magma/package_files/thread_queue.patch",
        sha256="126031f4f9bc698ea89b9622a5b415bc7b5d354527629e1061b8e18357e6f616",
        level=0,
        when="@2.6.1+cuda ^cuda@12.6:",
    )
    patch(
        "https://raw.githubusercontent.com/pytorch/pytorch/v2.14.0/.ci/magma/package_files/cuda13.patch",
        sha256="8821f65bf2b1b278d5c4743babd68f11fadca13c7055627a39216c6ccadeb947",
        level=1,
        when="@2.6.1+cuda ^cuda@12.6:",
    )
    patch(
        "https://raw.githubusercontent.com/pytorch/pytorch/v2.14.0/.ci/magma/package_files/getrf_shfl.patch",
        sha256="8cabcc164f0108619bc71d282ac40ccb6efdf1489ac304c1f74500d8486f0a71",
        level=1,
        when="@2.6.1+cuda ^cuda@12.6:",
    )
    patch(
        "https://raw.githubusercontent.com/pytorch/pytorch/v2.14.0/.ci/magma/package_files/getrf_nbparam.patch",
        sha256="8179c1ebf1751956dfc1d67352dcdab6bda8bbcd494bc1c2284f7894fbe9da9b",
        level=1,
        when="@2.6.1+cuda ^cuda@12.6:",
    )

    conflicts("^cuda@12.6:", when="@:2.6.0")
    conflicts("^cuda@12.6:", when="@2.6.2:2.8.0")
    conflicts("^cuda@13:", when="@2.6.2:2.9.0")


def _is_superseded_cuda_conflict(directive):
    keywords = getattr(directive, "keywords", {})
    conflict_spec = keywords.get("conflict_spec", keywords.get("spec"))
    return (
        str(conflict_spec) == "^cuda@12.6:"
        and str(keywords.get("when")) == "@:2.8.0"
    ) or (
        str(conflict_spec) == "^cuda@13:"
        and str(keywords.get("when")) == "@:2.9.0"
    )


Magma._directives_to_be_executed["conflicts"] = [
    directive
    for directive in Magma._directives_to_be_executed["conflicts"]
    if not _is_superseded_cuda_conflict(directive)
]
