#!/usr/bin/env bash
# Commit explicit paths only after the standing verify passes on exactly the
# tree being committed. For the target owner (TRAE), whose commits ARE the
# target: a red commit there breaks everyone, so this is the only way to commit.
#
#   tools/agents/verified-commit.sh -m "Imperative subject" [-m body] \
#     [--allow-native-rebuild-other "justification"] -- PATH...
#
# Refuses when tracked files outside PATH... are modified, because the verify
# would then test a tree that differs from the commit. Untracked files are
# ignored. Never amends; never stages anything but PATH...
set -uo pipefail
msgs=()
msg_values=()
allow_native_rebuild_other=""
while [ $# -gt 0 ]; do
  case "$1" in
    -m) msg_values+=("${2:?}"); msgs+=(-m "${2:?}"); shift 2 ;;
    --allow-native-rebuild-other)
      allow_native_rebuild_other="${2:?}"
      shift 2
      ;;
    --) shift; break ;;
    *) echo "usage: verified-commit.sh -m MSG [-m MSG] [--allow-native-rebuild-other JUSTIFICATION] -- PATH..." >&2; exit 2 ;;
  esac
done
[ ${#msgs[@]} -gt 0 ] && [ $# -gt 0 ] || { echo "usage: verified-commit.sh -m MSG [-m MSG] [--allow-native-rebuild-other JUSTIFICATION] -- PATH..." >&2; exit 2; }
: "${VASO_ESTATE_ROOT:?set VASO_ESTATE_ROOT}"
ROOT=$(git rev-parse --show-toplevel)
cd "$ROOT"
outside=()
while IFS= read -r line; do
  path=${line:3}
  keep=0
  for p in "$@"; do
    p=${p%/}
    case "$path" in "$p"|"$p"/*) keep=1 ;; esac
  done
  [ "$keep" = 1 ] || outside+=("$path")
done < <(git status --porcelain --untracked-files=no)
if [ ${#outside[@]} -gt 0 ]; then
  echo "REFUSED: tracked files outside the commit are modified; the verify would not test the committed tree:" >&2
  printf '  %s\n' "${outside[@]}" >&2
  echo "Commit or restore them first, or include them in PATH... if they belong to this change." >&2
  exit 3
fi
if git diff --quiet HEAD -- "$@" && [ -z "$(git ls-files --others --exclude-standard -- "$@")" ]; then
  echo "REFUSED: nothing to commit under the given paths" >&2; exit 3
fi

has_native_rebuild_reason=0
native_rebuild_reason_re='Native[[:space:]]+rebuild:[^;]+;[[:space:]]+reason:[[:space:]]+(source|toolchain|flags):[[:space:]]+.+'
for ((idx=1; idx<${#msg_values[@]}; idx++)); do
  if [[ "${msg_values[$idx]}" =~ $native_rebuild_reason_re ]]; then
    has_native_rebuild_reason=1
  fi
done

native_rebuild_check() {
  local tool="$ROOT/foundry/tools/rebuild_impact.py"
  [ -f "$tool" ] || return 0

  local agent="${VASO_AGENT:-verified-commit}"
  local tmp_root="${TMPDIR:-}"
  if [ -z "$tmp_root" ]; then
    tmp_root="$VASO_ESTATE_ROOT/agents/$agent/tmp"
  fi
  mkdir -p "$tmp_root"
  local index="$tmp_root/verified-commit-index.$$"
  local impact_json="$tmp_root/native-rebuild-impact.$$.json"
  local real_index
  real_index="$(git rev-parse --git-path index)"
  if [ -f "$real_index" ]; then
    cp "$real_index" "$index"
  else
    : > "$index"
  fi
  trap 'rm -f "$index" "$impact_json"' RETURN

  if ! GIT_INDEX_FILE="$index" git add -- "$@"; then
    echo "REFUSED: could not stage PATH... in the native rebuild check index" >&2
    return 5
  fi
  GIT_INDEX_FILE="$index" python3 "$tool" --repo-root "$ROOT" --fast --base HEAD --staged --format json >"$impact_json"
  local rc=$?
  case "$rc" in
    0) return 0 ;;
    10)
      if [ "$has_native_rebuild_reason" = 1 ]; then
        return 0
      fi
      echo "REFUSED: native rebuild impact detected; add a commit body line like:" >&2
      echo "  Native rebuild: <pkg list>; reason: <source|toolchain|flags>: <what changed>" >&2
      GIT_INDEX_FILE="$index" python3 "$tool" --repo-root "$ROOT" --fast --base HEAD --staged --format text >&2
      return 6
      ;;
    20)
      if [ -z "$allow_native_rebuild_other" ]; then
        echo "REFUSED: native rebuild impact includes doctrine-violating 'other' causes." >&2
        echo "Use --allow-native-rebuild-other \"<justification>\" only with lead approval." >&2
        GIT_INDEX_FILE="$index" python3 "$tool" --repo-root "$ROOT" --fast --base HEAD --staged --format text >&2
        return 7
      fi
      record_native_rebuild_other_override "$impact_json" "$allow_native_rebuild_other" "$@"
      return 0
      ;;
    *)
      echo "REFUSED: native rebuild impact check failed rc=$rc" >&2
      return 5
      ;;
  esac
}

record_native_rebuild_other_override() {
  local impact_json="$1"
  local justification="$2"
  shift 2
  if [ -z "${VASO_ESTATE_ROOT:-}" ]; then
    echo "REFUSED: --allow-native-rebuild-other requires VASO_ESTATE_ROOT so the override can be recorded" >&2
    return 7
  fi
  local agent="${VASO_AGENT:-verified-commit}"
  local ledger="$VASO_ESTATE_ROOT/agents/$agent/native-rebuild-overrides.jsonl"
  mkdir -p "$(dirname "$ledger")"
  python3 - "$impact_json" "$ledger" "$justification" "$@" <<'PY'
import datetime as dt
import json
import os
import sys

impact_path, ledger_path, justification, *paths = sys.argv[1:]
with open(impact_path, encoding="utf-8") as handle:
    impact = json.load(handle)
record = {
    "type": "native-rebuild-other-override",
    "written_utc": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    "agent": os.environ.get("VASO_AGENT") or os.environ.get("USER") or "unknown",
    "head": os.popen("git rev-parse HEAD").read().strip(),
    "justification": justification,
    "paths": list(paths),
    "impact": impact,
}
with open(ledger_path, "a", encoding="utf-8") as handle:
    handle.write(json.dumps(record, sort_keys=True) + "\n")
PY
}

native_rebuild_check "$@" || exit $?
"$(dirname "$0")/standing-verify.sh" || { echo "REFUSED: standing verify failed; not committing" >&2; exit 4; }
git add -- "$@" && git commit -q "${msgs[@]}" -- "$@" && echo "COMMITTED $(git rev-parse --short HEAD)"
