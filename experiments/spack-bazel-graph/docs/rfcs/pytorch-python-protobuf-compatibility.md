# RFC: PyTorch Python/Protobuf Compatibility Island

Status: proposed follow-up for the main thread
Date: 2026-09-27

## Goal

Prove a coherent Python/protobuf/PyTorch build island before allowing the native
PyTorch provider to move past dry-run preflight. The target island is:

```text
python@3.13.x
py-torch@2.14.0
protobuf@21.12      # upstream/protoc API 3.21.12
py-protobuf@4.21.12
vendored ONNX v1.18.0 at e709452ef2bbc1d113faf678c24e6d3467696e83
```

The output of this RFC is not a full PyTorch build. The output is a hermetic
compatibility gate that proves the selected Python and protobuf providers can
coexist with the PyTorch source tree and that ONNX does not pull in an alternate
protobuf/Abseil universe.

## Non-Negotiable Constraints

- Use only Bazel-owned hermetic Spack and Bazel-owned package repos inside the
  sealed CUDA insula. Host Spack is not a data source or an execution substrate.
- Do not run the full native PyTorch build without the trusted token
  `build-native-pytorch`.
- Do not upgrade protobuf opportunistically. If protobuf moves, move the full
  protobuf family intentionally and re-audit ONNX, Abseil, utf8_range, gRPC,
  and Python protobuf together.
- Do not install standalone ONNX Python packaging into the PyTorch-native
  prefix in this island. PyTorch may use its vendored ONNX source tree.
- Keep native override keys exact for ODR-sensitive packages:
  `protobuf@21.12`, `py-protobuf@4.21.12`, `abseil-cpp@...`,
  `grpc@...`, `grpc-cpp@...`, `py-grpcio@...`, and `boost@...`.

## Source Facts

The current source-policy guard landed in
`//tools:pytorch_python_protobuf_compat_test` and
`//native/pytorch:plan_test`.

Upstream facts already encoded in the repo:

- PyTorch target: `v2.14.0`
- PyTorch commit: `2b3ec34829036a65cd9d1398ea72a0167dc37470`
- PyTorch `third_party/protobuf` gitlink:
  `f0dc78d7e6e331b8c6bb2d5283e06aa26883ca7c`
- Protobuf tag for that gitlink: `v3.21.12`; Spack-style tag `v21.12`
  peels to the same commit
- PyTorch `third_party/onnx` gitlink:
  `e709452ef2bbc1d113faf678c24e6d3467696e83`
- ONNX tag for that gitlink: `v1.18.0`
- ONNX `v1.18.0` CMake requires Abseil and `utf8_range` when an external
  `Protobuf_VERSION >= 4.22.0` is used
- ONNX custom-protobuf fallback fetches protobuf `29.2` and Abseil
  `20240722.1`
- ONNX `v1.18.0` standalone Python packaging declares `protobuf>=4.25.1`
- `py-protobuf@4.21.12` currently fails against the native Python `3.14` path
  at `google/protobuf/pyext` with an incomplete `PyFrameObject`

## Required Outcome

Add a live hermetic compatibility run, executed inside `./run.sh`, that proves:

1. Bazel-owned hermetic Spack can represent the selected island:
   `python@3.13.x`, `protobuf@21.12`, and `py-protobuf@4.21.12`.
2. `py-protobuf@4.21.12` source-builds and imports under Python `3.13.x` inside
   the insula.
3. The native C++ protobuf prefix and Python protobuf package are one coherent
   family: C++ protoc reports `libprotoc 3.21.12`, Python reports `4.21.12`,
   and a generated message round-trips between C++ and Python.
4. PyTorch's dry-run planner emits only the selected protobuf family and the
   ONNX guard environment:

   ```text
   BUILD_CUSTOM_PROTOBUF=OFF
   ONNX_BUILD_CUSTOM_PROTOBUF=OFF
   ONNX_USE_PROTOBUF_SHARED_LIBS=ON
   PROTOBUF_PROTOC_EXECUTABLE=<protobuf>/bin/protoc
   CMAKE_PREFIX_PATH=<python>:<cmake>:<protobuf>
   ```

5. No accepted PyTorch prefix input admits `abseil-cpp`, `utf8_range`, `grpc`,
   `grpc-cpp`, `py-grpcio`, `boost`, or standalone `onnx`.

## Proposed Implementation

### Task 1: Python 3.13 Compatibility Island Lock

Create a hermetic, side-lock capture for the selected compatibility island.

Files:

- Modify: `experiments/spack-bazel-graph/run.sh`
- Modify or extend: `experiments/spack-bazel-graph/tools/spack_lock.sh`
- Create: `experiments/spack-bazel-graph/pytorch_python313_protobuf_build_graph.json`
  only if the main thread wants a checked-in reference artifact
- Create: `experiments/spack-bazel-graph/pytorch_python313_protobuf_spack_graph.lock.json`
  only if the main thread wants a checked-in reference artifact

Implementation requirements:

- Run through `./run.sh` or the same `bazel_insula` path; do not call host
  `spack`.
