# libbsd native recipe

## Position in the hillclimb

`libbsd` is topo index 16 in the `python` root graph, after `libmd` and before
`expat`:

```text
15 libmd   autotools
16 libbsd  autotools
17 expat   autotools
```

`spack_libbsd` has been flipped from provider `spack` to provider `native`
without changing its DAG position or downstream edges:

```json
{
  "package": "libbsd",
  "version": "0.12.2",
  "build": "native",
  "link_deps": ["spack_libmd"],
  "link_libs": ["bsd", "bsd-ctor"],
  "include_dirs": ["include", "include/bsd"],
  "native_prefix": "@libbsd_native//:lib"
}
```

`link_deps` stays on `spack_libmd`, which is already backed by the native
`@libmd_native//:lib` provider in this hillclimb state.

## Spack evidence

Reference prefix from the Bazel-vendored Spack v1.2.2 run inside the CUDA
insula:

```text
/vaso/cache/spack/opt/spack/linux-icelake/libbsd-0.12.2-pa7l7mnnjfmldvsp73jvxb4xqcd3pfap
```

Source provenance from the hermetic Spack package recipe:

- Package class: `Libbsd(AutotoolsPackage)`
- Homepage: `https://libbsd.freedesktop.org/`
- Source URL used by the native rule:
  `https://libbsd.freedesktop.org/releases/libbsd-0.12.2.tar.xz`
- Version `0.12.2` SHA256:
  `b88cc9163d0c652aaf39a99991d974ddba1c3a9711db8f1b5838af2a14731014`
- Dependency: `depends_on("libmd", when="@0.11:")`

The concrete Spack build uses the standard Autotools flow:

```text
./configure --prefix=/vaso/cache/spack/opt/spack/linux-icelake/libbsd-0.12.2-pa7l7mnnjfmldvsp73jvxb4xqcd3pfap
make V=1
make install
```

Configure detects the already-migrated dependency with:

```text
checking for library containing MD5Update... -lmd
```

## Prefix contract

The ABI-relevant prefix contract used by the gate is:

- headers: `include/bsd/*.h`, `include/bsd/sys/*.h`, and
  `include/bsd/netinet/ip_icmp.h`
- libraries: `lib/libbsd.a`, `lib/libbsd-ctor.a`, `lib/libbsd.so`,
  `lib/libbsd.so.0`, `lib/libbsd.so.0.12.2`
- pkg-config metadata: `lib/pkgconfig/libbsd.pc`,
  `lib/pkgconfig/libbsd-overlay.pc`, `lib/pkgconfig/libbsd-ctor.pc`

`lib/libbsd.so` is a GNU ld script, not an ELF shared object:

```text
GROUP(<prefix>/lib/libbsd.so.0.12.2 AS_NEEDED(-lmd))
```

`lib/libbsd.so.0.12.2` has SONAME `libbsd.so.0` and needs `libmd.so.0` and
`libc.so.6`.

## Native build

`native/libbsd/libbsd.bzl` defines `libbsd_native`, a Bazel repository rule
that declares `VASO_IN_INSULA` as an environment input and refuses to build
unless the hermetic insula sets `VASO_IN_INSULA=1`.

The build action fetches the pinned source archive, reads the Bazel-built
`libmd` prefix from `@libmd_native//:prefix_path.txt`, and runs:

```sh
export CPPFLAGS="-I${LIBMD_PREFIX}/include ${CPPFLAGS:-}"
export LDFLAGS="-L${LIBMD_PREFIX}/lib -Wl,-rpath,${LIBMD_PREFIX}/lib ${LDFLAGS:-}"
./configure --prefix="$PREFIX"
make V=1 -j"${MAKE_JOBS:-$(nproc)}"
make install
find "$PREFIX" -type f -name '*.la' -delete || true
```

It exposes:

- `@libbsd_native//:prefix` for prefix and ABI parity
- `@libbsd_native//:lib`, linked with `-lbsd`, `-lbsd-ctor`, and the
  Bazel-built `libmd`, as the stable provider behind `@spack_libbsd//:lib`
  after the provider flip

The exported Bazel include surface deliberately uses only `prefix/include`.
`libbsd` has a normal API (`#include <bsd/string.h>`) and an overlay API
(`-isystem <prefix>/include/bsd -DLIBBSD_OVERLAY`). Exposing
`prefix/include/bsd` as a normal include directory makes `bsd/string.h` include
itself recursively through `<string.h>`.

## ABI gate

`//synthetic:libbsd_abi_parity` compares `@libbsd_native//:prefix` against the
hermetic Spack reference prefix with `tools/abi_parity.py`:

- layout: ABI-relevant headers, static/shared libraries, ld-script symlink
  surface, and the three pkg-config files
- ABI: SONAME and exported dynamic symbols for `lib/libbsd.so.0.12.2`
- data: prefix-normalized `libbsd.pc`, `libbsd-overlay.pc`, and
  `libbsd-ctor.pc`
- link-and-run: `synthetic/use_libbsd.c` calls `strlcpy()` through
  `<bsd/string.h>` and expects `hello l:12`

Because `lib/libbsd.so` is an ld script containing `AS_NEEDED(-lmd)`, the
link-and-run axis explicitly adds the corresponding `libmd` prefix on each
side: `SPACK_LIBMD_PREFIX` for the reference and `@libmd_native//:prefix` for
the candidate. This matches the unchanged DAG edge `spack_libbsd ->
spack_libmd` rather than pretending `libbsd` is standalone.

Current verdict: migrated provider. With `libbsd` enabled in
`native_overrides.json`, this command passes inside the CUDA insula:

```sh
SPACK_ROOT_PKG=python VASO_NATIVE=1 VASO_SPACK_TIMEOUT=600 ./run.sh
```

The gate reports matching layout, matching SONAME (`libbsd.so.0`), matching
exported symbols (105), identical `strlcpy()` link-and-run output, and matching
prefix-normalized pkg-config files.
