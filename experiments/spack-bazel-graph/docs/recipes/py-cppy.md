# py-cppy native recipe

## Position in the hillclimb

`py-cppy@1.3.1` is the next `PythonPackage` source build after native
`py-setuptools-scm` in the lean `py-torch` frontier:

```text
110  py-pyyaml           6.0.3  python_pip  native
111  py-setuptools-scm   8.2.1  python_pip  native
112  py-cppy             1.3.1  python_pip
```

The focused reference graph for `SPACK_ROOT_PKG='py-cppy'` ends with 57 nodes.
Spack still owns the DAG shape; the native flip changes only
`spack_py_cppy.build` to `native` and re-exports `@py_cppy_native//:lib`. The
generated link/runtime edges remain Spack-derived.

## Spack evidence

All evidence here comes from Bazel's vendored `@spack_dist//:spack` release
running inside the CUDA insula. Do not use an ambient host Spack checkout.

Reference prefix from the focused hermetic run:

```text
/vaso/cache/spack/opt/spack/linux-icelake/py-cppy-1.3.1-ocia6gcafvdzn5sowrf76tlfj2asldse
```

Dependency prefixes from the same build environment:

```text
/vaso/cache/spack/opt/spack/linux-icelake/git-2.52.0-h5dvnizwmfqiyvjdh4yjstgojvjs5tuq
/vaso/cache/spack/opt/spack/linux-icelake/py-packaging-26.2-yt776uherxtsipfvv766cdsrj2zyrtbm
/vaso/cache/spack/opt/spack/linux-icelake/py-pip-26.1.2-uhzkdfzfb2i6lrqfrzd6arodvbwn7vwy
/vaso/cache/spack/opt/spack/linux-icelake/py-setuptools-79.0.1-amtimju3srqbl5rrw23fdcf4fyus2bhi
/vaso/cache/spack/opt/spack/linux-icelake/py-setuptools-scm-8.2.1-gvv2bvlsqfpqji7nqgpzh3reddpgwcb4
/vaso/cache/spack/opt/spack/linux-icelake/py-wheel-0.45.1-yzp43gtv6u2kqaxiyeit3jvutyu7mxub
/vaso/cache/spack/opt/spack/linux-icelake/python-venv-1.0-aiafo5jstbj56qrf2ezsa2stsdb4po43
/vaso/cache/spack/opt/spack/linux-icelake/python-3.14.5-slorb5hlbuw4ew2hjum37l3xm7gf74s6
```

Hermetic recipe path observed from the installed prefix:

```text
/vaso/cache/spack/opt/spack/linux-icelake/py-cppy-1.3.1-ocia6gcafvdzn5sowrf76tlfj2asldse/.spack/repos/spack_repo/builtin/packages/py_cppy/package.py
```

Recipe facts from hermetic Spack `v1.2.2`:

- package class: `PyCppy(PythonPackage)`
- selected build system: `python_pip`
- version: `1.3.1`
- source payload: PyPI source archive
  `https://files.pythonhosted.org/packages/source/c/cppy/cppy-1.3.1.tar.gz`
- source SHA256:
  `55b5307c11874f242ea135396f398cb67a5bbde4fab3e3c3294ea5fce43a6d68`
- active dependencies: `py-pip` build, `py-setuptools` build/run,
  `py-setuptools-scm` build, `py-wheel` build, `python` build/run, and
  `python-venv` build/run
- build-backend transitive dependency captured explicitly by the native rule:
  `py-packaging`, because `py-setuptools-scm` imports `packaging`
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
  --prefix=<py-cppy-prefix> \
  .
```

Spack supplies `pip`, `setuptools`, `setuptools-scm`, `wheel`, `packaging`,
and `python-venv` by putting their prefixes on `PYTHONPATH`, and it supplies
`git` by putting the selected prefix on `PATH`.

The installed wheel metadata says:

```text
Generator: setuptools (79.0.1)
Root-Is-Purelib: true
Tag: py3-none-any
Requires-Dist: setuptools>=61.2
```

Generated installation metadata that embeds temporary stage paths or complete
wheel file lists, such as `direct_url.json`, `RECORD`, and bytecode, is not
part of the stable parity surface.

## Native build recipe

`native/py_cppy/py_cppy.bzl` mirrors that install method directly:

```text
download and extract the exact cppy-1.3.1 source archive by SHA256
read @git_native//:prefix_path.txt
read @python_venv_native//:prefix_path.txt
read @py_packaging_native//:prefix_path.txt
read @py_pip_native//:prefix_path.txt
read @py_setuptools_native//:prefix_path.txt
read @py_setuptools_scm_native//:prefix_path.txt
read @py_wheel_native//:prefix_path.txt
validate GIT_PREFIX/bin/git
validate PYTHON_VENV_PREFIX/bin/python3
validate py-pip, py-setuptools, py-setuptools-scm, py-wheel, and py-packaging site-packages
clear PYTHONHOME
set PYTHONPATH from native py-pip, py-setuptools, py-setuptools-scm, py-wheel, py-packaging, and python-venv
set PATH from native git, pip, wheel, and python-venv prefixes
cd <source>
PYTHON_VENV_PREFIX/bin/python3 -m pip -vvv install \
  --no-deps --ignore-installed --no-build-isolation \
  --no-warn-script-location --no-index --prefix=<native-prefix> .
