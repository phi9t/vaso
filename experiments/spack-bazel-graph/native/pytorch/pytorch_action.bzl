"""Configured-action native PyTorch prefix rule.

The repository-rule skeleton remains a metadata/fallback path. This rule owns
the configured action where the full build can happen after the explicit token
is supplied.
"""

PytorchNativePrefixInfo = provider(
    doc = "Install prefix produced by the token-gated native PyTorch action.",
    fields = {
        "prefix": "Declared directory output containing the PyTorch prefix.",
        "wheel": "Declared wheel artifact copied out of the reusable build work tree.",
        "build_plan": "Planner JSON emitted by native/pytorch/plan.py.",
        "provider_metadata": "Native provider metadata JSON.",
        "package": "Spack package name.",
        "version": "Pinned PyTorch release.",
        "python_abi": "Python ABI tag supplied by the caller or derived by the driver.",
    },
)

PytorchTokenInfo = provider(
    doc = "Command-line token value for the native PyTorch execute action.",
    fields = {
        "value": "Token value supplied through --//native/pytorch:token.",
    },
)

PytorchMaxJobsInfo = provider(
    doc = "Per-run PyTorch build parallelism and resource reservation.",
    fields = {
        "value": "Integer job count supplied through --//native/pytorch:max_jobs.",
    },
)

_REQUIRED_TOKEN = "build-native-pytorch"
_PYTORCH_MAX_JOBS_CAP = 96
_PYTORCH_MEMORY_MB_PER_JOB = 4096
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
)

def _pytorch_token_flag_impl(ctx):
    return [PytorchTokenInfo(value = ctx.build_setting_value)]

def _pytorch_max_jobs_flag_impl(ctx):
    jobs = _validate_pytorch_max_jobs(ctx.build_setting_value)
    return [PytorchMaxJobsInfo(value = jobs)]

def _validate_pytorch_max_jobs(value):
    jobs = value if type(value) == "int" else int(value)
    if jobs < 1 or jobs > _PYTORCH_MAX_JOBS_CAP:
        fail("PyTorch max_jobs must be between 1 and {}, got {}".format(_PYTORCH_MAX_JOBS_CAP, value))
    return jobs

def _effective_pytorch_max_jobs(ctx):
    if ctx.attr.max_jobs:
        return _validate_pytorch_max_jobs(ctx.attr.max_jobs)
    return _validate_pytorch_max_jobs(ctx.attr._max_jobs_flag[PytorchMaxJobsInfo].value)

def _pytorch_resource_values(jobs):
    return {
        "cpu": jobs,
        "memory": jobs * _PYTORCH_MEMORY_MB_PER_JOB,
    }

def _pytorch_resource_set_1(os_name, inputs_size):
    return _pytorch_resource_values(1)

def _pytorch_resource_set_2(os_name, inputs_size):
    return _pytorch_resource_values(2)

def _pytorch_resource_set_3(os_name, inputs_size):
    return _pytorch_resource_values(3)

def _pytorch_resource_set_4(os_name, inputs_size):
    return _pytorch_resource_values(4)

def _pytorch_resource_set_5(os_name, inputs_size):
    return _pytorch_resource_values(5)

def _pytorch_resource_set_6(os_name, inputs_size):
    return _pytorch_resource_values(6)

def _pytorch_resource_set_7(os_name, inputs_size):
    return _pytorch_resource_values(7)

def _pytorch_resource_set_8(os_name, inputs_size):
    return _pytorch_resource_values(8)

def _pytorch_resource_set_9(os_name, inputs_size):
    return _pytorch_resource_values(9)

def _pytorch_resource_set_10(os_name, inputs_size):
    return _pytorch_resource_values(10)

def _pytorch_resource_set_11(os_name, inputs_size):
    return _pytorch_resource_values(11)

def _pytorch_resource_set_12(os_name, inputs_size):
    return _pytorch_resource_values(12)

def _pytorch_resource_set_13(os_name, inputs_size):
    return _pytorch_resource_values(13)

def _pytorch_resource_set_14(os_name, inputs_size):
    return _pytorch_resource_values(14)

def _pytorch_resource_set_15(os_name, inputs_size):
    return _pytorch_resource_values(15)

def _pytorch_resource_set_16(os_name, inputs_size):
    return _pytorch_resource_values(16)

