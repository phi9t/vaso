# py-six native recipe

## Position in the hillclimb

`py-six@1.17.0` is the next pure `PythonPackage` source build after native
`py-pluggy` in the lean `py-torch` frontier:

```text
113  py-kiwisolver  1.5.0   python_pip  native
114  py-pluggy      1.6.0   python_pip  native
115  py-six         1.17.0  python_pip
```

The focused reference graph for `SPACK_ROOT_PKG='py-six'` ends with 36 nodes.
Spack still owns the DAG shape; the native flip changes only
`spack_py_six.build` to `native` and re-exports `@py_six_native//:lib`. The
generated link/runtime edges remain Spack-derived.

## Spack evidence

All evidence here comes from Bazel's vendored `@spack_dist//:spack` release
running inside the CUDA insula. Do not use an ambient host Spack checkout.

Reference prefix from the focused hermetic run:

```text
/vaso/cache/spack/opt/spack/linux-icelake/py-six-1.17.0-hevnf4qdvyxdy6c75btlyseworymxi5s
```

Dependency prefixes from the same build environment:

```text
/vaso/cache/spack/opt/spack/linux-icelake/py-pip-26.1.2-sc3kgksmvkl72426pp2a56yvs2yrlnhp
/vaso/cache/spack/opt/spack/linux-icelake/py-setuptools-79.0.1-4ypbhzgnpjsizucrrgjdepf56pgze6iq
/vaso/cache/spack/opt/spack/linux-icelake/py-wheel-0.45.1-bx5xhrlfm5ynhkqz6iu7sy52wrjnxa2r
/vaso/cache/spack/opt/spack/linux-icelake/python-venv-1.0-4wd5zbf6opf7he65hsne2a6evpwuq4o3
/vaso/cache/spack/opt/spack/linux-icelake/python-3.13.13-micqjwdjbkfwhcggzvsshifmka7h7z64
```

Hermetic recipe path observed from the installed prefix:

```text
/vaso/cache/spack/opt/spack/linux-icelake/py-six-1.17.0-hevnf4qdvyxdy6c75btlyseworymxi5s/.spack/repos/spack_repo/builtin/packages/py_six/package.py
```

Recipe facts from hermetic Spack `v1.2.2`:

- package class: `PySix(PythonPackage)`
- selected build system: `python_pip`
- version: `1.17.0`
- source payload: PyPI source archive
  `https://files.pythonhosted.org/packages/source/s/six/six-1.17.0.tar.gz`
- source SHA256:
  `ff70335d468e7eb6ec65b95b99d3a2836546063f63acc5171de367e834932a81`
- dependencies: `python` build/run, `python-venv` build/run, plus
  `py-pip`, `py-setuptools`, and `py-wheel` build inputs
- patches: none
- install command:

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
  --prefix=<py-six-prefix> \
  .
```

Spack supplies `pip` by putting the `py-pip` prefix on `PYTHONPATH`; the build
log reports:

```text
Using pip 26.1.2 from <py-pip-prefix>/lib/python3.13/site-packages/pip (python 3.13)
```

Pip builds an intermediate universal wheel from the source tree, then installs
that wheel into the prefix. The generated wheel hash is not stable across build
directories and is not part of the prefix contract.

## Native build recipe

`native/py_six/py_six.bzl` mirrors that install method directly:

```text
download and extract the exact six-1.17.0 source archive by SHA256
read @python_venv_native//:prefix_path.txt
read @py_pip_native//:prefix_path.txt
read @py_setuptools_native//:prefix_path.txt
read @py_wheel_native//:prefix_path.txt
validate PYTHON_VENV_PREFIX/bin/python3
derive PYTHON_ABI from PYTHON_VENV_PREFIX/bin/python3
validate PYTHON_VENV_PREFIX/bin/python${PYTHON_ABI}
validate PY_PIP_PREFIX/lib/python${PYTHON_ABI}/site-packages/pip
validate PY_SETUPTOOLS_PREFIX/lib/python${PYTHON_ABI}/site-packages/setuptools
validate PY_WHEEL_PREFIX/lib/python${PYTHON_ABI}/site-packages/wheel
clear PYTHONHOME
set PYTHONPATH from native py-pip, py-setuptools, py-wheel, and python-venv
  prefixes under lib/python${PYTHON_ABI}/site-packages
cd <source>
PYTHON_VENV_PREFIX/bin/python3 -m pip -vvv install \
  --no-deps --ignore-installed --no-build-isolation \
  --no-warn-script-location --no-index --prefix=<native-prefix> .
