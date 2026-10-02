# ninja native recipe

## Position in the hillclimb

`ninja@1.13.2` is the next generic source build after native `re2c` in the
lean `py-torch` frontier:

```text
130  re2c   4.4     autotools  native
131  ninja  1.13.2  generic
```

The focused reference graph for `SPACK_ROOT_PKG='ninja'` has 33 build-graph
nodes and uses 22 `autotools`, 9 `generic`, and 2 `makefile` build-system
nodes. Spack still owns the DAG shape; the native flip changes only
`spack_ninja.build` to `native` and re-exports `@ninja_native//:lib`.

## Spack evidence

All evidence here comes from Bazel's vendored `@spack_dist//:spack` release
running inside the CUDA insula. Do not use an ambient host Spack checkout.
The vendored distribution is upstream Spack `v1.2.2`, which was also checked
against GitHub's latest release metadata while this recipe was captured.

Reference prefix from the focused hermetic run:

```text
/vaso/cache/spack/opt/spack/linux-icelake/ninja-1.13.2-flcjtbiowc67q2iazyyt4vepehiokiga
```

Hermetic recipe path observed from the installed prefix:

```text
/vaso/cache/spack/opt/spack/linux-icelake/ninja-1.13.2-flcjtbiowc67q2iazyyt4vepehiokiga/.spack/repos/spack_repo/builtin/packages/ninja/package.py
```

Recipe facts from hermetic Spack `v1.2.2`:

- package class: `Ninja(Package)`, using Spack's generic package interface
- version: `1.13.2`
- source archive:
  `https://github.com/ninja-build/ninja/archive/v1.13.2.tar.gz`
- source SHA256:
  `974d6b2f4eeefa25625d34da3cb36bdcebe7fbce40f4c16ac0835fd1c0cbae17`
- selected variant: `+re2c`
- concrete package dependencies: Python as a build dependency and
  `re2c@0.11.3:` as a build dependency
- concrete toolchain dependencies: compiler-wrapper, gcc, gcc-runtime, and glibc
- resource: googletest `release-1.12.1` is staged for `@1.12:`, but the
  default non-test build does not pass `--gtest-source-dir`
- patch surface: the `@1.12` googletest patch is not applied for `1.13.2`

The concrete Spack build log shows the package phases:

```text
python3 configure.py --bootstrap
[1/38] RE2C src/depfile_parser.cc
[2/38] RE2C src/lexer.cc
...
[38/38] LINK ninja
Installing ninja to <prefix>/bin
Installing misc to <prefix>/misc
```

The installed stable prefix surface is:

- executables: `bin/ninja` and symlink `bin/ninja-build -> ninja`
- support data: upstream `misc/`, including `ninja_syntax.py`,
  shell completions, fuzzing inputs, Python tests, packaging helpers, and
  example `*.ninja` files
- public library ABI: none

## Native build recipe

`native/ninja/ninja.bzl` mirrors the generic package install:

```text
download and extract the exact ninja-1.13.2 source archive by SHA256
read @python_313_native//:prefix_path.txt
read @re2c_native//:prefix_path.txt
validate PYTHON_PREFIX/bin/python3
validate RE2C_PREFIX/bin/re2c
set PATH=<python-prefix>/bin:<re2c-prefix>/bin:$PATH
set CFLAGS/CXXFLAGS=-march=icelake-client -mtune=icelake-client
run "$PYTHON_PREFIX/bin/python3" configure.py --bootstrap
install ninja to prefix/bin/ninja
create prefix/bin/ninja-build -> ninja
copy misc/ to prefix/misc
emit prefix_path.txt
```

The rule refuses to evaluate unless `VASO_IN_INSULA=1`, so configure, build,
install, and all dependency discovery run only inside the sealed CUDA rootfs.
It never searches for host Spack; all Spack facts come from the Bazel-vendored
Spack run, and Python plus re2c are supplied by Bazel-native prefixes.

The mechanism verifier is expected to report this build channel:

```text
native/ninja/ninja.bzl: python-bootstrap-tool: PYTHON_PREFIX, RE2C_PREFIX
```

`python-bootstrap-tool` is the mechanism-specific guard for generic packages
whose build entrypoint is a Python bootstrap script. It requires explicit
`*_prefix_file` attrs, shell-side prefix checks, a pinned
`$PYTHON_PREFIX/bin/python* configure.py --bootstrap` invocation, and PATH
exposure for build-tool prefixes such as re2c.

## Prefix and behavior gate

The smoke target is:

```text
//synthetic:use_ninja_native
```

It reads `@ninja_native//:prefix_path.txt`, checks `ninja --version`, checks
the installed `ninja-build` symlink and representative `misc/` files, then runs
a tiny `build.ninja` that copies an input file. The prefix path file is used so
the symlink check observes the native install tree, not Bazel's runfiles
wrappers.

The parity target is:

```text
//synthetic:ninja_prefix_parity
```

It compares `@ninja_native//:prefix` against the hermetic Spack reference prefix
and covers:

- executable layout and dynamic dependency parity for `bin/ninja` and
  `bin/ninja-build`;
- byte-identical selected `misc/` files;
- matching `ninja --version` behavior;
- matching behavior for a deterministic one-step build.

## ODR-sensitive provider invariant

This slice does not add protobuf, gRPC, Abseil, or Boost providers. Those
families must stay on one unified compatible concrete version family across
all companion packages before any native flip. The generator rejects
unqualified overrides and rejects mixed concrete family versions, including
protobuf/Python protobuf and gRPC/gRPC C++ pairings.
