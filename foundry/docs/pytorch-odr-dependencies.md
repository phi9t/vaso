# PyTorch ODR-sensitive dependency audit

This audit fixes the protobuf/gRPC/Abseil/Boost policy for the native PyTorch
source target. It uses upstream source/build files plus the Bazel-owned
hermetic Spack package repository; host Spack is not an input.

## Upstream PyTorch source target

The native skeleton targets upstream PyTorch `v2.14.0`, which currently resolves
on GitHub to commit `2b3ec34829036a65cd9d1398ea72a0167dc37470`. As of the
2026-09-27 refresh, sorted upstream release tags showed `v2.14.0` as the latest
stable release tag and `v2.14.1-rc1` as a later release candidate.

PyTorch `v2.14.0` has these relevant source facts, checked in `.gitmodules`,
`CMakeLists.txt`, `cmake/ProtoBuf.cmake`,
`cmake/public/protobuf.cmake`, and `pyproject.toml`:

- `.gitmodules` contains `third_party/protobuf`, and the gitlink at
  `third_party/protobuf` is
  `f0dc78d7e6e331b8c6bb2d5283e06aa26883ca7c`; upstream protobuf tags that
  exact commit as `v3.21.12`. The Spack-style protobuf release tag `v21.12`
  peels to the same commit; the annotated tag object itself is
  `f502b8e9c831bda0bea57d9cbeefca3eb76e4254`.
- The PyTorch `v2.14.0` tree does not contain gitlinks at
  `third_party/abseil-cpp`, `third_party/boost`, or `third_party/grpc`.
- `CMakeLists.txt` defines `BUILD_CUSTOM_PROTOBUF` and defaults it on, but when
  it is forced off the build follows `cmake/ProtoBuf.cmake` into
  `cmake/public/protobuf.cmake`, which calls `find_package(Protobuf)` and
  consumes the configured system protobuf/protoc.
- `pyproject.toml` uses `build-backend = "scikit_build_core.build"`; the native
  skeleton therefore uses `python -m pip wheel --no-build-isolation --no-deps`,
  not `setup.py bdist_wheel`.
- PyTorch `v2.14.0` has no top-level `grpc`, `grpc-cpp`, `py-grpcio`,
  `abseil-cpp`, or Boost submodule, and those packages are not PyTorch CMake
  provider inputs for the selected system-protobuf path.

## ONNX submodule caveat

PyTorch `v2.14.0` pins `third_party/onnx` to
`e709452ef2bbc1d113faf678c24e6d3467696e83`, which is ONNX `v1.18.0`.

ONNX `v1.18.0` does not make Abseil a required provider for the selected
protobuf line. Its `CMakeLists.txt` has two separate cases:

- When `ONNX_BUILD_CUSTOM_PROTOBUF=OFF` and the discovered `Protobuf_VERSION` is
  `>=4.22.0`, ONNX requires `find_package(absl REQUIRED)` and
  `find_package(utf8_range REQUIRED)`.
- When ONNX falls back to building custom protobuf, it fetches
  `abseil-cpp-20240722.1` with SHA1
  `0d6b07c6f3352981d3660978e109f2bc14594a3d` and protobuf `29.2` with SHA1
  `a5639ffb17e3743d696baf16bf377fbe752b6a1f`, then records CMake
  `Protobuf_VERSION` as `5.29.2`.

Neither case applies to the native PyTorch contract, which sets
`BUILD_CUSTOM_PROTOBUF=OFF`, `ONNX_BUILD_CUSTOM_PROTOBUF=OFF`,
`ONNX_USE_PROTOBUF_SHARED_LIBS=ON`, and supplies protobuf `3.21.12`.

The standalone ONNX Python package is a separate compatibility surface. ONNX
`v1.18.0`'s `pyproject.toml` declares build/runtime protobuf requirements of
`protobuf>=4.25.1`, while this PyTorch-native island requires Python protobuf
`4.21.12` to match the C++ protobuf `3.21.12` source family. Therefore the
PyTorch build may use the vendored ONNX source tree, but it must not install or
resolve the standalone ONNX Python package into the same prefix unless a new
compatibility audit intentionally moves the whole protobuf family forward.

## Hermetic Spack mapping

The Bazel-owned hermetic Spack `py-torch` recipe currently records upstream
Spack's conservative `~custom-protobuf` edge:

```text
depends_on("protobuf@3.13.0", when="@1.10:")
depends_on("py-protobuf@3.13", when="@1.10:")
```

