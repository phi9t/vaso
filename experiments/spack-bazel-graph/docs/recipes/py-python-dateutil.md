# py-python-dateutil native recipe

## Position in the hillclimb

`py-python-dateutil@2.9.0.post0` is the next pure `PythonPackage` source build
after native `py-protobuf` in the lean `py-torch` frontier:

```text
115  py-six              1.17.0        python_pip  native
116  py-protobuf         3.13.0        python_pip  native
117  py-python-dateutil  2.9.0.post0  python_pip
```

The focused reference graph for `SPACK_ROOT_PKG='py-python-dateutil'` has 58
nodes. Spack still owns the DAG shape; the native flip changes only
`spack_py_python_dateutil.build` to `native` and re-exports
`@py_python_dateutil_native//:lib`. The generated link/runtime edges remain
Spack-derived.

## Spack evidence

All evidence here comes from Bazel's vendored `@spack_dist//:spack` release
running inside the CUDA insula. Do not use an ambient host Spack checkout.

Reference prefix from the focused hermetic run:

```text
/vaso/cache/spack/opt/spack/linux-icelake/py-python-dateutil-2.9.0.post0-nygl5jfgluxj27h2c4h2e2sxrci6hh3k
```

Hermetic recipe path observed from the installed prefix:

```text
/vaso/cache/spack/opt/spack/linux-icelake/py-python-dateutil-2.9.0.post0-nygl5jfgluxj27h2c4h2e2sxrci6hh3k/.spack/repos/spack_repo/builtin/packages/py_python_dateutil/package.py
```

Recipe facts from hermetic Spack `v1.2.2`:

- package class: `PyPythonDateutil(PythonPackage)`
- selected build system: `python_pip`
- version: `2.9.0.post0`
- source payload: PyPI source archive
  `https://files.pythonhosted.org/packages/source/p/python-dateutil/python-dateutil-2.9.0.post0.tar.gz`
- source SHA256:
  `37dd54208da7e1cd875388217d5e00ebd4179249f90fb72437e91a35459a0ad3`
- dependencies: `python` and `python-venv` build/run; `py-six@1.5:` build/run;
  plus `py-pip`, `py-setuptools`, `py-setuptools-scm`, and `py-wheel` build
  inputs
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
  --prefix=<py-python-dateutil-prefix> \
  .
```

Pip builds an intermediate universal wheel from the source tree, then installs
that wheel into the prefix. The generated wheel hash and temporary staging
paths are not part of the prefix contract.

## Native build recipe

`native/py_python_dateutil/py_python_dateutil.bzl` mirrors that install method
directly:

```text
download and extract the exact python-dateutil-2.9.0.post0 source archive by SHA256
read @python_venv_native//:prefix_path.txt
read @py_pip_native//:prefix_path.txt
read @py_setuptools_native//:prefix_path.txt
read @py_setuptools_scm_native//:prefix_path.txt
read @py_six_native//:prefix_path.txt
read @py_wheel_native//:prefix_path.txt
validate PYTHON_VENV_PREFIX/bin/python3
validate native pip, setuptools, setuptools-scm, six, and wheel site-package payloads
clear PYTHONHOME
set PYTHONPATH from native py-pip, py-setuptools, py-setuptools-scm, py-six, py-wheel, and python-venv
PYTHON_VENV_PREFIX/bin/python3 -m pip -vvv install \
  --no-deps --ignore-installed --no-build-isolation \
  --no-warn-script-location --no-index --prefix=<native-prefix> .
validate dateutil modules, zoneinfo payload, and stable dist-info metadata
emit prefix_path.txt
```

The rule refuses to evaluate unless `VASO_IN_INSULA=1`, so the wheel build and
prefix install happen only inside the sealed CUDA rootfs. It never searches
`PATH` for Python or pip; Python comes from the Bazel-native venv prefix and
Python packaging tools come from Bazel-native prefixes on `PYTHONPATH`.

The mechanism verifier reports the expected build channel:

```text
native/py_python_dateutil/py_python_dateutil.bzl: python-pip-install: PYTHON_VENV_PREFIX, PY_PIP_PREFIX, PY_SETUPTOOLS_PREFIX, PY_SETUPTOOLS_SCM_PREFIX, PY_SIX_PREFIX, PY_WHEEL_PREFIX
```

## Prefix and behavior contract

The stable prefix surface is:

- package modules under `lib/python3.14/site-packages/dateutil`
- bundled zoneinfo payload
  `lib/python3.14/site-packages/dateutil/zoneinfo/dateutil-zoneinfo.tar.gz`
- metadata and license under
  `lib/python3.14/site-packages/python_dateutil-2.9.0.post0.dist-info`

Generated installation metadata that embeds the temporary build path or complete
wheel file list, such as `direct_url.json`, `RECORD`, and bytecode, is not part
of the stable parity surface.

## ABI and behavior gate

The smoke target is:

```text
//synthetic:use_py_python_dateutil_native
```

It resolves the native `py-python-dateutil`, native Python packaging prefixes,
and native `py-six`, imports `dateutil.parser`, `dateutil.relativedelta`, and
`dateutil.tz`, verifies the installed metadata version, and expects:

```text
py-python-dateutil:2.9.0.post0:UTC:UTC:28
```

The parity target is:

```text
//synthetic:py_python_dateutil_prefix_parity
```

It compares `@py_python_dateutil_native//:prefix` against the hermetic Spack
reference prefix and covers:

- selected layout parity for stable Python modules, zoneinfo data,
  `.dist-info` metadata, and license;
- byte-identical representative package files and metadata;
- empty ELF ABI axis, since the package installs no native shared libraries.

Current status: native `py-python-dateutil` is gated inside the hermetic CUDA
insula. This command uses Bazel 9.2.0, estate root
`$VASO_ESTATE_ROOT`, and the focused root
`SPACK_ROOT_PKG='py-python-dateutil'`:

```bash
env PATH=/usr/bin:/bin:$HOME/.local/bin \
  BAZEL_BIN=$HOME/.local/bin/bazel-9.2.0 \
  VASO_ESTATE_ROOT=$VASO_ESTATE_ROOT \
  SPACK_ROOT_PKG='py-python-dateutil' \
  VASO_NATIVE=1 \
  VASO_FORMAL=0 \
  VASO_SKIP_CONSUMER_TESTS=1 \
  VASO_SKIP_NATIVE_ABI_GATES=1 \
  VASO_FORCE_FETCH_REPOS='@py_python_dateutil_native' \
  VASO_EXTRA_TEST_TARGETS='//tools:hermetic_native_deps_guard_test,//synthetic:use_py_python_dateutil_native,//synthetic:py_python_dateutil_prefix_parity' \
  ./run.sh
```

It ran entirely inside the CUDA bundle rootfs, forced
`@py_python_dateutil_native`, regenerated the focused lock with root
`spack_py_python_dateutil`, passed
`//tools:hermetic_native_deps_guard_test`, passed
`//synthetic:use_py_python_dateutil_native` with output
`py-python-dateutil:2.9.0.post0:UTC:UTC:28`, and passed
`//synthetic:py_python_dateutil_prefix_parity`.

The parity verdict compared the hermetic Spack reference prefix

```text
/vaso/cache/spack/opt/spack/linux-icelake/py-python-dateutil-2.9.0.post0-nygl5jfgluxj27h2c4h2e2sxrci6hh3k
```

against the native Bazel prefix

```text
/vaso/cache/bazel/output-base/external/+py_python_dateutil_native+py_python_dateutil_native/prefix
```

and reported `ok: true`, layout count 13/13, byte-identical SHA256 values for
all selected stable files, and an empty successful ELF ABI axis.
