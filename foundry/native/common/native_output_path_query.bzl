"""Lightweight cquery-only targets for transitioned native output paths."""

load("//native/common:native_action_toolchain.bzl", "rootfs_platform_forward_attrs")


def _native_declared_directory_path_impl(ctx):
    prefix = ctx.actions.declare_directory(ctx.attr.output_basename + "_prefix")
    ctx.actions.run_shell(
        outputs = [prefix],
        command = "echo 'native output path query targets are cquery-only' >&2; exit 2",
        mnemonic = "VasoNativeOutputPathQuery",
        progress_message = "Declaring native output path %{label}",
    )
    return [DefaultInfo(files = depset([prefix]))]


_native_declared_directory_path = rule(
    implementation = _native_declared_directory_path_impl,
    attrs = {
        "output_basename": attr.string(mandatory = True),
    },
    doc = "Declare a native directory output path for cquery --output=files only.",
)


def _native_declared_directory_path_forward_impl(ctx):
    actual = ctx.attr.actual
    if type(actual) == "list":
        if len(actual) != 1:
            fail("expected one transitioned native output path target, got {}".format(len(actual)))
        actual = actual[0]
    return [actual[DefaultInfo]]


def _native_declared_directory_path_forward(profile):
    return rule(
        implementation = _native_declared_directory_path_forward_impl,
        attrs = rootfs_platform_forward_attrs(profile),
        doc = "Forward a cquery-only native output path through the rootfs transition.",
    )


_torch_native_declared_directory_path_forward = _native_declared_directory_path_forward("torch")
_jax_native_declared_directory_path_forward = _native_declared_directory_path_forward("jax")


def native_action_prefix_path(name, profile, output_basename, visibility = None, tags = None):
    """Expose the transitioned native action prefix path without native deps."""
    impl_name = name + "_rootfs_impl"
    impl_kwargs = {
        "name": impl_name,
        "output_basename": output_basename,
        "visibility": ["//visibility:private"],
    }
    if tags != None:
        impl_kwargs["tags"] = tags
    _native_declared_directory_path(**impl_kwargs)
    kwargs = {
        "name": name,
        "actual": ":" + impl_name,
    }
    if tags != None:
        kwargs["tags"] = tags
    if visibility != None:
        kwargs["visibility"] = visibility
    if profile == "torch":
        _torch_native_declared_directory_path_forward(**kwargs)
    elif profile == "jax":
        _jax_native_declared_directory_path_forward(**kwargs)
    else:
        fail("native action profile must be torch or jax, got {}".format(profile))
