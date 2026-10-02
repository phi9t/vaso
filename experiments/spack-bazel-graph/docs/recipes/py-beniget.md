# py-beniget native recipe

## Position in the hillclimb

`py-beniget@0.4.2.post1` is the next regular `PythonPackage` source build after
native `py-gast` in the lean `py-torch` frontier:

```text
98   py-fonttools  4.39.4        python_pip  native
99   py-gast       0.6.0         python_pip  native
100  py-beniget    0.4.2.post1   python_pip
```

The focused reference graph for `SPACK_ROOT_PKG='py-beniget@0.4.2.post1'` ends
with 37 nodes. Spack still owns the DAG shape; the native flip changes only
`spack_py_beniget.build` to `native` and re-exports
`@py_beniget_native//:lib`. The generated link/runtime edges remain
Spack-derived.

## Spack evidence

All evidence here comes from Bazel's vendored `@spack_dist//:spack` release
running inside the CUDA insula. Do not use an ambient host Spack checkout.

Reference prefix from the focused hermetic Python 3.13 run:

```text
/vaso/cache/spack/opt/spack/linux-icelake/py-beniget-0.4.2.post1-sxzqp2mcexilgtoixbxvcn77h2azmetp
```

Dependency prefixes from the same build environment:

```text
/vaso/cache/spack/opt/spack/linux-icelake/py-gast-0.6.0-z5lwihe7nljygllyvnxpf47jgptqukct
/vaso/cache/spack/opt/spack/linux-icelake/py-pip-26.1.2-rjfumck4mkxpheunqnsc4okzhxxq53lv
/vaso/cache/spack/opt/spack/linux-icelake/py-setuptools-79.0.1-ahvs37zplvfrbijtnucmlc2upqxl5ijk
/vaso/cache/spack/opt/spack/linux-icelake/py-wheel-0.45.1-zkzfjve4om2w4y2nwjai4d2hp53vgnwt
/vaso/cache/spack/opt/spack/linux-icelake/python-venv-1.0-ab2pdnquesi6tr3e5jrieb2itp4jdbab
/vaso/cache/spack/opt/spack/linux-icelake/python-3.13.13-6u37x4adbcqbp247uvyjtokjsun4oewt
```

Hermetic recipe path observed from the installed prefix:

```text
/vaso/cache/spack/opt/spack/linux-icelake/py-beniget-0.4.2.post1-sxzqp2mcexilgtoixbxvcn77h2azmetp/.spack/repos/spack_repo/builtin/packages/py_beniget/package.py
```

Recipe facts from hermetic Spack `v1.2.2`:

- package class: `PyBeniget(PythonPackage)`
- selected build system: `python_pip`
- version: `0.4.2.post1`
- source payload: PyPI source archive
  `https://files.pythonhosted.org/packages/source/b/beniget/beniget-0.4.2.post1.tar.gz`
- source SHA256:
  `a0258537e65e7e14ec33a86802f865a667f949bb6c73646d55e42f7c45a052ae`
- package.py dependency: `py-setuptools` build input
- versioned dependency: `py-gast@0.5.4:` for `@0.4.2:`, concretized here to
  native `py-gast@0.6.0`
- concretized PythonPackage machinery: `python`, `python-venv`, `py-pip`,
  `py-setuptools`, and `py-wheel`
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
  --prefix=<py-beniget-prefix> \
  .
```

Spack supplies `pip`, `setuptools`, `wheel`, and `gast` by putting their
prefixes on `PYTHONPATH`; the build log reports:

```text
Using pip 26.1.2 from <py-pip-prefix>/lib/python3.13/site-packages/pip (python 3.13)
```

Pip builds an intermediate pure-Python wheel from the source tree, then installs
that wheel into the prefix. Generated installation metadata that embeds
temporary stage paths or full file lists, such as `direct_url.json`, `RECORD`,
and bytecode, is not part of the stable parity surface.

## Native build recipe

`native/py_beniget/py_beniget.bzl` mirrors that install method directly:

```text
download and extract the exact beniget-0.4.2.post1 source archive by SHA256
read @python_venv_native//:prefix_path.txt
read @py_pip_native//:prefix_path.txt
read @py_setuptools_native//:prefix_path.txt
read @py_wheel_native//:prefix_path.txt
read @py_gast_native//:prefix_path.txt
validate PYTHON_VENV_PREFIX/bin/python3
derive PYTHON_ABI from PYTHON_VENV_PREFIX/bin/python3
validate PYTHON_VENV_PREFIX/bin/python${PYTHON_ABI}
validate PY_PIP_PREFIX/lib/python${PYTHON_ABI}/site-packages/pip
validate PY_SETUPTOOLS_PREFIX/lib/python${PYTHON_ABI}/site-packages/setuptools
validate PY_WHEEL_PREFIX/lib/python${PYTHON_ABI}/site-packages/wheel
validate PY_GAST_PREFIX/lib/python${PYTHON_ABI}/site-packages/gast
clear PYTHONHOME
set PYTHONPATH from native py-pip, py-setuptools, py-wheel, py-gast, and python-venv for PYTHON_ABI
cd <source>
PYTHON_VENV_PREFIX/bin/python3 -m pip -vvv install \
  --no-deps --ignore-installed --no-build-isolation \
  --no-warn-script-location --no-index --prefix=<native-prefix> .
