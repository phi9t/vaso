# py-versioneer native recipe

## Position in the hillclimb

`py-versioneer@0.29` is the next `PythonPackage` source build after native
`py-requests` in the lean `py-torch` frontier:

```text
128  py-requests   2.33.1  python_pip  native
129  py-versioneer 0.29    python_pip
130  re2c          4.4     autotools
```

The focused reference graph for `SPACK_ROOT_PKG='py-versioneer'` has 36
build-graph nodes and uses 21 `autotools`, 12 `generic`, 2 `makefile`, and 1
`python_pip` build-system nodes. Spack still owns the DAG shape; the native
flip changes only `spack_py_versioneer.build` to `native` and re-exports
`@py_versioneer_native//:lib`.

## Spack evidence

All evidence here comes from Bazel's vendored `@spack_dist//:spack` release
running inside the CUDA insula. Do not use an ambient host Spack checkout.

Reference prefix from the focused hermetic run:

```text
/vaso/cache/spack/opt/spack/linux-icelake/py-versioneer-0.29-x5gkxms4pc5ck2wirtozejttj6w3vcqh
```

Hermetic recipe path observed from the installed prefix:

```text
/vaso/cache/spack/opt/spack/linux-icelake/py-versioneer-0.29-x5gkxms4pc5ck2wirtozejttj6w3vcqh/.spack/repos/spack_repo/builtin/packages/py_versioneer/package.py
```

Recipe facts from hermetic Spack `v1.2.2`:

- package class: `PyVersioneer(PythonPackage)`
- selected build system: `python_pip`
- version: `0.29`
- source payload: PyPI source archive
  `https://files.pythonhosted.org/packages/source/v/versioneer/versioneer-0.29.tar.gz`
- source SHA256:
  `5ab283b9857211d61b53318b7c792cf68e798e765ee17c27ade9f6c924235731`
- homepage: `https://github.com/python-versioneer/python-versioneer`
- license: `Unlicense`
- concrete build dependencies: `py-pip`, `py-setuptools`, and `py-wheel`
- concrete build/run dependencies: `python` and `python-venv`
- active variant: `toml=true`
- `py-tomli` is inactive for this concrete Python 3.14 build because the Spack
  recipe only adds that edge for older Python ranges
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
  --prefix=<py-versioneer-prefix> \
  .
```

The hermetic build log reported:

```text
Using pip 26.1.2 from <py-pip-prefix>/lib/python3.14/site-packages/pip (python 3.14)
Created wheel for versioneer: filename=versioneer-0.29-py3-none-any.whl size=47209 sha256=02f6f0a652d00a4bd155b1e8a4b72204a4697c82b56e6173131f35f0483e2b12
Successfully installed versioneer-0.29
```

Generated installation metadata that embeds the temporary build path or
complete wheel file list, such as `direct_url.json`, `RECORD`, and bytecode, is
not part of the stable prefix contract.

## Native build recipe

`native/py_versioneer/py_versioneer.bzl` mirrors that install method directly:

```text
download and extract the exact versioneer-0.29 source archive by SHA256
read @python_venv_native//:prefix_path.txt
read @py_pip_native//:prefix_path.txt
read @py_setuptools_native//:prefix_path.txt
read @py_wheel_native//:prefix_path.txt
validate every native prefix before invoking pip
clear PYTHONHOME
set PYTHONPATH from native pip, setuptools, wheel, and python-venv prefixes
PYTHON_VENV_PREFIX/bin/python3 -m pip -vvv install \
  --no-deps --ignore-installed --no-build-isolation \
  --no-warn-script-location --no-index --prefix=<native-prefix> .
validate bin/versioneer, versioneer.py, dist-info metadata, entry points, and
  license
emit prefix_path.txt
```

The rule refuses to evaluate unless `VASO_IN_INSULA=1`, so the wheel build and
prefix install happen only inside the sealed CUDA rootfs. It never searches for
host Spack; all Spack facts come from the Bazel-vendored Spack run, and all
Python packaging tools come from Bazel-native prefixes.

The mechanism verifier is expected to report this build channel:

```text
native/py_versioneer/py_versioneer.bzl: python-pip-install: PYTHON_VENV_PREFIX, PY_PIP_PREFIX, PY_SETUPTOOLS_PREFIX, PY_WHEEL_PREFIX
```

## Prefix and behavior contract

The stable prefix surface is:

- `lib/python3.14/site-packages/versioneer.py`
- metadata, entry points, and license under
  `lib/python3.14/site-packages/versioneer-0.29.dist-info`

The package also installs `bin/versioneer`, but its shebang embeds the concrete
Python prefix. That script is behavior-tested rather than byte-compared in the
prefix parity gate. Generated installation metadata that embeds the temporary
build path or complete wheel file list, such as `direct_url.json`, `RECORD`,
and bytecode, is not part of the stable parity surface. The package installs
no ELF objects, so the ABI axis is expected to be empty.

## ABI and behavior gate

The smoke target is:

```text
//synthetic:use_py_versioneer_native
```

It resolves native `py-versioneer`, native Python, native Python venv, and
native `py-pip`, `py-setuptools`, and `py-wheel`, imports `versioneer`,
verifies the installed metadata version, checks that `versioneer.main` is
callable, and executes the generated `bin/versioneer --version` script.

The parity target is:

```text
//synthetic:py_versioneer_prefix_parity
```

It compares `@py_versioneer_native//:prefix` against the hermetic Spack
reference prefix and covers:

- selected layout parity for the package module, dist-info metadata, entry
  points, and license;
- byte-identical representative package files and metadata;
- empty ELF ABI axis.

## ODR-sensitive provider invariant

This slice does not add protobuf, gRPC, Abseil, or Boost providers. Those
families must stay on one unified compatible concrete version family across
all companion packages before any native flip. The generator rejects
unqualified overrides and rejects mixed concrete family versions, including
protobuf/Python protobuf and gRPC/gRPC C++ pairings.
