load("//settings:settings.bzl", "LabStringInfo")

def _flag_value(ctx, attr_name):
    target = getattr(ctx.attr, attr_name)
    if target:
        return target[LabStringInfo].value
    return ""

def _lab_action_impl(ctx):
    out = ctx.actions.declare_file(ctx.label.name + ".txt")
    toolchain = ctx.toolchains["//toolchains:c_toolchain_type"]

    args = ctx.actions.args()
    args.add("--mode", ctx.attr.mode)
    args.add("--label", str(ctx.label))
    args.add("--out", out.path)
    args.add("--cc-driver", toolchain.driver.path)

    inputs = []
    if ctx.file.plan:
        args.add("--plan", ctx.file.plan.path)
        inputs.append(ctx.file.plan)
    for src in ctx.files.srcs:
        args.add("--src", src.path)
        inputs.append(src)
    if ctx.file.dep:
        args.add("--declared-dep", ctx.file.dep.path)
        inputs.append(ctx.file.dep)
    for extra in ctx.files.extra_inputs:
        inputs.append(extra)

    seed = _flag_value(ctx, "seed")
    if seed:
        args.add("--seed", seed)
    jobs = _flag_value(ctx, "jobs")
    if jobs:
        args.add("--jobs", jobs)
    flag = _flag_value(ctx, "flag")
    if flag:
        args.add("--flag", flag)
    prefix_root = _flag_value(ctx, "prefix_root")
    if prefix_root:
        args.add("--prefix-root", prefix_root)

    if ctx.attr.dep_path:
        args.add("--dep-path", ctx.attr.dep_path)
    if ctx.attr.job_file:
        args.add("--job-file", ctx.attr.job_file)
    if ctx.attr.persistent_work_dir:
        args.add("--persistent-work-dir", ctx.attr.persistent_work_dir)
    if ctx.attr.read_env:
        args.add("--read-env", ctx.attr.read_env)
    if ctx.attr.observe_execution:
        args.add("--observe-execution")
    if ctx.attr.runtime_prefix_map:
        args.add("--runtime-prefix-map")

    env = dict(ctx.attr.env)
    env.setdefault("PATH", "/usr/bin:/bin")
    env.setdefault("LAB_ALLOWED", "1")
    env.setdefault("LAB_CC", toolchain.cc)

    requirements = {}
    if ctx.attr.no_sandbox:
        requirements["no-sandbox"] = "1"

    ctx.actions.run(
        executable = ctx.executable.executor,
        arguments = [args],
        inputs = depset(inputs),
        tools = [toolchain.driver],
        outputs = [out],
        env = env,
        use_default_shell_env = ctx.attr.use_default_shell_env,
        execution_requirements = requirements,
        mnemonic = "LabNative",
        progress_message = "running bazel-lab action %{label}",
    )
    return [DefaultInfo(files = depset([out]))]

lab_action = rule(
    implementation = _lab_action_impl,
    attrs = {
        "dep": attr.label(allow_single_file = True),
        "dep_path": attr.string(),
        "env": attr.string_dict(),
        "executor": attr.label(
            allow_files = True,
            cfg = "exec",
            default = Label("//tools:executor.py"),
            executable = True,
        ),
        "extra_inputs": attr.label_list(allow_files = True),
        "flag": attr.label(),
        "job_file": attr.string(),
        "jobs": attr.label(),
        "mode": attr.string(default = "simple"),
        "no_sandbox": attr.bool(default = False),
        "observe_execution": attr.bool(default = False),
        "persistent_work_dir": attr.string(),
        "plan": attr.label(allow_single_file = True),
        "prefix_root": attr.label(),
        "read_env": attr.string(),
        "runtime_prefix_map": attr.bool(default = False),
        "seed": attr.label(),
        "srcs": attr.label_list(allow_files = True),
        "use_default_shell_env": attr.bool(default = False),
    },
    toolchains = ["//toolchains:c_toolchain_type"],
)
