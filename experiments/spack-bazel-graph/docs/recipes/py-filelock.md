# py-filelock native recipe

## Position in the hillclimb

`py-filelock@3.29.1` is the next pure `PythonPackage` source build after
native `py-hatch-vcs` in the lean `py-torch` frontier:

```text
122  py-hatch-vcs   0.5.0   python_pip  native
124  py-filelock    3.29.1  python_pip
```

The focused reference graph for `SPACK_ROOT_PKG='py-filelock'` has 63
build-graph nodes and 58 Spack lock packages. Spack still owns the DAG shape;
the native flip changes only `spack_py_filelock.build` to `native` and
re-exports `@py_filelock_native//:lib`. Generated dependency edges remain
Spack-derived.

## Spack evidence

All evidence here comes from Bazel's vendored `@spack_dist//:spack` release
running inside the CUDA insula. Do not use an ambient host Spack checkout.

Reference prefix from the focused hermetic run:

```text
/vaso/cache/spack/opt/spack/linux-icelake/py-filelock-3.29.1-xvyrwotaeeknbanibpsenwrzq6j4tc5h
```

Hermetic recipe path observed from the installed prefix:

```text
/vaso/cache/spack/opt/spack/linux-icelake/py-filelock-3.29.1-xvyrwotaeeknbanibpsenwrzq6j4tc5h/.spack/repos/spack_repo/builtin/packages/py_filelock/package.py
```

Recipe facts from hermetic Spack `v1.2.2`:

- package class: `PyFilelock(PythonPackage)`
- selected build system: `python_pip`
- version: `3.29.1`
- source payload: PyPI source archive
  `https://files.pythonhosted.org/packages/source/f/filelock/filelock-3.29.1.tar.gz`
- source SHA256:
  `d97e6b1b9757569626c58caa07dc4beb1613f4a2938b1e8cc81afca398906c9e`
- homepage: `https://github.com/tox-dev/py-filelock`
- license: `MIT` for `@3.23:`
- Python requirement from installed metadata: `>=3.10`
- runtime metadata: no `Requires-Dist`
- recipe dependencies for this version: `python@3.10:` for build/run,
  `py-hatch-vcs@0.5:` for build, and `py-hatchling@1.29:` for build; the
  concrete hermetic build also supplies `py-pip`, `py-wheel`, and
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
  --prefix=<py-filelock-prefix> \
  .
```

The hermetic build log reported:

```text
==> No patches needed for py-filelock
Building wheel for filelock (pyproject.toml): finished with status 'done'
Created wheel for filelock: filename=filelock-3.29.1-py3-none-any.whl
Successfully installed filelock-3.29.1
```

Generated wheel records, bytecode, and temporary build paths are not part of
the stable prefix contract.

## Native build recipe

`native/py_filelock/py_filelock.bzl` mirrors that install method directly:

```text
download and extract the exact filelock-3.29.1 source archive by SHA256
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
validate filelock package modules, typed marker, metadata, and license
emit prefix_path.txt
```

The rule refuses to evaluate unless `VASO_IN_INSULA=1`, so the wheel build and
prefix install happen only inside the sealed CUDA rootfs. It never searches for
host Spack; all Spack facts come from the Bazel-vendored Spack run, and all
Python packaging tools come from Bazel-native prefixes.

The mechanism verifier is expected to report this build channel:

```text
native/py_filelock/py_filelock.bzl: python-pip-install: PYTHON_VENV_PREFIX, PY_HATCH_VCS_PREFIX, PY_HATCHLING_PREFIX, PY_PACKAGING_PREFIX, PY_PATHSPEC_PREFIX, PY_PIP_PREFIX, PY_PLUGGY_PREFIX, PY_SETUPTOOLS_SCM_PREFIX, PY_SETUPTOOLS_PREFIX, PY_TROVE_CLASSIFIERS_PREFIX, PY_WHEEL_PREFIX
```

## Prefix and behavior contract

The stable prefix surface is:

- package modules under `lib/python3.13/site-packages/filelock`
- metadata and license under
  `lib/python3.13/site-packages/filelock-3.29.1.dist-info`
- `lib/python3.13/site-packages/filelock/py.typed`

Generated installation metadata that embeds the temporary build path or complete
wheel file list, such as `direct_url.json`, `RECORD`, and bytecode, is not part
of the stable parity surface.

## ABI and behavior gate

The smoke target is:

```text
//synthetic:use_py_filelock_native
```

It resolves the native `py-filelock`, native Python, and native Python venv,
imports filelock through the native Python venv, verifies the installed metadata
version, and exercises a real `FileLock` acquisition in a temporary directory.
The expected output is:

```text
py-filelock:3.29.1:3.29.1:3.29.1:UnixFileLock:SoftFileLock
```

The parity target is:

```text
//synthetic:py_filelock_prefix_parity
```

It compares `@py_filelock_native//:prefix` against the hermetic Spack reference
prefix and covers:

- selected layout parity for package modules, `.dist-info` metadata, typed
  marker, and license;
- byte-identical representative package files and metadata;
- empty ELF ABI axis, since the package installs no native shared libraries.

The completed 2026-09-30 hermetic CUDA-insula proof used rootfs mode
`cuda-bundle`, Bazel-owned Spack `1.2.2`, and the decided Python line
`python@3.13.13`. It regenerated root `spack_py_filelock`, a 58-package lock
and 63-node build graph. `//synthetic:py_filelock_prefix_parity` passed with
`ok: true`, 20 selected `lib/python3.13/site-packages` paths, byte-identical
package files and metadata, no missing or extra candidate paths, and an empty
ELF ABI axis. Full log:
`$VASO_ESTATE_ROOT/agents/trae/logs/py-filelock-insula-proof-direct-pins-20260930T021258Z.log`.
After tightening the native action to invoke
`PYTHON_VENV_PREFIX/bin/python${PYTHON_ABI}`, the focused insula rerun of
`//synthetic:use_py_filelock_native` and
`//synthetic:py_filelock_prefix_parity` passed against the same hermetic Spack
reference prefixes. Focused log:
`$VASO_ESTATE_ROOT/agents/trae/logs/py-filelock-focused-insula-final-20260930T022426Z.log`.

## ODR-sensitive provider invariant

This slice does not add protobuf, gRPC, Abseil, or Boost providers. Those
families must stay on one unified compatible concrete version family; the
generator rejects unqualified overrides and mixed concrete versions for those
ODR-sensitive providers.
