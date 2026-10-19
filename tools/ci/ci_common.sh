#!/usr/bin/env bash
# Shared shell helpers for scheduled CI jobs.

stamp() {
  date -u +%Y-%m-%dT%H:%M:%SZ
}

duration_seconds() {
  python3 - "$1" "$2" <<'PY'
import sys

print(f"{(int(sys.argv[2]) - int(sys.argv[1])) / 1_000_000_000:.3f}")
PY
}

json_string_field() {
  python3 - "$1" "$2" <<'PY'
import json
import sys
from pathlib import Path

try:
    doc = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
except (FileNotFoundError, json.JSONDecodeError):
    doc = {}
print(doc.get(sys.argv[2], ""))
PY
}

write_state() {
  python3 - "$1" "$2" "$3" "$4" "$5" <<'PY'
import json
import os
import sys
from pathlib import Path

path = Path(sys.argv[1])
doc = {
    "schema_version": 1,
    "last_sha": sys.argv[2],
    "last_run_id": sys.argv[3],
    "last_verdict": sys.argv[4],
    "updated_utc": sys.argv[5],
}
path.parent.mkdir(parents=True, exist_ok=True)
tmp = path.with_name(f"{path.name}.tmp.{os.getpid()}")
tmp.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n", encoding="utf-8")
tmp.replace(path)
PY
}

write_host_facts() {
  local out=$1 nproc_value ram_kib gpu_count
  nproc_value="$(nproc 2>/dev/null || echo 1)"
  ram_kib="$(awk '/^MemTotal:/ {print $2}' /proc/meminfo 2>/dev/null || true)"
  ram_kib="${ram_kib:-0}"
  if [[ -n "${VASO_GPU_COUNT:-}" ]]; then
    gpu_count="$VASO_GPU_COUNT"
  elif command -v nvidia-smi >/dev/null 2>&1; then
    gpu_count="$(nvidia-smi -L 2>/dev/null | awk '/^GPU / {count++} END {print count + 0}')"
  else
    gpu_count=0
  fi
  python3 - "$out" "$nproc_value" "$ram_kib" "$gpu_count" "$(stamp)" <<'PY'
import json
import os
import sys
from pathlib import Path

path = Path(sys.argv[1])
doc = {
    "schema_version": 1,
    "nproc": int(sys.argv[2]),
    "ram_kib": int(sys.argv[3]),
    "gpu_count": int(sys.argv[4]),
    "recorded_utc": sys.argv[5],
}
path.parent.mkdir(parents=True, exist_ok=True)
tmp = path.with_name(f"{path.name}.tmp.{os.getpid()}")
tmp.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n", encoding="utf-8")
tmp.replace(path)
PY
}

lease_id() {
  python3 - "$1" <<'PY'
import json
import sys
from pathlib import Path

try:
    doc = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
except (FileNotFoundError, json.JSONDecodeError):
    doc = {}
print(doc.get("id", ""))
PY
}

lease_env_exports() {
  python3 - "$1" <<'PY'
import json
import shlex
import sys
from pathlib import Path

try:
    doc = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
except (FileNotFoundError, json.JSONDecodeError):
    doc = {}
env = doc.get("env", {})
if isinstance(env, dict):
    for key, value in sorted(env.items()):
        print(f"export {key}={shlex.quote(str(value))}")
PY
}

record_result() {
  local out=$1 stages_jsonl=$2 mode=$3 line=$4 args_json=$5 log_path=$6 leases=$7 commit=$8 rc=$9 started_utc=${10}
  local verdict="${11:-}" blocked_reason="${12:-}"
  local -a args=(
    "$RESULT_PY" finalize
    --out "$out" \
    --stages-jsonl "$stages_jsonl" \
    --mode "$mode" \
    --line "$line" \
    --agent "$VASO_AGENT" \
    --args-json "$args_json" \
    --log-path "$log_path" \
    --leases "$leases" \
    --commit "$commit" \
    --exit-code "$rc" \
    --started-utc "$started_utc"
  )
  if [[ -n "$verdict" ]]; then
    args+=(--verdict "$verdict")
  fi
  if [[ -n "$blocked_reason" ]]; then
    args+=(--blocked-reason "$blocked_reason")
  fi
  python3 "${args[@]}" >/dev/null
}

run_logged_stage() {
  local stage_name=$1 stages_jsonl=$2 log_path=$3
  shift 3
  local start_ns end_ns duration rc
  : >"$log_path"
  start_ns="$(date +%s%N)"
  set +e
  "$@" >"$log_path" 2>&1
  rc=$?
  set -e
  end_ns="$(date +%s%N)"
  duration="$(duration_seconds "$start_ns" "$end_ns")"
  python3 "$RESULT_PY" stage \
    --stages-jsonl "$stages_jsonl" \
    --name "$stage_name" \
    --exit-code "$rc" \
    --duration-seconds "$duration" \
    --log-path "$log_path"
  return "$rc"
}

append_attention_if_red() {
  local prefix summary verdict
  if [[ "$#" -eq 1 ]]; then
    prefix=CI_LOOP_RED
    summary=$1
  else
    prefix=$1
    summary=$2
  fi
  verdict="$(json_string_field "$summary" verdict)"
  if [[ "$verdict" != "passed" ]]; then
    mkdir -p "$(dirname "$ATTENTION_LOG")"
    echo "$(stamp) $prefix summary=$summary" >>"$ATTENTION_LOG"
  fi
}
