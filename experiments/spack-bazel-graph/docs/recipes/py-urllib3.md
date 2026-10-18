# py-urllib3 native recipe

## Position in the hillclimb

`py-urllib3@2.6.3` is the next `PythonPackage` source build after native
`py-typing-extensions` in the lean `py-torch` frontier:

```text
110  py-typing-extensions  4.15.0  python_pip  native
111  py-urllib3            2.6.3   python_pip
```

The focused reference graph for `SPACK_ROOT_PKG='py-urllib3'` has 63
build-graph nodes and 58 Spack lock packages. Spack still owns the DAG shape;
the native flip changes only `spack_py_urllib3.build` to `native` and
re-exports `@py_urllib3_native//:lib`.

## Spack evidence

All evidence here comes from Bazel's vendored `@spack_dist//:spack` release
running inside the CUDA insula. Do not use an ambient host Spack checkout.

Reference prefix from the focused hermetic run:

```text
/vaso/cache/spack/opt/spack/linux-icelake/py-urllib3-2.6.3-2etwtx236g5ucvgbhgjagnouggocqyoa
```

Hermetic recipe path observed from the installed prefix:

```text
/vaso/cache/spack/opt/spack/linux-icelake/py-urllib3-2.6.3-2etwtx236g5ucvgbhgjagnouggocqyoa/.spack/repos/spack_repo/builtin/packages/py_urllib3/package.py
```

Recipe facts from hermetic Spack `v1.2.2`:

- package class: `PyUrllib3(PythonPackage)`
- selected build system: `python_pip`
- version: `2.6.3`
- source payload: PyPI source archive
  `https://files.pythonhosted.org/packages/source/u/urllib3/urllib3-2.6.3.tar.gz`
- source SHA256:
  `1b62b6884944a57dbe321509ab94fd4d3b307075e0c2eae991ac71ee15ad38ed`
- homepage: `https://urllib3.readthedocs.io/`
- license: `MIT`
- concrete build dependencies: `py-hatchling@1.6:1`,
  `py-hatch-vcs@0.4:0.5`, `py-setuptools-scm@8`, `py-pip`, and `py-wheel`
- concrete build/run dependencies: `python@3.9:` and `python-venv`
- active variants: `brotli=false`, `socks=false`
- historical `secure` dependencies are inactive for `@2.6.3`
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
  --prefix=<py-urllib3-prefix> \
  .
```

The hermetic build log reported:

```text
Using pip 26.1.2 from <py-pip-prefix>/lib/python3.13/site-packages/pip (python 3.13)
Created wheel for urllib3: filename=urllib3-2.6.3-py3-none-any.whl size=131310 sha256=93e22cc155bd99ae327e53f8e2b818733e9ee46d957766fb9afac7091d5fd002
Successfully installed urllib3-2.6.3
```

Generated installation metadata that embeds the temporary build path or
complete wheel file list, such as `direct_url.json`, `RECORD`, and bytecode, is
not part of the stable prefix contract.

## Native build recipe

`native/py_urllib3/py_urllib3.bzl` mirrors that install method directly:

```text
download and extract the exact urllib3-2.6.3 source archive by SHA256
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
validate python-venv and every Python package prefix under lib/python${PYTHON_ABI}
clear PYTHONHOME
set HATCH_METADATA_CLASSIFIERS_NO_VERIFY=1
set PYTHONPATH from ABI-derived hatch-vcs, hatchling, packaging, pathspec, pip,
  pluggy, setuptools-scm, setuptools, trove-classifiers, wheel, and python-venv
PYTHON_VENV_PREFIX/bin/python3 -m pip -vvv install \
  --no-deps --ignore-installed --no-build-isolation \
  --no-warn-script-location --no-index --prefix=<native-prefix> .
validate selected urllib3 modules, dist-info metadata, and license under lib/python${PYTHON_ABI}
emit prefix_path.txt
```

The rule refuses to evaluate unless `VASO_IN_INSULA=1`, so the wheel build and
prefix install happen only inside the sealed CUDA rootfs. It never searches for
host Spack; all Spack facts come from the Bazel-vendored Spack run, and all
Python packaging tools come from Bazel-native prefixes.

The mechanism verifier is expected to report this build channel:

```text
native/py_urllib3/py_urllib3.bzl: python-pip-install: PYTHON_VENV_PREFIX, PY_HATCHLING_PREFIX, PY_HATCH_VCS_PREFIX, PY_PACKAGING_PREFIX, PY_PATHSPEC_PREFIX, PY_PIP_PREFIX, PY_PLUGGY_PREFIX, PY_SETUPTOOLS_PREFIX, PY_SETUPTOOLS_SCM_PREFIX, PY_TROVE_CLASSIFIERS_PREFIX, PY_WHEEL_PREFIX
```

## Prefix and behavior contract

The stable prefix surface is:

- selected importable modules under `lib/python3.13/site-packages/urllib3`
- `urllib3/py.typed`
- metadata and license under
  `lib/python3.13/site-packages/urllib3-2.6.3.dist-info`

Generated installation metadata that embeds the temporary build path or
complete wheel file list, such as `direct_url.json`, `RECORD`, and bytecode, is
not part of the stable parity surface. The package installs no ELF objects, so
the ABI axis is expected to be empty.

## ABI and behavior gate

The smoke target is:

```text
//synthetic:use_py_urllib3_native
```

It resolves native `py-urllib3`, native Python, and native Python venv, imports
`urllib3`, verifies the installed metadata version, and checks representative
runtime surfaces: `Retry`, `Timeout`, and URL parsing.

The parity target is:

```text
//synthetic:py_urllib3_prefix_parity
```

It compares `@py_urllib3_native//:prefix` against the hermetic Spack reference
prefix and covers:

- selected layout parity for package modules, dist-info metadata, and license;
- byte-identical representative package files and metadata;
- empty ELF ABI axis.

Focused 3.13 proof:

```text
$VASO_ESTATE_ROOT/agents/trae/logs/py-urllib3-focused-insula-20260930T045244Z.log
```

## ODR-sensitive provider invariant

This slice does not add protobuf, gRPC, Abseil, or Boost providers. Those
families must stay on one unified compatible concrete version family across
all companion packages before any native flip. The generator rejects
unqualified overrides and rejects mixed concrete family versions, including
protobuf/Python protobuf and gRPC/gRPC C++ pairings.