- Use `@spack_dist//:spack` from Bazel runfiles.
- Concretize the minimal compatibility target, not the whole PyTorch build.
- Require Python `3.13.x`; fail if hermetic Spack resolves Python `3.14.x`.
- Require `protobuf@21.12` and `py-protobuf@4.21.12`.
- Keep the output as a side lock unless the main thread chooses to update the
  canonical lock.

Verification:

```bash
VASO_GRAPH_ONLY=1 \
SPACK_ROOT_PKG='<compat target>' \
VASO_LOCK_OUT=/workspace/experiment/pytorch_python313_protobuf_spack_graph.lock.json \
VASO_BUILD_GRAPH_OUT=/workspace/experiment/pytorch_python313_protobuf_build_graph.json \
./run.sh
```

Expected evidence:

- The command runs inside the CUDA insula.
- The Spack executable reported by the logs is Bazel-owned hermetic Spack.
- The generated graph contains exactly one protobuf C++ family:
  `protobuf@21.12`.
- The generated graph contains exactly one Python protobuf package:
  `py-protobuf@4.21.12`.
- The generated graph contains Python `3.13.x`, not `3.14.x`.

### Task 2: Native Python 3.13 Provider

Capture Python `3.13.x` as a native provider or compatibility-only provider
without destabilizing the existing Python `3.14` frontier.

Files:

- Create or extend: `experiments/spack-bazel-graph/native/python_313/`
- Create or extend: `experiments/spack-bazel-graph/docs/recipes/python.md`
- Modify: `experiments/spack-bazel-graph/native_overrides.json` only with an
  exact `python@3.13.x` key if the main thread chooses to flip the side lock
- Extend: `experiments/spack-bazel-graph/tools/native_build_mechanism_guard.py`
  only if a new mechanism/verifier field is needed

Implementation requirements:

- Reuse the existing native Python recipe pattern where possible.
- Keep Python `3.13.x` separate from the existing Python `3.14` native path
  until the migration ledger and downstream Python packages are intentionally
  moved.
- Build, install, and verify only inside the insula.
- Add a mechanism-specific hermetic dependency verifier for the Python build.

Verification:

- Native prefix parity against hermetic Spack Python `3.13.x`.
- `python3 -c 'import sys; print(sys.version_info[:2])'` reports `(3, 13)`.
- No host library paths appear in Python's build config, RPATH, or extension
  module link lines.

### Task 3: py-protobuf 4.21.12 on Python 3.13

Make `py-protobuf@4.21.12` build from source against native `protobuf@21.12`
and Python `3.13.x`.

Files:

- Modify: `experiments/spack-bazel-graph/native/py_protobuf/py_protobuf.bzl`
- Modify: `experiments/spack-bazel-graph/native/py_protobuf/BUILD.bazel`
- Modify: `experiments/spack-bazel-graph/docs/recipes/py-protobuf.md`
- Extend synthetic tests under `experiments/spack-bazel-graph/synthetic/`

Implementation requirements:

- Consume `@protobuf_native//:prefix_path.txt` and the Python `3.13.x` prefix
  through Bazel labels.
- Set `PROTOC=<protobuf>/bin/protoc` and verify `libprotoc 3.21.12` before
  invoking the Python build.
- Build from the `protobuf-4.21.12` source archive, not a wheel downloaded from
  PyPI.
- Install into a prefix with Python `3.13` site-packages paths.
- Do not activate a native override for `py-protobuf@4.21.12` until the import
  and roundtrip gates pass.

Verification:

- `//synthetic:use_py_protobuf_native` imports `google.protobuf`, forces the C++
  implementation path where applicable, and reports version `4.21.12`.
- `//synthetic:py_protobuf_prefix_parity` compares the native prefix against
  the hermetic Spack reference prefix for Python `3.13.x`.
- `//tools:hermetic_native_deps_guard_test` classifies the build as
  `python-pip-install` and sees explicit Python/protobuf/package-tool prefixes.

### Task 4: Cross-Language Protobuf Roundtrip

Add a small generated-message fixture that proves the C++ and Python protobuf
halves are compatible in one insula process family.

Files:

- Create: `experiments/spack-bazel-graph/synthetic/protobuf_roundtrip/roundtrip.proto`
- Create: `experiments/spack-bazel-graph/synthetic/protobuf_roundtrip/write_message.cc`
- Create: `experiments/spack-bazel-graph/synthetic/protobuf_roundtrip/read_message.py`
- Modify: `experiments/spack-bazel-graph/synthetic/BUILD.bazel`

Implementation requirements:

- Generate C++ and Python bindings with the same hermetic
  `protobuf@21.12` `protoc`.
- C++ writer serializes a message containing scalar, repeated, and nested
  fields.
- Python reader imports `google.protobuf`, parses the payload, validates exact
  field values, and prints a stable digest line.
- Run with Python `3.13.x` and `py-protobuf@4.21.12`.

Verification:

```bash
bazel test //synthetic:protobuf_python_cpp_roundtrip
```