def _pytorch_resource_set_17(os_name, inputs_size):
    return _pytorch_resource_values(17)

def _pytorch_resource_set_18(os_name, inputs_size):
    return _pytorch_resource_values(18)

def _pytorch_resource_set_19(os_name, inputs_size):
    return _pytorch_resource_values(19)

def _pytorch_resource_set_20(os_name, inputs_size):
    return _pytorch_resource_values(20)

def _pytorch_resource_set_21(os_name, inputs_size):
    return _pytorch_resource_values(21)

def _pytorch_resource_set_22(os_name, inputs_size):
    return _pytorch_resource_values(22)

def _pytorch_resource_set_23(os_name, inputs_size):
    return _pytorch_resource_values(23)

def _pytorch_resource_set_24(os_name, inputs_size):
    return _pytorch_resource_values(24)

def _pytorch_resource_set_25(os_name, inputs_size):
    return _pytorch_resource_values(25)

def _pytorch_resource_set_26(os_name, inputs_size):
    return _pytorch_resource_values(26)

def _pytorch_resource_set_27(os_name, inputs_size):
    return _pytorch_resource_values(27)

def _pytorch_resource_set_28(os_name, inputs_size):
    return _pytorch_resource_values(28)

def _pytorch_resource_set_29(os_name, inputs_size):
    return _pytorch_resource_values(29)

def _pytorch_resource_set_30(os_name, inputs_size):
    return _pytorch_resource_values(30)

def _pytorch_resource_set_31(os_name, inputs_size):
    return _pytorch_resource_values(31)

def _pytorch_resource_set_32(os_name, inputs_size):
    return _pytorch_resource_values(32)

def _pytorch_resource_set_33(os_name, inputs_size):
    return _pytorch_resource_values(33)

def _pytorch_resource_set_34(os_name, inputs_size):
    return _pytorch_resource_values(34)

def _pytorch_resource_set_35(os_name, inputs_size):
    return _pytorch_resource_values(35)

def _pytorch_resource_set_36(os_name, inputs_size):
    return _pytorch_resource_values(36)

def _pytorch_resource_set_37(os_name, inputs_size):
    return _pytorch_resource_values(37)

def _pytorch_resource_set_38(os_name, inputs_size):
    return _pytorch_resource_values(38)

def _pytorch_resource_set_39(os_name, inputs_size):
    return _pytorch_resource_values(39)

def _pytorch_resource_set_40(os_name, inputs_size):
    return _pytorch_resource_values(40)

def _pytorch_resource_set_41(os_name, inputs_size):
    return _pytorch_resource_values(41)

def _pytorch_resource_set_42(os_name, inputs_size):
    return _pytorch_resource_values(42)

def _pytorch_resource_set_43(os_name, inputs_size):
    return _pytorch_resource_values(43)

def _pytorch_resource_set_44(os_name, inputs_size):
    return _pytorch_resource_values(44)

def _pytorch_resource_set_45(os_name, inputs_size):
    return _pytorch_resource_values(45)

def _pytorch_resource_set_46(os_name, inputs_size):
    return _pytorch_resource_values(46)

def _pytorch_resource_set_47(os_name, inputs_size):
    return _pytorch_resource_values(47)

def _pytorch_resource_set_48(os_name, inputs_size):
    return _pytorch_resource_values(48)

def _pytorch_resource_set_49(os_name, inputs_size):
    return _pytorch_resource_values(49)

def _pytorch_resource_set_50(os_name, inputs_size):
    return _pytorch_resource_values(50)

def _pytorch_resource_set_51(os_name, inputs_size):
    return _pytorch_resource_values(51)

def _pytorch_resource_set_52(os_name, inputs_size):
    return _pytorch_resource_values(52)

def _pytorch_resource_set_53(os_name, inputs_size):
    return _pytorch_resource_values(53)

def _pytorch_resource_set_54(os_name, inputs_size):
    return _pytorch_resource_values(54)

def _pytorch_resource_set_55(os_name, inputs_size):
    return _pytorch_resource_values(55)

def _pytorch_resource_set_56(os_name, inputs_size):
    return _pytorch_resource_values(56)

def _pytorch_resource_set_57(os_name, inputs_size):
    return _pytorch_resource_values(57)

