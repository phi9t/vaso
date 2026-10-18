"""Action-backed xz native prefix."""

load("//native/common:committed_plan_prefix.bzl", "committed_plan_prefix")

def xz_action_prefix(name, visibility = None, **kwargs):
    committed_plan_prefix(
        name = name,
        package = "xz",
        version = "5.8.3",
        mnemonic = "XzActionPrefix",
        visibility = visibility,
        **kwargs
    )
