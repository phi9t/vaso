# py-requests native recipe

## Position in the hillclimb

`py-requests@2.33.1` is the next `PythonPackage` source build after native
`py-urllib3` in the lean `py-torch` frontier:

```text
111  py-urllib3   2.6.3   python_pip  native
112  py-requests  2.33.1  python_pip
113  re2c         4.4     autotools
```

The focused reference graph for `SPACK_ROOT_PKG='py-requests'` has 67
build-graph nodes and uses 39 `autotools`, 12 `generic`, 2 `makefile`, and 14
`python_pip` build-system nodes. Spack still owns the DAG shape; the native
flip changes only `spack_py_requests.build` to `native` and re-exports
`@py_requests_native//:lib`.

## Spack evidence

All evidence here comes from Bazel's vendored `@spack_dist//:spack` release
running inside the CUDA insula. Do not use an ambient host Spack checkout.

Reference prefix from the focused hermetic run:

```text
/vaso/cache/spack/opt/spack/linux-icelake/py-requests-2.33.1-vlx7flw3cyzgcxeatz64tjjpl26wngel
```

Hermetic recipe path observed from the installed prefix:

```text
/vaso/cache/spack/opt/spack/linux-icelake/py-requests-2.33.1-vlx7flw3cyzgcxeatz64tjjpl26wngel/.spack/repos/spack_repo/builtin/packages/py_requests/package.py
```

Recipe facts from hermetic Spack `v1.2.2`:

- package class: `PyRequests(PythonPackage)`
- selected build system: `python_pip`
- version: `2.33.1`
- source payload: PyPI source archive
  `https://files.pythonhosted.org/packages/source/r/requests/requests-2.33.1.tar.gz`
- source SHA256:
  `18817f8c57c6263968bc123d237e3b8b08ac046f5456bd1e307ee8f4250d3517`
- homepage: `https://requests.readthedocs.io`
- license: `Apache-2.0`
- concrete build dependencies: `py-pip`, `py-setuptools`, and `py-wheel`
- concrete build/run dependencies: `py-certifi`, `py-charset-normalizer`,
  `py-idna`, `py-urllib3`, `python`, and `python-venv`
- active variant: `socks=false`
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
  --prefix=<py-requests-prefix> \
  .
```

The hermetic build log reported:

```text
Using pip 26.1.2 from <py-pip-prefix>/lib/python3.13/site-packages/pip (python 3.13)
Created wheel for requests: filename=requests-2.33.1-py3-none-any.whl size=65011 sha256=5484928f7f94b571e966cd015bc825b78edc4621480449395ad88c050ed2c2ea
Successfully installed requests-2.33.1
```

Generated installation metadata that embeds the temporary build path or
complete wheel file list, such as `direct_url.json`, `RECORD`, and bytecode, is
not part of the stable prefix contract.

## Native build recipe

`native/py_requests/py_requests.bzl` mirrors that install method directly:

```text
download and extract the exact requests-2.33.1 source archive by SHA256
read @python_venv_native//:prefix_path.txt
derive PYTHON_ABI from @python_venv_native
read @py_certifi_native//:prefix_path.txt
read @py_charset_normalizer_native//:prefix_path.txt
read @py_idna_native//:prefix_path.txt
read @py_pip_native//:prefix_path.txt
read @py_setuptools_native//:prefix_path.txt
read @py_urllib3_native//:prefix_path.txt
read @py_wheel_native//:prefix_path.txt
validate python-venv and every Python package prefix under lib/python${PYTHON_ABI}
clear PYTHONHOME
set PYTHONPATH from ABI-derived certifi, charset-normalizer, idna, pip,
  setuptools, urllib3, wheel, and python-venv prefixes
PYTHON_VENV_PREFIX/bin/python3 -m pip -vvv install \
  --no-deps --ignore-installed --no-build-isolation \
  --no-warn-script-location --no-index --prefix=<native-prefix> .
validate selected requests modules, dist-info metadata, license, and notice
  under lib/python${PYTHON_ABI}
emit prefix_path.txt
```

The rule refuses to evaluate unless `VASO_IN_INSULA=1`, so the wheel build and
prefix install happen only inside the sealed CUDA rootfs. It never searches for
host Spack; all Spack facts come from the Bazel-vendored Spack run, and all
Python packaging tools and runtime Python package dependencies come from
Bazel-native prefixes.

The mechanism verifier is expected to report this build channel:

```text
native/py_requests/py_requests.bzl: python-pip-install: PYTHON_VENV_PREFIX, PY_CERTIFI_PREFIX, PY_CHARSET_NORMALIZER_PREFIX, PY_IDNA_PREFIX, PY_PIP_PREFIX, PY_SETUPTOOLS_PREFIX, PY_URLLIB3_PREFIX, PY_WHEEL_PREFIX
```

## Prefix and behavior contract

The stable prefix surface is:

- selected importable modules under `lib/python3.13/site-packages/requests`
- metadata, license, and notice under
  `lib/python3.13/site-packages/requests-2.33.1.dist-info`

Generated installation metadata that embeds the temporary build path or
complete wheel file list, such as `direct_url.json`, `RECORD`, and bytecode, is
not part of the stable parity surface. The package installs no ELF objects, so
the ABI axis is expected to be empty.

## ABI and behavior gate

The smoke target is:

```text
//synthetic:use_py_requests_native
```

It resolves native `py-requests`, native Python, native Python venv, and native
`py-certifi`, `py-charset-normalizer`, `py-idna`, and `py-urllib3`, imports
`requests`, verifies the installed metadata version, and checks representative
runtime request preparation and status-code surfaces.

The parity target is:

```text
//synthetic:py_requests_prefix_parity
```

It compares `@py_requests_native//:prefix` against the hermetic Spack reference
prefix and covers:

- selected layout parity for package modules, dist-info metadata, license, and
  notice;
- byte-identical representative package files and metadata;
- empty ELF ABI axis.

Focused 3.13 proof:

```text
$VASO_ESTATE_ROOT/agents/trae/logs/py-requests-focused-insula-20260930T050747Z.log
```

## ODR-sensitive provider invariant

This slice does not add protobuf, gRPC, Abseil, or Boost providers. Those
families must stay on one unified compatible concrete version family across
all companion packages before any native flip. The generator rejects
unqualified overrides and rejects mixed concrete family versions, including
protobuf/Python protobuf and gRPC/gRPC C++ pairings.
