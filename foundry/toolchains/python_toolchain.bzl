PythonRootfsToolchainInfo = provider(
    doc = "Metadata for the Python runtime supplied by the native rootfs graph.",
    fields = {
        "python_prefix_file": "Label string for the existing Bazel-native Python prefix marker.",
        "version": "Exact CPython version.",
    },
)

def _python_prefix_file_impl(ctx):
    suffix = "/" + ctx.attr.path
    matches = [
        file
        for file in ctx.attr.prefix[DefaultInfo].files.to_list()
        if file.short_path == ctx.attr.path or file.short_path.endswith(suffix) or
           file.path == ctx.attr.path or file.path.endswith(suffix)
    ]
    if len(matches) != 1:
        fail("expected exactly one {} in {}, found {}".format(
            ctx.attr.path,
            ctx.attr.prefix.label,
            len(matches),
        ))
    return [DefaultInfo(files = depset(matches))]

python_prefix_file = rule(
    implementation = _python_prefix_file_impl,
    attrs = {
        "path": attr.string(mandatory = True),
        "prefix": attr.label(mandatory = True),
    },
)

def _python_toolchain_info_impl(ctx):
    info = PythonRootfsToolchainInfo(
        python_prefix_file = ctx.attr.python_prefix_file,
        version = ctx.attr.version,
    )
    return [
        info,
        platform_common.ToolchainInfo(python = info),
    ]

python_toolchain_info = rule(
    implementation = _python_toolchain_info_impl,
    attrs = {
        "python_prefix_file": attr.string(mandatory = True),
        "version": attr.string(mandatory = True),
    },
)
