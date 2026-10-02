# fribidi frontier recipe

## Position in the hillclimb

`fribidi@1.0.12` is the next lean `py-torch` frontier node after native
`libtool`. In the captured lean PyTorch graph it appears as:

```text
81  libtool  2.5.4   autotools  native; ABI parity green
82  fribidi  1.0.12  autotools
```

The focused reference capture used:

```bash
SPACK_ROOT_PKG='fribidi@1.0.12' \
VASO_NATIVE=0 \
VASO_FORMAL=0 \
VASO_SKIP_CONSUMER_TESTS=1 \
VASO_SKIP_NATIVE_ABI_GATES=1 \
VASO_LOCK_OUT=/workspace/experiment/fribidi_spack_graph.lock.json \
VASO_BUILD_GRAPH_OUT=/workspace/experiment/fribidi_build_graph.json \
VASO_SPACK_TIMEOUT=1800 \
./run.sh
```

It installed:

```text
/vaso/cache/spack/opt/spack/linux-icelake/fribidi-1.0.12-f5f55xm2hsmlmmpc5op5jbiacr5yluw3
```

and stopped at the non-canonical-lock guard, as expected for a side reference
capture.

## Spack evidence

All recipe evidence comes from the Bazel-owned hermetic Spack release
`@spack_dist//:spack`, pinned in `MODULE.bazel` to upstream Spack `v1.2.2`.
Do not use an ambient host Spack checkout for this package.

Hermetic recipe path:

```text
/vaso/cache/spack/user/package_repos/.../repos/spack_repo/builtin/packages/fribidi/package.py
```

Source provenance from the Spack recipe:

- package class: `Fribidi(AutotoolsPackage)`
- version: `1.0.12`
- upstream source URL:
  `https://github.com/fribidi/fribidi/releases/download/v1.0.12/fribidi-1.0.12.tar.xz`
- SHA256:
  `0cd233f97fc8c67bb3ac27ce8440def5d3ffacf516765b91c2cc654498293495`
- build system: `AutotoolsPackage`
- package-specific configure args: none

The concrete focused dependency edges are:

```text
build: autoconf, automake, compiler-wrapper, gcc, gmake, libtool, m4
link: gcc-runtime, glibc
```

The hermetic Spack build log shows:

```text
autoreconf
<spack-src>/configure --prefix=<fribidi-prefix>
make V=1
make install
```

Spack's libtool probes produce a shared-only library for this concrete build:

```text
lib/libfribidi.so
lib/libfribidi.so.0
lib/libfribidi.so.0.4.0
```

with SONAME `libfribidi.so.0`.

## Build recipe

`native/fribidi/fribidi.bzl` mirrors the Spack Autotools flow:

```text
download fribidi-1.0.12.tar.xz
cd <src>
PATH=<autoconf>:<automake>:<libtool>:<m4>:$PATH
M4=<m4-prefix>/bin/m4
autoreconf -ivf
./configure --prefix=<prefix>
make V=1
make install
delete .la files
```

The repository rule refuses to run unless the hermetic insula has set
`VASO_IN_INSULA=1`. Build tool dependencies are not discovered from the host.
These mandatory Bazel prefix files are read by the repository rule and
validated by the shell build before configure:

```text
AUTOCONF_PREFIX
AUTOMAKE_PREFIX
LIBTOOL_PREFIX
M4_PREFIX
```

The mechanism verifier must report:

```text
native/fribidi/fribidi.bzl: autotools: AUTOCONF_PREFIX, AUTOMAKE_PREFIX, LIBTOOL_PREFIX, M4_PREFIX
```

## ABI and behavior gate

`//synthetic:fribidi_abi_parity` compares the native prefix against the
hermetic Spack reference prefix supplied by `run.sh` through
`SPACK_FRIBIDI_PREFIX`. The gate covers:

- `include/` and `include/fribidi/`;
- `lib/libfribidi.so -> libfribidi.so.0 -> libfribidi.so.0.4.0`;
- SONAME and exported dynamic symbol parity for `libfribidi`;
- `lib/pkgconfig/fribidi.pc`;
- `bin/fribidi --version` behavior;
- a downstream C link-and-run consumer that checks `fribidi_version_info` and
  `fribidi_get_bidi_type` for LTR and RTL codepoints.

The smoke target prints:

```text
fribidi:1.0.12:bidi-ok
```
