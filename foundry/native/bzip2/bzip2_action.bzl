"""Action-backed bzip2 native prefix."""

load("//native/common:committed_plan_prefix.bzl", "committed_plan_prefix")

def bzip2_action_prefix(name, visibility = None, **kwargs):
    committed_plan_prefix(
        name = name,
        package = "bzip2",
        version = "1.0.8",
        mnemonic = "Bzip2ActionPrefix",
        visibility = visibility,
        **kwargs
    )
