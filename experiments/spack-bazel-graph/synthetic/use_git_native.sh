#!/usr/bin/env bash
set -euo pipefail

find_runfile() {
  local pattern="$1"
  local base hit
  for base in "${RUNFILES_DIR:-}" "$PWD" "$PWD/.." "$0.runfiles"; do
    [[ -n "$base" && -d "$base" ]] || continue
    hit="$(find "$base" -path "$pattern" -print -quit 2>/dev/null)"
    if [[ -n "$hit" ]]; then echo "$hit"; return 0; fi
  done
  return 1
}

PREFIX_PATH="$(find_runfile '*git_native*/prefix_path.txt')" || {
  echo "Git native prefix_path.txt was not staged in runfiles" >&2
  exit 1
}
PREFIX="$(tr -d '\n' < "$PREFIX_PATH")"
[[ -d "$PREFIX" ]] || {
  echo "Git native prefix is not a directory: $PREFIX" >&2
  exit 1
}

version="$("$PREFIX/bin/git" --version)"
case "$version" in
  "git version 2.53.0") ;;
  *)
    echo "unexpected git version: $version" >&2
    exit 1
    ;;
esac

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

"$PREFIX/bin/git" -C "$tmp" init -q
"$PREFIX/bin/git" -C "$tmp" config user.name "Vaso Native"
"$PREFIX/bin/git" -C "$tmp" config user.email "vaso-native@example.invalid"
printf 'native git\n' > "$tmp/message.txt"
"$PREFIX/bin/git" -C "$tmp" add message.txt
"$PREFIX/bin/git" -C "$tmp" commit -q -m "initial"
subject="$("$PREFIX/bin/git" -C "$tmp" log -1 --format=%s)"
[[ "$subject" == "initial" ]] || {
  echo "unexpected commit subject: $subject" >&2
  exit 1
}

test -x "$PREFIX/bin/git-subtree"
test -f "$PREFIX/share/bash-completion/completions/git"
test -f "$PREFIX/share/zsh/site-functions/_git"

echo "git:2.53.0:commit-ok"
