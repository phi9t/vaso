# py-networkx native recipe

## Position in the hillclimb

`py-networkx@3.6.1~default~extra` is the pure `PythonPackage` source build at
pruned lean `py-torch` topo index 96. FC-14 disables NetworkX's `default` and
`extra` variants, so this node does not pull `py-numpy`, `py-scipy`,
`py-pandas`, `py-matplotlib`, `py-pythran`, `py-numba`, `py-llvmlite`, or
`llvm` back into the PyTorch closure.

Spack still owns the DAG shape. The native flip changes only
`spack_py_networkx.build` to `native` and re-exports
`@py_networkx_native//:lib`; generated dependency edges stay Spack-derived.

## Spack evidence

All evidence here comes from Bazel's vendored `@spack_dist//:spack` release
running inside the CUDA insula. Do not use an ambient host Spack checkout.

Recipe facts from hermetic Spack `v1.2.2`:

- package class: `PyNetworkx(PythonPackage)`
- selected build system: `python_pip`
- version: `3.6.1`
- variants: `~default~extra`
- source payload: PyPI source archive
  `https://pypi.io/packages/source/n/networkx/networkx-3.6.1.tar.gz`
- source SHA256:
  `26b7c357accc0c8cde558ad486283728b65b6a95d85ee1cd66bafab4c8168509`
- homepage: `https://networkx.github.io/`
- license: `BSD-3-Clause`
- Python requirement: `>=3.11,!=3.14.1`
- concrete dependencies in the pruned graph: `python@3.13.13`,
  `python-venv@1.0`, `py-pip@26.1.2`, `py-setuptools@79.0.1`, and
  `py-wheel@0.45.1`
- active optional dependency variants: `default=false`, `extra=false`
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
  --prefix=<py-networkx-prefix> \
  .
```

## Native build recipe

`native/py_networkx/py_networkx.bzl` mirrors that install method directly:

```text
download and extract the exact networkx-3.6.1 source archive by SHA256
read @python_venv_native//:prefix_path.txt
derive PYTHON_ABI from @python_venv_native
read @py_pip_native//:prefix_path.txt
read @py_setuptools_native//:prefix_path.txt
read @py_wheel_native//:prefix_path.txt
validate native python-venv, pip, setuptools, and wheel prefixes under lib/python${PYTHON_ABI}/site-packages
clear PYTHONHOME
set PYTHONNOUSERSITE=1
set PATH from native pip, wheel, python-venv, and rootfs tool paths
set PYTHONPATH from native pip, setuptools, wheel, and python-venv using lib/python${PYTHON_ABI}/site-packages
PYTHON_VENV_PREFIX/bin/python${PYTHON_ABI} -m pip -vvv install \
  --no-deps --ignore-installed --no-build-isolation \
  --no-warn-script-location --no-index --prefix=<native-prefix> .
validate networkx package modules, metadata, entry points, and license
emit prefix_path.txt
```

The rule refuses to evaluate unless `VASO_IN_INSULA=1`, so the wheel build and
prefix install happen only inside the sealed CUDA rootfs. It never searches for
host Spack; all Spack facts come from the Bazel-vendored Spack run, and all
Python packaging tools come from Bazel-native prefixes.

The mechanism verifier is expected to report this build channel:

```text
native/py_networkx/py_networkx.bzl: python-pip-install: PYTHON_VENV_PREFIX, PY_PIP_PREFIX, PY_SETUPTOOLS_PREFIX, PY_WHEEL_PREFIX
```

## Prefix and behavior contract

The stable prefix surface is:

- package modules under `lib/python3.13/site-packages/networkx`
- metadata, entry points, and license under
  `lib/python3.13/site-packages/networkx-3.6.1.dist-info`

Generated installation metadata that embeds the temporary build path or
complete wheel file list, such as `direct_url.json`, `RECORD`, and bytecode, is
not part of the stable parity surface. The package installs no ELF objects, so
the ABI axis is expected to be empty.

## ABI and behavior gate

The smoke target is:

```text
//synthetic:use_py_networkx_native
```

It resolves native `py-networkx`, native Python, and native Python venv,
imports `networkx`, verifies the installed metadata version, and exercises
basic graph construction plus shortest-path behavior. The expected output is:

```text
py-networkx:3.6.1:3.6.1:4:6:0-1-2-3
```

The parity target is:

```text
//synthetic:py_networkx_prefix_parity
```

It compares `@py_networkx_native//:prefix` against the hermetic Spack
reference prefix and covers selected package modules, `.dist-info` metadata,
entry points, license, byte-identical representative files, and the empty ELF
ABI axis.

## ODR-sensitive provider invariant

This slice does not add protobuf, gRPC, Abseil, or Boost providers. Those
families must stay on one unified compatible concrete version family; the
generator rejects unqualified overrides and mixed concrete versions for those
ODR-sensitive providers.
