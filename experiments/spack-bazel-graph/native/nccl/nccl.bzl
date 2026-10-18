"""Native rootfs boundary for NCCL from the selected insula rootfs."""

load(
    "//native/common:rootfs_cuda_component.bzl",
    "rootfs_cuda_component_attrs",
    "rootfs_cuda_component_impl",
)

nccl_native = repository_rule(
    implementation = rootfs_cuda_component_impl,
    attrs = rootfs_cuda_component_attrs(
        component = "nccl",
        display_name = "NCCL",
        version_kind = "nccl",
        required_headers = ["nccl.h"],
        required_libs = ["libnccl.so"],
        link_libraries = ["nccl"],
    ),
    environ = [
        "VASO_CUDA_HOME",
        "VASO_IN_INSULA",
        "VASO_ROOTFS_BUNDLE_MANIFEST",
    ],
    doc = "Expose the selected rootfs NCCL as an exact-version sdk-boundary.",
)
