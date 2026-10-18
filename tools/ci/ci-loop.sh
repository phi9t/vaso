#!/usr/bin/env bash
# Scheduled production-build CI loop. Install externally, for example:
#   */30 * * * * cd /path/to/vaso && VASO_ESTATE_ROOT=/data/vaso tools/ci/ci-loop.sh >>/data/vaso/ci/cron.log 2>&1
#
# One invocation is cron-safe and flock'd. A new target tip runs standing
# verify first, then the fast triumvirate gate for torch/jax on cu129/cu130.
# The script records artifacts under $VASO_ESTATE_ROOT/ci/<run-id>/ and never
# installs the schedule itself.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
RESULT_PY="$ROOT/insula/execution/result.py"
LEASE_PY="$ROOT/insula/execution/lease.py"
SUMMARY_PY="$ROOT/tools/ci/ci_summary.py"
source "$ROOT/tools/ci/ci_common.sh"

: "${VASO_ESTATE_ROOT:?set VASO_ESTATE_ROOT}"
VASO_AGENT="${VASO_AGENT:-pb-ci}"
TARGET="${CI_LOOP_TARGET:-HEAD}"
VERIFY_CMD="${CI_LOOP_VERIFY_CMD:-$ROOT/tools/agents/standing-verify.sh}"
GATE_CMD="${CI_LOOP_GATE_CMD:-$ROOT/tools/agents/triumvirate-gate.sh}"
LEASE_TIMEOUT="${CI_LOOP_LEASE_TIMEOUT:-60}"
LEASE_TTL="${CI_LOOP_LEASE_TTL:-21600}"
GATE_TIMEOUT="${CI_LOOP_GATE_TIMEOUT:-6h}"
# The gate's workload stage runs with --gpus 8, so on an 8-GPU host gate runs
# take the GPU lease one at a time; set CI_LOOP_GPUS only for smaller gates.
ATTENTION_LOG="${CI_LOOP_ATTENTION_LOG:-$VASO_ESTATE_ROOT/agents/claude/autoland.log}"
CI_ROOT="$VASO_ESTATE_ROOT/ci"

mkdir -p "$CI_ROOT"
exec 9>"$CI_ROOT/ci-loop.lock"
if ! flock -n 9; then
  echo "CI_LOOP_LOCKED"
  exit 75
fi

run_standing_verify() {
  local sha=$1 run_dir=$2
  local stages_jsonl="$run_dir/results/standing-verify.stages.jsonl"
  local result_json="$run_dir/results/standing-verify.result.json"
  local log="$run_dir/logs/standing-verify.log"
  local output_base="$run_dir/output-bases/standing-verify"
  local args_json started_utc rc
  started_utc="$(stamp)"
  args_json="$(python3 "$RESULT_PY" redact-args -- "$VERIFY_CMD" --agent "$VASO_AGENT")"
  set +e
  run_logged_stage "standing_verify" "$stages_jsonl" "$log" env \
    TMPDIR="${TMPDIR:-$VASO_ESTATE_ROOT/agents/$VASO_AGENT/tmp}" \
    VASO_BAZEL_OB="$output_base" \
    "$VERIFY_CMD" --agent "$VASO_AGENT"
  rc=$?
  set -e
  record_result "$result_json" "$stages_jsonl" "ci-standing-verify" "" "$args_json" "$log" "" "$sha" "$rc" "$started_utc"
  return "$rc"
}

