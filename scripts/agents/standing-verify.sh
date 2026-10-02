#!/usr/bin/env bash
# The standing verify every agent runs before a commit moves the target or the
# landing branch: the //tools guards plus the planner tests, pytest and the
# cargo suites. One definition, so "focused tests passed" can't stand in for it.
#
#   VASO_ESTATE_ROOT=... scripts/agents/standing-verify.sh [--agent NAME]
#
# Bazel uses $VASO_BAZEL_OB, or <estate>/agents/<agent>/bazel-ob-verify.
# Logs go to $TMPDIR (default <estate>/agents/<agent>/tmp). Exit 0 only if all
# three stages pass. No stage ends in a pipe, so every exit status counts.
set -uo pipefail
AGENT=${VASO_AGENT:-lead}
while [ $# -gt 0 ]; do
  case "$1" in
    --agent) AGENT=${2:?}; shift 2 ;;
    -h|--help) sed -n '2,11p' "$0"; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done
: "${VASO_ESTATE_ROOT:?set VASO_ESTATE_ROOT}"
ROOT=$(git rev-parse --show-toplevel)
BAZEL=${BAZEL_BIN:-$HOME/.vaso-estate/opt-vaso/bin/bazel-real}
export TMPDIR=${TMPDIR_VERIFY:-$VASO_ESTATE_ROOT/agents/$AGENT/tmp}
export VASO_BAZEL_OB=${VASO_BAZEL_OB:-$VASO_ESTATE_ROOT/agents/$AGENT/bazel-ob-verify}
export CARGO_TARGET_DIR=${CARGO_TARGET_DIR:-$VASO_ESTATE_ROOT/agents/$AGENT/cargo-target}
ROOT_SHA=$(git -C "$ROOT" rev-parse HEAD 2>/dev/null || echo unknown)
RED_ROOT=$VASO_ESTATE_ROOT/agents/reds
ATTENTION_LOG=${ATTENTION_LOG:-$VASO_ESTATE_ROOT/agents/${ATTENTION_AGENT:-claude}/autoland.log}
mkdir -p "$TMPDIR"

red_registry() {
  local action=$1 stage=$2 target=${3:-}
  python3 - "$action" "$RED_ROOT" "$ATTENTION_LOG" "$AGENT" "$ROOT_SHA" "$stage" "$target" <<'PY'
import datetime as dt
import fcntl
import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path

action, red_root_s, attention_log_s, agent, sha, stage, target = sys.argv[1:]
red_root = Path(red_root_s)
attention_log = Path(attention_log_s)
if action == "clear-stage" and not red_root.exists():
    raise SystemExit(0)
red_root.mkdir(parents=True, exist_ok=True)
lock_path = red_root / ".lock"

def stamp(epoch: float) -> str:
    return dt.datetime.fromtimestamp(epoch, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

def safe_name(value: str) -> str:
    base = re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("_") or "red"
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:8]
    return f"{base}-{digest}.json"

def read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}

def write_json(path: Path, value: dict) -> None:
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)

with lock_path.open("a", encoding="utf-8") as lock:
    fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
    now = time.time()
    now_s = stamp(now)
    try:
        if action == "record":
            path = red_root / safe_name(target)
            existing = read_json(path)
            previous_agents = [str(item) for item in existing.get("agents", [])]
            agents = list(previous_agents)
            if agent not in agents:
                agents.append(agent)
            first_epoch = existing.get("first_seen_epoch", now)
            try:
                first_epoch = float(first_epoch)
            except (TypeError, ValueError):
                first_epoch = now
            age = max(0, int(now - first_epoch))
            second_distinct = agent not in previous_agents and len(previous_agents) >= 1
            stale = age >= 3600 and not existing.get("stale_escalated")
            shared = second_distinct and not existing.get("shared_escalated")
            doc = {
                "schema_version": 1,
                "target": target,
                "stage": stage,
                "agents": agents,
                "first_seen": existing.get("first_seen", now_s),
                "first_seen_epoch": first_epoch,
                "last_seen": now_s,
                "last_seen_epoch": now,
                "sha": sha,
                "shared_escalated": bool(existing.get("shared_escalated")) or shared,
                "stale_escalated": bool(existing.get("stale_escalated")) or stale,
            }
            write_json(path, doc)
            if shared or stale:
                attention_log.parent.mkdir(parents=True, exist_ok=True)
                with attention_log.open("a", encoding="utf-8") as out:
                    out.write(f"{now_s} SHARED_RED {target} agents={','.join(agents)} age={age}s\n")
        elif action == "clear-stage":
            for path in red_root.glob("*.json"):
                doc = read_json(path)
                if doc.get("stage") == stage:
                    path.unlink(missing_ok=True)
        else:
            raise SystemExit(f"unknown red registry action: {action}")
    finally:
        fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
