"""Committed-plan native jaxlib prefix rule."""

load("//native/common:build_settings.bzl", "ReleaseCudaLineInfo")
load("//native/common:native_action_inputs.bzl", "native_action_default_rootfs_identity", "native_action_prefix_manifest_attrs", "native_action_prefix_manifest_inputs", "native_action_prefix_manifest_map", "native_action_rootfs_identity_attrs", "native_action_rootfs_identity_file")
load("//native/common:native_action_toolchain.bzl", "rootfs_platform_forward_attrs")

JaxlibNativePrefixInfo = provider(
    doc = "Install prefix produced by the token-gated native jaxlib action.",
    fields = {
        "prefix": "Declared directory output containing the jaxlib prefix.",
        "wheelhouse": "Declared directory containing wheels copied out of the build flow.",
        "wheel_manifest": "Declared JSON manifest for copied wheels.",
        "build_plan": "Committed plan JSON copied for inspection.",
        "provider_metadata": "Native provider metadata JSON.",
        "package": "Spack package name.",
        "version": "Pinned jaxlib version.",
        "python_abi": "Python ABI tag supplied by the caller or derived by the metadata writer.",
    },
)

JaxlibTokenInfo = provider(
    doc = "Command-line token value for the native jaxlib execute action.",
    fields = {
        "value": "Token value supplied through --//native/jaxlib:token.",
    },
)

_REQUIRED_TOKEN = "build-native-llvm"
_JAXLIB_ACTION_CPUS = 4
_JAXLIB_ACTION_MEMORY_MB = 65536
_JAXLIB_NESTED_DISTDIR = "/vaso/sources/jax/distdir"
_JAXLIB_NESTED_REPOSITORY_CACHE = "/vaso/cache/bazel/repository-cache"
_JAXLIB_NESTED_DISK_CACHE = "/vaso/cache/bazel/jaxlib-disk-cache"
_PREFIX_KEY_ATTRS = (
    ("python", "python_prefix_file"),
    ("python-venv", "python_venv_prefix_file"),
    ("py-pip", "py_pip_prefix_file"),
    ("py-setuptools", "py_setuptools_prefix_file"),
    ("py-wheel", "py_wheel_prefix_file"),
    ("py-numpy", "py_numpy_prefix_file"),
    ("bazel", "bazel_prefix_file"),
    ("llvm", "llvm_prefix_file"),
    ("cuda", "cuda_prefix_file"),
    ("cudnn", "cudnn_prefix_file"),
    ("nccl", "nccl_prefix_file"),
    ("nvshmem", "nvshmem_prefix_file"),
    ("xxd-standalone", "xxd_standalone_prefix_file"),
)


def _jaxlib_resource_set(os_name, input_count):
    return {
        "cpu": _JAXLIB_ACTION_CPUS,
        "memory": _JAXLIB_ACTION_MEMORY_MB,
    }


def _jaxlib_token_flag_impl(ctx):
    return [JaxlibTokenInfo(value = ctx.build_setting_value)]


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


