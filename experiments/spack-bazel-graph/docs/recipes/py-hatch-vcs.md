# py-hatch-vcs native recipe

## Position in the hillclimb

`py-hatch-vcs@0.5.0` is the next pure `PythonPackage` source build after
native `py-hatchling` in the lean `py-torch` frontier:

```text
105  py-hatchling   1.29.0  python_pip  native
106  py-hatch-vcs   0.5.0   python_pip  native
```

The focused reference graph for the final proof used root
`spack_py_hatch_vcs`, 62 build-graph nodes, and 57 Spack lock packages. Spack
still owns the DAG shape; the native flip changes only
`spack_py_hatch_vcs.build` to `native` and re-exports
`@py_hatch_vcs_native//:lib`. Generated dependency edges remain Spack-derived.

## Spack evidence

All evidence here comes from Bazel's vendored `@spack_dist//:spack` release
running inside the CUDA insula. Do not use an ambient host Spack checkout.

Reference prefix from the focused hermetic run:

```text
/vaso/cache/spack/opt/spack/linux-icelake/py-hatch-vcs-0.5.0-4ewjmi6wdgd4mzdoayoqqielxiklitp4
```

Hermetic recipe path observed from the installed prefix:

```text
/vaso/cache/spack/opt/spack/linux-icelake/py-hatch-vcs-0.5.0-4ewjmi6wdgd4mzdoayoqqielxiklitp4/.spack/repos/spack_repo/builtin/packages/py_hatch_vcs/package.py
```

Recipe facts from hermetic Spack `v1.2.2`:

- package class: `PyHatchVcs(PythonPackage)`
- selected build system: `python_pip`
- version: `0.5.0`
- source payload: PyPI source archive
  `https://files.pythonhosted.org/packages/source/h/hatch-vcs/hatch_vcs-0.5.0.tar.gz`
- source SHA256:
  `0395fa126940340215090c344a2bf4e2a77bcbe7daab16f41b37b98c95809ff9`
- homepage: `https://github.com/ofek/hatch-vcs`
- license: `MIT`
- dependencies: `python@3.9:` for `@0.5:`, `py-hatchling@1.1:` for
  `@0.3:`, `py-setuptools-scm@8.2.0:` for `@0.5:`, and
  `py-setuptools-scm@6.4.0:` generally; the concrete hermetic 3.13 proof uses
  native `py-hatchling@1.29.0`, `py-setuptools-scm@8.2.1`,
  `py-pathspec@1.1.1`, `py-packaging@26.2`, `py-pluggy@1.6.0`,
  `py-trove-classifiers@2026.6.1.19`, `py-pip@26.1.2`, `py-wheel@0.45.1`,
  and `python-venv@1.0`
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
  --prefix=<py-hatch-vcs-prefix> \
  .
```

Pip builds an intermediate `hatch_vcs-0.5.0-py3-none-any.whl` from the source
tree, then installs that wheel into the prefix. Generated wheel records,
bytecode, and temporary build paths are not part of the stable prefix contract.

## Native build recipe

`native/py_hatch_vcs/py_hatch_vcs.bzl` mirrors that install method directly:

```text
download and extract the exact hatch_vcs-0.5.0 source archive by SHA256
read @python_venv_native//:prefix_path.txt
derive PYTHON_ABI from @python_venv_native/bin/python3
read @py_hatchling_native//:prefix_path.txt
read @py_packaging_native//:prefix_path.txt
read @py_pathspec_native//:prefix_path.txt
read @py_pip_native//:prefix_path.txt
read @py_pluggy_native//:prefix_path.txt
read @py_setuptools_scm_native//:prefix_path.txt
read @py_trove_classifiers_native//:prefix_path.txt
read @py_wheel_native//:prefix_path.txt
validate native package inputs under lib/python${PYTHON_ABI}/site-packages
clear PYTHONHOME
set HATCH_METADATA_CLASSIFIERS_NO_VERIFY=1
set PATH from native pip, wheel, python-venv, and rootfs tool paths
set PYTHONPATH from native hatchling, packaging, pathspec, pip, pluggy, setuptools-scm, trove-classifiers, wheel, and python-venv
PYTHON_VENV_PREFIX/bin/python3 -m pip -vvv install \
  --no-deps --ignore-installed --no-build-isolation \
  --no-warn-script-location --no-index --prefix=<native-prefix> .
