"""Shared build-setting providers for release and native toolchain actions."""

ReleaseCudaLineInfo = provider(
    doc = "CUDA line selected for release-wheel repair and rootfs toolchain metadata.",
    fields = {"value": "cu129 or cu130"},
)

ReleaseGitShaInfo = provider(
    doc = "Git sha suffix selected for release-wheel versions.",
    fields = {"value": "Release source sha; the repair tool uses the first 8 chars."},
)

NativeActionStringInfo = provider(
    doc = "String build setting consumed by native action and rootfs toolchain helpers.",
    fields = {"value": "Selected string value."},
)


def _release_cuda_line_impl(ctx):
    value = ctx.build_setting_value
    if value not in ("cu129", "cu130"):
        fail("release_line must be cu129 or cu130, got {}".format(value))
    return [ReleaseCudaLineInfo(value = value)]


release_cuda_line = rule(
    implementation = _release_cuda_line_impl,
    build_setting = config.string(flag = True),
    doc = "Selected CUDA line for repaired release wheel versioning and rootfs allowlist.",
)


def _release_git_sha_impl(ctx):
    value = ctx.build_setting_value
    if not value:
        fail("release_git_sha must be non-empty")
    return [ReleaseGitShaInfo(value = value)]


release_git_sha = rule(
    implementation = _release_git_sha_impl,
    build_setting = config.string(flag = True),
    doc = "Git SHA recorded in repaired release wheel versions.",
)


def _native_action_string_flag_impl(ctx):
    return [NativeActionStringInfo(value = ctx.build_setting_value)]


native_action_string_flag = rule(
    implementation = _native_action_string_flag_impl,
    build_setting = config.string(flag = True),
)
