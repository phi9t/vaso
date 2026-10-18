# Vaso glossary

These terms describe the two product surfaces clarified by the user. The
organization is recorded in `docs/architecture.md`.

| Term | Meaning |
|---|---|
| Rootfs | The filesystem presented to an insula process, including its prepared base and explicit projections; tools, toolchains, and dependencies resolve within it. |
| Insula | The root filesystem containing toolchains, tools, and dependencies; command execution; and estate projection into the sandbox. |
| Estate | The host directory structure backing rootfs storage, source trees, persistent sandbox state, and build outputs. |
| Projection | An explicit mapping from a host resource to a sandbox path, including its access mode. |
| Execution | A command running with a selected root filesystem, projected resources, environment, and writable outputs. |
| Foundry | The Bazel build process for adapted PyTorch, JAX, Triton, and required dependencies, executed inside insula. |
| Build profile | A compatible selection of packages, compiler policy, and build features. |
| CUDA line | A selected CUDA ecosystem version family, independently of the build profile. |
| Adaptation | Expressing a reference package recipe and dependency graph as build definitions. |
| Artifact | An identified build output, such as an install prefix or wheel. |

Spack supplies reference recipes and graph information. Automating adaptation
is outside the required product surface.
