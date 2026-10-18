# PRRTE frontier recipe

## Position in the hillclimb

`prrte@4.1.0` is the next non-toolchain node after native PMIx in the captured
PyTorch graph:

```text
87  prrte  4.1.0  autotools
```

The focused reference graph for `SPACK_ROOT_PKG='prrte@4.1.0'` ends with:

```text
39  prrte  4.1.0  autotools
```

Spack still owns the DAG shape. The native flip changes only
`spack_prrte.build` to `native` and re-exports `@prrte_native//:lib`; link
edges to `hwloc`, `libevent`, and `pmix` remain Spack-derived.

## Spack evidence

All recipe evidence comes from Bazel's vendored `@spack_dist//:spack` running
inside the CUDA insula. Do not use an ambient host Spack checkout.

Reference prefix from the focused hermetic run:

```text
/vaso/cache/spack/opt/spack/linux-icelake/prrte-4.1.0-iegqipqs5knq5n2lxeqm5nq4nagbdniv
```

Hermetic recipe path:

```text
/vaso/cache/spack/user/package_repos/fncqgg4/repos/spack_repo/builtin/packages/prrte/package.py
```

Source provenance from that recipe:

- package class: `Prrte(AutotoolsPackage)`
- upstream source URL used by the native rule:
  `https://github.com/pmix/prrte/releases/download/v4.1.0/prrte-4.1.0.tar.bz2`
- version `4.1.0` SHA256:
  `285ad62b670075708b9fcfe14c54baa599733bc274d10502a82e8eebba0b7c70`
- concrete variants: `schedulers=none`
- build dependencies: `autoconf`, `automake`, `flex`, `libtool`, `m4`, `perl`,
  `pkgconf`
- build/link dependencies: `hwloc`, `libevent`, `pmix`

Spack applies two upstream patches for `@4.1.0`:

- `https://github.com/openpmix/prrte/commit/378c61c1d8eff9858a7774c869fbd332c48711a8.patch?full_index=1`
  with SHA256 `64faa1acb89eddea096307a2658b11ccdaf85dc8c870fed4b3f8670329706a4f`
- `https://github.com/openpmix/prrte/commit/a6b09c9c3fb84838b056c31e802b5f79ac4e8d6b.patch?full_index=1`
  with SHA256 `91b28f5c701c8543b4807a29e9b5154b9d7e62f5d3a1e3036dadf1a9b5b0ca65`

The Spack recipe forces autoreconf:

```sh
perl autogen.pl
```

The Spack configure argument contract is:

```text
--enable-shared
--enable-static
--disable-sphinx
--with-libevent=<libevent-prefix>
--with-hwloc=<hwloc-prefix>
--with-pmix=<pmix-prefix>
```

## Native build

`native/prrte/prrte.bzl` defines `prrte_native`, a Bazel repository rule that
declares `VASO_IN_INSULA` as an environment input and refuses to build unless
the hermetic insula sets `VASO_IN_INSULA=1`.

The rule consumes only Bazel-native dependency prefixes:

```text
AUTOCONF_PREFIX  <- @autoconf_native//:prefix_path.txt
AUTOMAKE_PREFIX  <- @automake_native//:prefix_path.txt
FLEX_PREFIX      <- @flex_native//:prefix_path.txt
HWLOC_PREFIX     <- @hwloc_native//:prefix_path.txt
LIBEVENT_PREFIX  <- @libevent_native//:prefix_path.txt
LIBTOOL_PREFIX   <- @libtool_native//:prefix_path.txt
M4_PREFIX        <- @m4_native//:prefix_path.txt
PERL_PREFIX      <- @perl_native//:prefix_path.txt
PKGCONF_PREFIX   <- @pkgconf_native//:prefix_path.txt
PMIX_PREFIX      <- @pmix_native//:prefix_path.txt
```

The build action downloads the same release tarball, downloads and applies the
same two patch URLs by SHA256, pins Autotools and pkg-config tools from native
prefixes, runs `perl autogen.pl`, then configures/makes/installs with the
Spack arguments above. It removes libtool archives after install to match the
Spack public prefix surface.

The mechanism verifier records the dependency contract as:

```text
native/prrte/prrte.bzl: autotools: AUTOCONF_PREFIX, AUTOMAKE_PREFIX, FLEX_PREFIX, HWLOC_PREFIX, LIBEVENT_PREFIX, LIBTOOL_PREFIX, M4_PREFIX, PERL_PREFIX, PKGCONF_PREFIX, PMIX_PREFIX
```

## Prefix and ABI gate

The ABI-relevant prefix contract used by the gate is:

- headers under `include/` and `include/prte/`
- libraries: `lib/libprrte.a`, `lib/libprrte.so`,
  `lib/libprrte.so.3`, `lib/libprrte.so.3.1.0`
- executables: `bin/prte`, `bin/prte-info`, `bin/prte-submit`,
  `bin/prte-term`, `bin/prted`, plus command aliases installed by upstream

`//synthetic:use_prrte` links through the Spack-generated `@spack_prrte//:lib`
facade and prints version macros, so the consumer remains unchanged when the
provider flips. `//synthetic:use_prrte_native` links directly against
`@prrte_native//:lib` as a native-prefix smoke test.

`//synthetic:prrte_abi_parity` compares `@prrte_native//:prefix` against the
hermetic Spack reference prefix for layout, SONAME/exported symbols,
downstream link-and-run, executable dependency parity, and
`prte-info --version` behavior.
