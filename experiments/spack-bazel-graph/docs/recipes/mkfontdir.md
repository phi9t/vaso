# mkfontdir@1.0.7

## Position in the hillclimb

`mkfontdir@1.0.7` is the py-torch frontier node immediately after native
`mkfontscale@1.2.3`. In the captured `SPACK_ROOT_PKG=py-torch` graph it
appears as:

```text
72  mkfontdir  1.0.7  autotools
```

The focused reference graph used for this migration is:

```bash
SPACK_ROOT_PKG='mkfontdir@1.0.7'
```

The focused all-Spack run wrote `mkfontdir_spack_graph.lock.json` and
`mkfontdir_build_graph.json`. The native run wrote
`mkfontdir_native_spack_graph.lock.json` and
`mkfontdir_native_build_graph.json`, then flips `spack_mkfontdir` to
`@mkfontdir_native//:lib` without changing the Spack DAG edges. The lock keeps
the runtime edge to `spack_mkfontscale`.

## Hermetic Spack Evidence

All recipe evidence comes from the Bazel-owned hermetic Spack release
`@spack_dist//:spack`, pinned in `MODULE.bazel` to upstream Spack `v1.2.2`.
Do not use an ambient host Spack checkout for this package.

Hermetic recipe path in the isolated estate:

```text
/vaso/cache/spack/user/package_repos/fncqgg4/repos/spack_repo/builtin/packages/mkfontdir/package.py
```

Source provenance from the Spack recipe:

- package class: `Mkfontdir(AutotoolsPackage, XorgPackage)`
- homepage: `https://cgit.freedesktop.org/xorg/app/mkfontdir`
- concrete Linux build mechanism: Autotools
- upstream source URL:
  `https://xorg.freedesktop.org/archive/individual/app/mkfontdir-1.0.7.tar.gz`
- SHA256: `bccc5fb7af1b614eabe4a22766758c87bfc36d66191d08c19d2fa97674b7b5b7`

The concrete focused node has:

```text
build: compiler-wrapper, gcc, gmake, pkgconf, util-macros
link: gcc-runtime, glibc
run: mkfontscale
```

The reference prefix installed by hermetic Spack:

```text
/vaso/cache/spack/opt/spack/linux-icelake/mkfontdir-1.0.7-qw76ci3avoltwiap4pvazurwjrci5ekd
```

The prefix-relevant installed surface is:

```text
bin/mkfontdir
share/man/man1/mkfontdir.1
```

The reference `bin/mkfontdir` is a shell wrapper:

```bash
PATH="/vaso/cache/spack/opt/spack/linux-icelake/mkfontdir-1.0.7-qw76ci3avoltwiap4pvazurwjrci5ekd/bin:$PATH"
exec mkfontscale -b -s -l "$@"
```

The wrapper depends on `mkfontscale` being present on `PATH` through the Spack
runtime dependency, not through files inside the `mkfontdir` prefix. The parity
gate therefore supplies hermetic Spack `mkfontscale` on the reference side and
native `@mkfontscale_native//:prefix` on the candidate side.

Reference file hashes:

```text
b3ec31216a9430590318992af433bde7b2f16980f142d45aac133faca48fffb7  bin/mkfontdir
c635580fe4110625b299b1c953942dcede07dfb7693f2b6e20baf5fde5328b2b  share/man/man1/mkfontdir.1
```

## Native Build Recipe

`native/mkfontdir/mkfontdir.bzl` mirrors the concrete Spack Autotools flow:

```text
download mkfontdir-1.0.7.tar.gz
validate MKFONTSCALE_PREFIX, PKGCONF_PREFIX, and UTIL_MACROS_PREFIX
export PATH=<pkgconf-prefix>/bin:<mkfontscale-prefix>/bin:$PATH
export PKG_CONFIG=<pkgconf-prefix>/bin/pkgconf
export PKG_CONFIG_PATH=<pkgconf>/lib/pkgconfig:<util-macros>/share/pkgconfig
export ACLOCAL_PATH=<util-macros>/share/aclocal:<pkgconf>/share/aclocal
./configure --prefix=<prefix>
make V=1
make install
remove libtool archives
emit prefix_path.txt
```

