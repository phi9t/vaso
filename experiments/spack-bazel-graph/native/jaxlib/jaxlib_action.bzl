"""Configured-action native jaxlib prefix rule."""

JaxlibNativePrefixInfo = provider(
    doc = "Install prefix produced by the token-gated native jaxlib action.",
    fields = {
        "prefix": "Declared directory output containing the jaxlib prefix.",
        "wheelhouse": "Declared directory containing wheels copied out of the build flow.",
        "wheel_manifest": "Declared JSON manifest for copied wheels.",
        "build_plan": "Planner JSON emitted by native/jaxlib/plan.py.",
        "provider_metadata": "Native provider metadata JSON.",
        "package": "Spack package name.",
        "version": "Pinned jaxlib version.",
        "python_abi": "Python ABI tag supplied by the caller or derived by the driver.",
    },
)

JaxlibTokenInfo = provider(
    doc = "Command-line token value for the native jaxlib execute action.",
    fields = {
        "value": "Token value supplied through --//native/jaxlib:token.",
    },
)

JaxlibNestedPrefetchInfo = provider(
    doc = "Metadata emitted by the explicit online nested JAX Bazel prefetch.",
    fields = {
        "build_plan": "Planner JSON emitted by native/jaxlib/plan.py.",
        "provider_metadata": "Native provider metadata JSON.",
        "prefetch_log": "Log from the nested Bazel prefetch.",
        "result_marker": "Text marker confirming the prefetch completed.",
    },
)

_REQUIRED_TOKEN = "build-native-llvm"
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

def _jaxlib_token_flag_impl(ctx):
    return [JaxlibTokenInfo(value = ctx.build_setting_value)]

