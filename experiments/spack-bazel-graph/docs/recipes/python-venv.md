# python-venv native recipe

## Position in the hillclimb

`python-venv@1.0` is the first lean `py-torch` frontier node after the native
CPython closure:

```text
30  python       3.13.13 generic  native
31  python-venv  1.0     generic
```

The focused reference graph for `SPACK_ROOT_PKG='python-venv@1.0'` ends with
32 nodes. Spack still owns the DAG shape; the native flip changes only
`spack_python_venv.build` to `native` and re-exports
`@python_venv_native//:lib`. Its only graph edge remains the Spack-derived
`python` build/run dependency.

## Spack evidence

All evidence here comes from Bazel's vendored `@spack_dist//:spack` release
running inside the CUDA insula. Do not use an ambient host Spack checkout.

Reference prefix from the focused hermetic run:

```text
/vaso/cache/spack/opt/spack/linux-icelake/python-venv-1.0-ab2pdnquesi6tr3e5jrieb2itp4jdbab
```

Python dependency prefix from the same lock:

```text
/vaso/cache/spack/opt/spack/linux-icelake/python-3.13.13-6u37x4adbcqbp247uvyjtokjsun4oewt
```

Hermetic install metadata observed from the installed prefix:

```text
/vaso/cache/spack/opt/spack/linux-icelake/python-venv-1.0-ab2pdnquesi6tr3e5jrieb2itp4jdbab/.spack/spec.json
/vaso/cache/spack/opt/spack/linux-icelake/python-venv-1.0-ab2pdnquesi6tr3e5jrieb2itp4jdbab/.spack/spack-build-out.txt.gz
```

Recipe facts from hermetic Spack `v1.2.2`:

- package class: `PythonVenv(Package)`
- selected build system: generic package install method
- version: `1.0`
- source payload: none (`has_code = False`)
- dependencies: `extends("python")`, represented in the concrete graph as a
  build/run dependency on `python@3.13.13`
- install command:

```text
<python-prefix>/bin/python3.13 -m venv --without-pip <prefix>
```

## Native build recipe

`native/python_venv/python_venv.bzl` mirrors that install method directly:

```text
read @python_313_native//:prefix_path.txt
derive PYTHON_ABI from PYTHON_PREFIX/bin/python3
validate PYTHON_PREFIX/bin/python3 and PYTHON_PREFIX/include/python${PYTHON_ABI}
clear PYTHONHOME and PYTHONPATH
PYTHON_PREFIX/bin/python${PYTHON_ABI} -m venv --without-pip <native-prefix>
validate pyvenv.cfg, bin/python${PYTHON_ABI}, lib/python${PYTHON_ABI}/site-packages, and lib64
emit prefix_path.txt
```

The rule refuses to evaluate unless `VASO_IN_INSULA=1`, so the venv is created
only inside the sealed CUDA rootfs. It never searches `PATH` for Python; the
interpreter comes from the Bazel-native CPython prefix marker.

The mechanism verifier reports the expected build channel:

```text
native/python_venv/python_venv.bzl: python-venv: PYTHON_PREFIX
```

That verifier also checks that the rule invokes `$PYTHON_PREFIX/bin/python* -m
venv`, passes `--without-pip`, and clears `PYTHONHOME`/`PYTHONPATH`.

## Prefix and behavior contract

The ABI-relevant prefix surface is prefix-only, with no C link library of its
own:

- `pyvenv.cfg`
- activation scripts: `bin/activate`, `bin/activate.csh`,
  `bin/activate.fish`, and `bin/Activate.ps1`
- interpreter symlinks: `bin/python`, `bin/python3`, and `bin/python3.13`
- `lib64 -> lib`
- `lib/python3.13/site-packages`

`python -m venv` embeds the absolute venv prefix, the absolute Python
dependency prefix, and a prompt name derived from the install directory
basename. The parity verifier normalizes those generated values before
comparing metadata, while still requiring the same files, symlink targets after
dependency-prefix normalization, executable `NEEDED` set, Python version, and
venv/base-prefix relationship.

## ABI and behavior gate

The smoke target is:

```text
//synthetic:use_python_venv_native
```

It resolves the native repository rule's `prefix_path.txt`, runs
`bin/python${PYTHON_ABI}` (`bin/python3.13` for this graph), confirms it is
inside a 3.13 venv, and prints:

```text
python-venv:True:3.13:prefix
```

The parity target is:

```text
//synthetic:python_venv_abi_parity
```

It compares `@python_venv_native//:prefix` against the hermetic Spack reference
prefix and covers:

- selected layout parity for the venv metadata/scripts/symlinks/site-packages
  surface;
- prefix-normalized `pyvenv.cfg` parity;
- prefix- and prompt-normalized activation script parity;
- normalized symlink parity for the interpreter entries and `lib64`;
- executable `NEEDED` parity for `bin/python3.13`;
- runtime behavior parity for `sys.prefix != sys.base_prefix`, Python version,
  normalized `sys.prefix`, and normalized `sys.base_prefix`.

Current status: native `python-venv` is gated inside the hermetic CUDA insula.
The focused verification command was:

```bash
env PATH=/usr/bin:/bin:$HOME/.local/bin \
BAZEL_BIN=$HOME/.local/bin/bazel-9.2.0 \
VASO_ESTATE_ROOT=$VASO_ESTATE_ROOT \
SPACK_ROOT_PKG='python-venv@1.0 ^python@3.13.13+bz2+ctypes+dbm~debug+libxml2+lzma~optimizations+pic+pyexpat+pythoncmd+readline+shared+sqlite3+ssl~static~tkinter+uuid+zlib' \
VASO_NATIVE=1 \
VASO_FORMAL=0 \
VASO_EXTRA_TEST_TARGETS='//synthetic:python_venv_abi_parity,//synthetic:use_python_venv_native,//tools:hermetic_native_deps_guard_test,//tools:native_dep_wiring_live_test' \
VASO_SPACK_TIMEOUT=1800 \
./run.sh
```

That run used Bazel's `@spack_dist//:spack` inside rootfs mode `cuda-bundle`,
reported hermetic Spack version `1.2.2`, regenerated
`spack_graph.lock.json` with root `spack_python_venv`, passed
`//tools:hermetic_native_deps_guard_test`, passed
`//synthetic:python_venv_abi_parity`, and passed
`//synthetic:use_python_venv_native`.
