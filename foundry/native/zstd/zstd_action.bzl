"""Action-backed zstd native prefix."""

load("//native/common:committed_plan_prefix.bzl", "committed_plan_prefix")

def zstd_action_prefix(name, visibility = None, **kwargs):
    committed_plan_prefix(
        name = name,
        package = "zstd",
        version = "1.5.7",
        mnemonic = "ZstdActionPrefix",
        visibility = visibility,
        **kwargs
    )
