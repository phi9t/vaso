# py-trove-classifiers native recipe

## Position in the hillclimb

`py-trove-classifiers@2026.6.1.19` is the next pure `PythonPackage` source
build after native `py-tqdm` in the lean `py-torch` frontier:

```text
119  py-tqdm                4.67.3        python_pip  native
120  py-trove-classifiers   2026.6.1.19   python_pip
```

The focused reference graph for `SPACK_ROOT_PKG='py-trove-classifiers@2026.6.1.19
^python@3.13.13 ...'` has 37 build-graph nodes and 32 Spack lock packages.
Spack still owns the DAG shape; the native flip changes only
`spack_py_trove_classifiers.build` to `native` and re-exports
`@py_trove_classifiers_native//:lib`. Generated dependency edges remain
Spack-derived.

## Spack evidence

All evidence here comes from Bazel's vendored `@spack_dist//:spack` release
running inside the CUDA insula. Do not use an ambient host Spack checkout.

Reference prefix from the focused hermetic run:

```text
/vaso/cache/spack/opt/spack/linux-icelake/py-trove-classifiers-2026.6.1.19-phoxf56tikffuzhn6mtwnfitnd7qq7cp
```

Hermetic recipe path observed from the installed prefix:

```text
/vaso/cache/spack/opt/spack/linux-icelake/py-trove-classifiers-2026.6.1.19-phoxf56tikffuzhn6mtwnfitnd7qq7cp/.spack/repos/spack_repo/builtin/packages/py_trove_classifiers/package.py
```

Recipe facts from hermetic Spack `v1.2.2`:

- package class: `PyTroveClassifiers(PythonPackage)`
- selected build system: `python_pip`
- version: `2026.6.1.19`
- source payload: PyPI source archive
  `https://files.pythonhosted.org/packages/source/t/trove_classifiers/trove_classifiers-2026.6.1.19.tar.gz`
- source SHA256:
  `c5132b4b61a829d11cfbd2d72e97f20a45ed6edb95e45c5efdeb5e00836b2745`
- dependencies: `python` build/run, `py-setuptools` build, `py-calver` build;
  the concrete hermetic build also supplies `py-pip`, `py-wheel`, and
  `python-venv`
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
  --prefix=<py-trove-classifiers-prefix> \
  .
```

The source uses `setuptools.build_meta` with `use_calver`, so `py-calver` must
be present on `PYTHONPATH` during the build. Pip builds an intermediate wheel
from the source tree, then installs that wheel into the prefix. Generated
installation metadata that embeds temporary paths is not part of the stable
prefix contract.

## Native build recipe

`native/py_trove_classifiers/py_trove_classifiers.bzl` mirrors that install
method directly:

```text
download and extract the exact trove_classifiers-2026.6.1.19 source archive by SHA256
read @python_venv_native//:prefix_path.txt
read @py_calver_native//:prefix_path.txt
read @py_pip_native//:prefix_path.txt
read @py_setuptools_native//:prefix_path.txt
read @py_wheel_native//:prefix_path.txt
derive PYTHON_ABI from @python_venv_native
validate native python-venv, calver, pip, setuptools, and wheel prefixes under lib/python${PYTHON_ABI}
clear PYTHONHOME
set PATH from native pip, wheel, python-venv, and rootfs tool paths
set PYTHONPATH from native calver, pip, setuptools, wheel, and python-venv under lib/python${PYTHON_ABI}
PYTHON_VENV_PREFIX/bin/python3 -m pip -vvv install \
  --no-deps --ignore-installed --no-build-isolation \
  --no-warn-script-location --no-index --prefix=<native-prefix> .
validate trove-classifiers executable, package modules, typed marker, metadata, and license under lib/python${PYTHON_ABI}
emit prefix_path.txt
```

The rule refuses to evaluate unless `VASO_IN_INSULA=1`, so the wheel build and
prefix install happen only inside the sealed CUDA rootfs. It never searches for
host Spack; all Spack facts come from the Bazel-vendored Spack run, and all
Python packaging tools come from Bazel-native prefixes.

The mechanism verifier reports the expected build channel:

```text
native/py_trove_classifiers/py_trove_classifiers.bzl: python-pip-install: PYTHON_VENV_PREFIX, PY_CALVER_PREFIX, PY_PIP_PREFIX, PY_SETUPTOOLS_PREFIX, PY_WHEEL_PREFIX
```

## Prefix and behavior contract

The stable prefix surface is:

- `bin/trove-classifiers`
- package modules under `lib/python3.13/site-packages/trove_classifiers`
- metadata and license under
  `lib/python3.13/site-packages/trove_classifiers-2026.6.1.19.dist-info`

Generated installation metadata that embeds the temporary build path or complete
wheel file list, such as `direct_url.json`, `RECORD`, and bytecode, is not part
of the stable parity surface.

## ABI and behavior gate

The smoke target is:

```text
//synthetic:use_py_trove_classifiers_native
```

It resolves the native `py-trove-classifiers`, native Python packaging prefixes,
and native Python venv, imports `trove_classifiers` through the native Python
venv, verifies the installed metadata version, checks a CUDA classifier, and
expects:

```text
py-trove-classifiers:2026.6.1.19:895:True:Typing :: Typed
```

The parity target is:

```text
//synthetic:py_trove_classifiers_prefix_parity
```

It compares `@py_trove_classifiers_native//:prefix` against the hermetic Spack
reference prefix and covers:

- selected layout parity for the CLI wrapper, package modules, `.dist-info`
  metadata, and license;
- byte-identical representative package files and metadata, with prefix
  normalization for the generated `bin/trove-classifiers` wrapper;
- empty ELF ABI axis, since the package installs no native shared libraries.

Current status: native `py-trove-classifiers` is gated inside the hermetic CUDA
insula. This command uses Bazel 9.2.0, estate root
`$VASO_ESTATE_ROOT`, and the focused root
`SPACK_ROOT_PKG='py-trove-classifiers@2026.6.1.19 ^python@3.13.13 ...'`:

```bash
env PATH=/usr/bin:/bin:$HOME/.local/bin \
  BAZEL_BIN=$HOME/.local/bin/bazel-9.2.0 \
  VASO_ESTATE_ROOT=$VASO_ESTATE_ROOT \
  TMPDIR=$VASO_ESTATE_ROOT/agents/trae/tmp \
  SPACK_ROOT_PKG='py-trove-classifiers@2026.6.1.19 ^python@3.13.13+bz2+ctypes+dbm~debug+libxml2+lzma~optimizations+pic+pyexpat+pythoncmd+readline+shared+sqlite3+ssl~static~tkinter+uuid+zlib ^python-venv@1.0 ^py-pip@26.1.2 ^py-setuptools@79.0.1 ^py-wheel@0.45.1 ^py-calver@2025.10.20' \
  VASO_NATIVE=1 \
  VASO_SPACK_FRESH=1 \
  VASO_SPACK_TIMEOUT=900 \
  VASO_FORMAL=0 \
  VASO_SKIP_CONSUMER_TESTS=1 \
  VASO_SKIP_NATIVE_ABI_GATES=1 \
  VASO_EXTRA_TEST_TARGETS='//synthetic:use_py_trove_classifiers_native,//synthetic:py_trove_classifiers_prefix_parity,//tools:hermetic_native_deps_guard_test,//tools:native_dep_wiring_live_test' \
  ./run.sh
```

It ran entirely inside the CUDA bundle rootfs with Bazel-owned Spack `1.2.2`,
regenerated a focused lock with root `spack_py_trove_classifiers`, 32 Spack
packages, and a 37-node build graph tail `python@3.13.13`,
`python-venv@1.0`, `py-pip@26.1.2`, `py-setuptools@79.0.1`,
`py-wheel@0.45.1`, `py-calver@2025.10.20`, and
`py-trove-classifiers@2026.6.1.19`. The run passed
`//synthetic:use_py_trove_classifiers_native` with output
`py-trove-classifiers:2026.6.1.19:895:True:Typing :: Typed`, passed
`//synthetic:py_trove_classifiers_prefix_parity`, passed
`//tools:hermetic_native_deps_guard_test`, and passed
`//tools:native_dep_wiring_live_test` with 0 allowlisted mismatches. Full proof
log:
`$VASO_ESTATE_ROOT/agents/trae/logs/py-trove-classifiers-insula-proof-final-20260930T012816Z.log`.

The parity verdict compared the hermetic Spack reference prefix

```text
/vaso/cache/spack/opt/spack/linux-icelake/py-trove-classifiers-2026.6.1.19-phoxf56tikffuzhn6mtwnfitnd7qq7cp
```

against the native Bazel prefix

```text
/vaso/cache/bazel/output-base/external/+py_trove_classifiers_native+py_trove_classifiers_native/prefix
```

and reported `ok: true`, layout count 11/11, no missing or extra candidate
paths, byte-identical SHA256 values for all selected stable files after
prefix-normalizing `bin/trove-classifiers`, and an empty successful ELF ABI
axis.

## ODR-sensitive provider invariant

This slice does not add protobuf, gRPC, Abseil, or Boost providers. The graph
generator rejects package-wide native overrides and mixed concrete versions for
those ODR-sensitive families. Any future native capture in those families must
remain exact-version-qualified and family-version-consistent before it can enter
the lock.