PY
}

failing_targets() {
  local stage=$1 log=$2 fallback=$3
  python3 - "$stage" "$log" "$fallback" <<'PY'
import re
import sys
from pathlib import Path

stage, log_path, fallback = sys.argv[1:]
labels = []
seen = set()
pattern = re.compile(r"//[A-Za-z0-9_./+-]+:[A-Za-z0-9_./+-]+")
try:
    lines = Path(log_path).read_text(encoding="utf-8", errors="replace").splitlines()
except FileNotFoundError:
    lines = []
for line in lines:
    if not any(word in line for word in ("FAILED", "FAIL:", "ERROR:")):
        continue
    for label in pattern.findall(line):
        if label not in seen:
            seen.add(label)
            labels.append(label)
if not labels:
    labels.append(fallback or stage)
for label in labels:
    print(label)
PY
}

record_stage_failure() {
  local stage=$1 rc=$2 log=$3 fallback=$4 target
  while IFS= read -r target; do
    [ -n "$target" ] || continue
    red_registry record "$stage" "$target"
  done < <(failing_targets "$stage" "$log" "$fallback")
}

(cd "$ROOT/experiments/spack-bazel-graph" && "$BAZEL" --output_base="$VASO_BAZEL_OB" test \
  //tools:all //workloads:criteria_test //native/pytorch:plan_test //native/llvm:plan_test //native/py_llvmlite:plan_test \
  --test_tag_filters=-insula-only >"$TMPDIR/verify-bazel.log" 2>&1)
rc=$?
grep -E "Executed|FAILED" "$TMPDIR/verify-bazel.log" | head -12
if [ "$rc" = 0 ]; then
  red_registry clear-stage bazel
else
  record_stage_failure bazel "$rc" "$TMPDIR/verify-bazel.log" bazel
  echo "STANDING_VERIFY_FAILED stage=bazel rc=$rc log=$TMPDIR/verify-bazel.log"
  exit "$rc"
fi

if [ -n "${STANDING_VERIFY_PYTEST_CMD:-}" ]; then
  (cd "$ROOT" && bash -c "$STANDING_VERIFY_PYTEST_CMD" >"$TMPDIR/verify-pytest.log" 2>&1)
else
  (cd "$ROOT" && PYTHONDONTWRITEBYTECODE=1 /usr/bin/python3 -m pytest -q -p no:cacheprovider tests >"$TMPDIR/verify-pytest.log" 2>&1)
fi
rc=$?
tail -1 "$TMPDIR/verify-pytest.log"
if [ "$rc" = 0 ]; then
  red_registry clear-stage pytest
else
  record_stage_failure pytest "$rc" "$TMPDIR/verify-pytest.log" pytest:tests
  echo "STANDING_VERIFY_FAILED stage=pytest rc=$rc log=$TMPDIR/verify-pytest.log"
  exit "$rc"
fi

if [ -n "${STANDING_VERIFY_CARGO_CMD:-}" ]; then
  (cd "$ROOT" && bash -c "$STANDING_VERIFY_CARGO_CMD" >"$TMPDIR/verify-cargo.log" 2>&1)
else
  (cd "$ROOT" && cargo test --offline -q --manifest-path crates/autoland-tui/Cargo.toml >"$TMPDIR/verify-cargo.log" 2>&1)
fi
rc=$?
echo "cargo suites ok: $(grep -c "test result: ok" "$TMPDIR/verify-cargo.log")"
if [ "$rc" = 0 ]; then
  red_registry clear-stage cargo
else
  record_stage_failure cargo "$rc" "$TMPDIR/verify-cargo.log" cargo:autoland-tui
  echo "STANDING_VERIFY_FAILED stage=cargo rc=$rc log=$TMPDIR/verify-cargo.log"
  exit "$rc"
fi
echo "STANDING_VERIFY_PASSED $(git -C "$ROOT" rev-parse --short HEAD)"
