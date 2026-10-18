"""Native rootfs boundary for cuDNN from the selected insula rootfs."""

load(
    "//native/common:rootfs_cuda_component.bzl",
    "rootfs_cuda_component_attrs",
    "rootfs_cuda_component_impl",
)

cudnn_native = repository_rule(
    implementation = rootfs_cuda_component_impl,
    attrs = rootfs_cuda_component_attrs(
        component = "cudnn",
        display_name = "cuDNN",
        version_kind = "cudnn",
        required_headers = [
            "cudnn.h",
            "cudnn_version.h",
        ],
        required_libs = ["libcudnn.so"],
        link_libraries = ["cudnn"],
    ),
    environ = [
        "VASO_CUDA_HOME",
        "VASO_IN_INSULA",
        "VASO_ROOTFS_BUNDLE_MANIFEST",
    ],
    doc = "Expose the selected rootfs cuDNN as an exact-version sdk-boundary.",
)
