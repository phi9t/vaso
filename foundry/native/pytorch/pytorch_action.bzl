"""Committed-plan native PyTorch prefix rule."""

load("//native/common:build_settings.bzl", "ReleaseCudaLineInfo")
load("//native/common:native_action_inputs.bzl", "native_action_default_rootfs_identity", "native_action_dep_prefix_args", "native_action_prefix_manifest_attrs", "native_action_prefix_manifest_inputs", "native_action_prefix_manifest_map", "native_action_prefix_path", "native_action_rootfs_identity_attrs", "native_action_rootfs_identity_file")
load("//native/common:native_action_toolchain.bzl", "rootfs_platform_forward_attrs")

PytorchNativePrefixInfo = provider(
    doc = "Install prefix produced by the token-gated native PyTorch action.",
    fields = {
        "prefix": "Declared directory output containing the PyTorch prefix.",
        "wheel": "Declared wheel artifact copied out of the build flow.",
        "build_plan": "Committed plan JSON copied for inspection.",
        "provider_metadata": "Native provider metadata JSON.",
        "package": "Spack package name.",
        "version": "Pinned PyTorch release.",
        "python_abi": "Python ABI tag supplied by the caller or derived from the plan.",
    },
)

PytorchTokenInfo = provider(
    doc = "Command-line token value for the native PyTorch execute action.",
    fields = {
        "value": "Token value supplied through --//native/pytorch:token.",
    },
)

_REQUIRED_TOKEN = "build-native-pytorch"
_PYTORCH_ACTION_CPUS = 4
_PYTORCH_ACTION_MEMORY_MB = 65536
_PREFIX_KEY_ATTRS = (
    ("cuda", "cuda_prefix_file"),
    ("cudnn", "cudnn_prefix_file"),
    ("nccl", "nccl_prefix_file"),
    ("python", "python_prefix_file"),
    ("cmake", "cmake_prefix_file"),
    ("ninja", "ninja_prefix_file"),
    ("openblas", "openblas_prefix_file"),
    ("protobuf", "protobuf_prefix_file"),
    ("cusparselt", "cusparselt_prefix_file"),
    ("cudss", "cudss_prefix_file"),
    ("nvshmem", "nvshmem_prefix_file"),
    ("openmpi", "openmpi_prefix_file"),
    ("libevent", "libevent_prefix_file"),
    ("hwloc", "hwloc_prefix_file"),
    ("libpciaccess", "libpciaccess_prefix_file"),
    ("libxml2", "libxml2_prefix_file"),
    ("libiconv", "libiconv_prefix_file"),
    ("pmix", "pmix_prefix_file"),
    ("numactl", "numactl_prefix_file"),
    ("cpuinfo", "cpuinfo_prefix_file"),
    ("fp16", "fp16_prefix_file"),
    ("fxdiv", "fxdiv_prefix_file"),
    ("psimd", "psimd_prefix_file"),
    ("pthreadpool", "pthreadpool_prefix_file"),
    ("py-pip", "py_pip_prefix_file"),
    ("py-setuptools", "py_setuptools_prefix_file"),
    ("py-wheel", "py_wheel_prefix_file"),
    ("py-scikit-build-core", "py_scikit_build_core_prefix_file"),
    ("py-numpy", "py_numpy_prefix_file"),
    ("py-pyyaml", "py_pyyaml_prefix_file"),
    ("py-typing-extensions", "py_typing_extensions_prefix_file"),
    ("py-six", "py_six_prefix_file"),
    ("py-packaging", "py_packaging_prefix_file"),
    ("py-pathspec", "py_pathspec_prefix_file"),
    ("py-protobuf", "py_protobuf_prefix_file"),
    ("zstd", "zstd_prefix_file"),
)


def _pytorch_resource_set(os_name, input_count):
    return {
        "cpu": _PYTORCH_ACTION_CPUS,
        "memory": _PYTORCH_ACTION_MEMORY_MB,
    }


def _pytorch_token_flag_impl(ctx):
    return [PytorchTokenInfo(value = ctx.build_setting_value)]


def _line_checked_file(ctx, file, *, profile, consumer, kind):
    line = ctx.attr._line[ReleaseCudaLineInfo].value
    required_fragment = "/{}/{}/".format(profile, line)
    if required_fragment not in file.path:
        fail("{} {} {} does not match profile={} release_line={}".format(consumer, kind, file.path, profile, line))
    return file


