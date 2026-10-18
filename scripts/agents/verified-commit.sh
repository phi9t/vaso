#!/usr/bin/env bash
# Commit explicit paths only after the standing verify passes on exactly the
# tree being committed. For the target owner (TRAE), whose commits ARE the
# target: a red commit there breaks everyone, so this is the only way to commit.
#
#   scripts/agents/verified-commit.sh -m "Imperative subject" [-m body] -- PATH...
#
# Refuses when tracked files outside PATH... are modified, because the verify
# would then test a tree that differs from the commit. Untracked files are
# ignored. Never amends; never stages anything but PATH...
set -uo pipefail
msgs=()
while [ $# -gt 0 ]; do
  case "$1" in
    -m) msgs+=(-m "${2:?}"); shift 2 ;;
    --) shift; break ;;
    *) echo "usage: verified-commit.sh -m MSG [-m MSG] -- PATH..." >&2; exit 2 ;;
  esac
done
[ ${#msgs[@]} -gt 0 ] && [ $# -gt 0 ] || { echo "usage: verified-commit.sh -m MSG [-m MSG] -- PATH..." >&2; exit 2; }
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
"$(dirname "$0")/standing-verify.sh" || { echo "REFUSED: standing verify failed; not committing" >&2; exit 4; }
git add -- "$@" && git commit -q "${msgs[@]}" -- "$@" && echo "COMMITTED $(git rev-parse --short HEAD)"
