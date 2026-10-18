# py-markupsafe native recipe

## Position in the hillclimb

`py-markupsafe@3.0.3` is the next `PythonPackage` source build after native
`py-idna` in the lean `py-torch` frontier:

```text
100  py-beniget     0.4.2.post1  python_pip  native
101  py-idna        3.15         python_pip  native
102  py-markupsafe  3.0.3        python_pip
```

The focused reference graph for `SPACK_ROOT_PKG='py-markupsafe@3.0.3'` ends
with 36 nodes. Spack still owns the DAG shape; the native flip changes only
`spack_py_markupsafe.build` to `native` and re-exports
`@py_markupsafe_native//:lib`. The generated link/runtime edges remain
Spack-derived.

## Spack evidence

All evidence here comes from Bazel's vendored `@spack_dist//:spack` release
running inside the CUDA insula. Do not use an ambient host Spack checkout.

Reference prefix from the focused hermetic run:

```text
/vaso/cache/spack/opt/spack/linux-icelake/py-markupsafe-3.0.3-s3kyqz65jvocfd35qglqjmagzl776gvm
```

Dependency prefixes from the same build environment:

```text
/vaso/cache/spack/opt/spack/linux-icelake/py-pip-26.1.2-rjfumck4mkxpheunqnsc4okzhxxq53lv
/vaso/cache/spack/opt/spack/linux-icelake/py-setuptools-79.0.1-ahvs37zplvfrbijtnucmlc2upqxl5ijk
/vaso/cache/spack/opt/spack/linux-icelake/py-wheel-0.45.1-zkzfjve4om2w4y2nwjai4d2hp53vgnwt
/vaso/cache/spack/opt/spack/linux-icelake/python-venv-1.0-ab2pdnquesi6tr3e5jrieb2itp4jdbab
/vaso/cache/spack/opt/spack/linux-icelake/python-3.13.13-6u37x4adbcqbp247uvyjtokjsun4oewt
```

Hermetic recipe path observed from the installed prefix:

```text
/vaso/cache/spack/opt/spack/linux-icelake/py-markupsafe-3.0.3-s3kyqz65jvocfd35qglqjmagzl776gvm/.spack/repos/spack_repo/builtin/packages/py_markupsafe/package.py
```

Recipe facts from hermetic Spack `v1.2.2`:

- package class: `PyMarkupsafe(PythonPackage)`
- selected build system: `python_pip`
- version: `3.0.3`
- source payload: PyPI source archive
  `https://files.pythonhosted.org/packages/source/M/MarkupSafe/markupsafe-3.0.3.tar.gz`
- source SHA256:
  `722695808f4b6457b320fdc131280796bdceb04ab50fe1795cd540799ebe1698`
- dependencies: `python@3.9:` build/link/run, `python-venv` build/run,
  `py-setuptools@77:` for `@3.0.3:`, plus `py-pip` and `py-wheel` build
  inputs from PythonPackage machinery
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
  --prefix=<py-markupsafe-prefix> \
  .
```

Spack supplies `pip`, `setuptools`, and `wheel` by putting their prefixes on
`PYTHONPATH`. The build compiles
`markupsafe/_speedups.cpython-313-x86_64-linux-gnu.so` with includes from
`python-venv/include` and `python-3.13.13/include/python3.13`. The resulting
extension has no SONAME, needs only `libc.so.6`, and exports
`PyInit__speedups`.

Generated installation metadata that embeds temporary stage paths or complete
wheel file lists, such as `direct_url.json`, `RECORD`, and bytecode, is not
part of the stable parity surface.

## Native build recipe

`native/py_markupsafe/py_markupsafe.bzl` mirrors that install method directly:

```text
download and extract the exact markupsafe-3.0.3 source archive by SHA256
read @python_313_native//:prefix_path.txt
read @python_venv_native//:prefix_path.txt
read @py_pip_native//:prefix_path.txt
read @py_setuptools_native//:prefix_path.txt
read @py_wheel_native//:prefix_path.txt
derive PYTHON_ABI from PYTHON_PREFIX/bin/python3
validate PYTHON_PREFIX/bin/python${PYTHON_ABI} plus include/python${PYTHON_ABI} and lib
validate PYTHON_VENV_PREFIX/bin/python3 and bin/python${PYTHON_ABI}
validate PY_PIP_PREFIX/lib/python${PYTHON_ABI}/site-packages/pip
validate PY_SETUPTOOLS_PREFIX/lib/python${PYTHON_ABI}/site-packages/setuptools
validate PY_WHEEL_PREFIX/lib/python${PYTHON_ABI}/site-packages/wheel
clear PYTHONHOME
set PYTHONPATH from native py-pip, py-setuptools, py-wheel, and python-venv
set LD_LIBRARY_PATH, CFLAGS, and LDFLAGS from native Python
cd <source>
PYTHON_VENV_PREFIX/bin/python3 -m pip -vvv install \
  --no-deps --ignore-installed --no-build-isolation \
  --no-warn-script-location --no-index --prefix=<native-prefix> .
