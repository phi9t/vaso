"""Native rootfs boundary for NVSHMEM from the selected insula rootfs."""

load(
    "//native/common:rootfs_cuda_component.bzl",
    "rootfs_cuda_component_attrs",
    "rootfs_cuda_component_impl",
)

nvshmem_native = repository_rule(
    implementation = rootfs_cuda_component_impl,
    attrs = rootfs_cuda_component_attrs(
        component = "nvshmem",
        display_name = "NVSHMEM",
        version_kind = "nvshmem",
        required_headers = [
            "nvshmem.h",
            "non_abi/nvshmem_version.h",
        ],
        required_libs = [
            "libnvshmem_host.so",
            "libnvshmem_host.so.3",
            "libnvshmem_device.a",
        ],
        link_libraries = ["nvshmem_host"],
    ),
    environ = [
        "VASO_CUDA_HOME",
        "VASO_IN_INSULA",
        "VASO_ROOTFS_BUNDLE_MANIFEST",
    ],
    doc = "Expose the selected rootfs NVSHMEM as an exact-version sdk-boundary.",
)
