# py-jinja2 native recipe

## Position in the hillclimb

`py-jinja2@3.1.6` is the next pure `PythonPackage` source build after native
`py-markupsafe` in the pruned lean `py-torch` frontier:

```text
88  py-flit-core  3.12.0  python_pip  native
89  py-idna       3.15    python_pip  native
90  py-markupsafe 3.0.3   python_pip  native
91  py-jinja2     3.1.6   python_pip  native
92  py-mpmath     1.3.0   python_pip  next
```

The focused reference graph for
`SPACK_ROOT_PKG='py-jinja2@3.1.6 ^python@3.13.13+bz2+ctypes+dbm~debug+libxml2+lzma~optimizations+pic+pyexpat+pythoncmd+readline+shared+sqlite3+ssl~static~tkinter+uuid+zlib'`
ends with 38 nodes. Spack still owns the DAG shape; the native flip changes
only `spack_py_jinja2.build` to `native` and re-exports
`@py_jinja2_native//:lib`. The generated link/runtime edges remain
Spack-derived.

## Spack evidence

All evidence here comes from Bazel's vendored `@spack_dist//:spack` release
running inside the CUDA insula. Do not use an ambient host Spack checkout.

Reference prefix from the focused hermetic run:

```text
/vaso/cache/spack/opt/spack/linux-icelake/py-jinja2-3.1.6-jssnsurxhoomfcrxoawvrt37t2norgu7
```

Dependency prefixes from the same build environment:

```text
/vaso/cache/spack/opt/spack/linux-icelake/py-flit-core-3.12.0-vjkcb3p4z7cjbtr2bf7ky2w3n7vtwb7j
/vaso/cache/spack/opt/spack/linux-icelake/py-markupsafe-3.0.3-s3kyqz65jvocfd35qglqjmagzl776gvm
/vaso/cache/spack/opt/spack/linux-icelake/py-pip-26.1.2-rjfumck4mkxpheunqnsc4okzhxxq53lv
/vaso/cache/spack/opt/spack/linux-icelake/py-wheel-0.45.1-zkzfjve4om2w4y2nwjai4d2hp53vgnwt
/vaso/cache/spack/opt/spack/linux-icelake/python-venv-1.0-ab2pdnquesi6tr3e5jrieb2itp4jdbab
/vaso/cache/spack/opt/spack/linux-icelake/python-3.13.13-6u37x4adbcqbp247uvyjtokjsun4oewt
```

Hermetic recipe path observed from the installed prefix:

```text
/vaso/cache/spack/opt/spack/linux-icelake/py-jinja2-3.1.6-jssnsurxhoomfcrxoawvrt37t2norgu7/.spack/repos/spack_repo/builtin/packages/py_jinja2/package.py
```

Recipe facts from hermetic Spack `v1.2.2`:

- package class: `PyJinja2(PythonPackage)`
- selected build system: `python_pip`
- version: `3.1.6`
- source payload: PyPI source archive
  `https://files.pythonhosted.org/packages/source/j/jinja2/jinja2-3.1.6.tar.gz`
- source SHA256:
  `0137fb05990d35f1275a587e9aee6d56da821fc83491a0fb838183be43f66d6d`
- variant: `i18n` defaults to false
- dependencies for `@3.1.6`: `python@3.8:` build/run,
  `py-flit-core@:3` build, `py-markupsafe@2.0:` build/run, plus
  PythonPackage machinery `python-venv`, `py-pip`, and `py-wheel`
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
  --prefix=<py-jinja2-prefix> \
  .
```

Spack supplies `pip`, `wheel`, `flit_core`, and `markupsafe` by putting their
prefixes on `PYTHONPATH`. The installed wheel metadata says:

```text
Generator: flit 3.12.0
Root-Is-Purelib: true
Tag: py3-none-any
Requires-Dist: MarkupSafe>=2.0
```

The optional `i18n` dependency on Babel is not active in this concrete graph.
Generated installation metadata that embeds temporary stage paths or complete
wheel file lists, such as `direct_url.json`, `RECORD`, and bytecode, is not
part of the stable parity surface.

## Native build recipe

`native/py_jinja2/py_jinja2.bzl` mirrors that install method directly:

```text
download and extract the exact jinja2-3.1.6 source archive by SHA256
read @python_venv_native//:prefix_path.txt
read @py_pip_native//:prefix_path.txt
read @py_wheel_native//:prefix_path.txt
read @py_flit_core_native//:prefix_path.txt
read @py_markupsafe_native//:prefix_path.txt
validate PYTHON_VENV_PREFIX/bin/python3
derive PYTHON_ABI from PYTHON_VENV_PREFIX/bin/python3
validate PYTHON_VENV_PREFIX/bin/python${PYTHON_ABI}
validate PY_PIP_PREFIX/lib/python${PYTHON_ABI}/site-packages/pip
validate PY_WHEEL_PREFIX/lib/python${PYTHON_ABI}/site-packages/wheel
validate PY_FLIT_CORE_PREFIX/lib/python${PYTHON_ABI}/site-packages/flit_core
validate PY_MARKUPSAFE_PREFIX/lib/python${PYTHON_ABI}/site-packages/markupsafe
clear PYTHONHOME
set PYTHONPATH from native py-markupsafe, py-pip, py-wheel, py-flit-core,
  and python-venv under lib/python${PYTHON_ABI}/site-packages
