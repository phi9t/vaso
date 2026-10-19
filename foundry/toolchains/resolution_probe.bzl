def _rootfs_toolchain_probe_impl(ctx):
    return []

rootfs_toolchain_probe = rule(
    implementation = _rootfs_toolchain_probe_impl,
    toolchains = [
        ":cuda_toolchain_type",
        ":python_toolchain_info_type",
        "@bazel_tools//tools/cpp:toolchain_type",
    ],
)
