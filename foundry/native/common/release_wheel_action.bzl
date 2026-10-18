"""Bazel action wrapper for repaired release wheels."""

load("//native/jaxlib:jaxlib_action.bzl", "JaxlibNativePrefixInfo")
load("//native/common:build_settings.bzl", "ReleaseCudaLineInfo", "ReleaseGitShaInfo", "release_cuda_line", "release_git_sha")
load("//native/common:extension_info.bzl", "VasoTorchExtensionInfo")
load("//native/common:native_action_toolchain.bzl", "native_action_determinism_attrs", "native_action_deterministic_env")
load("//native/pytorch:pytorch_action.bzl", "PytorchNativePrefixInfo")
load("//native/torchaudio:torchaudio_action.bzl", "TorchaudioNativePrefixInfo")
load("//native/torchvision:torchvision_action.bzl", "TorchvisionNativePrefixInfo")
load("//native/triton:triton_action.bzl", "TritonNativePrefixInfo")

ReleaseWheelInfo = provider(
    doc = "Repaired release wheel outputs.",
    fields = {
        "manifest": "JSON manifest listing repaired wheel files.",
        "wheelhouse": "Declared directory containing repaired wheel files.",
    },
)

_SINGLE_WHEEL_PROVIDERS = (
    PytorchNativePrefixInfo,
    TorchvisionNativePrefixInfo,
    TorchaudioNativePrefixInfo,
    TritonNativePrefixInfo,
    VasoTorchExtensionInfo,
)

_RepairRuntimePrefixesInfo = provider(fields = ["prefixes"])


def _repair_runtime_prefixes_impl(target, ctx):
    direct = []
    transitive = []
    if PytorchNativePrefixInfo in target:
        direct.append(target[PytorchNativePrefixInfo].prefix)
    for name in ("actual", "torch_prefix"):
        dependencies = getattr(ctx.rule.attr, name, None)
        if dependencies == None:
            continue
        if type(dependencies) != "list":
            dependencies = [dependencies]
        for dependency in dependencies:
            if _RepairRuntimePrefixesInfo in dependency:
                transitive.append(dependency[_RepairRuntimePrefixesInfo].prefixes)
    return [_RepairRuntimePrefixesInfo(prefixes = depset(direct, transitive = transitive))]


_repair_runtime_prefixes = aspect(
    implementation = _repair_runtime_prefixes_impl,
    attr_aspects = ["actual", "torch_prefix"],
)


def _single_wheel_info(target):
    for provider in _SINGLE_WHEEL_PROVIDERS:
        if provider in target:
            info = target[provider]
            return struct(
                wheel = info.wheel,
                provider_metadata = info.provider_metadata,
            )
    return None


def _release_wheel_impl(ctx):
    out_dir = ctx.actions.declare_directory(ctx.label.name + "_wheelhouse")
    manifest = ctx.actions.declare_file(ctx.label.name + "_manifest.json")
    line = ctx.attr._line[ReleaseCudaLineInfo].value
    git_sha = ctx.attr._git_sha[ReleaseGitShaInfo].value
    profile = ctx.attr.profile
    if profile not in ("torch", "jax"):
        fail("profile must be torch or jax, got {}".format(profile))

    single = _single_wheel_info(ctx.attr.input)
    inputs = [ctx.file._rootfs_lock, ctx.file.spack_lock]
    args = [
        "--out-dir",
        out_dir.path,
        "--manifest-out",
        manifest.path,
        "--lock",
        ctx.file._rootfs_lock.path,
        "--spack-lock",
        ctx.file.spack_lock.path,
        "--profile",
        profile,
        "--line",
        line,
        "--git-sha",
        git_sha,
    ]

    if single:
        if ctx.attr.wheelhouse:
            fail("{} is a single-wheel action; remove wheelhouse=True".format(ctx.attr.input.label))
        inputs.extend([single.wheel, single.provider_metadata])
        args.extend([
            "--wheel",
            single.wheel.path,
            "--provider-metadata",
            single.provider_metadata.path,
        ])
    elif JaxlibNativePrefixInfo in ctx.attr.input:
        if not ctx.attr.wheelhouse:
            fail("{} is a wheelhouse action; set wheelhouse=True".format(ctx.attr.input.label))
        info = ctx.attr.input[JaxlibNativePrefixInfo]
        inputs.extend([info.wheelhouse, info.provider_metadata])
        args.extend([
            "--wheelhouse",
            info.wheelhouse.path,
            "--provider-metadata",
            info.provider_metadata.path,
        ])
    else:
        fail("input must provide a native PyTorch, torchvision, torchaudio, Triton, Vaso torch extension or jaxlib wheel provider")

    # Metadata paths do not materialize cached directory outputs. Declare the
    # native runtime artifacts whose libraries the repair driver may vendor.
    for prefix in sorted(ctx.attr.input[_RepairRuntimePrefixesInfo].prefixes.to_list(), key = lambda file: file.path):
        inputs.append(prefix)
        args.extend(["--provider-prefix", prefix.path])

    determinism = native_action_deterministic_env(ctx, compiler_flags = False)
    ctx.actions.run_shell(
        inputs = depset(inputs),
        tools = [ctx.executable._repair_driver],
        outputs = [out_dir, manifest],
        command = """\
set -euo pipefail
driver="$1"
shift
export PATCHELF="${PATCHELF:-/usr/bin/patchelf}"
export READELF="${READELF:-/usr/bin/readelf}"
exec "$driver" "$@"
""",
        arguments = [ctx.executable._repair_driver.path] + args,
        env = determinism.env,
        mnemonic = "VasoReleaseWheelRepair",
        progress_message = "Repairing release wheels %{label}",
        use_default_shell_env = True,
    )

    return [
        DefaultInfo(files = depset([out_dir, manifest])),
        ReleaseWheelInfo(
            manifest = manifest,
            wheelhouse = out_dir,
        ),
    ]


release_wheel_action = rule(
    implementation = _release_wheel_impl,
    attrs = {
        "input": attr.label(
            mandatory = True,
            aspects = [_repair_runtime_prefixes],
            doc = "Native wheel action provider to repair.",
        ),
        "profile": attr.string(
            mandatory = True,
            doc = "Release profile recorded in vaso_provenance.json: torch or jax.",
        ),
        "wheelhouse": attr.bool(
            default = False,
            doc = "Set for providers that emit a wheelhouse rather than a single wheel.",
        ),
        "spack_lock": attr.label(
            default = "//locks:torch.lock.json",
            allow_single_file = True,
            doc = "Checked-in profile lock used for release provenance.",
        ),
        "_git_sha": attr.label(default = Label("//native/common:release_git_sha")),
        "_line": attr.label(default = Label("//native/common:release_line")),
        "_repair_driver": attr.label(default = "//native/common:repair_wheel", executable = True, cfg = "exec"),
        "_rootfs_lock": attr.label(default = "//:rootfs/cuda_ecosystem.lock.json", allow_single_file = True),
    } | native_action_determinism_attrs(),
    doc = "Rewrite a native action's wheels into Vaso release wheels.",
)