def _pytorch_action_prefix_impl(ctx):
    output_basename = ctx.attr.output_basename or ctx.label.name
    prefix = ctx.actions.declare_directory(output_basename + "_prefix")
    wheel = ctx.actions.declare_file(output_basename + "_wheel.whl")
    build_plan = ctx.actions.declare_file(output_basename + "_build_plan.json")
    provider_metadata = ctx.actions.declare_file(output_basename + "_provider_metadata.json")
    result_marker = ctx.actions.declare_file(output_basename + "_result.txt")
    token = ctx.attr._token_flag[PytorchTokenInfo].value
    if not ctx.attr.execute:
        fail("PyTorch committed-plan action requires execute=True")
    if token != _REQUIRED_TOKEN:
        fail("full native PyTorch build requires token {}; set //native/pytorch:token to that value".format(_REQUIRED_TOKEN))

    line = ctx.attr._line[ReleaseCudaLineInfo].value
    plan = _line_checked_file(ctx, ctx.file.plan, profile = "torch", consumer = "PyTorch", kind = "plan")
    rootfs_identity = native_action_rootfs_identity_file(ctx, profile = "torch", consumer = "PyTorch")
    dep_manifest_inputs = native_action_prefix_manifest_inputs(
        ctx,
        _PREFIX_KEY_ATTRS,
        require_all = True,
    )
    dep_prefix_args = native_action_dep_prefix_args(ctx, _PREFIX_KEY_ATTRS)
    zstd_prefix = native_action_prefix_path(ctx, _PREFIX_KEY_ATTRS, "zstd")
    source_manifest_plan_path = "native/pytorch/pytorch-v2.14.0-2b3ec348-submodules.json"

    action_inputs = [
        ctx.file._runtime_executor,
        ctx.file._executor,
        ctx.file._executor_schema,
        ctx.file._schema,
        plan,
        rootfs_identity,
        ctx.file.source_archive,
        ctx.file.source_manifest,
    ] + dep_manifest_inputs
    arguments = [
        "--executor",
        ctx.file._executor.path,
        "--runtime-jobs-file",
        "/vaso/lines/{}/state/native/pytorch_jobs.txt".format(line),
        "--zstd-prefix",
        zstd_prefix,
        "--",
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
        "--input",
        source_manifest_plan_path + "=" + ctx.file.source_manifest.path,
    ]

    for target, plan_path in sorted(ctx.attr.inputs.items(), key = lambda item: item[1]):
        files = target.files.to_list()
        if len(files) != 1:
            fail("extra input {} must provide exactly one file".format(target.label))
        action_inputs.append(files[0])
        arguments.extend(["--input", plan_path + "=" + files[0].path])

    arguments.extend(dep_prefix_args)

    ctx.actions.run(
        inputs = depset(action_inputs),
        outputs = [prefix, wheel],
        executable = "python3",
        arguments = [ctx.file._runtime_executor.path] + arguments,
        env = {
            "LC_ALL": "C",
            "PATH": "/usr/bin:/bin",
            "PYTHONHASHSEED": "0",
            "VASO_ACTION_RESERVED_CPUS": str(_PYTORCH_ACTION_CPUS),
        },
        mnemonic = "PytorchNativePrefix",
        progress_message = "Building native PyTorch wheel %{label}",
        resource_set = _pytorch_resource_set,
        # Repo-rule prefixes still read manifest-derived directories until
        # their packages move to action prefixes.
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
    ] + dep_prefix_args
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
        mnemonic = "PytorchNativeMetadata",
        progress_message = "Writing native PyTorch metadata %{label}",
        use_default_shell_env = False,
    )

    return [
        DefaultInfo(files = depset([prefix, wheel, build_plan, provider_metadata, result_marker])),
        PytorchNativePrefixInfo(
            prefix = prefix,
            wheel = wheel,
            build_plan = build_plan,
            provider_metadata = provider_metadata,
            package = "py-torch",
            version = "2.14.0",
            python_abi = ctx.attr.python_abi,
        ),
    ]


pytorch_token_flag = rule(
    implementation = _pytorch_token_flag_impl,
    build_setting = config.string(flag = True),
    doc = "Command-line token gate for the native PyTorch execute action.",
)


