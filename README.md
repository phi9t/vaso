# Vaso

**A Bubblewrap-native insula rootfs for reproducible PyTorch, JAX, and GPU-ML
development.**

Vaso is a Linux filesystem boundary for ML repositories that need CUDA
toolchains, native wheel builds, GPU workloads, and multi-repository source
trees. A Vaso insula is a materialized root filesystem entered with
unprivileged [Bubblewrap](https://github.com/containers/bubblewrap). It is not
a long-running container, and normal execution does not depend on a Docker
daemon. Docker can still be used to materialize a rootfs bundle.

> [!IMPORTANT]
> Vaso is a reference implementation and release-workflow repository. The
> public checkout can inspect the design, run lightweight tests, and exercise
> local preflight code. Full rootfs builds, native wheel builds, GPU gates,
> release cutting, and artifact publication require a prepared estate, CUDA
> hardware, credentials, and operator judgment. Treat unproven combinations as
> unqualified.

## Product surfaces

- **Insula:** rootfs, execution, and estate management/projection. Everything
  relevant to the process must be under its rootfs.
- **Foundry:** hermetic Bazel builds of torch/JAX/Triton inside insula, adapted
  from Spack recipes and graphs. Adaptation automation is optional.

## What Exists Now

- Two CUDA rootfs lines: `cu129` and `cu130`, locked by
  `foundry/rootfs/cuda_ecosystem.lock.json` and built by
  `insula/rootfs/build_rootfs.sh`.
- A bwrap insula launcher and build entrypoint:
  `insula/execution/insula.sh` and `foundry/run.sh`.
  The selected line rootfs lives under `rootfs-lines/<line>` in the operator
  estate and is entered through bwrap with the Vaso namespace mounted inside.
- Native Bazel rules for the release wheels. The `torch` profile builds
  PyTorch, torchvision, torchaudio, and Triton wheels. The `jax` profile builds
  jaxlib wheels. Current acceptance records include PyTorch 2.14.0, Triton
  3.8.0, and self-built jaxlib 0.10.2 development wheels.
- Checked-in profile locks at `foundry/locks/torch.lock.json`
  and `foundry/locks/jax.lock.json`.
- GPU release gates that run rootfs checks, ABI/provenance checks, wheel
  packaging, and real workloads before a release can be cut.
- Determinism checks that build release wheels twice with fresh Bazel output
  bases and compare the resulting wheelhouses.
- A private OCI artifact store for release directories and rootfs bundles under
  `ghcr.io/phi9t`, implemented by `tools/publish/artifact_store.py`.

## Rootfs Contract

The insula exposes a small stable namespace:

```text
/workspace/<repo>   mounted source repositories
/vaso/cache         package, build, kernel, and tool caches
/vaso/state         durable Vaso-owned state
/vaso/runs          run plans, logs, results, and validation evidence
/vaso/traces        derived trace indexes and exports
/opt/vaso           read-only tools and runtime material
/run/nvidia-driver  projected host NVIDIA driver libraries and tools
/tmp                process-local temporary storage
```

The prepared base rootfs is read-only during normal execution. Explicit bwrap
binds provide writable estate state at `/vaso` and the selected home directory.
The standalone CLI mounts the project read-only; Foundry prepares its own
source and build-output projections.
Host CUDA driver libraries are projected under `/run/nvidia-driver`, while the
selected CUDA toolkit for compilation comes from the line rootfs.

Current contracts are in `docs/architecture.md`; the original design remains
in `docs/rootfs-container-infra-design.md` as historical reference.

## Quick Start

From a clean checkout, with Python and pytest available:

```bash
git clone https://github.com/phi9t/vaso.git
cd vaso

PYTHONPATH=insula/execution /usr/bin/python3 -m vaso.cli --help
PYTHONPATH=insula/execution /usr/bin/python3 -m vaso.cli validate --tier 0
/usr/bin/python3 -m pytest -q -p no:cacheprovider tools/publish/public_files_test.py
```

`validate --tier 0` is a local host-layout preflight. It creates the ignored
`.vaso/` directory, checks writable backing directories, and writes a run record
under `.vaso/runs/`. It does not enter the production insula.

The Python preflight CLI lives under `insula/execution/vaso`. Generic execution
is `insula/execution/insula.sh`; Foundry drives the native builds. The old
`harness/vaso` import path and `scripts/insula/` entrypoint remain compatibility
paths for existing callers.

## Operator Workflows

These commands are examples of the implemented operator surface. They are not
clean-checkout smoke tests.

Build a line rootfs bundle:

```bash
export VASO_ESTATE_ROOT=<estate>
insula/rootfs/build_rootfs.sh --line cu129
insula/rootfs/build_rootfs.sh --line cu130
```

Run a line smoke inside the insula:

```bash
export VASO_ESTATE_ROOT=<estate>
export VASO_CUDA_LINE=cu129
foundry/run.sh --cuda-line-smoke
```

Run the release gate for one profile and line:

```bash
export VASO_ESTATE_ROOT=<estate>
tools/agents/triumvirate-gate.sh \
  --release \
  --profile torch \
  --line cu129 \
  --acceptance-out <estate>/acceptance-torch-cu129.json
```

Run the double-build determinism check:

```bash
export VASO_ESTATE_ROOT=<estate>
scripts/release/determinism_check.sh \
  --profile torch \
  --line cu129 \
  --work-dir <estate>/determinism/torch-cu129 \
  --out <estate>/determinism/torch-cu129.json
```

`tools/ci/nightly.sh` is the nightly wrapper. It runs only when an operator
triggers it; it is flocked and installs no schedule. One run covers `torch` and
`jax` on both `cu129` and `cu130`, runs release gates under GPU and insula
leases, then runs determinism only after the release gates pass.

## Release Gates

The gate evidence lives in `foundry/acceptance-*.json`
and in generated operator run directories. The current acceptance records show:

- rootfs static and GPU verification for the selected CUDA line;
- compiler provenance and ABI invariant checks for native prefixes;
- PyTorch feature checks including CUDA import, matmul, cuDNN convolution,
  NCCL/Gloo distributed paths, `torch.compile`, flash attention, torchvision,
  torchaudio, and environment collection;
- torch workloads: eager and compiled GPT trainers, DDP, FSDP2 with checkpoint
  resume, standalone Triton kernels, Triton custom ops, and max-autotune MLP;
- JAX workloads: jit/grad and data-parallel trainers, Pallas kernels,
  FSDP-style and tensor-parallel GPT paths, cudnn/xla attention, Triton/Pallas
  matmul, determinism, and NVSHMEM remote DMA paths.

Anything not represented by a green acceptance record, release directory, and
determinism report should be treated as unproven.

## Artifacts

Release artifacts are private OCI artifacts under `ghcr.io/phi9t`.

- Release packages are named `vaso-release-<profile>-<line>` and contain
  wheels, `SHA256SUMS`, SBOM, acceptance JSON, workload results, determinism
  report, rootfs bundle manifest, and provenance.
- Rootfs packages are named `vaso-rootfs-<line>` and contain split rootfs
  archive layers plus rootfs sidecar metadata.
- Pulls verify OCI annotations, expected file digests, and rootfs reassembly.
- Real GHCR pushes require `GHCR_TOKEN` or `<estate>/publish/ghcr-token` and
  fail unless the package visibility is private. `--dry-run` uses an
  estate-local OCI layout.

Examples:

```bash
export VASO_ESTATE_ROOT=<estate>
tools/publish/install_oras.sh

tools/publish/artifact_store.py pull \
  --artifact release \
  --profile torch \
  --line cu129 \
  --digest sha256:<manifest-digest> \
  --target-dir <restore-dir>

tools/publish/artifact_store.py pull \
  --artifact rootfs \
  --line cu129 \
  --digest sha256:<manifest-digest> \
  --target-dir <restore-dir>
```

## Consumer Examples

`foundry/examples/consumers/` contains small consumers
that exercise the native prefixes from outside the native package definitions:

- `hello_cc` and `geom_core` for simple C/C++ consumers;
- `rust_pyo3` for a Rust/Python extension shape;
- `python_consumers` for wheel-style Python use;
- `cutlass_gemm`, `gsplat_rasterizer`, `voxel_scatter`, and `nvshmem_ep` for
  GPU-oriented consumer paths.

These are examples and guardrails, not a published downstream SDK.

## Repository Layout

```text
insula/         rootfs, execution, estate layout and projection
foundry/        Bazel builds: native rules, plans, locks, toolchains, workloads
tools/          optional agents, CI, Bazel experiments, publication, host testing
docs/           architecture, development, conditional operations, reference index
third_party/    vendored sources and provenance
scripts/        compatibility entrypoints and retained release helpers
harness/        compatibility imports and smoke fixtures
```

See [architecture](docs/architecture.md), [development](docs/development.md),
and the [glossary](GLOSSARY.md) for module interfaces, style, and data ownership.


## Status And Limits

- The production insula and native release graph are implemented, but they are
  operator workflows, not a one-command public install.
- Native package rebuilds are expensive. Read `docs/agents/build-stability.md`
  before changing anything under native rules, locks, toolchains, gate Bazel
  arguments, or rootfs inputs.
- The public GitHub flow is a sanitized snapshot, not private source history.
  See `docs/agents/publication.md`.
- The top-level CLI Tier 1 command still emits a plan and exits non-zero; it is
  not the production Bazel execution path.
- Artifact publication is private. Public package availability is not claimed.
- Security claims are limited to the tested bwrap/rootfs behavior. Vaso is not
  a blanket isolation boundary for arbitrary hostile code.

## Development

Run the Python test suite with:

```bash
/usr/bin/python3 -m pytest -q -p no:cacheprovider
```

Before changing code, read `AGENTS.md` and the guidance it references. Keep
generated rootfs state, run evidence, caches, and release artifacts outside the
tracked tree.

See [executable verification](docs/verification.md) for historical guard
regressions and a small real-insula acceptance command with preserved evidence.

## License

Vaso is available under the [MIT License](LICENSE).