run_gate() (
  set -euo pipefail
  local profile=$1 line=$2 sha=$3 run_dir=$4
  local name="gate-$profile-$line"
  local stages_jsonl="$run_dir/results/$name.stages.jsonl"
  local result_json="$run_dir/results/$name.result.json"
  local acceptance_json="$run_dir/results/$name.acceptance.json"
  local log="$run_dir/logs/$name.log"
  local output_base="$run_dir/output-bases/$name"
  local insula_lease="$run_dir/results/$name.insula-lease.json"
  local gpu_lease="$run_dir/results/$name.gpu-lease.json"
  local lease_err="$run_dir/logs/$name.lease.err"
  local insula_id="" gpu_id="" gpu_amount="${CI_LOOP_GPUS:-8}" started_utc args_json rc=0

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
  args_json="$(python3 "$RESULT_PY" redact-args -- "$GATE_CMD" --profile "$profile" --line "$line" --commit "$sha" --acceptance-out "$acceptance_json")"
  : >"$log"
  : >"$lease_err"

  if ! VASO_AGENT="$VASO_AGENT" python3 "$LEASE_PY" acquire \
      --resource "insula:$line" \
      --holder "$VASO_AGENT-ci-$profile-$line" \
      --pid "$BASHPID" \
      --ttl "$LEASE_TTL" \
      --timeout "$LEASE_TIMEOUT" >"$insula_lease" 2>>"$lease_err"; then
    cat "$lease_err" >"$log"
    python3 "$RESULT_PY" stage --stages-jsonl "$stages_jsonl" --name "$name" --exit-code 75 --duration-seconds 0 --log-path "$log"
    record_result "$result_json" "$stages_jsonl" "ci-gate" "$line" "$args_json" "$log" "" "$sha" 75 "$started_utc"
    exit 75
  fi
  insula_id="$(lease_id "$insula_lease")"

  if ! VASO_AGENT="$VASO_AGENT" python3 "$LEASE_PY" acquire \
      --resource gpu \
      --amount "$gpu_amount" \
      --holder "$VASO_AGENT-ci-$profile-$line" \
      --pid "$BASHPID" \
      --ttl "$LEASE_TTL" \
      --timeout "$LEASE_TIMEOUT" >"$gpu_lease" 2>>"$lease_err"; then
    cat "$lease_err" >"$log"
    python3 "$RESULT_PY" stage --stages-jsonl "$stages_jsonl" --name "$name" --exit-code 75 --duration-seconds 0 --log-path "$log"
    record_result "$result_json" "$stages_jsonl" "ci-gate" "$line" "$args_json" "$log" "insula:$line:$insula_id" "$sha" 75 "$started_utc"
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
    CI_LOOP_PROFILE="$profile" \
    CI_LOOP_LINE="$line" \
    CI_LOOP_ACCEPTANCE_OUT="$acceptance_json" \
    timeout "$GATE_TIMEOUT" "$GATE_CMD" \
      --profile "$profile" \
      --line "$line" \
      --commit "$sha" \
      --acceptance-out "$acceptance_json" >"$log" 2>&1
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
  record_result "$result_json" "$stages_jsonl" "ci-gate" "$line" "$args_json" "$log" "insula:$line:$insula_id,gpu:$gpu_id" "$sha" "$rc" "$started_utc"
  exit "$rc"
)

write_summary() {
  local run_dir=$1 run_id=$2 sha=$3 target=$4 started_utc=$5
  python3 "$SUMMARY_PY" \
    --run-dir "$run_dir" \
    --run-id "$run_id" \
    --commit "$sha" \
    --target "$target" \
    --started-utc "$started_utc" \
    --finished-utc "$(stamp)" \
    --host-facts "$run_dir/host-facts.json" \
    --out "$run_dir/summary.json" >/dev/null
}

state="$CI_ROOT/state.json"
sha="$(git -C "$ROOT" rev-parse "$TARGET")"
short_sha="${sha:0:12}"
last_sha="$(json_string_field "$state" last_sha)"
if [[ "$last_sha" == "$sha" ]]; then
  echo "CI_LOOP_SKIPPED same tip $short_sha"
  exit 0
fi

run_id="${CI_LOOP_RUN_ID:-$(date -u +%Y%m%dT%H%M%SZ)-$short_sha}"
run_dir="$CI_ROOT/$run_id"
if [[ -e "$run_dir" ]]; then
  run_id="$run_id-$$"
  run_dir="$CI_ROOT/$run_id"
fi
mkdir -p "$run_dir/logs" "$run_dir/results" "$run_dir/output-bases"
started_utc="$(stamp)"
write_host_facts "$run_dir/host-facts.json"

overall_rc=0
if ! run_standing_verify "$sha" "$run_dir"; then
  overall_rc=1
else
  pids=()
  for profile in torch jax; do
    for line in cu129 cu130; do
      run_gate "$profile" "$line" "$sha" "$run_dir" &
      pids+=("$!")
    done
  done
  for pid in "${pids[@]}"; do
    if ! wait "$pid"; then
      overall_rc=1
    fi
  done
fi

write_summary "$run_dir" "$run_id" "$sha" "$TARGET" "$started_utc"
summary="$run_dir/summary.json"
verdict="$(json_string_field "$summary" verdict)"
write_state "$state" "$sha" "$run_id" "$verdict" "$(stamp)"
append_attention_if_red CI_LOOP_RED "$summary"
echo "CI_LOOP_SUMMARY $summary"
if [[ "$verdict" == "passed" && "$overall_rc" == "0" ]]; then
  exit 0
fi
exit 1
