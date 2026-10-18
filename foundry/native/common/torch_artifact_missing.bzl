"""Small failing rule for unavailable public torch artifact prefixes."""

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
