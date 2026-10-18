# cudnn@9.21.0.82-12

## Hermetic Spack Evidence

- Source of truth: Bazel-vendored Spack `@spack_dist//:spack`, pinned to
  upstream Spack `v1.2.2`.
- Recipe path in the hermetic Spack cache:
  `/vaso/cache/spack/user/package_repos/.../repos/spack_repo/builtin/packages/cudnn/package.py`.
- The recipe is `Cudnn(Package)`, a Spack `generic` package that installs a
  NVIDIA redistributable binary archive.
- Concrete PyTorch-frontier node:
  - package: `cudnn`
  - version: `9.21.0.82-12`
  - topo index: `59`
  - Spack hash in the graph-only PyTorch DAG:
    `4aaj366g7fsiqjnhvsbbk5p7rw7nna6t`
  - dependency: `cuda@12.9.1`
- Reference install evidence was generated inside the isolated CUDA 12.9.1
  insula with Bazel's hermetic Spack:

```bash
SPACK_ROOT_PKG='cudnn@9.21.0.82-12 ^cuda@12.9.1' \
VASO_SPACK_INSTALL_ARGS='--only package' \
VASO_FORCE_FETCH_REPOS=@spack_dist \
./run.sh
```

The Spack install output marked CUDA as an external derived from the sealed
insula:

```text
[e] yfvwiwl cuda@12.9.1 /usr/local/cuda
```

The resulting hermetic Spack cuDNN reference prefix was:

```text
/vaso/cache/spack/opt/spack/linux-icelake/cudnn-9.21.0.82-12-zjmc3jvphnlhr52uinusyimqvg5wbr5p
```

## Recipe Contract

For Linux x86_64 and CUDA 12, Spack's recipe pins:

```text
url    = https://developer.download.nvidia.com/compute/cudnn/redist/cudnn/linux-x86_64/cudnn-linux-x86_64-9.21.0.82_cuda12-archive.tar.xz
sha256 = 9f97dde6528a1733550c79ee3f5ba3e9a0638e0e1670e4b167d56e0ef6d5910b
```

The package install body is equivalent to:

```python
install_tree(".", prefix)
```

There is no source configure/build phase to replay. The ABI-relevant contract is
the copied archive payload:

```text
prefix/
  include/cudnn_version.h
  lib/libcudnn.so
  lib/libcudnn_adv.so
  lib/libcudnn_cnn.so
  lib/libcudnn_engines_precompiled.so
  lib/libcudnn_engines_runtime_compiled.so
  lib/libcudnn_engines_tensor_ir.so
  lib/libcudnn_graph.so
  lib/libcudnn_heuristic.so
  lib/libcudnn_ops.so
```

The Spack graph still has a normal `cuda` dependency edge. The provider flip
must therefore validate a CUDA prefix rather than silently using rootfs or host
cuDNN state.

## Native Boundary Decision

cuDNN is a `binary-archive` native mechanism in this experiment. It is not a
source build and it is not an SDK/rootfs boundary like CUDA. The native provider
replays Spack's `install_tree(".", prefix)` with the exact Spack-pinned NVIDIA
archive and SHA256.

`native/cudnn/cudnn.bzl` validates:

- the repository rule is evaluated inside the insula (`VASO_IN_INSULA=1`);
- `CUDA_PREFIX` comes from the Bazel-native CUDA boundary and provides
  `bin/nvcc`;
- the extracted archive contains `include/cudnn_version.h`;
- the extracted archive contains `lib/libcudnn.so` or `lib64/libcudnn.so`;
- the header macros report `9.21.0`;
- the copied prefix still contains the header and library;
- `binary_archive.json` records `mechanism = binary-archive`, package
  `cudnn`, version `9.21.0.82-12`, header version `9.21.0`, and the CUDA
  dependency prefix used for the install.

This is covered by the mechanism-specific hermetic dependency verifier:

```text
//tools:hermetic_native_deps_guard_test
native/cudnn/cudnn.bzl: binary-archive: CUDA_PREFIX
```

## Current Status

cuDNN is flipped in `native_overrides.json` as:

```json
"cudnn": "@cudnn_native//:lib"
```

The smoke test:

```text
//synthetic:use_cudnn_native
cudnn:9.21.0.82-12:cuda=/vaso/cache/bazel/output-base/external/+cuda_native+cuda_native/prefix:ok
```

The focused native parity run used the isolated CUDA 12.9.1 estate:

```bash
BAZEL_BIN=$HOME/.local/bin/bazel-9.2.0 \
VASO_ESTATE_ROOT=$VASO_ESTATE_ROOT \
SPACK_ROOT_PKG='cudnn@9.21.0.82-12 ^cuda@12.9.1' \
VASO_SPACK_INSTALL_ARGS='--only package' \
VASO_NATIVE=1 \
VASO_LOCK_OUT=/workspace/experiment/cudnn_native_spack_graph.lock.json \
VASO_BUILD_GRAPH_OUT=/workspace/experiment/cudnn_native_build_graph.json \
VASO_FORCE_FETCH_REPOS=@spack_dist \
VASO_SKIP_CONSUMER_TESTS=1 \
./run.sh
```

The run stayed inside the bwrap insula, used Bazel's vendored
`@spack_dist//:spack`, and passed `//synthetic:cudnn_abi_parity`.

The ABI gate compared:

- 57 reference layout paths vs 57 candidate layout paths;
- byte-identical `include/cudnn_version.h`, SHA256
  `37829f4c1bb03f1d2b9db6614a098673a04674a53064a6b933d7c8b10831c8fd`;
- matching SONAME and exported-symbol sets for:
  - `libcudnn`
  - `libcudnn_adv`
  - `libcudnn_cnn`
  - `libcudnn_engines_precompiled`
  - `libcudnn_engines_runtime_compiled`
  - `libcudnn_engines_tensor_ir`
  - `libcudnn_graph`
  - `libcudnn_heuristic`
  - `libcudnn_ops`

The next PyTorch-frontier node after the native CUDA/cuDNN pair is
`curl@8.20.0` at topo index 60.