def _pytorch_resource_set_58(os_name, inputs_size):
    return _pytorch_resource_values(58)

def _pytorch_resource_set_59(os_name, inputs_size):
    return _pytorch_resource_values(59)

def _pytorch_resource_set_60(os_name, inputs_size):
    return _pytorch_resource_values(60)

def _pytorch_resource_set_61(os_name, inputs_size):
    return _pytorch_resource_values(61)

def _pytorch_resource_set_62(os_name, inputs_size):
    return _pytorch_resource_values(62)

def _pytorch_resource_set_63(os_name, inputs_size):
    return _pytorch_resource_values(63)

def _pytorch_resource_set_64(os_name, inputs_size):
    return _pytorch_resource_values(64)

def _pytorch_resource_set_65(os_name, inputs_size):
    return _pytorch_resource_values(65)

def _pytorch_resource_set_66(os_name, inputs_size):
    return _pytorch_resource_values(66)

def _pytorch_resource_set_67(os_name, inputs_size):
    return _pytorch_resource_values(67)

def _pytorch_resource_set_68(os_name, inputs_size):
    return _pytorch_resource_values(68)

def _pytorch_resource_set_69(os_name, inputs_size):
    return _pytorch_resource_values(69)

def _pytorch_resource_set_70(os_name, inputs_size):
    return _pytorch_resource_values(70)

def _pytorch_resource_set_71(os_name, inputs_size):
    return _pytorch_resource_values(71)

def _pytorch_resource_set_72(os_name, inputs_size):
    return _pytorch_resource_values(72)

def _pytorch_resource_set_73(os_name, inputs_size):
    return _pytorch_resource_values(73)

def _pytorch_resource_set_74(os_name, inputs_size):
    return _pytorch_resource_values(74)

def _pytorch_resource_set_75(os_name, inputs_size):
    return _pytorch_resource_values(75)

def _pytorch_resource_set_76(os_name, inputs_size):
    return _pytorch_resource_values(76)

def _pytorch_resource_set_77(os_name, inputs_size):
    return _pytorch_resource_values(77)

def _pytorch_resource_set_78(os_name, inputs_size):
    return _pytorch_resource_values(78)

def _pytorch_resource_set_79(os_name, inputs_size):
    return _pytorch_resource_values(79)

def _pytorch_resource_set_80(os_name, inputs_size):
    return _pytorch_resource_values(80)

def _pytorch_resource_set_81(os_name, inputs_size):
    return _pytorch_resource_values(81)

def _pytorch_resource_set_82(os_name, inputs_size):
    return _pytorch_resource_values(82)

def _pytorch_resource_set_83(os_name, inputs_size):
    return _pytorch_resource_values(83)

def _pytorch_resource_set_84(os_name, inputs_size):
    return _pytorch_resource_values(84)

def _pytorch_resource_set_85(os_name, inputs_size):
    return _pytorch_resource_values(85)

def _pytorch_resource_set_86(os_name, inputs_size):
    return _pytorch_resource_values(86)

def _pytorch_resource_set_87(os_name, inputs_size):
    return _pytorch_resource_values(87)

def _pytorch_resource_set_88(os_name, inputs_size):
    return _pytorch_resource_values(88)

def _pytorch_resource_set_89(os_name, inputs_size):
    return _pytorch_resource_values(89)

def _pytorch_resource_set_90(os_name, inputs_size):
    return _pytorch_resource_values(90)

def _pytorch_resource_set_91(os_name, inputs_size):
    return _pytorch_resource_values(91)

def _pytorch_resource_set_92(os_name, inputs_size):
    return _pytorch_resource_values(92)

def _pytorch_resource_set_93(os_name, inputs_size):
    return _pytorch_resource_values(93)

def _pytorch_resource_set_94(os_name, inputs_size):
    return _pytorch_resource_values(94)

def _pytorch_resource_set_95(os_name, inputs_size):
    return _pytorch_resource_values(95)

def _pytorch_resource_set_96(os_name, inputs_size):
    return _pytorch_resource_values(96)

