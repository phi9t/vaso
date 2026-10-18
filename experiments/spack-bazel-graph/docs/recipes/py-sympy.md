# py-sympy native recipe

## Position in the hillclimb

`py-sympy@1.14.0` is the pure `PythonPackage` source build after native
`py-mpmath` and `py-six` in the lean `py-torch` frontier. The protobuf
compatibility island is the preceding frontier slice, not a py-sympy
dependency:

```text
100  py-six       1.17.0   python_pip  native
101  py-protobuf  4.21.12  python_pip  native
102  py-sympy     1.14.0   python_pip
```

The focused reference graph for `SPACK_ROOT_PKG='py-sympy'` has 37 nodes.
Spack still owns the DAG shape; the native flip changes only
`spack_py_sympy.build` to `native` and re-exports `@py_sympy_native//:lib`.
The generated link/runtime edges remain Spack-derived.

## Spack evidence

All evidence here comes from Bazel's vendored `@spack_dist//:spack` release
running inside the CUDA insula. Do not use an ambient host Spack checkout.

Reference prefix from the focused hermetic run:

```text
/vaso/cache/spack/opt/spack/linux-icelake/py-sympy-1.14.0-u3jyxisxvpxzg3cfqwob3puoyv62zqd4
```

Hermetic recipe path observed from the installed prefix:

```text
/vaso/cache/spack/opt/spack/linux-icelake/py-sympy-1.14.0-u3jyxisxvpxzg3cfqwob3puoyv62zqd4/.spack/repos/spack_repo/builtin/packages/py_sympy/package.py
```

Recipe facts from hermetic Spack `v1.2.2`:

- package class: `PySympy(PythonPackage)`
- selected build system: `python_pip`
- version: `1.14.0`
- source payload: PyPI source archive
  `https://files.pythonhosted.org/packages/source/s/sympy/sympy-1.14.0.tar.gz`
- source SHA256:
  `d3d3fe8df1e5a0b42f0e7bdf50541697dbe7d23746e894990c030e2b05e72517`
- dependencies: `python@3.9:` build/run for `@1.14:`;
  `py-mpmath@1.1.0:1.3` build/run for `@1.13.0:`; plus concrete build
  inputs `py-pip`, `py-setuptools`, and `py-wheel`
- patches: none

Spack's install path is the standard PythonPackage pip invocation:

```text
<python-venv-prefix>/bin/python3 -m pip \
  -vvv \
  --no-input \
  --no-cache-dir \
  --disable-pip-version-check \
  install \
  --no-deps \
  --ignore-installed \
  --no-build-isolation \
  --no-warn-script-location \
  --no-index \
  --prefix=<py-sympy-prefix> \
  .
```

Pip builds an intermediate wheel from the source tree, then installs that wheel
into the prefix. The generated wheel hash and temporary staging paths are not
part of the prefix contract.

## Native build recipe

`native/py_sympy/py_sympy.bzl` mirrors that install method directly:

```text
download and extract the exact sympy-1.14.0 source archive by SHA256
read @python_venv_native//:prefix_path.txt
read @py_mpmath_native//:prefix_path.txt
read @py_pip_native//:prefix_path.txt
read @py_setuptools_native//:prefix_path.txt
read @py_wheel_native//:prefix_path.txt
validate PYTHON_VENV_PREFIX/bin/python3
validate native mpmath, pip, setuptools, and wheel site-package payloads
clear PYTHONHOME
set PYTHONPATH from native py-mpmath, py-pip, py-setuptools, py-wheel, and python-venv
PYTHON_VENV_PREFIX/bin/python3 -m pip -vvv install \
  --no-deps --ignore-installed --no-build-isolation \
  --no-warn-script-location --no-index --prefix=<native-prefix> .
validate isympy, selected sympy modules, stable dist-info metadata, and license
emit prefix_path.txt
```

The rule refuses to evaluate unless `VASO_IN_INSULA=1`, so the wheel build and
prefix install happen only inside the sealed CUDA rootfs. It never searches
`PATH` for Python or pip; Python comes from the Bazel-native venv prefix and
Python packaging tools come from Bazel-native prefixes on `PYTHONPATH`.

The mechanism verifier reports the expected build channel:

```text
native/py_sympy/py_sympy.bzl: python-pip-install: PYTHON_VENV_PREFIX, PY_MPMATH_PREFIX, PY_PIP_PREFIX, PY_SETUPTOOLS_PREFIX, PY_WHEEL_PREFIX
```

## Prefix and behavior contract

The stable prefix surface is:

- `bin/isympy`
- `share/man/man1/isympy.1`
- package modules under `lib/python3.13/site-packages/sympy`
- import helper `lib/python3.13/site-packages/isympy.py`
- metadata and license under `lib/python3.13/site-packages/sympy-1.14.0.dist-info`

