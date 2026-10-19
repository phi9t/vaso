"""Action-backed zlib-ng native prefix pilot."""

load("//native/common:build_settings.bzl", "ReleaseCudaLineInfo")
load("//native/common:native_action_toolchain.bzl", "rootfs_platform_forward_attrs")

NativeActionPrefixInfo = provider(
    doc = "Install prefix produced by a normal Bazel build action.",
    fields = {
        "prefix": "Declared directory output containing the install prefix.",
        "package": "Spack package name.",
        "version": "Pinned upstream version.",
    },
)

_ZLIB_NG_ACTION_CPUS = 4
_ZLIB_NG_ACTION_MEMORY_MB = 4096

def _zlib_ng_resource_set(os_name, input_count):
    return {
        "cpu": _ZLIB_NG_ACTION_CPUS,
        "memory": _ZLIB_NG_ACTION_MEMORY_MB,
    }

def _zlib_ng_action_prefix_impl(ctx):
    output_basename = ctx.attr.output_basename or ctx.label.name
    prefix = ctx.actions.declare_directory(output_basename)
    line = ctx.attr._line[ReleaseCudaLineInfo].value
    if "/{}/".format(line) not in ctx.file.plan.path:
        fail("zlib-ng plan {} does not match release_line={}".format(ctx.file.plan.path, line))
    if "/{}/".format(line) not in ctx.file.rootfs_identity.path:
        fail("zlib-ng rootfs identity {} does not match release_line={}".format(ctx.file.rootfs_identity.path, line))

    action_inputs = [
        ctx.file._executor,
        ctx.file._executor_schema,
        ctx.file._schema,
        ctx.file.plan,
        ctx.file.rootfs_identity,
        ctx.file.source_archive,
    ]
    arguments = [
        "--plan",
        ctx.file.plan.path,
        "--schema",
        ctx.file._schema.path,
        "--source-archive",
        "main=" + ctx.file.source_archive.path,
        "--rootfs-identity",
        ctx.file.rootfs_identity.path,
        "--prefix-out",
        prefix.path,
    ]

    for target, plan_path in sorted(ctx.attr.patches.items(), key = lambda item: item[1]):
        files = target.files.to_list()
        if len(files) != 1:
            fail("patch {} must provide exactly one file".format(target.label))
        action_inputs.append(files[0])
        arguments.extend(["--patch", plan_path + "=" + files[0].path])

    for target, dep_name in sorted(ctx.attr.deps.items(), key = lambda item: item[1]):
        files = target.files.to_list()
        if len(files) != 1:
            fail("dependency {} must provide exactly one prefix tree".format(target.label))
        action_inputs.append(files[0])
        arguments.extend(["--dep-prefix", dep_name + "=" + files[0].path])

    for target, plan_path in sorted(ctx.attr.inputs.items(), key = lambda item: item[1]):
        files = target.files.to_list()
        if len(files) != 1:
            fail("extra input {} must provide exactly one file".format(target.label))
        action_inputs.append(files[0])
        arguments.extend(["--input", plan_path + "=" + files[0].path])

    # The committed plan is the compiler source of truth for this pilot; Bazel's
    # C++ toolchain is deliberately not resolved here.
    ctx.actions.run(
        inputs = depset(action_inputs),
        outputs = [prefix],
        executable = "python3",
        arguments = [ctx.file._executor.path] + arguments,
        env = {
            "LC_ALL": "C",
            "PATH": "/usr/bin:/bin",
            "PYTHONHASHSEED": "0",
            "VASO_ACTION_RESERVED_CPUS": str(_ZLIB_NG_ACTION_CPUS),
        },
        mnemonic = "ZlibNgActionPrefix",
        progress_message = "Building zlib-ng action prefix %{label}",
        resource_set = _zlib_ng_resource_set,
        use_default_shell_env = False,
    )

    return [
        DefaultInfo(files = depset([prefix])),
        NativeActionPrefixInfo(
            prefix = prefix,
            package = "zlib-ng",
            version = "2.3.3",
        ),
    ]

_zlib_ng_action_prefix = rule(
    implementation = _zlib_ng_action_prefix_impl,
    attrs = {
        "plan": attr.label(
            allow_single_file = True,
            mandatory = True,
            doc = "Committed zlib-ng build plan for the selected line.",
        ),
        "rootfs_identity": attr.label(
            allow_single_file = True,
            mandatory = True,
            doc = "Committed rootfs line identity slice for the selected line.",
        ),
        "source_archive": attr.label(
            allow_single_file = True,
            mandatory = True,
            doc = "Pinned zlib-ng source archive.",
        ),
        "patches": attr.label_keyed_string_dict(
            allow_files = True,
            doc = "Patch files keyed by label, with values matching sources[].patches[].path in the plan.",
        ),
        "deps": attr.label_keyed_string_dict(
            allow_files = True,
            doc = "Dependency prefix tree labels keyed by label, with values matching deps keys in the plan.",
        ),
        "inputs": attr.label_keyed_string_dict(
            allow_files = True,
            doc = "Extra input files keyed by label, with values matching inputs[].path in the plan.",
        ),
        "output_basename": attr.string(),
        "_executor": attr.label(
            default = Label("//native/common:plan_executor.py"),
            allow_single_file = True,
        ),
        "_executor_schema": attr.label(
            default = Label("//native/common:plan_schema.py"),
            allow_single_file = True,
        ),
        "_schema": attr.label(
            default = Label("//plans:schema/v1.json"),
            allow_single_file = True,
        ),
    } | {
        "_line": attr.label(default = Label("//native/common:release_line")),
    },
    doc = "Build zlib-ng into a declared directory output using a Bazel action.",
)


def _zlib_ng_action_prefix_forward_impl(ctx):
    actual = ctx.attr.actual
    if type(actual) == "list":
        if len(actual) != 1:
            fail("expected one transitioned zlib-ng action target, got {}".format(len(actual)))
        actual = actual[0]
    return [
        actual[DefaultInfo],
        actual[NativeActionPrefixInfo],
    ]


_zlib_ng_action_prefix_forward = rule(
    implementation = _zlib_ng_action_prefix_forward_impl,
    attrs = rootfs_platform_forward_attrs("torch"),
)


def zlib_ng_action_prefix(name, visibility = None, **kwargs):
    impl_name = name + "_rootfs_impl"
    _zlib_ng_action_prefix(
        name = impl_name,
        output_basename = name,
        visibility = ["//visibility:private"],
        **kwargs
    )
    _zlib_ng_action_prefix_forward(
        name = name,
        actual = ":" + impl_name,
        visibility = visibility,
    )
