# py-typing-extensions native recipe

## Position in the hillclimb

`py-typing-extensions@4.15.0` is the next `PythonPackage` source build after
native `py-scikit-build-core` in the lean `py-torch` frontier:

```text
109  py-scikit-build-core  0.12.2  python_pip  native
110  py-typing-extensions  4.15.0  python_pip
```

The focused reference graph for `SPACK_ROOT_PKG='py-typing-extensions'` has 36
build-graph nodes and 31 Spack lock packages. Spack still owns the DAG shape;
the native flip changes only `spack_py_typing_extensions.build` to `native`
and re-exports `@py_typing_extensions_native//:lib`.

## Spack evidence

All evidence here comes from Bazel's vendored `@spack_dist//:spack` release
running inside the CUDA insula. Do not use an ambient host Spack checkout.

Reference prefix from the focused hermetic run:

```text
/vaso/cache/spack/opt/spack/linux-icelake/py-typing-extensions-4.15.0-tgz33n4mrao6ipmvkis3rdy47mlhwcm4
```

Hermetic recipe path observed from the installed prefix:

```text
/vaso/cache/spack/opt/spack/linux-icelake/py-typing-extensions-4.15.0-tgz33n4mrao6ipmvkis3rdy47mlhwcm4/.spack/repos/spack_repo/builtin/packages/py_typing_extensions/package.py
```

Recipe facts from hermetic Spack `v1.2.2`:

- package class: `PyTypingExtensions(PythonPackage)`
- selected build system: `python_pip`
- version: `4.15.0`
- source payload: PyPI source archive
  `https://files.pythonhosted.org/packages/source/t/typing_extensions/typing_extensions-4.15.0.tar.gz`
- source SHA256:
  `0cea48d173cc12fa28ecabc3b837ea3cf6f38c6d1136f85cbaaf598984861466`
- homepage: `https://github.com/python/typing_extensions`
- license: `0BSD AND PSF-2.0`
- concrete build dependencies: `py-flit-core@3.11:3`, `py-pip`, and `py-wheel`
- concrete build/run dependencies: `python@3.9:` and `python-venv`
- historical `py-setuptools` dependency is inactive for `@4.15.0`
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
  --prefix=<py-typing-extensions-prefix> \
  .
```

The hermetic build log reported:

```text
Using pip 26.1.2 from <py-pip-prefix>/lib/python3.13/site-packages/pip (python 3.13)
Created wheel for typing_extensions: filename=typing_extensions-4.15.0-py3-none-any.whl
Successfully installed typing_extensions-4.15.0
```

Generated installation metadata that embeds the temporary build path or
complete wheel file list, such as `direct_url.json`, `RECORD`, and bytecode, is
not part of the stable prefix contract.

## Native build recipe

`native/py_typing_extensions/py_typing_extensions.bzl` mirrors that install
method directly:

```text
download and extract the exact typing_extensions-4.15.0 source archive by SHA256
read @python_venv_native//:prefix_path.txt
derive PYTHON_ABI from @python_venv_native
read @py_flit_core_native//:prefix_path.txt
read @py_pip_native//:prefix_path.txt
read @py_wheel_native//:prefix_path.txt
validate native python-venv and flit-core/pip/wheel under lib/python${PYTHON_ABI}
clear PYTHONHOME
set PYTHONPATH from ABI-derived native flit-core, pip, wheel, and python-venv paths
PYTHON_VENV_PREFIX/bin/python3 -m pip -vvv install \
  --no-deps --ignore-installed --no-build-isolation \
  --no-warn-script-location --no-index --prefix=<native-prefix> .
validate typing_extensions.py, dist-info metadata, and license under lib/python${PYTHON_ABI}
emit prefix_path.txt
```

The rule refuses to evaluate unless `VASO_IN_INSULA=1`, so the wheel build and
prefix install happen only inside the sealed CUDA rootfs. It never searches for
host Spack; all Spack facts come from the Bazel-vendored Spack run, and all
Python packaging tools come from Bazel-native prefixes.

The mechanism verifier is expected to report this build channel:

```text
native/py_typing_extensions/py_typing_extensions.bzl: python-pip-install: PYTHON_VENV_PREFIX, PY_FLIT_CORE_PREFIX, PY_PIP_PREFIX, PY_WHEEL_PREFIX
```

## Prefix and behavior contract

The stable prefix surface is:

- `lib/python3.13/site-packages/typing_extensions.py`
- metadata and license under
  `lib/python3.13/site-packages/typing_extensions-4.15.0.dist-info`

Generated installation metadata that embeds the temporary build path or
complete wheel file list, such as `direct_url.json`, `RECORD`, and bytecode, is
not part of the stable parity surface. The package installs no ELF objects, so
the ABI axis is expected to be empty.

## ABI and behavior gate

The smoke target is:

```text
//synthetic:use_py_typing_extensions_native
```

It resolves native `py-typing-extensions`, native Python, and native Python
venv, imports `typing_extensions`, verifies the installed metadata version, and
checks representative runtime symbols including `TypeAliasType` and `Self`.

The parity target is:

```text
//synthetic:py_typing_extensions_prefix_parity
```

It compares `@py_typing_extensions_native//:prefix` against the hermetic Spack
reference prefix and covers:

- selected layout parity for the module, dist-info metadata, and license;
- byte-identical representative package files and metadata;
- empty ELF ABI axis.

Focused 3.13 proof:

```text
$VASO_ESTATE_ROOT/agents/trae/logs/py-typing-extensions-focused-insula-rerun-20260930T043742Z.log
```

## ODR-sensitive provider invariant

This slice does not add protobuf, gRPC, Abseil, or Boost providers. Those
families must stay on one unified compatible concrete version family across
all companion packages before any native flip. The generator rejects
unqualified overrides and rejects mixed concrete family versions, including
protobuf/Python protobuf and gRPC/gRPC C++ pairings.
