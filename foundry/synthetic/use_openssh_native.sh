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

PREFIX_PATH="$(find_runfile '*openssh_native*/prefix_path.txt')" || {
  echo "OpenSSH native prefix_path.txt was not staged in runfiles" >&2
  exit 1
}
PREFIX="$(tr -d '\n' < "$PREFIX_PATH")"
[[ -d "$PREFIX" ]] || {
  echo "OpenSSH native prefix is not a directory: $PREFIX" >&2
  exit 1
}

version="$("$PREFIX/bin/ssh" -V 2>&1)"
case "$version" in
  OpenSSH_10.3p1*) ;;
  *)
    echo "unexpected ssh version: $version" >&2
    exit 1
    ;;
esac

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
"$PREFIX/bin/ssh-keygen" -q -t ed25519 -N "" -f "$tmp/id_ed25519"
"$PREFIX/bin/ssh-keygen" -y -f "$tmp/id_ed25519" | grep -q '^ssh-ed25519 '

test -x "$PREFIX/sbin/sshd"
test -x "$PREFIX/libexec/sftp-server"
test -d "$PREFIX/var/empty"

echo "openssh:10.3p1:keygen-ok"
