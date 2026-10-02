# py-protobuf@4.21.12

## Position in the hillclimb

`py-protobuf@4.21.12` is the Python protobuf provider paired with native
hermetic Spack `protobuf@21.12` (upstream/protoc API `3.21.12`) for the
PyTorch `v2.14.0` source target:

```text
py-protobuf  4.21.12  python_pip  native
```

Spack still owns the DAG shape. This provider is active through the exact
`native_overrides.json` key `py-protobuf@4.21.12`, so it only serves the
Python/protobuf family selected for the PyTorch `v2.14.0` compatibility island.

`protobuf`, `py-protobuf`, `grpc`, `grpc-cpp`, `py-grpcio`, `abseil-cpp`, and
`boost` are ODR-sensitive provider families in this experiment. The protobuf
family is migrated with exact-version override keys only:

```json
"protobuf@21.12": "@protobuf_native//:lib",
"py-protobuf@4.21.12": "@py_protobuf_native//:lib"
```

Do not mix this capture with `py-protobuf@3.x`, `py-protobuf@6.x`, or
`protobuf@32.x` in the same native-provider graph.

## Spack evidence

All evidence comes from Bazel's vendored `@spack_dist//:spack` release running
inside the CUDA insula. Do not use an ambient host Spack checkout.

Hermetic Spack `v1.2.2` recipe facts:

- package class: `PyProtobuf(PythonPackage)`
- selected build system: `python_pip`
- `py-protobuf@4.21.12` is supplied by the repo-owned `vaso_overlay` package,
  because Spack `v1.2.2`'s builtin recipe does not declare that exact version
- `py-protobuf@:5.26` depends on `python@:3.13`
- dependencies: `protobuf`, `python`, `python-venv`, plus `py-pip`,
  `py-setuptools`, and `py-wheel` build inputs
- patches: none

The native rule passes the legacy setup.py option that selects the C++ Python
implementation:

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
  --prefix=<py-protobuf-prefix> \
  --config-settings=--global-option=--cpp_implementation \
  .
```

## Native build recipe

`native/py_protobuf/py_protobuf.bzl` mirrors that install method directly:

```text
download and extract the exact protobuf-4.21.12 source archive by SHA256
read @protobuf_native//:prefix_path.txt
read @python_313_native//:prefix_path.txt
read @python_venv_native//:prefix_path.txt
read @py_pip_native//:prefix_path.txt
read @py_setuptools_native//:prefix_path.txt
read @py_wheel_native//:prefix_path.txt
validate PROTOBUF_PREFIX/include/google/protobuf and PROTOBUF_PREFIX/bin/protoc
validate PROTOBUF_PREFIX/bin/protoc --version == libprotoc 3.21.12
derive PYTHON_ABI from PYTHON_PREFIX/bin/python3
validate PYTHON_ABI == 3.13
validate PYTHON_PREFIX/bin/python${PYTHON_ABI} and include/python${PYTHON_ABI}
validate PYTHON_VENV_PREFIX/bin/python3 and bin/python${PYTHON_ABI}
validate native pip, setuptools, and wheel site-package payloads
clear PYTHONHOME
set PROTOC to the native protobuf compiler
set PYTHONPATH from native py-pip, py-setuptools, py-wheel, and python-venv
put protobuf, Python, and Python packaging prefixes on PATH
thread Python/protobuf include and library prefixes through CFLAGS/CXXFLAGS/LDFLAGS
PYTHON_VENV_PREFIX/bin/python${PYTHON_ABI} -m pip -vvv install \
  --no-deps --ignore-installed --no-build-isolation \
  --no-warn-script-location --no-index --prefix=<native-prefix> \
  --config-settings=--global-option=--cpp_implementation .
validate google/protobuf modules, google/protobuf/pyext/_message*.so,
  google/protobuf/internal/_api_implementation*.so, and stable dist-info
emit prefix_path.txt
```

The rule refuses to evaluate unless `VASO_IN_INSULA=1`, so the build and prefix
install happen only inside the sealed CUDA rootfs. It never searches for Spack
on the host. Python 3.13, python-venv, pip, setuptools, wheel, and protobuf are
all supplied by Bazel-native prefix markers.

The mechanism verifier reports the expected build channel:

```text
native/py_protobuf/py_protobuf.bzl: python-pip-install: PROTOBUF_PREFIX, PYTHON_PREFIX, PYTHON_VENV_PREFIX, PY_PIP_PREFIX, PY_SETUPTOOLS_PREFIX, PY_WHEEL_PREFIX
```

## Prefix and behavior contract

The stable prefix surface is:

- package modules under `lib/python3.13/site-packages/google/protobuf`
- namespace package support through the generated `protobuf-4.21.12-py3.13-nspkg.pth`
- CPython extension
  `lib/python3.13/site-packages/google/protobuf/pyext/_message.cpython-313-x86_64-linux-gnu.so`
- implementation marker extension
  `lib/python3.13/site-packages/google/protobuf/internal/_api_implementation.cpython-313-x86_64-linux-gnu.so`
- metadata under `lib/python3.13/site-packages/protobuf-4.21.12.dist-info`

Generated installation metadata that embeds the temporary build path or complete
wheel file list, such as `direct_url.json`, `RECORD`, and bytecode, is not part
of the stable parity surface.

## ABI and behavior gate

The smoke target is:

```text
//synthetic:use_py_protobuf_native
```

It resolves native `py-protobuf`, native Python 3.13 packaging prefixes, and
native `protobuf@21.12`, imports `google.protobuf`, checks installed metadata,
forces `PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION=cpp`, validates `protoc
--version`, and exercises a JSON/Struct round trip. The expected output is:

```text
py-protobuf:4.21.12:cpp:7
```

The parity target is:

```text
//synthetic:py_protobuf_prefix_parity
```

It compares `@py_protobuf_native//:prefix` against the hermetic Spack reference
prefix and covers:

- selected layout parity for stable Python modules and `.dist-info` metadata;
- byte-identical representative package files and metadata;
- explicit ELF parity for `_api_implementation*.so` and `pyext/_message*.so`.

The cross-language target is:

```text
//synthetic:protobuf_cpp_python_roundtrip
```

It generates C++ and Python bindings with `@protobuf_native//:prefix`'s
`protoc`, serializes a message in C++, parses it through
`@py_protobuf_native//:prefix` on Python 3.13, and prints:

```text
protobuf-roundtrip:3.21.12:4.21.12:ok
```

```bash
env PATH=/usr/bin:/bin:$HOME/.local/bin \
BAZEL_BIN=$HOME/.local/bin/bazel-9.2.0 \
VASO_ESTATE_ROOT=$VASO_ESTATE_ROOT \
VASO_NATIVE=1 \
VASO_FORMAL=0 \
VASO_SKIP_CONSUMER_TESTS=1 \
VASO_SKIP_NATIVE_ABI_GATES=1 \
VASO_FORCE_FETCH_REPOS='@protobuf_native,@py_protobuf_native' \
VASO_EXTRA_TEST_TARGETS='//tools:hermetic_native_deps_guard_test,//tools:spack_to_bazel_unit_test,//synthetic:use_py_protobuf_native,//synthetic:protobuf_cpp_python_roundtrip,//synthetic:py_protobuf_prefix_parity' \
VASO_SPACK_TIMEOUT=2400 \
./run.sh
```
