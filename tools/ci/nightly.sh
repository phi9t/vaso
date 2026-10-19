#!/usr/bin/env bash
# Nightly production-build release gate. Install externally, for example:
#   17 2 * * * cd /path/to/vaso && VASO_ESTATE_ROOT=/data/vaso \
#     VASO_NATIVE_PYTORCH_TOKEN=build-native-pytorch \
#     VASO_NATIVE_TORCHVISION_TOKEN=build-native-torchvision \
#     VASO_NATIVE_TORCHAUDIO_TOKEN=build-native-torchaudio \
#     VASO_NATIVE_TRITON_TOKEN=build-native-triton \
#     VASO_NATIVE_JAXLIB_TOKEN=build-native-llvm \
#     tools/ci/nightly.sh >>/data/vaso/ci/nightly/cron.log 2>&1
#
# One invocation is cron-safe and flock'd. It runs the release gate for
# torch/jax on cu130/cu129 under a single GPU lease at a time, runs the
# determinism check for both profiles on cu130/cu129, records build SLOs and prunes
# stale estate artifacts. The script never installs the schedule itself.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
RESULT_PY="$ROOT/insula/execution/result.py"
LEASE_PY="$ROOT/insula/execution/lease.py"
SUMMARY_PY="$ROOT/tools/ci/nightly_summary.py"
GPU_WAIT_PY="$ROOT/tools/ci/gpu_wait.py"
source "$ROOT/tools/ci/ci_common.sh"

: "${VASO_ESTATE_ROOT:?set VASO_ESTATE_ROOT}"
VASO_AGENT="${VASO_AGENT:-pb-nightly}"
TARGET="${NIGHTLY_TARGET:-HEAD}"
GATE_CMD="${NIGHTLY_GATE_CMD:-$ROOT/tools/agents/triumvirate-gate.sh}"
DETERMINISM_CMD="${NIGHTLY_DETERMINISM_CMD:-$ROOT/scripts/release/determinism_check.sh}"
LEASE_TIMEOUT="${NIGHTLY_LEASE_TIMEOUT:-60}"
LEASE_TTL="${NIGHTLY_LEASE_TTL:-21600}"
GATE_TIMEOUT="${NIGHTLY_GATE_TIMEOUT:-8h}"
DETERMINISM_TIMEOUT="${NIGHTLY_DETERMINISM_TIMEOUT:-8h}"
ATTENTION_LOG="${NIGHTLY_ATTENTION_LOG:-$VASO_ESTATE_ROOT/agents/claude/autoland.log}"
CI_ROOT="$VASO_ESTATE_ROOT/ci/nightly"

mkdir -p "$CI_ROOT"
exec 9>"$CI_ROOT/nightly.lock"
if ! flock -n 9; then
  echo "NIGHTLY_LOCKED"
  exit 75
fi

write_summary() {
  local run_dir=$1 run_id=$2 sha=$3 target=$4 started_utc=$5
  python3 "$SUMMARY_PY" summary \
    --run-dir "$run_dir" \
    --run-id "$run_id" \
    --commit "$sha" \
    --target "$target" \
    --started-utc "$started_utc" \
    --finished-utc "$(stamp)" \
    --host-facts "$run_dir/host-facts.json" \
    --disk-headroom "$run_dir/results/disk-headroom.json" \
    --gc-report "$run_dir/results/gc.json" \
    --out "$run_dir/summary.json" >/dev/null
}

check_disk_headroom() {
  local run_dir=$1 out="$run_dir/results/disk-headroom.json"
  local -a args=(headroom --estate-root "$VASO_ESTATE_ROOT")
  if [[ -n "${NIGHTLY_FAKE_ESTATE_FREE_GIB:-}" ]]; then
    args+=(--fake-free-gib "$NIGHTLY_FAKE_ESTATE_FREE_GIB")
  fi
  args+=(--out "$out")
  python3 "$SUMMARY_PY" "${args[@]}" >/dev/null
  [[ "$(json_string_field "$out" verdict)" == "passed" ]]
}

run_gc() {
  local run_dir=$1 out="$run_dir/results/gc.json"
  local -a args=(
    gc
    --estate-root "$VASO_ESTATE_ROOT" \
    --current-run-dir "$run_dir" \
    --min-age-days "${NIGHTLY_GC_MIN_AGE_DAYS:-7}" \
    --out "$out"
  )
  if [[ "${NIGHTLY_GC_APPLY:-0}" == "1" ]]; then
    args+=(--apply)
  fi
  python3 "$SUMMARY_PY" "${args[@]}" >/dev/null
}

