"""Committed-plan native torchaudio prefix rule."""

load("//native/common:build_settings.bzl", "ReleaseCudaLineInfo")
load("//native/common:native_action_inputs.bzl", "native_action_default_rootfs_identity", "native_action_prefix_manifest_attrs", "native_action_prefix_manifest_inputs", "native_action_prefix_manifest_map", "native_action_rootfs_identity_attrs", "native_action_rootfs_identity_file")
load("//native/common:native_action_toolchain.bzl", "rootfs_platform_forward_attrs")
load("//native/pytorch:pytorch_action.bzl", "PytorchNativePrefixInfo")

TorchaudioNativePrefixInfo = provider(
    doc = "Install prefix produced by the token-gated native torchaudio action.",
    fields = {
        "prefix": "Declared directory output containing the torchaudio prefix.",
        "wheel": "Declared wheel artifact copied out of the build flow.",
        "build_plan": "Committed plan JSON copied for inspection.",
        "provider_metadata": "Native provider metadata JSON.",
        "package": "Spack package name.",
        "version": "Pinned torchaudio release.",
        "python_abi": "Python ABI tag supplied by the caller.",
    },
)

TorchaudioTokenInfo = provider(
    doc = "Command-line token value for the native torchaudio execute action.",
    fields = {
        "value": "Token value supplied through --//native/torchaudio:token.",
    },
)

_REQUIRED_TOKEN = "build-native-torchaudio"
_TORCHAUDIO_ACTION_CPUS = 4
_TORCHAUDIO_ACTION_MEMORY_MB = 16384
_PREFIX_KEY_ATTRS = (
    ("python", "python_prefix_file"),
    ("python-venv", "python_venv_prefix_file"),
    ("py-pip", "py_pip_prefix_file"),
    ("py-setuptools", "py_setuptools_prefix_file"),
    ("py-wheel", "py_wheel_prefix_file"),
    ("py-filelock", "py_filelock_prefix_file"),
    ("cuda", "cuda_prefix_file"),
    ("ninja", "ninja_prefix_file"),
)


def _torchaudio_resource_set(os_name, input_count):
    return {
        "cpu": _TORCHAUDIO_ACTION_CPUS,
        "memory": _TORCHAUDIO_ACTION_MEMORY_MB,
    }


def _torchaudio_token_flag_impl(ctx):
    return [TorchaudioTokenInfo(value = ctx.build_setting_value)]


def _prefix_manifest_files_by_key(ctx):
    expected = {key: attr_name for key, attr_name in _PREFIX_KEY_ATTRS}
    by_key = {}
    for target, dep_key in sorted(ctx.attr.prefix_manifest_files.items(), key = lambda item: item[1]):
        if dep_key not in expected:
            fail("{} declares unexpected dependency content manifest key {}".format(ctx.label, dep_key))
        files = target.files.to_list()
        if len(files) != 1:
            fail("{} dependency content manifest {} must provide exactly one file".format(ctx.label, target.label))
        if dep_key in by_key:
            fail("{} declares dependency content manifest key {} more than once".format(ctx.label, dep_key))
        by_key[dep_key] = files[0]
    missing = [key for key, _attr_name in _PREFIX_KEY_ATTRS if key not in by_key]
    if missing:
        fail("{} is missing dependency content manifest(s): {}".format(ctx.label, ", ".join(missing)))
    return by_key


def _repo_dep_prefix_args(ctx):
    by_key = _prefix_manifest_files_by_key(ctx)
    args = []
    for key, _attr_name in _PREFIX_KEY_ATTRS:
        manifest = by_key[key]
        args.extend(["--dep-prefix", "{}={}".format(key, manifest.dirname + "/prefix")])
    return args


def _line_checked_file(ctx, file, *, profile, consumer, kind):
    line = ctx.attr._line[ReleaseCudaLineInfo].value
    required_fragment = "/{}/{}/".format(profile, line)
    if required_fragment not in file.path:
        fail("{} {} {} does not match profile={} release_line={}".format(consumer, kind, file.path, profile, line))
    return file


