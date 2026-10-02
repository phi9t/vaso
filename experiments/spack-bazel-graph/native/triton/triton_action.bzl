"""Configured-action native Triton prefix rule."""

TritonNativePrefixInfo = provider(
    doc = "Install prefix produced by the token-gated native Triton action.",
    fields = {
        "prefix": "Declared directory output containing the Triton prefix.",
        "wheel": "Declared wheel artifact copied out of the build flow.",
        "build_plan": "Planner JSON emitted by native/triton/plan.py.",
        "provider_metadata": "Native provider metadata JSON.",
        "package": "Spack package name.",
        "version": "Pinned Triton release.",
        "python_abi": "Python ABI tag supplied by the caller or derived by the driver.",
    },
)

TritonTokenInfo = provider(
    doc = "Command-line token value for the native Triton execute action.",
    fields = {
        "value": "Token value supplied through --//native/triton:token.",
    },
)

_REQUIRED_TOKEN = "build-native-triton"
_PREFIX_KEY_ATTRS = (
    ("python", "python_prefix_file"),
    ("python-venv", "python_venv_prefix_file"),
    ("py-pip", "py_pip_prefix_file"),
    ("py-setuptools", "py_setuptools_prefix_file"),
    ("py-wheel", "py_wheel_prefix_file"),
    ("py-filelock", "py_filelock_prefix_file"),
    ("py-lit", "py_lit_prefix_file"),
    ("py-pybind11", "py_pybind11_prefix_file"),
    ("cmake", "cmake_prefix_file"),
    ("ninja", "ninja_prefix_file"),
    ("llvm", "llvm_prefix_file"),
    ("nlohmann_json", "nlohmann_json_prefix_file"),
    ("cuda", "cuda_prefix_file"),
    ("zlib_ng", "zlib_ng_prefix_file"),
)

def _triton_token_flag_impl(ctx):
    return [TritonTokenInfo(value = ctx.build_setting_value)]

def _triton_action_prefix_impl(ctx):
    prefix = ctx.actions.declare_directory(ctx.label.name + "_prefix")
    wheel = ctx.actions.declare_file(ctx.label.name + "_wheel.whl")
    build_plan = ctx.actions.declare_file(ctx.label.name + "_build_plan.json")
    provider_metadata = ctx.actions.declare_file(ctx.label.name + "_provider_metadata.json")
    result_marker = ctx.actions.declare_file(ctx.label.name + "_result.txt")
    token = ctx.attr._token_flag[TritonTokenInfo].value

    inputs = [
        ctx.file._plan,
        ctx.file._pins,
        ctx.file.source_anchor,
    ] + ctx.files.source_files
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
        "--wheel-out",
        wheel.path,
        "--source-anchor",
        ctx.file.source_anchor.path,
        "--python-abi",
        ctx.attr.python_abi,
        "--token",
        token,
    ]
    if ctx.attr.execute:
        args.append("--execute")
    if ctx.attr.synthetic_prefixes_for_dry_run:
        args.append("--synthetic-prefixes-for-dry-run")
    if ctx.file.drift_patch:
        if not ctx.attr.drift_patch_sha256:
            fail("drift_patch_sha256 is required when drift_patch is set")
        inputs.append(ctx.file.drift_patch)
        args.extend([
            "--patch-file",
            ctx.file.drift_patch.path,
            "--patch-sha256",
            ctx.attr.drift_patch_sha256,
        ])
    elif ctx.attr.drift_patch_sha256:
        fail("drift_patch must be set when drift_patch_sha256 is set")

    for key, attr_name in _PREFIX_KEY_ATTRS:
        prefix_file = getattr(ctx.file, attr_name)
        inputs.append(prefix_file)
        args.extend(["--prefix-file", "{}={}".format(key, prefix_file.path)])

    ctx.actions.run_shell(
        inputs = depset(inputs),
        tools = [ctx.executable._driver],
        outputs = [prefix, wheel, build_plan, provider_metadata, result_marker],
        command = """\
set -euo pipefail
driver="$1"
shift
exec "$driver" "$@"
""",
        arguments = [ctx.executable._driver.path] + args,
        mnemonic = "TritonNativePrefix",
        progress_message = "Planning native Triton prefix %{label}",
        use_default_shell_env = True,
    )

    return [
        DefaultInfo(files = depset([prefix, wheel, build_plan, provider_metadata, result_marker])),
        TritonNativePrefixInfo(
            prefix = prefix,
            wheel = wheel,
            build_plan = build_plan,
            provider_metadata = provider_metadata,
            package = "py-triton",
            version = "3.8.0",
            python_abi = ctx.attr.python_abi,
        ),
    ]