_PYTORCH_RESOURCE_SETS = {
    1: _pytorch_resource_set_1,
    2: _pytorch_resource_set_2,
    3: _pytorch_resource_set_3,
    4: _pytorch_resource_set_4,
    5: _pytorch_resource_set_5,
    6: _pytorch_resource_set_6,
    7: _pytorch_resource_set_7,
    8: _pytorch_resource_set_8,
    9: _pytorch_resource_set_9,
    10: _pytorch_resource_set_10,
    11: _pytorch_resource_set_11,
    12: _pytorch_resource_set_12,
    13: _pytorch_resource_set_13,
    14: _pytorch_resource_set_14,
    15: _pytorch_resource_set_15,
    16: _pytorch_resource_set_16,
    17: _pytorch_resource_set_17,
    18: _pytorch_resource_set_18,
    19: _pytorch_resource_set_19,
    20: _pytorch_resource_set_20,
    21: _pytorch_resource_set_21,
    22: _pytorch_resource_set_22,
    23: _pytorch_resource_set_23,
    24: _pytorch_resource_set_24,
    25: _pytorch_resource_set_25,
    26: _pytorch_resource_set_26,
    27: _pytorch_resource_set_27,
    28: _pytorch_resource_set_28,
    29: _pytorch_resource_set_29,
    30: _pytorch_resource_set_30,
    31: _pytorch_resource_set_31,
    32: _pytorch_resource_set_32,
    33: _pytorch_resource_set_33,
    34: _pytorch_resource_set_34,
    35: _pytorch_resource_set_35,
    36: _pytorch_resource_set_36,
    37: _pytorch_resource_set_37,
    38: _pytorch_resource_set_38,
    39: _pytorch_resource_set_39,
    40: _pytorch_resource_set_40,
    41: _pytorch_resource_set_41,
    42: _pytorch_resource_set_42,
    43: _pytorch_resource_set_43,
    44: _pytorch_resource_set_44,
    45: _pytorch_resource_set_45,
    46: _pytorch_resource_set_46,
    47: _pytorch_resource_set_47,
    48: _pytorch_resource_set_48,
    49: _pytorch_resource_set_49,
    50: _pytorch_resource_set_50,
    51: _pytorch_resource_set_51,
    52: _pytorch_resource_set_52,
    53: _pytorch_resource_set_53,
    54: _pytorch_resource_set_54,
    55: _pytorch_resource_set_55,
    56: _pytorch_resource_set_56,
    57: _pytorch_resource_set_57,
    58: _pytorch_resource_set_58,
    59: _pytorch_resource_set_59,
    60: _pytorch_resource_set_60,
    61: _pytorch_resource_set_61,
    62: _pytorch_resource_set_62,
    63: _pytorch_resource_set_63,
    64: _pytorch_resource_set_64,
    65: _pytorch_resource_set_65,
    66: _pytorch_resource_set_66,
    67: _pytorch_resource_set_67,
    68: _pytorch_resource_set_68,
    69: _pytorch_resource_set_69,
    70: _pytorch_resource_set_70,
    71: _pytorch_resource_set_71,
    72: _pytorch_resource_set_72,
    73: _pytorch_resource_set_73,
    74: _pytorch_resource_set_74,
    75: _pytorch_resource_set_75,
    76: _pytorch_resource_set_76,
    77: _pytorch_resource_set_77,
    78: _pytorch_resource_set_78,
    79: _pytorch_resource_set_79,
    80: _pytorch_resource_set_80,
    81: _pytorch_resource_set_81,
    82: _pytorch_resource_set_82,
    83: _pytorch_resource_set_83,
    84: _pytorch_resource_set_84,
    85: _pytorch_resource_set_85,
    86: _pytorch_resource_set_86,
    87: _pytorch_resource_set_87,
    88: _pytorch_resource_set_88,
    89: _pytorch_resource_set_89,
    90: _pytorch_resource_set_90,
    91: _pytorch_resource_set_91,
    92: _pytorch_resource_set_92,
    93: _pytorch_resource_set_93,
    94: _pytorch_resource_set_94,
    95: _pytorch_resource_set_95,
    96: _pytorch_resource_set_96,
}

