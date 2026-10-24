# Vaso

Vaso provides rootfs execution and hermetic Bazel builds for PyTorch, JAX,
and Triton on Linux with NVIDIA GPUs.

It has two product surfaces:

- **Insula** prepares a root filesystem, projects estate storage into it, and
  runs commands with [Bubblewrap](https://github.com/containers/bubblewrap).
  Tools, toolchains, and dependencies resolve inside that filesystem.
- **Foundry** builds PyTorch, JAX, Triton, and dependencies with Bazel inside
  insula. Its build definitions adapt Spack recipes and dependency graphs.

The estate holds source trees, rootfs bundles, caches, and outputs on the host.
Explicit projections expose them to insula; host driver and device projections
supply GPU access. Normal execution needs no Docker daemon.

## Start here

The three primary operations are `insula/rootfs/prepare.sh restore|build`,
`insula/run.sh -- COMMAND`, and `foundry/build.sh --line cu129 --profile torch`.
Foundry also provides `--prepare-only` and durable `--status` inspection.
See the [command migration table](docs/command-migration.md) for specialist and
retired routes.

[Read the operator guide](docs/operator-guide.md) to restore published artifacts,
run shipped wheels, or prepare a build estate. These workflows require a
prepared Linux host and access to the private GHCR packages.

The repository includes `cu129` and `cu130` CUDA lines and separate Torch/Triton
and JAX profiles. The first preview targets single-GPU execution using one
NVIDIA B200 on the tested Linux x86-64 host. Multi-GPU support is deferred;
other hardware is unqualified. Release verification is in progress. See
[status and evidence](docs/status.md).

## Documentation

- [Insula](docs/insula.md): rootfs, execution, and estate projection.
- [Foundry](docs/foundry.md): pinned inputs and offline Bazel builds.
- [Estate lifecycle](docs/estate.md): contents, persistence, and restore.
- [Code tour](docs/code-tour.md): follow a build and find the code to change.
- [Architecture](docs/architecture.md): module boundaries and data ownership.
- [Development](docs/development.md): coding conventions and verification.
- [Technical references](docs/reference/README.md): build rationale and history.
- [All documentation](docs/README.md) · [Glossary](GLOSSARY.md).

## Repository

```text
insula/       rootfs, execution, and estate projection
foundry/      native rules, plans, locks, toolchains, and workloads
docs/         guides, architecture, reference articles, and history
tools/        release, development, CI, build experiments, and publication
third_party/  vendored sources and provenance
```

Read [AGENTS.md](AGENTS.md) before changing the repository. Keep generated
rootfs state, evidence, caches, and release artifacts outside the tracked tree.

[MIT License](LICENSE).
