# Insula

Insula owns the rootfs, command execution, and estate directory projection.
Paths in the directory table are relative to `insula/`.
All tools, toolchains, and dependencies used by a process are under the
rootfs it sees. Host storage backs that filesystem and explicit data mounts.

| Directory | Responsibility |
|---|---|
| `rootfs/` | CUDA line rootfs construction, bundles, content identity, and validation |
| `estate/` | `VasoEstate`, storage placement, sandbox projections, and path translation |
| `execution/` | Bubblewrap argv, environment, readiness, leases, results, and operational acceptance |

Build a rootfs with `insula/rootfs/build_rootfs.sh --line cu129` and an explicit
`VASO_ESTATE_ROOT`. Rootfs construction can provision dependencies; execution
uses the prepared filesystem.

Run a generic command from the repository root without a Foundry profile or package lock:

```sh
VASO_ESTATE_ROOT=/path/to/estate insula/execution/insula.sh \
  --line cu129 --workspace /path/to/project -- /bin/sh -c 'pwd'
```

The command sees the project read-only at `/workspace/vaso`, writable estate
state at `/vaso`, a read-only rootfs, a cleared environment, and an isolated
network. `--rootfs DIR` selects an explicit rootfs instead of a CUDA line.
`--print-argv` renders the request without entering Bubblewrap. It still
materializes the selected estate layout. Fetch execution is explicit and
requires a SHA-256 pin and the network-fetch lease.

For callers that prepare their own projections, source `execution/insula.sh`
and supply `ROOT_PLAN`, `INSULA_BIND_ARGS`, `INSULA_ENV_ARGS`, and optional
working directory/namespace settings before calling `insula -- COMMAND`.
The caller owns shared execution leases; the launcher exposes lease helpers.
`--print-argv` makes the resulting request inspectable. Preflight failure
and command exit status propagate to the caller.

Foundry's build/release context is in `foundry/tools/insula_context.sh`.
Its canonical entrypoint is `foundry/insula.sh`; pinned build tools are
installed by `rootfs/foundry_tools.py` before execution. See the [Foundry guide](foundry.md)
for acquisition and offline prerequisite checks.
The old `scripts/insula/insula.sh` loads that adapter for existing callers.
Generic insula execution has no dependency on Foundry.

The Python package under `execution/vaso/` supplies the production readiness
checks and legacy host-layout preflight CLI. Tier-1 validation there remains
a plan-only scaffold; it is not a second sandbox execution implementation.

See [architecture](architecture.md) for identities and compatibility paths, and
[verification](verification.md) for real Bubblewrap acceptance.

For storage and recovery, see the [estate lifecycle deep dive](estate.md).

## Rootfs namespace

| Sandbox path | Purpose |
|---|---|
| `/workspace/<repo>` | Explicit source projections |
| `/vaso/cache` | Package, build, kernel, and tool caches |
| `/vaso/state` | Durable Vaso state |
| `/vaso/runs` | Plans, logs, results, and evidence |
| `/vaso/traces` | Derived trace indexes and exports |
| `/opt/vaso` | Prepared tools and runtime material |
| `/run/nvidia-driver` | Explicitly projected host driver libraries and tools |
| `/tmp` | Process-local temporary storage |

The prepared base is read-only during normal execution. Writable estate state
and home directories use explicit binds. The standalone launcher mounts the
project read-only; Foundry prepares its own source/output projections. The CUDA
compiler toolkit comes from the selected rootfs, separately from the projected
host driver. See [architecture](architecture.md) for caller-owned projections.
