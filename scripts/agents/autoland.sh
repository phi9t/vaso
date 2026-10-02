#!/usr/bin/env bash
# Lead autoland loop (docs/agents/cross-agent-collaboration.md, "Autoland").
# Keeps the landing branch verified on top of the follower's target without
# a model in the loop. Each new target tip is rebased and verified through
# relay.py; the landing branch moves only on VERIFY_PASSED. The follower keeps
# its interactive session; this replaces only the lead's verify-and-land step.
# Exits when attention is needed:
#   RED        verify failed or rebase conflicted
#   REWRITTEN  the target dropped its old tip
#   STALL      no target move, tracker edit or follower log for STALL_MIN minutes
#   GONE       the follower process exited
#   IO         /tmp or /var/tmp is at or above 80% usage
#   REVIEW     BATCH_HOURS elapsed; time for a batch review
# Non-exiting attention:
#   BACKLOG    the landing branch has too many or too-old unlanded commits
#   UNCOMMITTED the follower has old dirty tracked files with no newer target commit
# Run it from the lead worktree. Settings come from the environment:
#   VASO_ESTATE_ROOT (required), TARGET, LANDING, LEAD_BRANCH, TRACKER,
#   FOLLOWER, FOLLOWER_PID, FOLLOWER_AGENT, AGENT, STALL_MIN, BATCH_HOURS,
#   INTERVAL, BACKLOG_COMMITS, BACKLOG_MINUTES, BACKLOG_RATE_LIMIT_MIN,
#   UNCOMMITTED_MIN, UNCOMMITTED_RATE_LIMIT_MIN, AUTOLAND_LAND, AUTOLAND_GATE,
#   AUTOLAND_GATE_LINES, AUTOLAND_GATE_TIMEOUT, VERIFY
# Watch it with scripts/agents/autoland-status.sh.
set -u
. "$(dirname "$0")/autoland-env.sh" || exit 1
[ -n "$FOLLOWER_PID" ] || { echo "no follower process found in $FOLLOWER; set FOLLOWER_PID" >&2; exit 2; }
export TMPDIR=$AGENT_ROOT/tmp
export VASO_BAZEL_OB=${VASO_BAZEL_OB:-$AGENT_ROOT/bazel-ob}
mkdir -p "$TMPDIR"
# Every stage's exit status must reach relay.py; never end a stage in a pipe
# (a trailing `| tail` once hid a pytest failure and let a red tree land).
DEFAULT_VERIFY='scripts/agents/standing-verify.sh --agent "$AGENT"'
V=${VERIFY:-$DEFAULT_VERIFY}
AUTOLAND_LAND=${AUTOLAND_LAND:-0}
AUTOLAND_GATE=${AUTOLAND_GATE:-0}
AUTOLAND_GATE_LINES=${AUTOLAND_GATE_LINES:-cu130 cu129}
AUTOLAND_GATE_CMD=${AUTOLAND_GATE_CMD:-scripts/agents/triumvirate-gate.sh}
AUTOLAND_GATE_PROFILE=${AUTOLAND_GATE_PROFILE:-torch}
AUTOLAND_GATE_TIMEOUT=${AUTOLAND_GATE_TIMEOUT:-6h}
AUTOLAND_GATE_LEASE_TIMEOUT=${AUTOLAND_GATE_LEASE_TIMEOUT:-60}
AUTOLAND_GATE_LEASE_TTL=${AUTOLAND_GATE_LEASE_TTL:-21600}
AUTOLAND_GATE_GPUS=${AUTOLAND_GATE_GPUS:-8}
GATE_ROOT=$VASO_ESTATE_ROOT/agents/autoland/gate