That recipe edge is a reference recipe fact, not the native PyTorch source
target's provider family. The checked-in `py_torch_build_graph.json` and
`py_torch_lean_fonts_build_graph.json` (the ledger's py-torch frontier graph)
snapshots therefore remain valid only as
`py-torch@2.12.0` reference-topology graphs with the internally consistent
`protobuf@3.13.0` / `py-protobuf@3.13.0` family. They must not be used as the
native PyTorch `v2.14.0` source provider authority.

The native PyTorch source target must use:

```text
protobuf@21.12         # required PyTorch build input; upstream/protoc API 3.21.12
py-protobuf@4.21.12    # required family peer; hermetic overlay recipe is available
boost@1.90.0           # native-capable Spack graph node, not a PyTorch CMake input
```

`protobuf@21.12` is available in the hermetic Spack package repository and is
the version built by `@protobuf_native`. The active native override key is exact
(`"protobuf@21.12": "@protobuf_native//:lib"`) so an older Spack-selected
`protobuf@3.13.0` node cannot silently receive the native provider.

`py-protobuf@4.21.12` is represented in the native skeleton but is not an active
native override: the Bazel-owned hermetic Spack overlay declares the exact
recipe version, while the native `@py_protobuf_native` prefix remains blocked
because protobuf 4.21.x's C++ extension does not build against the current
native Python 3.14 path. Do not substitute the reference `py-torch` recipe's
older `py-protobuf@3.13` edge.

## Version contract

The source audit was refreshed against upstream GitHub on 2026-09-27:

```text
PyTorch tag v2.14.0:
  2b3ec34829036a65cd9d1398ea72a0167dc37470
PyTorch third_party/protobuf gitlink:
  f0dc78d7e6e331b8c6bb2d5283e06aa26883ca7c
PyTorch third_party/onnx gitlink:
  e709452ef2bbc1d113faf678c24e6d3467696e83
protobuf tags:
  v3.21.12 -> f0dc78d7e6e331b8c6bb2d5283e06aa26883ca7c
  v21.12^{} -> f0dc78d7e6e331b8c6bb2d5283e06aa26883ca7c
ONNX tag v1.18.0:
  e709452ef2bbc1d113faf678c24e6d3467696e83
```

The native PyTorch build policy is executable in
`native/pytorch/plan.py` as `source_dependency_policy.dependency_contract`:

```text
protobuf     required  protobuf@21.12      accepted build prefix
py-protobuf  required  py-protobuf@4.21.12 blocked until hermetic provider exists
grpc         no        none                rejected ODR provider
grpc-cpp     no        none                rejected ODR provider
py-grpcio    no        none                rejected ODR provider
abseil-cpp   no        none                rejected ODR provider
boost        no        boost@1.90.0        Spack graph node only
```

`boost@1.90.0` remains exact-keyed in `native_overrides.json` for consumers that
really depend on the Spack Boost node. The PyTorch native planner still rejects
a `boost` prefix input because upstream PyTorch `v2.14.0` does not make Boost a
source/build input for this build path.

## Enforced policy

`native/pytorch/plan.py` enforces the selected family by:

- accepting only `cuda`, `cudnn`, `nccl`, `python`, `cmake`, `ninja`,
  `openblas`, and `protobuf` prefix keys;
- rejecting `grpc`, `grpc-cpp`, `py-grpcio`, `abseil-cpp`, and `boost` prefix
  keys before they can enter `CMAKE_PREFIX_PATH`;
- checking `protobuf/bin/protoc --version` and requiring
  `libprotoc 3.21.12`;
- failing the PyTorch preflight until the exact hermetic
  `py-protobuf@4.21.12` provider is available, so a nearby
  `py-protobuf@4.21.9` or the Spack recipe's `py-protobuf@3.13` edge cannot be
  mistaken for the selected family;
- emitting `CMAKE_PREFIX_PATH` with only `python`, `cmake`, and `protobuf`;
- setting `BUILD_CUSTOM_PROTOBUF=OFF` and
  `PROTOBUF_PROTOC_EXECUTABLE=<protobuf>/bin/protoc`;
- setting `ONNX_BUILD_CUSTOM_PROTOBUF=OFF` and
  `ONNX_USE_PROTOBUF_SHARED_LIBS=ON`, then recording
  `source_dependency_policy.onnx_protobuf_guardrail` so the forbidden ONNX
  Abseil/protobuf fallback is visible in every dry-run plan.

`//tools:pytorch_odr_policy_check_test` keeps the policy executable across the
checked-in graph and native-source surfaces. It takes its ODR package families
and companion-version normalization from `tools/spack_to_bazel.py`, the same
tables the lock generator enforces, and checks the following.

**Override keys.** Every protobuf/gRPC/Abseil/Boost native override is keyed
exactly `name@X.Y.Z`. These all fail:
- unversioned keys;
- ranges such as `protobuf@3:`;
- variant or compiler keys such as `protobuf+shared@21.12`;
- case or whitespace variants.

**Admitted keys.** The ODR-family native overrides are exactly the ones the
PyTorch plan admits, with the plan's labels. A family may only admit its own
selected provider (`ODR_PROVIDER_FAMILIES[*].native_override_key` must equal
its `selected_cpp_provider`/`selected_provider`), so today the set is
`protobuf@21.12` and `boost@1.90.0`. These fail:
- any extra exact key, such as `abseil-cpp@<v>`, `grpc@<v>` or a second
  protobuf;
- a missing key;
- a label mismatch.

**py-protobuf.** `py-protobuf@4.21.12` becomes a required native override only
once the plan's `python_native_override_status` stops being `blocked`, and the
plan must then supply `python_native_override_label`.

**Graphs.** The ledger's py-torch frontier graph
(`frontier_graphs["py-torch"].graph`), `py_torch_build_graph.json` and
`protobuf_policy_check_build_graph.json` must:
- be single-version per ODR package;
- be single-family across protobuf/Python-protobuf companions (`py-protobuf`
  `M.x.y` ↔ `protobuf` `x.y` for every major ≥ 4);
- contain no gRPC or Abseil node, and no ODR node without a version;
- carry graph-node-only providers (`boost`) at exactly the plan's pinned
  version.

**Plan relations.**
- The protobuf source commit equals PyTorch's `third_party/protobuf` gitlink.
- The Spack-style `v21.12` tag peels to that same commit.
- The dependency contract names the same provider as `ODR_PROVIDER_FAMILIES`
  for every family.
- The selected protobuf/py-protobuf providers are one family.
- Rejected ODR providers are rejected prefix keys, and no key is both
  accepted and rejected.

The literal source pins (PyTorch `v2.14.0`, protobuf `v3.21.12` / Spack
`v21.12`, annotated tag `f502b8e9…`, vendored ONNX `v1.18.0`) live in
`//native/pytorch:plan_test`. `//tools:pytorch_odr_policy_check_unit_test`
exercises each rejection above with fixture overrides, graphs, and mutated
copies of `plan.py`. Malformed inputs exit 2 with an `input error:` line.

`protobuf_policy_check_build_graph.json` is a 22-node protobuf-rooted hermetic
Spack graph captured on 2026-09-27. It is the only checked-in capture that
resolves the native provider's `protobuf@21.12` (+shared); its root spec was not
recorded. The older `protobuf_build_graph.json` and
`protobuf_native_build_graph.json` captures still resolve `protobuf@3.13.0`,
so they predate the `protobuf@21.12` provider and are not evidence for it.

## Python/protobuf compatibility run

`//tools:pytorch_python_protobuf_compat_test` is the source-policy gate for the
Python/protobuf/PyTorch island before a native PyTorch build can move from
preflight to execution. The checker is pure and read-only: it does not query
host Spack, host Python, or the network. Live build/import probes must run later
inside the sealed insula using the Bazel-owned Spack graph.

The accepted source contract is:

```text
python@3.13.x
py-torch@2.14.0
protobuf@21.12      # upstream/protoc API 3.21.12
py-protobuf@4.21.12
vendored ONNX v1.18.0 at e709452ef2bbc1d113faf678c24e6d3467696e83
```

The gate rejects:

- Python `3.14.x` with `py-protobuf@4.21.12`, because the current source-build
  failure is the `google/protobuf/pyext` `PyFrameObject` incompatibility;
- any C++ protobuf version other than `3.21.12` or Python protobuf version other
  than `4.21.12` for this island;
- ONNX external protobuf `>=4.22.0`, because ONNX then requires Abseil and
  `utf8_range`;
- ONNX custom-protobuf fallback, because it fetches protobuf `29.2` plus
  Abseil `20240722.1`;
- standalone ONNX Python packaging in the same prefix, because ONNX `v1.18.0`
  asks for `protobuf>=4.25.1`.

The structured failure classes are:

```text
PYTHON_RANGE_UNSUPPORTED
PYTHON_COMPATIBILITY_ISLAND_MISMATCH
PY_PROTOBUF_BUILD_INCOMPATIBLE
PROTOBUF_FAMILY_MISMATCH
ONNX_ABSEIL_PROTOBUF_TRIGGER
ONNX_CUSTOM_PROTOBUF_FALLBACK
STANDALONE_ONNX_PYTHON_PROTOBUF_MISMATCH
PYTORCH_VERSION_UNSUPPORTED
```

The live insula run that consumes this checker still needs to prove:

- hermetic Spack can concretize `python@3.13`, `protobuf@21.12`, and
  `py-protobuf@4.21.12` from Bazel-owned repos/overlays;
- `py-protobuf@4.21.12` source-builds and imports under Python `3.13.x`;
- a generated message round-trips through C++ protobuf and Python protobuf;
- the PyTorch planner accepts only the selected prefixes and emits the ONNX
  guard environment shown above.

`tools/native_build_mechanism_guard.py` also treats Boost.Build as a
mechanism-specific verifier: a native Boost repository rule must check
`VASO_IN_INSULA=1` at repository-rule entry, before source extraction, in
addition to the generated shell build guard. That keeps the exact
`boost@1.90.0` provider from materializing outside the sealed insula even though
Boost is not admitted into the PyTorch CMake prefix set.
