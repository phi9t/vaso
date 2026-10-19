"""Bazel action wrapper for release runtime dependency wheelhouses."""

load("//native/common:native_action_toolchain.bzl", "native_action_determinism_attrs", "native_action_deterministic_env")

RuntimeWheelhouseInfo = provider(
    doc = "Release runtime dependency wheelhouse outputs.",
    fields = {
        "manifest": "JSON manifest listing generated runtime dependency wheels.",
        "wheelhouse": "Declared directory containing generated runtime dependency wheels.",
    },
)


def _runtime_wheelhouse_impl(ctx):
    if len(ctx.files.prefix_files) != len(ctx.attr.prefix_files):
        fail("prefix_files must resolve to one file each")
    if len(ctx.files.provider_prefix_files) != len(ctx.attr.provider_prefix_files):
        fail("provider_prefix_files must resolve to one file each")

    wheelhouse = ctx.actions.declare_directory(ctx.label.name + "_wheelhouse")
    manifest = ctx.actions.declare_file(ctx.label.name + "_manifest.json")
    args = [
        "--out-dir",
        wheelhouse.path,
        "--manifest-out",
        manifest.path,
    ]
    inputs = list(ctx.files.prefix_files)
    inputs.extend(ctx.files.prefix_trees)
    inputs.extend(ctx.files.provider_prefix_files)
    inputs.extend(ctx.files.provider_prefix_trees)
    for prefix_file in ctx.files.prefix_files:
        args.extend(["--prefix-file", prefix_file.path])
    for prefix_file in ctx.files.provider_prefix_files:
        args.extend(["--provider-prefix-file", prefix_file.path])

    determinism = native_action_deterministic_env(ctx, compiler_flags = False)
    ctx.actions.run_shell(
        inputs = depset(inputs),
        tools = [ctx.executable._driver],
        outputs = [wheelhouse, manifest],
        command = """\
set -euo pipefail
driver="$1"
shift
export PATCHELF="${PATCHELF:-/usr/bin/patchelf}"
export READELF="${READELF:-/usr/bin/readelf}"
exec "$driver" "$@"
""",
        arguments = [ctx.executable._driver.path] + args,
        env = determinism.env,
        mnemonic = "VasoRuntimeWheelhouse",
        progress_message = "Packaging release runtime wheelhouse %{label}",
        use_default_shell_env = True,
    )
    return [
        DefaultInfo(files = depset([wheelhouse, manifest])),
        RuntimeWheelhouseInfo(
            manifest = manifest,
            wheelhouse = wheelhouse,
        ),
    ]


runtime_wheelhouse_action = rule(
    implementation = _runtime_wheelhouse_impl,
    attrs = {
        "prefix_files": attr.label_list(
            mandatory = True,
            allow_files = True,
            doc = "prefix_path.txt files for runtime dependency prefixes to package.",
        ),
        "prefix_trees": attr.label_list(
            mandatory = True,
            allow_files = True,
            doc = "Prefix filegroups paired with prefix_files so wheel contents are declared action inputs.",
        ),
        "provider_prefix_files": attr.label_list(
            allow_files = True,
            doc = "prefix_path.txt files for native provider prefixes whose shared libraries may need vendoring.",
        ),
        "provider_prefix_trees": attr.label_list(
            allow_files = True,
            doc = "Provider prefix filegroups paired with provider_prefix_files so vendored libraries are declared action inputs.",
        ),
        "_driver": attr.label(
            default = "//native/common:runtime_wheelhouse",
            executable = True,
            cfg = "exec",
        ),
    } | native_action_determinism_attrs(),
    doc = "Package Bazel-owned Python runtime prefixes as an offline release wheelhouse.",
)
