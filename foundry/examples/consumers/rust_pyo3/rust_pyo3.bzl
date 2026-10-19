"""Hermetic Rust/PyO3 wheel action for the downstream consumer verifier."""

RustPyo3WheelInfo = provider(
    doc = "Rust/PyO3 repaired wheel output.",
    fields = {"wheel": "The repaired wheel file."},
)

RustPyo3ToolchainInfo = provider(
    doc = "Pinned Rust toolchain identity for the consumer PyO3 wheel.",
    fields = {
        "cargo_sha256": "Expected sha256 of the pinned cargo binary.",
        "rust_toolchain": "Absolute insula path to the pinned Rust toolchain.",
        "rust_toolchain_archive_sha256": "Expected sha256 of the prefetched Rust archive.",
        "rustc_sha256": "Expected sha256 of the pinned rustc binary.",
    },
)

def _rust_pyo3_toolchain_impl(ctx):
    return [platform_common.ToolchainInfo(
        rust_pyo3 = RustPyo3ToolchainInfo(
            cargo_sha256 = ctx.attr.cargo_sha256,
            rust_toolchain = ctx.attr.rust_toolchain,
            rust_toolchain_archive_sha256 = ctx.attr.rust_toolchain_archive_sha256,
            rustc_sha256 = ctx.attr.rustc_sha256,
        ),
    )]

rust_pyo3_toolchain = rule(
    implementation = _rust_pyo3_toolchain_impl,
    attrs = {
        "cargo_sha256": attr.string(mandatory = True),
        "rust_toolchain": attr.string(mandatory = True),
        "rust_toolchain_archive_sha256": attr.string(mandatory = True),
        "rustc_sha256": attr.string(mandatory = True),
    },
)

def _rust_pyo3_wheel_impl(ctx):
    out = ctx.actions.declare_file(ctx.label.name + ".whl")
    rust_toolchain = ctx.toolchains["//rust_pyo3:rust_pyo3_toolchain_type"].rust_pyo3
    sources = (
        ctx.files.srcs +
        ctx.files.cargo_files +
        ctx.files.cargo_vendor +
        [
            ctx.file.package_init,
            ctx.file.packager,
            ctx.file.vendor_manifest,
            ctx.file.rust_toolchain_manifest,
            ctx.file.linker,
        ]
    )
    args = ctx.actions.args()
    args.add(out.path)
    args.add(ctx.file.packager.path)
    args.add(ctx.file.package_init.path)
    args.add(ctx.file.vendor_manifest.path)
    args.add(ctx.file.linker.path)
    args.add(ctx.file.manifest.path)
    args.add(rust_toolchain.rust_toolchain)
    args.add(rust_toolchain.rust_toolchain_archive_sha256)
    args.add(rust_toolchain.rustc_sha256)
    args.add(rust_toolchain.cargo_sha256)

    command = """\
set -euo pipefail
out_rel="$1"
packager="$2"
package_init="$3"
vendor_manifest="$4"
linker="$5"
manifest="$6"
rust_toolchain="$7"
rust_archive_sha256="$8"
rustc_sha256="$9"
cargo_sha256="${10}"

case "$rust_toolchain" in
  /vaso/sources/rust/toolchains/*) ;;
  *) echo "rust_toolchain must be pinned under /vaso/sources/rust/toolchains" >&2; exit 2 ;;
esac

rustc="$rust_toolchain/bin/rustc"
cargo="$rust_toolchain/bin/cargo"
test -x "$rustc"
test -x "$cargo"
test "$(sha256sum "$rustc" | awk '{print $1}')" = "$rustc_sha256"
test "$(sha256sum "$cargo" | awk '{print $1}')" = "$cargo_sha256"

execroot="$PWD"
out="$execroot/$out_rel"
package_dir="$(dirname "$execroot/$manifest")"
manifest_name="$(basename "$manifest")"
build_dir="$execroot/$out_rel.build"
rm -rf "$build_dir"
mkdir -p "$build_dir/cargo-home" "$build_dir/rustup-home" "$build_dir/home" "$build_dir/target"

export CARGO_HOME="$build_dir/cargo-home"
export RUSTUP_HOME="$build_dir/rustup-home"
export HOME="$build_dir/home"
export PATH="/usr/bin:/bin"
export RUSTC="$rustc"
export CC="$execroot/$linker"
export CXX="$execroot/$linker"
export CARGO_TARGET_X86_64_UNKNOWN_LINUX_GNU_LINKER="$execroot/$linker"
export PYO3_NO_PYTHON=1
export VASO_IN_INSULA=1
export VASO_ROOTFS_BUNDLE_MANIFEST=/run/vaso/rootfs-bundle.json
export CARGO_TARGET_DIR="$build_dir/target"
export RUSTFLAGS="--remap-path-prefix=$execroot=. -C debuginfo=0 -C link-arg=-Wl,--build-id=none"

# The vendored crates live in the rust_pyo3_cargo module, next to the manifest
# that pins them; point cargo at that directory instead of a package-relative one.
vendor_dir="$execroot/$(dirname "$vendor_manifest")/vendor"
test -d "$vendor_dir"

cd "$package_dir"
"$cargo" build \
  --manifest-path "$manifest_name" \
  --config 'source.crates-io.replace-with="vendored-sources"' \
  --config 'source.vendored-sources.directory="'"$vendor_dir"'"' \
  --release \
  --locked \
  --offline

extension="$CARGO_TARGET_DIR/release/libvaso_rust_pyo3_ext.so"
test -f "$extension"
if command -v strip >/dev/null 2>&1; then
  strip --strip-debug "$extension" || true
fi

/usr/bin/python3 "$execroot/$packager" \
  --extension "$extension" \
  --init "$execroot/$package_init" \
  --out "$out" \
  --vendor-manifest "$execroot/$vendor_manifest" \
  --rust-toolchain "$rust_toolchain" \
  --rust-toolchain-archive-sha256 "$rust_archive_sha256" \
  --rustc-sha256 "$rustc_sha256" \
  --cargo-sha256 "$cargo_sha256"
"""

    ctx.actions.run_shell(
        inputs = depset(sources + [ctx.file.manifest, ctx.file.lockfile]),
        outputs = [out],
        arguments = [args],
        command = command,
        env = {
            "PATH": "/usr/bin:/bin",
            "VASO_IN_INSULA": "1",
            "VASO_ROOTFS_BUNDLE_MANIFEST": "/run/vaso/rootfs-bundle.json",
        },
        mnemonic = "VasoRustPyo3Wheel",
        progress_message = "Building Rust/PyO3 wheel %{label}",
    )
    return [
        DefaultInfo(files = depset([out])),
        RustPyo3WheelInfo(wheel = out),
    ]

rust_pyo3_wheel = rule(
    implementation = _rust_pyo3_wheel_impl,
    attrs = {
        "cargo_files": attr.label_list(allow_files = True),
        "cargo_vendor": attr.label(allow_files = True, mandatory = True),
        "linker": attr.label(allow_single_file = True, mandatory = True),
        "lockfile": attr.label(allow_single_file = True, mandatory = True),
        "manifest": attr.label(allow_single_file = True, mandatory = True),
        "package_init": attr.label(allow_single_file = True, mandatory = True),
        "packager": attr.label(allow_single_file = True, mandatory = True),
        "rust_toolchain_manifest": attr.label(allow_single_file = True, mandatory = True),
        "srcs": attr.label_list(allow_files = True),
        "vendor_manifest": attr.label(allow_single_file = True, mandatory = True),
    },
    toolchains = ["//rust_pyo3:rust_pyo3_toolchain_type"],
)