run_release_gate() (
  set -euo pipefail
  local profile=$1 line=$2 sha=$3 run_dir=$4
  local name="release-$profile-$line"
  local stages_jsonl="$run_dir/results/$name.stages.jsonl"
  local result_json="$run_dir/results/$name.result.json"
  local acceptance_json="$run_dir/results/$name.acceptance.json"
  local log="$run_dir/logs/$name.log"
  local output_base="$run_dir/output-bases/$name"
  local insula_lease="$run_dir/results/$name.insula-lease.json"
  local gpu_lease="$run_dir/results/$name.gpu-lease.json"
  local gpu_wait_json="$run_dir/results/$name.gpu-wait.json"
  local lease_err="$run_dir/logs/$name.lease.err"
  local insula_id="" gpu_id="" gpu_amount="${NIGHTLY_GPUS:-8}" started_utc args_json rc=0 blocked_reason=""

  cleanup() {
    set +e
    if [[ -n "$gpu_id" ]]; then
      VASO_ESTATE_ROOT="$VASO_ESTATE_ROOT" python3 "$LEASE_PY" release --id "$gpu_id" --resource gpu >>"$log" 2>&1
    fi
    if [[ -n "$insula_id" ]]; then
      VASO_ESTATE_ROOT="$VASO_ESTATE_ROOT" python3 "$LEASE_PY" release --id "$insula_id" --resource "insula:$line" >>"$log" 2>&1
    fi
  }
  trap cleanup EXIT

  started_utc="$(stamp)"
  args_json="$(python3 "$RESULT_PY" redact-args -- "$GATE_CMD" --release --profile "$profile" --line "$line" --commit "$sha" --acceptance-out "$acceptance_json")"
  : >"$log"
  : >"$lease_err"

  set +e
  python3 "$GPU_WAIT_PY" wait --json-out "$gpu_wait_json" >>"$log" 2>&1
  rc=$?
  set -e
  if [[ "$rc" != "0" ]]; then
    if [[ "$rc" == "75" ]]; then
      blocked_reason="$(json_string_field "$gpu_wait_json" message)"
      if [[ -z "$blocked_reason" ]]; then
        blocked_reason="GPU memory remained above the nightly threshold."
      fi
      python3 "$RESULT_PY" stage --stages-jsonl "$stages_jsonl" --name "$name" --exit-code 75 --duration-seconds 0 --log-path "$log"
      record_result "$result_json" "$stages_jsonl" "nightly-release-gate" "$line" "$args_json" "$log" "" "$sha" 75 "$started_utc" "blocked" "$blocked_reason"
      exit 75
    fi
    python3 "$RESULT_PY" stage --stages-jsonl "$stages_jsonl" --name "$name" --exit-code "$rc" --duration-seconds 0 --log-path "$log"
    record_result "$result_json" "$stages_jsonl" "nightly-release-gate" "$line" "$args_json" "$log" "" "$sha" "$rc" "$started_utc"
    exit "$rc"
  fi

  if ! VASO_AGENT="$VASO_AGENT" python3 "$LEASE_PY" acquire \
      --resource "insula:$line" \
      --holder "$VASO_AGENT-nightly-$profile-$line" \
      --pid "$BASHPID" \
      --ttl "$LEASE_TTL" \
      --timeout "$LEASE_TIMEOUT" >"$insula_lease" 2>>"$lease_err"; then
    cat "$lease_err" >>"$log"
    python3 "$RESULT_PY" stage --stages-jsonl "$stages_jsonl" --name "$name" --exit-code 75 --duration-seconds 0 --log-path "$log"
    record_result "$result_json" "$stages_jsonl" "nightly-release-gate" "$line" "$args_json" "$log" "" "$sha" 75 "$started_utc"
    exit 75
  fi
  insula_id="$(lease_id "$insula_lease")"

  if ! VASO_AGENT="$VASO_AGENT" python3 "$LEASE_PY" acquire \
      --resource gpu \
      --amount "$gpu_amount" \
      --holder "$VASO_AGENT-nightly-$profile-$line" \
      --pid "$BASHPID" \
      --ttl "$LEASE_TTL" \
      --timeout "$LEASE_TIMEOUT" >"$gpu_lease" 2>>"$lease_err"; then
    cat "$lease_err" >>"$log"
    python3 "$RESULT_PY" stage --stages-jsonl "$stages_jsonl" --name "$name" --exit-code 75 --duration-seconds 0 --log-path "$log"
    record_result "$result_json" "$stages_jsonl" "nightly-release-gate" "$line" "$args_json" "$log" "insula:$line:$insula_id" "$sha" 75 "$started_utc"
    exit 75
  fi
  gpu_id="$(lease_id "$gpu_lease")"
  eval "$(lease_env_exports "$gpu_lease")"
  VASO_LEASES_HELD="insula:$line:$insula_id,gpu:$gpu_id"
  VASO_GPUS="$gpu_amount"
  export VASO_LEASES_HELD VASO_GPUS

  local start_ns end_ns duration
  start_ns="$(date +%s%N)"
  set +e
  VASO_AGENT="$VASO_AGENT" \
    VASO_BAZEL_OB="$output_base" \
    NIGHTLY_PROFILE="$profile" \
    NIGHTLY_LINE="$line" \
    NIGHTLY_ACCEPTANCE_OUT="$acceptance_json" \
    timeout "$GATE_TIMEOUT" "$GATE_CMD" \
      --release \
      --profile "$profile" \
      --line "$line" \
      --commit "$sha" \
      --acceptance-out "$acceptance_json" >>"$log" 2>&1
  rc=$?
  set -e
  end_ns="$(date +%s%N)"
  duration="$(duration_seconds "$start_ns" "$end_ns")"
  python3 "$RESULT_PY" stage \
    --stages-jsonl "$stages_jsonl" \
    --name "$name" \
    --exit-code "$rc" \
    --duration-seconds "$duration" \
    --log-path "$log"
  record_result "$result_json" "$stages_jsonl" "nightly-release-gate" "$line" "$args_json" "$log" "insula:$line:$insula_id,gpu:$gpu_id" "$sha" "$rc" "$started_utc"
  exit "$rc"
)

