# py-kiwisolver native recipe

## Position in the hillclimb

`py-kiwisolver@1.5.0` is the next `PythonPackage` source build after native
`py-cppy` in the lean `py-torch` frontier:

```text
111  py-setuptools-scm  8.2.1  python_pip  native
112  py-cppy            1.3.1  python_pip  native
113  py-kiwisolver      1.5.0  python_pip
```

The focused reference graph for `SPACK_ROOT_PKG='py-kiwisolver'` ends with 58
nodes. Spack still owns the DAG shape; the native flip changes only
`spack_py_kiwisolver.build` to `native` and re-exports
`@py_kiwisolver_native//:lib`. The generated link/runtime edges remain
Spack-derived.

## Spack evidence

All evidence here comes from Bazel's vendored `@spack_dist//:spack` release
running inside the CUDA insula. Do not use an ambient host Spack checkout.

Reference prefix from the focused hermetic run:

```text
/vaso/cache/spack/opt/spack/linux-icelake/py-kiwisolver-1.5.0-f65lpr64y6pj6fvkkvr6lfwh4mugagnk
```

Dependency prefixes from the same build environment:

```text
/vaso/cache/spack/opt/spack/linux-icelake/git-2.52.0-h5dvnizwmfqiyvjdh4yjstgojvjs5tuq
/vaso/cache/spack/opt/spack/linux-icelake/py-cppy-1.3.1-ocia6gcafvdzn5sowrf76tlfj2asldse
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
/vaso/cache/spack/opt/spack/linux-icelake/py-kiwisolver-1.5.0-f65lpr64y6pj6fvkkvr6lfwh4mugagnk/.spack/repos/spack_repo/builtin/packages/py_kiwisolver/package.py
```

Recipe facts from hermetic Spack `v1.2.2`:

- package class: `PyKiwisolver(PythonPackage)`
- selected build system: `python_pip`
- version: `1.5.0`
- source payload: PyPI source archive
  `https://files.pythonhosted.org/packages/source/k/kiwisolver/kiwisolver-1.5.0.tar.gz`
- source SHA256:
  `d4193f3d9dc3f6f79aaed0e5637f45d98850ebf01f7ca20e69457f3e8946b66a`
- active build dependencies: C, C++, `py-cppy@1.3.0:`,
  `py-setuptools@61.2:`, and `py-setuptools-scm@3.4.3:+toml`
- active build/run dependency: `python@3.10:`
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
  --prefix=<py-kiwisolver-prefix> \
  .
```

Spack supplies `pip`, `setuptools`, `setuptools-scm`, `wheel`, `packaging`,
`cppy`, and `python-venv` by putting their prefixes on `PYTHONPATH`, and it
supplies `git` by putting the selected prefix on `PATH`.

The package builds one CPython extension module:

```text
lib/python3.14/site-packages/kiwisolver/_cext.cpython-314-x86_64-linux-gnu.so
```

Spack's visible extension compile line uses the compiler wrapper as `CXX` and
keeps Python's sysconfig extension flags:

```text
<compiler-wrapper>/libexec/spack/gcc/g++ \
  -fno-strict-overflow -Wsign-compare -DNDEBUG -g -O3 -Wall -fPIC -fPIC \
  -I<py-cppy>/lib/python3.14/site-packages/cppy/include \
  -I. -I<python-venv>/include -I<python>/include/python3.14 \
  -c py/src/constraint.cpp ... -std=c++11
```

The wrapper also injects Spack's target args:

```text
SPACK_TARGET_ARGS_CXX='-march=icelake-client -mtune=icelake-client'
```

Generated installation metadata that embeds temporary stage paths or complete
wheel file lists, such as `direct_url.json`, `RECORD`, and bytecode, is not
part of the stable parity surface.

## Native build recipe

`native/py_kiwisolver/py_kiwisolver.bzl` mirrors that install method directly:

```text
download and extract the exact kiwisolver-1.5.0 source archive by SHA256
read @git_native//:prefix_path.txt
read @python_native//:prefix_path.txt
read @python_venv_native//:prefix_path.txt
read @py_cppy_native//:prefix_path.txt
read @py_packaging_native//:prefix_path.txt
read @py_pip_native//:prefix_path.txt
read @py_setuptools_native//:prefix_path.txt
read @py_setuptools_scm_native//:prefix_path.txt
read @py_wheel_native//:prefix_path.txt
validate git, python, python-venv, cppy, pip, setuptools, setuptools-scm, wheel, and packaging prefixes
clear PYTHONHOME
set PYTHONPATH from native py-pip, py-setuptools, py-packaging, py-setuptools-scm, py-wheel, py-cppy, and python-venv
set PATH from native git, pip, wheel, python-venv, and python prefixes
set CC to /usr/bin/gcc
set CXX to a Bazel-local wrapper that execs /usr/bin/g++ with Spack target args
cd <source>
PYTHON_VENV_PREFIX/bin/python3 -m pip -vvv install \
  --no-deps --ignore-installed --no-build-isolation \
  --no-warn-script-location --no-index --prefix=<native-prefix> .
