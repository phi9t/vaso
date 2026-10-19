# Copyright Spack Project Developers. See COPYRIGHT file for details.
#
# SPDX-License-Identifier: (Apache-2.0 OR MIT)
#
# Vaso overlay note:
# Spack v1.2.2's builtin py-torch recipe currently tops out at v2.12.0, while
# the native-PyTorch migration is anchored to upstream PyTorch v2.14.0. Keep the
# Bazel-vendored Spack release as the execution substrate, and extend only the
# missing version in this repo-owned overlay.

from spack.package import *
from spack_repo.builtin.packages.py_torch.package import PyTorch as BuiltinPyTorch


class PyTorch(BuiltinPyTorch):
    version("2.14.0", tag="v2.14.0", commit="2b3ec34829036a65cd9d1398ea72a0167dc37470")

    depends_on("cudss", when="@2.14.0+cuda")
    # Hermetic Spack's nvshmem recipe rejects ~ucx~gdrcopy, so this keeps the
    # minimal satisfiable provider shape while avoiding MPI and a second NCCL edge.
    depends_on("nvshmem+cuda~mpi+ucx~nccl+gdrcopy cuda_arch=100", when="@2.14.0+cuda")
    depends_on(
        "magma@2.6.1+cuda cuda_arch=100",
        when="@2.14.0+cuda+magma",
        type=("build", "link", "run"),
    )
    depends_on("magma@:2.9+cuda", when="@:2.13+cuda+magma")
    depends_on("protobuf@21.12", when="@2.14.0~custom-protobuf")
    depends_on("py-protobuf@4.21.12", when="@2.14.0~custom-protobuf", type=("build", "run"))
    depends_on("py-scikit-build-core@1:", when="@2.14.0", type="build")


def _is_legacy_protobuf_directive(directive):
    keywords = getattr(directive, "keywords", {})
    spec = keywords.get("spec")
    when = keywords.get("when")
    when_parts = set(when) if isinstance(when, tuple) else {str(when)}
    return (
        (
            when_parts == {"~custom-protobuf", "@1.10:"}
            or str(when) == "@1.10:~custom-protobuf"
        )
        and str(spec) in {"protobuf@3.13.0", "py-protobuf@3.13"}
    )


def _is_inherited_unversioned_cuda_magma_directive(directive):
    keywords = getattr(directive, "keywords", {})
    return (
        str(keywords.get("spec")) == "magma@:2.9+cuda"
        and str(keywords.get("when")) in {"+magma+cuda", "+cuda+magma"}
    )


PyTorch._directives_to_be_executed["depends_on"] = [
    directive
    for directive in PyTorch._directives_to_be_executed["depends_on"]
    if not _is_legacy_protobuf_directive(directive)
    and not _is_inherited_unversioned_cuda_magma_directive(directive)
]