Expected output contract:

```text
protobuf-roundtrip:3.21.12:4.21.12:ok
```

### Task 5: ONNX Guardrail Live Check

Turn the source-policy ONNX guard into a live dry-run verifier that inspects the
actual PyTorch plan output.

Files:

- Modify: `experiments/spack-bazel-graph/tools/pytorch_python_protobuf_compat.py`
- Modify: `experiments/spack-bazel-graph/tools/pytorch_python_protobuf_compat_test.py`
- Modify: `experiments/spack-bazel-graph/native/pytorch/plan.py` only if new
  plan fields are needed

Implementation requirements:

- Read a PyTorch `build_plan.json` emitted by `native/pytorch/plan.py`.
- Assert `CMAKE_PREFIX_PATH` contains only `python`, `cmake`, and `protobuf`.
- Assert `BUILD_CUSTOM_PROTOBUF=OFF`.
- Assert `ONNX_BUILD_CUSTOM_PROTOBUF=OFF`.
- Assert `ONNX_USE_PROTOBUF_SHARED_LIBS=ON`.
- Fail if the plan admits standalone `onnx`, `abseil-cpp`, `utf8_range`,
  `grpc`, `grpc-cpp`, `py-grpcio`, or `boost` as PyTorch prefix inputs.
- Fail if the plan's protobuf version is not `libprotoc 3.21.12`.

Verification:

```bash
bazel test //tools:pytorch_python_protobuf_compat_test //native/pytorch:plan_test
```

### Task 6: Native Override Flip for py-protobuf

Flip `py-protobuf@4.21.12` only after Tasks 1 through 5 pass.

Files:

- Modify: `experiments/spack-bazel-graph/native_overrides.json`
- Modify generated side lock or canonical lock according to the main thread's
  chosen landing path
- Update: `experiments/spack-bazel-graph/migration_ledger.json`

Implementation requirements:

- Add exactly:

  ```json
  "py-protobuf@4.21.12": "@py_protobuf_native//:lib"
  ```

- Do not add unqualified `py-protobuf` or `protobuf` keys.
- Do not flip if the graph still contains `py-protobuf@3.x`, `py-protobuf@6.x`,
  `protobuf@3.13.0`, or `protobuf@32.x` in the same downstream process.
- Do not add Abseil, utf8_range, gRPC, or standalone ONNX to the PyTorch build
  prefix set as part of this flip.

Verification:

```bash
./run.sh
```

Expected evidence:

- The default `//tools:pytorch_python_protobuf_compat_test` passes inside the
  insula.
- `//tools:spack_to_bazel_unit_test` still rejects ambiguous ODR overrides.
- The py-protobuf synthetic import test passes.
- The cross-language roundtrip test passes.
- PyTorch planner preflight is still red only for the full native PyTorch build
  gate, not for protobuf-family incoherence.

## Review Focus

- Python version drift: a resolver silently returns Python `3.14.x`; the
  compatibility run must fail before any py-protobuf build.
- Protobuf family drift: C++ protobuf and Python protobuf do not share the
  `3.21.12` / `4.21.12` family; the checker must fail with
  `PROTOBUF_FAMILY_MISMATCH`.
- ONNX Abseil trigger: ONNX sees external protobuf `>=4.22.0`; the checker must
  fail with `ONNX_ABSEIL_PROTOBUF_TRIGGER`.
- ONNX fallback trigger: ONNX cannot find the selected protobuf/protoc and
  falls back to protobuf `29.2` plus Abseil `20240722.1`; the checker must fail
  with `ONNX_CUSTOM_PROTOBUF_FALLBACK`.
- Standalone ONNX Python package: `onnx` packaging pulls `protobuf>=4.25.1`;
  the checker must fail with `STANDALONE_ONNX_PYTHON_PROTOBUF_MISMATCH`.
- Host leakage: any build/import probe resolves host Spack, host Python, host
  `protoc`, host headers, or host libraries; the insula/hermetic-deps guards
  must fail loudly.

## Suggested Main-Thread Sequence

1. Start from commit `35eeb53` or later, which contains the source-policy guard.
2. Keep the compatibility island in a side lock until it passes live import and
   roundtrip tests.
3. Capture Python `3.13.x` or otherwise provide a hermetic Python `3.13.x`
   prefix for this island.
4. Build `py-protobuf@4.21.12` from source against `protobuf@21.12` and Python
   `3.13.x`.
5. Add the C++/Python protobuf roundtrip test.
6. Extend the compatibility checker to validate real `build_plan.json` output.
7. Flip `py-protobuf@4.21.12` with an exact native override only after all
   previous gates pass.

## Out of Scope

- Full native PyTorch execution.
- Moving the whole graph from Python `3.14` to Python `3.13`.
- Upgrading protobuf to `4.25`, `5.x`, `6.x`, or `29.x`.
- Adding standalone ONNX Python packaging to the PyTorch native prefix.
- Introducing Abseil, utf8_range, gRPC, or Boost as PyTorch prefix inputs.
