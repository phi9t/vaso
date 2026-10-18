# py-hatchling native recipe

## Position in the hillclimb

`py-hatchling@1.29.0` is the next pure `PythonPackage` source build after
native `py-trove-classifiers` in the lean `py-torch` frontier:

```text
104  py-trove-classifiers   2026.6.1.19  python_pip  native
105  py-hatchling           1.29.0        python_pip  native
```

The focused reference graph for the final proof used root
`spack_py_hatchling`, 61 build-graph nodes, and 56 Spack lock packages. Spack
still owns the DAG shape; the native flip changes only
`spack_py_hatchling.build` to `native` and re-exports
`@py_hatchling_native//:lib`. Generated dependency edges remain Spack-derived.

## Spack evidence

All evidence here comes from Bazel's vendored `@spack_dist//:spack` release
running inside the CUDA insula. Do not use an ambient host Spack checkout.

Reference prefix from the focused hermetic run:

```text
/vaso/cache/spack/opt/spack/linux-icelake/py-hatchling-1.29.0-rvc6nusqzula36tijlks5ojhdvnja4vi
```

Hermetic recipe path observed from the installed prefix:

```text
/vaso/cache/spack/opt/spack/linux-icelake/py-hatchling-1.29.0-rvc6nusqzula36tijlks5ojhdvnja4vi/.spack/repos/spack_repo/builtin/packages/py_hatchling/package.py
```

Recipe facts from hermetic Spack `v1.2.2`:

- package class: `PyHatchling(PythonPackage)`
- selected build system: `python_pip`
- version: `1.29.0`
- source payload: PyPI source archive
  `https://files.pythonhosted.org/packages/source/h/hatchling/hatchling-1.29.0.tar.gz`
- source SHA256:
  `793c31816d952cee405b83488ce001c719f325d9cda69f1fc4cd750527640ea6`
- license: `MIT`
- dependencies: `python@3.10:` for `@1.28:`, `py-packaging@24.2:` for
  `@1.26:`, `py-pathspec@0.10.1:` for `@1.9:`, `py-pluggy@1:`, and
  `py-trove-classifiers` for `@1.14:`; the concrete hermetic 3.13 proof pins
  `py-pathspec@1.1.1` so the Spack graph node matches the already-captured
  native provider exactly
- patches: none
- dependent build environment: `HATCH_METADATA_CLASSIFIERS_NO_VERIFY=1`

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
  --prefix=<py-hatchling-prefix> \
  .
```

Pip builds an intermediate wheel from the source tree, then installs that wheel
into the prefix. Generated wheel records, bytecode, and temporary build paths
are not part of the stable prefix contract.

## Native build recipe

`native/py_hatchling/py_hatchling.bzl` mirrors that install method directly:

```text
download and extract the exact hatchling-1.29.0 source archive by SHA256
read @python_venv_native//:prefix_path.txt
derive PYTHON_ABI from @python_venv_native/bin/python3
read @py_packaging_native//:prefix_path.txt
read @py_pathspec_native//:prefix_path.txt
read @py_pip_native//:prefix_path.txt
read @py_pluggy_native//:prefix_path.txt
read @py_trove_classifiers_native//:prefix_path.txt
read @py_wheel_native//:prefix_path.txt
validate native python-venv and package inputs under lib/python${PYTHON_ABI}/site-packages
clear PYTHONHOME
set HATCH_METADATA_CLASSIFIERS_NO_VERIFY=1
set PATH from native pip, wheel, python-venv, and rootfs tool paths
set PYTHONPATH from native packaging, pathspec, pip, pluggy, trove-classifiers, wheel, and python-venv
PYTHON_VENV_PREFIX/bin/python3 -m pip -vvv install \
  --no-deps --ignore-installed --no-build-isolation \
  --no-warn-script-location --no-index --prefix=<native-prefix> .