run_determinism() {
  local profile=$1 line=$2 sha=$3 run_dir=$4
  local name="determinism-$profile-$line"
  local stages_jsonl="$run_dir/results/$name.stages.jsonl"
  local result_json="$run_dir/results/$name.result.json"
  local report_json="$run_dir/results/$name.json"
  local log="$run_dir/logs/$name.log"
  local work_dir="$run_dir/determinism/$profile-$line"
  local args_json started_utc rc=0

  started_utc="$(stamp)"
  args_json="$(python3 "$RESULT_PY" redact-args -- "$DETERMINISM_CMD" --profile "$profile" --line "$line" --work-dir "$work_dir" --out "$report_json" --git-sha "$sha")"
  set +e
  run_logged_stage "$name" "$stages_jsonl" "$log" env \
    VASO_AGENT="$VASO_AGENT" \
    VASO_ESTATE_ROOT="$VASO_ESTATE_ROOT" \
    TMPDIR="${TMPDIR:-$VASO_ESTATE_ROOT/agents/$VASO_AGENT/tmp}" \
    timeout "$DETERMINISM_TIMEOUT" "$DETERMINISM_CMD" \
      --profile "$profile" \
      --line "$line" \
      --work-dir "$work_dir" \
      --out "$report_json" \
      --git-sha "$sha"
  rc=$?
  set -e
  record_result "$result_json" "$stages_jsonl" "nightly-determinism" "$line" "$args_json" "$log" "" "$sha" "$rc" "$started_utc"
  return "$rc"
}

sha="$(git -C "$ROOT" rev-parse "$TARGET")"
short_sha="${sha:0:12}"
run_id="${NIGHTLY_RUN_ID:-$(date -u +%Y%m%dT%H%M%SZ)-$short_sha}"
run_dir="$CI_ROOT/$run_id"
if [[ -e "$run_dir" ]]; then
  run_id="$run_id-$$"
  run_dir="$CI_ROOT/$run_id"
fi
mkdir -p "$run_dir/logs" "$run_dir/results" "$run_dir/output-bases" "$run_dir/determinism"
started_utc="$(stamp)"
write_host_facts "$run_dir/host-facts.json"
: >"$run_dir/results/gc.json"
printf '{}\n' >"$run_dir/results/gc.json"

overall_rc=0
if ! check_disk_headroom "$run_dir"; then
  overall_rc=1
else
  for profile in torch jax; do
    for line in cu130 cu129; do
      if ! run_release_gate "$profile" "$line" "$sha" "$run_dir"; then
        overall_rc=1
      fi
    done
  done
  if [[ "$overall_rc" == "0" ]]; then
    for profile in torch jax; do
      for line in cu130 cu129; do
        if ! run_determinism "$profile" "$line" "$sha" "$run_dir"; then
          overall_rc=1
        fi
      done
    done
  fi
fi

write_summary "$run_dir" "$run_id" "$sha" "$TARGET" "$started_utc"
summary="$run_dir/summary.json"
verdict="$(json_string_field "$summary" verdict)"
if [[ "$verdict" == "passed" ]]; then
  if ! run_gc "$run_dir"; then
    overall_rc=1
    if [[ ! -s "$run_dir/results/gc.json" ]]; then
      printf '{"schema_version":1,"verdict":"failed","message":"GC failed before writing a report."}\n' >"$run_dir/results/gc.json"
    fi
  fi
  write_summary "$run_dir" "$run_id" "$sha" "$TARGET" "$started_utc"
  verdict="$(json_string_field "$summary" verdict)"
fi
write_state "$CI_ROOT/state.json" "$sha" "$run_id" "$verdict" "$(stamp)"
append_attention_if_red NIGHTLY_RED "$summary"
echo "NIGHTLY_SUMMARY $summary"
if [[ "$verdict" == "passed" && "$overall_rc" == "0" ]]; then
  exit 0
fi
exit 1
