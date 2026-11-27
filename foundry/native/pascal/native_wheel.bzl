"""Real Pascal source-wheel actions using Foundry's shared native plan executor.

The immutable capsule is the tool/provider boundary. Its exact identity and
complete provider contract are declared inputs; no ambient host tools are used.
This additive graph leaves the modern source-built dependency graph unchanged.
"""

CapsuleToolchainInfo = provider(fields = ["identity", "contract"])

def _capsule_impl(ctx):
    return [
        DefaultInfo(files = depset([ctx.file.identity, ctx.file.contract])),
        CapsuleToolchainInfo(identity = ctx.file.identity, contract = ctx.file.contract),
    ]

capsule_toolchain = rule(
    implementation = _capsule_impl,
    attrs = {
        "identity": attr.label(allow_single_file = True, mandatory = True),
        "contract": attr.label(allow_single_file = True, mandatory = True),
    },
)

def _wheel_impl(ctx):
    tc = ctx.attr.toolchain[CapsuleToolchainInfo]
    wheel = ctx.actions.declare_file(ctx.attr.wheel_filename)
    args = [
        "run", "--offline", "--no-project", "--no-python-downloads",
        "--no-config", "--python", "/usr/local/bin/python3", "--",
        "python", "-B", ctx.file.runtime_executor.path,
        "--executor", ctx.file.executor.path,
        "--runtime-jobs-file", "/workspace/native-build/" + ctx.attr.package + "/jobs.txt",
        "--zstd-prefix", "/opt/vaso/pascal/zstd",
        "--", "--plan", ctx.file.plan.path, "--schema", ctx.file.schema.path,
        "--source-archive", "main=" + ctx.file.source_archive.path,
        "--rootfs-identity", tc.identity.path,
        "--wheel-out", wheel.path,
        # Existing Pascal wheels did not have the modern relocation guarantee.
        # This does not skip build, wheel or source validation.
        "--defer-relocation-audit",
    ]
    for name, prefix in ctx.attr.dependency_prefixes.items():
        args.extend(["--dep-prefix", name + "=" + prefix])
    for target, name in ctx.attr.extra_inputs.items():
        files = target.files.to_list()
        if len(files) != 1:
            fail("native extra input must contain one file: " + str(target.label))
        args.extend(["--input", name + "=" + files[0].path])
    for target, name in ctx.attr.patches.items():
        files = target.files.to_list()
        if len(files) != 1:
            fail("native patch must contain one file: " + str(target.label))
        args.extend(["--patch", name + "=" + files[0].path])
    all_inputs = [
        ctx.file.plan, ctx.file.schema, ctx.file.source_archive,
        ctx.file.executor, ctx.file.executor_schema, ctx.file.runtime_executor,
        ctx.file.native_step, tc.identity, tc.contract,
    ]
    ctx.actions.run(
        executable = "/opt/vaso/pascal/bin/uv",
        arguments = args,
        inputs = depset(all_inputs, transitive = [
            target.files for target in ctx.attr.extra_inputs
        ] + [
            target.files for target in ctx.attr.patches
        ]),
        outputs = [wheel],
        env = {
            "PATH": "/opt/vaso/pascal/bin:/usr/local/bin:/usr/bin:/bin",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONNOUSERSITE": "1",
            "UV_CACHE_DIR": "/workspace/native-build/uv-cache",
        },
        mnemonic = "PascalNativeWheel",
        progress_message = "Building full " + ctx.attr.package + " source wheel %{label}",
        execution_requirements = {"no-sandbox": "1"},
        use_default_shell_env = False,
    )
    return [DefaultInfo(files = depset([wheel]))]

native_wheel = rule(
    implementation = _wheel_impl,
    attrs = {
        "package": attr.string(mandatory = True, values = ["torch", "triton"]),
        "wheel_filename": attr.string(mandatory = True),
        "toolchain": attr.label(providers = [CapsuleToolchainInfo], mandatory = True),
        "plan": attr.label(allow_single_file = True, mandatory = True),
        "schema": attr.label(allow_single_file = True, mandatory = True),
        "source_archive": attr.label(allow_single_file = True, mandatory = True),
        "executor": attr.label(allow_single_file = True, mandatory = True),
        "executor_schema": attr.label(allow_single_file = True, mandatory = True),
        "runtime_executor": attr.label(allow_single_file = True, mandatory = True),
        "native_step": attr.label(allow_single_file = True, mandatory = True),
        "extra_inputs": attr.label_keyed_string_dict(allow_files = True),
        "patches": attr.label_keyed_string_dict(allow_files = True),
        "dependency_prefixes": attr.string_dict(),
    },
)