def _write_dry_run_outputs(ctx, *, prefix, wheelhouse, wheel_manifest, build_plan, provider_metadata, result_marker, plan, rootfs_identity):
    script = """\
import hashlib
import json
import pathlib
import sys

plan_path = pathlib.Path(sys.argv[1])
prefix = pathlib.Path(sys.argv[2])
wheelhouse = pathlib.Path(sys.argv[3])
wheel_manifest = pathlib.Path(sys.argv[4])
build_plan = pathlib.Path(sys.argv[5])
provider_metadata = pathlib.Path(sys.argv[6])
result_marker = pathlib.Path(sys.argv[7])
rootfs_identity = sys.argv[8]
plan = json.loads(plan_path.read_text(encoding="utf-8"))
prefix.mkdir(parents=True, exist_ok=True)
wheelhouse.mkdir(parents=True, exist_ok=True)
(prefix / "DRY_RUN_DO_NOT_USE_AS_JAXLIB_PREFIX").write_text(
    "Token-safe native jaxlib dry run. No wheel was built.\\n",
    encoding="utf-8",
)
dry_plan = dict(plan)
dry_plan["mode"] = "dry-run"
dry_plan["preflight_ok"] = True
dry_plan["will_build"] = False
dry_plan["required_prefixes"] = []
dry_plan["input_prefixes"] = {}
dry_plan["authorization"] = {
    "required_token": "build-native-llvm",
    "token_present": False,
    "execute_requested": False,
}
build_plan.write_text(json.dumps(dry_plan, indent=2, sort_keys=True) + "\\n", encoding="utf-8")
wheel_manifest.write_text(json.dumps({"schema_version": 1, "wheels": []}, indent=2, sort_keys=True) + "\\n", encoding="utf-8")
metadata = {
    "schema_version": 1,
    "package": "py-jaxlib",
    "version": "0.10.2",
    "action_input_digest": hashlib.sha256(plan_path.read_bytes()).hexdigest()[:32],
    "mechanism": "committed-plan",
    "mode": "dry-run",
    "token_present": False,
    "will_build": False,
    "build_work": "",
    "input_prefixes": {},
    "rootfs_manifest": rootfs_identity,
    "wheelhouse": str(wheelhouse),
    "wheel_manifest": str(wheel_manifest),
    "wheels": [],
}
provider_metadata.write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\\n", encoding="utf-8")
result_marker.write_text("Token-safe native jaxlib dry run. No wheel was built.\\n", encoding="utf-8")
"""
    ctx.actions.run(
        inputs = depset([plan, rootfs_identity]),
        outputs = [prefix, wheelhouse, wheel_manifest, build_plan, provider_metadata, result_marker],
        executable = "python3",
        arguments = [
            "-c",
            script,
            plan.path,
            prefix.path,
            wheelhouse.path,
            wheel_manifest.path,
            build_plan.path,
            provider_metadata.path,
            result_marker.path,
            rootfs_identity.path,
        ],
        env = {
            "LC_ALL": "C",
            "PATH": "/usr/bin:/bin",
            "PYTHONHASHSEED": "0",
            "VASO_ACTION_RESERVED_CPUS": str(_JAXLIB_ACTION_CPUS),
        },
        mnemonic = "JaxlibNativeDryRun",
        progress_message = "Writing token-safe native jaxlib dry-run outputs %{label}",
        use_default_shell_env = False,
    )