validate package files, extension module, dist-info metadata, and license
emit prefix_path.txt
```

Ruling: the native recipe injects `-march=icelake-client
-mtune=icelake-client` through the `CXX` executable boundary, not through
`CXXFLAGS`. Setting `CXXFLAGS` directly replaces Python's default extension
flags in this build path and drops `-fno-strict-overflow -Wsign-compare
-DNDEBUG -g -O3 -Wall`, which changes the exported dynamic symbol set. A tiny
wrapper matches Spack's compiler-wrapper behavior: Python still supplies its
own extension flags, while the compiler invocation receives Spack's target
args.

The rule refuses to evaluate unless `VASO_IN_INSULA=1`, so the wheel build and
prefix install happen only inside the sealed CUDA rootfs. It never searches for
Spack, Python packages, or git on the host; all inputs are Bazel-native prefix
markers or the hermetic system compiler path inside the insula.

The mechanism verifier reports the expected build channel:

```text
native/py_kiwisolver/py_kiwisolver.bzl: python-pip-install: GIT_PREFIX, PYTHON_PREFIX, PYTHON_VENV_PREFIX, PY_CPPY_PREFIX, PY_PACKAGING_PREFIX, PY_PIP_PREFIX, PY_SETUPTOOLS_PREFIX, PY_SETUPTOOLS_SCM_PREFIX, PY_WHEEL_PREFIX
```

That verifier checks every prefix-file input, the `python -m pip` invocation
through `PYTHON_VENV_PREFIX`, `PY_PIP_PREFIX` exposure on `PYTHONPATH`,
Spack's no-network/no-build-isolation/no-deps flags, `--prefix`, and clearing
`PYTHONHOME`.

## Prefix and behavior contract

The stable prefix surface includes:

- package tree: `lib/python3.14/site-packages/kiwisolver`
- extension module:
  `lib/python3.14/site-packages/kiwisolver/_cext.cpython-314-x86_64-linux-gnu.so`
- typing and support files:
  `kiwisolver/_cext.pyi`, `kiwisolver/exceptions.py`, and `kiwisolver/py.typed`
- metadata and license: `kiwisolver-1.5.0.dist-info`

The extension's ABI surface is explicit and must match the hermetic Spack
reference for NEEDED libraries, null SONAME, and exported dynamic symbols.

## ABI and behavior gate

The smoke target is:

```text
//synthetic:use_py_kiwisolver_native
```

It resolves the native `py-kiwisolver`, `python`, `python-venv`, `py-cppy`,
`py-pip`, `py-setuptools`, `py-setuptools-scm`, and `py-wheel` prefix markers,
sets the native Python library path and package `PYTHONPATH`, imports
`kiwisolver`, checks version metadata, creates two variables, solves
`x + y == 10` with `x == 3`, and expects:

```text
py-kiwisolver:1.5.0:1.5.0:3:7
```

The parity target is:

```text
//synthetic:py_kiwisolver_prefix_parity
```

It compares `@py_kiwisolver_native//:prefix` against the hermetic Spack
reference prefix and covers:

- selected layout parity for all stable package files, metadata, and license;
- byte-identical pure Python files, stubs, representative metadata, and
  license;
- explicit ELF parity for `_cext.cpython-314-x86_64-linux-gnu.so`, including
  matching NEEDED entries `libc.so.6`, `libgcc_s.so.1`, and `libstdc++.so.6`,
  null SONAME parity, and 117 exported symbols with no additions or removals.

Current status: native `py-kiwisolver` is gated inside the hermetic CUDA
insula. The focused verification command was:

```bash
env PATH=/usr/bin:/bin:$HOME/.local/bin \
BAZEL_BIN=$HOME/.local/bin/bazel-9.2.0 \
VASO_ESTATE_ROOT=$VASO_ESTATE_ROOT \
SPACK_ROOT_PKG='py-kiwisolver' \
VASO_NATIVE=1 \
VASO_FORMAL=0 \
VASO_SKIP_CONSUMER_TESTS=1 \
VASO_SKIP_NATIVE_ABI_GATES=1 \
VASO_FORCE_FETCH_REPOS='@py_kiwisolver_native' \
VASO_EXTRA_TEST_TARGETS='//synthetic:use_py_kiwisolver_native,//synthetic:py_kiwisolver_prefix_parity' \
./run.sh
```

That run used Bazel's `@spack_dist//:spack` inside rootfs mode `cuda-bundle`,
reported hermetic Spack version `1.2.2`, reused the focused lock with root
`spack_py_kiwisolver`, passed `//tools:hermetic_native_deps_guard_test`, passed
`//synthetic:use_py_kiwisolver_native`, and passed
`//synthetic:py_kiwisolver_prefix_parity`.
