"""Singleton query targets for native action outputs."""

load("//native/common:release_wheel_action.bzl", "ReleaseWheelInfo")
load("//native/common:runtime_wheelhouse_action.bzl", "RuntimeWheelhouseInfo")


def _native_action_prefix_file_impl(ctx):
    files = ctx.attr.target[DefaultInfo].files.to_list()
    if len(files) != 1:
        fail("{} must expose exactly one native action prefix path, got {}".format(ctx.attr.target.label, len(files)))
    return [DefaultInfo(files = depset([files[0]]))]


native_action_prefix_file = rule(
    implementation = _native_action_prefix_file_impl,
    attrs = {
        "target": attr.label(mandatory = True),
    },
    doc = "Expose exactly one native action prefix output for cquery --output=files.",
)


def _release_manifest_file_impl(ctx):
    target = ctx.attr.target
    if ReleaseWheelInfo in target:
        return [DefaultInfo(files = depset([target[ReleaseWheelInfo].manifest]))]
    if RuntimeWheelhouseInfo in target:
        return [DefaultInfo(files = depset([target[RuntimeWheelhouseInfo].manifest]))]
    fail("{} does not provide a release wheel manifest".format(ctx.attr.target.label))


release_manifest_file = rule(
    implementation = _release_manifest_file_impl,
    attrs = {
        "target": attr.label(mandatory = True),
    },
    doc = "Expose exactly one release manifest output for cquery --output=files.",
)