def _jaxlib_action_prefix_impl(ctx):
    output_basename = ctx.attr.output_basename or ctx.label.name
    prefix = ctx.actions.declare_directory(output_basename + "_prefix")
    wheelhouse = ctx.actions.declare_directory(output_basename + "_wheels")
    wheel_manifest = ctx.actions.declare_file(output_basename + "_wheel_manifest.json")
    build_plan = ctx.actions.declare_file(output_basename + "_build_plan.json")
    provider_metadata = ctx.actions.declare_file(output_basename + "_provider_metadata.json")
    result_marker = ctx.actions.declare_file(output_basename + "_result.txt")
    token = ctx.attr._token_flag[JaxlibTokenInfo].value
    plan = _line_checked_file(ctx, ctx.file.plan, profile = "jax", consumer = "jaxlib", kind = "plan")
    rootfs_identity = native_action_rootfs_identity_file(ctx, profile = "jax", consumer = "jaxlib")

    if not ctx.attr.execute:
        _write_dry_run_outputs(
            ctx,
            prefix = prefix,
            wheelhouse = wheelhouse,
            wheel_manifest = wheel_manifest,
            build_plan = build_plan,
            provider_metadata = provider_metadata,
            result_marker = result_marker,
            plan = plan,
            rootfs_identity = rootfs_identity,
        )
        return [
            DefaultInfo(files = depset([prefix, wheelhouse, wheel_manifest, build_plan, provider_metadata, result_marker])),
            JaxlibNativePrefixInfo(
                prefix = prefix,
                wheelhouse = wheelhouse,
                wheel_manifest = wheel_manifest,
                build_plan = build_plan,
                provider_metadata = provider_metadata,
                package = "py-jaxlib",
                version = "0.10.2",
                python_abi = ctx.attr.python_abi,
            ),
        ]

    if token != _REQUIRED_TOKEN:
        fail("full native jaxlib build requires token {}; set //native/jaxlib:token to that value".format(_REQUIRED_TOKEN))

    dep_manifest_inputs = native_action_prefix_manifest_inputs(
        ctx,
        _PREFIX_KEY_ATTRS,
        require_all = True,
    )
    repo_dep_prefix_args = _repo_dep_prefix_args(ctx)
    action_inputs = [
        ctx.file._runtime_executor,
        ctx.file._executor,
        ctx.file._executor_schema,
        ctx.file._schema,
        plan,
        rootfs_identity,
        ctx.file.source_archive,
        ctx.file.nested_cache_manifest,
    ] + dep_manifest_inputs
    arguments = [
        "--executor",
        ctx.file._executor.path,
        "--runtime-jobs-file",
        "/vaso/lines/{}/state/native/jaxlib_jobs.txt".format(ctx.attr._line[ReleaseCudaLineInfo].value),
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
        wheelhouse.path,
        "--nested-cache-manifest",
        "distdir=" + ctx.file.nested_cache_manifest.path,
        "--nested-cache-manifest",
        "repository_cache=" + ctx.file.nested_cache_manifest.path,
        "--nested-distdir",
        _JAXLIB_NESTED_DISTDIR,
        "--nested-repository-cache",
        _JAXLIB_NESTED_REPOSITORY_CACHE,
        "--nested-disk-cache",
        _JAXLIB_NESTED_DISK_CACHE,
    ]

    for target, plan_path in sorted(ctx.attr.inputs.items(), key = lambda item: item[1]):
        files = target.files.to_list()
        if len(files) != 1:
            fail("extra input {} must provide exactly one file".format(target.label))
        action_inputs.append(files[0])
        arguments.extend(["--input", plan_path + "=" + files[0].path])

    arguments.extend(repo_dep_prefix_args)

    ctx.actions.run(
        inputs = depset(action_inputs),
        outputs = [prefix, wheelhouse],
        executable = "python3",
        arguments = [ctx.file._runtime_executor.path] + arguments,
        env = {
            "LC_ALL": "C",
            "PATH": "/usr/bin:/bin",
            "PYTHONHASHSEED": "0",
            "VASO_ACTION_RESERVED_CPUS": str(_JAXLIB_ACTION_CPUS),
        },
        mnemonic = "JaxlibNativePrefix",
        progress_message = "Building native jaxlib wheelhouse %{label}",
        resource_set = _jaxlib_resource_set,
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
        "--wheelhouse",
        wheelhouse.path,
        "--wheel-manifest-out",
        wheel_manifest.path,
        "--build-plan-out",
        build_plan.path,
        "--provider-metadata-out",
        provider_metadata.path,
        "--result-marker-out",
        result_marker.path,
        "--python-abi",
        ctx.attr.python_abi,
    ] + repo_dep_prefix_args
    ctx.actions.run(
        inputs = depset([ctx.file._metadata_writer, plan, ctx.file._keys, rootfs_identity, wheelhouse]),
        outputs = [wheel_manifest, build_plan, provider_metadata, result_marker],
        executable = "python3",
        arguments = metadata_args,
        env = {
            "LC_ALL": "C",
            "PATH": "/usr/bin:/bin",
            "PYTHONHASHSEED": "0",
        },
        mnemonic = "JaxlibNativeMetadata",
        progress_message = "Writing native jaxlib metadata %{label}",
        use_default_shell_env = False,
    )

    return [
        DefaultInfo(files = depset([prefix, wheelhouse, wheel_manifest, build_plan, provider_metadata, result_marker])),
        JaxlibNativePrefixInfo(
            prefix = prefix,
            wheelhouse = wheelhouse,
            wheel_manifest = wheel_manifest,
            build_plan = build_plan,
            provider_metadata = provider_metadata,
            package = "py-jaxlib",
            version = "0.10.2",
            python_abi = ctx.attr.python_abi,
        ),
    ]


