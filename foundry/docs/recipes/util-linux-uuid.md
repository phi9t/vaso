# util-linux-uuid native recipe

## Position in the hillclimb

`util-linux-uuid` is topo index 23 in the `python` root graph, immediately
after `gdbm`:

```text
20 less
21 readline
22 gdbm
23 util-linux-uuid
24 xz
```

The generated lock exposes `libuuid` with no package link dependencies:

```json
{
  "package": "util-linux-uuid",
  "version": "2.41",
  "build": "spack",
  "link_deps": [],
  "link_libs": ["uuid"],
  "include_dirs": ["include", "include/uuid"]
}
```

## Spack evidence

All evidence here comes from the Bazel-owned hermetic Spack release
`@spack_dist//:spack`, pinned in `MODULE.bazel` to upstream Spack `v1.2.2`.
The reference prefix, archived package recipe, concrete spec, build log, and
build environment are from the hermetic `/vaso/cache/spack` store inside the
insula. Do not use an ambient host Spack checkout for this node.

Reference prefix:

```text
/vaso/cache/spack/opt/spack/linux-icelake/util-linux-uuid-2.41-euun67eg3ajbb6uy5gu3egzzeklpthfu
```

Hermetic recipe path:

```text
/vaso/cache/spack/opt/spack/linux-icelake/util-linux-uuid-2.41-euun67eg3ajbb6uy5gu3egzzeklpthfu/.spack/repos/spack_repo/builtin/packages/util_linux_uuid/package.py
```

Source provenance from the Spack recipe:

- package class: `UtilLinuxUuid(AutotoolsPackage)`
- URL pattern:
  `https://www.kernel.org/pub/linux/utils/util-linux/v{major.minor}/util-linux-{version}.tar.gz`
- concrete source URL:
  `https://www.kernel.org/pub/linux/utils/util-linux/v2.41/util-linux-2.41.tar.gz`
- SHA256: `c014b5861695b603d0be2ad1e6f10d5838b9d7859e1dd72d01504556817d8a87`
- build dependency: `pkgconfig`
- provided virtual on Linux: `uuid`

No package patch applies to `util-linux-uuid@2.41` for this concrete spec.

## Build recipe

The active Spack recipe logic is:

```python
def configure_args(self):
    config_args = [
        "--disable-use-tty-group",
        "--disable-makeinstall-chown",
        "--without-systemd",
        "--disable-all-programs",
        "--without-python",
        "--enable-libuuid",
        "--disable-bash-completion",
    ]
    return config_args
```

The installed configure argument capture is exactly:

```text
--disable-use-tty-group --disable-makeinstall-chown --without-systemd --disable-all-programs --without-python --enable-libuuid --disable-bash-completion
```

The hermetic build log shows the standard Autotools phases:

```text
==> util-linux-uuid: Executing phase: 'autoreconf'
==> util-linux-uuid: Executing phase: 'configure'
configure --prefix=<spack-prefix> \
  --disable-use-tty-group \
  --disable-makeinstall-chown \
  --without-systemd \
  --disable-all-programs \
  --without-python \
  --enable-libuuid \
  --disable-bash-completion
==> util-linux-uuid: Executing phase: 'build'
make V=1
libtool --mode=link ... -Wl,--version-script ./libuuid/src/libuuid.sym \
  -version-info 4:0:3 -o libuuid.la ... -lpthread
==> util-linux-uuid: Executing phase: 'install'
make install
```

Spack's Autotools plumbing filters the generated configure script before
running it. The native provider starts from the upstream release archive's
generated `configure` script, runs the same configure arguments, builds with
`make V=1`, installs, and removes libtool `.la` files so the prefix matches the
reference payload.

## Emitted prefix contract

ABI-relevant installed files:

- header: `include/uuid/uuid.h`
- shared library: `lib/libuuid.so.1.3.0`, SONAME `libuuid.so.1`
- shared-library symlinks: `lib/libuuid.so.1`, `lib/libuuid.so`
- static library: `lib/libuuid.a`
- pkg-config metadata: `lib/pkgconfig/uuid.pc`
- selected manpages:
  - `share/man/man3/uuid.3`
  - `share/man/man3/uuid_parse.3`
  - `share/man/man3/uuid_unparse.3`
  - `share/man/man3/uuid_generate.3`

Shared library dynamic contract:

- NEEDED `libc.so.6`, `ld-linux-x86-64.so.2`
- SONAME `libuuid.so.1`
- 24 exported dynamic symbols under the ABI gate's `nm -D --defined-only`
  filter

The installed `uuid.pc` exposes:

```text
Name: uuid
Description: Universally unique id library
Version: 2.41.0
Cflags: -I${includedir}/uuid
Libs.private:  -lpthread
Libs: -L${libdir} -luuid
```

## Prefix and ABI gate

`//synthetic:util_linux_uuid_abi_parity` compares
`@util_linux_uuid_native//:prefix` against the hermetic Spack reference prefix:

- layout: header, shared/static libraries, pkg-config file, and selected
  manpages
- ABI: SONAME and exported dynamic symbols for `libuuid`
- data: prefix-normalized `uuid.pc` and byte-identical selected manpages
- link-and-run: a downstream C consumer parses a fixed UUID, un-parses it
  canonically, copies it, compares it, and checks the null predicate

Current status: migrated provider. With `util-linux-uuid` enabled in
`native_overrides.json`, `SPACK_ROOT_PKG=python VASO_NATIVE=1
VASO_FORCE_FETCH_REPOS=@util_linux_uuid_native ./run.sh` passes inside the CUDA
insula. The gate reports matching layout, matching SONAME (`libuuid.so.1`),
matching exported symbols (24), prefix-normalized `uuid.pc` parity,
byte-identical selected manpages, and identical link-and-run output:

```text
uuid=00112233-4455-6677-8899-aabbccddeeff compare=0 null=1
```
