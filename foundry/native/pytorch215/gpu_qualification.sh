#!/usr/bin/env bash
# GPU admission for the pinned two-rank SM61 source-wheel acceptance workload.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/../../.." && pwd)"
REQUEST="${1:?Pass a qualification request JSON}"
mapfile -d '' SITE < <(python3 - "$REQUEST" <<'PY'
import json,sys
r=json.load(open(sys.argv[1]))
for k in ['rootfs','estate','workdir']:
    v=r[k]
    if not isinstance(v,str) or '\0' in v: raise SystemExit('Invalid request field: '+k)
    sys.stdout.write(v+'\0')
PY
)
[[ ${#SITE[@]} -eq 3 ]] || { echo 'Invalid request site' >&2; exit 64; }
export VASO_ESTATE_ROOT="${SITE[1]}" VASO_AGENT=foundry-torch215-qualification
export VASO_LEASE_TTL=360 VASO_LEASE_TIMEOUT=3
source "$REPO/insula/execution/insula.sh"
insula_prepare_context "${SITE[0]}" "$REPO" ""
INSULA_UNSHARE_NET=0; INSULA_UNSHARE_PID=0; INSULA_UNSHARE_IPC=0
INSULA_REQUIRE_DRIVER=1; INSULA_CHDIR="${SITE[2]}"
mapfile -d '' ENVIRONMENT < <(python3 - "$REQUEST" <<'PY'
import json,sys
for k,v in json.load(open(sys.argv[1]))['environment'].items():
    for x in ['--setenv',k,v]:
        if not isinstance(x,str) or '\0' in x: raise SystemExit('Invalid environment')
        sys.stdout.write(x+'\0')
PY
)
INSULA_ENV_ARGS+=("${ENVIRONMENT[@]}")
mapfile -d '' COMMAND < <(python3 - "$REQUEST" <<'PY'
import json,sys
for x in json.load(open(sys.argv[1]))['argv']:
    if not isinstance(x,str) or '\0' in x: raise SystemExit('Invalid argv')
    sys.stdout.write(x+'\0')
PY
)
[[ ${#COMMAND[@]} -gt 0 ]] || { echo 'Empty command' >&2; exit 64; }
CUDA_DRIVER_BINDS=(); insula_append_cuda_driver_binds CUDA_DRIVER_BINDS
for dev in /dev/nvidia0 /dev/nvidia1 /dev/nvidiactl /dev/nvidia-uvm /dev/nvidia-uvm-tools; do
    CUDA_DRIVER_BINDS+=(--dev-bind "$dev" "$dev")
done
INSULA_BIND_ARGS+=(--ro-bind /sys /sys)
APPS="$(nvidia-smi --query-compute-apps=pid --format=csv,noheader)"
if [[ -n "$APPS" ]]; then echo 'QUALIFICATION_DEFERRED_GPU_BUSY' >&2; exit 75; fi
# Build estates may be isolated, but physical GPU leases must coordinate at
# the host's shared lease authority rather than in independent per-build stores.
LEASE_ESTATE="$(python3 - "$REQUEST" <<'PY_LEASE'
import json,sys
r=json.load(open(sys.argv[1]));print(r.get('gpu_lease_estate',r['estate']))
PY_LEASE
)"
LEASE_JSON="$(VASO_ESTATE_ROOT="$LEASE_ESTATE" python3 "$INSULA_LEASE_PY" acquire \
    --resource gpu --holder "$VASO_AGENT" --pid "$$" --amount 2 --capacity 2 \
    --timeout "$VASO_LEASE_TIMEOUT" --ttl "$VASO_LEASE_TTL")"
held_gpu="$(insula_json_field id <<<"$LEASE_JSON")"
printf 'GPU_LEASE_AUTHORITY %s\n' "$LEASE_ESTATE"
cleanup() {
    local apps
    if apps="$(nvidia-smi --query-compute-apps=pid --format=csv,noheader)" && [[ -z "$apps" ]]; then
        VASO_ESTATE_ROOT="$LEASE_ESTATE" python3 "$INSULA_LEASE_PY" release --id "$held_gpu" >/dev/null
        echo OWNED_LEASE_RELEASED
    fi
}
trap cleanup EXIT
VASO_ESTATE_ROOT="$LEASE_ESTATE" python3 "$INSULA_LEASE_PY" check --id "$held_gpu" --resource gpu >/dev/null
APPS="$(nvidia-smi --query-compute-apps=pid --format=csv,noheader)"
if [[ -n "$APPS" ]]; then echo 'QUALIFICATION_DEFERRED_GPU_BUSY_AFTER_LEASE' >&2; exit 75; fi
insula -- "${COMMAND[@]}"
