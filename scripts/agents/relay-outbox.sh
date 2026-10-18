#!/usr/bin/env bash
# Draft, list, send and drop relay messages for the cockpit.
#
# Drafts live in $VASO_ESTATE_ROOT/agents/relay/outbox. Sending always
# re-captures the target pane, refuses unless it is idle at the prompt, pastes
# through a tmux buffer loaded from a file under $VASO_AGENT_IO_ROOT, then logs
# the send in outbox.log.
set -u

SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
. "$SCRIPT_DIR/autoland-env.sh" || exit 1

RELAY_ROOT=${VASO_AGENT_IO_ROOT:-$VASO_ESTATE_ROOT/agents/relay}
OUTBOX_ROOT=${OUTBOX_ROOT:-$VASO_ESTATE_ROOT/agents/relay/outbox}
OUTBOX_LOG=${OUTBOX_LOG:-$VASO_ESTATE_ROOT/agents/relay/outbox.log}
BUFFER_NAME=${BUFFER_NAME:-relay-outbox}
mkdir -p "$OUTBOX_ROOT" "$RELAY_ROOT/tmp"

usage() {
  cat >&2 <<'EOF'
usage:
  relay-outbox.sh draft [--author NAME] [--pane PANE] [--title TITLE] [--file PATH]
  relay-outbox.sh list
  relay-outbox.sh send [--pane PANE] DRAFT
  relay-outbox.sh drop DRAFT
EOF
}

die() {
  echo "REFUSED: $*" >&2
  exit 2
}

stamp() {
  date -u +%Y-%m-%dT%H:%M:%SZ
}

file_stamp() {
  date -u +%Y%m%dT%H%M%SZ
}

slugify() {
  local slug
  slug=$(printf '%s' "$1" | tr '[:upper:]' '[:lower:]' | sed -E 's/[^a-z0-9]+/-/g; s/^-+//; s/-+$//')
  printf '%s\n' "${slug:-message}"
}

resolve_draft() {
  case "$1" in
    /*) printf '%s\n' "$1" ;;
    */*) printf '%s\n' "$PWD/$1" ;;
    *) printf '%s\n' "$OUTBOX_ROOT/$1" ;;
  esac
}

front_value() {
  local path=$1 key=$2
  awk -F': ' -v key="$key" '
    NR == 1 && $0 == "---" { fm = 1; next }
    fm && $0 == "---" { exit }
    fm && $1 == key { print substr($0, length($1) + 3); exit }
  ' "$path"
}

draft_body() {
  awk '
    NR == 1 && $0 == "---" { fm = 1; next }
    fm && $0 == "---" { fm = 0; skip_blank = 1; next }
    skip_blank && $0 == "" { skip_blank = 0; next }
    { skip_blank = 0 }
    !fm { print }
  ' "$1"
}

mark_status() {
  local path=$1 status=$2 sent=${3:-} tmp
  tmp=$RELAY_ROOT/tmp/$(basename "$path").$$.tmp
  awk -v status="$status" -v sent="$sent" '
    NR == 1 && $0 == "---" { fm = 1; print; next }
    fm && $0 == "---" {
      if (!saw_status) print "status: " status
      if (sent != "" && !saw_sent) print "sent: " sent
      print
      fm = 0
      next
    }
    fm && /^status:/ { print "status: " status; saw_status = 1; next }
    fm && /^sent:/ {
      if (sent != "") print "sent: " sent
      saw_sent = 1
      next
    }
    { print }
  ' "$path" > "$tmp" && mv "$tmp" "$path"
}

log_event() {
  mkdir -p "$(dirname "$OUTBOX_LOG")"
  printf '%s %s\n' "$(stamp)" "$*" >> "$OUTBOX_LOG"
}

last_capture_lines() {
  # traecli's busy marker ("esc to interrupt") sits above the to-do list and
  # the prompt box, 10+ lines from the bottom, so look at a generous window
  # of non-blank lines (the marker is redrawn in place, never left in history).
  printf '%s\n' "$1" | grep -v '^[[:space:]]*$' | tail -n 40
}

capture_pane() {
  local pane=$1
  autoland_tmux capture-pane -p -e -J -S -20 -t "$pane"
}

pane_is_idle() {
  local pane=$1 capture tail
  capture=$(capture_pane "$pane") || die "pane $pane is not available"
  tail=$(last_capture_lines "$capture")
  # Busy markers: the interrupt hint, the turn timer's token counter (the
  # status line can be caught mid-redraw without the hint) and a message
  # already queued behind a tool call. "you can still chat" is an idle session
  # with only a background shell.
  if printf '%s\n' "$tail" | grep -vi 'you can still chat' | grep -Eqi 'esc to interrupt|tokens •|send after tool call'; then
    return 1
  fi
  printf '%s\n' "$tail" | grep -F '❯' >/dev/null
}

