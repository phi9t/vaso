# Copyright Spack Project Developers. See COPYRIGHT file for details.
#
# SPDX-License-Identifier: (Apache-2.0 OR MIT)
#
# Vaso overlay note:
# Spack v1.2.2's builtin py-jaxlib recipe currently tops out at 0.10.1 and
# intentionally leaves LOCAL_CUDA_PATH/LOCAL_CUDNN_PATH disabled. The one-LLVM
# migration needs jaxlib 0.10.2 built with the rootfs clang/CUDA stack only.

import glob

from spack.package import *
from spack_repo.builtin.build_systems.cuda import CudaPackage
from spack_repo.builtin.build_systems.python import PythonPipBuilder
from spack_repo.builtin.packages.py_jaxlib.package import PyJaxlib as BuiltinPyJaxlib


class PyJaxlib(BuiltinPyJaxlib):
    version("0.10.2", sha256="fa7214ab31ed1cd418b4305807e9c4f3f175c783eeea40c28e0f77c3f4c24bc7")

    variant(
        "build_cuda_with_clang",
        default=False,
        description="Compile CUDA kernels with Clang instead of the default nvcc path",
        when="@0.10.2+cuda",
    )

    # jax-v0.10.2/.bazelversion is exactly 7.7.0.
    depends_on("bazel@7.7.0", when="@0.10.2", type="build")
    depends_on("python@3.11:", when="@0.10.2", type=("build", "link", "run"))
    depends_on("py-numpy@2:", when="@0.10.2", type=("build", "run"))
    depends_on("py-scipy@1.14:", when="@0.10.2", type=("build", "run"))
    depends_on("py-ml-dtypes@0.5:", when="@0.10.2", type=("build", "run"))

    depends_on(
        "llvm@23.0.0 +clang +lld +mlir",
        when="@0.10.2",
        type="build",
    )
    depends_on("cuda+allow-unsupported-compilers", when="@0.10.2+cuda", type=("build", "link", "run"))
    depends_on("nvshmem", when="@0.10.2+cuda", type=("build", "link", "run"))

    def install(self, spec, prefix):
        if not spec.satisfies("@0.10.2"):
            super().install(spec, prefix)
            return

        args = ["build/build.py", "build"]
        if spec.satisfies("+cuda"):
            args.append("--wheels=jaxlib,jax-cuda-plugin,jax-cuda-pjrt")
        elif spec.satisfies("+rocm"):
            args.append("--wheels=jaxlib,jax-rocm-plugin,jax-rocm-pjrt")
        else:
            args.append("--wheels=jaxlib")

        # Disable rules_python's hermetic interpreter selection. The build uses
        # the Spack Python that is executing build.py.
        args.append("--python_version=")

        # Rootfs clang/lld 23-git is the one compiler. build.py turns this into
        # clang_local/cuda_clang_local and avoids the hermetic clang/sysroot.
        args.append(f"--clang_path={self.compiler.cc}")
        args.append("--bazel_options=--repo_env=USE_HERMETIC_CC_TOOLCHAIN=0")
        args.append("--bazel_options=--@rules_ml_toolchain//common:enable_hermetic_cc=False")

        if "+cuda" in spec:
            capabilities = CudaPackage.compute_capabilities(spec.variants["cuda_arch"].value)
            cuda_major = str(spec["cuda"].version).split(".")[0]
            args.append(f"--cuda_major_version={cuda_major}")
            args.append(f"--cuda_compute_capabilities={','.join(capabilities)}")

            # These LOCAL_* paths force rules_ml_toolchain to use the rootfs
            # externals and skip the NVIDIA redist downloads.
            args.extend(
                [
                    f"--bazel_options=--repo_env=LOCAL_CUDA_PATH={spec['cuda'].prefix}",
                    f"--bazel_options=--repo_env=LOCAL_CUDNN_PATH={spec['cudnn'].prefix}",
                    f"--bazel_options=--repo_env=LOCAL_NVSHMEM_PATH={spec['nvshmem'].prefix}",
                    f"--bazel_options=--repo_env=HERMETIC_CUDA_VERSION={spec['cuda'].version}",
                    "--bazel_options=--config=cuda_libraries_from_stubs",
                ]
            )
            if "+nccl" in spec:
                args.append(f"--bazel_options=--repo_env=LOCAL_NCCL_PATH={spec['nccl'].prefix}")

            # build.py defaults to --config=build_cuda_with_nvcc. Keep that for
            # wheel parity; expose Clang CUDA only as the explicit fallback.
            if spec.satisfies("+build_cuda_with_clang"):
                args.append("--build_cuda_with_clang")

        if "+rocm" in spec:
            args.append(f"--rocm_path={self.spec['hip'].prefix}")
            if not spec["hip"].external:
                args.append("--bazel_options=--@local_config_rocm//rocm:rocm_path_type=multiple")
            amdgpu_targets = ",".join(self.spec.variants["amdgpu_target"].value)
            args.append(f"--rocm_amdgpu_target={amdgpu_targets}")

        args.extend(
            [
                f"--bazel_options=--jobs={make_jobs}",
                "--bazel_startup_options=--nohome_rc",
                "--bazel_startup_options=--nosystem_rc",
            ]
        )

        python(*args)

        for whl in glob.glob(join_path("dist", "*.whl")):
            pip(*PythonPipBuilder.std_args(self), f"--prefix={self.prefix}", whl)