validate hatchling executable, selected package modules, typed marker, metadata, and license
emit prefix_path.txt
```

The rule refuses to evaluate unless `VASO_IN_INSULA=1`, so the wheel build and
prefix install happen only inside the sealed CUDA rootfs. It never searches for
host Spack; all Spack facts come from the Bazel-vendored Spack run, and all
Python packaging tools come from Bazel-native prefixes.

The mechanism verifier reports the expected build channel:

```text
native/py_hatchling/py_hatchling.bzl: python-pip-install: PYTHON_VENV_PREFIX, PY_PACKAGING_PREFIX, PY_PATHSPEC_PREFIX, PY_PIP_PREFIX, PY_PLUGGY_PREFIX, PY_TROVE_CLASSIFIERS_PREFIX, PY_WHEEL_PREFIX
```

## Prefix and behavior contract

The stable prefix surface is:

- `bin/hatchling`
- package modules under `lib/python3.13/site-packages/hatchling`
- metadata and license under
  `lib/python3.13/site-packages/hatchling-1.29.0.dist-info`

Generated installation metadata that embeds the temporary build path or complete
wheel file list, such as `direct_url.json`, `RECORD`, and bytecode, is not part
of the stable parity surface.

## ABI and behavior gate

The smoke target is:

```text
//synthetic:use_py_hatchling_native
```

It resolves the native `py-hatchling`, native Python packaging prefixes, and
native Python venv, imports hatchling through the native Python 3.13 venv,
verifies the installed metadata version, and expects:

```text
py-hatchling:1.29.0:1.29.0:WheelBuilder:hatchling
```

The parity target is:

```text
//synthetic:py_hatchling_prefix_parity
```

It compares `@py_hatchling_native//:prefix` against the hermetic Spack
reference prefix and covers:

- selected layout parity for the CLI wrapper, package modules, `.dist-info`
  metadata, typed marker, and license;
- byte-identical representative package files and metadata, with prefix
  normalization for the generated `bin/hatchling` wrapper;
- empty ELF ABI axis, since the package installs no native shared libraries.

Current status: native `py-hatchling` is gated inside the hermetic CUDA insula.
This command used Bazel 9.2.0, estate root
`$VASO_ESTATE_ROOT`, and no
`VASO_FORCE_FETCH_REPOS` override:

```bash
env \
  BAZEL_BIN=$HOME/.local/bin/bazel-9.2.0 \
  VASO_ESTATE_ROOT=$VASO_ESTATE_ROOT \
  TMPDIR=$VASO_ESTATE_ROOT/agents/trae/tmp \
  VASO_BAZEL_OB=$VASO_ESTATE_ROOT/agents/trae/bazel-ob \
  VASO_NATIVE=1 \
  SPACK_ROOT_PKG='py-hatchling@1.29.0 ^python@3.13.13+bz2+ctypes+dbm~debug+libxml2+lzma~optimizations+pic+pyexpat+pythoncmd+readline+shared+sqlite3+ssl~static~tkinter+uuid+zlib ^python-venv@1.0 ^py-pip@26.1.2 ^py-wheel@0.45.1 ^py-packaging@26.2 ^py-pathspec@1.1.1 ^py-pluggy@1.6.0 ^py-trove-classifiers@2026.6.1.19' \
  VASO_EXTRA_TEST_TARGETS='//tools:hermetic_native_deps_guard_test,//tools:spack_to_bazel_unit_test,//synthetic:use_py_hatchling_native,//synthetic:py_hatchling_prefix_parity' \
  ./run.sh
```

It ran entirely inside the CUDA bundle rootfs, regenerated the focused lock with
root `spack_py_hatchling`, passed `//tools:hermetic_native_deps_guard_test`,
passed `//tools:spack_to_bazel_unit_test`, passed
`//synthetic:use_py_hatchling_native` with output
`py-hatchling:1.29.0:1.29.0:WheelBuilder:hatchling`, and passed
`//synthetic:py_hatchling_prefix_parity`.

The parity verdict compared the hermetic Spack reference prefix

```text
/vaso/cache/spack/opt/spack/linux-icelake/py-hatchling-1.29.0-rvc6nusqzula36tijlks5ojhdvnja4vi
```

against the native Bazel prefix

```text
/vaso/cache/bazel/output-base/external/+py_hatchling_native+py_hatchling_native/prefix
```

and reported `ok: true`, selected stable layout parity under
`lib/python3.13/site-packages`, byte-identical SHA256 values for selected
stable files after prefix-normalizing `bin/hatchling`, and an empty successful
ELF ABI axis.

Full proof log:

```text
$VASO_ESTATE_ROOT/agents/trae/logs/py-hatchling-insula-proof-pathspec111-20260930T013948Z.log
```

## ODR-sensitive provider invariant

This slice does not add protobuf, gRPC, Abseil, or Boost providers.
`//tools:spack_to_bazel_unit_test` verifies that the graph generator rejects
package-wide native overrides and mixed concrete versions for those
ODR-sensitive families. Any future native capture in those families must remain
exact-version-qualified and family-version-consistent before it can enter the
lock.
