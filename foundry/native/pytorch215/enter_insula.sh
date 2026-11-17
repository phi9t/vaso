#!/usr/bin/env bash
set -euo pipefail
SELF="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$SELF/../../.." && pwd)"
REQUEST="${1:?expected execution request JSON}"
mapfile -d '' ARGS < <(python3 - "$REQUEST" <<'PY'
import json,sys
r=json.load(open(sys.argv[1]))
for x in [r['rootfs'],r['estate'],r['workdir']]:sys.stdout.write(x+'\0')
PY
)
export VASO_ESTATE_ROOT="${ARGS[1]}" VASO_AGENT=foundry-source-wheel
source "$REPO/insula/execution/insula.sh"
insula_prepare_context "${ARGS[0]}" "$REPO" ""
INSULA_UNSHARE_NET=0; INSULA_UNSHARE_PID=0; INSULA_UNSHARE_IPC=0
INSULA_REQUIRE_DRIVER=0; INSULA_CHDIR="${ARGS[2]}"
mapfile -d '' ENV_ARGS < <(python3 - "$REQUEST" <<'PY'
import json,sys
r=json.load(open(sys.argv[1]))
for k,v in r['environment'].items():
 for x in ['--setenv',k,v]:sys.stdout.write(x+'\0')
PY
)
INSULA_ENV_ARGS+=("${ENV_ARGS[@]}")
mapfile -d '' COMMAND < <(python3 - "$REQUEST" <<'PY'
import json,sys
for x in json.load(open(sys.argv[1]))['argv']:sys.stdout.write(x+'\0')
PY
)
insula -- "${COMMAND[@]}"