cd <source>
PYTHON_VENV_PREFIX/bin/python3 -m pip -vvv install \
  --no-deps --ignore-installed --no-build-isolation \
  --no-warn-script-location --no-index --prefix=<native-prefix> .
validate package files, dist-info metadata, entry points, and license
emit prefix_path.txt
```

The rule refuses to evaluate unless `VASO_IN_INSULA=1`, so the wheel build and
prefix install happen only inside the sealed CUDA rootfs. It never searches for
Python, pip, wheel, flit-core, or MarkupSafe on the host; the Python interpreter
comes from the Bazel-native venv prefix, the ABI is derived from that prefix at
repository evaluation time, and Python package inputs come from Bazel-native
prefixes on `PYTHONPATH`.

The mechanism verifier reports the expected build channel:

```text
native/py_jinja2/py_jinja2.bzl: python-pip-install: PYTHON_VENV_PREFIX, PY_FLIT_CORE_PREFIX, PY_MARKUPSAFE_PREFIX, PY_PIP_PREFIX, PY_WHEEL_PREFIX
```

That verifier checks every prefix-file input, the `python -m pip` invocation
through `PYTHON_VENV_PREFIX`, `PY_PIP_PREFIX` exposure on `PYTHONPATH`,
Spack's no-network/no-build-isolation/no-deps flags, `--prefix`, and clearing
`PYTHONHOME`.

## Prefix and behavior contract

The stable prefix surface includes:

- package tree: `lib/python3.13/site-packages/jinja2`
- metadata, entry points, and license: `jinja2-3.1.6.dist-info`

The package installs no ELF objects, so the ABI axis is expected to be empty.

## ABI and behavior gate

The smoke target is:

```text
//synthetic:use_py_jinja2_native
```

It resolves the native `py-jinja2`, `py-markupsafe`, `py-flit-core`, `python`,
`python-venv`, `py-pip`, and `py-wheel` prefix markers, sets the native Python
library path and package `PYTHONPATH`, imports Jinja2, checks version metadata,
renders an autoescaped HTML template through MarkupSafe, verifies sandboxed
filter behavior, and prints:

```text
py-jinja2:3.1.6:Hello World: &lt;strong&gt;&#34;safe&#34;&lt;/strong&gt;:6
```

The parity target is:

```text
//synthetic:py_jinja2_prefix_parity
```

It compares `@py_jinja2_native//:prefix` against the hermetic Spack reference
prefix and covers:

- selected layout parity for stable package files, metadata, entry points, and
  license;
- byte-identical package modules and representative metadata;
- empty ELF ABI axis, since the package installs no native shared libraries.

Current status: native `py-jinja2` is gated inside the hermetic CUDA insula.
The focused verification command was:

```bash
env PATH=/usr/bin:/bin:$HOME/.local/bin \
BAZEL_BIN=$HOME/.local/bin/bazel-9.2.0 \
VASO_ESTATE_ROOT=$VASO_ESTATE_ROOT \
TMPDIR=$VASO_ESTATE_ROOT/agents/trae/tmp \
SPACK_ROOT_PKG='py-jinja2@3.1.6 ^python@3.13.13+bz2+ctypes+dbm~debug+libxml2+lzma~optimizations+pic+pyexpat+pythoncmd+readline+shared+sqlite3+ssl~static~tkinter+uuid+zlib' \
VASO_NATIVE=1 \
VASO_FORMAL=0 \
VASO_SKIP_CONSUMER_TESTS=1 \
VASO_SKIP_NATIVE_ABI_GATES=1 \
VASO_EXTRA_TEST_TARGETS='//tools:hermetic_native_deps_guard_test,//tools:native_dep_wiring_live_test,//synthetic:use_py_jinja2_native,//synthetic:py_jinja2_prefix_parity' \
VASO_SPACK_TIMEOUT=2400 \
./run.sh
```

That run used Bazel's `@spack_dist//:spack` inside rootfs mode `cuda-bundle`,
reported hermetic Spack version `1.2.2`, regenerated `spack_graph.lock.json`
with 33 packages and root `spack_py_jinja2`, regenerated `build_graph.json`
with 38 nodes and tail `python@3.13.13`, `python-venv@1.0`,
`py-pip@26.1.2`, `py-setuptools@79.0.1`, `py-wheel@0.45.1`,
`py-flit-core@3.12.0`, `py-markupsafe@3.0.3`, and `py-jinja2@3.1.6`,
passed `//tools:hermetic_native_deps_guard_test`, passed
`//tools:native_dep_wiring_live_test` with 0 allowlisted mismatches, passed
`//synthetic:use_py_jinja2_native`, and passed
`//synthetic:py_jinja2_prefix_parity` with `"ok": true` for 15 selected
`lib/python3.13/site-packages` paths, byte-identical files/metadata/license,
and an empty ELF ABI axis. Full log:
`$VASO_ESTATE_ROOT/agents/trae/logs/py-jinja2-insula-proof-full-20260929T110032Z.log`.
