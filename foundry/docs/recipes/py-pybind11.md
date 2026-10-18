# py-pybind11 native recipe

## Position in the hillclimb

`py-pybind11@3.0.2` is a shared native-provider gap in the current
Python 3.13 triumvirate graphs: full `py-torch@2.14.0` (`~magma ~mkldnn
^py-networkx~default`), `py-triton@3.8.0`, and `py-jax/py-jaxlib@0.10.2`.

Spack still owns the DAG shape. The native flip changes only
`spack_py_pybind11.build` to `native` and re-exports
`@py_pybind11_native//:lib`; generated dependency edges remain Spack-derived.

## Spack evidence

All evidence here comes from Bazel's vendored `@spack_dist//:spack` release
running inside the CUDA insula. Do not use an ambient host Spack checkout.

Recipe facts from hermetic Spack `v1.2.2` and the Vaso overlay:

- package class: `PyPybind11(CMakePackage, PythonExtension)`
- selected build system: `cmake`
- version: `3.0.2`
- source payload: GitHub source archive
  `https://github.com/pybind/pybind11/archive/refs/tags/v3.0.2.tar.gz`
- source SHA256:
  `2f20a0af0b921815e0e169ea7fec63909869323581b89d7de1553468553f6a2d`
- active CMake generator: `ninja`
- active CMake options: `PYBIND11_TEST=OFF`,
  `prefix_for_pc_file=<py-pybind11-prefix>`
- Vaso overlay dependency: `py-scikit-build-core@1:` for `@3:`
- concrete graph dependencies: `cmake`, `ninja`, `py-pip`,
  `py-scikit-build-core`, `py-wheel`, `python@3.13.13`, and
  `python-venv`
- patches: none

Spack installs the CMake prefix first, then invokes the Python pip builder:

```text
cmake -G Ninja -DPYBIND11_TEST=OFF -Dprefix_for_pc_file=<prefix> ...
ninja install
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
  --prefix=<py-pybind11-prefix> \
  .
```

## Native build recipe

`native/py_pybind11/py_pybind11.bzl` mirrors that install method directly:

```text
download and extract the exact pybind11 v3.0.2 source archive by SHA256
read @cmake_native//:prefix_path.txt
read @ninja_native//:prefix_path.txt
read @python_venv_native//:prefix_path.txt
derive PYTHON_ABI from the native python-venv prefix
read Python build helper prefixes from native py-packaging, py-pathspec,
py-pip, py-scikit-build-core, py-setuptools, and py-wheel
validate the native CMake, Ninja, python-venv, and Python package prefixes
clear PYTHONHOME
set CMAKE_EXECUTABLE, PATH, and ABI-derived PYTHONPATH from native prefixes
cmake -G Ninja -DPYBIND11_TEST=OFF -DPYBIND11_INSTALL=ON \
  -DCMAKE_INTERPROCEDURAL_OPTIMIZATION=OFF \
  -DPython_EXECUTABLE=<python-venv>/bin/python${PYTHON_ABI} \
  -DPython3_EXECUTABLE=<python-venv>/bin/python${PYTHON_ABI}
ninja install
PYTHON_VENV_PREFIX/bin/python3 -m pip -vvv install \
  --no-deps --ignore-installed --no-build-isolation \
  --no-warn-script-location --no-index --prefix=<native-prefix> .
validate header, CMake config, package modules, metadata, and wheel metadata
emit prefix_path.txt
```

The rule refuses to evaluate unless `VASO_IN_INSULA=1`, so the prefix install
happens only inside the sealed CUDA rootfs. It never searches for host Spack;
all build and Python packaging inputs come from Bazel-native prefixes.

## Prefix and behavior contract

The stable prefix surface is:

- script `bin/pybind11-config`
- header tree under `include/pybind11`
- top-level CMake and pkg-config metadata under `share/cmake/pybind11` and
  `share/pkgconfig`
- Python package modules and mirrored CMake/pkg-config data under
  `lib/python3.13/site-packages/pybind11`
- metadata under `lib/python3.13/site-packages/pybind11-3.0.2.dist-info`

Generated installation metadata that embeds the temporary build path or
complete wheel file list, such as `direct_url.json`, `RECORD`, and bytecode, is
not part of the stable parity surface. The package installs no shared
libraries, so the ELF ABI axis is expected to be empty.

## ABI and behavior gate

The smoke target is:

```text
//synthetic:use_py_pybind11_native
```

It resolves native `py-pybind11`, native Python, native Python venv, native
CMake, and native Ninja, imports `pybind11`, verifies
`pybind11.get_include()`, configures a downstream CMake project with
`find_package(pybind11 CONFIG REQUIRED)`, builds a tiny CPython extension, and
imports it under native `python@3.13.13`. The expected output is:

```text
py-pybind11:3.0.2:include:42
```

The parity target is:

```text
//synthetic:py_pybind11_prefix_parity
```

It compares `@py_pybind11_native//:prefix` against the hermetic Spack reference
prefix injected by `run.sh`, checking representative headers, top-level and
Python-package CMake/pkg-config metadata, `pybind11-config`, package modules,
dist-info metadata, license, and the empty ELF ABI axis.

## ODR-sensitive provider invariant

This slice does not add protobuf, gRPC, Abseil, or Boost providers. Those
families must stay on one unified compatible concrete version family; the
generator rejects unqualified overrides and mixed concrete versions for those
ODR-sensitive providers.
