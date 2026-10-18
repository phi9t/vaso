# py-tqdm native recipe

## Position in the hillclimb

`py-tqdm@4.67.3` is the next pure `PythonPackage` source build after native
`py-sympy` in the lean `py-torch` frontier:

```text
118  py-sympy  1.14.0  python_pip  native
119  py-tqdm   4.67.3  python_pip
```

The focused reference graph for `SPACK_ROOT_PKG='py-tqdm@4.67.3~notebook~telegram
^python@3.13.13 ...'` has 57 build-graph nodes and 52 Spack lock packages.
Spack still owns the DAG shape; the native flip changes only
`spack_py_tqdm.build` to `native` and re-exports `@py_tqdm_native//:lib`.
The generated link/runtime edges remain Spack-derived.

## Spack evidence

All evidence here comes from Bazel's vendored `@spack_dist//:spack` release
running inside the CUDA insula. Do not use an ambient host Spack checkout.

Reference prefix from the focused hermetic run:

```text
/vaso/cache/spack/opt/spack/linux-icelake/py-tqdm-4.67.3-nzxuzsni4qbmh4qblfrlaofs7tqpmqbr
```

Hermetic recipe path observed from the installed prefix:

```text
/vaso/cache/spack/opt/spack/linux-icelake/py-tqdm-4.67.3-nzxuzsni4qbmh4qblfrlaofs7tqpmqbr/.spack/repos/spack_repo/builtin/packages/py_tqdm/package.py
```

Recipe facts from hermetic Spack `v1.2.2`:

- package class: `PyTqdm(PythonPackage)`
- selected build system: `python_pip`
- version: `4.67.3`
- source payload: PyPI source archive
  `https://files.pythonhosted.org/packages/source/t/tqdm/tqdm-4.67.3.tar.gz`
- source SHA256:
  `7d825f03f89244ef73f1d4ce193cb1774a8179fd96f31d7e1dcde62092b960bb`
- variants: `~notebook~telegram`
- dependencies: `python@3.7:` build/run, `py-setuptools@61:` build/run,
  `py-setuptools-scm@3.4:+toml` build, `py-wheel` build; the concrete hermetic
  build also supplies `py-pip`, `py-packaging`, `python-venv`, and `git`
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
  --prefix=<py-tqdm-prefix> \
  .
```

Pip builds an intermediate wheel from the source tree, then installs that wheel
into the prefix. The generated wheel hash and temporary staging paths are not
part of the prefix contract.

## Native build recipe

`native/py_tqdm/py_tqdm.bzl` mirrors that install method directly:

```text
download and extract the exact tqdm-4.67.3 source archive by SHA256
read @git_native//:prefix_path.txt
read @python_venv_native//:prefix_path.txt
read @py_packaging_native//:prefix_path.txt
read @py_pip_native//:prefix_path.txt
read @py_setuptools_native//:prefix_path.txt
read @py_setuptools_scm_native//:prefix_path.txt
read @py_wheel_native//:prefix_path.txt
derive PYTHON_ABI from @python_venv_native
validate native git, python-venv, packaging, pip, setuptools, setuptools-scm, and wheel prefixes
clear PYTHONHOME
set PATH from native git, pip, wheel, python-venv, and rootfs tool paths
set PYTHONPATH from native setuptools-scm, setuptools, pip, wheel, packaging, and python-venv under lib/python${PYTHON_ABI}
PYTHON_VENV_PREFIX/bin/python${PYTHON_ABI} -m pip -vvv install \
  --no-deps --ignore-installed --no-build-isolation \
  --no-warn-script-location --no-index --prefix=<native-prefix> .
validate tqdm executable, selected package modules, stable dist-info metadata, and license under lib/python${PYTHON_ABI}
emit prefix_path.txt
```

The rule refuses to evaluate unless `VASO_IN_INSULA=1`, so the wheel build and
prefix install happen only inside the sealed CUDA rootfs. It never searches for
host Spack; all Spack facts come from the Bazel-vendored Spack run, and all
Python packaging tools come from Bazel-native prefixes.

The mechanism verifier reports the expected build channel:

```text
native/py_tqdm/py_tqdm.bzl: python-pip-install: GIT_PREFIX, PYTHON_VENV_PREFIX, PY_PACKAGING_PREFIX, PY_PIP_PREFIX, PY_SETUPTOOLS_PREFIX, PY_SETUPTOOLS_SCM_PREFIX, PY_WHEEL_PREFIX
```

## Prefix and behavior contract

The stable prefix surface is:

- `bin/tqdm`
- package modules under `lib/python3.13/site-packages/tqdm`
- metadata and license under `lib/python3.13/site-packages/tqdm-4.67.3.dist-info`

Generated installation metadata that embeds the temporary build path or complete
wheel file list, such as `direct_url.json`, `RECORD`, and bytecode, is not part
of the stable parity surface.

## ABI and behavior gate

The smoke target is:

```text
//synthetic:use_py_tqdm_native
```

It resolves the native `py-tqdm`, native Python packaging prefixes, and native
Python venv, imports tqdm through the native Python venv, verifies the installed
metadata version, iterates a deterministic range through `tqdm(...,
disable=True)`, and expects:

```text
py-tqdm:4.67.3:3:3
```

The parity target is:

```text
//synthetic:py_tqdm_prefix_parity
```

It compares `@py_tqdm_native//:prefix` against the hermetic Spack reference
prefix and covers:

