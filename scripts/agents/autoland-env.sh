# Shared settings for autoland.sh and autoland-status.sh (sourced, not run).
# Defaults describe the pytorch-frontier-convergence effort; override by env.
: "${VASO_ESTATE_ROOT:?set VASO_ESTATE_ROOT to the disk-backed estate root}"
LEAD=$(git -C "$(dirname "${BASH_SOURCE[0]}")" rev-parse --show-toplevel)
# The follower is the main checkout: the parent of the common git dir.
FOLLOWER=${FOLLOWER:-$(dirname "$(git -C "$LEAD" rev-parse --path-format=absolute --git-common-dir)")}
TARGET=${TARGET:-vaso/insula-spack-bazel-graph}
LANDING=${LANDING:-vaso/frontier-convergence}
LEAD_BRANCH=${LEAD_BRANCH:-$(git -C "$LEAD" rev-parse --abbrev-ref HEAD)}
TRACKER=${TRACKER:-.scratch/pytorch-frontier-convergence}
DECISION_SPECS=${DECISION_SPECS:-$TRACKER/spec.md:.scratch/native-pytorch-build/spec.md}
AGENT=${AGENT:-claude}
FOLLOWER_AGENT=${FOLLOWER_AGENT:-trae}
AGENT_ROOT=$VASO_ESTATE_ROOT/agents/$AGENT
FOLLOWER_LOGS=$VASO_ESTATE_ROOT/agents/$FOLLOWER_AGENT/logs
STALL_MIN=${STALL_MIN:-45}
BATCH_HOURS=${BATCH_HOURS:-4}
INTERVAL=${INTERVAL:-60}
BACKLOG_COMMITS=${BACKLOG_COMMITS:-3}
BACKLOG_MINUTES=${BACKLOG_MINUTES:-30}
BACKLOG_RATE_LIMIT_MIN=${BACKLOG_RATE_LIMIT_MIN:-30}
UNCOMMITTED_MIN=${UNCOMMITTED_MIN:-60}
UNCOMMITTED_RATE_LIMIT_MIN=${UNCOMMITTED_RATE_LIMIT_MIN:-30}
LOG=$AGENT_ROOT/autoland.log
STATE=$AGENT_ROOT/autoland.state
if [ -z "${FOLLOWER_PID:-}" ]; then
  # the interactive traecli whose working directory is the follower checkout
  for p in $(pgrep -x traecli); do
    [ "$(readlink "/proc/$p/cwd")" = "$FOLLOWER" ] && FOLLOWER_PID=$p && break
  done
fi
FOLLOWER_PID=${FOLLOWER_PID:-}

autoland_pid_is_or_ancestor() {
  local ancestor=$1 child=$2 stat rest parent
  case "$ancestor" in ""|*[!0-9]*) return 1 ;; esac
  case "$child" in ""|*[!0-9]*) return 1 ;; esac
  while [ "$child" != "0" ] && [ "$child" != "1" ]; do
    [ "$child" = "$ancestor" ] && return 0
    [ -r "/proc/$child/stat" ] || return 1
    stat=$(cat "/proc/$child/stat") || return 1
    rest=${stat##*) }
    set -- $rest
    parent=${2:-}
    [ -n "$parent" ] || return 1
    [ "$parent" = "$child" ] && return 1
    child=$parent
  done
  [ "$child" = "$ancestor" ]
}

autoland_tmux() {
  if [ -n "${VASO_TMUX_SOCKET:-}" ]; then
    tmux -S "$VASO_TMUX_SOCKET" "$@"
  else
    tmux "$@"
  fi
}

autoland_discover_follower_pane() {
  [ -n "${FOLLOWER_PID:-}" ] || return 0
  command -v tmux >/dev/null 2>&1 || return 0
  local pane pane_pid rest
  while IFS=' ' read -r pane pane_pid rest; do
    [ -n "$pane" ] || continue
    if autoland_pid_is_or_ancestor "$pane_pid" "$FOLLOWER_PID"; then
      printf '%s\n' "$pane"
      return 0
    fi
  done < <(autoland_tmux list-panes -a -F '#{pane_id} #{pane_pid}' 2>/dev/null || true)
}

if [ -z "${FOLLOWER_PANE+x}" ]; then
  FOLLOWER_PANE=$(autoland_discover_follower_pane)
fi
FOLLOWER_PANE=${FOLLOWER_PANE:-}

autoland_discover_follower_rollout() {
  [ -n "${FOLLOWER_PID:-}" ] || return 0
  local fd target name
  for fd in "/proc/$FOLLOWER_PID"/fd/*; do
    [ -e "$fd" ] || continue
    target=$(readlink "$fd" 2>/dev/null || true)
    [ -n "$target" ] || continue
    name=${target##*/}
    case "$name" in
      rollout-*.jsonl) printf '%s\n' "$target"; return 0 ;;
    esac
  done
}

if [ -z "${FOLLOWER_ROLLOUT+x}" ]; then
  FOLLOWER_ROLLOUT=$(autoland_discover_follower_rollout)
fi
FOLLOWER_ROLLOUT=${FOLLOWER_ROLLOUT:-}