Generated installation metadata that embeds the temporary build path or complete
wheel file list, such as `direct_url.json`, `RECORD`, and bytecode, is not part
of the stable parity surface.

## ABI and behavior gate

The smoke target is:

```text
//synthetic:use_py_sympy_native
```

It resolves the native `py-sympy`, native Python packaging prefixes, and native
`py-mpmath`, imports SymPy through the native Python venv, verifies the installed
metadata version, and expects:

```text
py-sympy:1.14.0:pi/2:(x - 1)*(x + 1)*(x**2 + 1)
```

The parity target is:

```text
//synthetic:py_sympy_prefix_parity
```

It compares `@py_sympy_native//:prefix` against the hermetic Spack reference
prefix and covers:

- selected layout parity for stable modules, `isympy`, manpage, `.dist-info`
  metadata, and license;
- byte-identical representative package files and metadata, with prefix
  normalization for the generated `bin/isympy` wrapper;
- empty ELF ABI axis, since the package installs no native shared libraries.

Current status: native `py-sympy` is gated inside the hermetic CUDA insula. The
final proof used Bazel 9.2.0, estate root
`$VASO_ESTATE_ROOT`, and this focused root:

```bash
env PATH=/usr/bin:/bin:$HOME/.local/bin \
  BAZEL_BIN=$HOME/.local/bin/bazel-9.2.0 \
  VASO_ESTATE_ROOT=$VASO_ESTATE_ROOT \
  TMPDIR=$VASO_ESTATE_ROOT/agents/trae/tmp \
  SPACK_ROOT_PKG='py-sympy@1.14.0 ^python@3.13.13+bz2+ctypes+dbm~debug+libxml2+lzma~optimizations+pic+pyexpat+pythoncmd+readline+shared+sqlite3+ssl~static~tkinter+uuid+zlib ^python-venv@1.0 ^py-pip@26.1.2 ^py-setuptools@79.0.1 ^py-wheel@0.45.1 ^py-mpmath@1.3.0' \
  VASO_NATIVE=1 \
  VASO_SPACK_FRESH=1 \
  VASO_SPACK_TIMEOUT=900 \
  VASO_FORMAL=0 \
  VASO_SKIP_CONSUMER_TESTS=1 \
  VASO_SKIP_NATIVE_ABI_GATES=1 \
  VASO_EXTRA_TEST_TARGETS='//synthetic:use_py_sympy_native,//synthetic:py_sympy_prefix_parity,//tools:hermetic_native_deps_guard_test,//tools:native_dep_wiring_live_test' \
  ./run.sh
```

It ran entirely inside the CUDA bundle rootfs with Bazel-owned Spack 1.2.2,
regenerated a 32-package focused lock with root `spack_py_sympy` and a 37-node
build graph, passed the phase 2a hermetic Spack/tooling tests, passed
`//synthetic:use_py_sympy_native` with output
`py-sympy:1.14.0:pi/2:(x - 1)*(x + 1)*(x**2 + 1)`, passed
`//synthetic:py_sympy_prefix_parity`, and passed
`//tools:native_dep_wiring_live_test` with 0 allowlisted mismatches.

After the first full proof populated `/vaso/cache/bazel/output-base`, the
runfiles-data fix was checked with a focused insula rerun of only
`//synthetic:use_py_sympy_native`, `//synthetic:py_sympy_prefix_parity`,
`//tools:hermetic_native_deps_guard_test`, and
`//tools:native_dep_wiring_live_test`, without `fetch --configure --force`.

Proof logs:

```text
$VASO_ESTATE_ROOT/agents/trae/logs/py-sympy-focused-insula-20260930T005057Z.log
$VASO_ESTATE_ROOT/agents/trae/logs/py-sympy-insula-proof-final-20260930T005212Z.log
```

The parity verdict compared the hermetic Spack reference prefix

```text
/vaso/cache/spack/opt/spack/linux-icelake/py-sympy-1.14.0-u3jyxisxvpxzg3cfqwob3puoyv62zqd4
```

against the native Bazel prefix

```text
/vaso/cache/bazel/output-base/external/+py_sympy_native+py_sympy_native/prefix
```

and reported `ok: true`, layout count 19/19, byte-identical SHA256 values for
all selected stable files after prefix-normalizing `bin/isympy`, and an empty
successful ELF ABI axis.

## ODR-sensitive provider invariant

This slice does not add protobuf, gRPC, Abseil, or Boost providers. The graph
generator rejects package-wide native overrides and mixed concrete versions for
those ODR-sensitive families. Any future native capture in those families must
remain exact-version-qualified and family-version-consistent before it can enter
the lock.
