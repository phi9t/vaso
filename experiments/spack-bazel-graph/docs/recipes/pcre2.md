# pcre2 frontier recipe

## Position in the hillclimb

`pcre2` is the migrated py-torch frontier node immediately after `openblas`. In
the captured `SPACK_ROOT_PKG=py-torch` graph it appears as:

```text
32  pcre2  10.44  autotools  native; ABI parity green
```

The native verification run used the package-specific root:

```bash
BAZEL_BIN=$HOME/.local/bin/bazel-9.2.0 \
SPACK_ROOT_PKG='pcre2@10.44' \
VASO_NATIVE=1 \
VASO_FORMAL=0 \
VASO_SKIP_CONSUMER_TESTS=1 \
VASO_EXTRA_TEST_TARGETS='//synthetic:use_pcre2_native //tools:hermetic_native_deps_guard_test //tools:abi_parity_unit_test' \
VASO_SPACK_TIMEOUT=1800 \
VASO_LOCK_OUT=/workspace/experiment/pcre2_spack_graph.lock.json \
VASO_BUILD_GRAPH_OUT=/workspace/experiment/pcre2_build_graph.json \
VASO_FORCE_FETCH_REPOS='@pcre2_native' \
./run.sh
```

That command seats the CUDA insula, forces the Bazel-owned native repository
fetch inside the insula, runs Bazel's vendored `@spack_dist//:spack`, applies
the `native_overrides.json` flip to `@pcre2_native//:lib`, and runs the native
smoke, Autotools hermetic-deps guard, ABI metadata unit test, and
`//synthetic:pcre2_abi_parity` inside the same insula.

## Spack evidence

All recipe evidence comes from the Bazel-owned hermetic Spack release
`@spack_dist//:spack`, pinned in `MODULE.bazel` to upstream Spack `v1.2.2`.
Do not use an ambient host Spack checkout for this package.

Hermetic recipe path:

```text
/vaso/cache/spack/user/package_repos/.../repos/spack_repo/builtin/packages/pcre2/package.py
```

Source provenance from the Spack recipe:

- package class: `Pcre2(AutotoolsPackage, CMakePackage)`
- selected concrete build system: `autotools`
- upstream source URL:
  `https://github.com/PCRE2Project/pcre2/releases/download/pcre2-10.44/pcre2-10.44.tar.bz2`
- SHA256:
  `d34f02e113cf7193a1ebf2770d3ac527088d485d4e047ed10e5d217c6ef5de96`
- concrete variants: `+multibyte`, `~jit`, `+pic`

The concrete `pcre2@10.44` node has only toolchain dependencies:

```text
build: compiler-wrapper, gcc, gmake
link: gcc-runtime, glibc
```

The hermetic Spack build log shows the Autotools flow:

```text
autoreconf
configure --prefix=<pcre2-prefix> --enable-pcre2-16 --enable-pcre2-32
make V=1
make install
```

The configure summary records the important negative feature decisions:

```text
Enable JIT compiling support ....... : no
Link pcre2grep with libz ........... : no
Link pcre2grep with libbz2 ......... : no
Link pcre2test with libedit ........ : no
Link pcre2test with libreadline .... : no
Build shared libs .................. : yes
Build static libs .................. : yes
```

The reference prefix installed by hermetic Spack:

```text
/vaso/cache/spack/opt/spack/linux-icelake/pcre2-10.44-iplcr7o6iu7nyaurkfmeokpby6dozfaq
```

The ABI/behavior-relevant installed surface for this migration is:

```text
bin/pcre2-config
bin/pcre2grep
bin/pcre2test
include/pcre2.h
include/pcre2posix.h
lib/libpcre2-8.so.0.13.0
lib/libpcre2-16.so.0.13.0
lib/libpcre2-32.so.0.13.0
lib/libpcre2-posix.so.3.0.5
lib/libpcre2-8.a
lib/libpcre2-16.a
lib/libpcre2-32.a
lib/libpcre2-posix.a
lib/pkgconfig/libpcre2-8.pc
lib/pkgconfig/libpcre2-16.pc
lib/pkgconfig/libpcre2-32.pc
lib/pkgconfig/libpcre2-posix.pc
```

The reference shared libraries have SONAMEs `libpcre2-8.so.0`,
`libpcre2-16.so.0`, `libpcre2-32.so.0`, and `libpcre2-posix.so.3`.

## Build recipe

`native/pcre2/pcre2.bzl` mirrors Spack's Autotools build:

```text
download pcre2-10.44.tar.bz2
export CFLAGS="-O2 -fvisibility=hidden"
configure --prefix=<prefix> --enable-pcre2-16 --enable-pcre2-32 \
  --disable-jit --disable-pcre2grep-libz --disable-pcre2grep-libbz2 \
  --disable-pcre2test-libreadline --disable-pcre2test-libedit
make V=1 -j$MAKE_JOBS
make install
find <prefix> -type f -name '*.la' -delete
```

The extra `--disable-*` flags pin optional dependency choices that the Spack
configure summary observed as disabled. This prevents Autotools from discovering
ambient compression or line-editing libraries.

`pcre2` has no non-toolchain dependency prefixes, so the corresponding
hermetic-deps verifier for this build mechanism is the Autotools/no-dependency
case. `//tools:hermetic_native_deps_guard_test` confirms the rule is classified
as `autotools`, the build action is insula-gated, and no dependency prefix is
discovered from the host:

```text
native/pcre2/pcre2.bzl: autotools: no dep prefixes
```

## ABI gate target

`//synthetic:pcre2_abi_parity` compares the native prefix against the hermetic
Spack reference. The gate covers:

- exact ABI-relevant layout parity for 25 entries;
- SONAME parity for all four shared libraries;
- exported dynamic symbol parity: 76 symbols each for the 8/16/32-bit
  libraries and 4 symbols for the POSIX wrapper;
- static archive member/exported-symbol parity for all four archives;
- prefix-normalized `bin/pcre2-config`;
- prefix-normalized `lib/pkgconfig/libpcre2-{8,16,32,posix}.pc`;
- executable dynamic dependency parity for `bin/pcre2grep` and `bin/pcre2test`;
- downstream `pcre2_compile()`/`pcre2_match()` link-and-run behavior;
- matching `pcre2grep` and `pcre2-config --version` behavior.

The smoke target links against the native prefix and prints:

```text
pcre2:10.44:42:2:ok
```

Current status: native and ABI-gated. The latest run used `rootfs mode:
cuda-bundle`, reported `hermetic spack (Bazel-owned) version: 1.2.2`, and
passed `//synthetic:use_pcre2_native`,
`//tools:hermetic_native_deps_guard_test`, `//tools:abi_parity_unit_test`, and
`//synthetic:pcre2_abi_parity` inside the CUDA insula.