def _torchaudio_action_prefix_impl(ctx):
    output_basename = ctx.attr.output_basename or ctx.label.name
    prefix = ctx.actions.declare_directory(output_basename + "_prefix")
    wheel = ctx.actions.declare_file(output_basename + "_wheel.whl")
    build_plan = ctx.actions.declare_file(output_basename + "_build_plan.json")
    provider_metadata = ctx.actions.declare_file(output_basename + "_provider_metadata.json")
    result_marker = ctx.actions.declare_file(output_basename + "_result.txt")
    torch_prefix_manifest = ctx.actions.declare_file(output_basename + "_torch_prefix_manifest.json")
    torch_provider = ctx.attr.torch_prefix[PytorchNativePrefixInfo]
    token = ctx.attr._token_flag[TorchaudioTokenInfo].value
    if not ctx.attr.execute:
        fail("torchaudio committed-plan action requires execute=True")
    if token != _REQUIRED_TOKEN:
        fail("full native torchaudio build requires token {}; set //native/torchaudio:token to that value".format(_REQUIRED_TOKEN))

    plan = _line_checked_file(ctx, ctx.file.plan, profile = "torch", consumer = "torchaudio", kind = "plan")
    rootfs_identity = native_action_rootfs_identity_file(ctx, profile = "torch", consumer = "torchaudio")
    dep_manifest_inputs = native_action_prefix_manifest_inputs(
        ctx,
        _PREFIX_KEY_ATTRS,
        require_all = True,
    )
    torch_dep_prefix_args = ["--dep-prefix", "torch=" + torch_provider.prefix.path]
    repo_dep_prefix_args = _repo_dep_prefix_args(ctx)

    ctx.actions.run(
        inputs = depset([ctx.file._prefix_manifest_writer, torch_provider.prefix]),
        outputs = [torch_prefix_manifest],
        executable = "python3",
        arguments = [
            ctx.file._prefix_manifest_writer.path,
            "--prefix",
            torch_provider.prefix.path,
            "--out",
            torch_prefix_manifest.path,
            "--name",
            "torch",
        ],
        env = {
            "LC_ALL": "C",
            "PATH": "/usr/bin:/bin",
            "PYTHONHASHSEED": "0",
        },
        mnemonic = "TorchaudioTorchPrefixManifest",
        progress_message = "Writing PyTorch prefix content manifest %{label}",
        use_default_shell_env = False,
    )

    action_inputs = [
        ctx.file._executor,
        ctx.file._executor_schema,
        ctx.file._schema,
        plan,
        rootfs_identity,
        ctx.file.source_archive,
        torch_prefix_manifest,
    ] + dep_manifest_inputs
    arguments = [
        "--plan",
        plan.path,
        "--schema",
        ctx.file._schema.path,
        "--source-archive",
        "main=" + ctx.file.source_archive.path,
        "--rootfs-identity",
        rootfs_identity.path,
        "--prefix-out",
        prefix.path,
        "--wheel-out",
        wheel.path,
    ]

    for target, plan_path in sorted(ctx.attr.patches.items(), key = lambda item: item[1]):
        files = target.files.to_list()
        if len(files) != 1:
            fail("patch {} must provide exactly one file".format(target.label))
        action_inputs.append(files[0])
        arguments.extend(["--patch", plan_path + "=" + files[0].path])

    for target, plan_path in sorted(ctx.attr.inputs.items(), key = lambda item: item[1]):
        files = target.files.to_list()
        if len(files) != 1:
            fail("extra input {} must provide exactly one file".format(target.label))
        action_inputs.append(files[0])
        arguments.extend(["--input", plan_path + "=" + files[0].path])

    arguments.extend(torch_dep_prefix_args)
    arguments.extend(repo_dep_prefix_args)

    ctx.actions.run(
        inputs = depset(action_inputs),
        outputs = [prefix, wheel],
        executable = "python3",
        arguments = [ctx.file._executor.path] + arguments,
        env = {
            "LC_ALL": "C",
            "PATH": "/usr/bin:/bin",
            "PYTHONHASHSEED": "0",
            "VASO_ACTION_RESERVED_CPUS": str(_TORCHAUDIO_ACTION_CPUS),
        },
        mnemonic = "TorchaudioNativePrefix",
        progress_message = "Building native torchaudio wheel %{label}",
        resource_set = _torchaudio_resource_set,
        # N1-4 moves prefix trees into declared action inputs; until then the
        # executor reads manifest-derived prefix directories undeclared.
        execution_requirements = {"no-sandbox": "1"},
        use_default_shell_env = False,
    )

    metadata_args = [
        ctx.file._metadata_writer.path,
        "--plan",
        plan.path,
        "--keys",
        ctx.file._keys.path,
        "--rootfs-identity",
        rootfs_identity.path,
        "--wheel",
        wheel.path,
        "--build-plan-out",
        build_plan.path,
        "--provider-metadata-out",
        provider_metadata.path,
        "--result-marker-out",
        result_marker.path,
        "--python-abi",
        ctx.attr.python_abi,
    ] + torch_dep_prefix_args + repo_dep_prefix_args
    ctx.actions.run(
        inputs = depset([ctx.file._metadata_writer, plan, ctx.file._keys, rootfs_identity, wheel]),
        outputs = [build_plan, provider_metadata, result_marker],
        executable = "python3",
        arguments = metadata_args,
        env = {
            "LC_ALL": "C",
            "PATH": "/usr/bin:/bin",
            "PYTHONHASHSEED": "0",
        },
        mnemonic = "TorchaudioNativeMetadata",
        progress_message = "Writing native torchaudio metadata %{label}",
        use_default_shell_env = False,
    )

    return [
        DefaultInfo(files = depset([prefix, wheel, build_plan, provider_metadata, result_marker])),
        TorchaudioNativePrefixInfo(
            prefix = prefix,
            wheel = wheel,
            build_plan = build_plan,
            provider_metadata = provider_metadata,
            package = "py-torchaudio",
            version = "2.11.0",
            python_abi = ctx.attr.python_abi,
        ),
    ]


