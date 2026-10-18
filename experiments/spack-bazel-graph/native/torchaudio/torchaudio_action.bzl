"""Configured-action native torchaudio prefix rule."""

load("//native/pytorch:pytorch_action.bzl", "PytorchNativePrefixInfo")

TorchaudioNativePrefixInfo = provider(
    doc = "Install prefix produced by the token-gated native torchaudio action.",
    fields = {
        "prefix": "Declared directory output containing the torchaudio prefix.",
        "wheel": "Declared wheel artifact copied out of the reusable build work tree.",
        "build_plan": "Planner JSON emitted by native/torchaudio/plan.py.",
        "provider_metadata": "Native provider metadata JSON.",
        "package": "Spack package name.",
        "version": "Pinned torchaudio release.",
        "python_abi": "Python ABI tag supplied by the caller or derived by the driver.",
    },
)

TorchaudioTokenInfo = provider(
    doc = "Command-line token value for the native torchaudio execute action.",
    fields = {"value": "Token value supplied through --//native/torchaudio:token."},
)

TorchaudioMaxJobsInfo = provider(
    doc = "Per-run torchaudio build parallelism.",
    fields = {"value": "Integer job count supplied through --//native/torchaudio:max_jobs."},
)

_REQUIRED_TOKEN = "build-native-torchaudio"
_MAX_JOBS_CAP = 96
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

def _token_flag_impl(ctx):
    return [TorchaudioTokenInfo(value = ctx.build_setting_value)]

def _max_jobs_flag_impl(ctx):
    jobs = ctx.build_setting_value
    if jobs < 1 or jobs > _MAX_JOBS_CAP:
        fail("torchaudio max_jobs must be between 1 and {}, got {}".format(_MAX_JOBS_CAP, jobs))
    return [TorchaudioMaxJobsInfo(value = jobs)]

def _effective_max_jobs(ctx):
    jobs = int(ctx.attr.max_jobs) if ctx.attr.max_jobs else ctx.attr._max_jobs_flag[TorchaudioMaxJobsInfo].value
    if jobs < 1 or jobs > _MAX_JOBS_CAP:
        fail("torchaudio max_jobs must be between 1 and {}, got {}".format(_MAX_JOBS_CAP, jobs))
    return jobs

def _torchaudio_action_prefix_impl(ctx):
    prefix = ctx.actions.declare_directory(ctx.label.name + "_prefix")
    wheel = ctx.actions.declare_file(ctx.label.name + "_wheel.whl")
    build_plan = ctx.actions.declare_file(ctx.label.name + "_build_plan.json")
    provider_metadata = ctx.actions.declare_file(ctx.label.name + "_provider_metadata.json")
    result_marker = ctx.actions.declare_file(ctx.label.name + "_result.txt")
    torch_provider = ctx.attr.torch_prefix[PytorchNativePrefixInfo]
    token = ctx.attr._token_flag[TorchaudioTokenInfo].value
    max_jobs = _effective_max_jobs(ctx)

    inputs = [
        ctx.file._plan,
        ctx.file._pins,
        ctx.file.source_anchor,
        torch_provider.prefix,
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
        "--torch-prefix",
        torch_provider.prefix.path,
        "--python-abi",
        ctx.attr.python_abi,
        "--torch-cuda-arch-list",
        ctx.attr.torch_cuda_arch_list,
        "--max-jobs",
        str(max_jobs),
        "--token",
        token,
    ]
    for source in ctx.files.source_files:
        args.extend(["--source-files", source.path])
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
        outputs = [prefix, wheel, build_plan, provider_metadata, result_marker],
        command = """\
set -euo pipefail
driver="$1"
shift
exec "$driver" "$@"
""",
        arguments = [ctx.executable._driver.path] + args,
        mnemonic = "TorchaudioNativePrefix",
        progress_message = "Planning native torchaudio prefix %{label}",
        use_default_shell_env = True,
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
    implementation = _token_flag_impl,
    build_setting = config.string(flag = True),
    doc = "Command-line token gate for the native torchaudio execute action.",
)

torchaudio_max_jobs_flag = rule(
    implementation = _max_jobs_flag_impl,
    build_setting = config.int(flag = True),
    doc = "Per-run torchaudio build parallelism.",
)

torchaudio_action_prefix = rule(
    implementation = _torchaudio_action_prefix_impl,
    attrs = {
        "torch_prefix": attr.label(
            providers = [PytorchNativePrefixInfo],
            mandatory = True,
            doc = "Native PyTorch action prefix provider for the selected CUDA line.",
        ),
        "source_anchor": attr.label(allow_single_file = True, mandatory = True),
        "source_files": attr.label(allow_files = True, mandatory = True),
        "python_prefix_file": attr.label(allow_single_file = True, mandatory = True),
        "python_venv_prefix_file": attr.label(allow_single_file = True, mandatory = True),
        "py_pip_prefix_file": attr.label(allow_single_file = True, mandatory = True),
        "py_setuptools_prefix_file": attr.label(allow_single_file = True, mandatory = True),
        "py_wheel_prefix_file": attr.label(allow_single_file = True, mandatory = True),
        "py_filelock_prefix_file": attr.label(allow_single_file = True, mandatory = True),
        "cuda_prefix_file": attr.label(allow_single_file = True, mandatory = True),
        "ninja_prefix_file": attr.label(allow_single_file = True, mandatory = True),
        "python_abi": attr.string(default = "derived"),
        "torch_cuda_arch_list": attr.string(default = "10.0"),
        "max_jobs": attr.string(default = ""),
        "execute": attr.bool(default = False),
        "synthetic_prefixes_for_dry_run": attr.bool(default = False),
        "_token_flag": attr.label(default = Label("//native/torchaudio:token")),
        "_max_jobs_flag": attr.label(default = Label("//native/torchaudio:max_jobs")),
        "_driver": attr.label(default = "//native/torchaudio:action_driver", executable = True, cfg = "exec"),
        "_plan": attr.label(default = "//native/torchaudio:plan.py", allow_single_file = True),
        "_pins": attr.label(default = "//native/torchaudio:upstream_pins.json", allow_single_file = True),
    },
    doc = (
        "Build or dry-run a native torchaudio prefix against native PyTorch. " +
        "Full build requires the '{}' token and execute=True.".format(_REQUIRED_TOKEN)
    ),
)