cmd_draft() {
  local author=${AGENT:-lead} pane=${FOLLOWER_PANE:-} title="message" input_file= body slug path base created
  while [ "$#" -gt 0 ]; do
    case "$1" in
      --author) author=${2:?}; shift 2 ;;
      --pane) pane=${2:?}; shift 2 ;;
      --title) title=${2:?}; shift 2 ;;
      --file) input_file=${2:?}; shift 2 ;;
      -h|--help) usage; return 0 ;;
      *) die "unknown draft argument $1" ;;
    esac
  done
  if [ -n "$input_file" ]; then
    body=$(cat "$input_file")
  else
    body=$(cat)
  fi
  created=$(stamp)
  slug=$(slugify "$title")
  base=$OUTBOX_ROOT/$(file_stamp)-$slug.md
  path=$base
  local index=2
  while [ -e "$path" ]; do
    path=${base%.md}-$index.md
    index=$((index + 1))
  done
  {
    printf '%s\n' '---'
    printf 'author: %s\n' "$author"
    printf 'target_pane: %s\n' "$pane"
    printf 'status: draft\n'
    printf 'created: %s\n' "$created"
    printf 'sent: \n'
    printf 'title: %s\n' "$title"
    printf '%s\n\n' '---'
    printf '%s\n' "$body"
  } > "$path"
  log_event "DRAFT $(basename "$path") pane=${pane:-none} author=$author"
  printf '%s\n' "$path"
}

cmd_list() {
  local path status pane author title
  for path in "$OUTBOX_ROOT"/*.md; do
    [ -e "$path" ] || return 0
    status=$(front_value "$path" status)
    pane=$(front_value "$path" target_pane)
    author=$(front_value "$path" author)
    title=$(front_value "$path" title)
    printf '%s\t%s\tpane=%s\tauthor=%s\t%s\n' "$(basename "$path")" "${status:-?}" "${pane:-none}" "${author:-?}" "$title"
  done
}

cmd_send() {
  local pane= path status body payload sent_at bytes
  while [ "$#" -gt 0 ]; do
    case "$1" in
      --pane) pane=${2:?}; shift 2 ;;
      -h|--help) usage; return 0 ;;
      *) path=$(resolve_draft "$1"); shift ;;
    esac
  done
  [ -n "$path" ] || die "send needs a draft path"
  [ -f "$path" ] || die "draft not found: $path"
  status=$(front_value "$path" status)
  [ "$status" = "draft" ] || die "draft status is $status, not draft"
  pane=${pane:-$(front_value "$path" target_pane)}
  pane=${pane:-${FOLLOWER_PANE:-}}
  [ -n "$pane" ] && [ "$pane" != "none" ] || die "no target pane"
  pane_is_idle "$pane" || die "pane $pane is not idle"

  payload=$RELAY_ROOT/tmp/outbox-send-$(basename "$path").$$.txt
  draft_body "$path" > "$payload"
  autoland_tmux load-buffer -b "$BUFFER_NAME" "$payload" || die "tmux load-buffer failed"
  autoland_tmux paste-buffer -p -d -b "$BUFFER_NAME" -t "$pane" || die "tmux paste-buffer failed"
  autoland_tmux send-keys -t "$pane" Enter || die "tmux send-keys Enter failed"
  sent_at=$(stamp)
  mark_status "$path" sent "$sent_at"
  bytes=$(wc -c < "$payload" | tr -dc 0-9)
  log_event "SEND $(basename "$path") pane=$pane bytes=${bytes:-0}"
  printf 'SENT %s\n' "$path"
}

cmd_drop() {
  local path status
  [ "$#" -eq 1 ] || die "drop needs exactly one draft path"
  path=$(resolve_draft "$1")
  [ -f "$path" ] || die "draft not found: $path"
  status=$(front_value "$path" status)
  [ "$status" = "draft" ] || die "draft status is $status, not draft"
  mark_status "$path" dropped
  log_event "DROP $(basename "$path")"
  printf 'DROPPED %s\n' "$path"
}

command=${1:-}
[ "$#" -gt 0 ] && shift || true
case "$command" in
  draft) cmd_draft "$@" ;;
  list) cmd_list "$@" ;;
  send) cmd_send "$@" ;;
  drop) cmd_drop "$@" ;;
  -h|--help) usage ;;
  *) usage; exit 2 ;;
esac