say() { echo "$(date -u +%FT%TZ) $*" | tee -a "$LOG"; }
say_bg() { echo "$(date -u +%FT%TZ) $*" >> "$LOG"; }
latest_activity() {
  # newest mtime (epoch) among tracker files and follower logs
  find "$FOLLOWER/$TRACKER" "$FOLLOWER_LOGS" -type f -printf '%T@\n' 2>/dev/null \
    | sort -n | tail -1 | cut -d. -f1
}
backlog_snapshot() {
  backlog_commits=$(git rev-list --count "$TARGET..$LANDING" 2>/dev/null || echo 0)
  oldest_ts=$(git log --format=%ct --reverse "$TARGET..$LANDING" 2>/dev/null | head -1)
  if [ -n "$oldest_ts" ]; then
    backlog_oldest_min=$(( (now - oldest_ts) / 60 ))
    [ "$backlog_oldest_min" -lt 0 ] && backlog_oldest_min=0
  else
    backlog_oldest_min=0
  fi
  backlog_active=0
  [ "$backlog_commits" -ge "$BACKLOG_COMMITS" ] && backlog_active=1
  [ "$backlog_oldest_min" -ge "$BACKLOG_MINUTES" ] && backlog_active=1
}
uncommitted_snapshot() {
  uncommitted_count=0
  uncommitted_oldest_min=0
  uncommitted_newest_min=0
  uncommitted_active=0
  local entry path newest oldest target_ts now_mtime
  # Parse porcelain v1 -z. Rename/copy entries have a second NUL path; use the destination path.
  while IFS= read -r -d '' entry; do
    [ -n "$entry" ] || continue
    case "$entry" in
      "?? "*) continue ;;
    esac
    path=${entry#???}
    case "$entry" in
      R*|C*) IFS= read -r -d '' path || true ;;
    esac
    [ -n "$path" ] || continue
    [ -f "$FOLLOWER/$path" ] || continue
    now_mtime=$(stat -c %Y "$FOLLOWER/$path" 2>/dev/null || true)
    [ -n "$now_mtime" ] || continue
    if [ "$uncommitted_count" -eq 0 ]; then
      newest=$now_mtime
      oldest=$now_mtime
    else
      [ "$now_mtime" -gt "$newest" ] && newest=$now_mtime
      [ "$now_mtime" -lt "$oldest" ] && oldest=$now_mtime
    fi
    uncommitted_count=$((uncommitted_count + 1))
  done < <(git -C "$FOLLOWER" status --porcelain -z --untracked-files=no 2>/dev/null || true)
  [ "$uncommitted_count" -gt 0 ] || return 0
  target_ts=$(git -C "$LEAD" log -1 --format=%ct "$TARGET" 2>/dev/null || echo 0)
  [ "${target_ts:-0}" -ge "${newest:-0}" ] && return 0
  uncommitted_newest_min=$(( (now - newest) / 60 ))
  [ "$uncommitted_newest_min" -lt 0 ] && uncommitted_newest_min=0
  uncommitted_oldest_min=$(( (now - oldest) / 60 ))
  [ "$uncommitted_oldest_min" -lt 0 ] && uncommitted_oldest_min=0
  [ "$uncommitted_newest_min" -ge "$UNCOMMITTED_MIN" ] && uncommitted_active=1
}
gate_summary() {
  local line=$1 sha=$2 rc=$3 log_path=$4 json_path=$5
  python3 - "$line" "$sha" "$rc" "$log_path" "$json_path" "$AUTOLAND_GATE_PROFILE" <<'PY'
import json
import re
import sys
import time
from collections import OrderedDict
from pathlib import Path

line, sha, rc_s, log_path_s, json_path_s, profile = sys.argv[1:]
rc = int(rc_s)
log_path = Path(log_path_s)
json_path = Path(json_path_s)
doc = {}
try:
    doc = json.loads(json_path.read_text(encoding="utf-8"))
except (FileNotFoundError, json.JSONDecodeError):
    doc = {}

stages: OrderedDict[str, str] = OrderedDict()
for item in doc.get("sub_results", []) if isinstance(doc.get("sub_results", []), list) else []:
    if not isinstance(item, dict):
        continue
    stage = str(item.get("stage") or item.get("name") or "unknown")
    verdict = str(item.get("verdict") or "").lower()
    try:
        item_rc = int(item.get("returncode", 1))
    except (TypeError, ValueError):
        item_rc = 1
    failed = verdict == "failed" or item_rc != 0
    if stage not in stages or stages[stage] != "failed":
        stages[stage] = "failed" if failed else "passed"

if not stages and log_path.is_file():
    pattern = re.compile(r"\bGATE_STAGE\s+([A-Za-z0-9_.:-]+)\s+(passed|failed|PASS|FAIL)\b")
    for match in pattern.finditer(log_path.read_text(encoding="utf-8", errors="replace")):
        stages[match.group(1)] = "failed" if match.group(2).lower().startswith("fail") else "passed"

if not stages:
    stages["process"] = "passed" if rc == 0 else "failed"

failed_stages = [stage for stage, verdict in stages.items() if verdict == "failed"]
passed_stages = [stage for stage, verdict in stages.items() if verdict != "failed"]
verdict = str(doc.get("verdict") or ("failed" if failed_stages or rc != 0 else "passed")).lower()
if rc != 0 and verdict == "passed":
    verdict = "failed"
first = failed_stages[0] if failed_stages else "none"

doc.update({
    "schema_version": doc.get("schema_version", 1),
    "profile": doc.get("profile", profile),
    "line": doc.get("line", line),
    "commit": doc.get("commit", sha),
    "verdict": verdict,
    "first_failing_stage": first,
    "stage_summary": {
        "passed": passed_stages,
        "failed": failed_stages,
    },
    "returncode": rc,
    "log": str(log_path),
    "updated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
})
json_path.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n", encoding="utf-8")
print(f"{verdict} {first}")
PY
}
gate_previous_verdict() {
  local path=$1
  [ -f "$path" ] || return 0
  python3 - "$path" <<'PY'
import json
import sys
try:
    print(json.load(open(sys.argv[1], encoding="utf-8")).get("verdict", ""))
except Exception:
    pass
PY
}
gate_lease_env() {
  python3 - "$1" <<'PY'
import json
import shlex
import sys
doc = json.load(open(sys.argv[1], encoding="utf-8"))
for key, value in sorted(doc.get("env", {}).items()):
    print(f"export {key}={shlex.quote(str(value))}")
PY
}
run_gate_for_line() {
  local line=$1 sha=$2 short_sha=${2:0:12}
  local json_path="$GATE_ROOT/$short_sha-$line.json"
  local latest_path="$GATE_ROOT/latest-$line.json"
  local log_path="$GATE_ROOT/$short_sha-$line.log"
  local lease_path="$GATE_ROOT/$short_sha-$line.lease.json"
  local lease_err="$GATE_ROOT/$short_sha-$line.lease.err"
  local lease_id="" gate_rc=0 verdict first prev
  mkdir -p "$GATE_ROOT"
  prev=$(gate_previous_verdict "$latest_path")
  VASO_AGENT=autoland python3 scripts/insula/lease.py acquire --resource gpu --amount "$AUTOLAND_GATE_GPUS" \
    --holder autoland --pid "$BASHPID" --ttl "$AUTOLAND_GATE_LEASE_TTL" \
    --timeout "$AUTOLAND_GATE_LEASE_TIMEOUT" >"$lease_path" 2>"$lease_err"
  gate_rc=$?
  if [ "$gate_rc" != 0 ]; then
    {
      echo "GPU lease failed for $line"
      cat "$lease_err"
    } >"$log_path"
    read -r verdict first < <(gate_summary "$line" "$sha" "$gate_rc" "$log_path" "$json_path")
    cp "$json_path" "$latest_path"
    say_bg "GATE $line $verdict $first"
    [ "$prev" = "passed" ] && [ "$verdict" = "failed" ] && say_bg "REGRESSION GATE $line failed $first"
    return 0
  fi
  eval "$(gate_lease_env "$lease_path")"
  lease_id=$(python3 - "$lease_path" <<'PY'
import json
import sys
print(json.load(open(sys.argv[1], encoding="utf-8")).get("id", ""))
PY
)
  set +e
  VASO_AGENT=autoland timeout "$AUTOLAND_GATE_TIMEOUT" "$AUTOLAND_GATE_CMD" \
    --profile "$AUTOLAND_GATE_PROFILE" --line "$line" --commit "$sha" \
    --acceptance-out "$json_path" >"$log_path" 2>&1
  gate_rc=$?
  set -e
  if [ -n "$lease_id" ]; then
    python3 scripts/insula/lease.py release --id "$lease_id" --resource gpu >>"$log_path" 2>&1 || true
  fi
  read -r verdict first < <(gate_summary "$line" "$sha" "$gate_rc" "$log_path" "$json_path")
  cp "$json_path" "$latest_path"
  say_bg "GATE $line $verdict $first"
  [ "$prev" = "passed" ] && [ "$verdict" = "failed" ] && say_bg "REGRESSION GATE $line failed $first"
}
launch_gates() {
  local sha=$1 line
  [ "$AUTOLAND_GATE" = 1 ] || return 0
  for line in $AUTOLAND_GATE_LINES; do
    run_gate_for_line "$line" "$sha" >/dev/null 2>&1 &
  done
}

