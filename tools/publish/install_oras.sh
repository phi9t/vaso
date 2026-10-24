#!/usr/bin/env bash
# Install the pinned ORAS CLI used by tools/publish/artifact_store.py.
set -euo pipefail

ORAS_VERSION=1.3.4
ORAS_SHA256=f27adb935022d94df8dc77719c322dda592c78a0d57a6f7dcdd8d900b248c454

: "${VASO_ESTATE_ROOT:?set VASO_ESTATE_ROOT}"

case "$(uname -s):$(uname -m)" in
  Linux:x86_64) platform=linux_amd64 ;;
  *) echo "unsupported platform for pinned ORAS install: $(uname -s) $(uname -m)" >&2; exit 2 ;;
esac

base="$VASO_ESTATE_ROOT/opt-vaso"
bin_dir="$base/bin"
src_dir="$base/src"
archive="$src_dir/oras_${ORAS_VERSION}_${platform}.tar.gz"
url="https://github.com/oras-project/oras/releases/download/v${ORAS_VERSION}/oras_${ORAS_VERSION}_${platform}.tar.gz"

mkdir -p "$bin_dir" "$src_dir"
# Stage beside the cache so only verified bytes become a persistent archive.
work_dir="$(mktemp -d "$src_dir/.install-oras.XXXXXX")"
cleanup() {
  rm -rf "$work_dir"
}
trap cleanup EXIT

verify_archive() {
  printf '%s  %s\n' "$ORAS_SHA256" "$1" | sha256sum -c -
}

if [[ ! -f "$archive" ]] || ! verify_archive "$archive"; then
  curl -fsSL "$url" -o "$work_dir/archive.tar.gz"
  verify_archive "$work_dir/archive.tar.gz"
  mv -f -- "$work_dir/archive.tar.gz" "$archive"
fi
tar -xzf "$archive" -C "$work_dir" oras
install -m 0755 "$work_dir/oras" "$bin_dir/oras"
"$bin_dir/oras" version