validate package files, dist-info metadata, license, and _speedups extension
emit prefix_path.txt
```

The rule refuses to evaluate unless `VASO_IN_INSULA=1`, so the C extension
build and prefix install happen only inside the sealed CUDA rootfs. It never
searches for Python or pip on the host; Python comes from Bazel-native Python
prefixes and Python packaging tools come from Bazel-native prefixes on
`PYTHONPATH`.

The mechanism verifier reports the expected build channel:

```text
native/py_markupsafe/py_markupsafe.bzl: python-pip-install: PYTHON_PREFIX, PYTHON_VENV_PREFIX, PY_PIP_PREFIX, PY_SETUPTOOLS_PREFIX, PY_WHEEL_PREFIX
```

That verifier checks every prefix-file input, the `python -m pip` invocation
through `PYTHON_VENV_PREFIX`, `PY_PIP_PREFIX` exposure on `PYTHONPATH`,
Spack's no-network/no-build-isolation/no-deps flags, `--prefix`, and clearing
`PYTHONHOME`.

## Prefix and behavior contract

The stable prefix surface includes:

- package tree: `lib/python3.13/site-packages/markupsafe`
- metadata and license: `markupsafe-3.0.3.dist-info`
- CPython extension module:
  `markupsafe/_speedups.cpython-313-x86_64-linux-gnu.so`

The extension module lives under `site-packages`, not top-level `lib` or
`lib64`. The parity gate therefore uses `tools/abi_parity.py --elf-path` to
compare NEEDED libraries, SONAME, and exported symbols for that explicit path.

## ABI and behavior gate

The smoke target is:

```text
//synthetic:use_py_markupsafe_native
```

It resolves the native `py-markupsafe`, `python@3.13.13`, `python-venv`,
`py-pip`, `py-setuptools`, and `py-wheel` prefix markers, derives and asserts
Python ABI `3.13`, sets the native Python library path and package
`PYTHONPATH`, imports `markupsafe._speedups`, asserts the active extension
suffix contains `cpython-313`, checks version metadata, verifies escaping and
`Markup.format`, and prints:

```text
py-markupsafe:3.0.3:.cpython-313-x86_64-linux-gnu.so:markupsafe._speedups:&lt;em&gt;&#34;x&#34;&lt;/em&gt;:Hello <strong>World</strong>
```

The parity target is:

```text
//synthetic:py_markupsafe_prefix_parity
```

It compares `@py_markupsafe_native//:prefix` against the hermetic Spack
reference prefix and covers:

- selected layout parity for stable package files, metadata, license, and the
  extension module;
- byte-identical package modules and representative metadata;
- NEEDED, SONAME, and exported-symbol parity for the `_speedups` CPython
  extension module.

Current status: native `py-markupsafe` is gated inside the hermetic CUDA
insula. The focused verification command was:

```bash
env PATH=/usr/bin:/bin:$HOME/.local/bin \
BAZEL_BIN=$HOME/.local/bin/bazel-9.2.0 \
VASO_ESTATE_ROOT=$VASO_ESTATE_ROOT \
SPACK_ROOT_PKG='py-markupsafe@3.0.3 ^python@3.13.13+bz2+ctypes+dbm~debug+libxml2+lzma~optimizations+pic+pyexpat+pythoncmd+readline+shared+sqlite3+ssl~static~tkinter+uuid+zlib' \
VASO_NATIVE=1 \
VASO_FORMAL=0 \
VASO_SKIP_CONSUMER_TESTS=1 \
VASO_SKIP_NATIVE_ABI_GATES=1 \
VASO_EXTRA_TEST_TARGETS='//tools:hermetic_native_deps_guard_test,//tools:native_dep_wiring_live_test,//synthetic:use_py_markupsafe_native,//synthetic:py_markupsafe_prefix_parity' \
VASO_SPACK_TIMEOUT=2400 \
./run.sh
```

That run used Bazel's `@spack_dist//:spack` inside rootfs mode `cuda-bundle`,
reported hermetic Spack version `1.2.2`, regenerated `spack_graph.lock.json`
with root `spack_py_markupsafe` (31 packages) and `build_graph.json` (36 nodes,
tail `python@3.13.13`, `python-venv`, `py-pip`, `py-setuptools`, `py-wheel`,
`py-markupsafe`), passed `//tools:hermetic_native_deps_guard_test`, passed
`//tools:native_dep_wiring_live_test`, passed
`//synthetic:use_py_markupsafe_native`, and passed
`//synthetic:py_markupsafe_prefix_parity`. Full log:
`$VASO_ESTATE_ROOT/agents/trae/logs/py-markupsafe-rerun-20260929T062810Z.log`.