cd "$LEAD"
start=$(date +%s)
# start from what the lead branch already sits on, so a move made before startup is handled
last=$(git merge-base "$LEAD_BRANCH" "$TARGET")
last_move=$(git log -1 --format=%ct "$TARGET")  # idle clock starts at the last target commit
backlog_was_active=0
last_backlog_log=0
uncommitted_was_active=0
last_uncommitted_log=0
say "START target=${last:0:7} landing=$(git rev-parse --short $LANDING)"
while :; do
  now=$(date +%s)
  kill -0 "$FOLLOWER_PID" 2>/dev/null || { say "GONE follower pid $FOLLOWER_PID exited"; exit 0; }
  for d in /tmp /var/tmp; do
    use=$(df --output=pcent "$d" | tail -1 | tr -dc 0-9)
    [ "${use:-0}" -ge 80 ] && { say "IO $d at ${use}%"; exit 0; }
  done
  tip=$(git rev-parse "$TARGET")
  if [ "$tip" != "$last" ]; then
    if ! git merge-base --is-ancestor "$last" "$tip"; then
      say "REWRITTEN target ${last:0:7} -> ${tip:0:7} dropped the old tip"; exit 0
    fi
    say "MOVED ${last:0:7} -> ${tip:0:7}: $(git log --format=%s -1 $tip)"
    last=$tip; last_move=$now
    out=$(python3 scripts/agents/relay.py rebase --lead-worktree . --target "$TARGET" --agent "$AGENT" \
          --io-root "$VASO_ESTATE_ROOT" --verify "$V" 2>&1)
    echo "$out" >> "$LOG"
    if echo "$out" | grep -q '^VERIFY_PASSED'; then
      git branch -f "$LANDING" "$LEAD_BRANCH"
      say "LANDED $LANDING -> $(git rev-parse --short $LANDING) ($(echo "$out" | grep -E 'Executed|passed' | tr '\n' ' '))"
      if [ "$AUTOLAND_LAND" = 1 ]; then
        land_out=$(python3 scripts/agents/relay.py autoland-step --lead-worktree . --follower "$FOLLOWER" \
          --target "$TARGET" --landing "$LANDING" --tracker "$TRACKER" \
          --premerge-dir "$AGENT_ROOT/autoland-premerge" --agent "$AGENT" --io-root "$VASO_ESTATE_ROOT" 2>&1)
        land_rc=$?
        echo "$land_out" >> "$LOG"
        land_last=$(printf '%s\n' "$land_out" | tail -1)
        if [ "$land_rc" = 0 ]; then
          say "LAND_STEP ${land_last:-no output}"
          if printf '%s\n' "$land_out" | grep -q '^AUTOLAND_LANDED'; then
            launch_gates "$(git rev-parse "$LANDING")"
          fi
        else
          say "RED autoland-step rc=$land_rc ${land_last:-no output}"
          exit 0
        fi
      fi
    else
      say "RED $(echo "$out" | grep -E 'VERIFY_FAILED|REBASE_CONFLICT|FAILED|Error' | head -3 | tr '\n' ' ')"; exit 0
    fi
  fi
  backlog_snapshot
  if [ "$backlog_active" = 1 ] && [ "$backlog_was_active" = 0 ]; then
    if [ $(( now - last_backlog_log )) -ge $(( BACKLOG_RATE_LIMIT_MIN * 60 )) ]; then
      say "BACKLOG $backlog_commits commits, oldest $backlog_oldest_min min"
      last_backlog_log=$now
    fi
  fi
  backlog_was_active=$backlog_active
  uncommitted_snapshot
  if [ "$uncommitted_active" = 1 ] && [ "$uncommitted_was_active" = 0 ]; then
    if [ $(( now - last_uncommitted_log )) -ge $(( UNCOMMITTED_RATE_LIMIT_MIN * 60 )) ]; then
      say "UNCOMMITTED $uncommitted_count files, oldest $uncommitted_oldest_min min"
      last_uncommitted_log=$now
    fi
  fi
  uncommitted_was_active=$uncommitted_active
  act=$(latest_activity); act=${act:-0}
  [ "$last_move" -gt "$act" ] && act=$last_move
  if [ $(( now - act )) -ge $(( STALL_MIN * 60 )) ]; then
    say "STALL no target move, tracker edit or follower log for $(( (now - act) / 60 )) min"; exit 0
  fi
  if [ $(( now - start )) -ge $(( BATCH_HOURS * 3600 )) ]; then
    say "REVIEW ${BATCH_HOURS}h elapsed; target=$(git rev-parse --short $TARGET)"; exit 0
  fi
  # heartbeat for autoland-status.sh
  printf 'pid=%s beat=%s target=%s landing=%s last_move=%s last_activity=%s start=%s stall_min=%s batch_hours=%s backlog_commits=%s backlog_oldest_min=%s backlog_active=%s uncommitted_count=%s uncommitted_oldest_min=%s uncommitted_newest_min=%s uncommitted_active=%s\n' \
    "$$" "$now" "$(git rev-parse --short "$TARGET")" "$(git rev-parse --short "$LANDING")" \
    "$last_move" "$act" "$start" "$STALL_MIN" "$BATCH_HOURS" \
    "$backlog_commits" "$backlog_oldest_min" "$backlog_active" \
    "$uncommitted_count" "$uncommitted_oldest_min" "$uncommitted_newest_min" "$uncommitted_active" > "$STATE.tmp" && mv "$STATE.tmp" "$STATE"
  sleep "$INTERVAL"
done
