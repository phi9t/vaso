# py-torch frontier recipe

## Position in the hillclimb

`py-torch` is the first package beyond the completed `SPACK_ROOT_PKG=python`
native closure. It is not flipped to native yet. The current state is recipe
capture plus a gated native build skeleton:

```text
python closure native green -> py-torch recipe-captured -> gated native torch
```

The full native build remains unauthorized unless the trusted request includes
`build-native-pytorch`.

## Spack evidence

All evidence here comes from the Bazel-owned hermetic Spack release
`@spack_dist//:spack`, pinned in `MODULE.bazel` to upstream Spack `v1.2.2`.
The live verifier `//tools:pytorch_recipe_provenance_test` runs inside the
insula, invokes that Bazel-vendored Spack executable, and scans the package
repository materialized under:

```text
/vaso/cache/spack/user/package_repos/.../repos/spack_repo/builtin/packages
```

Current checked provenance:

- `py-torch` is present as `PyTorch(PythonPackage, CudaPackage, ROCmPackage)`.
- `py-triton`, `py-jax`, and `py-jaxlib` are present in the same hermetic
  package repository.
- `py-jaxlib` carries an upstream Bazel dependency surface, which is the
  expected path for the later JAX/XLA migration.
- `vendor-libtorch` and `libtorch` are absent from this pinned upstream Spack
  package universe. The vendor-specific ABI/install surface is therefore an
  explicitly encoded compatibility contract in `native/pytorch/`, not recipe
  provenance discovered from Spack v1.2.2.

Hermetic recipe path:

```text
/vaso/cache/spack/user/package_repos/.../repos/spack_repo/builtin/packages/py_torch/package.py
```

Source provenance from the Spack recipe:

- homepage: `https://pytorch.org/`
- git source: `https://github.com/pytorch/pytorch.git`
- submodules: enabled
- current latest release entry: `2.12.0`, tag `v2.12.0`,
  commit `0d62256a2b23365f8e1604297eb23a6545102aa8`
- selected import smoke modules: `torch`, `torch.autograd`, `torch.nn`,
  `torch.utils`

## ODR-sensitive dependency policy

The Bazel-vendored Spack `v1.2.2` recipe universe currently records
`py-torch@2.12.0` as its latest recipe entry. The native build skeleton follows
upstream PyTorch source policy instead: GitHub release tag `v2.14.0` has
commit `2b3ec34829036a65cd9d1398ea72a0167dc37470` and
`third_party/protobuf` at submodule commit
`f0dc78d7e6e331b8c6bb2d5283e06aa26883ca7c`, which protobuf tags as
`v3.21.12` and reports as C++ API/package version `3.21.12`. The matching
Python package line is PyPI `protobuf==4.21.12`.

The native skeleton therefore keeps the protobuf family unified as hermetic
Spack `protobuf@21.12` (upstream/protoc API `3.21.12`) plus
`py-protobuf@4.21.12`, uses exact native override keys, and forces PyTorch down
the system-prefix path with `BUILD_CUSTOM_PROTOBUF=OFF`.

The two surfaces are intentionally separate until the Spack overlay grows a
`py-torch@2.14.0` recipe: `py_torch_build_graph.json` and
`py_torch_lean_fonts_build_graph.json` are reference-topology snapshots for
hermetic Spack `py-torch@2.12.0`, while `native/pytorch/plan.py` is the source
authority for the future native PyTorch `v2.14.0` build. The checked reference
graphs are allowed to contain `protobuf@3.13.0` with `py-protobuf@3.13.0`
because that pair is internally single-family; the native PyTorch planner does
not admit that pair.

The associated upstream build files do not make `gRPC`, `Abseil`, or Boost
top-level PyTorch source/build inputs:

- `gRPC` is not a `v2.14.0` submodule or CMake dependency for the selected
  native build path.