The native provider exposes:

- `@mkfontdir_native//:prefix` for the installed prefix filegroup;
- `@mkfontdir_native//:prefix_path.txt` for downstream native repository
  rules;
- `@mkfontdir_native//:lib` as an empty `cc_library`, because mkfontdir is an
  executable/tool-prefix node with no public C ABI.

The corresponding mechanism verifier is the Autotools dependency-prefix case:

```text
native/mkfontdir/mkfontdir.bzl: autotools: MKFONTSCALE_PREFIX, PKGCONF_PREFIX, UTIL_MACROS_PREFIX
```

The rule refuses to evaluate unless `VASO_IN_INSULA=1`, consumes each
dependency through a mandatory Bazel `*_prefix_file`, validates all three
prefixes before configure, pins `PKG_CONFIG`, and threads dependency lookup
through Autotools-specific `PATH`, `PKG_CONFIG_PATH`, and `ACLOCAL_PATH`
channels.

## Gates

The smoke target is:

```text
//synthetic:use_mkfontdir_native
```

It runs the installed native wrapper from `@mkfontdir_native//:prefix`, supplies
native `mkfontscale` on `PATH`, checks the manpage, verifies `mkfontdir -v`,
creates an empty `fonts.dir`, and prints:

```text
mkfontdir:1.0.7:ok
```

`//synthetic:mkfontdir_prefix_parity` compares the native prefix against the
hermetic Spack reference. It covers:

- the two-path installed layout for `bin/mkfontdir` and
  `share/man/man1/mkfontdir.1`;
- prefix-normalized `bin/mkfontdir` wrapper content;
- exact manpage hash;
- empty shared-library ABI axis, matching the executable-only prefix;
- matching `mkfontdir -v` output with hermetic/native `mkfontscale` on `PATH`;
- matching empty-directory `fonts.dir` output, with SHA256
  `9a271f2a916b0b6ee6cecb2426f0b3206ef074578be55d9bc94f6f3fe3ab86aa`.

Current status: native mkfontdir is gated inside the hermetic CUDA insula. The
focused native verification command used a temporary one-package override file
containing `{"native":{"mkfontdir":"@mkfontdir_native//:lib"}}` so the run
exercised this new provider without sweeping unrelated native parity gates:

```bash
env PATH=/usr/bin:/bin:$HOME/.local/bin \
BAZEL_BIN=$HOME/.local/bin/bazel-9.2.0 \
VASO_ESTATE_ROOT=$VASO_ESTATE_ROOT \
SPACK_ROOT_PKG='mkfontdir@1.0.7' \
VASO_LOCK_OUT=/workspace/experiment/mkfontdir_native_spack_graph.lock.json \
VASO_BUILD_GRAPH_OUT=/workspace/experiment/mkfontdir_native_build_graph.json \
VASO_NATIVE=1 \
VASO_NATIVE_OVERRIDES=.tmp_mkfontdir_native_overrides.json \
VASO_SKIP_CONSUMER_TESTS=1 \
VASO_FORCE_FETCH_REPOS='@mkfontdir_native' \
VASO_EXTRA_TEST_TARGETS='//synthetic:use_mkfontdir_native //tools:hermetic_native_deps_guard_test //tools:native_build_mechanism_guard_unit_test //tools:abi_parity_unit_test' \
VASO_SPACK_TIMEOUT=1800 \
./run.sh
```

That run used Bazel's `@spack_dist//:spack` inside the isolated CUDA 12.9.1
insula, reported hermetic Spack version `1.2.2`, passed
`//tools:hermetic_spack_guard_test`, passed
`//tools:hermetic_native_deps_guard_test`, passed
`//tools:native_build_mechanism_guard_unit_test`, passed
`//tools:abi_parity_unit_test`, passed `//synthetic:use_mkfontdir_native`, and
passed `//synthetic:mkfontdir_prefix_parity`. The parity gate passed with
`SPACK_MKFONTDIR_PREFIX` and `SPACK_MKFONTSCALE_PREFIX` injected by `run.sh`.
