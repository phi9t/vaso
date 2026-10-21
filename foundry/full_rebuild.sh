#!/usr/bin/env bash
# Build the complete Foundry matrix in a newly created, cache-empty estate.
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

usage() {
  cat <<'EOF'
usage: foundry/full_rebuild.sh [--seed-estate PATH] [--estate-parent PATH]

Create a fresh estate, reuse only a verified rootfs and pinned source inputs,
then build every supported CUDA line and profile. The new estate and all run
records are preserved on success or failure.

This is the cold-proof path: selected Bazel output bases, action caches, nested
build caches, and native state begin empty. For normal incremental work, rerun
foundry/build.sh against the same estate and select the needed line/profile.

Configuration:
  --seed-estate    $VASO_FULL_REBUILD_SEED_ESTATE (required if the flag is omitted)
  --estate-parent  $VASO_FULL_REBUILD_ESTATE_PARENT or the seed's parent
EOF
}

fail() {
  printf 'full rebuild: %s\n' "$*" >&2
  exit 2
}

seed_estate="${VASO_FULL_REBUILD_SEED_ESTATE:-}"
estate_parent="${VASO_FULL_REBUILD_ESTATE_PARENT:-}"

while (($#)); do
  case "$1" in
    --seed-estate)
      (($# >= 2)) || fail "--seed-estate requires a path"
      seed_estate=$2
      shift 2
      ;;
    --seed-estate=*)
      seed_estate=${1#*=}
      shift
      ;;
    --estate-parent)
      (($# >= 2)) || fail "--estate-parent requires a path"
      estate_parent=$2
      shift 2
      ;;
    --estate-parent=*)
      estate_parent=${1#*=}
      shift
      ;;
    -h | --help)
      usage
      exit 0
      ;;
    *)
      fail "unknown argument: $1"
      ;;
  esac
done

[[ -n "$seed_estate" ]] ||
  fail "pass --seed-estate or set VASO_FULL_REBUILD_SEED_ESTATE"
[[ -d "$seed_estate" ]] || fail "seed estate is not a directory: $seed_estate"
seed_estate="$(cd "$seed_estate" && pwd -P)"

rootfs_lines="$seed_estate/rootfs-lines"
[[ -d "$rootfs_lines" ]] || fail "seed rootfs lines are missing: $rootfs_lines"
rootfs_lines="$(readlink -f -- "$rootfs_lines")"
for line in cu129 cu130; do
  [[ -d "$rootfs_lines/$line/rootfs" ]] ||
    fail "seed rootfs is missing for $line: $rootfs_lines/$line/rootfs"
  [[ -f "$rootfs_lines/$line/rootfs-bundle.json" ]] ||
    fail "seed rootfs manifest is missing for $line: $rootfs_lines/$line/rootfs-bundle.json"
done

bcr_mirror="$seed_estate/sources/bcr"
[[ -d "$bcr_mirror" ]] || fail "seed BCR mirror is missing: $bcr_mirror"

if [[ -z "$estate_parent" ]]; then
  estate_parent="$(dirname "$seed_estate")"
fi
mkdir -p "$estate_parent"
estate_parent="$(cd "$estate_parent" && pwd -P)"

export TMPDIR="$estate_parent"
fresh_estate="$(mktemp -d -p "$TMPDIR" ".vaso-estate-full-rebuild-XXXXXXXX")"
printf 'Fresh estate: %s\n' "$fresh_estate"
printf 'Seed inputs:  %s\n' "$seed_estate"

mkdir -p "$fresh_estate/sources"
ln -s -- "$rootfs_lines" "$fresh_estate/rootfs-lines"
cp -a -- "$bcr_mirror" "$fresh_estate/sources/bcr"

exec "$DIR/build.sh" \
  --estate-root "$fresh_estate" \
  --seed-estate "$seed_estate" \
  --line all \
  --profile all
