# py-idna native recipe

## Position in the hillclimb

`py-idna@3.15` is the next regular `PythonPackage` source build after native
`py-beniget` in the lean `py-torch` frontier:

```text
99   py-gast     0.6.0         python_pip  native
100  py-beniget  0.4.2.post1   python_pip  native
101  py-idna     3.15          python_pip  native
```

The focused reference graph for
`SPACK_ROOT_PKG='py-idna@3.15 ^python@3.13.13+bz2+ctypes+dbm~debug+libxml2+lzma~optimizations+pic+pyexpat+pythoncmd+readline+shared+sqlite3+ssl~static~tkinter+uuid+zlib'`
has a 31-package lock and 36-node build graph. Spack still owns the DAG shape;
the native flip changes only `spack_py_idna.build` to `native` and re-exports
`@py_idna_native//:lib`. The generated link/runtime edges remain Spack-derived.

## Spack evidence

All evidence here comes from Bazel's vendored `@spack_dist//:spack` release
running inside the CUDA insula. Do not use an ambient host Spack checkout.

Reference prefix from the focused hermetic run:

```text
/vaso/cache/spack/opt/spack/linux-icelake/py-idna-3.15-owb4oymihbbo4pbbuaxvzduel6vvwn2k
```

Dependency prefixes from the same build environment:

```text
/vaso/cache/spack/opt/spack/linux-icelake/py-flit-core-3.12.0-vjkcb3p4z7cjbtr2bf7ky2w3n7vtwb7j
/vaso/cache/spack/opt/spack/linux-icelake/py-pip-26.1.2-rjfumck4mkxpheunqnsc4okzhxxq53lv
/vaso/cache/spack/opt/spack/linux-icelake/py-wheel-0.45.1-zkzfjve4om2w4y2nwjai4d2hp53vgnwt
/vaso/cache/spack/opt/spack/linux-icelake/python-venv-1.0-ab2pdnquesi6tr3e5jrieb2itp4jdbab
/vaso/cache/spack/opt/spack/linux-icelake/python-3.13.13-6u37x4adbcqbp247uvyjtokjsun4oewt
```

Hermetic recipe path observed from the installed prefix:

```text
/vaso/cache/spack/opt/spack/linux-icelake/py-idna-3.15-owb4oymihbbo4pbbuaxvzduel6vvwn2k/.spack/repos/spack_repo/builtin/packages/py_idna/package.py
```

Recipe facts from hermetic Spack `v1.2.2`:

- package class: `PyIdna(PythonPackage)`
- selected build system: `python_pip`
- version: `3.15`
- source payload: PyPI source archive
  `https://files.pythonhosted.org/packages/source/i/idna/idna-3.15.tar.gz`
- source SHA256:
  `ca962446ea538f7092a95e057da437618e886f4d349216d2b1e294abfdb65fdc`
- versioned dependency for `@3.15`: `py-flit-core@3.11:4`
- concretized PythonPackage machinery: `python`, `python-venv`, `py-pip`,
  `py-wheel`, and `py-flit-core`
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
  --prefix=<py-idna-prefix> \
  .
```

Spack supplies `pip`, `wheel`, and `flit_core` by putting their prefixes on
`PYTHONPATH`; the build log reports:

```text
Using pip 26.1.2 from <py-pip-prefix>/lib/python3.13/site-packages/pip (python 3.13)
```

Pip builds an intermediate pure-Python wheel from the source tree, then installs
that wheel into the prefix. Generated installation metadata that embeds
temporary stage paths or full file lists, such as `direct_url.json`, `RECORD`,
and bytecode, is not part of the stable parity surface.

## Native build recipe

`native/py_idna/py_idna.bzl` mirrors that install method directly:

```text
download and extract the exact idna-3.15 source archive by SHA256
read @python_venv_native//:prefix_path.txt
read @py_pip_native//:prefix_path.txt
read @py_wheel_native//:prefix_path.txt
read @py_flit_core_native//:prefix_path.txt
validate PYTHON_VENV_PREFIX/bin/python3
derive PYTHON_ABI from PYTHON_VENV_PREFIX/bin/python3
validate PYTHON_VENV_PREFIX/bin/python${PYTHON_ABI}
validate PY_PIP_PREFIX/lib/python${PYTHON_ABI}/site-packages/pip
validate PY_WHEEL_PREFIX/lib/python${PYTHON_ABI}/site-packages/wheel
validate PY_FLIT_CORE_PREFIX/lib/python${PYTHON_ABI}/site-packages/flit_core
clear PYTHONHOME
set PYTHONPATH from native py-pip, py-wheel, py-flit-core, and python-venv
cd <source>
PYTHON_VENV_PREFIX/bin/python3 -m pip -vvv install \
  --no-deps --ignore-installed --no-build-isolation \
  --no-warn-script-location --no-index --prefix=<native-prefix> .
