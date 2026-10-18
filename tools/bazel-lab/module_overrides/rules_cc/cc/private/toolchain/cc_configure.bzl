def _cc_configure_impl(repository_ctx):
    repository_ctx.file("BUILD.bazel", "")

cc_configure = repository_rule(implementation = _cc_configure_impl)