def _pytorch_action_prefix_impl(ctx):
    prefix = ctx.actions.declare_directory(ctx.label.name + "_prefix")
    wheel = ctx.actions.declare_file(ctx.label.name + "_wheel.whl")
    build_plan = ctx.actions.declare_file(ctx.label.name + "_build_plan.json")
    provider_metadata = ctx.actions.declare_file(ctx.label.name + "_provider_metadata.json")
    result_marker = ctx.actions.declare_file(ctx.label.name + "_result.txt")
    token = ctx.attr._token_flag[PytorchTokenInfo].value
    effective_max_jobs = _effective_pytorch_max_jobs(ctx)

    inputs = [
        ctx.file._plan,
        ctx.file.source_anchor,
        ctx.file.source_archive,
        ctx.file.source_manifest,
        ctx.file.zstd_prefix_file,
    ]
    args = [
        "--plan",
        ctx.file._plan.path,
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
        "--source-archive",
        ctx.file.source_archive.path,
        "--source-manifest",
        ctx.file.source_manifest.path,
        "--zstd-prefix-file",
        ctx.file.zstd_prefix_file.path,
        "--build-version",
        ctx.attr.build_version,
        "--torch-cuda-arch-list",
        ctx.attr.torch_cuda_arch_list,
        "--python-abi",
        ctx.attr.python_abi,
        "--token",
        token,
    ]
    if ctx.attr.execute:
        args.append("--execute")
    if ctx.attr.rootfs_cuda_bundle:
        args.append("--rootfs-cuda-bundle")
    if ctx.attr.synthetic_prefixes_for_dry_run:
        args.append("--synthetic-prefixes-for-dry-run")
    args.extend(["--max-jobs", str(effective_max_jobs)])

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
        mnemonic = "PytorchNativePrefix",
        progress_message = "Planning native PyTorch prefix %{label}",
        resource_set = _PYTORCH_RESOURCE_SETS[effective_max_jobs],
        use_default_shell_env = True,
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

pytorch_max_jobs_flag = rule(
    implementation = _pytorch_max_jobs_flag_impl,
    build_setting = config.int(flag = True),
    doc = "Per-run PyTorch build parallelism and matching action resources.",
)

