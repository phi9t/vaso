#!/usr/bin/env bash
# Summarize follower commits since the last lead review mark.
set -u

SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
. "$SCRIPT_DIR/autoland-env.sh" || exit 1

REPO=$FOLLOWER
MARK_FILE=$AGENT_ROOT/review.mark
MARK=0
LIMIT=50

usage() {
  cat >&2 <<'EOF'
usage: review-batch.sh [--repo PATH] [--target REF] [--mark-file PATH] [--mark] [--limit N]

Print commits since the saved review mark with touched files and matching proof
verdict lines. --mark advances the review mark to the target tip after printing.
EOF
}

die() {
  echo "REFUSED: $*" >&2
  exit 2
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --repo) REPO=${2:?}; shift 2 ;;
    --target) TARGET=${2:?}; shift 2 ;;
    --mark-file) MARK_FILE=${2:?}; shift 2 ;;
    --mark) MARK=1; shift ;;
    --limit) LIMIT=${2:?}; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) usage; exit 2 ;;
  esac
done

git_out() {
  git -C "$REPO" "$@"
}

tip=$(git_out rev-parse "$TARGET") || die "cannot resolve target $TARGET"
if [ -f "$MARK_FILE" ]; then
  read -r base < "$MARK_FILE" || base=
else
  base=$(git_out rev-parse "$TARGET^" 2>/dev/null || printf '%s' "$tip")
fi
[ -n "$base" ] || base=$tip

if ! git_out cat-file -e "$base^{commit}" 2>/dev/null; then
  die "review mark $base is not a commit in $REPO"
fi

commits=$(git_out log --reverse --format=%H "$base..$TARGET")
count=$(printf '%s\n' "$commits" | sed '/^$/d' | wc -l | tr -dc 0-9)
short_base=$(git_out rev-parse --short=12 "$base")
short_tip=$(git_out rev-parse --short=12 "$tip")
printf 'Review batch %s..%s (%s commit(s))\n' "$short_base" "$short_tip" "${count:-0}"

line_count=1
print_line() {
  [ "$line_count" -lt "$LIMIT" ] || return 1
  printf '%s\n' "$1"
  line_count=$((line_count + 1))
}

proof_lines_for() {
  local subject=$1 slug pattern file found=0
  slug=$(printf '%s' "$subject" | tr '[:upper:]' '[:lower:]' | sed -E 's/^re-seat[[:space:]]+//; s/[^a-z0-9]+/-/g; s/^-+//; s/-+$//')
  for pattern in "$slug" "${slug#py-}" "$(printf '%s' "$subject" | tr '[:upper:]' '[:lower:]')"; do
    [ -n "$pattern" ] || continue
    for file in "$FOLLOWER_LOGS"/*"$pattern"*; do
      [ -f "$file" ] || continue
      print_line "  proof: $(basename "$file")" || return 0
      while IFS= read -r line; do
        case "$line" in
          *ok=true*|*ok=false*|*PASSED*|*FAILED*|*parity\ counts*|*parity*)
            print_line "    $line" || return 0
            found=1
            ;;
        esac
      done < "$file"
      [ "$found" = 1 ] && return 0
    done
  done
  print_line "  proof: no matching proof log in $FOLLOWER_LOGS" || true
}

for commit in $commits; do
  short=$(git_out rev-parse --short "$commit")
  subject=$(git_out log -1 --format=%s "$commit")
  print_line ""
  print_line "$short $subject" || break
  files=$(git_out diff-tree --no-commit-id --name-only -r "$commit" | sed 's/^/  file: /')
  while IFS= read -r file; do
    [ -n "$file" ] || continue
    print_line "$file" || break
  done <<EOF
$files
EOF
  proof_lines_for "$subject"
done

if [ "$MARK" = 1 ]; then
  mkdir -p "$(dirname "$MARK_FILE")"
  printf '%s\n' "$tip" > "$MARK_FILE"
  print_line "MARK $(git_out rev-parse --short "$tip") saved to $MARK_FILE" || true
fi
