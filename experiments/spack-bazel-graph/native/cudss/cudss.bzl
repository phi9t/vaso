"""Native rootfs boundary for cuDSS from the selected insula rootfs."""

load(
    "//native/common:rootfs_cuda_component.bzl",
    "rootfs_cuda_component_attrs",
    "rootfs_cuda_component_impl",
)

cudss_native = repository_rule(
    implementation = rootfs_cuda_component_impl,
    attrs = rootfs_cuda_component_attrs(
        component = "cudss",
        display_name = "cuDSS",
        version_kind = "cudss",
        required_headers = ["cudss.h"],
        required_libs = ["libcudss.so"],
        link_libraries = ["cudss"],
    ),
    environ = [
        "VASO_CUDA_HOME",
        "VASO_IN_INSULA",
        "VASO_ROOTFS_BUNDLE_MANIFEST",
    ],
    doc = "Expose the selected rootfs cuDSS as an exact-version sdk-boundary.",
)