def _jaxlib_action_prefix_impl(ctx):
    prefix = ctx.actions.declare_directory(ctx.label.name + "_prefix")
    wheelhouse = ctx.actions.declare_directory(ctx.label.name + "_wheels")
    wheel_manifest = ctx.actions.declare_file(ctx.label.name + "_wheel_manifest.json")
    build_plan = ctx.actions.declare_file(ctx.label.name + "_build_plan.json")
    provider_metadata = ctx.actions.declare_file(ctx.label.name + "_provider_metadata.json")
    result_marker = ctx.actions.declare_file(ctx.label.name + "_result.txt")
    token = ctx.attr._token_flag[JaxlibTokenInfo].value

    inputs = [
        ctx.file._plan,
        ctx.file._pins,
        ctx.file.source_anchor,
        ctx.file.source_archive,
    ]
    args = [
        "--plan",
        ctx.file._plan.path,
        "--pins",
        ctx.file._pins.path,
        "--prefix-out",
        prefix.path,
        "--build-plan-out",
        build_plan.path,
        "--provider-metadata-out",
        provider_metadata.path,
        "--result-marker-out",
        result_marker.path,
        "--wheelhouse-out",
        wheelhouse.path,
        "--wheel-manifest-out",
        wheel_manifest.path,
        "--source-anchor",
        ctx.file.source_anchor.path,
        "--source-archive",
        ctx.file.source_archive.path,
        "--python-abi",
        ctx.attr.python_abi,
        "--token",
        token,
    ]
    if ctx.attr.execute:
        args.append("--execute")
    if ctx.attr.synthetic_prefixes_for_dry_run:
        args.append("--synthetic-prefixes-for-dry-run")

    for key, attr_name in _PREFIX_KEY_ATTRS:
        prefix_file = getattr(ctx.file, attr_name)
        inputs.append(prefix_file)
        args.extend(["--prefix-file", "{}={}".format(key, prefix_file.path)])

    ctx.actions.run_shell(
        inputs = depset(inputs),
        tools = [ctx.executable._driver],
        outputs = [prefix, wheelhouse, wheel_manifest, build_plan, provider_metadata, result_marker],
        command = """\
set -euo pipefail
driver="$1"
shift
exec "$driver" "$@"
""",
        arguments = [ctx.executable._driver.path] + args,
        mnemonic = "JaxlibNativePrefix",
        progress_message = "Planning native jaxlib prefix %{label}",
        use_default_shell_env = True,
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

def _jaxlib_nested_prefetch_impl(ctx):
    build_plan = ctx.actions.declare_file(ctx.label.name + "_build_plan.json")
    provider_metadata = ctx.actions.declare_file(ctx.label.name + "_provider_metadata.json")
    result_marker = ctx.actions.declare_file(ctx.label.name + "_result.txt")
    prefetch_log = ctx.actions.declare_file(ctx.label.name + "_log.txt")
    token = ctx.attr._token_flag[JaxlibTokenInfo].value

    inputs = [
        ctx.file._plan,
        ctx.file._pins,
        ctx.file.source_anchor,
        ctx.file.source_archive,
    ]
    args = [
        "--plan",
        ctx.file._plan.path,
        "--pins",
        ctx.file._pins.path,
        "--build-plan-out",
        build_plan.path,
        "--provider-metadata-out",
        provider_metadata.path,
        "--result-marker-out",
        result_marker.path,
        "--prefetch-log-out",
        prefetch_log.path,
        "--source-anchor",
        ctx.file.source_anchor.path,
        "--source-archive",
        ctx.file.source_archive.path,
        "--python-abi",
        ctx.attr.python_abi,
        "--token",
        token,
        "--execute",
        "--prefetch-nested-bazel",
    ]

    for key, attr_name in _PREFIX_KEY_ATTRS:
        prefix_file = getattr(ctx.file, attr_name)
        inputs.append(prefix_file)
        args.extend(["--prefix-file", "{}={}".format(key, prefix_file.path)])

    ctx.actions.run_shell(
        inputs = depset(inputs),
        tools = [ctx.executable._driver],
        outputs = [build_plan, provider_metadata, result_marker, prefetch_log],
        command = """\
set -euo pipefail
driver="$1"
shift
exec "$driver" "$@"
""",
        arguments = [ctx.executable._driver.path] + args,
        mnemonic = "JaxlibNestedBazelPrefetch",
        progress_message = "Prefetching nested JAX Bazel repositories %{label}",
        use_default_shell_env = True,
    )

    return [
        DefaultInfo(files = depset([build_plan, provider_metadata, result_marker, prefetch_log])),
        JaxlibNestedPrefetchInfo(
            build_plan = build_plan,
            provider_metadata = provider_metadata,
            prefetch_log = prefetch_log,
            result_marker = result_marker,
        ),
    ]

jaxlib_token_flag = rule(
    implementation = _jaxlib_token_flag_impl,
    build_setting = config.string(flag = True),
    doc = "Command-line token gate for the native jaxlib execute action.",
)

jaxlib_action_prefix = rule(
    implementation = _jaxlib_action_prefix_impl,
    attrs = {
        "source_anchor": attr.label(
            allow_single_file = True,
            mandatory = True,
            doc = "File inside the pinned JAX source tree.",
        ),
        "source_archive": attr.label(
            allow_single_file = True,
            mandatory = True,
            doc = "The sha256-pinned JAX source archive used by execute mode.",
        ),
        "python_prefix_file": attr.label(
            allow_single_file = True,
            mandatory = True,
            doc = "Bazel-native Python prefix_path.txt.",
        ),
        "python_venv_prefix_file": attr.label(
            allow_single_file = True,
            mandatory = True,
            doc = "Bazel-native Python venv prefix_path.txt.",
        ),
        "py_pip_prefix_file": attr.label(
            allow_single_file = True,
            mandatory = True,
            doc = "Native py-pip prefix_path.txt.",
        ),
        "py_setuptools_prefix_file": attr.label(
            allow_single_file = True,
            mandatory = True,
            doc = "Native py-setuptools prefix_path.txt.",
        ),
        "py_wheel_prefix_file": attr.label(
            allow_single_file = True,
            mandatory = True,
            doc = "Native py-wheel prefix_path.txt.",
        ),
        "py_numpy_prefix_file": attr.label(
            allow_single_file = True,
            mandatory = True,
            doc = "Native py-numpy prefix_path.txt.",
        ),
        "bazel_prefix_file": attr.label(
            allow_single_file = True,
            mandatory = True,
            doc = "Native Bazel 7.7.0 prefix_path.txt.",
        ),
        "llvm_prefix_file": attr.label(
            allow_single_file = True,
            mandatory = True,
            doc = "Rootfs LLVM prefix_path.txt for the one-LLVM JAX/XLA build.",
        ),
        "cuda_prefix_file": attr.label(
            allow_single_file = True,
            mandatory = True,
            doc = "Bazel-native CUDA rootfs boundary prefix_path.txt.",
        ),
        "cudnn_prefix_file": attr.label(
            allow_single_file = True,
            mandatory = True,
            doc = "Bazel-native cuDNN rootfs boundary prefix_path.txt.",
        ),
        "nccl_prefix_file": attr.label(
            allow_single_file = True,
            mandatory = True,
            doc = "Bazel-native NCCL rootfs boundary prefix_path.txt.",
        ),
        "nvshmem_prefix_file": attr.label(
            allow_single_file = True,
            mandatory = True,
            doc = "Bazel-native NVSHMEM rootfs boundary prefix_path.txt.",
        ),
        "xxd_standalone_prefix_file": attr.label(
            allow_single_file = True,
            mandatory = True,
            doc = "Native xxd-standalone prefix_path.txt.",
        ),
        "python_abi": attr.string(default = "derived"),
        "execute": attr.bool(default = False),
        "synthetic_prefixes_for_dry_run": attr.bool(default = False),
        "_token_flag": attr.label(
            default = Label("//native/jaxlib:token"),
        ),
        # action_driver.py performs the insula/rootfs checks and invokes plan.py.
        "_driver": attr.label(
            default = "//native/jaxlib:action_driver",
            executable = True,
            cfg = "exec",
        ),
        "_plan": attr.label(
            default = "//native/jaxlib:plan.py",
            allow_single_file = True,
        ),
        "_pins": attr.label(
            default = "//native/jaxlib:upstream_pins.json",
            allow_single_file = True,
        ),
    },
    doc = (
        "Build or dry-run a native jaxlib prefix with declared source, prefix, " +
        "plan, wheel manifest, and metadata inputs/outputs. Full build requires " +
        "the '{}' token and execute=True.".format(_REQUIRED_TOKEN)
    ),
)

jaxlib_nested_prefetch = rule(
    implementation = _jaxlib_nested_prefetch_impl,
    attrs = {
        "source_anchor": attr.label(
            allow_single_file = True,
            mandatory = True,
            doc = "File inside the pinned JAX source tree.",
        ),
        "source_archive": attr.label(
            allow_single_file = True,
            mandatory = True,
            doc = "The sha256-pinned JAX source archive used by execute mode.",
        ),
        "python_prefix_file": attr.label(
            allow_single_file = True,
            mandatory = True,
            doc = "Bazel-native Python prefix_path.txt.",
        ),
        "python_venv_prefix_file": attr.label(
            allow_single_file = True,
            mandatory = True,
            doc = "Bazel-native Python venv prefix_path.txt.",
        ),
        "py_pip_prefix_file": attr.label(
            allow_single_file = True,
            mandatory = True,
            doc = "Native py-pip prefix_path.txt.",
        ),
        "py_setuptools_prefix_file": attr.label(
            allow_single_file = True,
            mandatory = True,
            doc = "Native py-setuptools prefix_path.txt.",
        ),
        "py_wheel_prefix_file": attr.label(
            allow_single_file = True,
            mandatory = True,
            doc = "Native py-wheel prefix_path.txt.",
        ),
        "py_numpy_prefix_file": attr.label(
            allow_single_file = True,
            mandatory = True,
            doc = "Native py-numpy prefix_path.txt.",
        ),
        "bazel_prefix_file": attr.label(
            allow_single_file = True,
            mandatory = True,
            doc = "Native Bazel 7.7.0 prefix_path.txt.",
        ),
        "llvm_prefix_file": attr.label(
            allow_single_file = True,
            mandatory = True,
            doc = "Rootfs LLVM prefix_path.txt for the one-LLVM JAX/XLA build.",
        ),
        "cuda_prefix_file": attr.label(
            allow_single_file = True,
            mandatory = True,
            doc = "Bazel-native CUDA rootfs boundary prefix_path.txt.",
        ),
        "cudnn_prefix_file": attr.label(
            allow_single_file = True,
            mandatory = True,
            doc = "Bazel-native cuDNN rootfs boundary prefix_path.txt.",
        ),
        "nccl_prefix_file": attr.label(
            allow_single_file = True,
            mandatory = True,
            doc = "Bazel-native NCCL rootfs boundary prefix_path.txt.",
        ),
        "nvshmem_prefix_file": attr.label(
            allow_single_file = True,
            mandatory = True,
            doc = "Bazel-native NVSHMEM prefix_path.txt.",
        ),
        "xxd_standalone_prefix_file": attr.label(
            allow_single_file = True,
            mandatory = True,
            doc = "Native xxd-standalone prefix_path.txt.",
        ),
        "python_abi": attr.string(default = "derived"),
        "_token_flag": attr.label(
            default = Label("//native/jaxlib:token"),
        ),
        "_driver": attr.label(
            default = "//native/jaxlib:action_driver",
            executable = True,
            cfg = "exec",
        ),
        "_plan": attr.label(
            default = "//native/jaxlib:plan.py",
            allow_single_file = True,
        ),
        "_pins": attr.label(
            default = "//native/jaxlib:upstream_pins.json",
            allow_single_file = True,
        ),
    },
    doc = (
        "Run the explicit online nested JAX Bazel repository prefetch for " +
        "jaxlib's wheel targets. It writes metadata/log outputs but does not " +
        "declare or install the jaxlib prefix."
    ),
)