_pytorch_action_prefix = rule(
    implementation = _pytorch_action_prefix_impl,
    attrs = {
        "plan": attr.label(
            allow_single_file = True,
            mandatory = True,
            doc = "Committed PyTorch build plan for the selected line.",
        ),
        "rootfs_identity": attr.label(
            allow_single_file = True,
            mandatory = True,
            doc = "Committed rootfs line identity slice for the selected line.",
        ),
        "source_archive": attr.label(
            allow_single_file = True,
            mandatory = True,
            doc = "The sha256-pinned recursive PyTorch source archive.",
        ),
        "source_manifest": attr.label(
            allow_single_file = True,
            default = "//native/pytorch:pytorch-v2.14.0-2b3ec348-submodules.json",
            doc = "Manifest of the recursive PyTorch source archive's third-party submodules.",
        ),
        "inputs": attr.label_keyed_string_dict(
            allow_files = True,
            doc = "Extra input files keyed by label, with values matching inputs[].path in the plan.",
        ),
        "cuda_prefix_file": attr.label(allow_single_file = True, mandatory = True),
        "cudnn_prefix_file": attr.label(allow_single_file = True, mandatory = True),
        "nccl_prefix_file": attr.label(allow_single_file = True, mandatory = True),
        "python_prefix_file": attr.label(allow_single_file = True, mandatory = True),
        "cmake_prefix_file": attr.label(allow_single_file = True, mandatory = True),
        "ninja_prefix_file": attr.label(allow_single_file = True, mandatory = True),
        "openblas_prefix_file": attr.label(allow_single_file = True, mandatory = True),
        "protobuf_prefix_file": attr.label(allow_single_file = True, mandatory = True),
        "cusparselt_prefix_file": attr.label(allow_single_file = True, mandatory = True),
        "cudss_prefix_file": attr.label(allow_single_file = True, mandatory = True),
        "nvshmem_prefix_file": attr.label(allow_single_file = True, mandatory = True),
        "openmpi_prefix_file": attr.label(allow_single_file = True, mandatory = True),
        "libevent_prefix_file": attr.label(allow_single_file = True, mandatory = True),
        "hwloc_prefix_file": attr.label(allow_single_file = True, mandatory = True),
        "libpciaccess_prefix_file": attr.label(allow_single_file = True, mandatory = True),
        "libxml2_prefix_file": attr.label(allow_single_file = True, mandatory = True),
        "libiconv_prefix_file": attr.label(allow_single_file = True, mandatory = True),
        "pmix_prefix_file": attr.label(allow_single_file = True, mandatory = True),
        "numactl_prefix_file": attr.label(allow_single_file = True, mandatory = True),
        "cpuinfo_prefix_file": attr.label(allow_single_file = True, mandatory = True),
        "fp16_prefix_file": attr.label(allow_single_file = True, mandatory = True),
        "fxdiv_prefix_file": attr.label(allow_single_file = True, mandatory = True),
        "psimd_prefix_file": attr.label(allow_single_file = True, mandatory = True),
        "pthreadpool_prefix_file": attr.label(allow_single_file = True, mandatory = True),
        "py_pip_prefix_file": attr.label(allow_single_file = True, mandatory = True),
        "py_setuptools_prefix_file": attr.label(allow_single_file = True, mandatory = True),
        "py_wheel_prefix_file": attr.label(allow_single_file = True, mandatory = True),
        "py_scikit_build_core_prefix_file": attr.label(allow_single_file = True, mandatory = True),
        "py_numpy_prefix_file": attr.label(allow_single_file = True, mandatory = True),
        "py_pyyaml_prefix_file": attr.label(allow_single_file = True, mandatory = True),
        "py_typing_extensions_prefix_file": attr.label(allow_single_file = True, mandatory = True),
        "py_six_prefix_file": attr.label(allow_single_file = True, mandatory = True),
        "py_packaging_prefix_file": attr.label(allow_single_file = True, mandatory = True),
        "py_pathspec_prefix_file": attr.label(allow_single_file = True, mandatory = True),
        "py_protobuf_prefix_file": attr.label(allow_single_file = True, mandatory = True),
        "zstd_prefix_file": attr.label(allow_single_file = True, mandatory = True),
        "python_abi": attr.string(default = "derived"),
        "execute": attr.bool(default = False),
        "output_basename": attr.string(),
        "_token_flag": attr.label(
            default = Label("//native/pytorch:token"),
        ),
        "_runtime_executor": attr.label(
            default = Label("//native/pytorch:runtime_jobs_executor.py"),
            allow_single_file = True,
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
            default = Label("//native/pytorch:metadata_writer.py"),
            allow_single_file = True,
        ),
    } | native_action_prefix_manifest_attrs() | native_action_rootfs_identity_attrs(),
    doc = (
        "Build native PyTorch from a committed plan. Full build requires the " +
        "'{}' token and execute=True.".format(_REQUIRED_TOKEN)
    ),
)


def _pytorch_action_prefix_forward_impl(ctx):
    actual = ctx.attr.actual
    if type(actual) == "list":
        if len(actual) != 1:
            fail("expected one transitioned PyTorch action target, got {}".format(len(actual)))
        actual = actual[0]
    return [
        actual[DefaultInfo],
        actual[PytorchNativePrefixInfo],
    ]


_pytorch_action_prefix_forward = rule(
    implementation = _pytorch_action_prefix_forward_impl,
    attrs = rootfs_platform_forward_attrs("torch"),
)


def pytorch_action_prefix(name, visibility = None, **kwargs):
    impl_name = name + "_rootfs_impl"
    if "prefix_manifest_files" not in kwargs:
        kwargs["prefix_manifest_files"] = native_action_prefix_manifest_map(_PREFIX_KEY_ATTRS, kwargs)
    if "rootfs_identity" not in kwargs:
        kwargs["rootfs_identity"] = native_action_default_rootfs_identity("torch")
    _pytorch_action_prefix(
        name = impl_name,
        output_basename = name,
        visibility = ["//visibility:private"],
        **kwargs
    )
    _pytorch_action_prefix_forward(
        name = name,
        actual = ":" + impl_name,
        visibility = visibility,
    )