jaxlib_token_flag = rule(
    implementation = _jaxlib_token_flag_impl,
    build_setting = config.string(flag = True),
    doc = "Command-line token gate for the native jaxlib execute action.",
)


_jaxlib_action_prefix = rule(
    implementation = _jaxlib_action_prefix_impl,
    attrs = {
        "plan": attr.label(
            allow_single_file = True,
            mandatory = True,
            doc = "Committed jaxlib build plan for the selected line.",
        ),
        "rootfs_identity": attr.label(
            allow_single_file = True,
            mandatory = True,
            doc = "Committed rootfs line identity slice for the selected line.",
        ),
        "source_archive": attr.label(
            allow_single_file = True,
            mandatory = True,
            doc = "The sha256-pinned JAX source archive used by execute mode.",
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
        "py_numpy_prefix_file": attr.label(allow_single_file = True, mandatory = True),
        "bazel_prefix_file": attr.label(allow_single_file = True, mandatory = True),
        "llvm_prefix_file": attr.label(allow_single_file = True, mandatory = True),
        "cuda_prefix_file": attr.label(allow_single_file = True, mandatory = True),
        "cudnn_prefix_file": attr.label(allow_single_file = True, mandatory = True),
        "nccl_prefix_file": attr.label(allow_single_file = True, mandatory = True),
        "nvshmem_prefix_file": attr.label(allow_single_file = True, mandatory = True),
        "xxd_standalone_prefix_file": attr.label(allow_single_file = True, mandatory = True),
        "nested_cache_manifest": attr.label(
            allow_single_file = True,
            mandatory = True,
            doc = "Content manifest for jaxlib's nested Bazel distdir and repository cache.",
        ),
        "python_abi": attr.string(default = "derived"),
        "execute": attr.bool(default = False),
        "synthetic_prefixes_for_dry_run": attr.bool(default = False),
        "output_basename": attr.string(),
        "_token_flag": attr.label(
            default = Label("//native/jaxlib:token"),
        ),
        "_executor": attr.label(
            default = Label("//native/common:plan_executor.py"),
            allow_single_file = True,
        ),
        "_runtime_executor": attr.label(
            default = Label("//native/jaxlib:runtime_jobs_executor.py"),
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
            default = Label("//native/jaxlib:metadata_writer.py"),
            allow_single_file = True,
        ),
    } | native_action_prefix_manifest_attrs() | native_action_rootfs_identity_attrs(),
    doc = (
        "Build native jaxlib from a committed plan. Full build requires the " +
        "'{}' token and execute=True.".format(_REQUIRED_TOKEN)
    ),
)


def _jaxlib_action_prefix_forward_impl(ctx):
    actual = ctx.attr.actual
    if type(actual) == "list":
        if len(actual) != 1:
            fail("expected one transitioned jaxlib action target, got {}".format(len(actual)))
        actual = actual[0]
    return [
        actual[DefaultInfo],
        actual[JaxlibNativePrefixInfo],
    ]


_jaxlib_action_prefix_forward = rule(
    implementation = _jaxlib_action_prefix_forward_impl,
    attrs = rootfs_platform_forward_attrs("jax"),
)


def jaxlib_action_prefix(name, visibility = None, **kwargs):
    impl_name = name + "_rootfs_impl"
    if "nested_cache_manifest" not in kwargs:
        kwargs["nested_cache_manifest"] = "@jaxlib_nested_cache_manifest//:manifest.json"
    if "prefix_manifest_files" not in kwargs:
        kwargs["prefix_manifest_files"] = native_action_prefix_manifest_map(_PREFIX_KEY_ATTRS, kwargs)
    if "rootfs_identity" not in kwargs:
        kwargs["rootfs_identity"] = native_action_default_rootfs_identity("jax")
    _jaxlib_action_prefix(
        name = impl_name,
        output_basename = name,
        visibility = ["//visibility:private"],
        **kwargs
    )
    _jaxlib_action_prefix_forward(
        name = name,
        actual = ":" + impl_name,
        visibility = visibility,
    )
