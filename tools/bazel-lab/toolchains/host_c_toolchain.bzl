def _host_c_toolchain_impl(ctx):
    return [
        platform_common.ToolchainInfo(
            cc = ctx.attr.cc,
            driver = ctx.executable.driver,
        ),
    ]

host_c_toolchain = rule(
    implementation = _host_c_toolchain_impl,
    attrs = {
        "cc": attr.string(default = "gcc"),
        "driver": attr.label(
            allow_files = True,
            cfg = "exec",
            executable = True,
            mandatory = True,
        ),
    },
)
