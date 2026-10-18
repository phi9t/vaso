# icu4c frontier recipe

## Position in the hillclimb

`icu4c@76.1` is the lean `py-torch` frontier Unicode library/tool node after
native `fontconfig@2.15.0`:

```text
85  fontconfig  2.15.0  autotools  native; ABI parity green
86  icu4c       76.1    autotools
```

The focused reference graph for `SPACK_ROOT_PKG='icu4c@76.1'` ends with:

```text
38  icu4c  76.1  autotools
```

Spack still owns the DAG shape. The native flip changes only
`spack_icu4c.build` to `native` and re-exports `@icu4c_native//:lib`; link
edges remain Spack-derived.

## Spack evidence

All recipe evidence comes from Bazel's vendored `@spack_dist//:spack` running
inside the CUDA insula. Do not use an ambient host Spack checkout.

Reference prefix from the focused hermetic run:

```text
/vaso/cache/spack/opt/spack/linux-icelake/icu4c-76.1-m2jcuzeyoq2giajw45jixqzu2fs4dya4
```

Hermetic recipe path observed from the installed prefix:

```text
/vaso/cache/spack/opt/spack/linux-icelake/icu4c-76.1-m2jcuzeyoq2giajw45jixqzu2fs4dya4/.spack/repos/spack_repo/builtin/packages/icu4c/package.py
```

Source provenance from that recipe:

- package class: `Icu4c(AutotoolsPackage, MSBuildPackage)`
- selected build system: `autotools`
- variant: `cxxstd=17`
- upstream source URL:
  `https://github.com/unicode-org/icu/releases/download/release-76-1/icu4c-76_1-src.tgz`
- version `76.1` SHA256:
  `dfacb46bfe4747410472ce3e1144bf28a102feeaa4e3875bac9b4c6cf30f4f3e`

The concrete focused node has:

```text
build: autoconf, automake, compiler-wrapper, gcc, gmake, libtool, python
link: gcc-runtime, glibc
```

The hermetic Spack recipe contract is:

```text
LC_ALL=en_US.UTF-8
CXXFLAGS=-std=c++17
cd source
./configure --prefix=<prefix> PYTHON=<python-prefix>/bin/python3.14
make
make install
```

Spack's compiler wrapper also injects the target policy
`-march=icelake-client -mtune=icelake-client` and old-dtags RPATH policy at
execution time. That is intentionally not preserved as a host Spack dependency;
the native rule replays the observed policy directly inside the insula.

## Native build recipe

`native/icu4c/icu4c.bzl` mirrors the Spack Autotools flow:

```text
download icu4c-76_1-src.tgz
cd <src>/source
validate AUTOCONF_PREFIX, AUTOMAKE_PREFIX, LIBTOOL_PREFIX, M4_PREFIX,
         PYTHON_PREFIX
PATH=<autoconf>:<automake>:<libtool>:<m4>:<python>:$PATH
M4=<m4-prefix>/bin/m4
PYTHON=<python-prefix>/bin/python3
CC=gcc
CXX=g++
unset CFLAGS
CXXFLAGS=-std=c++17
./configure --prefix=<prefix> PYTHON=<python-prefix>/bin/python3
make V=1 CFLAGS="<ICU C release flags> -march=icelake-client -mtune=icelake-client" \
         CXXFLAGS+=" -march=icelake-client -mtune=icelake-client" \
         LDFLAGS+=" -Wl,-rpath,<prefix>/lib -Wl,--disable-new-dtags"
make install
delete .la files
scrub target/RPATH flags from lib/icu/current/pkgdata.inc and lib/icu/76.1/pkgdata.inc
preserve lib/icu/pkgdata.inc as a symlink to current/pkgdata.inc
emit prefix_path.txt
```

The native rule refuses to evaluate unless `VASO_IN_INSULA=1`, consumes every
build dependency through a mandatory Bazel `*_prefix_file`, validates all five
prefixes before configure, and routes Autotools/Python discovery through
`PATH`, `M4`, and `PYTHON`.

The C++ flag shape is part of the ABI contract: overriding ICU's make-time
`CXXFLAGS` with `-O2` removed exported weak C++ symbols relative to the Spack
reference. The native recipe therefore follows Spack's recorded
`CXXFLAGS=-std=c++17` at configure time and appends only the target CPU flags at
make time.

The corresponding mechanism verifier reports:

```text
native/icu4c/icu4c.bzl: autotools: AUTOCONF_PREFIX, AUTOMAKE_PREFIX, LIBTOOL_PREFIX, M4_PREFIX, PYTHON_PREFIX
```

## ABI and behavior gate

The smoke target is:

```text
//synthetic:use_icu4c_native
```

It links a C++ consumer against `@icu4c_native//:lib`, converts UTF-8 text
through ICU, checks version `76.1`, and prints:

```text
icu4c:76.1:utf8-ok
```

`//synthetic:icu4c_abi_parity` compares the native prefix against the hermetic
Spack reference. It covers:

- shared-library layout and SONAME/exported-symbol parity for `libicudata`,
  `libicui18n`, `libicuio`, `libicutest`, `libicutu`, and `libicuuc`;
- prefix-normalized `lib/pkgconfig/icu-uc.pc`, `icu-i18n.pc`, and `icu-io.pc`;
- prefix-normalized `lib/icu/Makefile.inc`, `lib/icu/current/pkgdata.inc`,
  and `lib/icu/76.1/pkgdata.inc`, with top-level `lib/icu/pkgdata.inc`
  preserving Spack's `current/pkgdata.inc` symlink shape;
- executable NEEDED parity for `icuinfo`, `uconv`, and `icu-config`;
- matching `icuinfo --version`, `uconv -V`, and
  `icu-config --version --detect-prefix` behavior;
- downstream C++ link-and-run behavior against both the Spack reference and
  native candidate prefixes.

Current status: native `icu4c` is gated inside the hermetic CUDA insula. The
focused verification command was:

```bash
env PATH=/usr/bin:/bin:$HOME/.local/bin \
BAZEL_BIN=$HOME/.local/bin/bazel-9.2.0 \
VASO_ESTATE_ROOT=$VASO_ESTATE_ROOT \
SPACK_ROOT_PKG='icu4c@76.1' \
VASO_NATIVE=1 \
VASO_SKIP_CONSUMER_TESTS=1 \
VASO_SKIP_NATIVE_ABI_GATES=1 \
VASO_EXTRA_TEST_TARGETS='//tools:hermetic_native_deps_guard_test //synthetic:use_icu4c_native //synthetic:icu4c_abi_parity' \
VASO_LOCK_OUT=/workspace/experiment/spack_graph.lock.json \
VASO_BUILD_GRAPH_OUT=/workspace/experiment/build_graph.json \
VASO_SPACK_TIMEOUT=1800 \
./run.sh
```

That run used Bazel's `@spack_dist//:spack` inside rootfs mode `cuda-bundle`,
reported hermetic Spack version `1.2.2`, passed
`//tools:hermetic_native_deps_guard_test`, passed
`//synthetic:use_icu4c_native`, and passed
`//synthetic:icu4c_abi_parity`.
