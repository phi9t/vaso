"""Action-backed zlib-ng native prefix pilot."""

load("@rules_cc//cc:action_names.bzl", "C_COMPILE_ACTION_NAME")
load("@rules_cc//cc:find_cc_toolchain.bzl", "CC_TOOLCHAIN_ATTRS", "find_cpp_toolchain", "use_cc_toolchain")
load("@rules_cc//cc/common:cc_common.bzl", "cc_common")

NativeActionPrefixInfo = provider(
    doc = "Install prefix produced by a normal Bazel build action.",
    fields = {
        "prefix": "Declared directory output containing the install prefix.",
        "package": "Spack package name.",
        "version": "Pinned upstream version.",
    },
)

_ZLIB_NG_ACTION_CPUS = 4
_ZLIB_NG_ACTION_MEMORY_MB = 4096

def _zlib_ng_resource_set(os_name, input_count):
    return {
        "cpu": _ZLIB_NG_ACTION_CPUS,
        "memory": _ZLIB_NG_ACTION_MEMORY_MB,
    }

def _zlib_ng_action_prefix_impl(ctx):
    prefix = ctx.actions.declare_directory(ctx.label.name)
    cc_toolchain = find_cpp_toolchain(ctx)
    feature_configuration = cc_common.configure_features(
        ctx = ctx,
        cc_toolchain = cc_toolchain,
        requested_features = ctx.features,
        unsupported_features = ctx.disabled_features,
    )
    cc = cc_common.get_tool_for_action(
        feature_configuration = feature_configuration,
        action_name = C_COMPILE_ACTION_NAME,
    )

    inputs = depset(
        ctx.files.srcs + [ctx.file.configure],
        transitive = [cc_toolchain.all_files],
    )
    command = """\
set -euo pipefail
configure="$1"
prefix="$2"

case "$prefix" in
  /*) prefix_abs="$prefix" ;;
  *) prefix_abs="$PWD/$prefix" ;;
esac

src_dir="$(dirname "$configure")"
work="${prefix_abs}.work"
rm -rf "$prefix_abs" "$work"
mkdir -p "$work/src"
cp -R -L "$src_dir"/. "$work/src"/
chmod -R u+w "$work/src"

cd "$work/src"
./configure --prefix="$prefix_abs" --zlib-compat
make -j"${MAKE_JOBS}"
make install

pc="$prefix_abs/lib/pkgconfig/zlib.pc"
if [[ -f "$pc" ]]; then
  sed -i 's|^prefix=.*|prefix=${pcfiledir}/../..|' "$pc"
fi

rm -rf "$work"
"""
    ctx.actions.run_shell(
        inputs = inputs,
        outputs = [prefix],
        command = command,
        arguments = [
            ctx.file.configure.path,
            prefix.path,
        ],
        env = {
            "CC": cc,
            "LC_ALL": "C",
            "MAKE_JOBS": str(_ZLIB_NG_ACTION_CPUS),
            "PATH": "/usr/bin:/bin",
        },
        mnemonic = "ZlibNgActionPrefix",
        progress_message = "Building zlib-ng action prefix %{label}",
        resource_set = _zlib_ng_resource_set,
    )

    return [
        DefaultInfo(files = depset([prefix])),
        NativeActionPrefixInfo(
            prefix = prefix,
            package = "zlib-ng",
            version = "2.3.3",
        ),
    ]

zlib_ng_action_prefix = rule(
    implementation = _zlib_ng_action_prefix_impl,
    attrs = {
        "configure": attr.label(
            allow_single_file = True,
            mandatory = True,
            doc = "The zlib-ng configure script from the source archive.",
        ),
        "srcs": attr.label_list(
            allow_files = True,
            mandatory = True,
            doc = "All files from the pinned zlib-ng source archive.",
        ),
    } | CC_TOOLCHAIN_ATTRS,
    fragments = ["cpp"],
    toolchains = use_cc_toolchain(),
    doc = "Build zlib-ng into a declared directory output using a Bazel action.",
)