validate six.py and stable dist-info metadata
emit prefix_path.txt
```

The rule refuses to evaluate unless `VASO_IN_INSULA=1`, so both the local wheel
build and the prefix install happen only inside the sealed CUDA rootfs. It
never searches `PATH` for Python or pip; Python comes from the Bazel-native
venv prefix and Python packaging tools come from Bazel-native prefixes on
`PYTHONPATH`.

The mechanism verifier reports the expected build channel:

```text
native/py_six/py_six.bzl: python-pip-install: PYTHON_VENV_PREFIX, PY_PIP_PREFIX, PY_SETUPTOOLS_PREFIX, PY_WHEEL_PREFIX
```

That verifier checks every prefix-file input, `python -m pip` invocation
through `PYTHON_VENV_PREFIX`, `PY_PIP_PREFIX` exposure on `PYTHONPATH`,
Spack's no-network/no-build-isolation/no-deps flags, `--prefix`, and clearing
`PYTHONHOME`.

## Prefix and behavior contract

The ABI-relevant prefix surface is a pure Python package prefix:

- package module: `lib/python3.13/site-packages/six.py`
- metadata: `lib/python3.13/site-packages/six-1.17.0.dist-info`
- license: `lib/python3.13/site-packages/six-1.17.0.dist-info/licenses/LICENSE`

Generated installation metadata that embeds the temporary Spack stage path or
complete wheel file list, such as `direct_url.json`, `RECORD`, and bytecode, is
not part of the stable parity surface.

## ABI and behavior gate

The smoke target is:

```text
//synthetic:use_py_six_native
```

It resolves the native `py-six`, `python`, `python-venv`, `py-pip`,
`py-setuptools`, and `py-wheel` prefix markers, sets the native Python library
path and ABI-derived package `PYTHONPATH`, asserts Python ABI `3.13`, imports
`six`, verifies the installed version through `importlib.metadata`, checks a
representative compatibility helper, and expects:

```text
py-six:1.17.0:ok:42
```

The parity target is:

```text
//synthetic:py_six_prefix_parity
```

It compares `@py_six_native//:prefix` against the hermetic Spack reference
prefix and covers:

- selected layout parity for stable package files, `.dist-info` metadata, and
  license;
- byte-identical package module and representative metadata files;
- empty ELF ABI axis, since the package installs no native shared libraries.

Current status: native `py-six` is gated inside the hermetic CUDA insula. The
focused verification command was:

```bash
env PATH=/usr/bin:/bin:$HOME/.local/bin \
BAZEL_BIN=$HOME/.local/bin/bazel-9.2.0 \
VASO_ESTATE_ROOT=$VASO_ESTATE_ROOT \
SPACK_ROOT_PKG='py-six' \
VASO_NATIVE=1 \
VASO_FORMAL=0 \
VASO_SKIP_CONSUMER_TESTS=1 \
VASO_SKIP_NATIVE_ABI_GATES=1 \
VASO_FORCE_FETCH_REPOS='@py_six_native' \
VASO_EXTRA_TEST_TARGETS='//tools:hermetic_native_deps_guard_test,//synthetic:use_py_six_native,//synthetic:py_six_prefix_parity' \
./run.sh
```

That run used Bazel's `@spack_dist//:spack` inside rootfs mode `cuda-bundle`,
reported hermetic Spack version `1.2.2`, regenerated `spack_graph.lock.json`
with root `spack_py_six`, and selected Python `3.13.13`.

Final package proof used the same estate and rootfs with the constrained root:

```bash
SPACK_ROOT_PKG='py-six@1.17.0 ^python@3.13.13+bz2+ctypes+dbm~debug+libxml2+lzma~optimizations+pic+pyexpat+pythoncmd+readline+shared+sqlite3+ssl~static~tkinter+uuid+zlib ^python-venv@1.0 ^py-pip@26.1.2 ^py-setuptools@79.0.1 ^py-wheel@0.45.1' \
VASO_NATIVE=1 \
VASO_SPACK_FRESH=1 \
VASO_SPACK_TIMEOUT=900 \
VASO_SKIP_CONSUMER_TESTS=1 \
VASO_SKIP_NATIVE_ABI_GATES=1 \
VASO_EXTRA_TEST_TARGETS='//synthetic:use_py_six_native,//synthetic:py_six_prefix_parity,//tools:hermetic_native_deps_guard_test,//tools:native_dep_wiring_live_test' \
./run.sh
```

Log:
`$VASO_ESTATE_ROOT/agents/trae/logs/py-six-insula-proof-final-20260930T003305Z.log`.
It passed `//synthetic:use_py_six_native`,
`//synthetic:py_six_prefix_parity`, `//tools:hermetic_native_deps_guard_test`,
and `//tools:native_dep_wiring_live_test` with zero allowlisted native-dep
wiring mismatches.
