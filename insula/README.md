# Insula

Insula owns the rootfs, command execution, and estate directory projection.
All tools, toolchains, and dependencies used by a process are under the
rootfs it sees. Host storage backs that filesystem and explicit data mounts.

| Directory | Responsibility |
|---|---|
| `rootfs/` | CUDA line rootfs construction, bundles, content identity, and validation |
| `estate/` | `VasoEstate`, storage placement, sandbox projections, and path translation |
| `execution/` | Bubblewrap argv, environment, readiness, leases, results, and operational acceptance |

Build a rootfs with `rootfs/build_rootfs.sh --line cu129` and an explicit
`VASO_ESTATE_ROOT`. Rootfs construction can provision dependencies; execution
uses the prepared filesystem.

Run a generic command from this directory without a Foundry profile or package lock:

```sh
VASO_ESTATE_ROOT=/path/to/estate execution/insula.sh \
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

Foundry's build/release context is in `../foundry/tools/insula_context.sh`.
Its canonical entrypoint is `../foundry/insula.sh`; pinned build tools are
installed by `rootfs/foundry_tools.py` before execution. See Foundry's README
for acquisition and offline prerequisite checks.
The old `../scripts/insula/insula.sh` loads that adapter for existing callers.
Generic insula execution has no dependency on Foundry.

The Python package under `execution/vaso/` supplies the production readiness
checks and legacy host-layout preflight CLI. Tier-1 validation there remains
a plan-only scaffold; it is not a second sandbox execution implementation.

See `../docs/architecture.md` for identities and compatibility paths, and
`../docs/verification.md` for real Bubblewrap acceptance.