pytorch_action_prefix = rule(
    implementation = _pytorch_action_prefix_impl,
    attrs = {
        "source_anchor": attr.label(
            allow_single_file = True,
            mandatory = True,
            doc = "File at the root of the pinned PyTorch source tree.",
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
        "zstd_prefix_file": attr.label(
            allow_single_file = True,
            mandatory = True,
            doc = "Native zstd prefix_path.txt used to unpack the tar.zst source archive.",
        ),
        "cuda_prefix_file": attr.label(
            allow_single_file = True,
            mandatory = True,
            doc = "Bazel-native CUDA prefix_path.txt.",
        ),
        "cudnn_prefix_file": attr.label(
            allow_single_file = True,
            mandatory = True,
            doc = "Bazel-native cuDNN prefix_path.txt.",
        ),
        "nccl_prefix_file": attr.label(
            allow_single_file = True,
            mandatory = True,
            doc = "Native NCCL prefix_path.txt.",
        ),
        "python_prefix_file": attr.label(
            allow_single_file = True,
            mandatory = True,
            doc = "Bazel-native Python prefix_path.txt.",
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
        "openblas_prefix_file": attr.label(
            allow_single_file = True,
            mandatory = True,
            doc = "Bazel-native OpenBLAS prefix_path.txt.",
        ),
        "protobuf_prefix_file": attr.label(
            allow_single_file = True,
            mandatory = True,
            doc = "Bazel-native protobuf prefix_path.txt.",
        ),
        "cusparselt_prefix_file": attr.label(
            allow_single_file = True,
            mandatory = True,
            doc = "Bazel-native cuSPARSELt prefix_path.txt.",
        ),
        "cudss_prefix_file": attr.label(
            allow_single_file = True,
            mandatory = True,
            doc = "Native cuDSS prefix_path.txt.",
        ),
        "nvshmem_prefix_file": attr.label(
            allow_single_file = True,
            mandatory = True,
            doc = "Native NVSHMEM prefix_path.txt.",
        ),
        "openmpi_prefix_file": attr.label(
            allow_single_file = True,
            mandatory = True,
            doc = "Bazel-native OpenMPI prefix_path.txt.",
        ),
        "numactl_prefix_file": attr.label(
            allow_single_file = True,
            mandatory = True,
            doc = "Bazel-native numactl prefix_path.txt for USE_NUMA.",
        ),
        "cpuinfo_prefix_file": attr.label(
            allow_single_file = True,
            mandatory = True,
            doc = "Native cpuinfo prefix_path.txt for PyTorch helper providers.",
        ),
        "fp16_prefix_file": attr.label(
            allow_single_file = True,
            mandatory = True,
            doc = "Native FP16 prefix_path.txt for PyTorch helper providers.",
        ),
        "fxdiv_prefix_file": attr.label(
            allow_single_file = True,
            mandatory = True,
            doc = "Native FXdiv prefix_path.txt for PyTorch helper providers.",
        ),
        "psimd_prefix_file": attr.label(
            allow_single_file = True,
            mandatory = True,
            doc = "Native psimd prefix_path.txt for PyTorch helper providers.",
        ),
        "pthreadpool_prefix_file": attr.label(
            allow_single_file = True,
            mandatory = True,
            doc = "Native pthreadpool prefix_path.txt for PyTorch helper providers.",
        ),
        "py_pip_prefix_file": attr.label(
            allow_single_file = True,
            mandatory = True,
            doc = "Native py-pip prefix_path.txt for python -m pip.",
        ),
        "py_setuptools_prefix_file": attr.label(
            allow_single_file = True,
            mandatory = True,
            doc = "Native py-setuptools prefix_path.txt for no-build-isolation wheel builds.",
        ),
        "py_wheel_prefix_file": attr.label(
            allow_single_file = True,
            mandatory = True,
            doc = "Native py-wheel prefix_path.txt for wheel frontend support.",
        ),
        "py_scikit_build_core_prefix_file": attr.label(
            allow_single_file = True,
            mandatory = True,
            doc = "Native py-scikit-build-core prefix_path.txt for PyTorch's build backend.",
        ),
        "py_numpy_prefix_file": attr.label(
            allow_single_file = True,
            mandatory = True,
            doc = "Native py-numpy prefix_path.txt for PyTorch build requirements.",
        ),
        "py_pyyaml_prefix_file": attr.label(
            allow_single_file = True,
            mandatory = True,
            doc = "Native py-pyyaml prefix_path.txt for PyTorch build requirements.",
        ),
        "py_typing_extensions_prefix_file": attr.label(
            allow_single_file = True,
            mandatory = True,
            doc = "Native py-typing-extensions prefix_path.txt for PyTorch build requirements.",
        ),
        "py_six_prefix_file": attr.label(
            allow_single_file = True,
            mandatory = True,
            doc = "Native py-six prefix_path.txt for PyTorch build requirements.",
        ),
        "py_packaging_prefix_file": attr.label(
            allow_single_file = True,
            mandatory = True,
            doc = "Native py-packaging prefix_path.txt for PyTorch build requirements.",
        ),
        "py_pathspec_prefix_file": attr.label(
            allow_single_file = True,
            mandatory = True,
            doc = "Native py-pathspec prefix_path.txt for scikit-build-core.",
        ),
        "py_protobuf_prefix_file": attr.label(
            allow_single_file = True,
            mandatory = True,
            doc = "Native py-protobuf prefix_path.txt for the selected protobuf family.",
        ),
        "build_version": attr.string(default = "2.14.0"),
        "torch_cuda_arch_list": attr.string(default = "10.0"),
        "python_abi": attr.string(default = "derived"),
        "execute": attr.bool(default = False),
        "rootfs_cuda_bundle": attr.bool(default = True),
        "synthetic_prefixes_for_dry_run": attr.bool(default = False),
        "max_jobs": attr.string(default = ""),
        "_token_flag": attr.label(
            default = Label("//native/pytorch:token"),
        ),
        "_max_jobs_flag": attr.label(
            default = Label("//native/pytorch:max_jobs"),
        ),
        # action_driver.py performs the insula/rootfs checks and invokes plan.py.
        "_driver": attr.label(
            default = "//native/pytorch:action_driver",
            executable = True,
            cfg = "exec",
        ),
        "_plan": attr.label(
            default = "//native/pytorch:plan.py",
            allow_single_file = True,
        ),
    },
    doc = (
        "Build or dry-run a native PyTorch prefix with declared source, prefix, " +
        "plan, and metadata inputs/outputs. Full build requires the " +
        "'{}' token and execute=True.".format(_REQUIRED_TOKEN)
    ),
)