- `Abseil` appears in ONNX `v1.18.0`, which PyTorch pins as
  `third_party/onnx` commit `e709452ef2bbc1d113faf678c24e6d3467696e83`, only
  when ONNX either uses a protobuf `>=4.22.0` system package or enters its
  fallback `ONNX_BUILD_CUSTOM_PROTOBUF` path. That fallback downloads
  `abseil-cpp-20240722.1` and protobuf `29.2`. Neither condition applies to
  the selected PyTorch-native provider, which is protobuf `3.21.12`; do not mix
  ONNX's fallback Abseil/protobuf pair with the `protobuf@21.12` native provider
  family.
- `boost@1.90.0` remains a native-capable Spack graph node for packages that
  really depend on Boost. It is not a PyTorch source/build input for the
  selected native build path, so the PyTorch planner rejects a Boost prefix
  before it can enter `CMAKE_PREFIX_PATH`.

`native/pytorch/plan.py` records this policy in `source_dependency_policy` and
preflights the protobuf input by executing `protoc --version`; any protobuf
prefix that does not report `libprotoc 3.21.12` is rejected before a native
PyTorch build can start. It also reports the current hard blocker:
`py-protobuf@4.21.12` is the required Python half of the protobuf family, and
the repo-owned `vaso_overlay` Spack package repository now supplies that exact
recipe through the Bazel-vendored hermetic Spack path. The native
`@py_protobuf_native` prefix is still blocked because protobuf 4.21.x's C++
extension does not build against the current native Python 3.14 path. Until
that native prefix exists, native PyTorch preflight stays red and no full build
should start.

See `docs/pytorch-odr-dependencies.md` for the upstream source/build-file audit
behind these version choices.

## Build recipe

Spack models `py-torch` as a Python package with CUDA and ROCm mixins. The
current hermetic Spack `py-torch@2.12.0` reference recipe still invokes
PyTorch's historical setup front-end, which then drives CMake/Ninja:

```text
python setup.py install
```

For the native source target, upstream PyTorch `v2.14.0` has moved wheel builds
to `pyproject.toml` with `build-backend = "scikit_build_core.build"`;
`setup.py` rejects `bdist_wheel` and tells callers to use:

```text
python -m build --wheel --no-isolation
```

The shared contract is the environment forwarding into CMake: variables
beginning with `BUILD_`, `USE_`, or `CMAKE_` are the build-mechanism seam for
the Bazel-native skeleton. A Python-wheel repository rule must validate the
prefixes and then pass the recipe-derived environment to the upstream
scikit-build-core front-end, not let the build discover host libraries.

For the CUDA path relevant to this migration, the Spack recipe:

- requires `cuda_arch` when `+cuda`
- sets `CUDA_TOOLKIT_ROOT_DIR`, `CUDA_HOME`, and `CUDA_PATH` from the CUDA
  prefix
- computes `TORCH_CUDA_ARCH_LIST` from Spack's CUDA arch values
- sets `CUDNN_INCLUDE_DIR` and `CUDNN_LIBRARY` from the cuDNN prefix when
  `+cudnn`
- sets `NCCL_LIB_DIR` and `NCCL_INCLUDE_DIR` from the NCCL prefix when
  `+cuda+nccl`
- with `~custom-protobuf`, sets `BUILD_CUSTOM_PROTOBUF=OFF` and supplies the
  unified system protobuf family
- drives feature toggles through `USE_*` / `BUILD_*` environment variables
- maps BLAS providers to `BLAS`, `WITH_BLAS`, and provider-specific roots,
  including `OpenBLAS_HOME` for OpenBLAS
- prefers system dependencies for `benchmark`, `cpuinfo`, `eigen`, `fp16`,
  `fxdiv`, `gloo`, `nccl`, `nvtx`, `psimd`, `pthreadpool`, `pybind11`, `sleef`,
  and `ucc`

Important defaults and variants for the current upstream recipe:

- `cuda` defaults on for non-Darwin platforms
- `cudnn` defaults on when `+cuda`
- `nccl` defaults on for `+cuda platform=linux`
- `distributed`, `tensorpipe`, `fbgemm`, `kineto`, `xnnpack`, `qnnpack`,
  `openmp`, and `mkldnn` default on
- `rocm` defaults off and conflicts with `+cuda`
- `test` and `caffe2` default off

The current native skeleton intentionally narrows this surface to the
vendor-libtorch-compatible CUDA wheel path:

```text
USE_CUDA=1
USE_CUDNN=1
USE_NCCL=1
USE_SYSTEM_NCCL=1
BUILD_CUSTOM_PROTOBUF=OFF
USE_MKL=0
USE_MKLDNN=0
_GLIBCXX_USE_CXX11_ABI=1
TORCH_CUDA_ARCH_LIST=8.0;9.0;10.0
BUILD_TEST=0
BUILD_BINARY=0
BUILD_CAFFE2_OPS=0
```

That narrowing is a conscious migration interface: Spack's generic upstream
recipe remains the reference for dependency shape and setup.py/CMake mechanics,
while the emitted prefix must match the vendor-libtorch consumer ABI contract.

## Hermetic dependency contract

The corresponding build mechanism for the native skeleton is `python-wheel`.
Before any full build can run, the repository rule and planner must prove these
inputs come from Bazel/insula prefixes:

- CUDA prefix with `bin/nvcc`
- cuDNN prefix with `include/cudnn.h` and a `lib` or `lib64` directory
- NCCL prefix with `include/nccl.h` and a `lib` or `lib64` directory
- Python prefix with `bin/python3`
- CMake prefix with `bin/cmake`
- Ninja prefix with `bin/ninja`
- OpenBLAS prefix with `include/cblas.h` or `include/openblas_config.h` and a
  `lib` or `lib64` directory
- PyTorch-source-aligned protobuf prefix with `include/google/protobuf`,
  `bin/protoc` reporting `libprotoc 3.21.12`, and a `lib` or `lib64` directory

`gRPC`, `Abseil`, and Boost are not accepted as PyTorch-native prefix inputs
for the selected `protobuf@21.12` source path. Supplying any of them to
`native/pytorch/plan.py` fails preflight so `CMAKE_PREFIX_PATH` remains limited
to `python`, `cmake`, and `protobuf`.
- sealed CUDA/Ubuntu rootfs as the insula base root

`//tools:hermetic_native_deps_guard_test` checks that the PyTorch repository
rule declares this Python-wheel mechanism and threads the planned prefixes as
Bazel inputs. `//native/pytorch:plan_test` checks the concrete prefix surface
and refuses `--execute` unless `VASO_NATIVE_PYTORCH_TOKEN=build-native-pytorch`
is present.
`//tools:pytorch_odr_policy_check_test` additionally checks the exact
protobuf/gRPC/Abseil/Boost policy against the ledger's py-torch frontier graph
and the other checked snapshots, `native_overrides.json`, and the native
PyTorch source plan, so downstream ODR families cannot drift silently (details
in `docs/pytorch-odr-dependencies.md`).

## Prefix and ABI gate target

The eventual native prefix must emit:

```text
prefix/
  artifacts/wheels/torch-*.whl
  lib/site-packages/torch/include/
  lib/site-packages/torch/lib/
```

The future ABI gate must compare that tree against the selected Spack external
or vendor-libtorch-compatible reference prefix:

- wheel artifact exists and matches the selected build version
- `torch/include` header surface matches
- `torch/lib` shared-object layout, SONAMEs, and exported dynamic symbols match
- `import torch`, `torch.cuda.is_available()`, and a small CUDA kernel smoke
  run identically inside the sealed insula

Current status: recipe captured and dry-run/preflight skeleton verified. Native
PyTorch has not been built and cannot build without the explicit token.