validate idna package files, dist-info metadata, and license
emit prefix_path.txt
```

The rule refuses to evaluate unless `VASO_IN_INSULA=1`, so both the local wheel
build and the prefix install happen only inside the sealed CUDA rootfs. It
never searches `PATH` for Python, pip, wheel, or flit-core; Python comes from
the Bazel-native venv prefix and Python package inputs come from Bazel-native
prefixes on `PYTHONPATH`.

The mechanism verifier reports the expected build channel:

```text
native/py_idna/py_idna.bzl: python-pip-install: PYTHON_VENV_PREFIX, PY_FLIT_CORE_PREFIX, PY_PIP_PREFIX, PY_WHEEL_PREFIX
```

That verifier also checks every prefix-file input, `python -m pip` invocation
through `PYTHON_VENV_PREFIX`, `PY_PIP_PREFIX` exposure on `PYTHONPATH`, Spack's
no-network/no-build-isolation/no-deps flags, `--prefix`, and clearing
`PYTHONHOME`.

## Prefix and behavior contract

The stable prefix surface includes:

- package tree: `lib/python3.13/site-packages/idna`
- metadata and license: `idna-3.15.dist-info`

Generated installation metadata that embeds the temporary Spack stage path or
complete wheel file list, such as `direct_url.json`, `RECORD`, and bytecode, is
not part of the stable parity surface. The package installs no ELF objects, so
the ABI axis is expected to be empty.

## ABI and behavior gate

The smoke target is:

```text
//synthetic:use_py_idna_native
```

It resolves the native `py-idna`, `py-flit-core`, `python@3.13.13`,
`python-venv`, `py-pip`, and `py-wheel` prefix markers, derives and asserts
ABI `3.13`, sets the native Python library path and package `PYTHONPATH`,
imports `idna`, checks version metadata, performs an IDNA encode/decode round
trip, checks UTS-46 normalization, and prints:

```text
py-idna:3.15:xn--eckwd4c7c.xn--zckzah:ドメイン.テスト:xn--knigsgchen-b4a3dun
```

The parity target is:

```text
//synthetic:py_idna_prefix_parity
```

It compares `@py_idna_native//:prefix` against the hermetic Spack reference
prefix and covers:

- selected layout parity for stable package files and metadata;
- byte-identical package modules, representative metadata, and license files;
- empty ELF ABI axis, since the package installs no native shared libraries.

Current status: native `py-idna` is gated inside the hermetic CUDA insula. The
focused verification command was:

```bash
env PATH=/usr/bin:/bin:$HOME/.local/bin \
BAZEL_BIN=$HOME/.local/bin/bazel-9.2.0 \
TMPDIR=$VASO_ESTATE_ROOT/agents/trae/tmp \
VASO_ESTATE_ROOT=$VASO_ESTATE_ROOT \
SPACK_ROOT_PKG='py-idna@3.15 ^python@3.13.13+bz2+ctypes+dbm~debug+libxml2+lzma~optimizations+pic+pyexpat+pythoncmd+readline+shared+sqlite3+ssl~static~tkinter+uuid+zlib' \
VASO_NATIVE=1 \
VASO_FORMAL=0 \
VASO_SKIP_CONSUMER_TESTS=1 \
VASO_SKIP_NATIVE_ABI_GATES=1 \
VASO_EXTRA_TEST_TARGETS='//tools:hermetic_native_deps_guard_test,//tools:native_dep_wiring_live_test,//synthetic:use_py_idna_native,//synthetic:py_idna_prefix_parity' \
VASO_SPACK_TIMEOUT=2400 \
./run.sh
```

That run used Bazel's `@spack_dist//:spack` inside rootfs mode `cuda-bundle`,
reported hermetic Spack version `1.2.2`, regenerated `spack_graph.lock.json`
with root `spack_py_idna`, and wrote the full log to:

```text
$VASO_ESTATE_ROOT/agents/trae/logs/py-idna-rerun-20260929T061355Z.log
```

The final run passed `//tools:hermetic_native_deps_guard_test`,
`//tools:native_dep_wiring_live_test`, `//synthetic:use_py_idna_native`, and
`//synthetic:py_idna_prefix_parity`. The parity gate reported `"ok": true`,
12/12 selected stable layout/data paths under `lib/python3.13/site-packages`,
no missing or extra candidate paths, byte-identical package files, metadata,
license, and an empty ELF ABI axis.
