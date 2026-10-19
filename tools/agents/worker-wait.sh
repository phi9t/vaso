#!/usr/bin/env bash
# Light supervisor for briefed workers: a zero-token wait the lead runs as a
# background shell. It prints one line and exits when attention is needed:
#   EXITED <agent> rc=<n>     a worker's run wrote exit_code
#   STALLED <agent> <s>s      a worker's events.jsonl was quiet for STALL_SECONDS
#   LOOP <line>               an autoland attention event (with --loop-log)
# Usage: worker-wait.sh [--stall-seconds N] [--interval N] [--loop-log FILE] RUN_DIR...
set -u
stall=1800 interval=30 loop_log=""
while [ $# -gt 0 ]; do
  case "$1" in
    --stall-seconds) stall=$2; shift 2 ;;
    --interval) interval=$2; shift 2 ;;
    --loop-log) loop_log=$2; shift 2 ;;
    -h|--help) sed -n '2,9p' "$0"; exit 0 ;;
    *) break ;;
  esac
done
[ $# -gt 0 ] || { echo "usage: worker-wait.sh [options] RUN_DIR..." >&2; exit 2; }
for run in "$@"; do [ -d "$run" ] || { echo "no run dir: $run" >&2; exit 2; }; done
offset=0
[ -n "$loop_log" ] && [ -f "$loop_log" ] && offset=$(stat -c %s "$loop_log")
agent_of() { basename "$(dirname "$(dirname "$1")")"; }
while :; do
  now=$(date +%s)
  for run in "$@"; do
    if [ -f "$run/exit_code" ]; then echo "EXITED $(agent_of "$run") rc=$(cat "$run/exit_code") run=$run"; exit 0; fi
    events=$run/events.jsonl
    last=$(stat -c %Y "$events" 2>/dev/null || stat -c %Y "$run")
    if [ $(( now - last )) -ge "$stall" ]; then echo "STALLED $(agent_of "$run") $(( now - last ))s run=$run"; exit 0; fi
  done
  if [ -n "$loop_log" ] && [ -f "$loop_log" ]; then
    hit=$(tail -c +$(( offset + 1 )) "$loop_log" | grep -E ' (RED|REWRITTEN|STALL|GONE|IO|REVIEW) ' | head -1)
    [ -n "$hit" ] && { echo "LOOP $hit"; exit 0; }
  fi
  sleep "$interval"
done