- selected layout parity for the CLI wrapper, package modules, `.dist-info`
  metadata, and license;
- byte-identical representative package files and metadata, with prefix
  normalization for the generated `bin/tqdm` wrapper;
- empty ELF ABI axis, since the package installs no native shared libraries.

Current status: native `py-tqdm` is gated inside the hermetic CUDA insula. This
command uses Bazel 9.2.0, estate root
`$VASO_ESTATE_ROOT`, and the focused root
`SPACK_ROOT_PKG='py-tqdm@4.67.3~notebook~telegram ...'`:

```bash
env PATH=/usr/bin:/bin:$HOME/.local/bin \
  BAZEL_BIN=$HOME/.local/bin/bazel-9.2.0 \
  VASO_ESTATE_ROOT=$VASO_ESTATE_ROOT \
  TMPDIR=$VASO_ESTATE_ROOT/agents/trae/tmp \
  SPACK_ROOT_PKG='py-tqdm@4.67.3~notebook~telegram ^python@3.13.13+bz2+ctypes+dbm~debug+libxml2+lzma~optimizations+pic+pyexpat+pythoncmd+readline+shared+sqlite3+ssl~static~tkinter+uuid+zlib ^python-venv@1.0 ^py-pip@26.1.2 ^py-setuptools@79.0.1 ^py-wheel@0.45.1 ^py-setuptools-scm@8.2.1' \
  VASO_NATIVE=1 \
  VASO_SPACK_FRESH=1 \
  VASO_SPACK_TIMEOUT=900 \
  VASO_FORMAL=0 \
  VASO_SKIP_CONSUMER_TESTS=1 \
  VASO_SKIP_NATIVE_ABI_GATES=1 \
  VASO_EXTRA_TEST_TARGETS='//synthetic:use_py_tqdm_native,//synthetic:py_tqdm_prefix_parity,//tools:hermetic_native_deps_guard_test,//tools:native_dep_wiring_live_test' \
  ./run.sh
```

It ran entirely inside the CUDA bundle rootfs with Bazel-owned Spack `1.2.2`,
regenerated a focused lock with root `spack_py_tqdm`, 52 Spack packages, and a
57-node build graph tail `python@3.13.13`, `python-venv@1.0`,
`py-pip@26.1.2`, `py-setuptools@79.0.1`, `py-wheel@0.45.1`,
`py-flit-core@3.12.0`, `py-packaging@26.2`, `py-setuptools-scm@8.2.1`, and
`py-tqdm@4.67.3`. The run passed `//synthetic:use_py_tqdm_native` with output
`py-tqdm:4.67.3:3:3`, passed `//synthetic:py_tqdm_prefix_parity`, passed
`//tools:hermetic_native_deps_guard_test`, and passed
`//tools:native_dep_wiring_live_test` with 0 allowlisted mismatches. Full proof
log:
`$VASO_ESTATE_ROOT/agents/trae/logs/py-tqdm-insula-proof-final-20260930T011305Z.log`.

The parity verdict compared the hermetic Spack reference prefix

```text
/vaso/cache/spack/opt/spack/linux-icelake/py-tqdm-4.67.3-nzxuzsni4qbmh4qblfrlaofs7tqpmqbr
```

against the native Bazel prefix

```text
/vaso/cache/bazel/output-base/external/+py_tqdm_native+py_tqdm_native/prefix
```

and reported `ok: true`, layout count 20/20, no missing or extra candidate
paths, byte-identical SHA256 values for all selected stable files after
prefix-normalizing `bin/tqdm`, and an empty successful ELF ABI axis.

## ODR-sensitive provider invariant

This slice does not add protobuf, gRPC, Abseil, or Boost providers. The graph
generator rejects package-wide native overrides and mixed concrete versions for
those ODR-sensitive families. Any future native capture in those families must
remain exact-version-qualified and family-version-consistent before it can enter
the lock.
