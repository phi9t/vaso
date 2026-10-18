def _fixture_rule_impl(ctx):
    return [platform_common.ToolchainInfo()]

_cc_library = rule(implementation = _fixture_rule_impl)

_cc_toolchain = rule(
    implementation = _fixture_rule_impl,
    attrs = {
        "all_files": attr.label(),
        "ar_files": attr.label(),
        "compiler_files": attr.label(),
        "dwp_files": attr.label(),
        "linker_files": attr.label(),
        "objcopy_files": attr.label(),
        "strip_files": attr.label(),
        "supports_param_files": attr.bool(),
        "toolchain_config": attr.label(),
        "toolchain_identifier": attr.string(),
    },
)

def cc_library(name, **_kwargs):
    _cc_library(name = name)

def cc_binary(name, **_kwargs):
    _cc_library(name = name)

def cc_toolchain(name, **kwargs):
    _cc_toolchain(
        name = name,
        all_files = kwargs.get("all_files"),
        ar_files = kwargs.get("ar_files"),
        compiler_files = kwargs.get("compiler_files"),
        dwp_files = kwargs.get("dwp_files"),
        linker_files = kwargs.get("linker_files"),
        objcopy_files = kwargs.get("objcopy_files"),
        strip_files = kwargs.get("strip_files"),
        supports_param_files = kwargs.get("supports_param_files", False),
        toolchain_config = kwargs.get("toolchain_config"),
        toolchain_identifier = kwargs.get("toolchain_identifier", ""),
    )