validate cppy package files, C++ headers, dist-info metadata, and license
emit prefix_path.txt
```

The rule refuses to evaluate unless `VASO_IN_INSULA=1`, so the wheel build and
prefix install happen only inside the sealed CUDA rootfs. It never searches for
Python, pip, setuptools, setuptools-scm, wheel, packaging, or git on the host;
all inputs are Bazel-native prefixes.

The mechanism verifier reports the expected build channel:

```text
native/py_cppy/py_cppy.bzl: python-pip-install: GIT_PREFIX, PYTHON_VENV_PREFIX, PY_PACKAGING_PREFIX, PY_PIP_PREFIX, PY_SETUPTOOLS_PREFIX, PY_SETUPTOOLS_SCM_PREFIX, PY_WHEEL_PREFIX
```

That verifier checks every prefix-file input, the `python -m pip` invocation
through `PYTHON_VENV_PREFIX`, `PY_PIP_PREFIX` exposure on `PYTHONPATH`,
Spack's no-network/no-build-isolation/no-deps flags, `--prefix`, and clearing
`PYTHONHOME`.

## Prefix and behavior contract

The stable prefix surface includes:

- package tree: `lib/python3.14/site-packages/cppy`
- C++ headers:
  `lib/python3.14/site-packages/cppy/include/cppy/{cppy.h,defines.h,errors.h,ptr.h}`
- metadata and license: `cppy-1.3.1.dist-info`

The package installs no ELF objects, so the ABI axis is expected to be empty.

## ABI and behavior gate

The smoke target is:

```text
//synthetic:use_py_cppy_native
```

It resolves the native `py-cppy`, `python`, `python-venv`, `py-pip`,
`py-setuptools`, `py-setuptools-scm`, and `py-wheel` prefix markers, sets the
native Python library path and package `PYTHONPATH`, imports `cppy`, validates
`cppy.get_include()`, and checks that the installed header set contains
`cppy.h` and the `class ptr` wrapper in `ptr.h`. Expected output:

```text
py-cppy:1.3.1:1.3.1:cppy.h:class_ptr
```

The parity target is:

```text
//synthetic:py_cppy_prefix_parity
```

It compares `@py_cppy_native//:prefix` against the hermetic Spack reference
prefix and covers:

- selected layout parity for all stable package files, headers, metadata, and
  license;
- byte-identical package modules, headers, and representative metadata;
- empty ELF ABI axis, since the package installs no native shared libraries.

Current status: native `py-cppy` is gated inside the hermetic CUDA insula. The
focused verification command was:

```bash
env PATH=/usr/bin:/bin:$HOME/.local/bin \
BAZEL_BIN=$HOME/.local/bin/bazel-9.2.0 \
VASO_ESTATE_ROOT=$VASO_ESTATE_ROOT \
SPACK_ROOT_PKG='py-cppy' \
VASO_NATIVE=1 \
VASO_FORMAL=0 \
VASO_SKIP_CONSUMER_TESTS=1 \
VASO_SKIP_NATIVE_ABI_GATES=1 \
VASO_EXTRA_TEST_TARGETS='//tools:hermetic_native_deps_guard_test,//synthetic:use_py_cppy_native,//synthetic:py_cppy_prefix_parity' \
VASO_SPACK_TIMEOUT=2400 \
./run.sh
```

That run used Bazel's `@spack_dist//:spack` inside rootfs mode `cuda-bundle`,
reported hermetic Spack version `1.2.2`, regenerated `spack_graph.lock.json`
with root `spack_py_cppy`, passed `//tools:hermetic_native_deps_guard_test`,
passed `//synthetic:use_py_cppy_native`, and passed
`//synthetic:py_cppy_prefix_parity`.