validate hatch_vcs package modules and stable .dist-info metadata
emit prefix_path.txt
```

The rule refuses to evaluate unless `VASO_IN_INSULA=1`, so the wheel build and
prefix install happen only inside the sealed CUDA rootfs. It never searches for
host Spack; all Spack facts come from the Bazel-vendored Spack run, and all
Python packaging tools come from Bazel-native prefixes.

The mechanism verifier reports the expected build channel:

```text
native/py_hatch_vcs/py_hatch_vcs.bzl: python-pip-install: PYTHON_VENV_PREFIX, PY_HATCHLING_PREFIX, PY_PACKAGING_PREFIX, PY_PATHSPEC_PREFIX, PY_PIP_PREFIX, PY_PLUGGY_PREFIX, PY_SETUPTOOLS_SCM_PREFIX, PY_TROVE_CLASSIFIERS_PREFIX, PY_WHEEL_PREFIX
```

## Prefix and behavior contract

The stable prefix surface is:

- package modules under `lib/python3.13/site-packages/hatch_vcs`
- metadata under `lib/python3.13/site-packages/hatch_vcs-0.5.0.dist-info`

Generated installation metadata that embeds the temporary build path or complete
wheel file list, such as `direct_url.json`, `RECORD`, and bytecode, is not part
of the stable parity surface.

## ABI and behavior gate

The smoke target is:

```text
//synthetic:use_py_hatch_vcs_native
```

It resolves the native `py-hatch-vcs`, native Python packaging prefixes, and
native Python venv, imports hatch-vcs through the native Python 3.13 venv,
verifies the installed metadata version, and expects:

```text
py-hatch-vcs:0.5.0:0.5.0:hatch_vcs.build_hook:hatch_vcs.metadata_hook:hatch_vcs.version_source
```

The parity target is:

```text
//synthetic:py_hatch_vcs_prefix_parity
```

It compares `@py_hatch_vcs_native//:prefix` against the hermetic Spack
reference prefix and covers:

- selected layout parity for package modules and `.dist-info` metadata;
- byte-identical representative package files and metadata;
- empty ELF ABI axis, since the package installs no native shared libraries.

Current status: native `py-hatch-vcs` is gated inside the hermetic CUDA insula.
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
  SPACK_ROOT_PKG='py-hatch-vcs@0.5.0 ^python@3.13.13+bz2+ctypes+dbm~debug+libxml2+lzma~optimizations+pic+pyexpat+pythoncmd+readline+shared+sqlite3+ssl~static~tkinter+uuid+zlib ^python-venv@1.0 ^py-pip@26.1.2 ^py-wheel@0.45.1 ^py-packaging@26.2 ^py-pathspec@1.1.1 ^py-pluggy@1.6.0 ^py-trove-classifiers@2026.6.1.19 ^py-hatchling@1.29.0 ^py-setuptools-scm@8.2.1' \
  VASO_EXTRA_TEST_TARGETS='//tools:hermetic_native_deps_guard_test,//tools:spack_to_bazel_unit_test,//synthetic:use_py_hatch_vcs_native,//synthetic:py_hatch_vcs_prefix_parity' \
  ./run.sh
```

It ran entirely inside the CUDA bundle rootfs, regenerated the focused lock with
root `spack_py_hatch_vcs`, passed `//tools:hermetic_native_deps_guard_test`,
passed `//tools:spack_to_bazel_unit_test`, passed
`//synthetic:use_py_hatch_vcs_native` with output
`py-hatch-vcs:0.5.0:0.5.0:hatch_vcs.build_hook:hatch_vcs.metadata_hook:hatch_vcs.version_source`,
and passed `//synthetic:py_hatch_vcs_prefix_parity`.

The parity verdict compared the hermetic Spack reference prefix

```text
/vaso/cache/spack/opt/spack/linux-icelake/py-hatch-vcs-0.5.0-4ewjmi6wdgd4mzdoayoqqielxiklitp4
```

against the native Bazel prefix

```text
/vaso/cache/bazel/output-base/external/+py_hatch_vcs_native+py_hatch_vcs_native/prefix
```

and reported `ok: true`, selected stable layout parity under
`lib/python3.13/site-packages`, byte-identical SHA256 values for selected
stable files, and an empty successful ELF ABI axis.

Full proof log:

```text
$VASO_ESTATE_ROOT/agents/trae/logs/py-hatch-vcs-insula-proof-20260930T015621Z.log
```

## ODR-sensitive provider invariant

This slice does not add protobuf, gRPC, Abseil, or Boost providers.
`//tools:spack_to_bazel_unit_test` verifies that the graph generator rejects
package-wide native overrides and mixed concrete versions for those
ODR-sensitive families. Any future native capture in those families must remain
exact-version-qualified and family-version-consistent before it can enter the
lock.