torchaudio_token_flag = rule(
    implementation = _torchaudio_token_flag_impl,
    build_setting = config.string(flag = True),
    doc = "Command-line token gate for the native torchaudio execute action.",
)


_torchaudio_action_prefix = rule(
    implementation = _torchaudio_action_prefix_impl,
    attrs = {
        "torch_prefix": attr.label(
            providers = [PytorchNativePrefixInfo],
            mandatory = True,
            doc = "Native PyTorch action prefix provider for the selected CUDA line.",
        ),
        "plan": attr.label(
            allow_single_file = True,
            mandatory = True,
            doc = "Committed torchaudio build plan for the selected line.",
        ),
        "rootfs_identity": attr.label(
            allow_single_file = True,
            mandatory = True,
            doc = "Committed rootfs line identity slice for the selected line.",
        ),
        "source_archive": attr.label(
            allow_single_file = True,
            mandatory = True,
            doc = "Pinned torchaudio source archive.",
        ),
        "patches": attr.label_keyed_string_dict(
            allow_files = True,
            doc = "Patch files keyed by label, with values matching sources[].patches[].path in the plan.",
        ),
        "inputs": attr.label_keyed_string_dict(
            allow_files = True,
            doc = "Extra input files keyed by label, with values matching inputs[].path in the plan.",
        ),
        "python_prefix_file": attr.label(allow_single_file = True, mandatory = True),
        "python_venv_prefix_file": attr.label(allow_single_file = True, mandatory = True),
        "py_pip_prefix_file": attr.label(allow_single_file = True, mandatory = True),
        "py_setuptools_prefix_file": attr.label(allow_single_file = True, mandatory = True),
        "py_wheel_prefix_file": attr.label(allow_single_file = True, mandatory = True),
        "py_filelock_prefix_file": attr.label(allow_single_file = True, mandatory = True),
        "cuda_prefix_file": attr.label(allow_single_file = True, mandatory = True),
        "ninja_prefix_file": attr.label(allow_single_file = True, mandatory = True),
        "python_abi": attr.string(default = "derived"),
        "execute": attr.bool(default = False),
        "output_basename": attr.string(),
        "_token_flag": attr.label(
            default = Label("//native/torchaudio:token"),
        ),
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
        "_keys": attr.label(
            default = Label("//plans:keys.json"),
            allow_single_file = True,
        ),
        "_metadata_writer": attr.label(
            default = Label("//native/torchaudio:action_driver.py"),
            allow_single_file = True,
        ),
        "_prefix_manifest_writer": attr.label(
            default = Label("//native/common:prefix_manifest.py"),
            allow_single_file = True,
        ),
    } | native_action_prefix_manifest_attrs() | native_action_rootfs_identity_attrs(),
    doc = (
        "Build native torchaudio from a committed plan. Full build requires the " +
        "'{}' token and execute=True.".format(_REQUIRED_TOKEN)
    ),
)


def _torchaudio_action_prefix_forward_impl(ctx):
    actual = ctx.attr.actual
    if type(actual) == "list":
        if len(actual) != 1:
            fail("expected one transitioned torchaudio action target, got {}".format(len(actual)))
        actual = actual[0]
    return [
        actual[DefaultInfo],
        actual[TorchaudioNativePrefixInfo],
    ]


_torchaudio_action_prefix_forward = rule(
    implementation = _torchaudio_action_prefix_forward_impl,
    attrs = rootfs_platform_forward_attrs("torch"),
)


def torchaudio_action_prefix(name, visibility = None, **kwargs):
    impl_name = name + "_rootfs_impl"
    if "prefix_manifest_files" not in kwargs:
        kwargs["prefix_manifest_files"] = native_action_prefix_manifest_map(_PREFIX_KEY_ATTRS, kwargs)
    if "rootfs_identity" not in kwargs:
        kwargs["rootfs_identity"] = native_action_default_rootfs_identity("torch")
    _torchaudio_action_prefix(
        name = impl_name,
        output_basename = name,
        visibility = ["//visibility:private"],
        **kwargs
    )
    _torchaudio_action_prefix_forward(
        name = name,
        actual = ":" + impl_name,
        visibility = visibility,
    )