triton_token_flag = rule(
    implementation = _triton_token_flag_impl,
    build_setting = config.string(flag = True),
    doc = "Command-line token gate for the native Triton execute action.",
)

triton_action_prefix = rule(
    implementation = _triton_action_prefix_impl,
    attrs = {
        "source_anchor": attr.label(
            allow_single_file = True,
            mandatory = True,
            doc = "File inside the pinned Triton source tree.",
        ),
        "source_files": attr.label(
            allow_files = True,
            mandatory = True,
            doc = "The sha256-pinned Triton source tree inputs.",
        ),
        "drift_patch": attr.label(
            allow_single_file = True,
            doc = "Checked-in LLVM drift patch to apply to the copied Triton source tree.",
        ),
        "drift_patch_sha256": attr.string(
            doc = "Expected sha256 for drift_patch.",
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
        "py_filelock_prefix_file": attr.label(
            allow_single_file = True,
            mandatory = True,
            doc = "Native py-filelock prefix_path.txt.",
        ),
        "py_lit_prefix_file": attr.label(
            allow_single_file = True,
            mandatory = True,
            doc = "Native py-lit prefix_path.txt.",
        ),
        "py_pybind11_prefix_file": attr.label(
            allow_single_file = True,
            mandatory = True,
            doc = "Native py-pybind11 prefix_path.txt.",
        ),
        "cmake_prefix_file": attr.label(
            allow_single_file = True,
            mandatory = True,
            doc = "Bazel-native CMake prefix_path.txt.",
        ),
        "ninja_prefix_file": attr.label(
            allow_single_file = True,
            mandatory = True,
            doc = "Bazel-native Ninja prefix_path.txt.",
        ),
        "llvm_prefix_file": attr.label(
            allow_single_file = True,
            mandatory = True,
            doc = "Rootfs LLVM prefix_path.txt for the one-LLVM Triton build.",
        ),
        "nlohmann_json_prefix_file": attr.label(
            allow_single_file = True,
            mandatory = True,
            doc = "Native nlohmann-json prefix_path.txt.",
        ),
        "cuda_prefix_file": attr.label(
            allow_single_file = True,
            mandatory = True,
            doc = "Bazel-native CUDA rootfs boundary prefix_path.txt.",
        ),
        "zlib_ng_prefix_file": attr.label(
            allow_single_file = True,
            mandatory = True,
            doc = "Native zlib-ng prefix_path.txt.",
        ),
        "python_abi": attr.string(default = "derived"),
        "execute": attr.bool(default = False),
        "synthetic_prefixes_for_dry_run": attr.bool(default = False),
        "_token_flag": attr.label(
            default = Label("//native/triton:token"),
        ),
        # action_driver.py performs the insula/rootfs checks and invokes plan.py.
        "_driver": attr.label(
            default = "//native/triton:action_driver",
            executable = True,
            cfg = "exec",
        ),
        "_plan": attr.label(
            default = "//native/triton:plan.py",
            allow_single_file = True,
        ),
        "_pins": attr.label(
            default = "//native/triton:upstream_pins.json",
            allow_single_file = True,
        ),
    },
    doc = (
        "Build or dry-run a native Triton prefix with declared source, prefix, " +
        "plan, and metadata inputs/outputs. Full build requires the " +
        "'{}' token and execute=True.".format(_REQUIRED_TOKEN)
    ),
)
