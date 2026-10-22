#!/usr/bin/env bash
# Execute one Foundry build, query, or verification invocation inside insula.
# foundry/build.sh owns unattended matrix preparation and sequencing.
#
# This adapter validates the selected line/profile, prepares owned estate paths,
# leases, and run records, then enters the rootfs through foundry/insula.sh.
# Bazel tools resolve inside that rootfs. The estate persists caches and outputs
# projected at stable sandbox paths; its host location must be explicit.
# See docs/code-tour.md for the end-to-end call path.
set -euo pipefail

EXP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

REPO_ROOT="$(cd "$EXP_DIR/.." && pwd)"
cd "$EXP_DIR"
LEASE_PY="$REPO_ROOT/insula/execution/lease.py"
RESULT_PY="$REPO_ROOT/insula/execution/result.py"
INSULA_SH="$REPO_ROOT/foundry/insula.sh"
. "$EXP_DIR/tools/native_determinism_args.sh"
. "$INSULA_SH"

REQUIRED_GIB="${VASO_REQUIRED_GIB:-40}"
VASO_AGENT="${VASO_AGENT:-trae}"
if [[ ! "$VASO_AGENT" =~ ^[A-Za-z0-9_.-]+$ ]]; then
  echo "VASO_AGENT must contain only letters, numbers, dot, underscore or dash, got: $VASO_AGENT" >&2
  exit 2
fi

RUN_MODE="${1:-gate}"
RUN_RESULT_MODE="${RUN_MODE#--}"
[[ -n "$RUN_RESULT_MODE" ]] || RUN_RESULT_MODE="gate"
if [[ "${VASO_FETCH:-0}" == "1" ]]; then
  RUN_RESULT_MODE="fetch-only"
fi
DEFAULT_VASO_GPUS=8
case "$RUN_MODE" in
  --bazel-build|--bazel-aquery|--fetch-only|--jaxlib-prefetch)
    DEFAULT_VASO_GPUS=0
    ;;
esac
VASO_GPUS="${VASO_GPUS:-$DEFAULT_VASO_GPUS}"
RUN_RESULT_INITIALIZED=0
RUN_RESULT_FINALIZED=0
RUN_RESULT_STAGE_INDEX=0
RUN_RESULT_FILE=""
RUN_RESULT_LOG_DIR=""
RUN_RESULT_STAGES_JSONL=""
RUN_RESULT_ARGS_JSON="[]"
RUN_RESULT_STARTED_UTC=""
INSULA_IO_INITIALIZED=0
LEASE_IDS=()

# --- seat the host estate (placement discovery) ------------------------------
# The estate must be explicit. Old automatic placement could silently select an
# obsolete home estate and send results to the wrong tree.
: "${VASO_ESTATE_ROOT:?set VASO_ESTATE_ROOT}"
PREFER_DIR="$(df -P "$EXP_DIR" | awk 'NR==2{print $6}')"
ESTATE_ROOT="$(python3 "$REPO_ROOT/insula/estate/estate.py" \
  --root "$VASO_ESTATE_ROOT" \
  --required-gib "$REQUIRED_GIB" \
  --prefer "$PREFER_DIR" \
  --materialize)"
export VASO_ESTATE_ROOT="$ESTATE_ROOT"
echo "estate root: $ESTATE_ROOT"

init_run_result() {
  RUN_RESULT_STARTED_UTC="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  local ts
  ts="$(date -u +%Y%m%dT%H%M%S%NZ)"
  RUN_RESULT_LOG_DIR="$ESTATE_ROOT/agents/$VASO_AGENT/logs/$ts-$RUN_RESULT_MODE"
  RUN_RESULT_STAGES_JSONL="$RUN_RESULT_LOG_DIR/stages.jsonl"
  RUN_RESULT_FILE="$ESTATE_ROOT/agents/$VASO_AGENT/results/$ts-$RUN_RESULT_MODE.json"
  mkdir -p "$RUN_RESULT_LOG_DIR" "$(dirname "$RUN_RESULT_FILE")"
  RUN_RESULT_ARGS_JSON="$(python3 "$RESULT_PY" redact-args -- "$@")"
  RUN_RESULT_INITIALIZED=1
}

finalize_run_result() {
  local rc="$1"
  [[ "$RUN_RESULT_INITIALIZED" == "1" && "$RUN_RESULT_FINALIZED" == "0" ]] || return 0
  RUN_RESULT_FINALIZED=1
  local commit
  commit="$(git -C "$REPO_ROOT" rev-parse HEAD 2>/dev/null || echo unknown)"
  python3 "$RESULT_PY" finalize \
    --out "$RUN_RESULT_FILE" \
    --stages-jsonl "$RUN_RESULT_STAGES_JSONL" \
    --mode "$RUN_RESULT_MODE" \
    --line "${VASO_CUDA_LINE:-}" \
    --agent "$VASO_AGENT" \
    --args-json "$RUN_RESULT_ARGS_JSON" \
    --log-path "$RUN_RESULT_LOG_DIR" \
    --leases "${VASO_LEASES_HELD:-}" \
    --commit "$commit" \
    --exit-code "$rc" \
    --started-utc "$RUN_RESULT_STARTED_UTC" \
    --solve-spec "" >/dev/null 2>&1 || true
  echo "run result: $RUN_RESULT_FILE"
}

run_stage() {
  local stage="$1"
  shift
  local safe_stage log start_ns end_ns duration rc
  safe_stage="$(printf '%s' "$stage" | tr -c 'A-Za-z0-9_.-' '_')"
  printf -v log '%s/%03d-%s.log' "$RUN_RESULT_LOG_DIR" "$RUN_RESULT_STAGE_INDEX" "$safe_stage"
  RUN_RESULT_STAGE_INDEX=$((RUN_RESULT_STAGE_INDEX + 1))
  : > "$log"
  start_ns="$(date +%s%N)"
  set +e
  "$@" > >(tee -a "$log") 2> >(tee -a "$log" >&2)
  rc=$?
  set -e
  end_ns="$(date +%s%N)"
  duration="$(python3 - "$start_ns" "$end_ns" <<'PY'
import sys
print(f"{(int(sys.argv[2]) - int(sys.argv[1])) / 1_000_000_000:.3f}")
PY
)"
  python3 "$RESULT_PY" stage \
    --stages-jsonl "$RUN_RESULT_STAGES_JSONL" \
    --name "$stage" \
    --exit-code "$rc" \
    --duration-seconds "$duration" \
    --log-path "$log"
  return "$rc"
}

release_leases() {
  set +e
  local idx id
  for ((idx=${#LEASE_IDS[@]}-1; idx>=0; idx--)); do
    id="${LEASE_IDS[$idx]}"
    [[ -n "$id" ]] || continue
    VASO_ESTATE_ROOT="$ESTATE_ROOT" python3 "$LEASE_PY" release --id "$id" >/dev/null 2>&1
  done
}

cleanup_on_exit() {
  local rc=$?
  set +e
  finalize_run_result "$rc"
  release_leases
  if [[ "$INSULA_IO_INITIALIZED" == "1" ]]; then
    cleanup_insula_io
  fi
  exit "$rc"
}

init_run_result "$@"
trap cleanup_on_exit EXIT

if [[ ! "$VASO_GPUS" =~ ^[0-9]+$ ]]; then
  echo "VASO_GPUS must be a non-negative integer, got: $VASO_GPUS" >&2
  exit 2
fi

VASO_CUDA_LINE="${VASO_CUDA_LINE:-}"
case "$VASO_CUDA_LINE" in
  cu129|cu130) ;;
  "")
    cat >&2 <<'EOF'
VASO_CUDA_LINE must be set to one of: cu129, cu130

The CUDA insula rootfs is selected only from:
  $VASO_ESTATE_ROOT/rootfs-lines/<line>/{rootfs,rootfs-bundle.json}
The legacy $VASO_ESTATE_ROOT/rootfs path is not selected implicitly.
EOF
    exit 2
    ;;
  *)
    echo "unknown VASO_CUDA_LINE=$VASO_CUDA_LINE; expected cu129 or cu130" >&2
    exit 2
    ;;
esac

VASO_PROFILE="${VASO_PROFILE:-torch}"
case "$VASO_PROFILE" in
  torch|jax) ;;
  *) echo "unknown VASO_PROFILE=$VASO_PROFILE; expected torch or jax" >&2; exit 2 ;;
esac

# The canonical sandbox namespace (stable /vaso, /workspace, /home/kvothe, ...)
# comes from the estate model, so the overlay root and the insula present the
# same structure the design doc specifies.
CANONICAL_DIRS_TEXT="$(python3 "$REPO_ROOT/insula/estate/estate.py" --root "$ESTATE_ROOT" --print-sandbox-dirs)"
mapfile -t CANONICAL_DIRS <<< "$CANONICAL_DIRS_TEXT"

# Host-side durable dirs (from the estate model). The whole `vaso/` subtree is
# bound at /vaso, so cache/state/runs/traces all persist outside the insula.
VASO_HOST="$ESTATE_ROOT/vaso"
VASO_LINE_HOST="$VASO_HOST/lines/$VASO_CUDA_LINE"
HOME_HOST="$ESTATE_ROOT/home/kvothe"
SOURCES_HOST="$ESTATE_ROOT/sources"
PYTORCH_SOURCE_DISTDIR_HOST="$SOURCES_HOST/pytorch/distdir"
PYTORCH_SOURCE_DISTDIR_SB="/vaso/sources/pytorch/distdir"
TRITON_SOURCE_DISTDIR_HOST="$SOURCES_HOST/triton/distdir"
TRITON_SOURCE_DISTDIR_SB="/vaso/sources/triton/distdir"
JAX_SOURCE_DISTDIR_HOST="$SOURCES_HOST/jax/distdir"
JAX_SOURCE_DISTDIR_SB="/vaso/sources/jax/distdir"
JAXLIB_NESTED_HTTP_ARCHIVES_MANIFEST="$EXP_DIR/native/jaxlib/nested_http_archives.json"
NATIVE_SOURCE_DISTDIR_HOST="$SOURCES_HOST/native/distdir"
NATIVE_SOURCE_DISTDIR_SB="/vaso/sources/native/distdir"
BAZEL_TOOL_DISTDIR_HOST="$SOURCES_HOST/bazel/distdir"
BAZEL_TOOL_DISTDIR_SB="/vaso/sources/bazel/distdir"
OPT_VASO_HOST="$ESTATE_ROOT/rootfs-lines/$VASO_CUDA_LINE/rootfs/opt/vaso"
TOOLS_BIN_HOST="$OPT_VASO_HOST/compat-bin"
BAZEL_REAL_HOST="$OPT_VASO_HOST/bin/bazel-real"
BAZEL_REAL_SB="/opt/vaso/bin/bazel-real"
ROOTFS_BAZEL_VERSION="7.7.0"
ROOTFS_BAZEL_SHA256="fe7e799cbc9140f986b063e06800a3d4c790525075c877d00a7112669824acbf"
ROOTFS_BAZEL_FILENAME="bazel-$ROOTFS_BAZEL_VERSION-linux-x86_64"
ROOTFS_BAZEL_URL="https://github.com/bazelbuild/bazel/releases/download/$ROOTFS_BAZEL_VERSION/$ROOTFS_BAZEL_FILENAME"
ROOTFS_BAZEL_HOST="$OPT_VASO_HOST/bazel-$ROOTFS_BAZEL_VERSION/bin/bazel-real"
ROOTFS_BAZEL_SB="/opt/vaso/bazel-$ROOTFS_BAZEL_VERSION/bin/bazel-real"
. "$EXP_DIR/tools/insula_io.sh"
init_insula_io "$VASO_HOST"
# Bazel sees one constant TMPDIR per line (the insula:<line> lease makes it exclusive).
# The per-run INSULA_TMP_SB changes every run, and as an --action_env/--repo_env value
# it would change native action keys on every run (docs/agents/build-stability.md).
BAZEL_TMP_SB="/vaso/tmp/bazel-$VASO_CUDA_LINE"
mkdir -p "$VASO_HOST/tmp/bazel-$VASO_CUDA_LINE"
INSULA_IO_INITIALIZED=1
echo "insula tmp: $INSULA_TMP_SB (private tmpfs size: $INSULA_TMPFS_SIZE bytes)"
mkdir -p \
  "$SOURCES_HOST" \
  "$JAX_SOURCE_DISTDIR_HOST" \
  "$NATIVE_SOURCE_DISTDIR_HOST" \
  "$TRITON_SOURCE_DISTDIR_HOST" \
  "$BAZEL_TOOL_DISTDIR_HOST" \
  "$ESTATE_ROOT/agents" \
  "$ESTATE_ROOT/rootfs-lines" \
  "$ESTATE_ROOT/workspace/repo" \
  "$VASO_HOST/agents" \
  "$VASO_HOST/rootfs-lines" \
  "$VASO_HOST/cache/bazel/repository-cache" \
  "$VASO_HOST/sources" \
  "$VASO_LINE_HOST/cache/bazel/output-base" \
  "$VASO_LINE_HOST/cache/bazel/disk-cache" \
  "$VASO_LINE_HOST/state/native" \
  "$VASO_LINE_HOST/state/stamps"

lease_field() {
  local field="$1"
  python3 -c 'import json,sys; print(json.load(sys.stdin).get(sys.argv[1], ""))' "$field"
}

lease_env_field() {
  local field="$1"
  python3 -c 'import json,sys; print(json.load(sys.stdin).get("env", {}).get(sys.argv[1], ""))' "$field"
}

held_lease_id() {
  local resource="$1"
  local leases="${VASO_LEASES_HELD:-}"
  local entry held_resource held_id
  [[ -n "$leases" ]] || return 1
  local IFS=,
  for entry in $leases; do
    [[ "$entry" == *:* ]] || continue
    held_resource="${entry%:*}"
    held_id="${entry##*:}"
    if [[ "$held_resource" == "$resource" && -n "$held_id" ]]; then
      printf '%s\n' "$held_id"
      return 0
    fi
  done
  return 1
}

validate_inherited_lease() {
  local resource="$1"
  local lease_id="$2"
  if ! VASO_ESTATE_ROOT="$ESTATE_ROOT" python3 "$LEASE_PY" check --id "$lease_id" --resource "$resource" >/dev/null; then
    echo "inherited lease is not live: resource=$resource id=$lease_id" >&2
    return 75
  fi
}

acquire_lease() {
  local resource="$1"
  shift
  local lease_json lease_id
  if [[ "$resource" == insula:* || "$resource" == "gpu" ]]; then
    if lease_id="$(held_lease_id "$resource")"; then
      validate_inherited_lease "$resource" "$lease_id"
      export VASO_LEASES_HELD
      echo "lease inherited: resource=$resource id=$lease_id"
      return 0
    fi
  fi
  lease_json="$(python3 "$LEASE_PY" acquire \
    --resource "$resource" \
    --holder "$VASO_AGENT" \
    --pid "$$" \
    --timeout "${VASO_LEASE_TIMEOUT:-60}" \
    --ttl "${VASO_LEASE_TTL:-86400}" \
    "$@")"
  lease_id="$(lease_field id <<<"$lease_json")"
  LEASE_IDS+=("$lease_id")
  VASO_LEASES_HELD="${VASO_LEASES_HELD:+$VASO_LEASES_HELD,}$resource:$lease_id"
  export VASO_LEASES_HELD
  echo "lease acquired: resource=$resource id=$lease_id"
  if [[ "$resource" == "gpu" ]]; then
    lease_cuda_visible_devices="$(lease_env_field CUDA_VISIBLE_DEVICES <<<"$lease_json")"
    lease_vaso_gpu_set="$(lease_env_field VASO_GPU_SET <<<"$lease_json")"
    export CUDA_VISIBLE_DEVICES="$lease_cuda_visible_devices"
    export VASO_GPU_SET="$lease_vaso_gpu_set"
  elif [[ "$resource" == "host-cpu" ]]; then
    lease_host_cpu_jobs="$(lease_env_field VASO_HOST_CPU_JOBS <<<"$lease_json")"
    export VASO_HOST_CPU_JOBS="$lease_host_cpu_jobs"
  fi
}

acquire_lease "insula:$VASO_CUDA_LINE"
if (( VASO_GPUS > 0 )); then
  acquire_lease "gpu" --amount "$VASO_GPUS"
fi
cpu_lease_args=()
if [[ -n "${VASO_HOST_CPU_BUDGET:-}" ]]; then
  cpu_lease_args=(--budget "$VASO_HOST_CPU_BUDGET")
fi
acquire_lease "host-cpu" "${cpu_lease_args[@]}"

# Tools are provisioned with the selected rootfs; build execution never
# discovers, copies, repairs, or downloads an ambient host executable.
materialize_rootfs_bazel_tool() {
  python3 "$REPO_ROOT/insula/rootfs/foundry_tools.py" \
    --rootfs "$ESTATE_ROOT/rootfs-lines/$VASO_CUDA_LINE/rootfs" --verify >/dev/null || return $?
  python3 "$EXP_DIR/tools/bcr_mirror.py" --estate-root "$ESTATE_ROOT" --check >/dev/null || return $?
}
requires_rootfs_bazel_tool() { return 0; }

# Stable sandbox paths (vaso contract).
HOME_SB="/home/kvothe"
if [[ -n "${VASO_BAZEL_OB:-}" ]]; then
  BAZEL_OUTPUT_BASE_HOST="$VASO_BAZEL_OB"
elif [[ "$VASO_AGENT" == "trae" ]]; then
  BAZEL_OUTPUT_BASE_HOST="$VASO_LINE_HOST/cache/bazel/output-base"
else
  BAZEL_OUTPUT_BASE_HOST="$VASO_LINE_HOST/cache/bazel/output-base-$VASO_AGENT"
fi
if [[ "$BAZEL_OUTPUT_BASE_HOST" != /* ]]; then
  echo "Bazel output base must be an absolute host path: $BAZEL_OUTPUT_BASE_HOST" >&2
  exit 2
fi
BAZEL_OUTPUT_BASE_REAL="$(readlink -m "$BAZEL_OUTPUT_BASE_HOST")"
case "$BAZEL_OUTPUT_BASE_REAL" in
  /tmp/*|/var/tmp/*)
    echo "refusing Bazel output base on tmpfs-like shared scratch: $BAZEL_OUTPUT_BASE_REAL" >&2
    exit 2
    ;;
esac
BAZEL_OUTPUT_BASE_HOST="$BAZEL_OUTPUT_BASE_REAL"
BAZEL_OUTPUT_BASE_SB="$(native_determinism_fixed_output_base_mount)"
mkdir -p "$BAZEL_OUTPUT_BASE_HOST"
BAZEL_REPO_CACHE_HOST="$VASO_HOST/cache/bazel/repository-cache"
VASO_RELEASE_GIT_SHA_INPUT="${VASO_RELEASE_GIT_SHA:-HEAD}"
VASO_RELEASE_GIT_SHA="$(native_determinism_resolve_release_git_sha "$REPO_ROOT" "$VASO_RELEASE_GIT_SHA_INPUT")"
VASO_SOURCE_DATE_EPOCH="$(native_determinism_source_date_epoch "$REPO_ROOT" "$VASO_RELEASE_GIT_SHA")"
BAZEL_EXECROOT_SB="$BAZEL_OUTPUT_BASE_SB/execroot/_main"
BAZEL_NATIVE_WORK_SB="/vaso/lines/$VASO_CUDA_LINE/work"
BAZEL_RELEASE_ARGS=(
)
append_native_determinism_bazel_args \
  BAZEL_RELEASE_ARGS \
  "$VASO_CUDA_LINE" \
  "$REPO_ROOT" \
  "$VASO_RELEASE_GIT_SHA" \
  "$BAZEL_OUTPUT_BASE_SB" \
  "$BAZEL_NATIVE_WORK_SB"
BAZEL_REPO_CACHE_SB="/vaso/cache/bazel/repository-cache"
BAZEL_DISK_CACHE_HOST="$(foundry_disk_cache_host "$VASO_LINE_HOST/cache/bazel/disk-cache")"
BAZEL_DISK_CACHE_SB="/vaso/lines/$VASO_CUDA_LINE/cache/bazel/disk-cache"
bazel_disk_cache_gc_args=()
append_native_bazel_disk_cache_gc_args bazel_disk_cache_gc_args
VASO_NATIVE_STATE_HOST="$VASO_LINE_HOST/state/native"
VASO_NATIVE_STAMPS_HOST="$VASO_LINE_HOST/state/stamps"
VASO_NATIVE_STATE_SB="/vaso/lines/$VASO_CUDA_LINE/state/native"
VASO_NATIVE_STAMPS_SB="/vaso/lines/$VASO_CUDA_LINE/state/stamps"
BAZEL_DISTDIR_ARGS=()
BAZEL_DISTDIR_ARGS+=("--distdir=$BAZEL_TOOL_DISTDIR_SB")
BAZEL_DISTDIR_ARGS+=("--distdir=$NATIVE_SOURCE_DISTDIR_SB")
BAZEL_DISTDIR_ARGS+=("--distdir=$TRITON_SOURCE_DISTDIR_SB")
BAZEL_DISTDIR_ARGS+=("--distdir=$JAX_SOURCE_DISTDIR_SB")
if [[ -d "$PYTORCH_SOURCE_DISTDIR_HOST" ]]; then
  BAZEL_DISTDIR_ARGS+=("--distdir=$PYTORCH_SOURCE_DISTDIR_SB")
fi

# The experiment dir is projected under the canonical /workspace root so the
# insula never references a host path.
EXP_SB="/workspace/experiment"

sandbox_path_to_host() {
  local path="$1"
  if [[ "$path" == "$EXP_SB" ]]; then
    printf '%s\n' "$EXP_DIR"
  elif [[ "$path" == "$EXP_SB/"* ]]; then
    printf '%s/%s\n' "$EXP_DIR" "${path#"$EXP_SB/"}"
  elif [[ "$path" == "/vaso" ]]; then
    printf '%s\n' "$VASO_HOST"
  elif [[ "$path" == "/vaso/"* ]]; then
    printf '%s/%s\n' "$VASO_HOST" "${path#"/vaso/"}"
  else
    printf '%s\n' "$path"
  fi
}

CANONICAL_LOCK_SB="$EXP_SB/locks/$VASO_PROFILE.lock.json"
CANONICAL_LOCK_HOST="$EXP_DIR/locks/$VASO_PROFILE.lock.json"
LEGACY_ROOT_LOCK_HOST="$EXP_DIR/spack_graph.lock.json"
LOCK_CAPTURE_SB="/vaso/lines/$VASO_CUDA_LINE/state/spack-lock-captures"
VASO_LOCK_OUT="${VASO_LOCK_OUT:-$LOCK_CAPTURE_SB/$VASO_PROFILE.lock.json}"
VASO_BUILD_GRAPH_OUT="${VASO_BUILD_GRAPH_OUT:-$LOCK_CAPTURE_SB/$VASO_PROFILE.build_graph.json}"
LOCK_OUT_HOST="$(sandbox_path_to_host "$VASO_LOCK_OUT")"
BUILD_GRAPH_OUT_HOST="$(sandbox_path_to_host "$VASO_BUILD_GRAPH_OUT")"
export LOCK_OUT_HOST CANONICAL_LOCK_HOST

validate_lock_path_configuration() {
  if [[ ! -f "$CANONICAL_LOCK_HOST" ]]; then
    echo "missing canonical profile lock for VASO_PROFILE=$VASO_PROFILE: $CANONICAL_LOCK_HOST" >&2
    return 2
  fi

  local lock_out_real legacy_root_real profile_lock profile_lock_real
  lock_out_real="$(readlink -m "$LOCK_OUT_HOST")"
  legacy_root_real="$(readlink -m "$LEGACY_ROOT_LOCK_HOST")"
  if [[ "$lock_out_real" == "$legacy_root_real" ]]; then
    cat >&2 <<EOF
refusing to write legacy root lock output:
  VASO_LOCK_OUT=$VASO_LOCK_OUT

The canonical module lock input is $CANONICAL_LOCK_SB. Phase 1 writes side
captures only; choose a non-canonical VASO_LOCK_OUT for graph captures.
EOF
    return 2
  fi

  for profile_lock in "$EXP_DIR"/locks/*.lock.json; do
    [[ -e "$profile_lock" ]] || continue
    profile_lock_real="$(readlink -m "$profile_lock")"
    if [[ "$lock_out_real" == "$profile_lock_real" ]]; then
      cat >&2 <<EOF
refusing to overwrite checked-in profile lock:
  VASO_LOCK_OUT=$VASO_LOCK_OUT
  checked-in lock=$profile_lock

Use tools/lock_tool.py to edit profile locks. Phase 1 writes side captures only.
EOF
      return 2
    fi
  done
}
validate_lock_path_configuration
for capture_path in "$LOCK_OUT_HOST" "$BUILD_GRAPH_OUT_HOST"; do
  capture_path_real="$(readlink -m "$capture_path")"
  case "$capture_path_real" in
    "$ESTATE_ROOT"/*|"$EXP_DIR"/*) mkdir -p "$(dirname "$capture_path_real")" ;;
  esac
done

validate_canonical_lock() {
  python3 tools/lock_tool.py validate \
    --repo-root "$EXP_DIR" \
    --distdir "$NATIVE_SOURCE_DISTDIR_HOST" \
    --distdir "$PYTORCH_SOURCE_DISTDIR_HOST" \
    --distdir "$TRITON_SOURCE_DISTDIR_HOST" \
    --distdir "$JAX_SOURCE_DISTDIR_HOST" \
    --distdir "$BAZEL_TOOL_DISTDIR_HOST" \
    "$CANONICAL_LOCK_HOST"
}

if [[ "${1:-}" == "--print-lock-config-for-test" ]]; then
  printf 'VASO_PROFILE=%s\n' "$VASO_PROFILE"
  printf 'CANONICAL_LOCK_SB=%s\n' "$CANONICAL_LOCK_SB"
  printf 'CANONICAL_LOCK_HOST=%s\n' "$CANONICAL_LOCK_HOST"
  printf 'VASO_LOCK_OUT=%s\n' "$VASO_LOCK_OUT"
  printf 'LOCK_OUT_HOST=%s\n' "$LOCK_OUT_HOST"
  printf 'VASO_BUILD_GRAPH_OUT=%s\n' "$VASO_BUILD_GRAPH_OUT"
  printf 'BUILD_GRAPH_OUT_HOST=%s\n' "$BUILD_GRAPH_OUT_HOST"
  exit 0
fi

if [[ "${1:-}" == "--print-config-for-test" ]]; then
  printf 'VASO_AGENT=%s\n' "$VASO_AGENT"
  printf 'VASO_CUDA_LINE=%s\n' "$VASO_CUDA_LINE"
  printf 'BAZEL_OUTPUT_BASE_HOST=%s\n' "$BAZEL_OUTPUT_BASE_HOST"
  printf 'BAZEL_OUTPUT_BASE_SB=%s\n' "$BAZEL_OUTPUT_BASE_SB"
  printf 'BAZEL_REPO_CACHE_SB=%s\n' "$BAZEL_REPO_CACHE_SB"
  printf 'BAZEL_DISK_CACHE_SB=%s\n' "$BAZEL_DISK_CACHE_SB"
  printf 'VASO_RELEASE_GIT_SHA=%s\n' "$VASO_RELEASE_GIT_SHA"
  printf 'SOURCE_DATE_EPOCH=%s\n' "$VASO_SOURCE_DATE_EPOCH"
  printf 'BAZEL_EXECROOT_SB=%s\n' "$BAZEL_EXECROOT_SB"
  printf 'BAZEL_NATIVE_WORK_SB=%s\n' "$BAZEL_NATIVE_WORK_SB"
  printf 'NATIVE_ACTION_DETERMINISTIC_PREFIX_MAP_ROOTS=%s,%s\n' "$BAZEL_EXECROOT_SB" "$BAZEL_NATIVE_WORK_SB"
  printf 'NATIVE_ACTION_DETERMINISTIC_SEED=%s\n' "$VASO_RELEASE_GIT_SHA"
  printf 'BAZEL_RELEASE_ARG=%s\n' "${BAZEL_RELEASE_ARGS[@]}"
  exit 0
fi

if [[ "${1:-}" == "--stub-stage-for-test" ]]; then
  case "${2:-ok}" in
    ok)
      run_stage "stub-stage" bash -c 'echo "stub stage ok"'
      ;;
    fail)
      run_stage "stub-stage" bash -c 'echo "FAILED: stub stage" >&2; exit 17'
      ;;
    solve-fail)
      run_stage "lock-validate" bash -c '
spec="$1"
printf "%s\n" \
  "==> Error: No version exists that satisfies these input specs:" \
  "    $spec" >&2
exit 17
' bash "py-jax@0.10.2 ^py-jaxlib@0.10.2+cuda+nccl"
      ;;
    *)
      echo "usage: $0 --stub-stage-for-test [ok|fail|solve-fail]" >&2
      exit 2
      ;;
  esac
  exit 0
fi

# Repositories allowed in the explicit online fetch phase. Every entry is
# backed by a checked-in sha256 pin; normal build/test phases still run through
# bazel_insula() with --repository_disable_download.
SHA256_PINNED_FETCH_REPOS=(
  "@zlib_ng_action_archive"
  "@bzip2_action_archive"
  "@xz_action_archive"
  "@zstd_action_archive"
  "@py_absl_py_native"
  "@py_numpy_native"
  "@py_networkx_native"
  "@py_pillow_native"
  "@py_pyproject_hooks_native"
  "@py_build_native"
  "@py_hatch_fancy_pypi_readme_native"
  "@py_jax_native"
  "@py_ml_dtypes_native"
  "@py_opt_einsum_native"
  "@py_ply_native"
  "@py_pythran_native"
  "@py_scipy_native"
  "@py_scikit_build_core_native"
  "@py_setuptools_82_native"
  "@py_setuptools_scm_9_native"
  "@torchvision_v0_29_0_source"
  "@torchvision_v0_29_0_source_archive"
  "@torchaudio_v2_11_0_source"
  "@torchaudio_v2_11_0_source_archive"
  "@triton_v2_14_0_source"
  "@jax_v0_10_2_source"
  "@jax_v0_10_2_source_archive"
  "@rootfs_bazel_native"
  "@jaxlib_nested_http_archives"
  "@nlohmann_json_native"
  "@py_lit_native"
  "@py_pybind11_native"
  "@sleef_native"
  "@coreutils_native"
  "@gmake_native"
  "@xxd_standalone_native"
  "@libxml2_215_native"
)

# --- rootfs selection --------------------------------------------------------
# bwrap cannot create a new top-level mountpoint (/vaso, /home/kvothe) under a
# read-only root. So we always run over a thin "overlay root": a directory that
# symlinks the base system dirs from the chosen root and provides the vaso leaf
# mountpoints as real dirs to bind onto.
CUDA_ROOTFS="$ESTATE_ROOT/rootfs-lines/$VASO_CUDA_LINE/rootfs"
ROOTFS_BUNDLE_MANIFEST="$ESTATE_ROOT/rootfs-lines/$VASO_CUDA_LINE/rootfs-bundle.json"
ROOTFS_BUNDLE_MANIFEST_SB="/run/vaso/rootfs-bundle.json"
VASO_CUDA_HOME_SB="/usr/local/cuda"
if [[ -x "$CUDA_ROOTFS/bin/bash" ]]; then
  ROOTFS_MODE="cuda-bundle"
  BASE_ROOT="$CUDA_ROOTFS"
  INNER_EXTRA_PATH="$VASO_CUDA_HOME_SB/bin"
else
  cat >&2 <<EOF
missing hermetic insula rootfs for VASO_CUDA_LINE=$VASO_CUDA_LINE:
  $CUDA_ROOTFS/bin/bash

Build the Bazel/Spack insula rootfs first, for example:
  VASO_ESTATE_ROOT="$ESTATE_ROOT" "$REPO_ROOT/insula/rootfs/build_rootfs.sh" --line "$VASO_CUDA_LINE"

The legacy $ESTATE_ROOT/rootfs path is intentionally not selected implicitly.
EOF
  exit 2
fi
echo "rootfs mode: $ROOTFS_MODE (base root: $BASE_ROOT)"

ROOTFS_MANIFEST_BIND=()
if [[ -f "$ROOTFS_BUNDLE_MANIFEST" ]]; then
  ROOTFS_MANIFEST_BIND=(--ro-bind "$ROOTFS_BUNDLE_MANIFEST" "$ROOTFS_BUNDLE_MANIFEST_SB")
else
  echo "missing rootfs bundle manifest for VASO_CUDA_LINE=$VASO_CUDA_LINE: $ROOTFS_BUNDLE_MANIFEST" >&2
  exit 2
fi
INSULA_ROOTFS_LOCK="$EXP_DIR/rootfs/cuda_ecosystem.lock.json"
INSULA_REQUIRE_DRIVER=1
INSULA_ESTATE_MIN_FREE_GIB="${VASO_INSULA_MIN_FREE_GIB:-100}"
INSULA_DOCKER_ROOT="${VASO_DOCKER_ROOT:-/var/lib/docker}"
INSULA_DOCKER_MIN_FREE_GIB="${VASO_DOCKER_MIN_FREE_GIB:-100}"

rootfs_identity() {
  python3 - "$ROOTFS_MODE" "$BASE_ROOT" "$ROOTFS_BUNDLE_MANIFEST" <<'PY'
import hashlib
import json
import pathlib
import sys

mode, base_root, manifest = sys.argv[1:4]
manifest_path = pathlib.Path(manifest)
if manifest_path.is_file():
    data = manifest_path.read_bytes()
    try:
        doc = json.loads(data)
        digest = doc.get("derived_image_id") or hashlib.sha256(data).hexdigest()
    except json.JSONDecodeError:
        digest = hashlib.sha256(data).hexdigest()
else:
    base = pathlib.Path(base_root)
    try:
        st = base.stat()
        payload = f"{base.resolve()}:{st.st_dev}:{st.st_ino}:{st.st_mtime_ns}".encode()
    except OSError:
        payload = str(base).encode()
    digest = hashlib.sha256(payload).hexdigest()
print(f"{mode}:{digest}")
PY
}
ROOTFS_IDENTITY="$(rootfs_identity)"
ROOTFS_DIGEST="${ROOTFS_IDENTITY#*:}"
PHASE0_ROOTFS_STAMP_HOST="$BAZEL_OUTPUT_BASE_HOST/vaso-phase0-rootfs.identity"

CUDA_DRIVER_BINDS=()
insula_append_cuda_driver_binds CUDA_DRIVER_BINDS
for dev in /dev/nvidia*; do
  [[ -e "$dev" ]] && CUDA_DRIVER_BINDS+=(--dev-bind-try "$dev" "$dev")
done
[[ -d /proc/driver/nvidia ]] && CUDA_DRIVER_BINDS+=(--bind /proc/driver/nvidia /proc/driver/nvidia)
[[ -d /sys ]] && CUDA_DRIVER_BINDS+=(--ro-bind-try /sys /sys)

# Build the base-root bind plan. Every canonical sandbox dir becomes a real
# mountpoint (via --dir/--tmpfs) so the insula presents the full /vaso,
# /workspace, /opt/vaso, /home/kvothe namespace regardless of the base root.
MP_ARGS=()
for d in "${CANONICAL_DIRS[@]}"; do MP_ARGS+=(--mountpoint "$d"); done
MP_ARGS+=(--mountpoint /workspace/experiment)
MP_ARGS+=(--mountpoint /workspace/repo)
MP_ARGS+=(--mountpoint /workspace/third_party)
MP_ARGS+=(--mountpoint /vaso/rootfs-lines)
MP_ARGS+=(--mountpoint /run/nvidia-driver)
MP_ARGS+=(--mountpoint /run/nvidia-driver/lib)
if [[ "$BAZEL_OUTPUT_BASE_SB" != /vaso/* ]]; then
  MP_ARGS+=(--mountpoint "$BAZEL_OUTPUT_BASE_SB")
fi
mapfile -t ROOT_PLAN < <(python3 "$REPO_ROOT/insula/estate/overlay_root.py" --base "$BASE_ROOT" "${MP_ARGS[@]}")

INNER_PATH="/vaso/tools/bin${INNER_EXTRA_PATH:+:$INNER_EXTRA_PATH}:/usr/bin:/bin"
INSULA_LIBRARY_PATH="/run/nvidia-driver/lib:$VASO_CUDA_HOME_SB/lib64"

VASO_NATIVE_REPO_MAKE_JOBS="${VASO_NATIVE_REPO_MAKE_JOBS:-}"
resolve_default_jobs() {
  local jobs="${VASO_HOST_CPU_JOBS:-}"
  if [[ ! "$jobs" =~ ^[1-9][0-9]*$ ]]; then
    jobs="$(nproc 2>/dev/null || echo 1)"
  fi
  if [[ ! "$jobs" =~ ^[1-9][0-9]*$ ]]; then
    jobs=1
  fi
  printf '%s\n' "$jobs"
}
PYTORCH_MAX_JOBS_CAP=96
resolve_pytorch_max_jobs() {
  local requested="${VASO_PYTORCH_MAX_JOBS:-}"
  local jobs
  if [[ -n "$requested" ]]; then
    if [[ ! "$requested" =~ ^[1-9][0-9]*$ ]]; then
      echo "VASO_PYTORCH_MAX_JOBS must be a positive integer, got: $requested" >&2
      return 2
    fi
    if (( requested > PYTORCH_MAX_JOBS_CAP )); then
      echo "VASO_PYTORCH_MAX_JOBS must be <= $PYTORCH_MAX_JOBS_CAP, got: $requested" >&2
      return 2
    fi
    printf '%s\n' "$requested"
    return 0
  fi

  jobs="$(resolve_default_jobs)"
  if (( jobs > PYTORCH_MAX_JOBS_CAP )); then
    jobs="$PYTORCH_MAX_JOBS_CAP"
  fi
  printf '%s\n' "$jobs"
}
TORCHVISION_MAX_JOBS_CAP=96
resolve_torchvision_max_jobs() {
  local requested="${VASO_TORCHVISION_MAX_JOBS:-}"
  local jobs
  if [[ -n "$requested" ]]; then
    if [[ ! "$requested" =~ ^[1-9][0-9]*$ ]]; then
      echo "VASO_TORCHVISION_MAX_JOBS must be a positive integer, got: $requested" >&2
      return 2
    fi
    if (( requested > TORCHVISION_MAX_JOBS_CAP )); then
      echo "VASO_TORCHVISION_MAX_JOBS must be <= $TORCHVISION_MAX_JOBS_CAP, got: $requested" >&2
      return 2
    fi
    printf '%s\n' "$requested"
    return 0
  fi

  jobs="$(resolve_default_jobs)"
  if (( jobs > TORCHVISION_MAX_JOBS_CAP )); then
    jobs="$TORCHVISION_MAX_JOBS_CAP"
  fi
  printf '%s\n' "$jobs"
}
TORCHAUDIO_MAX_JOBS_CAP=96
resolve_torchaudio_max_jobs() {
  local requested="${VASO_TORCHAUDIO_MAX_JOBS:-}"
  local jobs
  if [[ -n "$requested" ]]; then
    if [[ ! "$requested" =~ ^[1-9][0-9]*$ ]]; then
      echo "VASO_TORCHAUDIO_MAX_JOBS must be a positive integer, got: $requested" >&2
      return 2
    fi
    if (( requested > TORCHAUDIO_MAX_JOBS_CAP )); then
      echo "VASO_TORCHAUDIO_MAX_JOBS must be <= $TORCHAUDIO_MAX_JOBS_CAP, got: $requested" >&2
      return 2
    fi
    printf '%s\n' "$requested"
    return 0
  fi

  jobs="$(resolve_default_jobs)"
  if (( jobs > TORCHAUDIO_MAX_JOBS_CAP )); then
    jobs="$TORCHAUDIO_MAX_JOBS_CAP"
  fi
  printf '%s\n' "$jobs"
}
VASO_PYTORCH_MAX_JOBS_RESOLVED="$(resolve_pytorch_max_jobs)"
printf '%s\n' "$VASO_PYTORCH_MAX_JOBS_RESOLVED" > "$VASO_NATIVE_STATE_HOST/pytorch_jobs.txt"
JAXLIB_MAX_JOBS_CAP=96
resolve_jaxlib_max_jobs() {
  local requested="${VASO_JAXLIB_MAX_JOBS:-}"
  local jobs
  if [[ -n "$requested" ]]; then
    if [[ ! "$requested" =~ ^[1-9][0-9]*$ ]]; then
      echo "VASO_JAXLIB_MAX_JOBS must be a positive integer, got: $requested" >&2
      return 2
    fi
    if (( requested > JAXLIB_MAX_JOBS_CAP )); then
      echo "VASO_JAXLIB_MAX_JOBS must be <= $JAXLIB_MAX_JOBS_CAP, got: $requested" >&2
      return 2
    fi
    printf '%s\n' "$requested"
    return 0
  fi

  jobs="$(resolve_default_jobs)"
  if (( jobs > JAXLIB_MAX_JOBS_CAP )); then
    jobs="$JAXLIB_MAX_JOBS_CAP"
  fi
  printf '%s\n' "$jobs"
}
VASO_JAXLIB_MAX_JOBS_RESOLVED="$(resolve_jaxlib_max_jobs)"
printf '%s\n' "$VASO_JAXLIB_MAX_JOBS_RESOLVED" > "$VASO_NATIVE_STATE_HOST/jaxlib_jobs.txt"
VASO_TORCHVISION_MAX_JOBS_RESOLVED="$(resolve_torchvision_max_jobs)"
VASO_TORCHAUDIO_MAX_JOBS_RESOLVED="$(resolve_torchaudio_max_jobs)"
BAZEL_REPO_ENV=(
  --repo_env=VASO_IN_INSULA=1
  "--repo_env=MAKE_JOBS=$VASO_NATIVE_REPO_MAKE_JOBS"
  "--repo_env=TMPDIR=$BAZEL_TMP_SB"
  "--repo_env=SOURCE_DATE_EPOCH=$(native_repo_source_date_epoch)"
  "--repo_env=VASO_HOST_CPU_JOBS=${VASO_HOST_CPU_JOBS:-}"
  "--repo_env=VASO_CUDA_HOME=$VASO_CUDA_HOME_SB"
  "--repo_env=VASO_CUDA_LINE=$VASO_CUDA_LINE"
  "--repo_env=VASO_PROFILE=$VASO_PROFILE"
  "--repo_env=VASO_ROOTFS_BUNDLE_MANIFEST=$ROOTFS_BUNDLE_MANIFEST_SB"
  "--repo_env=VASO_BAZEL=$ROOTFS_BAZEL_SB"
)
BAZEL_ACTION_ENV=(
  --action_env=VASO_IN_INSULA=1
  "--action_env=TMPDIR=$BAZEL_TMP_SB"
  "--action_env=VASO_CUDA_HOME=$VASO_CUDA_HOME_SB"
  "--action_env=VASO_CUDA_LINE=$VASO_CUDA_LINE"
  "--action_env=VASO_PROFILE=$VASO_PROFILE"
  --action_env=VASO_HOME=/vaso
  "--action_env=VASO_JAXLIB_MAX_JOBS=$VASO_JAXLIB_MAX_JOBS_RESOLVED"
  "--action_env=VASO_TORCHVISION_MAX_JOBS=$VASO_TORCHVISION_MAX_JOBS_RESOLVED"
  "--action_env=VASO_TORCHAUDIO_MAX_JOBS=$VASO_TORCHAUDIO_MAX_JOBS_RESOLVED"
  "--action_env=VASO_HOST_CPU_JOBS=${VASO_HOST_CPU_JOBS:-}"
  "--action_env=CUDA_VISIBLE_DEVICES=${CUDA_VISIBLE_DEVICES:-}"
  "--action_env=VASO_GPU_SET=${VASO_GPU_SET:-}"
  "--action_env=VASO_ROOTFS_BUNDLE_MANIFEST=$ROOTFS_BUNDLE_MANIFEST_SB"
)
BAZEL_TEST_ENV=(
  "--test_env=LD_LIBRARY_PATH=$INSULA_LIBRARY_PATH"
  --test_env=VASO_CUDA_DRIVER_LIB=/run/nvidia-driver/lib
  "--test_env=CUDA_VISIBLE_DEVICES=${CUDA_VISIBLE_DEVICES:-}"
  "--test_env=VASO_GPU_SET=${VASO_GPU_SET:-}"
  "--test_env=VASO_HOST_CPU_JOBS=${VASO_HOST_CPU_JOBS:-}"
)
# Base system is ro-bound per the plan; estate dirs are bound onto the declared
# mountpoints. /vaso is the writable surface; /opt/vaso and tool shims are ro.
INSULA_DIR_ARGS=(--dir /run/vaso --dir /run/nvidia-driver --dir /run/nvidia-driver/lib)
INSULA_BIND_ARGS=(
  --bind "$VASO_HOST" /vaso
  --bind "$ESTATE_ROOT/agents" /vaso/agents
  --ro-bind "$ESTATE_ROOT/rootfs-lines" /vaso/rootfs-lines
  --ro-bind "$SOURCES_HOST" /vaso/sources
  --bind "$BAZEL_OUTPUT_BASE_HOST" "$BAZEL_OUTPUT_BASE_SB"
  --bind "$BAZEL_DISK_CACHE_HOST" "$BAZEL_DISK_CACHE_SB"
  --bind "$VASO_NATIVE_STATE_HOST" "$VASO_NATIVE_STATE_SB"
  --bind "$VASO_NATIVE_STAMPS_HOST" "$VASO_NATIVE_STAMPS_SB"
  --ro-bind "$TOOLS_BIN_HOST" /vaso/tools/bin
  --ro-bind "$OPT_VASO_HOST" /opt/vaso
  --bind "$ESTATE_ROOT/workspace" /workspace
  --ro-bind "$REPO_ROOT" /workspace/repo
  --ro-bind "$REPO_ROOT/third_party" /workspace/third_party
  --bind "$HOME_HOST" "$HOME_SB"
  --bind "$EXP_DIR" "$EXP_SB"
)
INSULA_ENV_ARGS=(
  --setenv PATH "$INNER_PATH"
  --setenv LD_LIBRARY_PATH "$INSULA_LIBRARY_PATH"
  --setenv HOME "$HOME_SB"
  --setenv TMPDIR "$INSULA_TMP_SB"
  --setenv USER kvothe
  --setenv LOGNAME kvothe
  --setenv TERM "${TERM:-xterm}"
  --setenv XDG_CACHE_HOME "$HOME_SB/.cache"
  --setenv XDG_CONFIG_HOME "$HOME_SB/.config"
  --setenv VASO_HOME /vaso
  --setenv VASO_BAZEL_OB "$BAZEL_OUTPUT_BASE_SB"
  --setenv VASO_CUDA_HOME "$VASO_CUDA_HOME_SB"
  --setenv VASO_CUDA_LINE "$VASO_CUDA_LINE"
  --setenv VASO_INSULA_MIN_FREE_GIB "${VASO_INSULA_MIN_FREE_GIB:-}"
  --setenv INSULA_ESTATE_MIN_FREE_GIB "$INSULA_ESTATE_MIN_FREE_GIB"
  --setenv VASO_PROFILE "$VASO_PROFILE"
  --setenv CUDA_VISIBLE_DEVICES "${CUDA_VISIBLE_DEVICES:-}"
  --setenv VASO_GPU_SET "${VASO_GPU_SET:-}"
  --setenv VASO_HOST_CPU_JOBS "${VASO_HOST_CPU_JOBS:-}"
  --setenv VASO_LEASES_HELD "${VASO_LEASES_HELD:-}"
  --setenv VASO_JAXLIB_MAX_JOBS "$VASO_JAXLIB_MAX_JOBS_RESOLVED"
  --setenv VASO_IN_INSULA 1
  --setenv VASO_NATIVE_PYTORCH_TOKEN "${VASO_NATIVE_PYTORCH_TOKEN:-}"
  --setenv VASO_NATIVE_TORCHVISION_TOKEN "${VASO_NATIVE_TORCHVISION_TOKEN:-}"
  --setenv VASO_NATIVE_TORCHAUDIO_TOKEN "${VASO_NATIVE_TORCHAUDIO_TOKEN:-}"
  --setenv VASO_NATIVE_TRITON_TOKEN "${VASO_NATIVE_TRITON_TOKEN:-}"
  --setenv VASO_NATIVE_JAXLIB_TOKEN "${VASO_NATIVE_JAXLIB_TOKEN:-}"
  --setenv VASO_NATIVE_LLVM_TOKEN "${VASO_NATIVE_LLVM_TOKEN:-}"
  --setenv VASO_NATIVE_STATE "$VASO_NATIVE_STATE_SB"
  --setenv VASO_NATIVE_STAMPS "$VASO_NATIVE_STAMPS_SB"
  --setenv VASO_ROOTFS_BUNDLE_MANIFEST "$ROOTFS_BUNDLE_MANIFEST_SB"
  --setenv VASO_WORKSPACE_ROOT /workspace
  --setenv VASO_PREFIX /opt/vaso
  --setenv INSULA_BWRAP_BIN /vaso/tools/bin/bwrap
)
INSULA_CHDIR="$EXP_SB"
INSULA_UNSHARE_NET=1
materialize_rootfs_bazel_tool

if [[ "${1:-}" == "--cuda-line-smoke" ]]; then
  run_stage "cuda-line-smoke" insula bash -c '
set -euo pipefail
test -r "$VASO_ROOTFS_BUNDLE_MANIFEST"
test -x "$VASO_CUDA_HOME/bin/nvcc"
test "$LD_LIBRARY_PATH" = "/run/nvidia-driver/lib:$VASO_CUDA_HOME/lib64"
for soname in libcuda.so.1 libnvidia-ml.so.1 libnvidia-ptxjitcompiler.so.1 libnvidia-nvvm.so.4; do
  test -e "/run/nvidia-driver/lib/$soname"
done
compgen -G "/run/nvidia-driver/lib/libnvidia-gpucomp.so.*" >/dev/null
compgen -G "/dev/nvidia*" >/dev/null
python3 - <<PY
import json
import os

with open(os.environ["VASO_ROOTFS_BUNDLE_MANIFEST"], encoding="utf-8") as fh:
    manifest = json.load(fh)
line = os.environ["VASO_CUDA_LINE"]
actual_line = manifest.get("line")
if actual_line != line:
    raise SystemExit(f"manifest line mismatch: expected {line}, got {actual_line!r}")
cuda = manifest["verified_versions"]["components"]["cuda_toolkit"]["expected"]
print(f"cuda-line-smoke: ok line={line} cuda={cuda}")
PY
'
  exit 0
fi

if [[ "${1:-}" == "--insula-cmd" ]]; then
  shift
  if [[ "$#" -eq 0 ]]; then
    echo "usage: $0 --insula-cmd <command> [args...]" >&2
    exit 2
  fi
  echo "== validate canonical profile lock ($CANONICAL_LOCK_SB) =="
  validate_canonical_lock
  echo "== requested command (inside insula) =="
  run_stage "insula-cmd" insula "$@"
  echo "insula-cmd: ok line=$VASO_CUDA_LINE"
  exit 0
fi

bazel_insula() {
  # --batch avoids a persistent bazel server (the server does not survive the
  # insula's PID namespace teardown cleanly); --curses=no keeps output
  # line-based for logs. Cache/output all resolve to the persisted estate.
  local cmd="$1"
  shift
  if requires_rootfs_bazel_tool "$cmd" "$@"; then
    materialize_rootfs_bazel_tool
  fi
  local test_env_args=()
  if [[ "$cmd" == "test" ]]; then
    test_env_args=("${BAZEL_TEST_ENV[@]}")
  fi
  run_stage "bazel-$cmd" insula /vaso/tools/bin/bazel \
    "--output_base=$BAZEL_OUTPUT_BASE_SB" \
    "--host_jvm_args=-Djava.io.tmpdir=$INSULA_TMP_SB" \
    --batch \
    "$cmd" \
    "${BAZEL_REPO_ENV[@]}" \
    --registry=file:///vaso/sources/bcr/registry/current \
    --lockfile_mode=off \
    "${BAZEL_ACTION_ENV[@]}" \
    "${BAZEL_RELEASE_ARGS[@]}" \
    "${BAZEL_DISTDIR_ARGS[@]}" \
    "--repository_cache=$BAZEL_REPO_CACHE_SB" \
    "--disk_cache=$BAZEL_DISK_CACHE_SB" \
    "${bazel_disk_cache_gc_args[@]}" \
    "--local_resources=cpu=$VASO_PYTORCH_MAX_JOBS_RESOLVED" \
    --spawn_strategy=local \
    --curses=no \
    --color=no \
    "${test_env_args[@]}" \
    "$@"
}

normalize_fetch_repo() {
  local repo="$1"
  repo="${repo#@@}"
  repo="${repo#@}"
  printf '@%s\n' "$repo"
}

fetch_repo_source() {
  case "$1" in
    "@zlib_ng_action_archive")
      FETCH_SOURCE_URL="https://github.com/zlib-ng/zlib-ng/archive/2.3.3.tar.gz"
      FETCH_SOURCE_SHA256="f9c65aa9c852eb8255b636fd9f07ce1c406f061ec19a2e7d508b318ca0c907d1"
      FETCH_SOURCE_FILENAME="zlib-ng-2.3.3.tar.gz"
      FETCH_SOURCE_DISTDIR_HOST="$NATIVE_SOURCE_DISTDIR_HOST"
      FETCH_SOURCE_DISTDIR_SB="$NATIVE_SOURCE_DISTDIR_SB"
      ;;
    "@bzip2_action_archive")
      FETCH_SOURCE_URL="https://sourceware.org/pub/bzip2/bzip2-1.0.8.tar.gz"
      FETCH_SOURCE_SHA256="ab5a03176ee106d3f0fa90e381da478ddae405918153cca248e682cd0c4a2269"
      FETCH_SOURCE_FILENAME="bzip2-1.0.8.tar.gz"
      FETCH_SOURCE_DISTDIR_HOST="$NATIVE_SOURCE_DISTDIR_HOST"
      FETCH_SOURCE_DISTDIR_SB="$NATIVE_SOURCE_DISTDIR_SB"
      ;;
    "@xz_action_archive")
      FETCH_SOURCE_URL="https://github.com/tukaani-project/xz/releases/download/v5.8.3/xz-5.8.3.tar.gz"
      FETCH_SOURCE_SHA256="3d3a1b973af218114f4f889bbaa2f4c037deaae0c8e815eec381c3d546b974a0"
      FETCH_SOURCE_FILENAME="xz-5.8.3.tar.gz"
      FETCH_SOURCE_DISTDIR_HOST="$NATIVE_SOURCE_DISTDIR_HOST"
      FETCH_SOURCE_DISTDIR_SB="$NATIVE_SOURCE_DISTDIR_SB"
      ;;
    "@zstd_action_archive")
      FETCH_SOURCE_URL="https://github.com/facebook/zstd/archive/v1.5.7.tar.gz"
      FETCH_SOURCE_SHA256="37d7284556b20954e56e1ca85b80226768902e2edabd3b649e9e72c0c9012ee3"
      FETCH_SOURCE_FILENAME="zstd-1.5.7.tar.gz"
      FETCH_SOURCE_DISTDIR_HOST="$NATIVE_SOURCE_DISTDIR_HOST"
      FETCH_SOURCE_DISTDIR_SB="$NATIVE_SOURCE_DISTDIR_SB"
      ;;
    "@py_absl_py_native")
      FETCH_SOURCE_URL="https://files.pythonhosted.org/packages/source/a/absl-py/absl-py-1.4.0.tar.gz"
      FETCH_SOURCE_SHA256="d2c244d01048ba476e7c080bd2c6df5e141d211de80223460d5b3b8a2a58433d"
      FETCH_SOURCE_FILENAME="absl-py-1.4.0.tar.gz"
      FETCH_SOURCE_DISTDIR_HOST="$NATIVE_SOURCE_DISTDIR_HOST"
      FETCH_SOURCE_DISTDIR_SB="$NATIVE_SOURCE_DISTDIR_SB"
      ;;
    "@py_numpy_native")
      FETCH_SOURCE_URL="https://files.pythonhosted.org/packages/source/n/numpy/numpy-2.4.6.tar.gz"
      FETCH_SOURCE_SHA256="f3a3570c4a2a16746ac2c31a7c7c7b0c186b95ce902e33db6f28094ed7387dda"
      FETCH_SOURCE_FILENAME="numpy-2.4.6.tar.gz"
      FETCH_SOURCE_DISTDIR_HOST="$NATIVE_SOURCE_DISTDIR_HOST"
      FETCH_SOURCE_DISTDIR_SB="$NATIVE_SOURCE_DISTDIR_SB"
      ;;
    "@py_networkx_native")
      FETCH_SOURCE_URL="https://pypi.io/packages/source/n/networkx/networkx-3.6.1.tar.gz"
      FETCH_SOURCE_SHA256="26b7c357accc0c8cde558ad486283728b65b6a95d85ee1cd66bafab4c8168509"
      FETCH_SOURCE_FILENAME="networkx-3.6.1.tar.gz"
      FETCH_SOURCE_DISTDIR_HOST="$NATIVE_SOURCE_DISTDIR_HOST"
      FETCH_SOURCE_DISTDIR_SB="$NATIVE_SOURCE_DISTDIR_SB"
      ;;
    "@py_pillow_native")
      FETCH_SOURCE_URL="https://files.pythonhosted.org/packages/8c/21/c2bcdd5906101a30244eaffc1b6e6ce71a31bd0742a01eb89e660ebfac2d/pillow-12.2.0.tar.gz"
      FETCH_SOURCE_SHA256="a830b1a40919539d07806aa58e1b114df53ddd43213d9c8b75847eee6c0182b5"
      FETCH_SOURCE_FILENAME="pillow-12.2.0.tar.gz"
      FETCH_SOURCE_DISTDIR_HOST="$NATIVE_SOURCE_DISTDIR_HOST"
      FETCH_SOURCE_DISTDIR_SB="$NATIVE_SOURCE_DISTDIR_SB"
      ;;
    "@py_pyproject_hooks_native")
      FETCH_SOURCE_URL="https://files.pythonhosted.org/packages/e7/82/28175b2414effca1cdac8dc99f76d660e7a4fb0ceefa4b4ab8f5f6742925/pyproject_hooks-1.2.0.tar.gz"
      FETCH_SOURCE_SHA256="1e859bd5c40fae9448642dd871adf459e5e2084186e8d2c2a79a824c970da1f8"
      FETCH_SOURCE_FILENAME="pyproject_hooks-1.2.0.tar.gz"
      FETCH_SOURCE_DISTDIR_HOST="$NATIVE_SOURCE_DISTDIR_HOST"
      FETCH_SOURCE_DISTDIR_SB="$NATIVE_SOURCE_DISTDIR_SB"
      ;;
    "@py_build_native")
      FETCH_SOURCE_URL="https://files.pythonhosted.org/packages/3f/16/4b272700dea44c1d2e8ca963ebb3c684efe22b3eba8cfa31c5fdb60de707/build-1.4.3.tar.gz"
      FETCH_SOURCE_SHA256="5aa4231ae0e807efdf1fd0623e07366eca2ab215921345a2e38acdd5d0fa0a74"
      FETCH_SOURCE_FILENAME="build-1.4.3.tar.gz"
      FETCH_SOURCE_DISTDIR_HOST="$NATIVE_SOURCE_DISTDIR_HOST"
      FETCH_SOURCE_DISTDIR_SB="$NATIVE_SOURCE_DISTDIR_SB"
      ;;
    "@py_hatch_fancy_pypi_readme_native")
      FETCH_SOURCE_URL="https://files.pythonhosted.org/packages/f3/0f/aed57c301f339936eb91cb4d8c1e5088a101081854bd3ec18a889df32365/hatch_fancy_pypi_readme-25.1.0.tar.gz"
      FETCH_SOURCE_SHA256="9c58ed3dff90d51f43414ce37009ad1d5b0f08ffc9fc216998a06380f01c0045"
      FETCH_SOURCE_FILENAME="hatch_fancy_pypi_readme-25.1.0.tar.gz"
      FETCH_SOURCE_DISTDIR_HOST="$NATIVE_SOURCE_DISTDIR_HOST"
      FETCH_SOURCE_DISTDIR_SB="$NATIVE_SOURCE_DISTDIR_SB"
      ;;
    "@py_opt_einsum_native")
      FETCH_SOURCE_URL="https://files.pythonhosted.org/packages/8c/b9/2ac072041e899a52f20cf9510850ff58295003aa75525e58343591b0cbfb/opt_einsum-3.4.0.tar.gz"
      FETCH_SOURCE_SHA256="96ca72f1b886d148241348783498194c577fa30a8faac108586b14f1ba4473ac"
      FETCH_SOURCE_FILENAME="opt_einsum-3.4.0.tar.gz"
      FETCH_SOURCE_DISTDIR_HOST="$NATIVE_SOURCE_DISTDIR_HOST"
      FETCH_SOURCE_DISTDIR_SB="$NATIVE_SOURCE_DISTDIR_SB"
      ;;
    "@py_ml_dtypes_native")
      FETCH_SOURCE_URL="https://files.pythonhosted.org/packages/32/49/6e67c334872d2c114df3020e579f3718c333198f8312290e09ec0216703a/ml_dtypes-0.5.1.tar.gz"
      FETCH_SOURCE_SHA256="ac5b58559bb84a95848ed6984eb8013249f90b6bab62aa5acbad876e256002c9"
      FETCH_SOURCE_FILENAME="ml_dtypes-0.5.1.tar.gz"
      FETCH_SOURCE_DISTDIR_HOST="$NATIVE_SOURCE_DISTDIR_HOST"
      FETCH_SOURCE_DISTDIR_SB="$NATIVE_SOURCE_DISTDIR_SB"
      ;;
    "@py_ply_native")
      FETCH_SOURCE_URL="https://files.pythonhosted.org/packages/source/p/ply/ply-3.11.tar.gz"
      FETCH_SOURCE_SHA256="00c7c1aaa88358b9c765b6d3000c6eec0ba42abca5351b095321aef446081da3"
      FETCH_SOURCE_FILENAME="ply-3.11.tar.gz"
      FETCH_SOURCE_DISTDIR_HOST="$NATIVE_SOURCE_DISTDIR_HOST"
      FETCH_SOURCE_DISTDIR_SB="$NATIVE_SOURCE_DISTDIR_SB"
      ;;
    "@py_pythran_native")
      FETCH_SOURCE_URL="https://files.pythonhosted.org/packages/d4/84/17c4c44a24f5ec709991e603e601bf316d09c4fe915fbe348c689dede998/pythran-0.18.1.tar.gz"
      FETCH_SOURCE_SHA256="8803ed948bf841a11bbbb10472a8ff6ea24ebd70e67c3f77b77be3db900eccfe"
      FETCH_SOURCE_FILENAME="pythran-0.18.1.tar.gz"
      FETCH_SOURCE_DISTDIR_HOST="$NATIVE_SOURCE_DISTDIR_HOST"
      FETCH_SOURCE_DISTDIR_SB="$NATIVE_SOURCE_DISTDIR_SB"
      ;;
    "@py_scipy_native")
      FETCH_SOURCE_URL="https://files.pythonhosted.org/packages/7a/97/5a3609c4f8d58b039179648e62dd220f89864f56f7357f5d4f45c29eb2cc/scipy-1.17.1.tar.gz"
      FETCH_SOURCE_SHA256="95d8e012d8cb8816c226aef832200b1d45109ed4464303e997c5b13122b297c0"
      FETCH_SOURCE_FILENAME="scipy-1.17.1.tar.gz"
      FETCH_SOURCE_DISTDIR_HOST="$NATIVE_SOURCE_DISTDIR_HOST"
      FETCH_SOURCE_DISTDIR_SB="$NATIVE_SOURCE_DISTDIR_SB"
      ;;
    "@py_jax_native")
      FETCH_SOURCE_URL="https://files.pythonhosted.org/packages/source/j/jax/jax-0.10.2.tar.gz"
      FETCH_SOURCE_SHA256="bf77428a8c2e6904c4f46d5ab12aa5cfc6cad2179f07f7e4c0fc75ac86ef0639"
      FETCH_SOURCE_FILENAME="jax-0.10.2.tar.gz"
      FETCH_SOURCE_DISTDIR_HOST="$NATIVE_SOURCE_DISTDIR_HOST"
      FETCH_SOURCE_DISTDIR_SB="$NATIVE_SOURCE_DISTDIR_SB"
      ;;
    "@py_scikit_build_core_native")
      FETCH_SOURCE_URL="https://files.pythonhosted.org/packages/29/e2/4c0431fe84f9d8c24c1fc77b27264f568c71724ca888f514766986f270b0/scikit_build_core-1.0.0.tar.gz"
      FETCH_SOURCE_SHA256="b82a8b41dd66926b96096a61e8fc8df22214bbec437d251c0fda1bfb9d7df558"
      FETCH_SOURCE_FILENAME="scikit_build_core-1.0.0.tar.gz"
      FETCH_SOURCE_DISTDIR_HOST="$NATIVE_SOURCE_DISTDIR_HOST"
      FETCH_SOURCE_DISTDIR_SB="$NATIVE_SOURCE_DISTDIR_SB"
      ;;
    "@triton_v2_14_0_source")
      FETCH_SOURCE_URL="https://github.com/triton-lang/triton/archive/675c59878aa2280b31f722aaf42b825fcee21de8.tar.gz"
      FETCH_SOURCE_SHA256="61a11952362a0d54e67fb11fd29f25747da7339fe4ed39b2cff730c21ebe8825"
      FETCH_SOURCE_FILENAME="triton-675c59878aa2280b31f722aaf42b825fcee21de8.tar.gz"
      FETCH_SOURCE_DISTDIR_HOST="$TRITON_SOURCE_DISTDIR_HOST"
      FETCH_SOURCE_DISTDIR_SB="$TRITON_SOURCE_DISTDIR_SB"
      ;;
    "@jax_v0_10_2_source"|"@jax_v0_10_2_source_archive")
      FETCH_SOURCE_URL="https://github.com/jax-ml/jax/archive/refs/tags/jax-v0.10.2.tar.gz"
      FETCH_SOURCE_SHA256="fa7214ab31ed1cd418b4305807e9c4f3f175c783eeea40c28e0f77c3f4c24bc7"
      FETCH_SOURCE_FILENAME="jax-v0.10.2.tar.gz"
      FETCH_SOURCE_DISTDIR_HOST="$JAX_SOURCE_DISTDIR_HOST"
      FETCH_SOURCE_DISTDIR_SB="$JAX_SOURCE_DISTDIR_SB"
      ;;
    "@rootfs_bazel_native")
      FETCH_SOURCE_URL="$ROOTFS_BAZEL_URL"
      FETCH_SOURCE_SHA256="$ROOTFS_BAZEL_SHA256"
      FETCH_SOURCE_FILENAME="$ROOTFS_BAZEL_FILENAME"
      FETCH_SOURCE_DISTDIR_HOST="$BAZEL_TOOL_DISTDIR_HOST"
      FETCH_SOURCE_DISTDIR_SB="$BAZEL_TOOL_DISTDIR_SB"
      ;;
    "@torchvision_v0_29_0_source")
      FETCH_SOURCE_URL="https://github.com/pytorch/vision/archive/refs/tags/v0.29.0.tar.gz"
      FETCH_SOURCE_SHA256="24be57d922927d8a2ac2e8f076f07c3447ddf8f1d25ddbb7b65578f36c9ab8e3"
      FETCH_SOURCE_FILENAME="torchvision-v0.29.0.tar.gz"
      FETCH_SOURCE_DISTDIR_HOST="$NATIVE_SOURCE_DISTDIR_HOST"
      FETCH_SOURCE_DISTDIR_SB="$NATIVE_SOURCE_DISTDIR_SB"
      ;;
    "@torchvision_v0_29_0_source_archive")
      FETCH_SOURCE_URL="https://github.com/pytorch/vision/archive/refs/tags/v0.29.0.tar.gz"
      FETCH_SOURCE_SHA256="24be57d922927d8a2ac2e8f076f07c3447ddf8f1d25ddbb7b65578f36c9ab8e3"
      FETCH_SOURCE_FILENAME="torchvision-v0.29.0.tar.gz"
      FETCH_SOURCE_DISTDIR_HOST="$NATIVE_SOURCE_DISTDIR_HOST"
      FETCH_SOURCE_DISTDIR_SB="$NATIVE_SOURCE_DISTDIR_SB"
      ;;
    "@torchaudio_v2_11_0_source")
      FETCH_SOURCE_URL="https://github.com/pytorch/audio/archive/refs/tags/v2.11.0.tar.gz"
      FETCH_SOURCE_SHA256="599ec24e7e1eef476ef21f0178e33da00e2434f930ba42e9cc20bf4002220486"
      FETCH_SOURCE_FILENAME="torchaudio-v2.11.0.tar.gz"
      FETCH_SOURCE_DISTDIR_HOST="$NATIVE_SOURCE_DISTDIR_HOST"
      FETCH_SOURCE_DISTDIR_SB="$NATIVE_SOURCE_DISTDIR_SB"
      ;;
    "@torchaudio_v2_11_0_source_archive")
      FETCH_SOURCE_URL="https://github.com/pytorch/audio/archive/refs/tags/v2.11.0.tar.gz"
      FETCH_SOURCE_SHA256="599ec24e7e1eef476ef21f0178e33da00e2434f930ba42e9cc20bf4002220486"
      FETCH_SOURCE_FILENAME="torchaudio-v2.11.0.tar.gz"
      FETCH_SOURCE_DISTDIR_HOST="$NATIVE_SOURCE_DISTDIR_HOST"
      FETCH_SOURCE_DISTDIR_SB="$NATIVE_SOURCE_DISTDIR_SB"
      ;;
    "@py_setuptools_82_native")
      FETCH_SOURCE_URL="https://files.pythonhosted.org/packages/py3/s/setuptools/setuptools-82.0.1-py3-none-any.whl"
      FETCH_SOURCE_SHA256="a59e362652f08dcd477c78bb6e7bd9d80a7995bc73ce773050228a348ce2e5bb"
      FETCH_SOURCE_FILENAME="setuptools-82.0.1-py3-none-any.whl"
      FETCH_SOURCE_DISTDIR_HOST="$NATIVE_SOURCE_DISTDIR_HOST"
      FETCH_SOURCE_DISTDIR_SB="$NATIVE_SOURCE_DISTDIR_SB"
      ;;
    "@py_setuptools_scm_9_native")
      FETCH_SOURCE_URL="https://files.pythonhosted.org/packages/7b/b1/19587742aad604f1988a8a362e660e8c3ac03adccdb71c96d86526e5eb62/setuptools_scm-9.2.2.tar.gz"
      FETCH_SOURCE_SHA256="1c674ab4665686a0887d7e24c03ab25f24201c213e82ea689d2f3e169ef7ef57"
      FETCH_SOURCE_FILENAME="setuptools_scm-9.2.2.tar.gz"
      FETCH_SOURCE_DISTDIR_HOST="$NATIVE_SOURCE_DISTDIR_HOST"
      FETCH_SOURCE_DISTDIR_SB="$NATIVE_SOURCE_DISTDIR_SB"
      ;;
    "@nlohmann_json_native")
      FETCH_SOURCE_URL="https://github.com/nlohmann/json/archive/v3.11.3.tar.gz"
      FETCH_SOURCE_SHA256="0d8ef5af7f9794e3263480193c491549b2ba6cc74bb018906202ada498a79406"
      FETCH_SOURCE_FILENAME="v3.11.3.tar.gz"
      FETCH_SOURCE_DISTDIR_HOST="$NATIVE_SOURCE_DISTDIR_HOST"
      FETCH_SOURCE_DISTDIR_SB="$NATIVE_SOURCE_DISTDIR_SB"
      ;;
    "@py_lit_native")
      FETCH_SOURCE_URL="https://files.pythonhosted.org/packages/source/l/lit/lit-18.1.8.tar.gz"
      FETCH_SOURCE_SHA256="47c174a186941ae830f04ded76a3444600be67d5e5fb8282c3783fba671c4edb"
      FETCH_SOURCE_FILENAME="lit-18.1.8.tar.gz"
      FETCH_SOURCE_DISTDIR_HOST="$NATIVE_SOURCE_DISTDIR_HOST"
      FETCH_SOURCE_DISTDIR_SB="$NATIVE_SOURCE_DISTDIR_SB"
      ;;
    "@py_pybind11_native")
      FETCH_SOURCE_URL="https://github.com/pybind/pybind11/archive/refs/tags/v3.0.2.tar.gz"
      FETCH_SOURCE_SHA256="2f20a0af0b921815e0e169ea7fec63909869323581b89d7de1553468553f6a2d"
      FETCH_SOURCE_FILENAME="v3.0.2.tar.gz"
      FETCH_SOURCE_DISTDIR_HOST="$NATIVE_SOURCE_DISTDIR_HOST"
      FETCH_SOURCE_DISTDIR_SB="$NATIVE_SOURCE_DISTDIR_SB"
      ;;
    "@sleef_native")
      FETCH_SOURCE_URL="https://github.com/shibatch/sleef/archive/3.8.tar.gz"
      FETCH_SOURCE_SHA256="a12ccd50f57083c530e1c76f10d52865defbd19fc9e2c85b483493065709874a"
      FETCH_SOURCE_FILENAME="3.8.tar.gz"
      FETCH_SOURCE_DISTDIR_HOST="$NATIVE_SOURCE_DISTDIR_HOST"
      FETCH_SOURCE_DISTDIR_SB="$NATIVE_SOURCE_DISTDIR_SB"
      ;;
    "@coreutils_native")
      FETCH_SOURCE_URL="https://ftpmirror.gnu.org/coreutils/coreutils-9.10.tar.xz"
      FETCH_SOURCE_SHA256="16535a9adf0b10037364e2d612aad3d9f4eca3a344949ced74d12faf4bd51d25"
      FETCH_SOURCE_FILENAME="coreutils-9.10.tar.xz"
      FETCH_SOURCE_DISTDIR_HOST="$NATIVE_SOURCE_DISTDIR_HOST"
      FETCH_SOURCE_DISTDIR_SB="$NATIVE_SOURCE_DISTDIR_SB"
      ;;
    "@gmake_native")
      FETCH_SOURCE_URL="https://ftp.gnu.org/gnu/make/make-4.4.1.tar.gz"
      FETCH_SOURCE_SHA256="dd16fb1d67bfab79a72f5e8390735c49e3e8e70b4945a15ab1f81ddb78658fb3"
      FETCH_SOURCE_FILENAME="make-4.4.1.tar.gz"
      FETCH_SOURCE_DISTDIR_HOST="$NATIVE_SOURCE_DISTDIR_HOST"
      FETCH_SOURCE_DISTDIR_SB="$NATIVE_SOURCE_DISTDIR_SB"
      ;;
    "@xxd_standalone_native")
      FETCH_SOURCE_URL="https://github.com/vim/vim/archive/v8.2.1201.tar.gz"
      FETCH_SOURCE_SHA256="39032fe866f44724b104468038dc9ac4ff2c00a4b18c9a1e2c27064ab1f1143d"
      FETCH_SOURCE_FILENAME="v8.2.1201.tar.gz"
      FETCH_SOURCE_DISTDIR_HOST="$NATIVE_SOURCE_DISTDIR_HOST"
      FETCH_SOURCE_DISTDIR_SB="$NATIVE_SOURCE_DISTDIR_SB"
      ;;
    "@libxml2_215_native")
      FETCH_SOURCE_URL="https://download.gnome.org/sources/libxml2/2.15/libxml2-2.15.3.tar.xz"
      FETCH_SOURCE_SHA256="78262a6e7ac170d6528ebfe2efccdf220191a5af6a6cd61ea4a9a9a5042c7a07"
      FETCH_SOURCE_FILENAME="libxml2-2.15.3.tar.xz"
      FETCH_SOURCE_DISTDIR_HOST="$NATIVE_SOURCE_DISTDIR_HOST"
      FETCH_SOURCE_DISTDIR_SB="$NATIVE_SOURCE_DISTDIR_SB"
      ;;
    *)
      return 1
      ;;
  esac
}

fetch_repo_allowed() {
  local repo="$1"
  local allowed
  for allowed in "${SHA256_PINNED_FETCH_REPOS[@]}"; do
    [[ "$repo" == "$allowed" ]] && return 0
  done
  return 1
}

seed_bazel_repository_cache() {
  local src="$1"
  local sha256="$2"
  [[ -n "${BAZEL_REPO_CACHE_HOST:-}" ]] || return 0
  [[ -f "$src" ]] || { echo "repository-cache seed source is missing: $src" >&2; return 1; }

  local cache_dir="$BAZEL_REPO_CACHE_HOST/content_addressable/sha256/$sha256"
  local dest="$cache_dir/file"
  local actual=""
  if [[ -f "$dest" ]]; then
    actual="$(sha256sum "$dest" | awk '{print $1}')"
    if [[ "$actual" == "$sha256" ]]; then
      return 0
    fi
    echo "fetch-only: replacing checksum-mismatched repository-cache entry sha256=$sha256 actual=$actual" >&2
  fi

  mkdir -p "$cache_dir"
  local tmp="$dest.tmp.$$"
  cp "$src" "$tmp"
  actual="$(sha256sum "$tmp" | awk '{print $1}')"
  if [[ "$actual" != "$sha256" ]]; then
    rm -f "$tmp"
    echo "fetch-only: repository-cache seed mismatch src=$src expected=$sha256 actual=$actual" >&2
    return 1
  fi
  chmod 0444 "$tmp"
  mv -f "$tmp" "$dest"
  echo "fetch-only: seeded repository-cache sha256=$sha256 path=$dest"
}

prefetch_jaxlib_nested_http_archives() {
  local manifest="$JAXLIB_NESTED_HTTP_ARCHIVES_MANIFEST"
  [[ -f "$manifest" ]] || { echo "missing JAX nested http archive manifest: $manifest" >&2; return 2; }

  local count=0
  local manifest_rows name sha256 filename joined_url_list url rc ok dest
  local -a urls
  manifest_rows="$(python3 - "$manifest" <<'PY'
import json
import re
import sys

manifest_path = sys.argv[1]
with open(manifest_path, encoding="utf-8") as handle:
    manifest = json.load(handle)

archives = manifest.get("archives")
if not isinstance(archives, list) or manifest.get("archive_count") != len(archives):
    raise SystemExit(f"invalid JAX nested archive manifest count: {manifest_path}")

seen_filenames = set()
for archive in archives:
    name = str(archive.get("name", ""))
    sha256 = str(archive.get("sha256", ""))
    filename = str(archive.get("filename", ""))
    urls = archive.get("urls")
    if not name or not re.fullmatch(r"[0-9A-Za-z_.-]+", name):
        raise SystemExit(f"invalid JAX nested archive name in {manifest_path}: {name!r}")
    if not re.fullmatch(r"[0-9a-f]{64}", sha256):
        raise SystemExit(f"invalid JAX nested archive sha256 for {name}: {sha256!r}")
    if not filename or "/" in filename or ".." in filename or filename in seen_filenames:
        raise SystemExit(f"invalid JAX nested archive filename for {name}: {filename!r}")
    if not isinstance(urls, list) or not urls or any(not str(url).startswith("https://") for url in urls):
        raise SystemExit(f"invalid JAX nested archive URLs for {name}")
    seen_filenames.add(filename)
    print("\t".join((name, sha256, filename, " ".join(str(url) for url in urls))))
PY
)" || return $?
  while IFS=$'\t' read -r name sha256 filename joined_url_list; do
    [[ -n "$name" ]] || continue
    count=$((count + 1))
    ok=0
    read -r -a urls <<<"$joined_url_list"
    for url in "${urls[@]}"; do
      if prefetch_one_sha256_source \
        "@jaxlib_nested_http_archive//$name" \
        "$url" \
        "$sha256" \
        "$filename" \
        "$JAX_SOURCE_DISTDIR_HOST" \
        "$JAX_SOURCE_DISTDIR_SB"; then
        dest="$JAX_SOURCE_DISTDIR_HOST/$filename"
        seed_bazel_repository_cache "$dest" "$sha256"
        ok=1
        break
      else
        rc=$?
        [[ "$rc" == "2" ]] && return 2
      fi
    done
    if [[ "$ok" != "1" ]]; then
      echo "fetch-only: failed to fetch JAX nested archive name=$name sha256=$sha256" >&2
      return 1
    fi
  done <<<"$manifest_rows"
  echo "fetch-only: jaxlib nested archives ok count=$count manifest=$manifest distdir=$JAX_SOURCE_DISTDIR_HOST repository_cache=$BAZEL_REPO_CACHE_HOST"
}

prefetch_sha256_source() {
  local repo="$1"
  if [[ "$repo" == "@jaxlib_nested_http_archives" ]]; then
    prefetch_jaxlib_nested_http_archives
    return $?
  fi

  fetch_repo_source "$repo" || return 1

  prefetch_one_sha256_source "$repo" "$FETCH_SOURCE_URL" "$FETCH_SOURCE_SHA256" "$FETCH_SOURCE_FILENAME" "$FETCH_SOURCE_DISTDIR_HOST" "$FETCH_SOURCE_DISTDIR_SB"

  if [[ "$repo" == "@py_ml_dtypes_native" ]]; then
    prefetch_one_sha256_source \
      "$repo" \
      "https://gitlab.com/libeigen/eigen/-/raw/7bf2968fed5f246c0589e1111004cb420fcd7c71/Eigen/src/Core/arch/AVX512/TrsmUnrolls.inc" \
      "aa05b280fc2419e0378961a534de8c0fb2be5b15cf7638b314a6097863e5634f" \
      "TrsmUnrolls.inc" \
      "$NATIVE_SOURCE_DISTDIR_HOST" \
      "$NATIVE_SOURCE_DISTDIR_SB"
  fi
}

prefetch_one_sha256_source() {
  local repo="$1"
  local url="$2"
  local sha256="$3"
  local filename="$4"
  local distdir_host="$5"
  local distdir_sb="$6"

  if [[ ! "$sha256" =~ ^[0-9a-f]{64}$ ]]; then
    echo "refusing to fetch $repo without a valid sha256: $sha256" >&2
    return 2
  fi
  if [[ "$url" != https://* ]]; then
    echo "refusing to fetch $repo from non-https URL: $url" >&2
    return 2
  fi
  if ! command -v curl >/dev/null 2>&1; then
    echo "curl is required for fetch-only source prefetch" >&2
    return 1
  fi

  mkdir -p "$distdir_host"
  local dest="$distdir_host/$filename"
  local actual=""
  if [[ -f "$dest" ]]; then
    actual="$(sha256sum "$dest" | awk '{print $1}')"
    if [[ "$actual" == "$sha256" ]]; then
      echo "fetch-only: cache hit repo=$repo url=$url sha256=$sha256 path=$dest distdir=$distdir_sb"
      return 0
    fi
    echo "fetch-only: replacing checksum-mismatched source repo=$repo path=$dest expected=$sha256 actual=$actual" >&2
  fi

  local tmp="$dest.tmp.$$"
  rm -f "$tmp"
  echo "fetch-only: download repo=$repo url=$url sha256=$sha256 path=$dest distdir=$distdir_sb"
  if ! insula_network_fetch --sha256 "$sha256" -- curl -fL --proto '=https' --tlsv1.2 --retry 3 --retry-delay 2 -o "$tmp" "$url"; then
    rm -f "$tmp"
    return 1
  fi
  actual="$(sha256sum "$tmp" | awk '{print $1}')"
  if [[ "$actual" != "$sha256" ]]; then
    rm -f "$tmp"
    echo "fetch-only: sha256 mismatch repo=$repo path=$tmp expected=$sha256 actual=$actual" >&2
    return 1
  fi
  mv -f "$tmp" "$dest"
  chmod 0444 "$dest"
  echo "fetch-only: stored repo=$repo url=$url sha256=$sha256 path=$dest"
}

jaxlib_bazel_build_token_args() {
  local arg
  local needs_token=0
  for arg in "$@"; do
    case "$arg" in
      *"//native/jaxlib:jaxlib_action"*|*"//native/jaxlib:all"*)
        needs_token=1
        ;;
      "--//native/jaxlib:token="*)
        return 0
        ;;
    esac
  done
  [[ "$needs_token" == "1" ]] || return 0

  JAXLIB_BUILD_TOKEN="${VASO_NATIVE_JAXLIB_TOKEN:-${VASO_NATIVE_LLVM_TOKEN:-}}"
  if [[ -z "$JAXLIB_BUILD_TOKEN" ]]; then
    echo "jaxlib build requires VASO_NATIVE_JAXLIB_TOKEN or VASO_NATIVE_LLVM_TOKEN" >&2
    return 2
  fi
  printf '%s\n' "--//native/jaxlib:token=$JAXLIB_BUILD_TOKEN"
}

if [[ "${1:-}" == "--fetch-only" ]]; then
  VASO_FETCH=1
fi
if [[ "${VASO_FETCH:-0}" == "1" ]]; then
  FETCH_REPOS=()
  if [[ -n "${VASO_FETCH_REPOS:-}" ]]; then
    for repo in ${VASO_FETCH_REPOS//,/ }; do
      [[ -n "$repo" ]] && FETCH_REPOS+=("$(normalize_fetch_repo "$repo")")
    done
  else
    FETCH_REPOS=("${SHA256_PINNED_FETCH_REPOS[@]}")
  fi
  if [[ "${#FETCH_REPOS[@]}" -eq 0 ]]; then
    echo "fetch-only: no repositories requested" >&2
    exit 2
  fi
  echo "== prefetch: sha256-pinned source archives (online, explicit) =="
  for repo in "${FETCH_REPOS[@]}"; do
    if ! fetch_repo_allowed "$repo"; then
      echo "refusing to fetch non-allowlisted or unpinned source repo: $repo" >&2
      echo "allowed sha256-pinned source repos: ${SHA256_PINNED_FETCH_REPOS[*]}" >&2
      exit 2
    fi
    echo "fetch-only: fetching sha256-pinned source: $repo"
    run_stage "fetch-$repo" prefetch_sha256_source "$repo"
  done
  echo "fetch-only: ok repos=${#FETCH_REPOS[@]} distdir=$NATIVE_SOURCE_DISTDIR_HOST"
  exit 0
fi

if [[ "${1:-}" == "--jaxlib-prefetch" ]]; then
  shift
  echo "== prefetch: JAX nested Bazel repositories (sha256 manifest) =="
  run_stage "fetch-@jax_v0_10_2_source" prefetch_sha256_source "@jax_v0_10_2_source"
  run_stage "fetch-@jax_v0_10_2_source_archive" prefetch_sha256_source "@jax_v0_10_2_source_archive"
  run_stage "fetch-@rootfs_bazel_native" prefetch_sha256_source "@rootfs_bazel_native"
  run_stage "fetch-@jaxlib_nested_http_archives" prefetch_sha256_source "@jaxlib_nested_http_archives"
  echo "jaxlib-prefetch: ok line=$VASO_CUDA_LINE"
  exit 0
fi

if [[ "${1:-}" == "--cubin-guard-offline-check" ]]; then
  shift
  echo "== cubin_guard offline check (inside insula, downloads disabled) =="
  bazel_insula test //tools:cubin_guard_test --test_output=all --announce_rc "$@"
  echo "cubin-guard-offline-check: ok line=$VASO_CUDA_LINE"
  exit 0
fi

if [[ "${1:-}" == "--bazel-build" ]]; then
  shift
  if [[ "$#" -eq 0 ]]; then
    echo "usage: $0 --bazel-build <target-or-bazel-arg>..." >&2
    exit 2
  fi
  extra_bazel_args=()
  if jaxlib_token_arg="$(jaxlib_bazel_build_token_args "$@")"; then
    [[ -n "$jaxlib_token_arg" ]] && extra_bazel_args+=("$jaxlib_token_arg")
  else
    exit $?
  fi
  echo "== requested Bazel build targets (inside insula, offline) =="
  bazel_insula build --announce_rc "${extra_bazel_args[@]}" "$@"
  echo "bazel-build: ok line=$VASO_CUDA_LINE targets=$#"
  exit 0
fi

if [[ "${1:-}" == "--bazel-aquery" ]]; then
  shift
  [[ "$#" -gt 0 ]] || { echo "usage: $0 --bazel-aquery <expression-or-bazel-arg>..." >&2; exit 2; }
  extra_bazel_args=()
  if jaxlib_token_arg="$(jaxlib_bazel_build_token_args "$@")"; then
    [[ -z "$jaxlib_token_arg" ]] || extra_bazel_args+=("$jaxlib_token_arg")
  else
    exit $?
  fi
  echo "== requested Bazel action graph (inside insula, offline) =="
  bazel_insula aquery --announce_rc "${extra_bazel_args[@]}" "$@"
  echo "bazel-aquery: ok line=$VASO_CUDA_LINE"
  exit 0
fi

if [[ "${1:-}" == "--rootfs-boundaries-smoke" ]]; then
  ROOTFS_BOUNDARY_TARGETS=(
    //synthetic:use_cuda_boundary
    //synthetic:use_cudnn_native
    //synthetic:use_cusparselt_native
    //synthetic:use_cudss_native
    //synthetic:use_nccl_native
    //synthetic:use_nvshmem_native
  )
  echo "rootfs-boundaries-smoke: testing line=$VASO_CUDA_LINE"
  bazel_insula test "${ROOTFS_BOUNDARY_TARGETS[@]}" \
    --test_output=all \
    --announce_rc \
    --test_env=VASO_IN_INSULA=1 \
    "--test_env=VASO_ROOTFS_BUNDLE_MANIFEST=$ROOTFS_BUNDLE_MANIFEST_SB"
  echo "rootfs-boundaries-smoke: ok line=$VASO_CUDA_LINE targets=${#ROOTFS_BOUNDARY_TARGETS[@]}"
  exit 0
fi

echo "== phase 0: refresh Bazel configurable repos inside insula =="
# The Bazel output base persists across runs and may have been configured under
# a different rootfs. Re-run only the compiler-config repos when that identity
# changes so repository-rule native prefixes are not invalidated by a global
# forced fetch.
DEFAULT_PHASE0_COMPILER_CONFIG_REPOS=(
  "@@rules_cc++cc_configure_extension+local_config_cc"
  "@@rules_cc++cc_configure_extension+local_config_cc_toolchains"
)
PHASE0_COMPILER_CONFIG_REPOS=("${DEFAULT_PHASE0_COMPILER_CONFIG_REPOS[@]}")
if [[ -n "${VASO_PHASE0_COMPILER_CONFIG_REPOS:-}" ]]; then
  PHASE0_COMPILER_CONFIG_REPOS=()
  for repo in ${VASO_PHASE0_COMPILER_CONFIG_REPOS//,/ }; do
    [[ -n "$repo" ]] && PHASE0_COMPILER_CONFIG_REPOS+=("$repo")
  done
fi
if [[ ${#PHASE0_COMPILER_CONFIG_REPOS[@]} -eq 0 ]]; then
  echo "no Bazel compiler-config repos configured for phase 0" >&2
  exit 2
fi
mkdir -p "$BAZEL_OUTPUT_BASE_HOST"
if [[ "$(cat "$PHASE0_ROOTFS_STAMP_HOST" 2>/dev/null || true)" == "$ROOTFS_IDENTITY" ]]; then
  echo "phase 0: compiler-config repos already current for rootfs identity $ROOTFS_MODE/$ROOTFS_DIGEST"
else
  echo "phase 0: refreshing compiler-config repos for rootfs identity $ROOTFS_MODE/$ROOTFS_DIGEST"
  for repo in "${PHASE0_COMPILER_CONFIG_REPOS[@]}"; do
    echo "refreshing Bazel compiler-config repo inside insula: $repo"
    bazel_insula fetch --force --repo="$repo"
  done
  tmp="$PHASE0_ROOTFS_STAMP_HOST.tmp.$$"
  printf '%s\n' "$ROOTFS_IDENTITY" > "$tmp"
  mv -f "$tmp" "$PHASE0_ROOTFS_STAMP_HOST"
fi
if [[ -n "${VASO_FORCE_FETCH_REPOS:-}" ]]; then
  for repo in ${VASO_FORCE_FETCH_REPOS//,/ }; do
    [[ -n "$repo" ]] || continue
    echo "forcing Bazel repo fetch inside insula: $repo"
    bazel_insula fetch --force --repo="$repo"
  done
fi

echo "== phase 1: validate canonical profile lock ($CANONICAL_LOCK_SB) =="
validate_canonical_lock

# Resolve Spack reference prefixes from the canonical profile lock so ABI-parity
# gates compare native builds against the lock that Bazel reads. A key may be
# absent when the selected profile does not include that node.
lock_field() {  # $1=bazel repo name  $2=field
  python3 - "$1" "$2" <<'PY'
import json
import os
import sys

key = sys.argv[1]
field = sys.argv[2]
path = os.environ["CANONICAL_LOCK_HOST"]
try:
    with open(path, encoding="utf-8") as fh:
        lock = json.load(fh)
except FileNotFoundError:
    raise SystemExit(f"canonical profile lock is missing: {path}")
except json.JSONDecodeError as exc:
    raise SystemExit(f"canonical profile lock is invalid JSON: {path}: {exc}")
pkg = lock.get("packages", {}).get(key)
print(pkg.get(field, "") if pkg else "")
PY
}
lock_prefix() {  # $1=bazel repo name
  lock_field "$1" prefix
}
lock_build() {  # $1=bazel repo name
  lock_field "$1" build
}
lock_version() {  # $1=bazel repo name
  lock_field "$1" version
}
SPACK_ZLIB_NG_PREFIX="$(lock_prefix vaso_zlib_ng)"
SPACK_BISON_PREFIX="$(lock_prefix vaso_bison)"
SPACK_ZSTD_PREFIX="$(lock_prefix vaso_zstd)"
SPACK_BINUTILS_PREFIX="$(lock_prefix vaso_binutils)"
SPACK_FILE_PREFIX="$(lock_prefix vaso_file)"
SPACK_XZ_PREFIX="$(lock_prefix vaso_xz)"
SPACK_LIBXML2_PREFIX="$(lock_prefix vaso_libxml2)"
SPACK_LIBXML2_VERSION="$(lock_version vaso_libxml2)"
SPACK_LIBICONV_PREFIX="$(lock_prefix vaso_libiconv)"
SPACK_LIBFFI_PREFIX="$(lock_prefix vaso_libffi)"
SPACK_DIFFUTILS_PREFIX="$(lock_prefix vaso_diffutils)"
SPACK_BZIP2_PREFIX="$(lock_prefix vaso_bzip2)"
SPACK_BOOST_PREFIX="$(lock_prefix vaso_boost)"
SPACK_LIBMD_PREFIX="$(lock_prefix vaso_libmd)"
SPACK_LIBBSD_PREFIX="$(lock_prefix vaso_libbsd)"
SPACK_EXPAT_PREFIX="$(lock_prefix vaso_expat)"
SPACK_GPERF_PREFIX="$(lock_prefix vaso_gperf)"
SPACK_GMAKE_PREFIX="$(lock_prefix vaso_gmake)"
SPACK_XXD_STANDALONE_PREFIX="$(lock_prefix vaso_xxd_standalone)"
SPACK_RE2C_PREFIX="$(lock_prefix vaso_re2c)"
SPACK_NINJA_PREFIX="$(lock_prefix vaso_ninja)"
SPACK_MESON_PREFIX="$(lock_prefix vaso_meson)"
SPACK_CPUINFO_PREFIX="$(lock_prefix vaso_cpuinfo)"
SPACK_FP16_PREFIX="$(lock_prefix vaso_fp16)"
SPACK_FXDIV_PREFIX="$(lock_prefix vaso_fxdiv)"
SPACK_PSIMD_PREFIX="$(lock_prefix vaso_psimd)"
SPACK_PTHREADPOOL_PREFIX="$(lock_prefix vaso_pthreadpool)"
SPACK_GZIP_PREFIX="$(lock_prefix vaso_gzip)"
SPACK_FINDUTILS_PREFIX="$(lock_prefix vaso_findutils)"
SPACK_FLEX_PREFIX="$(lock_prefix vaso_flex)"
SPACK_KRB5_PREFIX="$(lock_prefix vaso_krb5)"
SPACK_LIBTOOL_PREFIX="$(lock_prefix vaso_libtool)"
SPACK_FRIBIDI_PREFIX="$(lock_prefix vaso_fribidi)"
SPACK_NUMACTL_PREFIX="$(lock_prefix vaso_numactl)"
SPACK_OPENSSH_PREFIX="$(lock_prefix vaso_openssh)"
SPACK_GIT_PREFIX="$(lock_prefix vaso_git)"
SPACK_LIBSIGSEGV_PREFIX="$(lock_prefix vaso_libsigsegv)"
SPACK_LIBUNISTRING_PREFIX="$(lock_prefix vaso_libunistring)"
SPACK_LIBIDN2_PREFIX="$(lock_prefix vaso_libidn2)"
SPACK_LIBYAML_PREFIX="$(lock_prefix vaso_libyaml)"
SPACK_LZO_PREFIX="$(lock_prefix vaso_lzo)"
SPACK_M4_PREFIX="$(lock_prefix vaso_m4)"
SPACK_NASM_PREFIX="$(lock_prefix vaso_nasm)"
SPACK_OPENBLAS_PREFIX="$(lock_prefix vaso_openblas)"
SPACK_PCRE2_PREFIX="$(lock_prefix vaso_pcre2)"
SPACK_NCURSES_PREFIX="$(lock_prefix vaso_ncurses)"
SPACK_LESS_PREFIX="$(lock_prefix vaso_less)"
SPACK_LIBEDIT_PREFIX="$(lock_prefix vaso_libedit)"
SPACK_NGHTTP2_PREFIX="$(lock_prefix vaso_nghttp2)"
SPACK_LIBEVENT_PREFIX="$(lock_prefix vaso_libevent)"
SPACK_LIBJPEG_TURBO_PREFIX="$(lock_prefix vaso_libjpeg_turbo)"
SPACK_LIBPNG_PREFIX="$(lock_prefix vaso_libpng)"
SPACK_PIXMAN_PREFIX="$(lock_prefix vaso_pixman)"
SPACK_CAIRO_PREFIX="$(lock_prefix vaso_cairo)"
SPACK_HARFBUZZ_PREFIX="$(lock_prefix vaso_harfbuzz)"
SPACK_LIBRAQM_PREFIX="$(lock_prefix vaso_libraqm)"
SPACK_FREETYPE_PREFIX="$(lock_prefix vaso_freetype)"
SPACK_READLINE_PREFIX="$(lock_prefix vaso_readline)"
SPACK_GDBM_PREFIX="$(lock_prefix vaso_gdbm)"
SPACK_UNZIP_PREFIX="$(lock_prefix vaso_unzip)"
SPACK_UTIL_LINUX_UUID_PREFIX="$(lock_prefix vaso_util_linux_uuid)"
SPACK_UTIL_MACROS_PREFIX="$(lock_prefix vaso_util_macros)"
SPACK_FONTSPROTO_PREFIX="$(lock_prefix vaso_fontsproto)"
SPACK_LIBPCIACCESS_PREFIX="$(lock_prefix vaso_libpciaccess)"
SPACK_XPROTO_PREFIX="$(lock_prefix vaso_xproto)"
SPACK_XTRANS_PREFIX="$(lock_prefix vaso_xtrans)"
SPACK_LIBFONTENC_PREFIX="$(lock_prefix vaso_libfontenc)"
SPACK_LIBXFONT_PREFIX="$(lock_prefix vaso_libxfont)"
SPACK_BDFTOPCF_PREFIX="$(lock_prefix vaso_bdftopcf)"
SPACK_LUA_PREFIX="$(lock_prefix vaso_lua)"
SPACK_MKFONTSCALE_PREFIX="$(lock_prefix vaso_mkfontscale)"
SPACK_MKFONTDIR_PREFIX="$(lock_prefix vaso_mkfontdir)"
SPACK_FONT_UTIL_PREFIX="$(lock_prefix vaso_font_util)"
SPACK_FONTCONFIG_PREFIX="$(lock_prefix vaso_fontconfig)"
SPACK_ICU4C_PREFIX="$(lock_prefix vaso_icu4c)"
SPACK_PERL_DATA_DUMPER_PREFIX="$(lock_prefix vaso_perl_data_dumper)"
SPACK_HWLOC_PREFIX="$(lock_prefix vaso_hwloc)"
SPACK_PERL_PREFIX="$(lock_prefix vaso_perl)"
SPACK_AUTOCONF_PREFIX="$(lock_prefix vaso_autoconf)"
SPACK_AUTOMAKE_PREFIX="$(lock_prefix vaso_automake)"
SPACK_LIBXCRYPT_PREFIX="$(lock_prefix vaso_libxcrypt)"
SPACK_OPENSSL_PREFIX="$(lock_prefix vaso_openssl)"
SPACK_COREUTILS_PREFIX="$(lock_prefix vaso_coreutils)"
SPACK_CUDA_PREFIX="$(lock_prefix vaso_cuda)"
SPACK_CUDSS_PREFIX="$(lock_prefix vaso_cudss)"
SPACK_CUDNN_PREFIX="$(lock_prefix vaso_cudnn)"
SPACK_CUSPARSELT_PREFIX="$(lock_prefix vaso_cusparselt)"
SPACK_NCCL_PREFIX="$(lock_prefix vaso_nccl)"
SPACK_MAGMA_PREFIX="$(lock_prefix vaso_magma)"
SPACK_NVSHMEM_PREFIX="$(lock_prefix vaso_nvshmem)"
SPACK_CURL_PREFIX="$(lock_prefix vaso_curl)"
SPACK_CMAKE_PREFIX="$(lock_prefix vaso_cmake)"
SPACK_EIGEN_PREFIX="$(lock_prefix vaso_eigen)"
SPACK_PIGZ_PREFIX="$(lock_prefix vaso_pigz)"
SPACK_SQLITE_PREFIX="$(lock_prefix vaso_sqlite)"
SPACK_TAR_PREFIX="$(lock_prefix vaso_tar)"
SPACK_GETTEXT_PREFIX="$(lock_prefix vaso_gettext)"
SPACK_GLIB_BOOTSTRAP_PREFIX="$(lock_prefix vaso_glib_bootstrap)"
SPACK_GLIB_PREFIX="$(lock_prefix vaso_glib)"
SPACK_GOBJECT_INTROSPECTION_PREFIX="$(lock_prefix vaso_gobject_introspection)"
SPACK_ELFUTILS_PREFIX="$(lock_prefix vaso_elfutils)"
SPACK_PYTHON_PREFIX="$(lock_prefix vaso_python)"
SPACK_PYTHON_VENV_PREFIX="$(lock_prefix vaso_python_venv)"
SPACK_PY_CALVER_PREFIX="$(lock_prefix vaso_py_calver)"
SPACK_PY_CERTIFI_PREFIX="$(lock_prefix vaso_py_certifi)"
SPACK_PY_CHARSET_NORMALIZER_PREFIX="$(lock_prefix vaso_py_charset_normalizer)"
SPACK_PY_CYCLER_PREFIX="$(lock_prefix vaso_py_cycler)"
SPACK_PY_CYTHON_PREFIX="$(lock_prefix vaso_py_cython)"
SPACK_PY_FLIT_CORE_PREFIX="$(lock_prefix vaso_py_flit_core)"
SPACK_PY_FONTTOOLS_PREFIX="$(lock_prefix vaso_py_fonttools)"
SPACK_PY_GAST_PREFIX="$(lock_prefix vaso_py_gast)"
SPACK_PY_BENIGET_PREFIX="$(lock_prefix vaso_py_beniget)"
SPACK_PY_IDNA_PREFIX="$(lock_prefix vaso_py_idna)"
SPACK_PY_JINJA2_PREFIX="$(lock_prefix vaso_py_jinja2)"
SPACK_PY_MARKUPSAFE_PREFIX="$(lock_prefix vaso_py_markupsafe)"
SPACK_PY_MPMATH_PREFIX="$(lock_prefix vaso_py_mpmath)"
SPACK_PY_NETWORKX_PREFIX="$(lock_prefix vaso_py_networkx)"
SPACK_PY_PACKAGING_PREFIX="$(lock_prefix vaso_py_packaging)"
SPACK_PY_PATHSPEC_PREFIX="$(lock_prefix vaso_py_pathspec)"
SPACK_PY_PLY_PREFIX="$(lock_prefix vaso_py_ply)"
SPACK_PY_PYPARSING_PREFIX="$(lock_prefix vaso_py_pyparsing)"
SPACK_PY_PYPROJECT_HOOKS_PREFIX="$(lock_prefix vaso_py_pyproject_hooks)"
SPACK_PY_BUILD_PREFIX="$(lock_prefix vaso_py_build)"
SPACK_PY_PYPROJECT_METADATA_PREFIX="$(lock_prefix vaso_py_pyproject_metadata)"
SPACK_PY_MESON_PYTHON_PREFIX="$(lock_prefix vaso_py_meson_python)"
SPACK_PY_ML_DTYPES_PREFIX="$(lock_prefix vaso_py_ml_dtypes)"
SPACK_PY_NUMPY_PREFIX="$(lock_prefix vaso_py_numpy)"
SPACK_PY_PYTHRAN_PREFIX="$(lock_prefix vaso_py_pythran)"
SPACK_PY_SCIPY_PREFIX="$(lock_prefix vaso_py_scipy)"
SPACK_PY_PYBIND11_PREFIX="$(lock_prefix vaso_py_pybind11)"
SPACK_PY_PYYAML_PREFIX="$(lock_prefix vaso_py_pyyaml)"
SPACK_PY_SETUPTOOLS_SCM_PREFIX="$(lock_prefix vaso_py_setuptools_scm)"
SPACK_PY_CPPY_PREFIX="$(lock_prefix vaso_py_cppy)"
SPACK_PY_KIWISOLVER_PREFIX="$(lock_prefix vaso_py_kiwisolver)"
SPACK_SLEEF_PREFIX="$(lock_prefix vaso_sleef)"
SPACK_PY_PLUGGY_PREFIX="$(lock_prefix vaso_py_pluggy)"
SPACK_PY_SIX_PREFIX="$(lock_prefix vaso_py_six)"
SPACK_PY_PROTOBUF_PREFIX="$(lock_prefix vaso_py_protobuf)"
SPACK_PY_PYTHON_DATEUTIL_PREFIX="$(lock_prefix vaso_py_python_dateutil)"
SPACK_PY_SYMPY_PREFIX="$(lock_prefix vaso_py_sympy)"
SPACK_PY_TQDM_PREFIX="$(lock_prefix vaso_py_tqdm)"
SPACK_PY_TROVE_CLASSIFIERS_PREFIX="$(lock_prefix vaso_py_trove_classifiers)"
SPACK_PY_HATCHLING_PREFIX="$(lock_prefix vaso_py_hatchling)"
SPACK_PY_HATCH_FANCY_PYPI_README_PREFIX="$(lock_prefix vaso_py_hatch_fancy_pypi_readme)"
SPACK_PY_HATCH_VCS_PREFIX="$(lock_prefix vaso_py_hatch_vcs)"
SPACK_PY_OPT_EINSUM_PREFIX="$(lock_prefix vaso_py_opt_einsum)"
SPACK_PY_FILELOCK_PREFIX="$(lock_prefix vaso_py_filelock)"
SPACK_PY_FSSPEC_PREFIX="$(lock_prefix vaso_py_fsspec)"
SPACK_PY_SCIKIT_BUILD_CORE_PREFIX="$(lock_prefix vaso_py_scikit_build_core)"
SPACK_PY_TYPING_EXTENSIONS_PREFIX="$(lock_prefix vaso_py_typing_extensions)"
SPACK_PY_URLLIB3_PREFIX="$(lock_prefix vaso_py_urllib3)"
SPACK_PY_REQUESTS_PREFIX="$(lock_prefix vaso_py_requests)"
SPACK_PY_VERSIONEER_PREFIX="$(lock_prefix vaso_py_versioneer)"
SPACK_NVTX_PREFIX="$(lock_prefix vaso_nvtx)"
SPACK_PY_PIP_PREFIX="$(lock_prefix vaso_py_pip)"
SPACK_PY_SETUPTOOLS_PREFIX="$(lock_prefix vaso_py_setuptools)"
SPACK_PY_WHEEL_PREFIX="$(lock_prefix vaso_py_wheel)"
SPACK_PKGCONF_PREFIX="$(lock_prefix vaso_pkgconf)"
SPACK_CA_CERTIFICATES_MOZILLA_PREFIX="$(lock_prefix vaso_ca_certificates_mozilla)"
SPACK_BERKELEY_DB_PREFIX="$(lock_prefix vaso_berkeley_db)"
SPACK_PROTOBUF_PREFIX="$(lock_prefix vaso_protobuf)"
SPACK_QHULL_PREFIX="$(lock_prefix vaso_qhull)"
SPACK_SWIG_PREFIX="$(lock_prefix vaso_swig)"
SPACK_PMIX_PREFIX="$(lock_prefix vaso_pmix)"
SPACK_PRRTE_PREFIX="$(lock_prefix vaso_prrte)"
SPACK_OPENMPI_PREFIX="$(lock_prefix vaso_openmpi)"
SPACK_ZLIB_NG_BUILD="$(lock_build vaso_zlib_ng)"
SPACK_BISON_BUILD="$(lock_build vaso_bison)"
SPACK_ZSTD_BUILD="$(lock_build vaso_zstd)"
SPACK_BINUTILS_BUILD="$(lock_build vaso_binutils)"
SPACK_FILE_BUILD="$(lock_build vaso_file)"
SPACK_XZ_BUILD="$(lock_build vaso_xz)"
SPACK_LIBXML2_BUILD="$(lock_build vaso_libxml2)"
SPACK_LIBICONV_BUILD="$(lock_build vaso_libiconv)"
SPACK_LIBFFI_BUILD="$(lock_build vaso_libffi)"
SPACK_DIFFUTILS_BUILD="$(lock_build vaso_diffutils)"
SPACK_BZIP2_BUILD="$(lock_build vaso_bzip2)"
SPACK_BOOST_BUILD="$(lock_build vaso_boost)"
SPACK_LIBMD_BUILD="$(lock_build vaso_libmd)"
SPACK_LIBBSD_BUILD="$(lock_build vaso_libbsd)"
SPACK_EXPAT_BUILD="$(lock_build vaso_expat)"
SPACK_GPERF_BUILD="$(lock_build vaso_gperf)"
SPACK_GMAKE_BUILD="$(lock_build vaso_gmake)"
SPACK_XXD_STANDALONE_BUILD="$(lock_build vaso_xxd_standalone)"
SPACK_RE2C_BUILD="$(lock_build vaso_re2c)"
SPACK_NINJA_BUILD="$(lock_build vaso_ninja)"
SPACK_CPUINFO_BUILD="$(lock_build vaso_cpuinfo)"
SPACK_FP16_BUILD="$(lock_build vaso_fp16)"
SPACK_FXDIV_BUILD="$(lock_build vaso_fxdiv)"
SPACK_PSIMD_BUILD="$(lock_build vaso_psimd)"
SPACK_PTHREADPOOL_BUILD="$(lock_build vaso_pthreadpool)"
SPACK_GZIP_BUILD="$(lock_build vaso_gzip)"
SPACK_FINDUTILS_BUILD="$(lock_build vaso_findutils)"
SPACK_FLEX_BUILD="$(lock_build vaso_flex)"
SPACK_KRB5_BUILD="$(lock_build vaso_krb5)"
SPACK_LIBTOOL_BUILD="$(lock_build vaso_libtool)"
SPACK_FRIBIDI_BUILD="$(lock_build vaso_fribidi)"
SPACK_NUMACTL_BUILD="$(lock_build vaso_numactl)"
SPACK_OPENSSH_BUILD="$(lock_build vaso_openssh)"
SPACK_GIT_BUILD="$(lock_build vaso_git)"
SPACK_LIBSIGSEGV_BUILD="$(lock_build vaso_libsigsegv)"
SPACK_LIBUNISTRING_BUILD="$(lock_build vaso_libunistring)"
SPACK_LIBIDN2_BUILD="$(lock_build vaso_libidn2)"
SPACK_LIBYAML_BUILD="$(lock_build vaso_libyaml)"
SPACK_LZO_BUILD="$(lock_build vaso_lzo)"
SPACK_M4_BUILD="$(lock_build vaso_m4)"
SPACK_NASM_BUILD="$(lock_build vaso_nasm)"
SPACK_OPENBLAS_BUILD="$(lock_build vaso_openblas)"
SPACK_PCRE2_BUILD="$(lock_build vaso_pcre2)"
SPACK_NCURSES_BUILD="$(lock_build vaso_ncurses)"
SPACK_LESS_BUILD="$(lock_build vaso_less)"
SPACK_LIBEDIT_BUILD="$(lock_build vaso_libedit)"
SPACK_NGHTTP2_BUILD="$(lock_build vaso_nghttp2)"
SPACK_LIBEVENT_BUILD="$(lock_build vaso_libevent)"
SPACK_LIBJPEG_TURBO_BUILD="$(lock_build vaso_libjpeg_turbo)"
SPACK_LIBPNG_BUILD="$(lock_build vaso_libpng)"
SPACK_PIXMAN_BUILD="$(lock_build vaso_pixman)"
SPACK_CAIRO_BUILD="$(lock_build vaso_cairo)"
SPACK_HARFBUZZ_BUILD="$(lock_build vaso_harfbuzz)"
SPACK_LIBRAQM_BUILD="$(lock_build vaso_libraqm)"
SPACK_FREETYPE_BUILD="$(lock_build vaso_freetype)"
SPACK_READLINE_BUILD="$(lock_build vaso_readline)"
SPACK_GDBM_BUILD="$(lock_build vaso_gdbm)"
SPACK_UNZIP_BUILD="$(lock_build vaso_unzip)"
SPACK_UTIL_LINUX_UUID_BUILD="$(lock_build vaso_util_linux_uuid)"
SPACK_UTIL_MACROS_BUILD="$(lock_build vaso_util_macros)"
SPACK_FONTSPROTO_BUILD="$(lock_build vaso_fontsproto)"
SPACK_LIBPCIACCESS_BUILD="$(lock_build vaso_libpciaccess)"
SPACK_XPROTO_BUILD="$(lock_build vaso_xproto)"
SPACK_XTRANS_BUILD="$(lock_build vaso_xtrans)"
SPACK_LIBFONTENC_BUILD="$(lock_build vaso_libfontenc)"
SPACK_LIBXFONT_BUILD="$(lock_build vaso_libxfont)"
SPACK_BDFTOPCF_BUILD="$(lock_build vaso_bdftopcf)"
SPACK_LUA_BUILD="$(lock_build vaso_lua)"
SPACK_MKFONTSCALE_BUILD="$(lock_build vaso_mkfontscale)"
SPACK_MKFONTDIR_BUILD="$(lock_build vaso_mkfontdir)"
SPACK_FONT_UTIL_BUILD="$(lock_build vaso_font_util)"
SPACK_FONTCONFIG_BUILD="$(lock_build vaso_fontconfig)"
SPACK_ICU4C_BUILD="$(lock_build vaso_icu4c)"
SPACK_PERL_DATA_DUMPER_BUILD="$(lock_build vaso_perl_data_dumper)"
SPACK_HWLOC_BUILD="$(lock_build vaso_hwloc)"
SPACK_PERL_BUILD="$(lock_build vaso_perl)"
SPACK_AUTOCONF_BUILD="$(lock_build vaso_autoconf)"
SPACK_AUTOMAKE_BUILD="$(lock_build vaso_automake)"
SPACK_LIBXCRYPT_BUILD="$(lock_build vaso_libxcrypt)"
SPACK_OPENSSL_BUILD="$(lock_build vaso_openssl)"
SPACK_COREUTILS_BUILD="$(lock_build vaso_coreutils)"
SPACK_CUDA_BUILD="$(lock_build vaso_cuda)"
SPACK_CUDSS_BUILD="$(lock_build vaso_cudss)"
SPACK_CUDNN_BUILD="$(lock_build vaso_cudnn)"
SPACK_CUSPARSELT_BUILD="$(lock_build vaso_cusparselt)"
SPACK_NCCL_BUILD="$(lock_build vaso_nccl)"
SPACK_MAGMA_BUILD="$(lock_build vaso_magma)"
SPACK_NVSHMEM_BUILD="$(lock_build vaso_nvshmem)"
SPACK_CURL_BUILD="$(lock_build vaso_curl)"
SPACK_CMAKE_BUILD="$(lock_build vaso_cmake)"
SPACK_EIGEN_BUILD="$(lock_build vaso_eigen)"
SPACK_PIGZ_BUILD="$(lock_build vaso_pigz)"
SPACK_SQLITE_BUILD="$(lock_build vaso_sqlite)"
SPACK_TAR_BUILD="$(lock_build vaso_tar)"
SPACK_GETTEXT_BUILD="$(lock_build vaso_gettext)"
SPACK_GLIB_BOOTSTRAP_BUILD="$(lock_build vaso_glib_bootstrap)"
SPACK_GLIB_BUILD="$(lock_build vaso_glib)"
SPACK_GOBJECT_INTROSPECTION_BUILD="$(lock_build vaso_gobject_introspection)"
SPACK_ELFUTILS_BUILD="$(lock_build vaso_elfutils)"
SPACK_PYTHON_BUILD="$(lock_build vaso_python)"
SPACK_PYTHON_VERSION="$(lock_version vaso_python)"
SPACK_PYTHON_VENV_BUILD="$(lock_build vaso_python_venv)"
SPACK_PY_CALVER_BUILD="$(lock_build vaso_py_calver)"
SPACK_PY_CERTIFI_BUILD="$(lock_build vaso_py_certifi)"
SPACK_PY_CHARSET_NORMALIZER_BUILD="$(lock_build vaso_py_charset_normalizer)"
SPACK_PY_CYCLER_BUILD="$(lock_build vaso_py_cycler)"
SPACK_PY_CYTHON_BUILD="$(lock_build vaso_py_cython)"
SPACK_PY_FLIT_CORE_BUILD="$(lock_build vaso_py_flit_core)"
SPACK_PY_FONTTOOLS_BUILD="$(lock_build vaso_py_fonttools)"
SPACK_PY_GAST_BUILD="$(lock_build vaso_py_gast)"
SPACK_PY_BENIGET_BUILD="$(lock_build vaso_py_beniget)"
SPACK_PY_IDNA_BUILD="$(lock_build vaso_py_idna)"
SPACK_PY_JINJA2_BUILD="$(lock_build vaso_py_jinja2)"
SPACK_PY_MARKUPSAFE_BUILD="$(lock_build vaso_py_markupsafe)"
SPACK_PY_MPMATH_BUILD="$(lock_build vaso_py_mpmath)"
SPACK_PY_NETWORKX_BUILD="$(lock_build vaso_py_networkx)"
SPACK_PY_PACKAGING_BUILD="$(lock_build vaso_py_packaging)"
SPACK_PY_PATHSPEC_BUILD="$(lock_build vaso_py_pathspec)"
SPACK_PY_PLY_BUILD="$(lock_build vaso_py_ply)"
SPACK_PY_PYPARSING_BUILD="$(lock_build vaso_py_pyparsing)"
SPACK_PY_PYPROJECT_HOOKS_BUILD="$(lock_build vaso_py_pyproject_hooks)"
SPACK_PY_BUILD_BUILD="$(lock_build vaso_py_build)"
SPACK_PY_PYPROJECT_METADATA_BUILD="$(lock_build vaso_py_pyproject_metadata)"
SPACK_PY_MESON_PYTHON_BUILD="$(lock_build vaso_py_meson_python)"
SPACK_PY_ML_DTYPES_BUILD="$(lock_build vaso_py_ml_dtypes)"
SPACK_PY_NUMPY_BUILD="$(lock_build vaso_py_numpy)"
SPACK_PY_PYTHRAN_BUILD="$(lock_build vaso_py_pythran)"
SPACK_PY_SCIPY_BUILD="$(lock_build vaso_py_scipy)"
SPACK_PY_PYBIND11_BUILD="$(lock_build vaso_py_pybind11)"
SPACK_PY_PYYAML_BUILD="$(lock_build vaso_py_pyyaml)"
SPACK_PY_SETUPTOOLS_SCM_BUILD="$(lock_build vaso_py_setuptools_scm)"
SPACK_PY_CPPY_BUILD="$(lock_build vaso_py_cppy)"
SPACK_PY_KIWISOLVER_BUILD="$(lock_build vaso_py_kiwisolver)"
SPACK_SLEEF_BUILD="$(lock_build vaso_sleef)"
SPACK_PY_PLUGGY_BUILD="$(lock_build vaso_py_pluggy)"
SPACK_PY_SIX_BUILD="$(lock_build vaso_py_six)"
SPACK_PY_PROTOBUF_BUILD="$(lock_build vaso_py_protobuf)"
SPACK_PY_PYTHON_DATEUTIL_BUILD="$(lock_build vaso_py_python_dateutil)"
SPACK_PY_SYMPY_BUILD="$(lock_build vaso_py_sympy)"
SPACK_PY_TQDM_BUILD="$(lock_build vaso_py_tqdm)"
SPACK_PY_TROVE_CLASSIFIERS_BUILD="$(lock_build vaso_py_trove_classifiers)"
SPACK_PY_HATCHLING_BUILD="$(lock_build vaso_py_hatchling)"
SPACK_PY_HATCH_FANCY_PYPI_README_BUILD="$(lock_build vaso_py_hatch_fancy_pypi_readme)"
SPACK_PY_HATCH_VCS_BUILD="$(lock_build vaso_py_hatch_vcs)"
SPACK_PY_OPT_EINSUM_BUILD="$(lock_build vaso_py_opt_einsum)"
SPACK_PY_FILELOCK_BUILD="$(lock_build vaso_py_filelock)"
SPACK_PY_FSSPEC_BUILD="$(lock_build vaso_py_fsspec)"
SPACK_PY_SCIKIT_BUILD_CORE_BUILD="$(lock_build vaso_py_scikit_build_core)"
SPACK_PY_TYPING_EXTENSIONS_BUILD="$(lock_build vaso_py_typing_extensions)"
SPACK_PY_URLLIB3_BUILD="$(lock_build vaso_py_urllib3)"
SPACK_PY_REQUESTS_BUILD="$(lock_build vaso_py_requests)"
SPACK_PY_VERSIONEER_BUILD="$(lock_build vaso_py_versioneer)"
SPACK_NVTX_BUILD="$(lock_build vaso_nvtx)"
SPACK_MESON_BUILD="$(lock_build vaso_meson)"
SPACK_PY_PIP_BUILD="$(lock_build vaso_py_pip)"
SPACK_PY_SETUPTOOLS_BUILD="$(lock_build vaso_py_setuptools)"
SPACK_PY_WHEEL_BUILD="$(lock_build vaso_py_wheel)"
SPACK_PKGCONF_BUILD="$(lock_build vaso_pkgconf)"
SPACK_CA_CERTIFICATES_MOZILLA_BUILD="$(lock_build vaso_ca_certificates_mozilla)"
SPACK_BERKELEY_DB_BUILD="$(lock_build vaso_berkeley_db)"
SPACK_PROTOBUF_BUILD="$(lock_build vaso_protobuf)"
SPACK_QHULL_BUILD="$(lock_build vaso_qhull)"
SPACK_SWIG_BUILD="$(lock_build vaso_swig)"
SPACK_PMIX_BUILD="$(lock_build vaso_pmix)"
SPACK_PRRTE_BUILD="$(lock_build vaso_prrte)"
SPACK_OPENMPI_BUILD="$(lock_build vaso_openmpi)"

echo "== phase 2a: native graph self-checks (inside insula) =="
bazel_insula test //tools:spack_to_bazel_unit_test --test_output=all
bazel_insula test //tools:build_graph_unit_test --test_output=all
bazel_insula test //tools:native_build_mechanism_guard_unit_test --test_output=all
bazel_insula test //tools:hermetic_native_deps_guard_test --test_output=all
bazel_insula test //tools:pytorch_python_protobuf_compat_test --test_output=all
bazel_insula test //native/pytorch:plan_test --test_output=all
if [[ -n "${VASO_EXTRA_TEST_TARGETS:-}" ]]; then
  echo "== phase 2a-extra: requested Bazel test targets (inside insula) =="
  EXTRA_TEST_ENV=(
    --test_env=VASO_IN_INSULA=1
    --test_env=VASO_ROOTFS_BUNDLE_MANIFEST="$ROOTFS_BUNDLE_MANIFEST_SB"
  )
  for var in ${!SPACK_@}; do
    case "$var" in
      SPACK_*_PREFIX|SPACK_*_BUILD)
        [[ -n "${!var}" ]] && EXTRA_TEST_ENV+=("--test_env=$var=${!var}")
        ;;
    esac
  done
  for target in ${VASO_EXTRA_TEST_TARGETS//,/ }; do
    [[ -n "$target" ]] || continue
    bazel_insula test "$target" --test_output=all --announce_rc "${EXTRA_TEST_ENV[@]}"
  done
fi

if [[ "${VASO_SKIP_CONSUMER_TESTS:-0}" == "1" ]]; then
  echo "== phase 2b: skipping default synthetic consumer sweep (VASO_SKIP_CONSUMER_TESTS=1) =="
else
  echo "== phase 2b: bazel test consuming Spack prefixes (inside insula) =="
  bazel_insula test //synthetic:use_zlib --test_output=all --announce_rc
  if [[ -n "$SPACK_XZ_PREFIX" ]]; then
    bazel_insula test //synthetic:use_xz --test_output=all --announce_rc
  fi
  if [[ -n "$SPACK_LIBXML2_PREFIX" ]]; then
    bazel_insula test //synthetic:use_libxml2 --test_output=all --announce_rc
  fi
  if [[ -n "$SPACK_LIBICONV_PREFIX" ]]; then
    bazel_insula test //synthetic:use_libiconv --test_output=all --announce_rc
  fi
  if [[ -n "$SPACK_PKGCONF_PREFIX" ]]; then
    bazel_insula test //synthetic:use_pkgconf --test_output=all --announce_rc
  fi
  if [[ -n "$SPACK_BERKELEY_DB_PREFIX" ]]; then
    bazel_insula test //synthetic:use_berkeley_db --test_output=all --announce_rc
  fi
  if [[ -n "$SPACK_LIBFFI_PREFIX" ]]; then
    bazel_insula test //synthetic:use_libffi --test_output=all --announce_rc
  fi
  if [[ -n "$SPACK_DIFFUTILS_PREFIX" && "$SPACK_DIFFUTILS_BUILD" == "native" ]]; then
    bazel_insula test //synthetic:use_diffutils --test_output=all --announce_rc
  fi
  if [[ -n "$SPACK_BZIP2_PREFIX" ]]; then
    bazel_insula test //synthetic:use_bzip2 --test_output=all --announce_rc
  fi
  if [[ -n "$SPACK_LIBMD_PREFIX" ]]; then
    bazel_insula test //synthetic:use_libmd --test_output=all --announce_rc
  fi
  if [[ -n "$SPACK_LIBBSD_PREFIX" ]]; then
    bazel_insula test //synthetic:use_libbsd --test_output=all --announce_rc
  fi
  if [[ -n "$SPACK_EXPAT_PREFIX" ]]; then
    bazel_insula test //synthetic:use_expat --test_output=all --announce_rc
  fi
  if [[ -n "$SPACK_GPERF_PREFIX" && "$SPACK_GPERF_BUILD" == "native" ]]; then
    bazel_insula test //synthetic:use_gperf_native --test_output=all --announce_rc
  fi
  if [[ -n "$SPACK_GMAKE_PREFIX" && "$SPACK_GMAKE_BUILD" == "native" ]]; then
    bazel_insula test //synthetic:use_gmake_native --test_output=all --announce_rc
  fi
  if [[ -n "$SPACK_XXD_STANDALONE_PREFIX" && "$SPACK_XXD_STANDALONE_BUILD" == "native" ]]; then
    bazel_insula test //synthetic:use_xxd_standalone_native --test_output=all --announce_rc
  fi
  if [[ -n "$SPACK_RE2C_PREFIX" && "$SPACK_RE2C_BUILD" == "native" ]]; then
    bazel_insula test //synthetic:use_re2c_native --test_output=all --announce_rc
  fi
  if [[ -n "$SPACK_NINJA_PREFIX" && "$SPACK_NINJA_BUILD" == "native" ]]; then
    bazel_insula test //synthetic:use_ninja_native --test_output=all --announce_rc
  fi
  if [[ -n "$SPACK_CPUINFO_PREFIX" && "$SPACK_CPUINFO_BUILD" == "native" ]]; then
    bazel_insula test //synthetic:use_cpuinfo_native --test_output=all --announce_rc
  fi
  if [[ -n "$SPACK_FP16_PREFIX" && "$SPACK_FP16_BUILD" == "native" ]]; then
    bazel_insula test //synthetic:use_fp16_native --test_output=all --announce_rc
  fi
  if [[ -n "$SPACK_FXDIV_PREFIX" && "$SPACK_FXDIV_BUILD" == "native" ]]; then
    bazel_insula test //synthetic:use_fxdiv_native --test_output=all --announce_rc
  fi
  if [[ -n "$SPACK_PSIMD_PREFIX" && "$SPACK_PSIMD_BUILD" == "native" ]]; then
    bazel_insula test //synthetic:use_psimd_native --test_output=all --announce_rc
  fi
  if [[ -n "$SPACK_PTHREADPOOL_PREFIX" && "$SPACK_PTHREADPOOL_BUILD" == "native" ]]; then
    bazel_insula test //synthetic:use_pthreadpool_native --test_output=all --announce_rc
  fi
  if [[ -n "$SPACK_SLEEF_PREFIX" && "$SPACK_SLEEF_BUILD" == "native" ]]; then
    bazel_insula test //synthetic:use_sleef_native --test_output=all --announce_rc
  fi
  if [[ -n "$SPACK_GZIP_PREFIX" && "$SPACK_GZIP_BUILD" == "native" ]]; then
    bazel_insula test //synthetic:use_gzip_native --test_output=all --announce_rc
  fi
  if [[ -n "$SPACK_LIBSIGSEGV_PREFIX" && "$SPACK_LIBSIGSEGV_BUILD" == "native" ]]; then
    bazel_insula test //synthetic:use_libsigsegv_native --test_output=all --announce_rc
  fi
  if [[ -n "$SPACK_LIBUNISTRING_PREFIX" && "$SPACK_LIBUNISTRING_BUILD" == "native" ]]; then
    bazel_insula test //synthetic:use_libunistring_native --test_output=all --announce_rc
  fi
  if [[ -n "$SPACK_LIBIDN2_PREFIX" && "$SPACK_LIBIDN2_BUILD" == "native" ]]; then
    bazel_insula test //synthetic:use_libidn2_native --test_output=all --announce_rc
  fi
  if [[ -n "$SPACK_LIBYAML_PREFIX" && "$SPACK_LIBYAML_BUILD" == "native" ]]; then
    bazel_insula test //synthetic:use_libyaml_native --test_output=all --announce_rc
  fi
  if [[ -n "$SPACK_LZO_PREFIX" && "$SPACK_LZO_BUILD" == "native" ]]; then
    bazel_insula test //synthetic:use_lzo_native --test_output=all --announce_rc
  fi
  if [[ -n "$SPACK_M4_PREFIX" && "$SPACK_M4_BUILD" == "native" ]]; then
    bazel_insula test //synthetic:use_m4_native --test_output=all --announce_rc
  fi
  if [[ -n "$SPACK_BISON_PREFIX" && "$SPACK_BISON_BUILD" == "native" ]]; then
    bazel_insula test //synthetic:use_bison_native --test_output=all --announce_rc
  fi
  if [[ -n "$SPACK_FLEX_PREFIX" && "$SPACK_FLEX_BUILD" == "native" ]]; then
    bazel_insula test //synthetic:use_flex_native --test_output=all --announce_rc
  fi
  if [[ -n "$SPACK_KRB5_PREFIX" && "$SPACK_KRB5_BUILD" == "native" ]]; then
    bazel_insula test //synthetic:use_krb5_native --test_output=all --announce_rc
  fi
  if [[ -n "$SPACK_LIBTOOL_PREFIX" && "$SPACK_LIBTOOL_BUILD" == "native" ]]; then
    bazel_insula test //synthetic:use_libtool_native --test_output=all --announce_rc
  fi
  if [[ -n "$SPACK_FRIBIDI_PREFIX" && "$SPACK_FRIBIDI_BUILD" == "native" ]]; then
    bazel_insula test //synthetic:use_fribidi_native --test_output=all --announce_rc
  fi
  if [[ -n "$SPACK_NUMACTL_PREFIX" && "$SPACK_NUMACTL_BUILD" == "native" ]]; then
    bazel_insula test //synthetic:use_numactl_native --test_output=all --announce_rc
  fi
  if [[ -n "$SPACK_OPENSSH_PREFIX" && "$SPACK_OPENSSH_BUILD" == "native" ]]; then
    bazel_insula test //synthetic:use_openssh_native --test_output=all --announce_rc
  fi
  if [[ -n "$SPACK_GIT_PREFIX" && "$SPACK_GIT_BUILD" == "native" ]]; then
    bazel_insula test //synthetic:use_git_native --test_output=all --announce_rc
  fi
  if [[ -n "$SPACK_NASM_PREFIX" && "$SPACK_NASM_BUILD" == "native" ]]; then
    bazel_insula test //synthetic:use_nasm_native --test_output=all --announce_rc
  fi
  if [[ -n "$SPACK_OPENBLAS_PREFIX" && "$SPACK_OPENBLAS_BUILD" == "native" ]]; then
    bazel_insula test //synthetic:use_openblas_native --test_output=all --announce_rc
  fi
  if [[ -n "$SPACK_PCRE2_PREFIX" && "$SPACK_PCRE2_BUILD" == "native" ]]; then
    bazel_insula test //synthetic:use_pcre2_native --test_output=all --announce_rc
  fi
  if [[ -n "$SPACK_LIBXCRYPT_PREFIX" && "$SPACK_LIBXCRYPT_BUILD" == "native" ]]; then
    bazel_insula test //synthetic:use_libxcrypt_native --test_output=all --announce_rc
  fi
  if [[ -n "$SPACK_NCURSES_PREFIX" ]]; then
    bazel_insula test //synthetic:use_ncurses --test_output=all --announce_rc
  fi
  if [[ -n "$SPACK_LESS_PREFIX" && "$SPACK_LESS_BUILD" == "native" ]]; then
    bazel_insula test //synthetic:use_less --test_output=all --announce_rc
  fi
  if [[ -n "$SPACK_LIBEDIT_PREFIX" ]]; then
    bazel_insula test //synthetic:use_libedit --test_output=all --announce_rc
  fi
  if [[ -n "$SPACK_NGHTTP2_PREFIX" ]]; then
    bazel_insula test //synthetic:use_nghttp2 --test_output=all --announce_rc
  fi
  if [[ -n "$SPACK_LIBEVENT_PREFIX" ]]; then
    bazel_insula test //synthetic:use_libevent --test_output=all --announce_rc
  fi
  if [[ -n "$SPACK_READLINE_PREFIX" ]]; then
    bazel_insula test //synthetic:use_readline --test_output=all --announce_rc
  fi
  if [[ -n "$SPACK_GDBM_PREFIX" ]]; then
    bazel_insula test //synthetic:use_gdbm --test_output=all --announce_rc
  fi
  if [[ -n "$SPACK_UNZIP_PREFIX" && "$SPACK_UNZIP_BUILD" == "native" ]]; then
    bazel_insula test //synthetic:use_unzip_native --test_output=all --announce_rc
  fi
  if [[ -n "$SPACK_UTIL_LINUX_UUID_PREFIX" ]]; then
    bazel_insula test //synthetic:use_util_linux_uuid --test_output=all --announce_rc
  fi
  if [[ -n "$SPACK_UTIL_MACROS_PREFIX" && "$SPACK_UTIL_MACROS_BUILD" == "native" ]]; then
    bazel_insula test //synthetic:use_util_macros_native --test_output=all --announce_rc
  fi
  if [[ -n "$SPACK_FONTSPROTO_PREFIX" && "$SPACK_FONTSPROTO_BUILD" == "native" ]]; then
    bazel_insula test //synthetic:use_fontsproto_native --test_output=all --announce_rc
  fi
  if [[ -n "$SPACK_LIBPCIACCESS_PREFIX" && "$SPACK_LIBPCIACCESS_BUILD" == "native" ]]; then
    bazel_insula test //synthetic:use_libpciaccess_native --test_output=all --announce_rc
  fi
  if [[ -n "$SPACK_XPROTO_PREFIX" && "$SPACK_XPROTO_BUILD" == "native" ]]; then
    bazel_insula test //synthetic:use_xproto_native --test_output=all --announce_rc
  fi
  if [[ -n "$SPACK_XTRANS_PREFIX" && "$SPACK_XTRANS_BUILD" == "native" ]]; then
    bazel_insula test //synthetic:use_xtrans_native --test_output=all --announce_rc
  fi
  if [[ -n "$SPACK_LIBFONTENC_PREFIX" && "$SPACK_LIBFONTENC_BUILD" == "native" ]]; then
    bazel_insula test //synthetic:use_libfontenc_native --test_output=all --announce_rc
  fi
  if [[ -n "$SPACK_LIBXFONT_PREFIX" && "$SPACK_LIBXFONT_BUILD" == "native" ]]; then
    bazel_insula test //synthetic:use_libxfont_native --test_output=all --announce_rc
  fi
  if [[ -n "$SPACK_BDFTOPCF_PREFIX" && "$SPACK_BDFTOPCF_BUILD" == "native" ]]; then
    bazel_insula test //synthetic:use_bdftopcf_native --test_output=all --announce_rc
  fi
  if [[ -n "$SPACK_LUA_PREFIX" && "$SPACK_LUA_BUILD" == "native" ]]; then
    bazel_insula test //synthetic:use_lua_native --test_output=all --announce_rc
    bazel_insula test //synthetic:use_lua_prefix_native --test_output=all --announce_rc
  fi
  if [[ -n "$SPACK_MKFONTSCALE_PREFIX" && "$SPACK_MKFONTSCALE_BUILD" == "native" ]]; then
    bazel_insula test //synthetic:use_mkfontscale_native --test_output=all --announce_rc
  fi
  if [[ -n "$SPACK_MKFONTDIR_PREFIX" && "$SPACK_MKFONTDIR_BUILD" == "native" ]]; then
    bazel_insula test //synthetic:use_mkfontdir_native --test_output=all --announce_rc
  fi
  if [[ -n "$SPACK_FONT_UTIL_PREFIX" && "$SPACK_FONT_UTIL_BUILD" == "native" ]]; then
    bazel_insula test //synthetic:use_font_util_native --test_output=all --announce_rc
  fi
  if [[ -n "$SPACK_FONTCONFIG_PREFIX" && "$SPACK_FONTCONFIG_BUILD" == "native" ]]; then
    bazel_insula test //synthetic:use_fontconfig_native --test_output=all --announce_rc
  fi
  if [[ -n "$SPACK_ICU4C_PREFIX" && "$SPACK_ICU4C_BUILD" == "native" ]]; then
    bazel_insula test //synthetic:use_icu4c_native --test_output=all --announce_rc
  fi
  if [[ -n "$SPACK_PERL_DATA_DUMPER_PREFIX" && "$SPACK_PERL_DATA_DUMPER_BUILD" == "native" ]]; then
    bazel_insula test //synthetic:use_perl_data_dumper_native --test_output=all --announce_rc
  fi
  if [[ -n "$SPACK_HWLOC_PREFIX" && "$SPACK_HWLOC_BUILD" == "native" ]]; then
    bazel_insula test //synthetic:use_hwloc_native --test_output=all --announce_rc
  fi
  if [[ -n "$SPACK_AUTOCONF_PREFIX" && "$SPACK_AUTOCONF_BUILD" == "native" ]]; then
    bazel_insula test //synthetic:use_autoconf_native --test_output=all --announce_rc
  fi
  if [[ -n "$SPACK_OPENSSL_PREFIX" ]]; then
    bazel_insula test //synthetic:use_openssl --test_output=all --announce_rc
  fi
  if [[ -n "$SPACK_COREUTILS_PREFIX" && "$SPACK_COREUTILS_BUILD" == "native" ]]; then
    bazel_insula test //synthetic:use_coreutils_native --test_output=all --announce_rc
  fi
  if [[ -n "$SPACK_CUDA_PREFIX" && "$SPACK_CUDA_BUILD" == "native" ]]; then
    bazel_insula test //synthetic:use_cuda_boundary --test_output=all --announce_rc
  fi
  if [[ -n "$SPACK_CUDNN_PREFIX" && "$SPACK_CUDNN_BUILD" == "native" ]]; then
    bazel_insula test //synthetic:use_cudnn_native --test_output=all --announce_rc
  fi
  if [[ -n "$SPACK_CUSPARSELT_PREFIX" && "$SPACK_CUSPARSELT_BUILD" == "native" ]]; then
    bazel_insula test //synthetic:use_cusparselt_native --test_output=all --announce_rc
  fi
  if [[ -n "$SPACK_CUDSS_PREFIX" && "$SPACK_CUDSS_BUILD" == "native" ]]; then
    bazel_insula test //synthetic:use_cudss_native --test_output=all --announce_rc
  fi
  if [[ -n "$SPACK_NCCL_PREFIX" ]]; then
    bazel_insula test //synthetic:use_nccl --test_output=all --announce_rc
  fi
  if [[ -n "$SPACK_MAGMA_PREFIX" && "$SPACK_MAGMA_BUILD" == "native" ]]; then
    bazel_insula test //synthetic:use_magma_native --test_output=all --announce_rc
  fi
  if [[ -n "$SPACK_NVSHMEM_PREFIX" && "$SPACK_NVSHMEM_BUILD" == "native" ]]; then
    bazel_insula test //synthetic:use_nvshmem_native --test_output=all --announce_rc
  fi
  if [[ -n "$SPACK_CURL_PREFIX" && "$SPACK_CURL_BUILD" == "native" ]]; then
    bazel_insula test //synthetic:use_curl_native --test_output=all --announce_rc
  fi
  if [[ -n "$SPACK_CMAKE_PREFIX" && "$SPACK_CMAKE_BUILD" == "native" ]]; then
    bazel_insula test //synthetic:use_cmake_native --test_output=all --announce_rc
  fi
  if [[ -n "$SPACK_LIBEVENT_PREFIX" && "$SPACK_LIBEVENT_BUILD" == "native" ]]; then
    bazel_insula test //synthetic:use_libevent_native --test_output=all --announce_rc
  fi
  if [[ -n "$SPACK_LIBJPEG_TURBO_PREFIX" && "$SPACK_LIBJPEG_TURBO_BUILD" == "native" ]]; then
    bazel_insula test //synthetic:use_libjpeg_turbo_native --test_output=all --announce_rc
  fi
  if [[ -n "$SPACK_LIBPNG_PREFIX" && "$SPACK_LIBPNG_BUILD" == "native" ]]; then
    bazel_insula test //synthetic:use_libpng_native --test_output=all --announce_rc
  fi
  if [[ -n "$SPACK_PIXMAN_PREFIX" && "$SPACK_PIXMAN_BUILD" == "native" ]]; then
    bazel_insula test //synthetic:use_pixman_native --test_output=all --announce_rc
  fi
  if [[ -n "$SPACK_FREETYPE_PREFIX" && "$SPACK_FREETYPE_BUILD" == "native" ]]; then
    bazel_insula test //synthetic:use_freetype_native --test_output=all --announce_rc
  fi
  if [[ -n "$SPACK_CAIRO_PREFIX" && "$SPACK_CAIRO_BUILD" == "native" ]]; then
    bazel_insula test //synthetic:use_cairo_native --test_output=all --announce_rc
  fi
  if [[ -n "$SPACK_HARFBUZZ_PREFIX" && "$SPACK_HARFBUZZ_BUILD" == "native" ]]; then
    bazel_insula test //synthetic:use_harfbuzz_native --test_output=all --announce_rc
  fi
  if [[ -n "$SPACK_LIBRAQM_PREFIX" && "$SPACK_LIBRAQM_BUILD" == "native" ]]; then
    bazel_insula test //synthetic:use_libraqm_native --test_output=all --announce_rc
  fi
  if [[ -n "$SPACK_PIGZ_PREFIX" && "$SPACK_PIGZ_BUILD" == "native" ]]; then
    bazel_insula test //synthetic:use_pigz --test_output=all --announce_rc
  fi
  if [[ -n "$SPACK_SQLITE_PREFIX" ]]; then
    bazel_insula test //synthetic:use_sqlite --test_output=all --announce_rc
  fi
  if [[ -n "$SPACK_TAR_PREFIX" && "$SPACK_TAR_BUILD" == "native" ]]; then
    bazel_insula test //synthetic:use_tar --test_output=all --announce_rc
  fi
  if [[ -n "$SPACK_GETTEXT_PREFIX" ]]; then
    bazel_insula test //synthetic:use_gettext --test_output=all --announce_rc
  fi
  if [[ -n "$SPACK_GLIB_BOOTSTRAP_PREFIX" && "$SPACK_GLIB_BOOTSTRAP_BUILD" == "native" ]]; then
    bazel_insula test //synthetic:use_glib_bootstrap_native --test_output=all --announce_rc
  fi
  if [[ -n "$SPACK_GOBJECT_INTROSPECTION_PREFIX" && "$SPACK_GOBJECT_INTROSPECTION_BUILD" == "native" ]]; then
    bazel_insula test //synthetic:use_gobject_introspection_native --test_output=all --announce_rc
  fi
  if [[ -n "$SPACK_GLIB_PREFIX" && "$SPACK_GLIB_BUILD" == "native" ]]; then
    bazel_insula test //synthetic:use_glib_native --test_output=all --announce_rc
  fi
  if [[ -n "$SPACK_ELFUTILS_PREFIX" ]]; then
    bazel_insula test //synthetic:use_elfutils --test_output=all --announce_rc
  fi
  if [[ -n "$SPACK_PYTHON_PREFIX" ]]; then
    bazel_insula test //synthetic:use_python --test_output=all --announce_rc
  fi
  if [[ -n "$SPACK_PROTOBUF_PREFIX" && "$SPACK_PROTOBUF_BUILD" == "native" ]]; then
    bazel_insula test //synthetic:use_protobuf_native --test_output=all --announce_rc
    bazel_insula test //synthetic:use_protobuf_prefix_native --test_output=all --announce_rc
  fi
  if [[ -n "$SPACK_QHULL_PREFIX" && "$SPACK_QHULL_BUILD" == "native" ]]; then
    bazel_insula test //synthetic:use_qhull_native --test_output=all --announce_rc
    bazel_insula test //synthetic:use_qhull_prefix_native --test_output=all --announce_rc
  fi
  if [[ -n "$SPACK_SWIG_PREFIX" && "$SPACK_SWIG_BUILD" == "native" ]]; then
    bazel_insula test //synthetic:use_swig_native --test_output=all --announce_rc
  fi
  if [[ -n "$SPACK_BINUTILS_PREFIX" && "$SPACK_BINUTILS_BUILD" == "native" ]]; then
    bazel_insula test //synthetic:use_binutils_native --test_output=all --announce_rc
  fi
  if [[ -n "$SPACK_FILE_PREFIX" && "$SPACK_FILE_BUILD" == "native" ]]; then
    bazel_insula test //synthetic:use_file_native --test_output=all --announce_rc
  fi
  if [[ -n "$SPACK_PMIX_PREFIX" ]]; then
    bazel_insula test //synthetic:use_pmix --test_output=all --announce_rc
  fi
  if [[ -n "$SPACK_PMIX_PREFIX" && "$SPACK_PMIX_BUILD" == "native" ]]; then
    bazel_insula test //synthetic:use_pmix_native --test_output=all --announce_rc
  fi
  if [[ -n "$SPACK_PRRTE_PREFIX" ]]; then
    bazel_insula test //synthetic:use_prrte --test_output=all --announce_rc
  fi
  if [[ -n "$SPACK_PRRTE_PREFIX" && "$SPACK_PRRTE_BUILD" == "native" ]]; then
    bazel_insula test //synthetic:use_prrte_native --test_output=all --announce_rc
  fi
  if [[ -n "$SPACK_OPENMPI_PREFIX" ]]; then
    bazel_insula test //synthetic:use_openmpi --test_output=all --announce_rc
  fi
  if [[ -n "$SPACK_OPENMPI_PREFIX" && "$SPACK_OPENMPI_BUILD" == "native" ]]; then
    bazel_insula test //synthetic:use_openmpi_native --test_output=all --announce_rc
  fi
fi

if [[ "${VASO_SKIP_NATIVE_ABI_GATES:-0}" == "1" ]]; then
  echo "== phase 2c+: skipping default native ABI ladder (VASO_SKIP_NATIVE_ABI_GATES=1) =="
  echo "== done (canonical lock: $CANONICAL_LOCK_SB; side lock capture: $VASO_LOCK_OUT; estate: $ESTATE_ROOT) =="
  exit 0
fi

if [[ "${VASO_NATIVE:-0}" == "1" ]]; then
  if [[ -n "$SPACK_ZLIB_NG_PREFIX" && "$SPACK_ZLIB_NG_BUILD" == "native" ]]; then
    echo "== phase 2c: native zlib-ng ABI-parity gate vs Spack prefix (inside insula) =="
    bazel_insula test //synthetic:zlib_ng_abi_parity \
      --test_output=all --announce_rc \
      --test_env=SPACK_ZLIB_NG_PREFIX="$SPACK_ZLIB_NG_PREFIX"
  elif [[ -n "$SPACK_ZLIB_NG_PREFIX" ]]; then
    echo "== phase 2c: skipping zlib-ng native ABI gate (provider remains $SPACK_ZLIB_NG_BUILD) =="
  fi

  if [[ -n "$SPACK_BISON_PREFIX" && "$SPACK_BISON_BUILD" == "native" ]]; then
    echo "== phase 2c-bison: native bison prefix/behavior parity gate vs Spack prefix (inside insula) =="
    bazel_insula test //synthetic:bison_prefix_parity \
      --test_output=all --announce_rc \
      --test_env=SPACK_BISON_PREFIX="$SPACK_BISON_PREFIX" \
      --test_env=SPACK_M4_PREFIX="$SPACK_M4_PREFIX"
  elif [[ -n "$SPACK_BISON_PREFIX" ]]; then
    echo "== phase 2c-bison: skipping bison native parity gate (provider remains $SPACK_BISON_BUILD) =="
  fi

  if [[ -n "$SPACK_FLEX_PREFIX" && "$SPACK_FLEX_BUILD" == "native" ]]; then
    echo "== phase 2c-flex: native flex prefix/behavior parity gate vs Spack prefix (inside insula) =="
    bazel_insula test //synthetic:flex_prefix_parity \
      --test_output=all --announce_rc \
      --test_env=SPACK_FLEX_PREFIX="$SPACK_FLEX_PREFIX"
  elif [[ -n "$SPACK_FLEX_PREFIX" ]]; then
    echo "== phase 2c-flex: skipping flex native parity gate (provider remains $SPACK_FLEX_BUILD) =="
  fi

  if [[ -n "$SPACK_KRB5_PREFIX" && "$SPACK_KRB5_BUILD" == "native" ]]; then
    echo "== phase 2c-krb5: native krb5 ABI/behavior parity gate vs Spack prefix (inside insula) =="
    bazel_insula test //synthetic:krb5_abi_parity \
      --test_output=all --announce_rc \
      --test_env=SPACK_KRB5_PREFIX="$SPACK_KRB5_PREFIX" \
      --test_env=SPACK_GETTEXT_PREFIX="$SPACK_GETTEXT_PREFIX" \
      --test_env=SPACK_LIBEDIT_PREFIX="$SPACK_LIBEDIT_PREFIX" \
      --test_env=SPACK_NCURSES_PREFIX="$SPACK_NCURSES_PREFIX" \
      --test_env=SPACK_OPENSSL_PREFIX="$SPACK_OPENSSL_PREFIX"
  elif [[ -n "$SPACK_KRB5_PREFIX" ]]; then
    echo "== phase 2c-krb5: skipping krb5 native ABI gate (provider remains $SPACK_KRB5_BUILD) =="
  fi

  if [[ -n "$SPACK_LIBTOOL_PREFIX" && "$SPACK_LIBTOOL_BUILD" == "native" ]]; then
    echo "== phase 2c-libtool: native libtool ABI/behavior parity gate vs Spack prefix (inside insula) =="
    bazel_insula test //synthetic:libtool_abi_parity \
      --test_output=all --announce_rc \
      --test_env=SPACK_LIBTOOL_PREFIX="$SPACK_LIBTOOL_PREFIX" \
      --test_env=SPACK_FILE_PREFIX="$SPACK_FILE_PREFIX" \
      --test_env=SPACK_FINDUTILS_PREFIX="$SPACK_FINDUTILS_PREFIX" \
      --test_env=SPACK_M4_PREFIX="$SPACK_M4_PREFIX"
  elif [[ -n "$SPACK_LIBTOOL_PREFIX" ]]; then
    echo "== phase 2c-libtool: skipping libtool native ABI gate (provider remains $SPACK_LIBTOOL_BUILD) =="
  fi

  if [[ -n "$SPACK_FRIBIDI_PREFIX" && "$SPACK_FRIBIDI_BUILD" == "native" ]]; then
    echo "== phase 2c-fribidi: native fribidi ABI/behavior parity gate vs Spack prefix (inside insula) =="
    bazel_insula test //synthetic:fribidi_abi_parity \
      --test_output=all --announce_rc \
      --test_env=SPACK_FRIBIDI_PREFIX="$SPACK_FRIBIDI_PREFIX" \
      --test_env=SPACK_AUTOCONF_PREFIX="$SPACK_AUTOCONF_PREFIX" \
      --test_env=SPACK_AUTOMAKE_PREFIX="$SPACK_AUTOMAKE_PREFIX" \
      --test_env=SPACK_LIBTOOL_PREFIX="$SPACK_LIBTOOL_PREFIX" \
      --test_env=SPACK_M4_PREFIX="$SPACK_M4_PREFIX"
  elif [[ -n "$SPACK_FRIBIDI_PREFIX" ]]; then
    echo "== phase 2c-fribidi: skipping fribidi native ABI gate (provider remains $SPACK_FRIBIDI_BUILD) =="
  fi

  if [[ -n "$SPACK_NUMACTL_PREFIX" && "$SPACK_NUMACTL_BUILD" == "native" ]]; then
    echo "== phase 2c-numactl: native numactl ABI/behavior parity gate vs Spack prefix (inside insula) =="
    bazel_insula test //synthetic:numactl_abi_parity \
      --test_output=all --announce_rc \
      --test_env=SPACK_NUMACTL_PREFIX="$SPACK_NUMACTL_PREFIX" \
      --test_env=SPACK_AUTOCONF_PREFIX="$SPACK_AUTOCONF_PREFIX" \
      --test_env=SPACK_AUTOMAKE_PREFIX="$SPACK_AUTOMAKE_PREFIX" \
      --test_env=SPACK_LIBTOOL_PREFIX="$SPACK_LIBTOOL_PREFIX" \
      --test_env=SPACK_M4_PREFIX="$SPACK_M4_PREFIX"
  elif [[ -n "$SPACK_NUMACTL_PREFIX" ]]; then
    echo "== phase 2c-numactl: skipping numactl native ABI gate (provider remains $SPACK_NUMACTL_BUILD) =="
  fi

  if [[ -n "$SPACK_OPENSSH_PREFIX" && "$SPACK_OPENSSH_BUILD" == "native" ]]; then
    echo "== phase 2c-openssh: native OpenSSH prefix/behavior parity gate vs Spack prefix (inside insula) =="
    bazel_insula test //synthetic:openssh_prefix_parity \
      --test_output=all --announce_rc \
      --test_env=SPACK_OPENSSH_PREFIX="$SPACK_OPENSSH_PREFIX" \
      --test_env=SPACK_KRB5_PREFIX="$SPACK_KRB5_PREFIX" \
      --test_env=SPACK_LIBEDIT_PREFIX="$SPACK_LIBEDIT_PREFIX" \
      --test_env=SPACK_LIBXCRYPT_PREFIX="$SPACK_LIBXCRYPT_PREFIX" \
      --test_env=SPACK_NCURSES_PREFIX="$SPACK_NCURSES_PREFIX" \
      --test_env=SPACK_OPENSSL_PREFIX="$SPACK_OPENSSL_PREFIX" \
      --test_env=SPACK_ZLIB_NG_PREFIX="$SPACK_ZLIB_NG_PREFIX"
  elif [[ -n "$SPACK_OPENSSH_PREFIX" ]]; then
    echo "== phase 2c-openssh: skipping OpenSSH native parity gate (provider remains $SPACK_OPENSSH_BUILD) =="
  fi

  if [[ -n "$SPACK_GIT_PREFIX" && "$SPACK_GIT_BUILD" == "native" ]]; then
    echo "== phase 2c-git: native Git prefix/behavior parity gate vs Spack prefix (inside insula) =="
    bazel_insula test //synthetic:git_prefix_parity \
      --test_output=all --announce_rc \
      --test_env=SPACK_GIT_PREFIX="$SPACK_GIT_PREFIX" \
      --test_env=SPACK_AUTOCONF_PREFIX="$SPACK_AUTOCONF_PREFIX" \
      --test_env=SPACK_AUTOMAKE_PREFIX="$SPACK_AUTOMAKE_PREFIX" \
      --test_env=SPACK_CURL_PREFIX="$SPACK_CURL_PREFIX" \
      --test_env=SPACK_DIFFUTILS_PREFIX="$SPACK_DIFFUTILS_PREFIX" \
      --test_env=SPACK_EXPAT_PREFIX="$SPACK_EXPAT_PREFIX" \
      --test_env=SPACK_GETTEXT_PREFIX="$SPACK_GETTEXT_PREFIX" \
      --test_env=SPACK_LIBICONV_PREFIX="$SPACK_LIBICONV_PREFIX" \
      --test_env=SPACK_LIBIDN2_PREFIX="$SPACK_LIBIDN2_PREFIX" \
      --test_env=SPACK_LIBTOOL_PREFIX="$SPACK_LIBTOOL_PREFIX" \
      --test_env=SPACK_M4_PREFIX="$SPACK_M4_PREFIX" \
      --test_env=SPACK_OPENSSH_PREFIX="$SPACK_OPENSSH_PREFIX" \
      --test_env=SPACK_OPENSSL_PREFIX="$SPACK_OPENSSL_PREFIX" \
      --test_env=SPACK_PCRE2_PREFIX="$SPACK_PCRE2_PREFIX" \
      --test_env=SPACK_PERL_PREFIX="$SPACK_PERL_PREFIX" \
      --test_env=SPACK_ZLIB_NG_PREFIX="$SPACK_ZLIB_NG_PREFIX"
  elif [[ -n "$SPACK_GIT_PREFIX" ]]; then
    echo "== phase 2c-git: skipping Git native parity gate (provider remains $SPACK_GIT_BUILD) =="
  fi

  if [[ -n "$SPACK_NASM_PREFIX" && "$SPACK_NASM_BUILD" == "native" ]]; then
    echo "== phase 2c-nasm: native nasm prefix/behavior parity gate vs Spack prefix (inside insula) =="
    bazel_insula test //synthetic:nasm_prefix_parity \
      --test_output=all --announce_rc \
      --test_env=SPACK_NASM_PREFIX="$SPACK_NASM_PREFIX"
  elif [[ -n "$SPACK_NASM_PREFIX" ]]; then
    echo "== phase 2c-nasm: skipping nasm native parity gate (provider remains $SPACK_NASM_BUILD) =="
  fi

  if [[ -n "$SPACK_OPENBLAS_PREFIX" && "$SPACK_OPENBLAS_BUILD" == "native" ]]; then
    echo "== phase 2c-openblas: native OpenBLAS ABI-parity gate vs Spack prefix (inside insula) =="
    bazel_insula test //synthetic:openblas_abi_parity \
      --test_output=all --announce_rc \
      --test_env=SPACK_OPENBLAS_PREFIX="$SPACK_OPENBLAS_PREFIX"
  elif [[ -n "$SPACK_OPENBLAS_PREFIX" ]]; then
    echo "== phase 2c-openblas: skipping OpenBLAS native ABI gate (provider remains $SPACK_OPENBLAS_BUILD) =="
  fi

  if [[ -n "$SPACK_PCRE2_PREFIX" && "$SPACK_PCRE2_BUILD" == "native" ]]; then
    echo "== phase 2c-pcre2: native PCRE2 ABI-parity gate vs Spack prefix (inside insula) =="
    bazel_insula test //synthetic:pcre2_abi_parity \
      --test_output=all --announce_rc \
      --test_env=SPACK_PCRE2_PREFIX="$SPACK_PCRE2_PREFIX"
  elif [[ -n "$SPACK_PCRE2_PREFIX" ]]; then
    echo "== phase 2c-pcre2: skipping PCRE2 native ABI gate (provider remains $SPACK_PCRE2_BUILD) =="
  fi

  if [[ -n "$SPACK_LIBEDIT_PREFIX" && "$SPACK_LIBEDIT_BUILD" == "native" ]]; then
    echo "== phase 2c-libedit: native libedit ABI-parity gate vs Spack prefix (inside insula) =="
    bazel_insula test //synthetic:libedit_abi_parity \
      --test_output=all --announce_rc \
      --test_env=SPACK_LIBEDIT_PREFIX="$SPACK_LIBEDIT_PREFIX" \
      --test_env=SPACK_NCURSES_PREFIX="$SPACK_NCURSES_PREFIX"
  elif [[ -n "$SPACK_LIBEDIT_PREFIX" ]]; then
    echo "== phase 2c-libedit: skipping libedit native ABI gate (provider remains $SPACK_LIBEDIT_BUILD) =="
  fi

  if [[ -n "$SPACK_NGHTTP2_PREFIX" && "$SPACK_NGHTTP2_BUILD" == "native" ]]; then
    echo "== phase 2c-nghttp2: native nghttp2 ABI-parity gate vs Spack prefix (inside insula) =="
    bazel_insula test //synthetic:nghttp2_abi_parity \
      --test_output=all --announce_rc \
      --test_env=SPACK_NGHTTP2_PREFIX="$SPACK_NGHTTP2_PREFIX"
  elif [[ -n "$SPACK_NGHTTP2_PREFIX" ]]; then
    echo "== phase 2c-nghttp2: skipping nghttp2 native ABI gate (provider remains $SPACK_NGHTTP2_BUILD) =="
  fi

  if [[ -n "$SPACK_LIBEVENT_PREFIX" && "$SPACK_LIBEVENT_BUILD" == "native" ]]; then
    echo "== phase 2c-libevent: native libevent ABI-parity gate vs Spack prefix (inside insula) =="
    bazel_insula test //synthetic:libevent_abi_parity \
      --test_output=all --announce_rc \
      --test_env=SPACK_LIBEVENT_PREFIX="$SPACK_LIBEVENT_PREFIX" \
      --test_env=SPACK_OPENSSL_PREFIX="$SPACK_OPENSSL_PREFIX" \
      --test_env=SPACK_ZLIB_NG_PREFIX="$SPACK_ZLIB_NG_PREFIX"
  elif [[ -n "$SPACK_LIBEVENT_PREFIX" ]]; then
    echo "== phase 2c-libevent: skipping libevent native ABI gate (provider remains $SPACK_LIBEVENT_BUILD) =="
  fi

  if [[ -n "$SPACK_LIBJPEG_TURBO_PREFIX" && "$SPACK_LIBJPEG_TURBO_BUILD" == "native" ]]; then
    echo "== phase 2c-libjpeg-turbo: native libjpeg-turbo ABI-parity gate vs Spack prefix (inside insula) =="
    bazel_insula test //synthetic:libjpeg_turbo_abi_parity \
      --test_output=all --announce_rc \
      --test_env=SPACK_LIBJPEG_TURBO_PREFIX="$SPACK_LIBJPEG_TURBO_PREFIX"
  elif [[ -n "$SPACK_LIBJPEG_TURBO_PREFIX" ]]; then
    echo "== phase 2c-libjpeg-turbo: skipping libjpeg-turbo native ABI gate (provider remains $SPACK_LIBJPEG_TURBO_BUILD) =="
  fi

  if [[ -n "$SPACK_LIBPNG_PREFIX" && "$SPACK_LIBPNG_BUILD" == "native" ]]; then
    echo "== phase 2c-libpng: native libpng ABI-parity gate vs Spack prefix (inside insula) =="
    bazel_insula test //synthetic:libpng_abi_parity \
      --test_output=all --announce_rc \
      --test_env=SPACK_LIBPNG_PREFIX="$SPACK_LIBPNG_PREFIX" \
      --test_env=SPACK_ZLIB_NG_PREFIX="$SPACK_ZLIB_NG_PREFIX"
  elif [[ -n "$SPACK_LIBPNG_PREFIX" ]]; then
    echo "== phase 2c-libpng: skipping libpng native ABI gate (provider remains $SPACK_LIBPNG_BUILD) =="
  fi

  if [[ -n "$SPACK_PIXMAN_PREFIX" && "$SPACK_PIXMAN_BUILD" == "native" ]]; then
    echo "== phase 2c-pixman: native Pixman ABI-parity gate vs Spack prefix (inside insula) =="
    bazel_insula test //synthetic:pixman_abi_parity \
      --test_output=all --announce_rc \
      --test_env=SPACK_PIXMAN_PREFIX="$SPACK_PIXMAN_PREFIX" \
      --test_env=SPACK_LIBPNG_PREFIX="$SPACK_LIBPNG_PREFIX" \
      --test_env=SPACK_ZLIB_NG_PREFIX="$SPACK_ZLIB_NG_PREFIX"
  elif [[ -n "$SPACK_PIXMAN_PREFIX" ]]; then
    echo "== phase 2c-pixman: skipping Pixman native ABI gate (provider remains $SPACK_PIXMAN_BUILD) =="
  fi

  if [[ -n "$SPACK_FREETYPE_PREFIX" && "$SPACK_FREETYPE_BUILD" == "native" ]]; then
    echo "== phase 2c-freetype: native freetype ABI-parity gate vs Spack prefix (inside insula) =="
    bazel_insula test //synthetic:freetype_abi_parity \
      --test_output=all --announce_rc \
      --test_env=SPACK_FREETYPE_PREFIX="$SPACK_FREETYPE_PREFIX" \
      --test_env=SPACK_BZIP2_PREFIX="$SPACK_BZIP2_PREFIX" \
      --test_env=SPACK_LIBPNG_PREFIX="$SPACK_LIBPNG_PREFIX" \
      --test_env=SPACK_ZLIB_NG_PREFIX="$SPACK_ZLIB_NG_PREFIX"
  elif [[ -n "$SPACK_FREETYPE_PREFIX" ]]; then
    echo "== phase 2c-freetype: skipping freetype native ABI gate (provider remains $SPACK_FREETYPE_BUILD) =="
  fi

  if [[ -n "$SPACK_XZ_PREFIX" && "$SPACK_XZ_BUILD" == "native" ]]; then
    echo "== phase 2d: native xz ABI-parity gate vs Spack prefix (inside insula) =="
    bazel_insula test //synthetic:xz_abi_parity \
      --test_output=all --announce_rc \
      --test_env=SPACK_XZ_PREFIX="$SPACK_XZ_PREFIX"
  elif [[ -n "$SPACK_XZ_PREFIX" ]]; then
    echo "== phase 2d: skipping xz native ABI gate (provider remains $SPACK_XZ_BUILD) =="
  fi

  if [[ -n "$SPACK_LIBXML2_PREFIX" && "$SPACK_LIBXML2_BUILD" == "native" ]]; then
    echo "== phase 2e: native libxml2 ABI-parity gate vs Spack prefix (inside insula) =="
    libxml2_parity_target="//synthetic:libxml2_abi_parity"
    if [[ "$SPACK_LIBXML2_VERSION" == "2.15.3" ]]; then
      libxml2_parity_target="//synthetic:libxml2_215_abi_parity"
    fi
    bazel_insula test "$libxml2_parity_target" \
      --test_output=all --announce_rc \
      --test_env=SPACK_LIBXML2_PREFIX="$SPACK_LIBXML2_PREFIX"
  elif [[ -n "$SPACK_LIBXML2_PREFIX" ]]; then
    echo "== phase 2e: skipping libxml2 native ABI gate (provider remains $SPACK_LIBXML2_BUILD) =="
  fi

  if [[ -n "$SPACK_LIBICONV_PREFIX" && "$SPACK_LIBICONV_BUILD" == "native" ]]; then
    echo "== phase 2f: native libiconv ABI-parity gate vs Spack prefix (inside insula) =="
    bazel_insula test //synthetic:libiconv_abi_parity \
      --test_output=all --announce_rc \
      --test_env=SPACK_LIBICONV_PREFIX="$SPACK_LIBICONV_PREFIX"
  elif [[ -n "$SPACK_LIBICONV_PREFIX" ]]; then
    echo "== phase 2f: skipping libiconv native ABI gate (provider remains $SPACK_LIBICONV_BUILD) =="
  fi

  if [[ -n "$SPACK_PKGCONF_PREFIX" && "$SPACK_PKGCONF_BUILD" == "native" ]]; then
    echo "== phase 2g: native pkgconf ABI-parity gate vs Spack prefix (inside insula) =="
    bazel_insula test //synthetic:pkgconf_abi_parity \
      --test_output=all --announce_rc \
      --test_env=SPACK_PKGCONF_PREFIX="$SPACK_PKGCONF_PREFIX"
  elif [[ -n "$SPACK_PKGCONF_PREFIX" ]]; then
    echo "== phase 2g: skipping pkgconf native ABI gate (provider remains $SPACK_PKGCONF_BUILD) =="
  fi

  if [[ -n "$SPACK_CA_CERTIFICATES_MOZILLA_PREFIX" && "$SPACK_CA_CERTIFICATES_MOZILLA_BUILD" == "native" ]]; then
    echo "== phase 2h: native ca-certificates-mozilla prefix-parity gate vs Spack prefix (inside insula) =="
    bazel_insula test //synthetic:ca_certificates_mozilla_prefix_parity \
      --test_output=all --announce_rc \
      --test_env=SPACK_CA_CERTIFICATES_MOZILLA_PREFIX="$SPACK_CA_CERTIFICATES_MOZILLA_PREFIX"
  elif [[ -n "$SPACK_CA_CERTIFICATES_MOZILLA_PREFIX" ]]; then
    echo "== phase 2h: skipping ca-certificates-mozilla prefix-parity gate (provider remains $SPACK_CA_CERTIFICATES_MOZILLA_BUILD) =="
  fi

  if [[ -n "$SPACK_BERKELEY_DB_PREFIX" && "$SPACK_BERKELEY_DB_BUILD" == "native" ]]; then
    echo "== phase 2i: native berkeley-db ABI-parity gate vs Spack prefix (inside insula) =="
    bazel_insula test //synthetic:berkeley_db_abi_parity \
      --test_output=all --announce_rc \
      --test_env=SPACK_BERKELEY_DB_PREFIX="$SPACK_BERKELEY_DB_PREFIX"
  elif [[ -n "$SPACK_BERKELEY_DB_PREFIX" ]]; then
    echo "== phase 2i: skipping berkeley-db native ABI gate (provider remains spack) =="
  fi

  if [[ -n "$SPACK_LIBFFI_PREFIX" && "$SPACK_LIBFFI_BUILD" == "native" ]]; then
    echo "== phase 2j: native libffi ABI-parity gate vs Spack prefix (inside insula) =="
    bazel_insula test //synthetic:libffi_abi_parity \
      --test_output=all --announce_rc \
      --test_env=SPACK_LIBFFI_PREFIX="$SPACK_LIBFFI_PREFIX"
  elif [[ -n "$SPACK_LIBFFI_PREFIX" ]]; then
    echo "== phase 2j: skipping libffi native ABI gate (provider remains $SPACK_LIBFFI_BUILD) =="
  fi

  if [[ -n "$SPACK_DIFFUTILS_PREFIX" && "$SPACK_DIFFUTILS_BUILD" == "native" ]]; then
    echo "== phase 2k: native diffutils prefix/behavior parity gate vs Spack prefix (inside insula) =="
    bazel_insula test //synthetic:diffutils_prefix_parity \
      --test_output=all --announce_rc \
      --test_env=SPACK_DIFFUTILS_PREFIX="$SPACK_DIFFUTILS_PREFIX"
  elif [[ -n "$SPACK_DIFFUTILS_PREFIX" ]]; then
    echo "== phase 2k: skipping diffutils native parity gate (provider remains $SPACK_DIFFUTILS_BUILD) =="
  fi

  if [[ -n "$SPACK_BZIP2_PREFIX" && "$SPACK_BZIP2_BUILD" == "native" ]]; then
    echo "== phase 2l: native bzip2 ABI-parity gate vs Spack prefix (inside insula) =="
    bazel_insula test //synthetic:bzip2_abi_parity \
      --test_output=all --announce_rc \
      --test_env=SPACK_BZIP2_PREFIX="$SPACK_BZIP2_PREFIX"
  elif [[ -n "$SPACK_BZIP2_PREFIX" ]]; then
    echo "== phase 2l: skipping bzip2 native ABI gate (provider remains $SPACK_BZIP2_BUILD) =="
  fi

  if [[ -n "$SPACK_BOOST_PREFIX" && "$SPACK_BOOST_BUILD" == "native" ]]; then
    echo "== phase 2l-boost: native boost ABI-parity gate vs Spack prefix (inside insula) =="
    bazel_insula test //synthetic:boost_abi_parity \
      --test_output=all --announce_rc \
      --test_env=SPACK_BOOST_PREFIX="$SPACK_BOOST_PREFIX"
  elif [[ -n "$SPACK_BOOST_PREFIX" ]]; then
    echo "== phase 2l-boost: skipping boost native ABI gate (provider remains $SPACK_BOOST_BUILD) =="
  fi

  if [[ -n "$SPACK_LIBMD_PREFIX" && "$SPACK_LIBMD_BUILD" == "native" ]]; then
    echo "== phase 2m: native libmd ABI-parity gate vs Spack prefix (inside insula) =="
    bazel_insula test //synthetic:libmd_abi_parity \
      --test_output=all --announce_rc \
      --test_env=SPACK_LIBMD_PREFIX="$SPACK_LIBMD_PREFIX"
  elif [[ -n "$SPACK_LIBMD_PREFIX" ]]; then
    echo "== phase 2m: skipping libmd native ABI gate (provider remains $SPACK_LIBMD_BUILD) =="
  fi

  if [[ -n "$SPACK_LIBBSD_PREFIX" && "$SPACK_LIBBSD_BUILD" == "native" ]]; then
    echo "== phase 2n: native libbsd ABI-parity gate vs Spack prefix (inside insula) =="
    bazel_insula test //synthetic:libbsd_abi_parity \
      --test_output=all --announce_rc \
      --test_env=SPACK_LIBBSD_PREFIX="$SPACK_LIBBSD_PREFIX" \
      --test_env=SPACK_LIBMD_PREFIX="$SPACK_LIBMD_PREFIX"
  elif [[ -n "$SPACK_LIBBSD_PREFIX" ]]; then
    echo "== phase 2n: skipping libbsd native ABI gate (provider remains $SPACK_LIBBSD_BUILD) =="
  fi

  if [[ -n "$SPACK_EXPAT_PREFIX" && "$SPACK_EXPAT_BUILD" == "native" ]]; then
    echo "== phase 2o: native expat ABI-parity gate vs Spack prefix (inside insula) =="
    bazel_insula test //synthetic:expat_abi_parity \
      --test_output=all --announce_rc \
      --test_env=SPACK_EXPAT_PREFIX="$SPACK_EXPAT_PREFIX"
  elif [[ -n "$SPACK_EXPAT_PREFIX" ]]; then
    echo "== phase 2o: skipping expat native ABI gate (provider remains $SPACK_EXPAT_BUILD) =="
  fi

  if [[ -n "$SPACK_GPERF_PREFIX" && "$SPACK_GPERF_BUILD" == "native" ]]; then
    echo "== phase 2o-gperf: native gperf prefix/behavior parity gate vs Spack prefix (inside insula) =="
    bazel_insula test //synthetic:gperf_prefix_parity \
      --test_output=all --announce_rc \
      --test_env=SPACK_GPERF_PREFIX="$SPACK_GPERF_PREFIX"
  elif [[ -n "$SPACK_GPERF_PREFIX" ]]; then
    echo "== phase 2o-gperf: skipping gperf native parity gate (provider remains $SPACK_GPERF_BUILD) =="
  fi

  if [[ -n "$SPACK_GMAKE_PREFIX" && "$SPACK_GMAKE_BUILD" == "native" ]]; then
    echo "== phase 2o-gmake: native gmake prefix/behavior parity gate vs Spack prefix (inside insula) =="
    bazel_insula test //synthetic:gmake_prefix_parity \
      --test_output=all --announce_rc \
      --test_env=SPACK_GMAKE_PREFIX="$SPACK_GMAKE_PREFIX"
  elif [[ -n "$SPACK_GMAKE_PREFIX" ]]; then
    echo "== phase 2o-gmake: skipping gmake native parity gate (provider remains $SPACK_GMAKE_BUILD) =="
  fi

  if [[ -n "$SPACK_XXD_STANDALONE_PREFIX" && "$SPACK_XXD_STANDALONE_BUILD" == "native" ]]; then
    echo "== phase 2o-xxd-standalone: native xxd prefix/behavior parity gate vs Spack prefix (inside insula) =="
    bazel_insula test //synthetic:xxd_standalone_prefix_parity \
      --test_output=all --announce_rc \
      --test_env=SPACK_XXD_STANDALONE_PREFIX="$SPACK_XXD_STANDALONE_PREFIX"
  elif [[ -n "$SPACK_XXD_STANDALONE_PREFIX" ]]; then
    echo "== phase 2o-xxd-standalone: skipping xxd native parity gate (provider remains $SPACK_XXD_STANDALONE_BUILD) =="
  fi

  if [[ -n "$SPACK_GZIP_PREFIX" && "$SPACK_GZIP_BUILD" == "native" ]]; then
    echo "== phase 2o-gzip: native gzip prefix/behavior parity gate vs Spack prefix (inside insula) =="
    bazel_insula test //synthetic:gzip_prefix_parity \
      --test_output=all --announce_rc \
      --test_env=SPACK_GZIP_PREFIX="$SPACK_GZIP_PREFIX"
  elif [[ -n "$SPACK_GZIP_PREFIX" ]]; then
    echo "== phase 2o-gzip: skipping gzip native parity gate (provider remains $SPACK_GZIP_BUILD) =="
  fi

  if [[ -n "$SPACK_LIBSIGSEGV_PREFIX" && "$SPACK_LIBSIGSEGV_BUILD" == "native" ]]; then
    echo "== phase 2o-libsigsegv: native libsigsegv ABI-parity gate vs Spack prefix (inside insula) =="
    bazel_insula test //synthetic:libsigsegv_abi_parity \
      --test_output=all --announce_rc \
      --test_env=SPACK_LIBSIGSEGV_PREFIX="$SPACK_LIBSIGSEGV_PREFIX"
  elif [[ -n "$SPACK_LIBSIGSEGV_PREFIX" ]]; then
    echo "== phase 2o-libsigsegv: skipping libsigsegv native ABI gate (provider remains $SPACK_LIBSIGSEGV_BUILD) =="
  fi

  if [[ -n "$SPACK_LIBUNISTRING_PREFIX" && "$SPACK_LIBUNISTRING_BUILD" == "native" ]]; then
    echo "== phase 2o-libunistring: native libunistring ABI-parity gate vs Spack prefix (inside insula) =="
    bazel_insula test //synthetic:libunistring_abi_parity \
      --test_output=all --announce_rc \
      --test_env=SPACK_LIBUNISTRING_PREFIX="$SPACK_LIBUNISTRING_PREFIX" \
      --test_env=SPACK_LIBICONV_PREFIX="$SPACK_LIBICONV_PREFIX"
  elif [[ -n "$SPACK_LIBUNISTRING_PREFIX" ]]; then
    echo "== phase 2o-libunistring: skipping libunistring native ABI gate (provider remains $SPACK_LIBUNISTRING_BUILD) =="
  fi

  if [[ -n "$SPACK_LIBIDN2_PREFIX" && "$SPACK_LIBIDN2_BUILD" == "native" ]]; then
    echo "== phase 2o-libidn2: native libidn2 ABI/behavior parity gate vs Spack prefix (inside insula) =="
    bazel_insula test //synthetic:libidn2_abi_parity \
      --test_output=all --announce_rc \
      --test_env=SPACK_LIBIDN2_PREFIX="$SPACK_LIBIDN2_PREFIX" \
      --test_env=SPACK_LIBUNISTRING_PREFIX="$SPACK_LIBUNISTRING_PREFIX" \
      --test_env=SPACK_LIBICONV_PREFIX="$SPACK_LIBICONV_PREFIX"
  elif [[ -n "$SPACK_LIBIDN2_PREFIX" ]]; then
    echo "== phase 2o-libidn2: skipping libidn2 native ABI gate (provider remains $SPACK_LIBIDN2_BUILD) =="
  fi

  if [[ -n "$SPACK_LIBYAML_PREFIX" && "$SPACK_LIBYAML_BUILD" == "native" ]]; then
    echo "== phase 2o-libyaml: native libyaml ABI/behavior parity gate vs Spack prefix (inside insula) =="
    bazel_insula test //synthetic:libyaml_abi_parity \
      --test_output=all --announce_rc \
      --test_env=SPACK_LIBYAML_PREFIX="$SPACK_LIBYAML_PREFIX"
  elif [[ -n "$SPACK_LIBYAML_PREFIX" ]]; then
    echo "== phase 2o-libyaml: skipping libyaml native ABI gate (provider remains $SPACK_LIBYAML_BUILD) =="
  fi

  if [[ -n "$SPACK_LZO_PREFIX" && "$SPACK_LZO_BUILD" == "native" ]]; then
    echo "== phase 2o-lzo: native lzo ABI/behavior parity gate vs Spack prefix (inside insula) =="
    bazel_insula test //synthetic:lzo_abi_parity \
      --test_output=all --announce_rc \
      --test_env=SPACK_LZO_PREFIX="$SPACK_LZO_PREFIX"
  elif [[ -n "$SPACK_LZO_PREFIX" ]]; then
    echo "== phase 2o-lzo: skipping lzo native ABI gate (provider remains $SPACK_LZO_BUILD) =="
  fi

  if [[ -n "$SPACK_M4_PREFIX" && "$SPACK_M4_BUILD" == "native" ]]; then
    echo "== phase 2o-m4: native m4 prefix/behavior parity gate vs Spack prefix (inside insula) =="
    bazel_insula test //synthetic:m4_prefix_parity \
      --test_output=all --announce_rc \
      --test_env=SPACK_M4_PREFIX="$SPACK_M4_PREFIX" \
      --test_env=SPACK_LIBSIGSEGV_PREFIX="$SPACK_LIBSIGSEGV_PREFIX"
  elif [[ -n "$SPACK_M4_PREFIX" ]]; then
    echo "== phase 2o-m4: skipping m4 native parity gate (provider remains $SPACK_M4_BUILD) =="
  fi

  if [[ -n "$SPACK_NCURSES_PREFIX" && "$SPACK_NCURSES_BUILD" == "native" ]]; then
    echo "== phase 2p: native ncurses ABI-parity gate vs Spack prefix (inside insula) =="
    bazel_insula test //synthetic:ncurses_abi_parity \
      --test_output=all --announce_rc \
      --test_env=SPACK_NCURSES_PREFIX="$SPACK_NCURSES_PREFIX"
  elif [[ -n "$SPACK_NCURSES_PREFIX" ]]; then
    echo "== phase 2p: skipping ncurses native ABI gate (provider remains $SPACK_NCURSES_BUILD) =="
  fi

  if [[ -n "$SPACK_LESS_PREFIX" && "$SPACK_LESS_BUILD" == "native" ]]; then
    echo "== phase 2q: native less prefix/behavior parity gate vs Spack prefix (inside insula) =="
    bazel_insula test //synthetic:less_prefix_parity \
      --test_output=all --announce_rc \
      --test_env=SPACK_LESS_PREFIX="$SPACK_LESS_PREFIX"
  elif [[ -n "$SPACK_LESS_PREFIX" ]]; then
    echo "== phase 2q: skipping less native parity gate (provider remains $SPACK_LESS_BUILD) =="
  fi

  if [[ -n "$SPACK_READLINE_PREFIX" && "$SPACK_READLINE_BUILD" == "native" ]]; then
    echo "== phase 2r: native readline ABI-parity gate vs Spack prefix (inside insula) =="
    bazel_insula test //synthetic:readline_abi_parity \
      --test_output=all --announce_rc \
      --test_env=SPACK_READLINE_PREFIX="$SPACK_READLINE_PREFIX" \
      --test_env=SPACK_NCURSES_PREFIX="$SPACK_NCURSES_PREFIX"
  elif [[ -n "$SPACK_READLINE_PREFIX" ]]; then
    echo "== phase 2r: skipping readline native ABI gate (provider remains $SPACK_READLINE_BUILD) =="
  fi

  if [[ -n "$SPACK_GDBM_PREFIX" && "$SPACK_GDBM_BUILD" == "native" ]]; then
    echo "== phase 2s: native gdbm ABI-parity gate vs Spack prefix (inside insula) =="
    bazel_insula test //synthetic:gdbm_abi_parity \
      --test_output=all --announce_rc \
      --test_env=SPACK_GDBM_PREFIX="$SPACK_GDBM_PREFIX" \
      --test_env=SPACK_READLINE_PREFIX="$SPACK_READLINE_PREFIX" \
      --test_env=SPACK_NCURSES_PREFIX="$SPACK_NCURSES_PREFIX"
  elif [[ -n "$SPACK_GDBM_PREFIX" ]]; then
    echo "== phase 2s: skipping gdbm native ABI gate (provider remains $SPACK_GDBM_BUILD) =="
  fi

  if [[ -n "$SPACK_UNZIP_PREFIX" && "$SPACK_UNZIP_BUILD" == "native" ]]; then
    echo "== phase 2s-unzip: native unzip prefix/behavior parity gate vs Spack prefix (inside insula) =="
    bazel_insula test //synthetic:unzip_prefix_parity \
      --test_output=all --announce_rc \
      --test_env=SPACK_UNZIP_PREFIX="$SPACK_UNZIP_PREFIX"
  elif [[ -n "$SPACK_UNZIP_PREFIX" ]]; then
    echo "== phase 2s-unzip: skipping unzip native parity gate (provider remains $SPACK_UNZIP_BUILD) =="
  fi

  if [[ -n "$SPACK_UTIL_LINUX_UUID_PREFIX" && "$SPACK_UTIL_LINUX_UUID_BUILD" == "native" ]]; then
    echo "== phase 2t: native util-linux-uuid ABI-parity gate vs Spack prefix (inside insula) =="
    bazel_insula test //synthetic:util_linux_uuid_abi_parity \
      --test_output=all --announce_rc \
      --test_env=SPACK_UTIL_LINUX_UUID_PREFIX="$SPACK_UTIL_LINUX_UUID_PREFIX"
  elif [[ -n "$SPACK_UTIL_LINUX_UUID_PREFIX" ]]; then
    echo "== phase 2t: skipping util-linux-uuid native ABI gate (provider remains $SPACK_UTIL_LINUX_UUID_BUILD) =="
  fi

  if [[ -n "$SPACK_UTIL_MACROS_PREFIX" && "$SPACK_UTIL_MACROS_BUILD" == "native" ]]; then
    echo "== phase 2t-util-macros: native util-macros prefix parity gate vs Spack prefix (inside insula) =="
    bazel_insula test //synthetic:util_macros_prefix_parity \
      --test_output=all --announce_rc \
      --test_env=SPACK_UTIL_MACROS_PREFIX="$SPACK_UTIL_MACROS_PREFIX"
  elif [[ -n "$SPACK_UTIL_MACROS_PREFIX" ]]; then
    echo "== phase 2t-util-macros: skipping util-macros native parity gate (provider remains $SPACK_UTIL_MACROS_BUILD) =="
  fi

  if [[ -n "$SPACK_FONTSPROTO_PREFIX" && "$SPACK_FONTSPROTO_BUILD" == "native" ]]; then
    echo "== phase 2t-fontsproto: native fontsproto prefix parity gate vs Spack prefix (inside insula) =="
    bazel_insula test //synthetic:fontsproto_prefix_parity \
      --test_output=all --announce_rc \
      --test_env=SPACK_FONTSPROTO_PREFIX="$SPACK_FONTSPROTO_PREFIX"
  elif [[ -n "$SPACK_FONTSPROTO_PREFIX" ]]; then
    echo "== phase 2t-fontsproto: skipping fontsproto native parity gate (provider remains $SPACK_FONTSPROTO_BUILD) =="
  fi

  if [[ -n "$SPACK_LIBPCIACCESS_PREFIX" && "$SPACK_LIBPCIACCESS_BUILD" == "native" ]]; then
    echo "== phase 2t-libpciaccess: native libpciaccess ABI-parity gate vs Spack prefix (inside insula) =="
    bazel_insula test //synthetic:libpciaccess_abi_parity \
      --test_output=all --announce_rc \
      --test_env=SPACK_LIBPCIACCESS_PREFIX="$SPACK_LIBPCIACCESS_PREFIX"
  elif [[ -n "$SPACK_LIBPCIACCESS_PREFIX" ]]; then
    echo "== phase 2t-libpciaccess: skipping libpciaccess native ABI gate (provider remains $SPACK_LIBPCIACCESS_BUILD) =="
  fi

  if [[ -n "$SPACK_XPROTO_PREFIX" && "$SPACK_XPROTO_BUILD" == "native" ]]; then
    echo "== phase 2t-xproto: native xproto prefix parity gate vs Spack prefix (inside insula) =="
    bazel_insula test //synthetic:xproto_prefix_parity \
      --test_output=all --announce_rc \
      --test_env=SPACK_XPROTO_PREFIX="$SPACK_XPROTO_PREFIX"
  elif [[ -n "$SPACK_XPROTO_PREFIX" ]]; then
    echo "== phase 2t-xproto: skipping xproto native prefix gate (provider remains $SPACK_XPROTO_BUILD) =="
  fi

  if [[ -n "$SPACK_XTRANS_PREFIX" && "$SPACK_XTRANS_BUILD" == "native" ]]; then
    echo "== phase 2t-xtrans: native xtrans prefix parity gate vs Spack prefix (inside insula) =="
    bazel_insula test //synthetic:xtrans_prefix_parity \
      --test_output=all --announce_rc \
      --test_env=SPACK_XTRANS_PREFIX="$SPACK_XTRANS_PREFIX"
  elif [[ -n "$SPACK_XTRANS_PREFIX" ]]; then
    echo "== phase 2t-xtrans: skipping xtrans native prefix gate (provider remains $SPACK_XTRANS_BUILD) =="
  fi

  if [[ -n "$SPACK_LIBFONTENC_PREFIX" && "$SPACK_LIBFONTENC_BUILD" == "native" ]]; then
    echo "== phase 2t-libfontenc: native libfontenc ABI-parity gate vs Spack prefix (inside insula) =="
    bazel_insula test //synthetic:libfontenc_abi_parity \
      --test_output=all --announce_rc \
      --test_env=SPACK_LIBFONTENC_PREFIX="$SPACK_LIBFONTENC_PREFIX" \
      --test_env=SPACK_ZLIB_NG_PREFIX="$SPACK_ZLIB_NG_PREFIX"
  elif [[ -n "$SPACK_LIBFONTENC_PREFIX" ]]; then
    echo "== phase 2t-libfontenc: skipping libfontenc native ABI gate (provider remains $SPACK_LIBFONTENC_BUILD) =="
  fi

  if [[ -n "$SPACK_LIBXFONT_PREFIX" && "$SPACK_LIBXFONT_BUILD" == "native" ]]; then
    echo "== phase 2t-libxfont: native libXfont ABI-parity gate vs Spack prefix (inside insula) =="
    bazel_insula test //synthetic:libxfont_abi_parity \
      --test_output=all --announce_rc \
      --test_env=SPACK_LIBXFONT_PREFIX="$SPACK_LIBXFONT_PREFIX" \
      --test_env=SPACK_BZIP2_PREFIX="$SPACK_BZIP2_PREFIX" \
      --test_env=SPACK_FREETYPE_PREFIX="$SPACK_FREETYPE_PREFIX" \
      --test_env=SPACK_LIBFONTENC_PREFIX="$SPACK_LIBFONTENC_PREFIX" \
      --test_env=SPACK_LIBPNG_PREFIX="$SPACK_LIBPNG_PREFIX" \
      --test_env=SPACK_ZLIB_NG_PREFIX="$SPACK_ZLIB_NG_PREFIX"
  elif [[ -n "$SPACK_LIBXFONT_PREFIX" ]]; then
    echo "== phase 2t-libxfont: skipping libXfont native ABI gate (provider remains $SPACK_LIBXFONT_BUILD) =="
  fi

  if [[ -n "$SPACK_BDFTOPCF_PREFIX" && "$SPACK_BDFTOPCF_BUILD" == "native" ]]; then
    echo "== phase 2t-bdftopcf: native bdftopcf prefix/behavior parity gate vs Spack prefix (inside insula) =="
    bazel_insula test //synthetic:bdftopcf_prefix_parity \
      --test_output=all --announce_rc \
      --test_env=SPACK_BDFTOPCF_PREFIX="$SPACK_BDFTOPCF_PREFIX"
  elif [[ -n "$SPACK_BDFTOPCF_PREFIX" ]]; then
    echo "== phase 2t-bdftopcf: skipping bdftopcf native parity gate (provider remains $SPACK_BDFTOPCF_BUILD) =="
  fi

  if [[ -n "$SPACK_LUA_PREFIX" && "$SPACK_LUA_BUILD" == "native" ]]; then
    echo "== phase 2t-lua: native lua ABI/behavior parity gate vs Spack prefix (inside insula) =="
    bazel_insula test //synthetic:lua_abi_parity \
      --test_output=all --announce_rc \
      --test_env=SPACK_LUA_PREFIX="$SPACK_LUA_PREFIX" \
      --test_env=SPACK_NCURSES_PREFIX="$SPACK_NCURSES_PREFIX" \
      --test_env=SPACK_READLINE_PREFIX="$SPACK_READLINE_PREFIX"
  elif [[ -n "$SPACK_LUA_PREFIX" ]]; then
    echo "== phase 2t-lua: skipping lua native ABI gate (provider remains $SPACK_LUA_BUILD) =="
  fi

  if [[ -n "$SPACK_MKFONTSCALE_PREFIX" && "$SPACK_MKFONTSCALE_BUILD" == "native" ]]; then
    echo "== phase 2t-mkfontscale: native mkfontscale prefix/behavior parity gate vs Spack prefix (inside insula) =="
    bazel_insula test //synthetic:mkfontscale_prefix_parity \
      --test_output=all --announce_rc \
      --test_env=SPACK_MKFONTSCALE_PREFIX="$SPACK_MKFONTSCALE_PREFIX"
  elif [[ -n "$SPACK_MKFONTSCALE_PREFIX" ]]; then
    echo "== phase 2t-mkfontscale: skipping mkfontscale native parity gate (provider remains $SPACK_MKFONTSCALE_BUILD) =="
  fi

  if [[ -n "$SPACK_MKFONTDIR_PREFIX" && "$SPACK_MKFONTDIR_BUILD" == "native" ]]; then
    echo "== phase 2t-mkfontdir: native mkfontdir prefix/behavior parity gate vs Spack prefix (inside insula) =="
    bazel_insula test //synthetic:mkfontdir_prefix_parity \
      --test_output=all --announce_rc \
      --test_env=SPACK_MKFONTDIR_PREFIX="$SPACK_MKFONTDIR_PREFIX" \
      --test_env=SPACK_MKFONTSCALE_PREFIX="$SPACK_MKFONTSCALE_PREFIX"
  elif [[ -n "$SPACK_MKFONTDIR_PREFIX" ]]; then
    echo "== phase 2t-mkfontdir: skipping mkfontdir native parity gate (provider remains $SPACK_MKFONTDIR_BUILD) =="
  fi

  if [[ -n "$SPACK_FONT_UTIL_PREFIX" && "$SPACK_FONT_UTIL_BUILD" == "native" ]]; then
    echo "== phase 2t-font-util: native font-util lean prefix/behavior parity gate vs Spack prefix (inside insula) =="
    bazel_insula test //synthetic:font_util_prefix_parity \
      --test_output=all --announce_rc \
      --test_env=SPACK_FONT_UTIL_PREFIX="$SPACK_FONT_UTIL_PREFIX"
  elif [[ -n "$SPACK_FONT_UTIL_PREFIX" ]]; then
    echo "== phase 2t-font-util: skipping font-util native parity gate (provider remains $SPACK_FONT_UTIL_BUILD) =="
  fi

  if [[ -n "$SPACK_FONTCONFIG_PREFIX" && "$SPACK_FONTCONFIG_BUILD" == "native" ]]; then
    echo "== phase 2t-fontconfig: native fontconfig ABI/behavior parity gate vs Spack prefix (inside insula) =="
    bazel_insula test //synthetic:fontconfig_abi_parity \
      --test_output=all --announce_rc \
      --test_env=SPACK_FONTCONFIG_PREFIX="$SPACK_FONTCONFIG_PREFIX" \
      --test_env=SPACK_FONT_UTIL_PREFIX="$SPACK_FONT_UTIL_PREFIX" \
      --test_env=SPACK_FREETYPE_PREFIX="$SPACK_FREETYPE_PREFIX" \
      --test_env=SPACK_LIBXML2_PREFIX="$SPACK_LIBXML2_PREFIX" \
      --test_env=SPACK_UTIL_LINUX_UUID_PREFIX="$SPACK_UTIL_LINUX_UUID_PREFIX"
  elif [[ -n "$SPACK_FONTCONFIG_PREFIX" ]]; then
    echo "== phase 2t-fontconfig: skipping fontconfig native ABI gate (provider remains $SPACK_FONTCONFIG_BUILD) =="
  fi

  if [[ -n "$SPACK_CAIRO_PREFIX" && "$SPACK_CAIRO_BUILD" == "native" ]]; then
    echo "== phase 2t-cairo: native cairo ABI/behavior parity gate vs Spack prefix (inside insula) =="
    bazel_insula test //synthetic:cairo_abi_parity \
      --test_output=all --announce_rc \
      --test_env=SPACK_CAIRO_PREFIX="$SPACK_CAIRO_PREFIX" \
      --test_env=SPACK_BZIP2_PREFIX="$SPACK_BZIP2_PREFIX" \
      --test_env=SPACK_FONTCONFIG_PREFIX="$SPACK_FONTCONFIG_PREFIX" \
      --test_env=SPACK_FREETYPE_PREFIX="$SPACK_FREETYPE_PREFIX" \
      --test_env=SPACK_LIBPNG_PREFIX="$SPACK_LIBPNG_PREFIX" \
      --test_env=SPACK_LIBXML2_PREFIX="$SPACK_LIBXML2_PREFIX" \
      --test_env=SPACK_LZO_PREFIX="$SPACK_LZO_PREFIX" \
      --test_env=SPACK_PIXMAN_PREFIX="$SPACK_PIXMAN_PREFIX" \
      --test_env=SPACK_ZLIB_NG_PREFIX="$SPACK_ZLIB_NG_PREFIX"
  elif [[ -n "$SPACK_CAIRO_PREFIX" ]]; then
    echo "== phase 2t-cairo: skipping cairo native ABI gate (provider remains $SPACK_CAIRO_BUILD) =="
  fi

  if [[ -n "$SPACK_HARFBUZZ_PREFIX" && "$SPACK_HARFBUZZ_BUILD" == "native" ]]; then
    echo "== phase 2t-harfbuzz: native harfbuzz ABI/behavior parity gate vs Spack prefix (inside insula) =="
    bazel_insula test //synthetic:harfbuzz_abi_parity \
      --test_output=all --announce_rc \
      --test_env=SPACK_HARFBUZZ_PREFIX="$SPACK_HARFBUZZ_PREFIX" \
      --test_env=SPACK_CAIRO_PREFIX="$SPACK_CAIRO_PREFIX" \
      --test_env=SPACK_FREETYPE_PREFIX="$SPACK_FREETYPE_PREFIX" \
      --test_env=SPACK_GLIB_PREFIX="$SPACK_GLIB_PREFIX" \
      --test_env=SPACK_GOBJECT_INTROSPECTION_PREFIX="$SPACK_GOBJECT_INTROSPECTION_PREFIX" \
      --test_env=SPACK_ICU4C_PREFIX="$SPACK_ICU4C_PREFIX" \
      --test_env=SPACK_ZLIB_NG_PREFIX="$SPACK_ZLIB_NG_PREFIX"
  elif [[ -n "$SPACK_HARFBUZZ_PREFIX" ]]; then
    echo "== phase 2t-harfbuzz: skipping harfbuzz native ABI gate (provider remains $SPACK_HARFBUZZ_BUILD) =="
  fi

  if [[ -n "$SPACK_LIBRAQM_PREFIX" && "$SPACK_LIBRAQM_BUILD" == "native" ]]; then
    echo "== phase 2t-libraqm: native libraqm ABI/behavior parity gate vs Spack prefix (inside insula) =="
    bazel_insula test //synthetic:libraqm_abi_parity \
      --test_output=all --announce_rc \
      --test_env=SPACK_LIBRAQM_PREFIX="$SPACK_LIBRAQM_PREFIX" \
      --test_env=SPACK_BZIP2_PREFIX="$SPACK_BZIP2_PREFIX" \
      --test_env=SPACK_FREETYPE_PREFIX="$SPACK_FREETYPE_PREFIX" \
      --test_env=SPACK_FRIBIDI_PREFIX="$SPACK_FRIBIDI_PREFIX" \
      --test_env=SPACK_GLIB_PREFIX="$SPACK_GLIB_PREFIX" \
      --test_env=SPACK_HARFBUZZ_PREFIX="$SPACK_HARFBUZZ_PREFIX" \
      --test_env=SPACK_LIBPNG_PREFIX="$SPACK_LIBPNG_PREFIX" \
      --test_env=SPACK_PCRE2_PREFIX="$SPACK_PCRE2_PREFIX" \
      --test_env=SPACK_ZLIB_NG_PREFIX="$SPACK_ZLIB_NG_PREFIX"
  elif [[ -n "$SPACK_LIBRAQM_PREFIX" ]]; then
    echo "== phase 2t-libraqm: skipping libraqm native ABI gate (provider remains $SPACK_LIBRAQM_BUILD) =="
  fi

  if [[ -n "$SPACK_ICU4C_PREFIX" && "$SPACK_ICU4C_BUILD" == "native" ]]; then
    echo "== phase 2t-icu4c: native icu4c ABI/behavior parity gate vs Spack prefix (inside insula) =="
    bazel_insula test //synthetic:icu4c_abi_parity \
      --test_output=all --announce_rc \
      --test_env=SPACK_ICU4C_PREFIX="$SPACK_ICU4C_PREFIX"
  elif [[ -n "$SPACK_ICU4C_PREFIX" ]]; then
    echo "== phase 2t-icu4c: skipping icu4c native ABI gate (provider remains $SPACK_ICU4C_BUILD) =="
  fi

  if [[ -n "$SPACK_HWLOC_PREFIX" && "$SPACK_HWLOC_BUILD" == "native" ]]; then
    echo "== phase 2t-hwloc: native hwloc ABI-parity gate vs Spack prefix (inside insula) =="
    bazel_insula test //synthetic:hwloc_abi_parity \
      --test_output=all --announce_rc \
      --test_env=SPACK_HWLOC_PREFIX="$SPACK_HWLOC_PREFIX" \
      --test_env=SPACK_LIBPCIACCESS_PREFIX="$SPACK_LIBPCIACCESS_PREFIX" \
      --test_env=SPACK_LIBXML2_PREFIX="$SPACK_LIBXML2_PREFIX" \
      --test_env=SPACK_NCURSES_PREFIX="$SPACK_NCURSES_PREFIX"
  elif [[ -n "$SPACK_HWLOC_PREFIX" ]]; then
    echo "== phase 2t-hwloc: skipping hwloc native ABI gate (provider remains $SPACK_HWLOC_BUILD) =="
  fi

  if [[ -n "$SPACK_PERL_PREFIX" && "$SPACK_PERL_BUILD" == "native" ]]; then
    echo "== phase 2u: native perl prefix/ABI parity gate vs Spack prefix (inside insula) =="
    bazel_insula test //synthetic:perl_prefix_parity \
      --test_output=all --announce_rc \
      --test_env=SPACK_PERL_PREFIX="$SPACK_PERL_PREFIX"
  elif [[ -n "$SPACK_PERL_PREFIX" ]]; then
    echo "== phase 2u: skipping perl native parity gate (provider remains $SPACK_PERL_BUILD) =="
  fi

  if [[ -n "$SPACK_PERL_DATA_DUMPER_PREFIX" && "$SPACK_PERL_DATA_DUMPER_BUILD" == "native" ]]; then
    echo "== phase 2u-perl-data-dumper: native perl-data-dumper ABI/behavior parity gate vs Spack prefix (inside insula) =="
    bazel_insula test //synthetic:perl_data_dumper_abi_parity \
      --test_output=all --announce_rc \
      --test_env=SPACK_PERL_DATA_DUMPER_PREFIX="$SPACK_PERL_DATA_DUMPER_PREFIX"
  elif [[ -n "$SPACK_PERL_DATA_DUMPER_PREFIX" ]]; then
    echo "== phase 2u-perl-data-dumper: skipping perl-data-dumper native ABI gate (provider remains $SPACK_PERL_DATA_DUMPER_BUILD) =="
  fi

  if [[ -n "$SPACK_AUTOCONF_PREFIX" && "$SPACK_AUTOCONF_BUILD" == "native" ]]; then
    echo "== phase 2u-autoconf: native autoconf prefix/behavior parity gate vs Spack prefix (inside insula) =="
    bazel_insula test //synthetic:autoconf_prefix_parity \
      --test_output=all --announce_rc \
      --test_env=SPACK_AUTOCONF_PREFIX="$SPACK_AUTOCONF_PREFIX" \
      --test_env=SPACK_M4_PREFIX="$SPACK_M4_PREFIX" \
      --test_env=SPACK_PERL_PREFIX="$SPACK_PERL_PREFIX"
  elif [[ -n "$SPACK_AUTOCONF_PREFIX" ]]; then
    echo "== phase 2u-autoconf: skipping autoconf native parity gate (provider remains $SPACK_AUTOCONF_BUILD) =="
  fi

  if [[ -n "$SPACK_AUTOMAKE_PREFIX" && "$SPACK_AUTOMAKE_BUILD" == "native" ]]; then
    echo "== phase 2u-automake: native automake prefix/behavior parity gate vs Spack prefix (inside insula) =="
    bazel_insula test //synthetic:automake_prefix_parity \
      --test_output=all --announce_rc \
      --test_env=SPACK_AUTOMAKE_PREFIX="$SPACK_AUTOMAKE_PREFIX" \
      --test_env=SPACK_AUTOCONF_PREFIX="$SPACK_AUTOCONF_PREFIX" \
      --test_env=SPACK_PERL_PREFIX="$SPACK_PERL_PREFIX"
  elif [[ -n "$SPACK_AUTOMAKE_PREFIX" ]]; then
    echo "== phase 2u-automake: skipping automake native parity gate (provider remains $SPACK_AUTOMAKE_BUILD) =="
  fi

  if [[ -n "$SPACK_LIBXCRYPT_PREFIX" && "$SPACK_LIBXCRYPT_BUILD" == "native" ]]; then
    echo "== phase 2u-libxcrypt: native libxcrypt ABI/behavior parity gate vs Spack prefix (inside insula) =="
    bazel_insula test //synthetic:libxcrypt_abi_parity \
      --test_output=all --announce_rc \
      --test_env=SPACK_LIBXCRYPT_PREFIX="$SPACK_LIBXCRYPT_PREFIX"
  elif [[ -n "$SPACK_LIBXCRYPT_PREFIX" ]]; then
    echo "== phase 2u-libxcrypt: skipping libxcrypt native ABI gate (provider remains $SPACK_LIBXCRYPT_BUILD) =="
  fi

  if [[ -n "$SPACK_OPENSSL_PREFIX" && "$SPACK_OPENSSL_BUILD" == "native" ]]; then
    echo "== phase 2v: native openssl ABI-parity gate vs Spack prefix (inside insula) =="
    bazel_insula test //synthetic:openssl_abi_parity \
      --test_output=all --announce_rc \
      --test_env=SPACK_OPENSSL_PREFIX="$SPACK_OPENSSL_PREFIX" \
      --test_env=SPACK_ZLIB_NG_PREFIX="$SPACK_ZLIB_NG_PREFIX"
  elif [[ -n "$SPACK_OPENSSL_PREFIX" ]]; then
    echo "== phase 2v: skipping openssl native parity gate (provider remains $SPACK_OPENSSL_BUILD) =="
  fi

  if [[ -n "$SPACK_COREUTILS_PREFIX" && "$SPACK_COREUTILS_BUILD" == "native" ]]; then
    echo "== phase 2v-coreutils: native coreutils prefix/behavior parity gate vs Spack prefix (inside insula) =="
    bazel_insula test //synthetic:coreutils_prefix_parity \
      --test_output=all --announce_rc \
      --test_env=SPACK_COREUTILS_PREFIX="$SPACK_COREUTILS_PREFIX" \
      --test_env=SPACK_OPENSSL_PREFIX="$SPACK_OPENSSL_PREFIX"
  elif [[ -n "$SPACK_COREUTILS_PREFIX" ]]; then
    echo "== phase 2v-coreutils: skipping coreutils native parity gate (provider remains $SPACK_COREUTILS_BUILD) =="
  fi

  if [[ -n "$SPACK_CUDNN_PREFIX" && "$SPACK_CUDNN_BUILD" == "native" ]]; then
    echo "== phase 2v-cudnn: native cuDNN ABI-parity gate vs Spack prefix (inside insula) =="
    bazel_insula test //synthetic:cudnn_abi_parity \
      --test_output=all --announce_rc \
      --test_env=SPACK_CUDNN_PREFIX="$SPACK_CUDNN_PREFIX" \
      --test_env=SPACK_CUDA_PREFIX="$SPACK_CUDA_PREFIX"
  elif [[ -n "$SPACK_CUDNN_PREFIX" ]]; then
    echo "== phase 2v-cudnn: skipping cuDNN native parity gate (provider remains $SPACK_CUDNN_BUILD) =="
  fi

  if [[ -n "$SPACK_CUSPARSELT_PREFIX" && "$SPACK_CUSPARSELT_BUILD" == "native" ]]; then
    echo "== phase 2v-cusparselt: native cuSPARSELt ABI-parity gate vs Spack prefix (inside insula) =="
    bazel_insula test //synthetic:cusparselt_abi_parity \
      --test_output=all --announce_rc \
      --test_env=SPACK_CUSPARSELT_PREFIX="$SPACK_CUSPARSELT_PREFIX" \
      --test_env=SPACK_CUDA_PREFIX="$SPACK_CUDA_PREFIX"
  elif [[ -n "$SPACK_CUSPARSELT_PREFIX" ]]; then
    echo "== phase 2v-cusparselt: skipping cuSPARSELt native parity gate (provider remains $SPACK_CUSPARSELT_BUILD) =="
  fi

  if [[ -n "$SPACK_CUDSS_PREFIX" && "$SPACK_CUDSS_BUILD" == "native" ]]; then
    echo "== phase 2v-cudss: native cuDSS ABI-parity gate vs Spack prefix (inside insula) =="
    bazel_insula test //synthetic:cudss_abi_parity \
      --test_output=all --announce_rc \
      --test_env=SPACK_CUDSS_PREFIX="$SPACK_CUDSS_PREFIX" \
      --test_env=SPACK_CUDA_PREFIX="$SPACK_CUDA_PREFIX"
  elif [[ -n "$SPACK_CUDSS_PREFIX" ]]; then
    echo "== phase 2v-cudss: skipping cuDSS native ABI gate (provider remains $SPACK_CUDSS_BUILD) =="
  fi

  if [[ -n "$SPACK_NCCL_PREFIX" && "$SPACK_NCCL_BUILD" == "native" ]]; then
    echo "== phase 2v-nccl: native NCCL ABI-parity gate vs Spack prefix (inside insula) =="
    bazel_insula test //synthetic:nccl_abi_parity \
      --test_output=all --announce_rc \
      --test_env=SPACK_NCCL_PREFIX="$SPACK_NCCL_PREFIX" \
      --test_env=SPACK_CUDA_PREFIX="$SPACK_CUDA_PREFIX"
  elif [[ -n "$SPACK_NCCL_PREFIX" ]]; then
    echo "== phase 2v-nccl: skipping NCCL native ABI gate (provider remains $SPACK_NCCL_BUILD) =="
  fi

  if [[ -n "$SPACK_MAGMA_PREFIX" && "$SPACK_MAGMA_BUILD" == "native" ]]; then
    echo "== phase 2v-magma: native MAGMA behavior gate (inside insula) =="
    bazel_insula test //synthetic:use_magma_native --test_output=all --announce_rc
  elif [[ -n "$SPACK_MAGMA_PREFIX" ]]; then
    echo "== phase 2v-magma: skipping MAGMA native behavior gate (provider remains $SPACK_MAGMA_BUILD) =="
  fi

  if [[ -n "$SPACK_NVSHMEM_PREFIX" && "$SPACK_NVSHMEM_BUILD" == "native" ]]; then
    echo "== phase 2v-nvshmem: native NVSHMEM behavior gate (inside insula) =="
    bazel_insula test //synthetic:use_nvshmem_native --test_output=all --announce_rc
  elif [[ -n "$SPACK_NVSHMEM_PREFIX" ]]; then
    echo "== phase 2v-nvshmem: skipping NVSHMEM native behavior gate (provider remains $SPACK_NVSHMEM_BUILD) =="
  fi

  if [[ -n "$SPACK_CURL_PREFIX" && "$SPACK_CURL_BUILD" == "native" ]]; then
    echo "== phase 2v-curl: native curl ABI/behavior parity gate vs Spack prefix (inside insula) =="
    bazel_insula test //synthetic:curl_abi_parity \
      --test_output=all --announce_rc \
      --test_env=SPACK_CURL_PREFIX="$SPACK_CURL_PREFIX" \
      --test_env=SPACK_NGHTTP2_PREFIX="$SPACK_NGHTTP2_PREFIX" \
      --test_env=SPACK_OPENSSL_PREFIX="$SPACK_OPENSSL_PREFIX" \
      --test_env=SPACK_ZLIB_NG_PREFIX="$SPACK_ZLIB_NG_PREFIX"
  elif [[ -n "$SPACK_CURL_PREFIX" ]]; then
    echo "== phase 2v-curl: skipping curl native ABI gate (provider remains $SPACK_CURL_BUILD) =="
  fi

  if [[ -n "$SPACK_CMAKE_PREFIX" && "$SPACK_CMAKE_BUILD" == "native" ]]; then
    echo "== phase 2v-cmake: native cmake prefix/behavior parity gate vs Spack prefix (inside insula) =="
    bazel_insula test //synthetic:cmake_prefix_parity \
      --test_output=all --announce_rc \
      --test_env=SPACK_CMAKE_PREFIX="$SPACK_CMAKE_PREFIX" \
      --test_env=SPACK_CURL_PREFIX="$SPACK_CURL_PREFIX" \
      --test_env=SPACK_NCURSES_PREFIX="$SPACK_NCURSES_PREFIX" \
      --test_env=SPACK_ZLIB_NG_PREFIX="$SPACK_ZLIB_NG_PREFIX"
  elif [[ -n "$SPACK_CMAKE_PREFIX" ]]; then
    echo "== phase 2v-cmake: skipping cmake native parity gate (provider remains $SPACK_CMAKE_BUILD) =="
  fi

  if [[ -n "$SPACK_EIGEN_PREFIX" && "$SPACK_EIGEN_BUILD" == "native" ]]; then
    echo "== phase 2v-eigen: native Eigen prefix/header parity gate vs Spack prefix (inside insula) =="
    bazel_insula test //synthetic:eigen_prefix_parity \
      --test_output=all --announce_rc \
      --test_env=SPACK_EIGEN_PREFIX="$SPACK_EIGEN_PREFIX"
  elif [[ -n "$SPACK_EIGEN_PREFIX" ]]; then
    echo "== phase 2v-eigen: skipping Eigen native parity gate (provider remains $SPACK_EIGEN_BUILD) =="
  fi

  if [[ -n "$SPACK_PIGZ_PREFIX" && "$SPACK_PIGZ_BUILD" == "native" ]]; then
    echo "== phase 2w: native pigz prefix/behavior parity gate vs Spack prefix (inside insula) =="
    bazel_insula test //synthetic:pigz_prefix_parity \
      --test_output=all --announce_rc \
      --test_env=SPACK_PIGZ_PREFIX="$SPACK_PIGZ_PREFIX" \
      --test_env=SPACK_ZLIB_NG_PREFIX="$SPACK_ZLIB_NG_PREFIX"
  elif [[ -n "$SPACK_PIGZ_PREFIX" ]]; then
    echo "== phase 2w: skipping pigz native parity gate (provider remains $SPACK_PIGZ_BUILD) =="
  fi

  if [[ -n "$SPACK_SQLITE_PREFIX" && "$SPACK_SQLITE_BUILD" == "native" ]]; then
    echo "== phase 2x: native sqlite ABI/behavior parity gate vs Spack prefix (inside insula) =="
    bazel_insula test //synthetic:sqlite_abi_parity \
      --test_output=all --announce_rc \
      --test_env=SPACK_SQLITE_PREFIX="$SPACK_SQLITE_PREFIX" \
      --test_env=SPACK_READLINE_PREFIX="$SPACK_READLINE_PREFIX" \
      --test_env=SPACK_ZLIB_NG_PREFIX="$SPACK_ZLIB_NG_PREFIX"
  elif [[ -n "$SPACK_SQLITE_PREFIX" ]]; then
    echo "== phase 2x: skipping sqlite native parity gate (provider remains $SPACK_SQLITE_BUILD) =="
  fi

  if [[ -n "$SPACK_TAR_PREFIX" && "$SPACK_TAR_BUILD" == "native" ]]; then
    echo "== phase 2y: native tar prefix/behavior parity gate vs Spack prefix (inside insula) =="
    bazel_insula test //synthetic:tar_prefix_parity \
      --test_output=all --announce_rc \
      --test_env=SPACK_TAR_PREFIX="$SPACK_TAR_PREFIX" \
      --test_env=SPACK_LIBICONV_PREFIX="$SPACK_LIBICONV_PREFIX"
  elif [[ -n "$SPACK_TAR_PREFIX" ]]; then
    echo "== phase 2y: skipping tar native parity gate (provider remains $SPACK_TAR_BUILD) =="
  fi

  if [[ -n "$SPACK_GETTEXT_PREFIX" && "$SPACK_GETTEXT_BUILD" == "native" ]]; then
    echo "== phase 2z: native gettext ABI/behavior parity gate vs Spack prefix (inside insula) =="
    bazel_insula test //synthetic:gettext_abi_parity \
      --test_output=all --announce_rc \
      --test_env=SPACK_GETTEXT_PREFIX="$SPACK_GETTEXT_PREFIX" \
      --test_env=SPACK_LIBICONV_PREFIX="$SPACK_LIBICONV_PREFIX" \
      --test_env=SPACK_LIBXML2_PREFIX="$SPACK_LIBXML2_PREFIX" \
      --test_env=SPACK_NCURSES_PREFIX="$SPACK_NCURSES_PREFIX"
  elif [[ -n "$SPACK_GETTEXT_PREFIX" ]]; then
    echo "== phase 2z: skipping gettext native parity gate (provider remains $SPACK_GETTEXT_BUILD) =="
  fi

  if [[ -n "$SPACK_GLIB_BOOTSTRAP_PREFIX" && "$SPACK_GLIB_BOOTSTRAP_BUILD" == "native" ]]; then
    echo "== phase 2z-glib-bootstrap: native glib-bootstrap ABI/behavior parity gate vs Spack prefix (inside insula) =="
    bazel_insula test //synthetic:glib_bootstrap_abi_parity \
      --test_output=all --announce_rc \
      --test_env=SPACK_GLIB_BOOTSTRAP_PREFIX="$SPACK_GLIB_BOOTSTRAP_PREFIX" \
      --test_env=SPACK_GETTEXT_PREFIX="$SPACK_GETTEXT_PREFIX" \
      --test_env=SPACK_LIBFFI_PREFIX="$SPACK_LIBFFI_PREFIX" \
      --test_env=SPACK_LIBICONV_PREFIX="$SPACK_LIBICONV_PREFIX" \
      --test_env=SPACK_PCRE2_PREFIX="$SPACK_PCRE2_PREFIX" \
      --test_env=SPACK_ZLIB_NG_PREFIX="$SPACK_ZLIB_NG_PREFIX"
  elif [[ -n "$SPACK_GLIB_BOOTSTRAP_PREFIX" ]]; then
    echo "== phase 2z-glib-bootstrap: skipping glib-bootstrap native parity gate (provider remains $SPACK_GLIB_BOOTSTRAP_BUILD) =="
  fi

  if [[ -n "$SPACK_GOBJECT_INTROSPECTION_PREFIX" && "$SPACK_GOBJECT_INTROSPECTION_BUILD" == "native" ]]; then
    echo "== phase 2z-gobject-introspection: native gobject-introspection ABI/behavior parity gate vs Spack prefix (inside insula) =="
    bazel_insula test //synthetic:gobject_introspection_abi_parity \
      --test_output=all --announce_rc \
      --test_env=SPACK_GOBJECT_INTROSPECTION_PREFIX="$SPACK_GOBJECT_INTROSPECTION_PREFIX" \
      --test_env=SPACK_GLIB_BOOTSTRAP_PREFIX="$SPACK_GLIB_BOOTSTRAP_PREFIX" \
      --test_env=SPACK_LIBFFI_PREFIX="$SPACK_LIBFFI_PREFIX" \
      --test_env=SPACK_LIBICONV_PREFIX="$SPACK_LIBICONV_PREFIX" \
      --test_env=SPACK_PCRE2_PREFIX="$SPACK_PCRE2_PREFIX" \
      --test_env=SPACK_PYTHON_PREFIX="$SPACK_PYTHON_PREFIX" \
      --test_env=SPACK_ZLIB_NG_PREFIX="$SPACK_ZLIB_NG_PREFIX"
  elif [[ -n "$SPACK_GOBJECT_INTROSPECTION_PREFIX" ]]; then
    echo "== phase 2z-gobject-introspection: skipping gobject-introspection native parity gate (provider remains $SPACK_GOBJECT_INTROSPECTION_BUILD) =="
  fi

  if [[ -n "$SPACK_GLIB_PREFIX" && "$SPACK_GLIB_BUILD" == "native" ]]; then
    echo "== phase 2z-glib: native glib ABI/behavior parity gate vs Spack prefix (inside insula) =="
    bazel_insula test //synthetic:glib_abi_parity \
      --test_output=all --announce_rc \
      --test_env=SPACK_GLIB_PREFIX="$SPACK_GLIB_PREFIX" \
      --test_env=SPACK_ELFUTILS_PREFIX="$SPACK_ELFUTILS_PREFIX" \
      --test_env=SPACK_GETTEXT_PREFIX="$SPACK_GETTEXT_PREFIX" \
      --test_env=SPACK_GOBJECT_INTROSPECTION_PREFIX="$SPACK_GOBJECT_INTROSPECTION_PREFIX" \
      --test_env=SPACK_LIBFFI_PREFIX="$SPACK_LIBFFI_PREFIX" \
      --test_env=SPACK_LIBICONV_PREFIX="$SPACK_LIBICONV_PREFIX" \
      --test_env=SPACK_PCRE2_PREFIX="$SPACK_PCRE2_PREFIX" \
      --test_env=SPACK_ZLIB_NG_PREFIX="$SPACK_ZLIB_NG_PREFIX"
  elif [[ -n "$SPACK_GLIB_PREFIX" ]]; then
    echo "== phase 2z-glib: skipping glib native parity gate (provider remains $SPACK_GLIB_BUILD) =="
  fi

  if [[ -n "$SPACK_ELFUTILS_PREFIX" && "$SPACK_ELFUTILS_BUILD" == "native" ]]; then
    echo "== phase 2z-elfutils: native elfutils ABI/behavior parity gate vs Spack prefix (inside insula) =="
    bazel_insula test //synthetic:elfutils_abi_parity \
      --test_output=all --announce_rc \
      --test_env=SPACK_ELFUTILS_PREFIX="$SPACK_ELFUTILS_PREFIX" \
      --test_env=SPACK_BZIP2_PREFIX="$SPACK_BZIP2_PREFIX" \
      --test_env=SPACK_GETTEXT_PREFIX="$SPACK_GETTEXT_PREFIX" \
      --test_env=SPACK_LIBICONV_PREFIX="$SPACK_LIBICONV_PREFIX" \
      --test_env=SPACK_XZ_PREFIX="$SPACK_XZ_PREFIX" \
      --test_env=SPACK_ZLIB_NG_PREFIX="$SPACK_ZLIB_NG_PREFIX" \
      --test_env=SPACK_ZSTD_PREFIX="$SPACK_ZSTD_PREFIX"
  elif [[ -n "$SPACK_ELFUTILS_PREFIX" ]]; then
    echo "== phase 2z-elfutils: skipping elfutils native ABI gate (provider remains $SPACK_ELFUTILS_BUILD) =="
  fi

  if [[ -n "$SPACK_PYTHON_PREFIX" && "$SPACK_PYTHON_BUILD" == "native" && "$SPACK_PYTHON_VERSION" == "3.13.13" ]]; then
    echo "== phase 2aa: native python@3.13.13 ABI/behavior parity gate vs Spack prefix (inside insula) =="
    bazel_insula test //synthetic:python_313_abi_parity \
      --test_output=all --announce_rc \
      --test_env=SPACK_PYTHON_PREFIX="$SPACK_PYTHON_PREFIX" \
      --test_env=SPACK_BZIP2_PREFIX="$SPACK_BZIP2_PREFIX" \
      --test_env=SPACK_EXPAT_PREFIX="$SPACK_EXPAT_PREFIX" \
      --test_env=SPACK_GDBM_PREFIX="$SPACK_GDBM_PREFIX" \
      --test_env=SPACK_GETTEXT_PREFIX="$SPACK_GETTEXT_PREFIX" \
      --test_env=SPACK_LIBFFI_PREFIX="$SPACK_LIBFFI_PREFIX" \
      --test_env=SPACK_NCURSES_PREFIX="$SPACK_NCURSES_PREFIX" \
      --test_env=SPACK_OPENSSL_PREFIX="$SPACK_OPENSSL_PREFIX" \
      --test_env=SPACK_READLINE_PREFIX="$SPACK_READLINE_PREFIX" \
      --test_env=SPACK_SQLITE_PREFIX="$SPACK_SQLITE_PREFIX" \
      --test_env=SPACK_UTIL_LINUX_UUID_PREFIX="$SPACK_UTIL_LINUX_UUID_PREFIX" \
      --test_env=SPACK_XZ_PREFIX="$SPACK_XZ_PREFIX" \
      --test_env=SPACK_ZLIB_NG_PREFIX="$SPACK_ZLIB_NG_PREFIX" \
      --test_env=SPACK_ZSTD_PREFIX="$SPACK_ZSTD_PREFIX"
  elif [[ -n "$SPACK_PYTHON_PREFIX" && "$SPACK_PYTHON_BUILD" == "native" ]]; then
    echo "== phase 2aa: native python ABI/behavior parity gate vs Spack prefix (inside insula) =="
    bazel_insula test //synthetic:python_abi_parity \
      --test_output=all --announce_rc \
      --test_env=SPACK_PYTHON_PREFIX="$SPACK_PYTHON_PREFIX" \
      --test_env=SPACK_BZIP2_PREFIX="$SPACK_BZIP2_PREFIX" \
      --test_env=SPACK_EXPAT_PREFIX="$SPACK_EXPAT_PREFIX" \
      --test_env=SPACK_GDBM_PREFIX="$SPACK_GDBM_PREFIX" \
      --test_env=SPACK_GETTEXT_PREFIX="$SPACK_GETTEXT_PREFIX" \
      --test_env=SPACK_LIBFFI_PREFIX="$SPACK_LIBFFI_PREFIX" \
      --test_env=SPACK_NCURSES_PREFIX="$SPACK_NCURSES_PREFIX" \
      --test_env=SPACK_OPENSSL_PREFIX="$SPACK_OPENSSL_PREFIX" \
      --test_env=SPACK_READLINE_PREFIX="$SPACK_READLINE_PREFIX" \
      --test_env=SPACK_SQLITE_PREFIX="$SPACK_SQLITE_PREFIX" \
      --test_env=SPACK_UTIL_LINUX_UUID_PREFIX="$SPACK_UTIL_LINUX_UUID_PREFIX" \
      --test_env=SPACK_XZ_PREFIX="$SPACK_XZ_PREFIX" \
      --test_env=SPACK_ZLIB_NG_PREFIX="$SPACK_ZLIB_NG_PREFIX" \
      --test_env=SPACK_ZSTD_PREFIX="$SPACK_ZSTD_PREFIX"
  elif [[ -n "$SPACK_PYTHON_PREFIX" ]]; then
    echo "== phase 2aa: skipping python native parity gate (provider remains $SPACK_PYTHON_BUILD) =="
  fi

  if [[ -n "$SPACK_PYTHON_VENV_PREFIX" && "$SPACK_PYTHON_VENV_BUILD" == "native" ]]; then
    echo "== phase 2aa-python-venv: native python-venv ABI/behavior parity gate vs Spack prefix (inside insula) =="
    bazel_insula test //synthetic:python_venv_abi_parity \
      --test_output=all --announce_rc \
      --test_env=SPACK_PYTHON_VENV_PREFIX="$SPACK_PYTHON_VENV_PREFIX" \
      --test_env=SPACK_PYTHON_PREFIX="$SPACK_PYTHON_PREFIX"
  elif [[ -n "$SPACK_PYTHON_VENV_PREFIX" ]]; then
    echo "== phase 2aa-python-venv: skipping python-venv native parity gate (provider remains $SPACK_PYTHON_VENV_BUILD) =="
  fi

  if [[ -n "$SPACK_PY_PIP_PREFIX" && "$SPACK_PY_PIP_BUILD" == "native" ]]; then
    echo "== phase 2aa-py-pip: native py-pip prefix/behavior parity gate vs Spack prefix (inside insula) =="
    bazel_insula test //synthetic:py_pip_prefix_parity \
      --test_output=all --announce_rc \
      --test_env=SPACK_PY_PIP_PREFIX="$SPACK_PY_PIP_PREFIX" \
      --test_env=SPACK_PYTHON_PREFIX="$SPACK_PYTHON_PREFIX" \
      --test_env=SPACK_PYTHON_VENV_PREFIX="$SPACK_PYTHON_VENV_PREFIX"
  elif [[ -n "$SPACK_PY_PIP_PREFIX" ]]; then
    echo "== phase 2aa-py-pip: skipping py-pip native parity gate (provider remains $SPACK_PY_PIP_BUILD) =="
  fi

  if [[ -n "$SPACK_PY_SETUPTOOLS_PREFIX" && "$SPACK_PY_SETUPTOOLS_BUILD" == "native" ]]; then
    echo "== phase 2aa-py-setuptools: native py-setuptools prefix parity gate vs Spack prefix (inside insula) =="
    bazel_insula test //synthetic:py_setuptools_prefix_parity \
      --test_output=all --announce_rc \
      --test_env=SPACK_PY_SETUPTOOLS_PREFIX="$SPACK_PY_SETUPTOOLS_PREFIX" \
      --test_env=SPACK_PY_PIP_PREFIX="$SPACK_PY_PIP_PREFIX" \
      --test_env=SPACK_PYTHON_PREFIX="$SPACK_PYTHON_PREFIX" \
      --test_env=SPACK_PYTHON_VENV_PREFIX="$SPACK_PYTHON_VENV_PREFIX"
  elif [[ -n "$SPACK_PY_SETUPTOOLS_PREFIX" ]]; then
    echo "== phase 2aa-py-setuptools: skipping py-setuptools native parity gate (provider remains $SPACK_PY_SETUPTOOLS_BUILD) =="
  fi

  if [[ -n "$SPACK_PY_WHEEL_PREFIX" && "$SPACK_PY_WHEEL_BUILD" == "native" ]]; then
    echo "== phase 2aa-py-wheel: native py-wheel prefix/behavior parity gate vs Spack prefix (inside insula) =="
    bazel_insula test //synthetic:py_wheel_prefix_parity \
      --test_output=all --announce_rc \
      --test_env=SPACK_PY_WHEEL_PREFIX="$SPACK_PY_WHEEL_PREFIX" \
      --test_env=SPACK_PY_PIP_PREFIX="$SPACK_PY_PIP_PREFIX" \
      --test_env=SPACK_PYTHON_PREFIX="$SPACK_PYTHON_PREFIX" \
      --test_env=SPACK_PYTHON_VENV_PREFIX="$SPACK_PYTHON_VENV_PREFIX"
  elif [[ -n "$SPACK_PY_WHEEL_PREFIX" ]]; then
    echo "== phase 2aa-py-wheel: skipping py-wheel native parity gate (provider remains $SPACK_PY_WHEEL_BUILD) =="
  fi

  if [[ -n "$SPACK_PY_CALVER_PREFIX" && "$SPACK_PY_CALVER_BUILD" == "native" ]]; then
    echo "== phase 2aa-py-calver: native py-calver prefix parity gate vs Spack prefix (inside insula) =="
    bazel_insula test //synthetic:py_calver_prefix_parity \
      --test_output=all --announce_rc \
      --test_env=SPACK_PY_CALVER_PREFIX="$SPACK_PY_CALVER_PREFIX" \
      --test_env=SPACK_PY_PIP_PREFIX="$SPACK_PY_PIP_PREFIX" \
      --test_env=SPACK_PY_SETUPTOOLS_PREFIX="$SPACK_PY_SETUPTOOLS_PREFIX" \
      --test_env=SPACK_PY_WHEEL_PREFIX="$SPACK_PY_WHEEL_PREFIX" \
      --test_env=SPACK_PYTHON_PREFIX="$SPACK_PYTHON_PREFIX" \
      --test_env=SPACK_PYTHON_VENV_PREFIX="$SPACK_PYTHON_VENV_PREFIX"
  elif [[ -n "$SPACK_PY_CALVER_PREFIX" ]]; then
    echo "== phase 2aa-py-calver: skipping py-calver native parity gate (provider remains $SPACK_PY_CALVER_BUILD) =="
  fi

  if [[ -n "$SPACK_PY_CERTIFI_PREFIX" && "$SPACK_PY_CERTIFI_BUILD" == "native" ]]; then
    echo "== phase 2aa-py-certifi: native py-certifi prefix parity gate vs Spack prefix (inside insula) =="
    bazel_insula test //synthetic:py_certifi_prefix_parity \
      --test_output=all --announce_rc \
      --test_env=SPACK_PY_CERTIFI_PREFIX="$SPACK_PY_CERTIFI_PREFIX" \
      --test_env=SPACK_PY_PIP_PREFIX="$SPACK_PY_PIP_PREFIX" \
      --test_env=SPACK_PY_SETUPTOOLS_PREFIX="$SPACK_PY_SETUPTOOLS_PREFIX" \
      --test_env=SPACK_PY_WHEEL_PREFIX="$SPACK_PY_WHEEL_PREFIX" \
      --test_env=SPACK_PYTHON_PREFIX="$SPACK_PYTHON_PREFIX" \
      --test_env=SPACK_PYTHON_VENV_PREFIX="$SPACK_PYTHON_VENV_PREFIX"
  elif [[ -n "$SPACK_PY_CERTIFI_PREFIX" ]]; then
    echo "== phase 2aa-py-certifi: skipping py-certifi native parity gate (provider remains $SPACK_PY_CERTIFI_BUILD) =="
  fi

  if [[ -n "$SPACK_PY_CHARSET_NORMALIZER_PREFIX" && "$SPACK_PY_CHARSET_NORMALIZER_BUILD" == "native" ]]; then
    echo "== phase 2aa-py-charset-normalizer: native py-charset-normalizer prefix parity gate vs Spack prefix (inside insula) =="
    bazel_insula test //synthetic:py_charset_normalizer_prefix_parity \
      --test_output=all --announce_rc \
      --test_env=SPACK_PY_CHARSET_NORMALIZER_PREFIX="$SPACK_PY_CHARSET_NORMALIZER_PREFIX" \
      --test_env=SPACK_PY_PIP_PREFIX="$SPACK_PY_PIP_PREFIX" \
      --test_env=SPACK_PY_SETUPTOOLS_PREFIX="$SPACK_PY_SETUPTOOLS_PREFIX" \
      --test_env=SPACK_PY_WHEEL_PREFIX="$SPACK_PY_WHEEL_PREFIX" \
      --test_env=SPACK_PYTHON_PREFIX="$SPACK_PYTHON_PREFIX" \
      --test_env=SPACK_PYTHON_VENV_PREFIX="$SPACK_PYTHON_VENV_PREFIX"
  elif [[ -n "$SPACK_PY_CHARSET_NORMALIZER_PREFIX" ]]; then
    echo "== phase 2aa-py-charset-normalizer: skipping py-charset-normalizer native parity gate (provider remains $SPACK_PY_CHARSET_NORMALIZER_BUILD) =="
  fi

  if [[ -n "$SPACK_PY_CYCLER_PREFIX" && "$SPACK_PY_CYCLER_BUILD" == "native" ]]; then
    echo "== phase 2aa-py-cycler: native py-cycler prefix parity gate vs Spack prefix (inside insula) =="
    bazel_insula test //synthetic:py_cycler_prefix_parity \
      --test_output=all --announce_rc \
      --test_env=SPACK_PY_CYCLER_PREFIX="$SPACK_PY_CYCLER_PREFIX" \
      --test_env=SPACK_PY_PIP_PREFIX="$SPACK_PY_PIP_PREFIX" \
      --test_env=SPACK_PY_SETUPTOOLS_PREFIX="$SPACK_PY_SETUPTOOLS_PREFIX" \
      --test_env=SPACK_PY_WHEEL_PREFIX="$SPACK_PY_WHEEL_PREFIX" \
      --test_env=SPACK_PYTHON_PREFIX="$SPACK_PYTHON_PREFIX" \
      --test_env=SPACK_PYTHON_VENV_PREFIX="$SPACK_PYTHON_VENV_PREFIX"
  elif [[ -n "$SPACK_PY_CYCLER_PREFIX" ]]; then
    echo "== phase 2aa-py-cycler: skipping py-cycler native parity gate (provider remains $SPACK_PY_CYCLER_BUILD) =="
  fi

  if [[ -n "$SPACK_PY_CYTHON_PREFIX" && "$SPACK_PY_CYTHON_BUILD" == "native" ]]; then
    echo "== phase 2aa-py-cython: native py-cython prefix parity gate vs Spack prefix (inside insula) =="
    bazel_insula test //synthetic:py_cython_prefix_parity \
      --test_output=all --announce_rc \
      --test_env=SPACK_PY_CYTHON_PREFIX="$SPACK_PY_CYTHON_PREFIX" \
      --test_env=SPACK_PY_PIP_PREFIX="$SPACK_PY_PIP_PREFIX" \
      --test_env=SPACK_PY_SETUPTOOLS_PREFIX="$SPACK_PY_SETUPTOOLS_PREFIX" \
      --test_env=SPACK_PY_WHEEL_PREFIX="$SPACK_PY_WHEEL_PREFIX" \
      --test_env=SPACK_PYTHON_PREFIX="$SPACK_PYTHON_PREFIX" \
      --test_env=SPACK_PYTHON_VENV_PREFIX="$SPACK_PYTHON_VENV_PREFIX"
  elif [[ -n "$SPACK_PY_CYTHON_PREFIX" ]]; then
    echo "== phase 2aa-py-cython: skipping py-cython native parity gate (provider remains $SPACK_PY_CYTHON_BUILD) =="
  fi

  if [[ -n "$SPACK_NVTX_PREFIX" && "$SPACK_NVTX_BUILD" == "native" ]]; then
    echo "== phase 2aa-nvtx: native nvtx prefix parity gate vs Spack prefix (inside insula) =="
    bazel_insula test //synthetic:nvtx_prefix_parity \
      --test_output=all --announce_rc \
      --test_env=SPACK_NVTX_PREFIX="$SPACK_NVTX_PREFIX" \
      --test_env=SPACK_PY_CYTHON_PREFIX="$SPACK_PY_CYTHON_PREFIX" \
      --test_env=SPACK_PY_PIP_PREFIX="$SPACK_PY_PIP_PREFIX" \
      --test_env=SPACK_PY_SETUPTOOLS_PREFIX="$SPACK_PY_SETUPTOOLS_PREFIX" \
      --test_env=SPACK_PY_WHEEL_PREFIX="$SPACK_PY_WHEEL_PREFIX" \
      --test_env=SPACK_PYTHON_PREFIX="$SPACK_PYTHON_PREFIX" \
      --test_env=SPACK_PYTHON_VENV_PREFIX="$SPACK_PYTHON_VENV_PREFIX"
  elif [[ -n "$SPACK_NVTX_PREFIX" ]]; then
    echo "== phase 2aa-nvtx: skipping nvtx native parity gate (provider remains $SPACK_NVTX_BUILD) =="
  fi

  if [[ -n "$SPACK_PY_FLIT_CORE_PREFIX" && "$SPACK_PY_FLIT_CORE_BUILD" == "native" ]]; then
    echo "== phase 2aa-py-flit-core: native py-flit-core prefix parity gate vs Spack prefix (inside insula) =="
    bazel_insula test //synthetic:py_flit_core_prefix_parity \
      --test_output=all --announce_rc \
      --test_env=SPACK_PY_FLIT_CORE_PREFIX="$SPACK_PY_FLIT_CORE_PREFIX" \
      --test_env=SPACK_PY_PIP_PREFIX="$SPACK_PY_PIP_PREFIX" \
      --test_env=SPACK_PY_WHEEL_PREFIX="$SPACK_PY_WHEEL_PREFIX" \
      --test_env=SPACK_PYTHON_PREFIX="$SPACK_PYTHON_PREFIX" \
      --test_env=SPACK_PYTHON_VENV_PREFIX="$SPACK_PYTHON_VENV_PREFIX"
  elif [[ -n "$SPACK_PY_FLIT_CORE_PREFIX" ]]; then
    echo "== phase 2aa-py-flit-core: skipping py-flit-core native parity gate (provider remains $SPACK_PY_FLIT_CORE_BUILD) =="
  fi

  if [[ -n "$SPACK_PY_FONTTOOLS_PREFIX" && "$SPACK_PY_FONTTOOLS_BUILD" == "native" ]]; then
    echo "== phase 2aa-py-fonttools: native py-fonttools prefix parity gate vs Spack prefix (inside insula) =="
    bazel_insula test //synthetic:py_fonttools_prefix_parity \
      --test_output=all --announce_rc \
      --test_env=SPACK_PY_FONTTOOLS_PREFIX="$SPACK_PY_FONTTOOLS_PREFIX" \
      --test_env=SPACK_PY_PIP_PREFIX="$SPACK_PY_PIP_PREFIX" \
      --test_env=SPACK_PY_SETUPTOOLS_PREFIX="$SPACK_PY_SETUPTOOLS_PREFIX" \
      --test_env=SPACK_PY_WHEEL_PREFIX="$SPACK_PY_WHEEL_PREFIX" \
      --test_env=SPACK_PYTHON_PREFIX="$SPACK_PYTHON_PREFIX" \
      --test_env=SPACK_PYTHON_VENV_PREFIX="$SPACK_PYTHON_VENV_PREFIX"
  elif [[ -n "$SPACK_PY_FONTTOOLS_PREFIX" ]]; then
    echo "== phase 2aa-py-fonttools: skipping py-fonttools native parity gate (provider remains $SPACK_PY_FONTTOOLS_BUILD) =="
  fi

  if [[ -n "$SPACK_PY_GAST_PREFIX" && "$SPACK_PY_GAST_BUILD" == "native" ]]; then
    echo "== phase 2aa-py-gast: native py-gast prefix parity gate vs Spack prefix (inside insula) =="
    bazel_insula test //synthetic:py_gast_prefix_parity \
      --test_output=all --announce_rc \
      --test_env=SPACK_PY_GAST_PREFIX="$SPACK_PY_GAST_PREFIX" \
      --test_env=SPACK_PY_PIP_PREFIX="$SPACK_PY_PIP_PREFIX" \
      --test_env=SPACK_PY_SETUPTOOLS_PREFIX="$SPACK_PY_SETUPTOOLS_PREFIX" \
      --test_env=SPACK_PY_WHEEL_PREFIX="$SPACK_PY_WHEEL_PREFIX" \
      --test_env=SPACK_PYTHON_PREFIX="$SPACK_PYTHON_PREFIX" \
      --test_env=SPACK_PYTHON_VENV_PREFIX="$SPACK_PYTHON_VENV_PREFIX"
  elif [[ -n "$SPACK_PY_GAST_PREFIX" ]]; then
    echo "== phase 2aa-py-gast: skipping py-gast native parity gate (provider remains $SPACK_PY_GAST_BUILD) =="
  fi

  if [[ -n "$SPACK_PY_BENIGET_PREFIX" && "$SPACK_PY_BENIGET_BUILD" == "native" ]]; then
    echo "== phase 2aa-py-beniget: native py-beniget prefix parity gate vs Spack prefix (inside insula) =="
    bazel_insula test //synthetic:py_beniget_prefix_parity \
      --test_output=all --announce_rc \
      --test_env=SPACK_PY_BENIGET_PREFIX="$SPACK_PY_BENIGET_PREFIX" \
      --test_env=SPACK_PY_GAST_PREFIX="$SPACK_PY_GAST_PREFIX" \
      --test_env=SPACK_PY_PIP_PREFIX="$SPACK_PY_PIP_PREFIX" \
      --test_env=SPACK_PY_SETUPTOOLS_PREFIX="$SPACK_PY_SETUPTOOLS_PREFIX" \
      --test_env=SPACK_PY_WHEEL_PREFIX="$SPACK_PY_WHEEL_PREFIX" \
      --test_env=SPACK_PYTHON_PREFIX="$SPACK_PYTHON_PREFIX" \
      --test_env=SPACK_PYTHON_VENV_PREFIX="$SPACK_PYTHON_VENV_PREFIX"
  elif [[ -n "$SPACK_PY_BENIGET_PREFIX" ]]; then
    echo "== phase 2aa-py-beniget: skipping py-beniget native parity gate (provider remains $SPACK_PY_BENIGET_BUILD) =="
  fi

  if [[ -n "$SPACK_PY_IDNA_PREFIX" && "$SPACK_PY_IDNA_BUILD" == "native" ]]; then
    echo "== phase 2aa-py-idna: native py-idna prefix parity gate vs Spack prefix (inside insula) =="
    bazel_insula test //synthetic:py_idna_prefix_parity \
      --test_output=all --announce_rc \
      --test_env=SPACK_PY_IDNA_PREFIX="$SPACK_PY_IDNA_PREFIX" \
      --test_env=SPACK_PY_FLIT_CORE_PREFIX="$SPACK_PY_FLIT_CORE_PREFIX" \
      --test_env=SPACK_PY_PIP_PREFIX="$SPACK_PY_PIP_PREFIX" \
      --test_env=SPACK_PY_WHEEL_PREFIX="$SPACK_PY_WHEEL_PREFIX" \
      --test_env=SPACK_PYTHON_PREFIX="$SPACK_PYTHON_PREFIX" \
      --test_env=SPACK_PYTHON_VENV_PREFIX="$SPACK_PYTHON_VENV_PREFIX"
  elif [[ -n "$SPACK_PY_IDNA_PREFIX" ]]; then
    echo "== phase 2aa-py-idna: skipping py-idna native parity gate (provider remains $SPACK_PY_IDNA_BUILD) =="
  fi

  if [[ -n "$SPACK_PY_MARKUPSAFE_PREFIX" && "$SPACK_PY_MARKUPSAFE_BUILD" == "native" ]]; then
    echo "== phase 2aa-py-markupsafe: native py-markupsafe prefix parity gate vs Spack prefix (inside insula) =="
    bazel_insula test //synthetic:py_markupsafe_prefix_parity \
      --test_output=all --announce_rc \
      --test_env=SPACK_PY_MARKUPSAFE_PREFIX="$SPACK_PY_MARKUPSAFE_PREFIX" \
      --test_env=SPACK_PY_PIP_PREFIX="$SPACK_PY_PIP_PREFIX" \
      --test_env=SPACK_PY_SETUPTOOLS_PREFIX="$SPACK_PY_SETUPTOOLS_PREFIX" \
      --test_env=SPACK_PY_WHEEL_PREFIX="$SPACK_PY_WHEEL_PREFIX" \
      --test_env=SPACK_PYTHON_PREFIX="$SPACK_PYTHON_PREFIX" \
      --test_env=SPACK_PYTHON_VENV_PREFIX="$SPACK_PYTHON_VENV_PREFIX"
  elif [[ -n "$SPACK_PY_MARKUPSAFE_PREFIX" ]]; then
    echo "== phase 2aa-py-markupsafe: skipping py-markupsafe native parity gate (provider remains $SPACK_PY_MARKUPSAFE_BUILD) =="
  fi

  if [[ -n "$SPACK_PY_JINJA2_PREFIX" && "$SPACK_PY_JINJA2_BUILD" == "native" ]]; then
    echo "== phase 2aa-py-jinja2: native py-jinja2 prefix parity gate vs Spack prefix (inside insula) =="
    bazel_insula test //synthetic:py_jinja2_prefix_parity \
      --test_output=all --announce_rc \
      --test_env=SPACK_PY_JINJA2_PREFIX="$SPACK_PY_JINJA2_PREFIX" \
      --test_env=SPACK_PY_FLIT_CORE_PREFIX="$SPACK_PY_FLIT_CORE_PREFIX" \
      --test_env=SPACK_PY_MARKUPSAFE_PREFIX="$SPACK_PY_MARKUPSAFE_PREFIX" \
      --test_env=SPACK_PY_PIP_PREFIX="$SPACK_PY_PIP_PREFIX" \
      --test_env=SPACK_PY_WHEEL_PREFIX="$SPACK_PY_WHEEL_PREFIX" \
      --test_env=SPACK_PYTHON_PREFIX="$SPACK_PYTHON_PREFIX" \
      --test_env=SPACK_PYTHON_VENV_PREFIX="$SPACK_PYTHON_VENV_PREFIX"
  elif [[ -n "$SPACK_PY_JINJA2_PREFIX" ]]; then
    echo "== phase 2aa-py-jinja2: skipping py-jinja2 native parity gate (provider remains $SPACK_PY_JINJA2_BUILD) =="
  fi

  if [[ -n "$SPACK_PY_MPMATH_PREFIX" && "$SPACK_PY_MPMATH_BUILD" == "native" ]]; then
    echo "== phase 2aa-py-mpmath: native py-mpmath prefix parity gate vs Spack prefix (inside insula) =="
    bazel_insula test //synthetic:py_mpmath_prefix_parity \
      --test_output=all --announce_rc \
      --test_env=SPACK_PY_MPMATH_PREFIX="$SPACK_PY_MPMATH_PREFIX" \
      --test_env=SPACK_PY_PIP_PREFIX="$SPACK_PY_PIP_PREFIX" \
      --test_env=SPACK_PY_SETUPTOOLS_PREFIX="$SPACK_PY_SETUPTOOLS_PREFIX" \
      --test_env=SPACK_PY_WHEEL_PREFIX="$SPACK_PY_WHEEL_PREFIX" \
      --test_env=SPACK_PYTHON_PREFIX="$SPACK_PYTHON_PREFIX" \
      --test_env=SPACK_PYTHON_VENV_PREFIX="$SPACK_PYTHON_VENV_PREFIX"
  elif [[ -n "$SPACK_PY_MPMATH_PREFIX" ]]; then
    echo "== phase 2aa-py-mpmath: skipping py-mpmath native parity gate (provider remains $SPACK_PY_MPMATH_BUILD) =="
  fi

  if [[ -n "$SPACK_PY_NETWORKX_PREFIX" && "$SPACK_PY_NETWORKX_BUILD" == "native" ]]; then
    echo "== phase 2aa-py-networkx: native py-networkx prefix parity gate vs Spack prefix (inside insula) =="
    bazel_insula test //synthetic:py_networkx_prefix_parity \
      --test_output=all --announce_rc \
      --test_env=SPACK_PY_NETWORKX_PREFIX="$SPACK_PY_NETWORKX_PREFIX" \
      --test_env=SPACK_PY_PIP_PREFIX="$SPACK_PY_PIP_PREFIX" \
      --test_env=SPACK_PY_SETUPTOOLS_PREFIX="$SPACK_PY_SETUPTOOLS_PREFIX" \
      --test_env=SPACK_PY_WHEEL_PREFIX="$SPACK_PY_WHEEL_PREFIX" \
      --test_env=SPACK_PYTHON_PREFIX="$SPACK_PYTHON_PREFIX" \
      --test_env=SPACK_PYTHON_VENV_PREFIX="$SPACK_PYTHON_VENV_PREFIX"
  elif [[ -n "$SPACK_PY_NETWORKX_PREFIX" ]]; then
    echo "== phase 2aa-py-networkx: skipping py-networkx native parity gate (provider remains $SPACK_PY_NETWORKX_BUILD) =="
  fi

  if [[ -n "$SPACK_PY_PACKAGING_PREFIX" && "$SPACK_PY_PACKAGING_BUILD" == "native" ]]; then
    echo "== phase 2aa-py-packaging: native py-packaging prefix parity gate vs Spack prefix (inside insula) =="
    bazel_insula test //synthetic:py_packaging_prefix_parity \
      --test_output=all --announce_rc \
      --test_env=SPACK_PY_PACKAGING_PREFIX="$SPACK_PY_PACKAGING_PREFIX" \
      --test_env=SPACK_PY_FLIT_CORE_PREFIX="$SPACK_PY_FLIT_CORE_PREFIX" \
      --test_env=SPACK_PY_PIP_PREFIX="$SPACK_PY_PIP_PREFIX" \
      --test_env=SPACK_PY_WHEEL_PREFIX="$SPACK_PY_WHEEL_PREFIX" \
      --test_env=SPACK_PYTHON_PREFIX="$SPACK_PYTHON_PREFIX" \
      --test_env=SPACK_PYTHON_VENV_PREFIX="$SPACK_PYTHON_VENV_PREFIX"
  elif [[ -n "$SPACK_PY_PACKAGING_PREFIX" ]]; then
    echo "== phase 2aa-py-packaging: skipping py-packaging native parity gate (provider remains $SPACK_PY_PACKAGING_BUILD) =="
  fi

  if [[ -n "$SPACK_PY_PATHSPEC_PREFIX" && "$SPACK_PY_PATHSPEC_BUILD" == "native" ]]; then
    echo "== phase 2aa-py-pathspec: native py-pathspec prefix parity gate vs Spack prefix (inside insula) =="
    bazel_insula test //synthetic:py_pathspec_prefix_parity \
      --test_output=all --announce_rc \
      --test_env=SPACK_PY_PATHSPEC_PREFIX="$SPACK_PY_PATHSPEC_PREFIX" \
      --test_env=SPACK_PY_FLIT_CORE_PREFIX="$SPACK_PY_FLIT_CORE_PREFIX" \
      --test_env=SPACK_PY_PIP_PREFIX="$SPACK_PY_PIP_PREFIX" \
      --test_env=SPACK_PY_WHEEL_PREFIX="$SPACK_PY_WHEEL_PREFIX" \
      --test_env=SPACK_PYTHON_PREFIX="$SPACK_PYTHON_PREFIX" \
      --test_env=SPACK_PYTHON_VENV_PREFIX="$SPACK_PYTHON_VENV_PREFIX"
  elif [[ -n "$SPACK_PY_PATHSPEC_PREFIX" ]]; then
    echo "== phase 2aa-py-pathspec: skipping py-pathspec native parity gate (provider remains $SPACK_PY_PATHSPEC_BUILD) =="
  fi

  if [[ -n "$SPACK_PY_PLY_PREFIX" && "$SPACK_PY_PLY_BUILD" == "native" ]]; then
    echo "== phase 2aa-py-ply: native py-ply prefix parity gate vs Spack prefix (inside insula) =="
    bazel_insula test //synthetic:py_ply_prefix_parity \
      --test_output=all --announce_rc \
      --test_env=SPACK_PY_PLY_PREFIX="$SPACK_PY_PLY_PREFIX" \
      --test_env=SPACK_PY_PIP_PREFIX="$SPACK_PY_PIP_PREFIX" \
      --test_env=SPACK_PY_SETUPTOOLS_PREFIX="$SPACK_PY_SETUPTOOLS_PREFIX" \
      --test_env=SPACK_PY_WHEEL_PREFIX="$SPACK_PY_WHEEL_PREFIX" \
      --test_env=SPACK_PYTHON_PREFIX="$SPACK_PYTHON_PREFIX" \
      --test_env=SPACK_PYTHON_VENV_PREFIX="$SPACK_PYTHON_VENV_PREFIX"
  elif [[ -n "$SPACK_PY_PLY_PREFIX" ]]; then
    echo "== phase 2aa-py-ply: skipping py-ply native parity gate (provider remains $SPACK_PY_PLY_BUILD) =="
  fi

  if [[ -n "$SPACK_PY_PYPARSING_PREFIX" && "$SPACK_PY_PYPARSING_BUILD" == "native" ]]; then
    echo "== phase 2aa-py-pyparsing: native py-pyparsing prefix parity gate vs Spack prefix (inside insula) =="
    bazel_insula test //synthetic:py_pyparsing_prefix_parity \
      --test_output=all --announce_rc \
      --test_env=SPACK_PY_PYPARSING_PREFIX="$SPACK_PY_PYPARSING_PREFIX" \
      --test_env=SPACK_PY_FLIT_CORE_PREFIX="$SPACK_PY_FLIT_CORE_PREFIX" \
      --test_env=SPACK_PY_PIP_PREFIX="$SPACK_PY_PIP_PREFIX" \
      --test_env=SPACK_PY_WHEEL_PREFIX="$SPACK_PY_WHEEL_PREFIX" \
      --test_env=SPACK_PYTHON_PREFIX="$SPACK_PYTHON_PREFIX" \
      --test_env=SPACK_PYTHON_VENV_PREFIX="$SPACK_PYTHON_VENV_PREFIX"
  elif [[ -n "$SPACK_PY_PYPARSING_PREFIX" ]]; then
    echo "== phase 2aa-py-pyparsing: skipping py-pyparsing native parity gate (provider remains $SPACK_PY_PYPARSING_BUILD) =="
  fi

  if [[ -n "$SPACK_PY_PYPROJECT_HOOKS_PREFIX" && "$SPACK_PY_PYPROJECT_HOOKS_BUILD" == "native" ]]; then
    echo "== phase 2aa-py-pyproject-hooks: native py-pyproject-hooks prefix parity gate vs Spack prefix (inside insula) =="
    bazel_insula test //synthetic:py_pyproject_hooks_prefix_parity \
      --test_output=all --announce_rc \
      --test_env=SPACK_PY_PYPROJECT_HOOKS_PREFIX="$SPACK_PY_PYPROJECT_HOOKS_PREFIX" \
      --test_env=SPACK_PY_FLIT_CORE_PREFIX="$SPACK_PY_FLIT_CORE_PREFIX" \
      --test_env=SPACK_PY_PIP_PREFIX="$SPACK_PY_PIP_PREFIX" \
      --test_env=SPACK_PY_WHEEL_PREFIX="$SPACK_PY_WHEEL_PREFIX" \
      --test_env=SPACK_PYTHON_PREFIX="$SPACK_PYTHON_PREFIX" \
      --test_env=SPACK_PYTHON_VENV_PREFIX="$SPACK_PYTHON_VENV_PREFIX"
  elif [[ -n "$SPACK_PY_PYPROJECT_HOOKS_PREFIX" ]]; then
    echo "== phase 2aa-py-pyproject-hooks: skipping py-pyproject-hooks native parity gate (provider remains $SPACK_PY_PYPROJECT_HOOKS_BUILD) =="
  fi

  if [[ -n "$SPACK_PY_BUILD_PREFIX" && "$SPACK_PY_BUILD_BUILD" == "native" ]]; then
    echo "== phase 2aa-py-build: native py-build prefix parity gate vs Spack prefix (inside insula) =="
    bazel_insula test //synthetic:py_build_prefix_parity \
      --test_output=all --announce_rc \
      --test_env=SPACK_PY_BUILD_PREFIX="$SPACK_PY_BUILD_PREFIX" \
      --test_env=SPACK_PY_FLIT_CORE_PREFIX="$SPACK_PY_FLIT_CORE_PREFIX" \
      --test_env=SPACK_PY_PACKAGING_PREFIX="$SPACK_PY_PACKAGING_PREFIX" \
      --test_env=SPACK_PY_PIP_PREFIX="$SPACK_PY_PIP_PREFIX" \
      --test_env=SPACK_PY_PYPROJECT_HOOKS_PREFIX="$SPACK_PY_PYPROJECT_HOOKS_PREFIX" \
      --test_env=SPACK_PY_WHEEL_PREFIX="$SPACK_PY_WHEEL_PREFIX" \
      --test_env=SPACK_PYTHON_PREFIX="$SPACK_PYTHON_PREFIX" \
      --test_env=SPACK_PYTHON_VENV_PREFIX="$SPACK_PYTHON_VENV_PREFIX"
  elif [[ -n "$SPACK_PY_BUILD_PREFIX" ]]; then
    echo "== phase 2aa-py-build: skipping py-build native parity gate (provider remains $SPACK_PY_BUILD_BUILD) =="
  fi

  if [[ -n "$SPACK_PY_PYPROJECT_METADATA_PREFIX" && "$SPACK_PY_PYPROJECT_METADATA_BUILD" == "native" ]]; then
    echo "== phase 2aa-py-pyproject-metadata: native py-pyproject-metadata prefix parity gate vs Spack prefix (inside insula) =="
    bazel_insula test //synthetic:py_pyproject_metadata_prefix_parity \
      --test_output=all --announce_rc \
      --test_env=SPACK_PY_PYPROJECT_METADATA_PREFIX="$SPACK_PY_PYPROJECT_METADATA_PREFIX" \
      --test_env=SPACK_PY_FLIT_CORE_PREFIX="$SPACK_PY_FLIT_CORE_PREFIX" \
      --test_env=SPACK_PY_PACKAGING_PREFIX="$SPACK_PY_PACKAGING_PREFIX" \
      --test_env=SPACK_PY_PIP_PREFIX="$SPACK_PY_PIP_PREFIX" \
      --test_env=SPACK_PY_WHEEL_PREFIX="$SPACK_PY_WHEEL_PREFIX" \
      --test_env=SPACK_PYTHON_PREFIX="$SPACK_PYTHON_PREFIX" \
      --test_env=SPACK_PYTHON_VENV_PREFIX="$SPACK_PYTHON_VENV_PREFIX"
  elif [[ -n "$SPACK_PY_PYPROJECT_METADATA_PREFIX" ]]; then
    echo "== phase 2aa-py-pyproject-metadata: skipping py-pyproject-metadata native parity gate (provider remains $SPACK_PY_PYPROJECT_METADATA_BUILD) =="
  fi

  if [[ -n "$SPACK_PY_MESON_PYTHON_PREFIX" && "$SPACK_PY_MESON_PYTHON_BUILD" == "native" ]]; then
    echo "== phase 2aa-py-meson-python: native py-meson-python prefix parity gate vs Spack prefix (inside insula) =="
    bazel_insula test //synthetic:py_meson_python_prefix_parity \
      --test_output=all --announce_rc \
      --test_env=SPACK_PY_MESON_PYTHON_PREFIX="$SPACK_PY_MESON_PYTHON_PREFIX" \
      --test_env=SPACK_MESON_PREFIX="$SPACK_MESON_PREFIX" \
      --test_env=SPACK_NINJA_PREFIX="$SPACK_NINJA_PREFIX" \
      --test_env=SPACK_PY_PACKAGING_PREFIX="$SPACK_PY_PACKAGING_PREFIX" \
      --test_env=SPACK_PY_PIP_PREFIX="$SPACK_PY_PIP_PREFIX" \
      --test_env=SPACK_PY_PYPROJECT_METADATA_PREFIX="$SPACK_PY_PYPROJECT_METADATA_PREFIX" \
      --test_env=SPACK_PY_WHEEL_PREFIX="$SPACK_PY_WHEEL_PREFIX" \
      --test_env=SPACK_PYTHON_PREFIX="$SPACK_PYTHON_PREFIX" \
      --test_env=SPACK_PYTHON_VENV_PREFIX="$SPACK_PYTHON_VENV_PREFIX"
  elif [[ -n "$SPACK_PY_MESON_PYTHON_PREFIX" ]]; then
    echo "== phase 2aa-py-meson-python: skipping py-meson-python native parity gate (provider remains $SPACK_PY_MESON_PYTHON_BUILD) =="
  fi

  if [[ -n "$SPACK_PY_NUMPY_PREFIX" && "$SPACK_PY_NUMPY_BUILD" == "native" ]]; then
    echo "== phase 2aa-py-numpy: native py-numpy prefix parity gate vs Spack prefix (inside insula) =="
    bazel_insula test //synthetic:py_numpy_prefix_parity \
      --test_output=all --announce_rc \
      --test_env=SPACK_PY_NUMPY_PREFIX="$SPACK_PY_NUMPY_PREFIX" \
      --test_env=SPACK_OPENBLAS_PREFIX="$SPACK_OPENBLAS_PREFIX" \
      --test_env=SPACK_PKGCONF_PREFIX="$SPACK_PKGCONF_PREFIX" \
      --test_env=SPACK_MESON_PREFIX="$SPACK_MESON_PREFIX" \
      --test_env=SPACK_NINJA_PREFIX="$SPACK_NINJA_PREFIX" \
      --test_env=SPACK_PY_CYTHON_PREFIX="$SPACK_PY_CYTHON_PREFIX" \
      --test_env=SPACK_PY_MESON_PYTHON_PREFIX="$SPACK_PY_MESON_PYTHON_PREFIX" \
      --test_env=SPACK_PY_PACKAGING_PREFIX="$SPACK_PY_PACKAGING_PREFIX" \
      --test_env=SPACK_PY_PIP_PREFIX="$SPACK_PY_PIP_PREFIX" \
      --test_env=SPACK_PY_PYPROJECT_METADATA_PREFIX="$SPACK_PY_PYPROJECT_METADATA_PREFIX" \
      --test_env=SPACK_PY_WHEEL_PREFIX="$SPACK_PY_WHEEL_PREFIX" \
      --test_env=SPACK_PYTHON_PREFIX="$SPACK_PYTHON_PREFIX" \
      --test_env=SPACK_PYTHON_VENV_PREFIX="$SPACK_PYTHON_VENV_PREFIX"
  elif [[ -n "$SPACK_PY_NUMPY_PREFIX" ]]; then
    echo "== phase 2aa-py-numpy: skipping py-numpy native parity gate (provider remains $SPACK_PY_NUMPY_BUILD) =="
  fi

  if [[ -n "$SPACK_PY_ML_DTYPES_PREFIX" && "$SPACK_PY_ML_DTYPES_BUILD" == "native" ]]; then
    echo "== phase 2aa-py-ml-dtypes: native py-ml-dtypes prefix parity gate vs Spack prefix (inside insula) =="
    bazel_insula test //synthetic:py_ml_dtypes_prefix_parity \
      --test_output=all --announce_rc \
      --test_env=SPACK_PY_ML_DTYPES_PREFIX="$SPACK_PY_ML_DTYPES_PREFIX" \
      --test_env=SPACK_OPENBLAS_PREFIX="$SPACK_OPENBLAS_PREFIX" \
      --test_env=SPACK_PY_NUMPY_PREFIX="$SPACK_PY_NUMPY_PREFIX" \
      --test_env=SPACK_PY_PIP_PREFIX="$SPACK_PY_PIP_PREFIX" \
      --test_env=SPACK_PY_SETUPTOOLS_PREFIX="$SPACK_PY_SETUPTOOLS_PREFIX" \
      --test_env=SPACK_PY_WHEEL_PREFIX="$SPACK_PY_WHEEL_PREFIX" \
      --test_env=SPACK_PYTHON_PREFIX="$SPACK_PYTHON_PREFIX" \
      --test_env=SPACK_PYTHON_VENV_PREFIX="$SPACK_PYTHON_VENV_PREFIX"
  elif [[ -n "$SPACK_PY_ML_DTYPES_PREFIX" ]]; then
    echo "== phase 2aa-py-ml-dtypes: skipping py-ml-dtypes native parity gate (provider remains $SPACK_PY_ML_DTYPES_BUILD) =="
  fi

  if [[ -n "$SPACK_PY_PYBIND11_PREFIX" && "$SPACK_PY_PYBIND11_BUILD" == "native" ]]; then
    echo "== phase 2aa-py-pybind11: native py-pybind11 prefix parity gate vs Spack prefix (inside insula) =="
    bazel_insula test //synthetic:py_pybind11_prefix_parity \
      --test_output=all --announce_rc \
      --test_env=SPACK_PY_PYBIND11_PREFIX="$SPACK_PY_PYBIND11_PREFIX" \
      --test_env=SPACK_CMAKE_PREFIX="$SPACK_CMAKE_PREFIX" \
      --test_env=SPACK_NINJA_PREFIX="$SPACK_NINJA_PREFIX" \
      --test_env=SPACK_PY_PACKAGING_PREFIX="$SPACK_PY_PACKAGING_PREFIX" \
      --test_env=SPACK_PY_PATHSPEC_PREFIX="$SPACK_PY_PATHSPEC_PREFIX" \
      --test_env=SPACK_PY_PIP_PREFIX="$SPACK_PY_PIP_PREFIX" \
      --test_env=SPACK_PY_SCIKIT_BUILD_CORE_PREFIX="$SPACK_PY_SCIKIT_BUILD_CORE_PREFIX" \
      --test_env=SPACK_PY_SETUPTOOLS_PREFIX="$SPACK_PY_SETUPTOOLS_PREFIX" \
      --test_env=SPACK_PY_WHEEL_PREFIX="$SPACK_PY_WHEEL_PREFIX" \
      --test_env=SPACK_PYTHON_PREFIX="$SPACK_PYTHON_PREFIX" \
      --test_env=SPACK_PYTHON_VENV_PREFIX="$SPACK_PYTHON_VENV_PREFIX"
  elif [[ -n "$SPACK_PY_PYBIND11_PREFIX" ]]; then
    echo "== phase 2aa-py-pybind11: skipping py-pybind11 native parity gate (provider remains $SPACK_PY_PYBIND11_BUILD) =="
  fi

  if [[ -n "$SPACK_PY_PYYAML_PREFIX" && "$SPACK_PY_PYYAML_BUILD" == "native" ]]; then
    echo "== phase 2aa-py-pyyaml: native py-pyyaml prefix parity gate vs Spack prefix (inside insula) =="
    bazel_insula test //synthetic:py_pyyaml_prefix_parity \
      --test_output=all --announce_rc \
      --test_env=SPACK_PY_PYYAML_PREFIX="$SPACK_PY_PYYAML_PREFIX" \
      --test_env=SPACK_LIBYAML_PREFIX="$SPACK_LIBYAML_PREFIX" \
      --test_env=SPACK_PY_CYTHON_PREFIX="$SPACK_PY_CYTHON_PREFIX" \
      --test_env=SPACK_PY_PIP_PREFIX="$SPACK_PY_PIP_PREFIX" \
      --test_env=SPACK_PY_SETUPTOOLS_PREFIX="$SPACK_PY_SETUPTOOLS_PREFIX" \
      --test_env=SPACK_PY_WHEEL_PREFIX="$SPACK_PY_WHEEL_PREFIX" \
      --test_env=SPACK_PYTHON_PREFIX="$SPACK_PYTHON_PREFIX" \
      --test_env=SPACK_PYTHON_VENV_PREFIX="$SPACK_PYTHON_VENV_PREFIX"
  elif [[ -n "$SPACK_PY_PYYAML_PREFIX" ]]; then
    echo "== phase 2aa-py-pyyaml: skipping py-pyyaml native parity gate (provider remains $SPACK_PY_PYYAML_BUILD) =="
  fi

  if [[ -n "$SPACK_PY_SETUPTOOLS_SCM_PREFIX" && "$SPACK_PY_SETUPTOOLS_SCM_BUILD" == "native" ]]; then
    echo "== phase 2aa-py-setuptools-scm: native py-setuptools-scm prefix parity gate vs Spack prefix (inside insula) =="
    bazel_insula test //synthetic:py_setuptools_scm_prefix_parity \
      --test_output=all --announce_rc \
      --test_env=SPACK_PY_SETUPTOOLS_SCM_PREFIX="$SPACK_PY_SETUPTOOLS_SCM_PREFIX" \
      --test_env=SPACK_GIT_PREFIX="$SPACK_GIT_PREFIX" \
      --test_env=SPACK_PY_PACKAGING_PREFIX="$SPACK_PY_PACKAGING_PREFIX" \
      --test_env=SPACK_PY_PIP_PREFIX="$SPACK_PY_PIP_PREFIX" \
      --test_env=SPACK_PY_SETUPTOOLS_PREFIX="$SPACK_PY_SETUPTOOLS_PREFIX" \
      --test_env=SPACK_PY_WHEEL_PREFIX="$SPACK_PY_WHEEL_PREFIX" \
      --test_env=SPACK_PYTHON_PREFIX="$SPACK_PYTHON_PREFIX" \
      --test_env=SPACK_PYTHON_VENV_PREFIX="$SPACK_PYTHON_VENV_PREFIX"
  elif [[ -n "$SPACK_PY_SETUPTOOLS_SCM_PREFIX" ]]; then
    echo "== phase 2aa-py-setuptools-scm: skipping py-setuptools-scm native parity gate (provider remains $SPACK_PY_SETUPTOOLS_SCM_BUILD) =="
  fi

  if [[ -n "$SPACK_PY_CPPY_PREFIX" && "$SPACK_PY_CPPY_BUILD" == "native" ]]; then
    echo "== phase 2aa-py-cppy: native py-cppy prefix parity gate vs Spack prefix (inside insula) =="
    bazel_insula test //synthetic:py_cppy_prefix_parity \
      --test_output=all --announce_rc \
      --test_env=SPACK_PY_CPPY_PREFIX="$SPACK_PY_CPPY_PREFIX" \
      --test_env=SPACK_PY_PACKAGING_PREFIX="$SPACK_PY_PACKAGING_PREFIX" \
      --test_env=SPACK_PY_PIP_PREFIX="$SPACK_PY_PIP_PREFIX" \
      --test_env=SPACK_PY_SETUPTOOLS_PREFIX="$SPACK_PY_SETUPTOOLS_PREFIX" \
      --test_env=SPACK_PY_SETUPTOOLS_SCM_PREFIX="$SPACK_PY_SETUPTOOLS_SCM_PREFIX" \
      --test_env=SPACK_PY_WHEEL_PREFIX="$SPACK_PY_WHEEL_PREFIX" \
      --test_env=SPACK_PYTHON_PREFIX="$SPACK_PYTHON_PREFIX" \
      --test_env=SPACK_PYTHON_VENV_PREFIX="$SPACK_PYTHON_VENV_PREFIX"
  elif [[ -n "$SPACK_PY_CPPY_PREFIX" ]]; then
    echo "== phase 2aa-py-cppy: skipping py-cppy native parity gate (provider remains $SPACK_PY_CPPY_BUILD) =="
  fi

  if [[ -n "$SPACK_PY_KIWISOLVER_PREFIX" && "$SPACK_PY_KIWISOLVER_BUILD" == "native" ]]; then
    echo "== phase 2aa-py-kiwisolver: native py-kiwisolver prefix parity gate vs Spack prefix (inside insula) =="
    bazel_insula test //synthetic:py_kiwisolver_prefix_parity \
      --test_output=all --announce_rc \
      --test_env=SPACK_PY_KIWISOLVER_PREFIX="$SPACK_PY_KIWISOLVER_PREFIX" \
      --test_env=SPACK_PY_CPPY_PREFIX="$SPACK_PY_CPPY_PREFIX" \
      --test_env=SPACK_PY_PACKAGING_PREFIX="$SPACK_PY_PACKAGING_PREFIX" \
      --test_env=SPACK_PY_PIP_PREFIX="$SPACK_PY_PIP_PREFIX" \
      --test_env=SPACK_PY_SETUPTOOLS_PREFIX="$SPACK_PY_SETUPTOOLS_PREFIX" \
      --test_env=SPACK_PY_SETUPTOOLS_SCM_PREFIX="$SPACK_PY_SETUPTOOLS_SCM_PREFIX" \
      --test_env=SPACK_PY_WHEEL_PREFIX="$SPACK_PY_WHEEL_PREFIX" \
      --test_env=SPACK_PYTHON_PREFIX="$SPACK_PYTHON_PREFIX" \
      --test_env=SPACK_PYTHON_VENV_PREFIX="$SPACK_PYTHON_VENV_PREFIX"
  elif [[ -n "$SPACK_PY_KIWISOLVER_PREFIX" ]]; then
    echo "== phase 2aa-py-kiwisolver: skipping py-kiwisolver native parity gate (provider remains $SPACK_PY_KIWISOLVER_BUILD) =="
  fi

  if [[ -n "$SPACK_PY_PLUGGY_PREFIX" && "$SPACK_PY_PLUGGY_BUILD" == "native" ]]; then
    echo "== phase 2aa-py-pluggy: native py-pluggy prefix parity gate vs Spack prefix (inside insula) =="
    bazel_insula test //synthetic:py_pluggy_prefix_parity \
      --test_output=all --announce_rc \
      --test_env=SPACK_PY_PLUGGY_PREFIX="$SPACK_PY_PLUGGY_PREFIX" \
      --test_env=SPACK_GIT_PREFIX="$SPACK_GIT_PREFIX" \
      --test_env=SPACK_PY_PACKAGING_PREFIX="$SPACK_PY_PACKAGING_PREFIX" \
      --test_env=SPACK_PY_PIP_PREFIX="$SPACK_PY_PIP_PREFIX" \
      --test_env=SPACK_PY_SETUPTOOLS_PREFIX="$SPACK_PY_SETUPTOOLS_PREFIX" \
      --test_env=SPACK_PY_SETUPTOOLS_SCM_PREFIX="$SPACK_PY_SETUPTOOLS_SCM_PREFIX" \
      --test_env=SPACK_PY_WHEEL_PREFIX="$SPACK_PY_WHEEL_PREFIX" \
      --test_env=SPACK_PYTHON_PREFIX="$SPACK_PYTHON_PREFIX" \
      --test_env=SPACK_PYTHON_VENV_PREFIX="$SPACK_PYTHON_VENV_PREFIX"
  elif [[ -n "$SPACK_PY_PLUGGY_PREFIX" ]]; then
    echo "== phase 2aa-py-pluggy: skipping py-pluggy native parity gate (provider remains $SPACK_PY_PLUGGY_BUILD) =="
  fi

  if [[ -n "$SPACK_PY_SIX_PREFIX" && "$SPACK_PY_SIX_BUILD" == "native" ]]; then
    echo "== phase 2aa-py-six: native py-six prefix parity gate vs Spack prefix (inside insula) =="
    bazel_insula test //synthetic:py_six_prefix_parity \
      --test_output=all --announce_rc \
      --test_env=SPACK_PY_SIX_PREFIX="$SPACK_PY_SIX_PREFIX" \
      --test_env=SPACK_PY_PIP_PREFIX="$SPACK_PY_PIP_PREFIX" \
      --test_env=SPACK_PY_SETUPTOOLS_PREFIX="$SPACK_PY_SETUPTOOLS_PREFIX" \
      --test_env=SPACK_PY_WHEEL_PREFIX="$SPACK_PY_WHEEL_PREFIX" \
      --test_env=SPACK_PYTHON_PREFIX="$SPACK_PYTHON_PREFIX" \
      --test_env=SPACK_PYTHON_VENV_PREFIX="$SPACK_PYTHON_VENV_PREFIX"
  elif [[ -n "$SPACK_PY_SIX_PREFIX" ]]; then
    echo "== phase 2aa-py-six: skipping py-six native parity gate (provider remains $SPACK_PY_SIX_BUILD) =="
  fi

  if [[ -n "$SPACK_PY_PROTOBUF_PREFIX" && "$SPACK_PY_PROTOBUF_BUILD" == "native" ]]; then
    echo "== phase 2aa-py-protobuf: native py-protobuf prefix/ABI parity gate vs Spack prefix (inside insula) =="
    bazel_insula test //synthetic:py_protobuf_prefix_parity \
      --test_output=all --announce_rc \
      --test_env=SPACK_PY_PROTOBUF_PREFIX="$SPACK_PY_PROTOBUF_PREFIX" \
      --test_env=SPACK_PROTOBUF_PREFIX="$SPACK_PROTOBUF_PREFIX" \
      --test_env=SPACK_PY_PIP_PREFIX="$SPACK_PY_PIP_PREFIX" \
      --test_env=SPACK_PY_SETUPTOOLS_PREFIX="$SPACK_PY_SETUPTOOLS_PREFIX" \
      --test_env=SPACK_PY_WHEEL_PREFIX="$SPACK_PY_WHEEL_PREFIX" \
      --test_env=SPACK_PYTHON_PREFIX="$SPACK_PYTHON_PREFIX" \
      --test_env=SPACK_PYTHON_VENV_PREFIX="$SPACK_PYTHON_VENV_PREFIX"
  elif [[ -n "$SPACK_PY_PROTOBUF_PREFIX" ]]; then
    echo "== phase 2aa-py-protobuf: skipping py-protobuf native parity gate (provider remains $SPACK_PY_PROTOBUF_BUILD) =="
  fi

  if [[ -n "$SPACK_PY_PYTHON_DATEUTIL_PREFIX" && "$SPACK_PY_PYTHON_DATEUTIL_BUILD" == "native" ]]; then
    echo "== phase 2aa-py-python-dateutil: native py-python-dateutil prefix parity gate vs Spack prefix (inside insula) =="
    bazel_insula test //synthetic:py_python_dateutil_prefix_parity \
      --test_output=all --announce_rc \
      --test_env=SPACK_PY_PYTHON_DATEUTIL_PREFIX="$SPACK_PY_PYTHON_DATEUTIL_PREFIX" \
      --test_env=SPACK_PY_PIP_PREFIX="$SPACK_PY_PIP_PREFIX" \
      --test_env=SPACK_PY_SETUPTOOLS_PREFIX="$SPACK_PY_SETUPTOOLS_PREFIX" \
      --test_env=SPACK_PY_SETUPTOOLS_SCM_PREFIX="$SPACK_PY_SETUPTOOLS_SCM_PREFIX" \
      --test_env=SPACK_PY_SIX_PREFIX="$SPACK_PY_SIX_PREFIX" \
      --test_env=SPACK_PY_WHEEL_PREFIX="$SPACK_PY_WHEEL_PREFIX" \
      --test_env=SPACK_PYTHON_PREFIX="$SPACK_PYTHON_PREFIX" \
      --test_env=SPACK_PYTHON_VENV_PREFIX="$SPACK_PYTHON_VENV_PREFIX"
  elif [[ -n "$SPACK_PY_PYTHON_DATEUTIL_PREFIX" ]]; then
    echo "== phase 2aa-py-python-dateutil: skipping py-python-dateutil native parity gate (provider remains $SPACK_PY_PYTHON_DATEUTIL_BUILD) =="
  fi

  if [[ -n "$SPACK_PY_SYMPY_PREFIX" && "$SPACK_PY_SYMPY_BUILD" == "native" ]]; then
    echo "== phase 2aa-py-sympy: native py-sympy prefix parity gate vs Spack prefix (inside insula) =="
    bazel_insula test //synthetic:py_sympy_prefix_parity \
      --test_output=all --announce_rc \
      --test_env=SPACK_PY_SYMPY_PREFIX="$SPACK_PY_SYMPY_PREFIX" \
      --test_env=SPACK_PY_MPMATH_PREFIX="$SPACK_PY_MPMATH_PREFIX" \
      --test_env=SPACK_PY_PIP_PREFIX="$SPACK_PY_PIP_PREFIX" \
      --test_env=SPACK_PY_SETUPTOOLS_PREFIX="$SPACK_PY_SETUPTOOLS_PREFIX" \
      --test_env=SPACK_PY_WHEEL_PREFIX="$SPACK_PY_WHEEL_PREFIX" \
      --test_env=SPACK_PYTHON_PREFIX="$SPACK_PYTHON_PREFIX" \
      --test_env=SPACK_PYTHON_VENV_PREFIX="$SPACK_PYTHON_VENV_PREFIX"
  elif [[ -n "$SPACK_PY_SYMPY_PREFIX" ]]; then
    echo "== phase 2aa-py-sympy: skipping py-sympy native parity gate (provider remains $SPACK_PY_SYMPY_BUILD) =="
  fi

  if [[ -n "$SPACK_PY_TQDM_PREFIX" && "$SPACK_PY_TQDM_BUILD" == "native" ]]; then
    echo "== phase 2aa-py-tqdm: native py-tqdm prefix parity gate vs Spack prefix (inside insula) =="
    bazel_insula test //synthetic:py_tqdm_prefix_parity \
      --test_output=all --announce_rc \
      --test_env=SPACK_PY_TQDM_PREFIX="$SPACK_PY_TQDM_PREFIX" \
      --test_env=SPACK_GIT_PREFIX="$SPACK_GIT_PREFIX" \
      --test_env=SPACK_PY_PACKAGING_PREFIX="$SPACK_PY_PACKAGING_PREFIX" \
      --test_env=SPACK_PY_PIP_PREFIX="$SPACK_PY_PIP_PREFIX" \
      --test_env=SPACK_PY_SETUPTOOLS_PREFIX="$SPACK_PY_SETUPTOOLS_PREFIX" \
      --test_env=SPACK_PY_SETUPTOOLS_SCM_PREFIX="$SPACK_PY_SETUPTOOLS_SCM_PREFIX" \
      --test_env=SPACK_PY_WHEEL_PREFIX="$SPACK_PY_WHEEL_PREFIX" \
      --test_env=SPACK_PYTHON_PREFIX="$SPACK_PYTHON_PREFIX" \
      --test_env=SPACK_PYTHON_VENV_PREFIX="$SPACK_PYTHON_VENV_PREFIX"
  elif [[ -n "$SPACK_PY_TQDM_PREFIX" ]]; then
    echo "== phase 2aa-py-tqdm: skipping py-tqdm native parity gate (provider remains $SPACK_PY_TQDM_BUILD) =="
  fi

  if [[ -n "$SPACK_PY_TROVE_CLASSIFIERS_PREFIX" && "$SPACK_PY_TROVE_CLASSIFIERS_BUILD" == "native" ]]; then
    echo "== phase 2aa-py-trove-classifiers: native py-trove-classifiers prefix parity gate vs Spack prefix (inside insula) =="
    bazel_insula test //synthetic:py_trove_classifiers_prefix_parity \
      --test_output=all --announce_rc \
      --test_env=SPACK_PY_TROVE_CLASSIFIERS_PREFIX="$SPACK_PY_TROVE_CLASSIFIERS_PREFIX" \
      --test_env=SPACK_PY_CALVER_PREFIX="$SPACK_PY_CALVER_PREFIX" \
      --test_env=SPACK_PY_PIP_PREFIX="$SPACK_PY_PIP_PREFIX" \
      --test_env=SPACK_PY_SETUPTOOLS_PREFIX="$SPACK_PY_SETUPTOOLS_PREFIX" \
      --test_env=SPACK_PY_WHEEL_PREFIX="$SPACK_PY_WHEEL_PREFIX" \
      --test_env=SPACK_PYTHON_PREFIX="$SPACK_PYTHON_PREFIX" \
      --test_env=SPACK_PYTHON_VENV_PREFIX="$SPACK_PYTHON_VENV_PREFIX"
  elif [[ -n "$SPACK_PY_TROVE_CLASSIFIERS_PREFIX" ]]; then
    echo "== phase 2aa-py-trove-classifiers: skipping py-trove-classifiers native parity gate (provider remains $SPACK_PY_TROVE_CLASSIFIERS_BUILD) =="
  fi

  if [[ -n "$SPACK_PY_HATCHLING_PREFIX" && "$SPACK_PY_HATCHLING_BUILD" == "native" ]]; then
    echo "== phase 2aa-py-hatchling: native py-hatchling prefix parity gate vs Spack prefix (inside insula) =="
    bazel_insula test //synthetic:py_hatchling_prefix_parity \
      --test_output=all --announce_rc \
      --test_env=SPACK_PY_HATCHLING_PREFIX="$SPACK_PY_HATCHLING_PREFIX" \
      --test_env=SPACK_PY_PACKAGING_PREFIX="$SPACK_PY_PACKAGING_PREFIX" \
      --test_env=SPACK_PY_PATHSPEC_PREFIX="$SPACK_PY_PATHSPEC_PREFIX" \
      --test_env=SPACK_PY_PIP_PREFIX="$SPACK_PY_PIP_PREFIX" \
      --test_env=SPACK_PY_PLUGGY_PREFIX="$SPACK_PY_PLUGGY_PREFIX" \
      --test_env=SPACK_PY_TROVE_CLASSIFIERS_PREFIX="$SPACK_PY_TROVE_CLASSIFIERS_PREFIX" \
      --test_env=SPACK_PY_WHEEL_PREFIX="$SPACK_PY_WHEEL_PREFIX" \
      --test_env=SPACK_PYTHON_PREFIX="$SPACK_PYTHON_PREFIX" \
      --test_env=SPACK_PYTHON_VENV_PREFIX="$SPACK_PYTHON_VENV_PREFIX"
  elif [[ -n "$SPACK_PY_HATCHLING_PREFIX" ]]; then
    echo "== phase 2aa-py-hatchling: skipping py-hatchling native parity gate (provider remains $SPACK_PY_HATCHLING_BUILD) =="
  fi

  if [[ -n "$SPACK_PY_HATCH_FANCY_PYPI_README_PREFIX" && "$SPACK_PY_HATCH_FANCY_PYPI_README_BUILD" == "native" ]]; then
    echo "== phase 2aa-py-hatch-fancy-pypi-readme: native py-hatch-fancy-pypi-readme prefix parity gate vs Spack prefix (inside insula) =="
    bazel_insula test //synthetic:py_hatch_fancy_pypi_readme_prefix_parity \
      --test_output=all --announce_rc \
      --test_env=SPACK_PY_HATCH_FANCY_PYPI_README_PREFIX="$SPACK_PY_HATCH_FANCY_PYPI_README_PREFIX" \
      --test_env=SPACK_PY_HATCHLING_PREFIX="$SPACK_PY_HATCHLING_PREFIX" \
      --test_env=SPACK_PY_PACKAGING_PREFIX="$SPACK_PY_PACKAGING_PREFIX" \
      --test_env=SPACK_PY_PATHSPEC_PREFIX="$SPACK_PY_PATHSPEC_PREFIX" \
      --test_env=SPACK_PY_PIP_PREFIX="$SPACK_PY_PIP_PREFIX" \
      --test_env=SPACK_PY_PLUGGY_PREFIX="$SPACK_PY_PLUGGY_PREFIX" \
      --test_env=SPACK_PY_TROVE_CLASSIFIERS_PREFIX="$SPACK_PY_TROVE_CLASSIFIERS_PREFIX" \
      --test_env=SPACK_PY_WHEEL_PREFIX="$SPACK_PY_WHEEL_PREFIX" \
      --test_env=SPACK_PYTHON_PREFIX="$SPACK_PYTHON_PREFIX" \
      --test_env=SPACK_PYTHON_VENV_PREFIX="$SPACK_PYTHON_VENV_PREFIX"
  elif [[ -n "$SPACK_PY_HATCH_FANCY_PYPI_README_PREFIX" ]]; then
    echo "== phase 2aa-py-hatch-fancy-pypi-readme: skipping py-hatch-fancy-pypi-readme native parity gate (provider remains $SPACK_PY_HATCH_FANCY_PYPI_README_BUILD) =="
  fi

  if [[ -n "$SPACK_PY_HATCH_VCS_PREFIX" && "$SPACK_PY_HATCH_VCS_BUILD" == "native" ]]; then
    echo "== phase 2aa-py-hatch-vcs: native py-hatch-vcs prefix parity gate vs Spack prefix (inside insula) =="
    bazel_insula test //synthetic:py_hatch_vcs_prefix_parity \
      --test_output=all --announce_rc \
      --test_env=SPACK_PY_HATCH_VCS_PREFIX="$SPACK_PY_HATCH_VCS_PREFIX" \
      --test_env=SPACK_PY_HATCHLING_PREFIX="$SPACK_PY_HATCHLING_PREFIX" \
      --test_env=SPACK_PY_PACKAGING_PREFIX="$SPACK_PY_PACKAGING_PREFIX" \
      --test_env=SPACK_PY_PATHSPEC_PREFIX="$SPACK_PY_PATHSPEC_PREFIX" \
      --test_env=SPACK_PY_PIP_PREFIX="$SPACK_PY_PIP_PREFIX" \
      --test_env=SPACK_PY_PLUGGY_PREFIX="$SPACK_PY_PLUGGY_PREFIX" \
      --test_env=SPACK_PY_SETUPTOOLS_SCM_PREFIX="$SPACK_PY_SETUPTOOLS_SCM_PREFIX" \
      --test_env=SPACK_PY_TROVE_CLASSIFIERS_PREFIX="$SPACK_PY_TROVE_CLASSIFIERS_PREFIX" \
      --test_env=SPACK_PY_WHEEL_PREFIX="$SPACK_PY_WHEEL_PREFIX" \
      --test_env=SPACK_PYTHON_PREFIX="$SPACK_PYTHON_PREFIX" \
      --test_env=SPACK_PYTHON_VENV_PREFIX="$SPACK_PYTHON_VENV_PREFIX"
  elif [[ -n "$SPACK_PY_HATCH_VCS_PREFIX" ]]; then
    echo "== phase 2aa-py-hatch-vcs: skipping py-hatch-vcs native parity gate (provider remains $SPACK_PY_HATCH_VCS_BUILD) =="
  fi

  if [[ -n "$SPACK_PY_OPT_EINSUM_PREFIX" && "$SPACK_PY_OPT_EINSUM_BUILD" == "native" ]]; then
    echo "== phase 2aa-py-opt-einsum: native py-opt-einsum prefix parity gate vs Spack prefix (inside insula) =="
    bazel_insula test //synthetic:py_opt_einsum_prefix_parity \
      --test_output=all --announce_rc \
      --test_env=SPACK_PY_OPT_EINSUM_PREFIX="$SPACK_PY_OPT_EINSUM_PREFIX" \
      --test_env=SPACK_PY_HATCH_FANCY_PYPI_README_PREFIX="$SPACK_PY_HATCH_FANCY_PYPI_README_PREFIX" \
      --test_env=SPACK_PY_HATCH_VCS_PREFIX="$SPACK_PY_HATCH_VCS_PREFIX" \
      --test_env=SPACK_PY_HATCHLING_PREFIX="$SPACK_PY_HATCHLING_PREFIX" \
      --test_env=SPACK_PY_PACKAGING_PREFIX="$SPACK_PY_PACKAGING_PREFIX" \
      --test_env=SPACK_PY_PATHSPEC_PREFIX="$SPACK_PY_PATHSPEC_PREFIX" \
      --test_env=SPACK_PY_PIP_PREFIX="$SPACK_PY_PIP_PREFIX" \
      --test_env=SPACK_PY_PLUGGY_PREFIX="$SPACK_PY_PLUGGY_PREFIX" \
      --test_env=SPACK_PY_SETUPTOOLS_SCM_PREFIX="$SPACK_PY_SETUPTOOLS_SCM_PREFIX" \
      --test_env=SPACK_PY_TROVE_CLASSIFIERS_PREFIX="$SPACK_PY_TROVE_CLASSIFIERS_PREFIX" \
      --test_env=SPACK_PY_WHEEL_PREFIX="$SPACK_PY_WHEEL_PREFIX" \
      --test_env=SPACK_PYTHON_PREFIX="$SPACK_PYTHON_PREFIX" \
      --test_env=SPACK_PYTHON_VENV_PREFIX="$SPACK_PYTHON_VENV_PREFIX"
  elif [[ -n "$SPACK_PY_OPT_EINSUM_PREFIX" ]]; then
    echo "== phase 2aa-py-opt-einsum: skipping py-opt-einsum native parity gate (provider remains $SPACK_PY_OPT_EINSUM_BUILD) =="
  fi

  if [[ -n "$SPACK_PY_PYTHRAN_PREFIX" && "$SPACK_PY_PYTHRAN_BUILD" == "native" ]]; then
    echo "== phase 2aa-py-pythran: native py-pythran prefix parity gate vs Spack prefix (inside insula) =="
    bazel_insula test //synthetic:py_pythran_prefix_parity \
      --test_output=all --announce_rc \
      --test_env=SPACK_PY_PYTHRAN_PREFIX="$SPACK_PY_PYTHRAN_PREFIX" \
      --test_env=SPACK_PY_BENIGET_PREFIX="$SPACK_PY_BENIGET_PREFIX" \
      --test_env=SPACK_PY_GAST_PREFIX="$SPACK_PY_GAST_PREFIX" \
      --test_env=SPACK_PY_NUMPY_PREFIX="$SPACK_PY_NUMPY_PREFIX" \
      --test_env=SPACK_PY_PIP_PREFIX="$SPACK_PY_PIP_PREFIX" \
      --test_env=SPACK_PY_PLY_PREFIX="$SPACK_PY_PLY_PREFIX" \
      --test_env=SPACK_PY_SETUPTOOLS_PREFIX="$SPACK_PY_SETUPTOOLS_PREFIX" \
      --test_env=SPACK_PY_WHEEL_PREFIX="$SPACK_PY_WHEEL_PREFIX" \
      --test_env=SPACK_PYTHON_PREFIX="$SPACK_PYTHON_PREFIX" \
      --test_env=SPACK_PYTHON_VENV_PREFIX="$SPACK_PYTHON_VENV_PREFIX"
  elif [[ -n "$SPACK_PY_PYTHRAN_PREFIX" ]]; then
    echo "== phase 2aa-py-pythran: skipping py-pythran native parity gate (provider remains $SPACK_PY_PYTHRAN_BUILD) =="
  fi

  if [[ -n "$SPACK_PY_SCIPY_PREFIX" && "$SPACK_PY_SCIPY_BUILD" == "native" ]]; then
    echo "== phase 2aa-py-scipy: native py-scipy prefix parity gate vs Spack prefix (inside insula) =="
    bazel_insula test //synthetic:py_scipy_prefix_parity \
      --test_output=all --announce_rc \
      --test_env=SPACK_PY_SCIPY_PREFIX="$SPACK_PY_SCIPY_PREFIX" \
      --test_env=SPACK_OPENBLAS_PREFIX="$SPACK_OPENBLAS_PREFIX" \
      --test_env=SPACK_PKGCONF_PREFIX="$SPACK_PKGCONF_PREFIX" \
      --test_env=SPACK_MESON_PREFIX="$SPACK_MESON_PREFIX" \
      --test_env=SPACK_NINJA_PREFIX="$SPACK_NINJA_PREFIX" \
      --test_env=SPACK_PY_BENIGET_PREFIX="$SPACK_PY_BENIGET_PREFIX" \
      --test_env=SPACK_PY_CYTHON_PREFIX="$SPACK_PY_CYTHON_PREFIX" \
      --test_env=SPACK_PY_GAST_PREFIX="$SPACK_PY_GAST_PREFIX" \
      --test_env=SPACK_PY_MESON_PYTHON_PREFIX="$SPACK_PY_MESON_PYTHON_PREFIX" \
      --test_env=SPACK_PY_NUMPY_PREFIX="$SPACK_PY_NUMPY_PREFIX" \
      --test_env=SPACK_PY_PACKAGING_PREFIX="$SPACK_PY_PACKAGING_PREFIX" \
      --test_env=SPACK_PY_PIP_PREFIX="$SPACK_PY_PIP_PREFIX" \
      --test_env=SPACK_PY_PLY_PREFIX="$SPACK_PY_PLY_PREFIX" \
      --test_env=SPACK_PY_PYBIND11_PREFIX="$SPACK_PY_PYBIND11_PREFIX" \
      --test_env=SPACK_PY_PYPROJECT_METADATA_PREFIX="$SPACK_PY_PYPROJECT_METADATA_PREFIX" \
      --test_env=SPACK_PY_PYTHRAN_PREFIX="$SPACK_PY_PYTHRAN_PREFIX" \
      --test_env=SPACK_PY_WHEEL_PREFIX="$SPACK_PY_WHEEL_PREFIX" \
      --test_env=SPACK_PYTHON_PREFIX="$SPACK_PYTHON_PREFIX" \
      --test_env=SPACK_PYTHON_VENV_PREFIX="$SPACK_PYTHON_VENV_PREFIX"
  elif [[ -n "$SPACK_PY_SCIPY_PREFIX" ]]; then
    echo "== phase 2aa-py-scipy: skipping py-scipy native parity gate (provider remains $SPACK_PY_SCIPY_BUILD) =="
  fi

  if [[ -n "$SPACK_PY_FILELOCK_PREFIX" && "$SPACK_PY_FILELOCK_BUILD" == "native" ]]; then
    echo "== phase 2aa-py-filelock: native py-filelock prefix parity gate vs Spack prefix (inside insula) =="
    bazel_insula test //synthetic:py_filelock_prefix_parity \
      --test_output=all --announce_rc \
      --test_env=SPACK_PY_FILELOCK_PREFIX="$SPACK_PY_FILELOCK_PREFIX" \
      --test_env=SPACK_PY_HATCH_VCS_PREFIX="$SPACK_PY_HATCH_VCS_PREFIX" \
      --test_env=SPACK_PY_HATCHLING_PREFIX="$SPACK_PY_HATCHLING_PREFIX" \
      --test_env=SPACK_PY_PACKAGING_PREFIX="$SPACK_PY_PACKAGING_PREFIX" \
      --test_env=SPACK_PY_PATHSPEC_PREFIX="$SPACK_PY_PATHSPEC_PREFIX" \
      --test_env=SPACK_PY_PIP_PREFIX="$SPACK_PY_PIP_PREFIX" \
      --test_env=SPACK_PY_PLUGGY_PREFIX="$SPACK_PY_PLUGGY_PREFIX" \
      --test_env=SPACK_PY_SETUPTOOLS_SCM_PREFIX="$SPACK_PY_SETUPTOOLS_SCM_PREFIX" \
      --test_env=SPACK_PY_SETUPTOOLS_PREFIX="$SPACK_PY_SETUPTOOLS_PREFIX" \
      --test_env=SPACK_PY_TROVE_CLASSIFIERS_PREFIX="$SPACK_PY_TROVE_CLASSIFIERS_PREFIX" \
      --test_env=SPACK_PY_WHEEL_PREFIX="$SPACK_PY_WHEEL_PREFIX" \
      --test_env=SPACK_PYTHON_PREFIX="$SPACK_PYTHON_PREFIX" \
      --test_env=SPACK_PYTHON_VENV_PREFIX="$SPACK_PYTHON_VENV_PREFIX"
  elif [[ -n "$SPACK_PY_FILELOCK_PREFIX" ]]; then
    echo "== phase 2aa-py-filelock: skipping py-filelock native parity gate (provider remains $SPACK_PY_FILELOCK_BUILD) =="
  fi

  if [[ -n "$SPACK_PY_FSSPEC_PREFIX" && "$SPACK_PY_FSSPEC_BUILD" == "native" ]]; then
    echo "== phase 2aa-py-fsspec: native py-fsspec prefix parity gate vs Spack prefix (inside insula) =="
    bazel_insula test //synthetic:py_fsspec_prefix_parity \
      --test_output=all --announce_rc \
      --test_env=SPACK_PY_FSSPEC_PREFIX="$SPACK_PY_FSSPEC_PREFIX" \
      --test_env=SPACK_PY_HATCH_VCS_PREFIX="$SPACK_PY_HATCH_VCS_PREFIX" \
      --test_env=SPACK_PY_HATCHLING_PREFIX="$SPACK_PY_HATCHLING_PREFIX" \
      --test_env=SPACK_PY_PACKAGING_PREFIX="$SPACK_PY_PACKAGING_PREFIX" \
      --test_env=SPACK_PY_PATHSPEC_PREFIX="$SPACK_PY_PATHSPEC_PREFIX" \
      --test_env=SPACK_PY_PIP_PREFIX="$SPACK_PY_PIP_PREFIX" \
      --test_env=SPACK_PY_PLUGGY_PREFIX="$SPACK_PY_PLUGGY_PREFIX" \
      --test_env=SPACK_PY_SETUPTOOLS_SCM_PREFIX="$SPACK_PY_SETUPTOOLS_SCM_PREFIX" \
      --test_env=SPACK_PY_SETUPTOOLS_PREFIX="$SPACK_PY_SETUPTOOLS_PREFIX" \
      --test_env=SPACK_PY_TROVE_CLASSIFIERS_PREFIX="$SPACK_PY_TROVE_CLASSIFIERS_PREFIX" \
      --test_env=SPACK_PY_WHEEL_PREFIX="$SPACK_PY_WHEEL_PREFIX" \
      --test_env=SPACK_PYTHON_PREFIX="$SPACK_PYTHON_PREFIX" \
      --test_env=SPACK_PYTHON_VENV_PREFIX="$SPACK_PYTHON_VENV_PREFIX"
  elif [[ -n "$SPACK_PY_FSSPEC_PREFIX" ]]; then
    echo "== phase 2aa-py-fsspec: skipping py-fsspec native parity gate (provider remains $SPACK_PY_FSSPEC_BUILD) =="
  fi

  if [[ -n "$SPACK_PY_SCIKIT_BUILD_CORE_PREFIX" && "$SPACK_PY_SCIKIT_BUILD_CORE_BUILD" == "native" ]]; then
    echo "== phase 2aa-py-scikit-build-core: native py-scikit-build-core prefix parity gate vs Spack prefix (inside insula) =="
    bazel_insula test //synthetic:py_scikit_build_core_prefix_parity \
      --test_output=all --announce_rc \
      --test_env=SPACK_PY_SCIKIT_BUILD_CORE_PREFIX="$SPACK_PY_SCIKIT_BUILD_CORE_PREFIX" \
      --test_env=SPACK_CMAKE_PREFIX="$SPACK_CMAKE_PREFIX" \
      --test_env=SPACK_PY_HATCH_VCS_PREFIX="$SPACK_PY_HATCH_VCS_PREFIX" \
      --test_env=SPACK_PY_HATCHLING_PREFIX="$SPACK_PY_HATCHLING_PREFIX" \
      --test_env=SPACK_PY_PACKAGING_PREFIX="$SPACK_PY_PACKAGING_PREFIX" \
      --test_env=SPACK_PY_PATHSPEC_PREFIX="$SPACK_PY_PATHSPEC_PREFIX" \
      --test_env=SPACK_PY_PIP_PREFIX="$SPACK_PY_PIP_PREFIX" \
      --test_env=SPACK_PY_PLUGGY_PREFIX="$SPACK_PY_PLUGGY_PREFIX" \
      --test_env=SPACK_PY_SETUPTOOLS_SCM_PREFIX="$SPACK_PY_SETUPTOOLS_SCM_PREFIX" \
      --test_env=SPACK_PY_SETUPTOOLS_PREFIX="$SPACK_PY_SETUPTOOLS_PREFIX" \
      --test_env=SPACK_PY_TROVE_CLASSIFIERS_PREFIX="$SPACK_PY_TROVE_CLASSIFIERS_PREFIX" \
      --test_env=SPACK_PY_WHEEL_PREFIX="$SPACK_PY_WHEEL_PREFIX" \
      --test_env=SPACK_PYTHON_PREFIX="$SPACK_PYTHON_PREFIX" \
      --test_env=SPACK_PYTHON_VENV_PREFIX="$SPACK_PYTHON_VENV_PREFIX"
  elif [[ -n "$SPACK_PY_SCIKIT_BUILD_CORE_PREFIX" ]]; then
    echo "== phase 2aa-py-scikit-build-core: skipping py-scikit-build-core native parity gate (provider remains $SPACK_PY_SCIKIT_BUILD_CORE_BUILD) =="
  fi

  if [[ -n "$SPACK_PY_TYPING_EXTENSIONS_PREFIX" && "$SPACK_PY_TYPING_EXTENSIONS_BUILD" == "native" ]]; then
    echo "== phase 2aa-py-typing-extensions: native py-typing-extensions prefix parity gate vs Spack prefix (inside insula) =="
    bazel_insula test //synthetic:py_typing_extensions_prefix_parity \
      --test_output=all --announce_rc \
      --test_env=SPACK_PY_TYPING_EXTENSIONS_PREFIX="$SPACK_PY_TYPING_EXTENSIONS_PREFIX" \
      --test_env=SPACK_PY_FLIT_CORE_PREFIX="$SPACK_PY_FLIT_CORE_PREFIX" \
      --test_env=SPACK_PY_PIP_PREFIX="$SPACK_PY_PIP_PREFIX" \
      --test_env=SPACK_PY_WHEEL_PREFIX="$SPACK_PY_WHEEL_PREFIX" \
      --test_env=SPACK_PYTHON_PREFIX="$SPACK_PYTHON_PREFIX" \
      --test_env=SPACK_PYTHON_VENV_PREFIX="$SPACK_PYTHON_VENV_PREFIX"
  elif [[ -n "$SPACK_PY_TYPING_EXTENSIONS_PREFIX" ]]; then
    echo "== phase 2aa-py-typing-extensions: skipping py-typing-extensions native parity gate (provider remains $SPACK_PY_TYPING_EXTENSIONS_BUILD) =="
  fi

  if [[ -n "$SPACK_PY_URLLIB3_PREFIX" && "$SPACK_PY_URLLIB3_BUILD" == "native" ]]; then
    echo "== phase 2aa-py-urllib3: native py-urllib3 prefix parity gate vs Spack prefix (inside insula) =="
    bazel_insula test //synthetic:py_urllib3_prefix_parity \
      --test_output=all --announce_rc \
      --test_env=SPACK_PY_URLLIB3_PREFIX="$SPACK_PY_URLLIB3_PREFIX" \
      --test_env=SPACK_PY_HATCH_VCS_PREFIX="$SPACK_PY_HATCH_VCS_PREFIX" \
      --test_env=SPACK_PY_HATCHLING_PREFIX="$SPACK_PY_HATCHLING_PREFIX" \
      --test_env=SPACK_PY_PACKAGING_PREFIX="$SPACK_PY_PACKAGING_PREFIX" \
      --test_env=SPACK_PY_PATHSPEC_PREFIX="$SPACK_PY_PATHSPEC_PREFIX" \
      --test_env=SPACK_PY_PIP_PREFIX="$SPACK_PY_PIP_PREFIX" \
      --test_env=SPACK_PY_PLUGGY_PREFIX="$SPACK_PY_PLUGGY_PREFIX" \
      --test_env=SPACK_PY_SETUPTOOLS_SCM_PREFIX="$SPACK_PY_SETUPTOOLS_SCM_PREFIX" \
      --test_env=SPACK_PY_SETUPTOOLS_PREFIX="$SPACK_PY_SETUPTOOLS_PREFIX" \
      --test_env=SPACK_PY_TROVE_CLASSIFIERS_PREFIX="$SPACK_PY_TROVE_CLASSIFIERS_PREFIX" \
      --test_env=SPACK_PY_WHEEL_PREFIX="$SPACK_PY_WHEEL_PREFIX" \
      --test_env=SPACK_PYTHON_PREFIX="$SPACK_PYTHON_PREFIX" \
      --test_env=SPACK_PYTHON_VENV_PREFIX="$SPACK_PYTHON_VENV_PREFIX"
  elif [[ -n "$SPACK_PY_URLLIB3_PREFIX" ]]; then
    echo "== phase 2aa-py-urllib3: skipping py-urllib3 native parity gate (provider remains $SPACK_PY_URLLIB3_BUILD) =="
  fi

  if [[ -n "$SPACK_PY_REQUESTS_PREFIX" && "$SPACK_PY_REQUESTS_BUILD" == "native" ]]; then
    echo "== phase 2aa-py-requests: native py-requests prefix parity gate vs Spack prefix (inside insula) =="
    bazel_insula test //synthetic:py_requests_prefix_parity \
      --test_output=all --announce_rc \
      --test_env=SPACK_PY_REQUESTS_PREFIX="$SPACK_PY_REQUESTS_PREFIX" \
      --test_env=SPACK_PY_CERTIFI_PREFIX="$SPACK_PY_CERTIFI_PREFIX" \
      --test_env=SPACK_PY_CHARSET_NORMALIZER_PREFIX="$SPACK_PY_CHARSET_NORMALIZER_PREFIX" \
      --test_env=SPACK_PY_IDNA_PREFIX="$SPACK_PY_IDNA_PREFIX" \
      --test_env=SPACK_PY_PIP_PREFIX="$SPACK_PY_PIP_PREFIX" \
      --test_env=SPACK_PY_SETUPTOOLS_PREFIX="$SPACK_PY_SETUPTOOLS_PREFIX" \
      --test_env=SPACK_PY_URLLIB3_PREFIX="$SPACK_PY_URLLIB3_PREFIX" \
      --test_env=SPACK_PY_WHEEL_PREFIX="$SPACK_PY_WHEEL_PREFIX" \
      --test_env=SPACK_PYTHON_PREFIX="$SPACK_PYTHON_PREFIX" \
      --test_env=SPACK_PYTHON_VENV_PREFIX="$SPACK_PYTHON_VENV_PREFIX"
  elif [[ -n "$SPACK_PY_REQUESTS_PREFIX" ]]; then
    echo "== phase 2aa-py-requests: skipping py-requests native parity gate (provider remains $SPACK_PY_REQUESTS_BUILD) =="
  fi

  if [[ -n "$SPACK_PY_VERSIONEER_PREFIX" && "$SPACK_PY_VERSIONEER_BUILD" == "native" ]]; then
    echo "== phase 2aa-py-versioneer: native py-versioneer prefix parity gate vs Spack prefix (inside insula) =="
    bazel_insula test //synthetic:py_versioneer_prefix_parity \
      --test_output=all --announce_rc \
      --test_env=SPACK_PY_VERSIONEER_PREFIX="$SPACK_PY_VERSIONEER_PREFIX" \
      --test_env=SPACK_PY_PIP_PREFIX="$SPACK_PY_PIP_PREFIX" \
      --test_env=SPACK_PY_SETUPTOOLS_PREFIX="$SPACK_PY_SETUPTOOLS_PREFIX" \
      --test_env=SPACK_PY_WHEEL_PREFIX="$SPACK_PY_WHEEL_PREFIX" \
      --test_env=SPACK_PYTHON_PREFIX="$SPACK_PYTHON_PREFIX" \
      --test_env=SPACK_PYTHON_VENV_PREFIX="$SPACK_PYTHON_VENV_PREFIX"
  elif [[ -n "$SPACK_PY_VERSIONEER_PREFIX" ]]; then
    echo "== phase 2aa-py-versioneer: skipping py-versioneer native parity gate (provider remains $SPACK_PY_VERSIONEER_BUILD) =="
  fi

  if [[ -n "$SPACK_RE2C_PREFIX" && "$SPACK_RE2C_BUILD" == "native" ]]; then
    echo "== phase 2aa-re2c: native re2c ABI/behavior parity gate vs Spack prefix (inside insula) =="
    bazel_insula test //synthetic:re2c_abi_parity \
      --test_output=all --announce_rc \
      --test_env=SPACK_RE2C_PREFIX="$SPACK_RE2C_PREFIX"
  elif [[ -n "$SPACK_RE2C_PREFIX" ]]; then
    echo "== phase 2aa-re2c: skipping re2c native parity gate (provider remains $SPACK_RE2C_BUILD) =="
  fi

  if [[ -n "$SPACK_NINJA_PREFIX" && "$SPACK_NINJA_BUILD" == "native" ]]; then
    echo "== phase 2aa-ninja: native ninja prefix/behavior parity gate vs Spack prefix (inside insula) =="
    bazel_insula test //synthetic:ninja_prefix_parity \
      --test_output=all --announce_rc \
      --test_env=SPACK_NINJA_PREFIX="$SPACK_NINJA_PREFIX"
  elif [[ -n "$SPACK_NINJA_PREFIX" ]]; then
    echo "== phase 2aa-ninja: skipping ninja native parity gate (provider remains $SPACK_NINJA_BUILD) =="
  fi

  if [[ -n "$SPACK_MESON_PREFIX" && "$SPACK_MESON_BUILD" == "native" ]]; then
    echo "== phase 2aa-meson: native meson prefix/behavior parity gate vs Spack prefix (inside insula) =="
    bazel_insula test //synthetic:meson_prefix_parity \
      --test_output=all --announce_rc \
      --test_env=SPACK_MESON_PREFIX="$SPACK_MESON_PREFIX" \
      --test_env=SPACK_NINJA_PREFIX="$SPACK_NINJA_PREFIX" \
      --test_env=SPACK_PY_PIP_PREFIX="$SPACK_PY_PIP_PREFIX" \
      --test_env=SPACK_PY_SETUPTOOLS_PREFIX="$SPACK_PY_SETUPTOOLS_PREFIX" \
      --test_env=SPACK_PY_WHEEL_PREFIX="$SPACK_PY_WHEEL_PREFIX" \
      --test_env=SPACK_PYTHON_PREFIX="$SPACK_PYTHON_PREFIX" \
      --test_env=SPACK_PYTHON_VENV_PREFIX="$SPACK_PYTHON_VENV_PREFIX"
  elif [[ -n "$SPACK_MESON_PREFIX" ]]; then
    echo "== phase 2aa-meson: skipping meson native parity gate (provider remains $SPACK_MESON_BUILD) =="
  fi

  if [[ -n "$SPACK_CPUINFO_PREFIX" && "$SPACK_CPUINFO_BUILD" == "native" ]]; then
    echo "== phase 2aa-cpuinfo: native cpuinfo ABI/behavior parity gate vs Spack prefix (inside insula) =="
    bazel_insula test //synthetic:cpuinfo_abi_parity \
      --test_output=all --announce_rc \
      --test_env=SPACK_CPUINFO_PREFIX="$SPACK_CPUINFO_PREFIX"
  elif [[ -n "$SPACK_CPUINFO_PREFIX" ]]; then
    echo "== phase 2aa-cpuinfo: skipping cpuinfo native ABI gate (provider remains $SPACK_CPUINFO_BUILD) =="
  fi

  if [[ -n "$SPACK_FP16_PREFIX" && "$SPACK_FP16_BUILD" == "native" ]]; then
    echo "== phase 2aa-fp16: native FP16 prefix/header parity gate vs Spack prefix (inside insula) =="
    bazel_insula test //synthetic:fp16_prefix_parity \
      --test_output=all --announce_rc \
      --test_env=SPACK_FP16_PREFIX="$SPACK_FP16_PREFIX"
  elif [[ -n "$SPACK_FP16_PREFIX" ]]; then
    echo "== phase 2aa-fp16: skipping FP16 native prefix gate (provider remains $SPACK_FP16_BUILD) =="
  fi

  if [[ -n "$SPACK_FXDIV_PREFIX" && "$SPACK_FXDIV_BUILD" == "native" ]]; then
    echo "== phase 2aa-fxdiv: native FXdiv prefix/header parity gate vs Spack prefix (inside insula) =="
    bazel_insula test //synthetic:fxdiv_prefix_parity \
      --test_output=all --announce_rc \
      --test_env=SPACK_FXDIV_PREFIX="$SPACK_FXDIV_PREFIX"
  elif [[ -n "$SPACK_FXDIV_PREFIX" ]]; then
    echo "== phase 2aa-fxdiv: skipping FXdiv native prefix gate (provider remains $SPACK_FXDIV_BUILD) =="
  fi

  if [[ -n "$SPACK_PSIMD_PREFIX" && "$SPACK_PSIMD_BUILD" == "native" ]]; then
    echo "== phase 2aa-psimd: native psimd prefix/header parity gate vs Spack prefix (inside insula) =="
    bazel_insula test //synthetic:psimd_prefix_parity \
      --test_output=all --announce_rc \
      --test_env=SPACK_PSIMD_PREFIX="$SPACK_PSIMD_PREFIX"
  elif [[ -n "$SPACK_PSIMD_PREFIX" ]]; then
    echo "== phase 2aa-psimd: skipping psimd native prefix gate (provider remains $SPACK_PSIMD_BUILD) =="
  fi

  if [[ -n "$SPACK_PTHREADPOOL_PREFIX" && "$SPACK_PTHREADPOOL_BUILD" == "native" ]]; then
    echo "== phase 2aa-pthreadpool: native pthreadpool prefix/static-library parity gate vs Spack prefix (inside insula) =="
    bazel_insula test //synthetic:pthreadpool_prefix_parity \
      --test_output=all --announce_rc \
      --test_env=SPACK_PTHREADPOOL_PREFIX="$SPACK_PTHREADPOOL_PREFIX"
  elif [[ -n "$SPACK_PTHREADPOOL_PREFIX" ]]; then
    echo "== phase 2aa-pthreadpool: skipping pthreadpool native prefix gate (provider remains $SPACK_PTHREADPOOL_BUILD) =="
  fi

  if [[ -n "$SPACK_PROTOBUF_PREFIX" && "$SPACK_PROTOBUF_BUILD" == "native" ]]; then
    echo "== phase 2ab: native protobuf ABI/behavior parity gate vs Spack prefix (inside insula) =="
    bazel_insula test //synthetic:protobuf_abi_parity \
      --test_output=all --announce_rc \
      --test_env=SPACK_PROTOBUF_PREFIX="$SPACK_PROTOBUF_PREFIX" \
      --test_env=SPACK_ZLIB_NG_PREFIX="$SPACK_ZLIB_NG_PREFIX"
  elif [[ -n "$SPACK_PROTOBUF_PREFIX" ]]; then
    echo "== phase 2ab: skipping protobuf native ABI gate (provider remains $SPACK_PROTOBUF_BUILD) =="
  fi

  if [[ -n "$SPACK_QHULL_PREFIX" && "$SPACK_QHULL_BUILD" == "native" ]]; then
    echo "== phase 2ac: native qhull ABI/behavior parity gate vs Spack prefix (inside insula) =="
    bazel_insula test //synthetic:qhull_abi_parity \
      --test_output=all --announce_rc \
      --test_env=SPACK_QHULL_PREFIX="$SPACK_QHULL_PREFIX"
  elif [[ -n "$SPACK_QHULL_PREFIX" ]]; then
    echo "== phase 2ac: skipping qhull native ABI gate (provider remains $SPACK_QHULL_BUILD) =="
  fi

  if [[ -n "$SPACK_SLEEF_PREFIX" && "$SPACK_SLEEF_BUILD" == "native" ]]; then
    echo "== phase 2ac-sleef: native SLEEF prefix/static-library parity gate vs Spack prefix (inside insula) =="
    bazel_insula test //synthetic:sleef_prefix_parity \
      --test_output=all --announce_rc \
      --test_env=SPACK_SLEEF_PREFIX="$SPACK_SLEEF_PREFIX"
  elif [[ -n "$SPACK_SLEEF_PREFIX" ]]; then
    echo "== phase 2ac-sleef: skipping SLEEF native parity gate (provider remains $SPACK_SLEEF_BUILD) =="
  fi

  if [[ -n "$SPACK_SWIG_PREFIX" && "$SPACK_SWIG_BUILD" == "native" ]]; then
    echo "== phase 2ad: native swig prefix/behavior parity gate vs Spack prefix (inside insula) =="
    bazel_insula test //synthetic:swig_prefix_parity \
      --test_output=all --announce_rc \
      --test_env=SPACK_SWIG_PREFIX="$SPACK_SWIG_PREFIX" \
      --test_env=SPACK_PCRE2_PREFIX="$SPACK_PCRE2_PREFIX" \
      --test_env=SPACK_ZLIB_NG_PREFIX="$SPACK_ZLIB_NG_PREFIX"
  elif [[ -n "$SPACK_SWIG_PREFIX" ]]; then
    echo "== phase 2ad: skipping swig native parity gate (provider remains $SPACK_SWIG_BUILD) =="
  fi

  if [[ -n "$SPACK_BINUTILS_PREFIX" && "$SPACK_BINUTILS_BUILD" == "native" ]]; then
    echo "== phase 2ae: native binutils ABI/behavior parity gate vs Spack prefix (inside insula) =="
    bazel_insula test //synthetic:binutils_abi_parity \
      --test_output=all --announce_rc \
      --test_env=SPACK_BINUTILS_PREFIX="$SPACK_BINUTILS_PREFIX" \
      --test_env=SPACK_ZLIB_NG_PREFIX="$SPACK_ZLIB_NG_PREFIX" \
      --test_env=SPACK_ZSTD_PREFIX="$SPACK_ZSTD_PREFIX"
  elif [[ -n "$SPACK_BINUTILS_PREFIX" ]]; then
    echo "== phase 2ae: skipping binutils native ABI gate (provider remains $SPACK_BINUTILS_BUILD) =="
  fi

  if [[ -n "$SPACK_FILE_PREFIX" && "$SPACK_FILE_BUILD" == "native" ]]; then
    echo "== phase 2af: native file/libmagic ABI/behavior parity gate vs Spack prefix (inside insula) =="
    bazel_insula test //synthetic:file_abi_parity \
      --test_output=all --announce_rc \
      --test_env=SPACK_FILE_PREFIX="$SPACK_FILE_PREFIX" \
      --test_env=SPACK_BZIP2_PREFIX="$SPACK_BZIP2_PREFIX" \
      --test_env=SPACK_XZ_PREFIX="$SPACK_XZ_PREFIX" \
      --test_env=SPACK_ZLIB_NG_PREFIX="$SPACK_ZLIB_NG_PREFIX" \
      --test_env=SPACK_ZSTD_PREFIX="$SPACK_ZSTD_PREFIX"
  elif [[ -n "$SPACK_FILE_PREFIX" ]]; then
    echo "== phase 2af: skipping file/libmagic native ABI gate (provider remains $SPACK_FILE_BUILD) =="
  fi

  if [[ -n "$SPACK_PMIX_PREFIX" && "$SPACK_PMIX_BUILD" == "native" ]]; then
    echo "== phase 2ag: native PMIx ABI/behavior parity gate vs Spack prefix (inside insula) =="
    bazel_insula test //synthetic:pmix_abi_parity \
      --test_output=all --announce_rc \
      --test_env=SPACK_PMIX_PREFIX="$SPACK_PMIX_PREFIX" \
      --test_env=SPACK_HWLOC_PREFIX="$SPACK_HWLOC_PREFIX" \
      --test_env=SPACK_LIBEVENT_PREFIX="$SPACK_LIBEVENT_PREFIX" \
      --test_env=SPACK_LIBPCIACCESS_PREFIX="$SPACK_LIBPCIACCESS_PREFIX" \
      --test_env=SPACK_LIBXML2_PREFIX="$SPACK_LIBXML2_PREFIX" \
      --test_env=SPACK_XZ_PREFIX="$SPACK_XZ_PREFIX" \
      --test_env=SPACK_LIBICONV_PREFIX="$SPACK_LIBICONV_PREFIX" \
      --test_env=SPACK_ZLIB_NG_PREFIX="$SPACK_ZLIB_NG_PREFIX"
  elif [[ -n "$SPACK_PMIX_PREFIX" ]]; then
    echo "== phase 2ag: skipping PMIx native ABI gate (provider remains $SPACK_PMIX_BUILD) =="
  fi

  if [[ -n "$SPACK_PRRTE_PREFIX" && "$SPACK_PRRTE_BUILD" == "native" ]]; then
    echo "== phase 2ah: native PRRTE ABI/behavior parity gate vs Spack prefix (inside insula) =="
    bazel_insula test //synthetic:prrte_abi_parity \
      --test_output=all --announce_rc \
      --test_env=SPACK_PRRTE_PREFIX="$SPACK_PRRTE_PREFIX" \
      --test_env=SPACK_PMIX_PREFIX="$SPACK_PMIX_PREFIX" \
      --test_env=SPACK_HWLOC_PREFIX="$SPACK_HWLOC_PREFIX" \
      --test_env=SPACK_LIBEVENT_PREFIX="$SPACK_LIBEVENT_PREFIX"
  elif [[ -n "$SPACK_PRRTE_PREFIX" ]]; then
    echo "== phase 2ah: skipping PRRTE native ABI gate (provider remains $SPACK_PRRTE_BUILD) =="
  fi

  if [[ -n "$SPACK_OPENMPI_PREFIX" && "$SPACK_OPENMPI_BUILD" == "native" ]]; then
    echo "== phase 2ai: native OpenMPI ABI/behavior parity gate vs Spack prefix (inside insula) =="
    bazel_insula test //synthetic:openmpi_abi_parity \
      --test_output=all --announce_rc \
      --test_env=SPACK_OPENMPI_PREFIX="$SPACK_OPENMPI_PREFIX" \
      --test_env=SPACK_PMIX_PREFIX="$SPACK_PMIX_PREFIX" \
      --test_env=SPACK_PRRTE_PREFIX="$SPACK_PRRTE_PREFIX" \
      --test_env=SPACK_HWLOC_PREFIX="$SPACK_HWLOC_PREFIX" \
      --test_env=SPACK_LIBEVENT_PREFIX="$SPACK_LIBEVENT_PREFIX" \
      --test_env=SPACK_NUMACTL_PREFIX="$SPACK_NUMACTL_PREFIX" \
      --test_env=SPACK_ZLIB_NG_PREFIX="$SPACK_ZLIB_NG_PREFIX"
  elif [[ -n "$SPACK_OPENMPI_PREFIX" ]]; then
    echo "== phase 2ai: skipping OpenMPI native ABI gate (provider remains $SPACK_OPENMPI_BUILD) =="
  fi
fi

if [[ "${VASO_FORMAL:-0}" == "1" ]]; then
  echo "== phase 3: formal migration model checks (inside insula) =="
  bazel_insula test --config=formal \
    //formal/build_migration:migration_tlc_test \
    //formal/build_migration:migration_hermetic_deps_tlc_test \
    //formal/lean:lean_abi_parity_test \
    --test_env=VASO_IN_INSULA=1 \
    --test_output=all --announce_rc
fi

echo "== done (estate: $ESTATE_ROOT) =="
exit 0
