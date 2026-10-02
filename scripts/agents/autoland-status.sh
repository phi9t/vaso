#!/usr/bin/env bash
# One-screen, read-only view of the autoland loop and the follower.
#   scripts/agents/autoland-status.sh                 once
#   watch -n 30 scripts/agents/autoland-status.sh     live
# Settings as for autoland.sh (VASO_ESTATE_ROOT required).
set -u
. "$(dirname "$0")/autoland-env.sh" || exit 1
REPO=$LEAD
now=$(date +%s)
ago() { local s=$(( now - $1 )); if [ $s -ge 3600 ]; then echo "$((s/3600))h$(( (s%3600)/60 ))m"; else echo "$((s/60))m$((s%60))s"; fi; }
g() { git -C "$REPO" "$@"; }
backlog_line() {
  local count oldest_ts oldest_min active
  count=$(g rev-list --count "$TARGET..$LANDING" 2>/dev/null || echo 0)
  oldest_ts=$(g log --format=%ct --reverse "$TARGET..$LANDING" 2>/dev/null | head -1)
  if [ -n "$oldest_ts" ]; then
    oldest_min=$(( (now - oldest_ts) / 60 ))
    [ "$oldest_min" -lt 0 ] && oldest_min=0
  else
    oldest_min=0
  fi
  active=0
  [ "$count" -ge "$BACKLOG_COMMITS" ] && active=1
  [ "$oldest_min" -ge "$BACKLOG_MINUTES" ] && active=1
  [ "$active" = 1 ] && echo "backlog:   $count commits, oldest $oldest_min min"
}
uncommitted_line() {
  local count newest oldest target_ts newest_min oldest_min severity entry path mtime
  count=0
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
    mtime=$(stat -c %Y "$FOLLOWER/$path" 2>/dev/null || true)
    [ -n "$mtime" ] || continue
    if [ "$count" -eq 0 ]; then
      newest=$mtime
      oldest=$mtime
    else
      [ "$mtime" -gt "$newest" ] && newest=$mtime
      [ "$mtime" -lt "$oldest" ] && oldest=$mtime
    fi
    count=$((count + 1))
  done < <(git -C "$FOLLOWER" status --porcelain -z --untracked-files=no 2>/dev/null || true)
  [ "$count" -gt 0 ] || return 0
  target_ts=$(g log -1 --format=%ct "$TARGET" 2>/dev/null || echo 0)
  [ "${target_ts:-0}" -ge "${newest:-0}" ] && return 0
  newest_min=$(( (now - newest) / 60 ))
  [ "$newest_min" -lt 0 ] && newest_min=0
  [ "$newest_min" -ge "$UNCOMMITTED_MIN" ] || return 0
  oldest_min=$(( (now - oldest) / 60 ))
  [ "$oldest_min" -lt 0 ] && oldest_min=0
  severity=YELLOW
  [ "$oldest_min" -ge $(( UNCOMMITTED_MIN * 3 )) ] && severity=RED
  echo "uncommitted:   $count files, oldest $oldest_min min ($severity)"
}

echo "== autoland  $(date -u +%FT%TZ)"
if [ -f "$STATE" ]; then
  eval "$(tr ' ' '\n' < "$STATE" | sed 's/^/st_/')"
  if kill -0 "$st_pid" 2>/dev/null; then alive="RUNNING pid $st_pid"; else alive="NOT RUNNING (last pid $st_pid)"; fi
  echo "loop:      $alive, heartbeat $(ago "$st_beat") ago, up $(ago "$st_start")"
  idle=$(( now - st_last_activity ))
  echo "stall:     follower idle $(ago "$st_last_activity") of ${st_stall_min}m allowed"
  echo "review:    batch review in $(( (st_start + st_batch_hours*3600 - now) / 60 ))m"
else
  echo "loop:      no heartbeat yet ($STATE)"
fi

if [ -n "$FOLLOWER_PID" ] && ps -p "$FOLLOWER_PID" >/dev/null; then
  kids=$(pstree -p "$FOLLOWER_PID" 2>/dev/null | grep -oE '(bazel[^(]*|insula[^(]*|run\.sh|spack|python3?|pytest|git)\(' | tr -d '(' | sort | uniq -c | awk '{printf "%s×%s ", $2, $1}')
  echo "follower:  alive (pid $FOLLOWER_PID) ${kids:+busy: $kids}"
else
  echo "follower:  not running"
fi

t=$(g rev-parse --short "$TARGET"); l=$(g rev-parse --short "$LANDING")
if g merge-base --is-ancestor "$LANDING" "$TARGET"; then pend="landed"; else pend="$(g rev-list --count "$TARGET..$LANDING") lead commit(s) waiting for the follower to land"; fi
echo "target:    $t   landing: $l ($pend)"
backlog_line
uncommitted_line
echo
echo "-- recent target commits"
g log --format='   %h %<(8)%cr %s' -6 "$TARGET"
echo
echo "-- latest follower logs"
ls -t "$FOLLOWER_LOGS" 2>/dev/null | head -3 | while read -r f; do
  m=$(stat -c %Y "$FOLLOWER_LOGS/$f"); echo "   $(ago "$m") ago  $f"; done
echo
echo "-- loop events"
grep -E ' (START|MOVED|LANDED|RED|REWRITTEN|STALL|GONE|IO|REVIEW|BACKLOG|UNCOMMITTED) ' "$LOG" 2>/dev/null | tail -6 | sed 's/^/   /'
echo
echo "-- disk: $(df --output=target,pcent /tmp /var/tmp "$VASO_ESTATE_ROOT" | tail -n +2 | awk '{printf "%s %s  ", $1, $2}')"
