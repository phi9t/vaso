# re2c native recipe

## Position in the hillclimb

`re2c@4.4` is the next Autotools source build after native `py-versioneer` in
the lean `py-torch` frontier:

```text
129  py-versioneer 0.29  python_pip  native
130  re2c          4.4   autotools
```

The focused reference graph for `SPACK_ROOT_PKG='re2c'` has 32 build-graph
nodes and uses 22 `autotools`, 8 `generic`, and 2 `makefile` build-system
nodes. Spack still owns the DAG shape; the native flip changes only
`spack_re2c.build` to `native` and re-exports `@re2c_native//:lib`.

## Spack evidence

All evidence here comes from Bazel's vendored `@spack_dist//:spack` release
running inside the CUDA insula. Do not use an ambient host Spack checkout.

Reference prefix from the focused hermetic run:

```text
/vaso/cache/spack/opt/spack/linux-icelake/re2c-4.4-fkzs7nzjbdmkl5hk4hwowlpbzoj7hhxy
```

Hermetic recipe path observed from the installed prefix:

```text
/vaso/cache/spack/opt/spack/linux-icelake/re2c-4.4-fkzs7nzjbdmkl5hk4hwowlpbzoj7hhxy/.spack/repos/spack_repo/builtin/packages/re2c/package.py
```

Recipe facts from hermetic Spack `v1.2.2`:

- package class: `Re2c(AutotoolsPackage, CMakePackage)`
- selected build system: `autotools`
- version: `4.4`
- source archive:
  `https://github.com/skvadrik/re2c/releases/download/4.4/re2c-4.4.tar.xz`
- source SHA256:
  `6b6b865924447ef992d5db4e52fb9307e5f65f26edd43efa91395da810f4280a`
- license: `Public-Domain`
- concrete package dependency: Python `@3.7:` as a build dependency
- concrete toolchain dependencies: compiler-wrapper, gcc, gcc-runtime, glibc,
  and gmake
- removed libtool archive: `lib/libre2c.la`

Spack's configured Autotools command is:

```text
../configure \
  --prefix=<re2c-prefix> \
  --disable-benchmarks \
  --disable-debug \
  --disable-dependency-tracking \
  --disable-docs \
  --disable-lexers \
  --enable-libs \
  --enable-golang
make V=1
make install
```

The installed stable prefix surface is:

- executables: `bin/re2c`, `bin/re2d`, `bin/re2go`, `bin/re2hs`, `bin/re2js`,
  `bin/re2ocaml`, `bin/re2py`, `bin/re2rust`, `bin/re2swift`, `bin/re2v`, and
  `bin/re2zig`
- libraries: `lib/libre2c.so.0.0.0`, symlinks `lib/libre2c.so.0` and
  `lib/libre2c.so`, and `lib/libre2c.a`
- manpages: one `share/man/man1/re2*.1` page per executable
- data: `share/re2c/stdlib/{c,d,go,haskell,java,js,ocaml,python,rust,swift,v,zig}`
  and `share/re2c/stdlib/unicode_categories.re`

## Native build recipe

`native/re2c/re2c.bzl` mirrors the Autotools install:

```text
download and extract the exact re2c-4.4 source archive by SHA256
read @python_313_native//:prefix_path.txt
validate PYTHON_PREFIX/bin/python3
set PATH=<python-prefix>/bin:$PATH
set CFLAGS/CXXFLAGS=-march=icelake-client -mtune=icelake-client
configure from an out-of-tree spack-build directory with Spack's args
make V=1
make install
delete libtool archives from the emitted prefix
emit prefix_path.txt
```

The rule refuses to evaluate unless `VASO_IN_INSULA=1`, so configure, build,
install, and all dependency discovery run only inside the sealed CUDA rootfs.
It never searches for host Spack; all Spack facts come from the Bazel-vendored
Spack run, and Python is supplied by the Bazel-native prefix.

The mechanism verifier is expected to report this build channel:

```text
native/re2c/re2c.bzl: autotools: PYTHON_PREFIX
```

## ABI and behavior gate

The smoke target is:

```text
//synthetic:use_re2c_native
```

It resolves `@re2c_native//:prefix`, checks `re2c --version`, generates
deterministic C from a minimal `.re` input with `--no-generation-date`, and
verifies representative library and stdlib files.

The parity target is:

```text
//synthetic:re2c_abi_parity
```

It compares `@re2c_native//:prefix` against the hermetic Spack reference prefix
and covers:

- executable layout and dynamic dependency parity for every `bin/re2*` wrapper;
- `libre2c` SONAME/exported-symbol parity and static archive member/symbol
  parity;
- byte-identical selected manpages and stdlib data;
- matching `re2c --version` behavior;
- matching deterministic generated C output for a minimal lexer input.

## ODR-sensitive provider invariant

This slice does not add protobuf, gRPC, Abseil, or Boost providers. Those
families must stay on one unified compatible concrete version family across
all companion packages before any native flip. The generator rejects
unqualified overrides and rejects mixed concrete family versions, including
protobuf/Python protobuf and gRPC/gRPC C++ pairings.
