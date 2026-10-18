"""Shared rootfs toolchain helpers for action-backed native builds."""

load("//native/common:build_settings.bzl", "NativeActionStringInfo", "native_action_string_flag")
load("@rules_cc//cc:find_cc_toolchain.bzl", "CC_TOOLCHAIN_ATTRS", "find_cpp_toolchain", "use_cc_toolchain")

_NATIVE_ACTION_CC_TOOLCHAINS = {
    "vaso-rootfs-clang-23": {
        "cc": "/usr/lib/llvm-23/bin/clang",
        "cxx": "/usr/lib/llvm-23/bin/clang++",
    },
    "vaso-rootfs-gcc-13.3": {
        "cc": "/usr/bin/gcc",
        "cxx": "/usr/bin/g++",
    },
}

def _torch_rootfs_platform_transition_impl(settings, attr):
    return {"//command_line_option:platforms": "//toolchains:torch_rootfs_platform"}


def _jax_rootfs_platform_transition_impl(settings, attr):
    return {"//command_line_option:platforms": "//toolchains:jax_rootfs_platform"}


_torch_rootfs_platform_transition = transition(
    implementation = _torch_rootfs_platform_transition_impl,
    inputs = [],
    outputs = ["//command_line_option:platforms"],
)

_jax_rootfs_platform_transition = transition(
    implementation = _jax_rootfs_platform_transition_impl,
    inputs = [],
    outputs = ["//command_line_option:platforms"],
)


def rootfs_platform_transition(profile):
    if profile == "torch":
        return _torch_rootfs_platform_transition
    if profile == "jax":
        return _jax_rootfs_platform_transition
    fail("native action profile must be torch or jax, got {}".format(profile))


def rootfs_platform_forward_attrs(profile):
    return {
        "actual": attr.label(
            mandatory = True,
            cfg = rootfs_platform_transition(profile),
        ),
        "_allowlist_function_transition": attr.label(
            default = Label("@bazel_tools//tools/allowlists/function_transition_allowlist"),
        ),
    }


def native_action_determinism_attrs():
    return {
        "_deterministic_prefix_map_roots": attr.label(default = Label("//native/common:deterministic_prefix_map_roots")),
        "_deterministic_seed": attr.label(default = Label("//native/common:deterministic_seed")),
        "_source_date_epoch": attr.label(default = Label("//native/common:source_date_epoch")),
    }


def native_action_toolchain_attrs():
    return CC_TOOLCHAIN_ATTRS | native_action_determinism_attrs() | {
        "_native_action_cuda_home": attr.label(default = Label("//native/common:native_action_cuda_home")),
    }


def native_action_toolchains():
    return use_cc_toolchain() + [
        "//toolchains:python_toolchain_info_type",
        "//toolchains:cuda_toolchain_type",
    ]


def _native_action_cc_paths(toolchain_identifier):
    paths = _NATIVE_ACTION_CC_TOOLCHAINS.get(toolchain_identifier)
    if paths == None:
        fail("native action C++ toolchain metadata is missing for {}".format(toolchain_identifier))
    return paths


def _deterministic_prefix_map_flags(prefix_map_roots):
    flags = []
    for root in prefix_map_roots.split(","):
        if root:
            flags.extend([
                "-ffile-prefix-map={}=.".format(root),
                "-fdebug-prefix-map={}=.".format(root),
                "-fmacro-prefix-map={}=.".format(root),
            ])
    return " ".join(flags)


