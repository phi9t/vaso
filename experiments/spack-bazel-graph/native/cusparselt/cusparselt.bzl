"""Native rootfs boundary for cuSPARSELt from the selected insula rootfs."""

load(
    "//native/common:rootfs_cuda_component.bzl",
    "rootfs_cuda_component_attrs",
    "rootfs_cuda_component_impl",
)

cusparselt_native = repository_rule(
    implementation = rootfs_cuda_component_impl,
    attrs = rootfs_cuda_component_attrs(
        component = "cusparselt",
        display_name = "cuSPARSELt",
        version_kind = "cusparselt",
        required_headers = ["cusparseLt.h"],
        required_libs = [
            "libcusparseLt.so",
            "libcusparseLt.so.0",
            "libcusparseLt_static.a",
        ],
        link_libraries = ["cusparseLt"],
    ),
    environ = [
        "VASO_CUDA_HOME",
        "VASO_IN_INSULA",
        "VASO_ROOTFS_BUNDLE_MANIFEST",
    ],
    doc = "Expose the selected rootfs cuSPARSELt as an exact-version sdk-boundary.",
)
