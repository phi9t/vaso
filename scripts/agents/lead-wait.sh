#!/usr/bin/env bash
# Wait for the next autoland event that needs lead attention.
#
# The saved offset lets a supervisor re-arm this command after handling an
# event without re-firing on the same log line.
set -u

SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
. "$SCRIPT_DIR/autoland-env.sh" || exit 1

OFFSET_FILE=${OFFSET_FILE:-$AGENT_ROOT/lead-wait.offset}
MAX_SECONDS=0
INTERVAL_SECONDS=5
FROM_START=0

usage() {
  cat >&2 <<'EOF'
usage: lead-wait.sh [--log PATH] [--offset-file PATH] [--max-seconds N] [--interval N] [--from-start]

Exit 0 and print the first new RED, REGRESSION, STALL, REVIEW, GONE, IO or
REWRITTEN line.
BACKLOG is deliberately not a wake-up: it is for the human (cockpit), and a
chronic backlog would otherwise wake the lead every 30 minutes.
Exit 3 with QUIET when --max-seconds elapses without an attention line.
EOF
}

die() {
  echo "REFUSED: $*" >&2
  exit 2
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --log) LOG=${2:?}; shift 2 ;;
    --offset-file) OFFSET_FILE=${2:?}; shift 2 ;;
    --max-seconds) MAX_SECONDS=${2:?}; shift 2 ;;
    --interval) INTERVAL_SECONDS=${2:?}; shift 2 ;;
    --from-start) FROM_START=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) usage; exit 2 ;;
  esac
done

mkdir -p "$(dirname "$OFFSET_FILE")"

attention_line() {
  awk '$2 ~ /^(RED|REGRESSION|REWRITTEN|STALL|GONE|IO|REVIEW)$/ { print; exit }'
}

file_size() {
  if [ -f "$LOG" ]; then
    wc -c < "$LOG" | tr -dc 0-9
  else
    printf '0\n'
  fi
}

read_offset() {
  if [ -f "$OFFSET_FILE" ]; then
    read -r value < "$OFFSET_FILE" || value=0
    case "$value" in ""|*[!0-9]*) printf '0\n' ;; *) printf '%s\n' "$value" ;; esac
  elif [ "$FROM_START" = 1 ]; then
    printf '0\n'
  else
    file_size
  fi
}

write_offset() {
  local value=$1 tmp
  tmp=$OFFSET_FILE.$$.tmp
  printf '%s\n' "$value" > "$tmp" && mv "$tmp" "$OFFSET_FILE"
}

start_time=$(date +%s)
offset=$(read_offset)
write_offset "$offset"

while :; do
  size=$(file_size)
  if [ "$size" -lt "$offset" ]; then
    offset=0
  fi
  if [ "$size" -gt "$offset" ] && [ -f "$LOG" ]; then
    chunk=$(tail -c +"$((offset + 1))" "$LOG")
    line=$(printf '%s\n' "$chunk" | attention_line)
    offset=$size
    write_offset "$offset"
    if [ -n "$line" ]; then
      printf '%s\n' "$line"
      exit 0
    fi
  fi
  if [ "$MAX_SECONDS" != 0 ]; then
    now=$(date +%s)
    if [ $((now - start_time)) -ge "$MAX_SECONDS" ]; then
      write_offset "$(file_size)"
      printf 'QUIET %ss offset=%s\n' "$MAX_SECONDS" "$(cat "$OFFSET_FILE")"
      exit 3
    fi
  fi
  sleep "$INTERVAL_SECONDS"
done