def native_action_deterministic_env(ctx, *, compiler_flags, emit_nvcc_flags = False, native_build_token = ""):
    prefix_map_roots = ctx.attr._deterministic_prefix_map_roots[NativeActionStringInfo].value
    prefix_map_flags = _deterministic_prefix_map_flags(prefix_map_roots)
    deterministic_seed = ctx.attr._deterministic_seed[NativeActionStringInfo].value
    source_date_epoch = ctx.attr._source_date_epoch[NativeActionStringInfo].value
    if native_build_token:
        missing = []
        if not prefix_map_roots:
            missing.append("determinism prefix-map roots")
        if deterministic_seed == "unknown":
            missing.append("deterministic seed")
        if source_date_epoch == "0":
            missing.append("SOURCE_DATE_EPOCH")
        if missing:
            fail(
                "native action determinism inputs are required when native build token is set: " +
                ", ".join(missing),
            )
    env = {
        "PYTHONHASHSEED": "0",
        "SOURCE_DATE_EPOCH": source_date_epoch,
    }
    nvcc_flags = []
    if compiler_flags:
        if prefix_map_flags:
            env.update({
                "CFLAGS": prefix_map_flags,
                "CXXFLAGS": prefix_map_flags,
            })
    if emit_nvcc_flags:
        nvcc_flags.append("-forward-unknown-to-host-compiler")
        if prefix_map_flags:
            nvcc_flags.append(prefix_map_flags)
        if deterministic_seed:
            nvcc_flags.append("--frandom-seed={}".format(deterministic_seed))
        nvcc_flags.append("--objdir-as-tempdir")
        env["NVCC_FLAGS"] = " ".join(nvcc_flags)
    return struct(
        env = env,
        nvcc_flags = " ".join(nvcc_flags),
        prefix_map_flags = prefix_map_flags,
        prefix_map_roots = prefix_map_roots,
        seed = deterministic_seed,
        source_date_epoch = env["SOURCE_DATE_EPOCH"],
    )


def resolve_native_action_toolchain(ctx, *, output_basename, emit_nvcc_flags = True, native_build_token = ""):
    """Resolve the selected native build toolchains and emit a JSON descriptor."""
    cc_toolchain = find_cpp_toolchain(ctx)
    python = ctx.toolchains["//toolchains:python_toolchain_info_type"].python
    cuda = ctx.toolchains["//toolchains:cuda_toolchain_type"].cuda
    cc_paths = _native_action_cc_paths(cc_toolchain.toolchain_id)
    cc = cc_paths["cc"]
    cxx = cc_paths["cxx"]
    determinism = native_action_deterministic_env(
        ctx,
        compiler_flags = True,
        emit_nvcc_flags = emit_nvcc_flags,
        native_build_token = native_build_token,
    )
    selected_cuda_home = ctx.attr._native_action_cuda_home[NativeActionStringInfo].value
    cuda_home = selected_cuda_home or cuda.cuda_home
    if native_build_token and not selected_cuda_home:
        fail(
            "native action CUDA home is required when native build token is set: " +
            "pass --//native/common:native_action_cuda_home=<selected @cuda_native prefix>",
        )
    toolchain_json = ctx.actions.declare_file(output_basename + "_native_toolchain.json")
    payload = {
        "schema_version": 1,
        "cc": {
            "cc": cc,
            "cxx": cxx,
            "toolchain_identifier": cc_toolchain.toolchain_id,
        },
        "cuda": {
            "arch_list": cuda.cuda_arch_list,
            "home": cuda_home,
            "nvcc": cuda_home + "/bin/nvcc",
            "prefix_file": cuda.cuda_prefix_file,
            "providers": [
                {
                    "label": "@cuda_native//:lib",
                    "package": "cuda",
                },
            ],
        },
        "determinism": {
            "env": determinism.env,
            "prefix_map_flags": determinism.prefix_map_flags,
            "prefix_map_roots": determinism.prefix_map_roots,
        },
        "python": {
            "prefix_file": python.python_prefix_file,
            "version": python.version,
        },
    }
    ctx.actions.write(
        output = toolchain_json,
        content = json.encode_indent(payload, indent = "  ") + "\n",
    )
    return struct(
        all_files = cc_toolchain.all_files,
        cc = cc,
        cuda_prefix_file = cuda.cuda_prefix_file,
        cxx = cxx,
        deterministic_env = determinism.env,
        deterministic_prefix_map_flags = determinism.prefix_map_flags,
        deterministic_seed = determinism.seed,
        deterministic_source_date_epoch = determinism.source_date_epoch,
        deterministic_nvcc_flags = determinism.nvcc_flags,
        python_prefix_file = python.python_prefix_file,
        toolchain_identifier = cc_toolchain.toolchain_id,
        toolchain_json = toolchain_json,
    )
