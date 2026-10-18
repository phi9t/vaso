"""Provider adapter targets for public torch artifact repositories."""

load("//native/pytorch:pytorch_action.bzl", "PytorchNativePrefixInfo")

def _torch_artifact_prefix_impl(ctx):
    return [
        DefaultInfo(files = depset(
            ctx.files.srcs + [
                ctx.file.anchor,
                ctx.file.build_plan,
                ctx.file.provider_metadata,
                ctx.file.wheel,
            ],
        )),
        PytorchNativePrefixInfo(
            prefix = ctx.file.anchor,
            wheel = ctx.file.wheel,
            build_plan = ctx.file.build_plan,
            provider_metadata = ctx.file.provider_metadata,
            package = "py-torch",
            version = "2.14.0",
            python_abi = ctx.attr.python_abi,
        ),
    ]

torch_artifact_prefix = rule(
    implementation = _torch_artifact_prefix_impl,
    attrs = {
        "anchor": attr.label(allow_single_file = True, mandatory = True),
        "build_plan": attr.label(allow_single_file = True, mandatory = True),
        "provider_metadata": attr.label(allow_single_file = True, mandatory = True),
        "python_abi": attr.string(mandatory = True),
        "srcs": attr.label_list(allow_files = True),
        "wheel": attr.label(allow_single_file = True, mandatory = True),
    },
    doc = "Adapt extracted torch artifact source files to PytorchNativePrefixInfo.",
)

def _torch_artifact_missing_prefix_impl(ctx):
    if ctx.attr.reason:
        fail(ctx.attr.reason)
    fail("no torch_artifact tag declared for CUDA line {}".format(ctx.attr.line))

torch_artifact_missing_prefix = rule(
    implementation = _torch_artifact_missing_prefix_impl,
    attrs = {
        "line": attr.string(mandatory = True),
        "reason": attr.string(),
    },
)