validate beniget package files, dist-info metadata, and license
emit prefix_path.txt
```

The rule refuses to evaluate unless `VASO_IN_INSULA=1`, so both the local wheel
build and the prefix install happen only inside the sealed CUDA rootfs. It
never searches `PATH` for Python, pip, setuptools, wheel, or gast; Python comes
from the Bazel-native venv prefix and Python package inputs come from
Bazel-native prefixes on `PYTHONPATH`.

The mechanism verifier reports the expected build channel:

```text
native/py_beniget/py_beniget.bzl: python-pip-install: PYTHON_VENV_PREFIX, PY_GAST_PREFIX, PY_PIP_PREFIX, PY_SETUPTOOLS_PREFIX, PY_WHEEL_PREFIX
```

That verifier also checks every prefix-file input, `python -m pip` invocation
through `PYTHON_VENV_PREFIX`, `PY_PIP_PREFIX` exposure on `PYTHONPATH`, Spack's
no-network/no-build-isolation/no-deps flags, `--prefix`, and clearing
`PYTHONHOME`.

## Prefix and behavior contract

The stable prefix surface includes:

- package tree: `lib/python3.13/site-packages/beniget`
- metadata and license: `beniget-0.4.2.post1.dist-info`

Generated installation metadata that embeds the temporary Spack stage path or
complete wheel file list, such as `direct_url.json`, `RECORD`, and bytecode, is
not part of the stable parity surface. The package installs no ELF objects, so
the ABI axis is expected to be empty.

## ABI and behavior gate

The smoke target is:

```text
//synthetic:use_py_beniget_native
```

It resolves the native `py-beniget`, `py-gast`, `python`, `python-venv`,
`py-pip`, `py-setuptools`, and `py-wheel` prefix markers, sets the native Python
library path and package `PYTHONPATH` for the ABI derived from native
`python3`, asserts `3.13`, imports `beniget` and `gast`, builds `Ancestors` and
`DefUseChains` for a small function, and prints:

```text
py-beniget:0.4.2.post1:Module>FunctionDef:x,y
```

The parity target is:

```text
//synthetic:py_beniget_prefix_parity
```

It compares `@py_beniget_native//:prefix` against the hermetic Spack reference
prefix and covers:

- selected layout parity for stable package files and metadata;
- byte-identical package modules, representative metadata, and license files;
- empty ELF ABI axis, since the package installs no native shared libraries.

Current status: native `py-beniget` is gated inside the hermetic CUDA insula.
The focused verification command was:

```bash
env PATH=/usr/bin:/bin:$HOME/.local/bin \
BAZEL_BIN=$HOME/.local/bin/bazel-9.2.0 \
VASO_ESTATE_ROOT=$VASO_ESTATE_ROOT \
SPACK_ROOT_PKG='py-beniget@0.4.2.post1 ^python@3.13.13+bz2+ctypes+dbm~debug+libxml2+lzma~optimizations+pic+pyexpat+pythoncmd+readline+shared+sqlite3+ssl~static~tkinter+uuid+zlib' \
VASO_NATIVE=1 \
VASO_FORMAL=0 \
VASO_SKIP_CONSUMER_TESTS=1 \
VASO_SKIP_NATIVE_ABI_GATES=1 \
VASO_EXTRA_TEST_TARGETS='//tools:hermetic_native_deps_guard_test,//tools:native_dep_wiring_live_test,//synthetic:use_py_beniget_native,//synthetic:py_beniget_prefix_parity' \
VASO_SPACK_TIMEOUT=2400 \
./run.sh
```

That run used Bazel's `@spack_dist//:spack` inside rootfs mode `cuda-bundle`,
reported hermetic Spack version `1.2.2`, regenerated `spack_graph.lock.json`
with root `spack_py_beniget`, wrote a 32-package lock and 37-node build graph,
and used reference prefix
`/vaso/cache/spack/opt/spack/linux-icelake/py-beniget-0.4.2.post1-sxzqp2mcexilgtoixbxvcn77h2azmetp`.
The RED host guard first failed on `native/py_beniget/py_beniget.bzl:51` for
six hard-coded Python ABI literal lines. After the rule derived the ABI from
`@python_venv_native`, `//tools:hermetic_native_deps_guard_test`,
`//tools:native_dep_wiring_live_test`, `//synthetic:use_py_beniget_native`, and
`//synthetic:py_beniget_prefix_parity` passed inside the insula. The smoke
output was `py-beniget:0.4.2.post1:Module>FunctionDef:x,y`; parity reported
`"ok": true`, 9/9 selected layout/data paths under
`lib/python3.13/site-packages`, no missing or extra candidate paths,
byte-identical package files, metadata, license, and an empty ELF ABI axis.
Full log:
`$VASO_ESTATE_ROOT/agents/trae/logs/py-beniget-rerun-20260929T060203Z.log`.
