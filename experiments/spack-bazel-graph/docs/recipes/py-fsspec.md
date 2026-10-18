# py-fsspec native recipe

## Position in the hillclimb

`py-fsspec@2026.3.0` is the next pure `PythonPackage` source build after
native `py-filelock` in the lean `py-torch` frontier:

```text
124  py-filelock  3.29.1    python_pip  native
125  py-fsspec    2026.3.0  python_pip
```

The focused reference graph for `SPACK_ROOT_PKG='py-fsspec'` has 63
build-graph nodes and 58 Spack lock packages. Spack still owns the DAG shape;
the native flip changes only `spack_py_fsspec.build` to `native` and re-exports
`@py_fsspec_native//:lib`. Generated dependency edges remain Spack-derived.

## Spack evidence

All evidence here comes from Bazel's vendored `@spack_dist//:spack` release
running inside the CUDA insula. Do not use an ambient host Spack checkout.

Reference prefix from the focused hermetic run:

```text
/vaso/cache/spack/opt/spack/linux-icelake/py-fsspec-2026.3.0-pfmvi7prfwl26qefxrhg3jgsccvaj4vs
```

Hermetic recipe path observed from the installed prefix:

```text
/vaso/cache/spack/opt/spack/linux-icelake/py-fsspec-2026.3.0-pfmvi7prfwl26qefxrhg3jgsccvaj4vs/.spack/repos/spack_repo/builtin/packages/py_fsspec/package.py
```

Recipe facts from hermetic Spack `v1.2.2`:

- package class: `PyFsspec(PythonPackage)`
- selected build system: `python_pip`
- version: `2026.3.0`
- source payload: PyPI source archive
  `https://files.pythonhosted.org/packages/source/f/fsspec/fsspec-2026.3.0.tar.gz`
- source SHA256:
  `1ee6a0e28677557f8c2f994e3eea77db6392b4de9cd1f5d7a9e87a0ae9d01b41`
- homepage: `https://github.com/intake/filesystem_spec`
- license: `BSD-3-Clause`
- active variant: `http=false`, so no `py-aiohttp` runtime edge is present
- recipe dependencies for this version: `python@3.10:` for build/run,
  `py-hatchling@1.27:` for build, and `py-hatch-vcs` for build; the concrete
  hermetic build also supplies `py-pip`, `py-wheel`, and `python-venv`
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
  --prefix=<py-fsspec-prefix> \
  .
```

The hermetic build log reported:

```text
Created wheel for fsspec: filename=fsspec-2026.3.0-py3-none-any.whl
Successfully installed fsspec-2026.3.0
```

Generated wheel records, bytecode, and temporary build paths are not part of
the stable prefix contract.

## Native build recipe

`native/py_fsspec/py_fsspec.bzl` mirrors that install method directly:

```text
download and extract the exact fsspec-2026.3.0 source archive by SHA256
read @python_venv_native//:prefix_path.txt
derive PYTHON_ABI from @python_venv_native
read @py_hatch_vcs_native//:prefix_path.txt
read @py_hatchling_native//:prefix_path.txt
read @py_packaging_native//:prefix_path.txt
read @py_pathspec_native//:prefix_path.txt
read @py_pip_native//:prefix_path.txt
read @py_pluggy_native//:prefix_path.txt
read @py_setuptools_scm_native//:prefix_path.txt
read @py_setuptools_native//:prefix_path.txt
read @py_trove_classifiers_native//:prefix_path.txt
read @py_wheel_native//:prefix_path.txt
validate native python-venv, hatch-vcs, hatchling, packaging, pathspec, pip, pluggy, setuptools-scm, setuptools, trove-classifiers, and wheel prefixes under lib/python${PYTHON_ABI}/site-packages
clear PYTHONHOME
set HATCH_METADATA_CLASSIFIERS_NO_VERIFY=1
set PATH from native pip, wheel, python-venv, and rootfs tool paths
set PYTHONPATH from native hatch-vcs, hatchling, packaging, pathspec, pip, pluggy, setuptools-scm, setuptools, trove-classifiers, wheel, and python-venv using lib/python${PYTHON_ABI}/site-packages
PYTHON_VENV_PREFIX/bin/python${PYTHON_ABI} -m pip -vvv install \
  --no-deps --ignore-installed --no-build-isolation \
  --no-warn-script-location --no-index --prefix=<native-prefix> .
validate fsspec package modules, metadata, and license
emit prefix_path.txt
```

The rule refuses to evaluate unless `VASO_IN_INSULA=1`, so the wheel build and
prefix install happen only inside the sealed CUDA rootfs. It never searches for
host Spack; all Spack facts come from the Bazel-vendored Spack run, and all
Python packaging tools come from Bazel-native prefixes.

The mechanism verifier is expected to report this build channel:

```text
native/py_fsspec/py_fsspec.bzl: python-pip-install: PYTHON_VENV_PREFIX, PY_HATCH_VCS_PREFIX, PY_HATCHLING_PREFIX, PY_PACKAGING_PREFIX, PY_PATHSPEC_PREFIX, PY_PIP_PREFIX, PY_PLUGGY_PREFIX, PY_SETUPTOOLS_SCM_PREFIX, PY_SETUPTOOLS_PREFIX, PY_TROVE_CLASSIFIERS_PREFIX, PY_WHEEL_PREFIX
```

## Prefix and behavior contract

The stable prefix surface is:

- package modules under `lib/python3.13/site-packages/fsspec`
- metadata and license under
  `lib/python3.13/site-packages/fsspec-2026.3.0.dist-info`

Generated installation metadata that embeds the temporary build path or complete
wheel file list, such as `direct_url.json`, `RECORD`, and bytecode, is not part
of the stable parity surface.

## ABI and behavior gate

The smoke target is:

```text
//synthetic:use_py_fsspec_native
```

It resolves the native `py-fsspec`, native Python, and native Python venv,
imports fsspec through the native Python venv, verifies the installed metadata
version, and exercises the in-memory filesystem implementation. The expected
output is:

```text
py-fsspec:2026.3.0:2026.3.0:memory:native-fsspec
```

The parity target is:

```text
//synthetic:py_fsspec_prefix_parity
```

It compares `@py_fsspec_native//:prefix` against the hermetic Spack reference
prefix and covers:

- selected layout parity for package modules, `.dist-info` metadata, and
  license;
- byte-identical representative package files and metadata;
- empty ELF ABI axis, since the package installs no native shared libraries.

The completed 2026-09-30 hermetic CUDA-insula proof used rootfs mode
`cuda-bundle`, Bazel-owned Spack `1.2.2`, and the decided Python line
`python@3.13.13`. It regenerated root `spack_py_fsspec`, a 58-package lock
and 63-node build graph. `//synthetic:py_fsspec_prefix_parity` passed with
`ok: true`, 19 selected `lib/python3.13/site-packages` paths, byte-identical
package files and metadata, no missing or extra candidate paths, and an empty
ELF ABI axis. `//synthetic:use_py_fsspec_native` passed with output
`py-fsspec:2026.3.0:2026.3.0:memory:native-fsspec`. Full proof log:
`$VASO_ESTATE_ROOT/agents/trae/logs/py-fsspec-insula-proof-20260930T022959Z.log`.

## ODR-sensitive provider invariant

This slice does not add protobuf, gRPC, Abseil, or Boost providers. Those
families must stay on one unified compatible concrete version family; the
generator rejects unqualified overrides and mixed concrete versions for those
ODR-sensitive providers.
