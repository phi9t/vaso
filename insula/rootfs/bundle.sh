#!/usr/bin/env bash
# Build and unpack content-addressed CUDA rootfs bundles.
#
#   rootfs/bundle.sh --line cu129 [--estate-root ROOT] [--out-dir DIR]
#   rootfs/bundle.sh unbundle --bundle rootfs-cu129-<sha256>.tar.zst --dest DIR
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

MODE=build
LINE=""
ESTATE_ROOT="${VASO_ESTATE_ROOT:-}"
OUT_DIR=""
LOCK="$REPO_ROOT/foundry/rootfs/cuda_ecosystem.lock.json"
BUNDLE=""
DEST=""
EXPECTED_DIGEST=""
OCI=0

usage() {
  cat >&2 <<'EOF'
usage:
  bundle.sh --line <cu129|cu130> [--estate-root ROOT] [--out-dir DIR] [--lock PATH] [--oci]
  bundle.sh unbundle --bundle PATH --dest DIR [--line <cu129|cu130>] [--digest SHA256]
EOF
}

if [[ "${1:-}" == "build" ]]; then
  MODE=build
  shift
elif [[ "${1:-}" == "unbundle" ]]; then
  MODE=unbundle
  shift
fi

while [[ "$#" -gt 0 ]]; do
  case "$1" in
    --line)
      LINE="${2:?missing value for --line}"
      shift 2
      ;;
    --estate-root)
      ESTATE_ROOT="${2:?missing value for --estate-root}"
      shift 2
      ;;
    --out-dir)
      OUT_DIR="${2:?missing value for --out-dir}"
      shift 2
      ;;
    --lock)
      LOCK="${2:?missing value for --lock}"
      shift 2
      ;;
    --bundle)
      BUNDLE="${2:?missing value for --bundle}"
      shift 2
      ;;
    --dest)
      DEST="${2:?missing value for --dest}"
      shift 2
      ;;
    --digest)
      EXPECTED_DIGEST="${2:?missing value for --digest}"
      shift 2
      ;;
    --oci)
      OCI=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "unknown argument: $1" >&2
      usage
      exit 2
      ;;
  esac
done

validate_line() {
  case "$1" in
    cu129|cu130) ;;
    *)
      echo "unknown line: ${1:-<empty>}" >&2
      return 2
      ;;
  esac
}

sha256_file() {
  sha256sum "$1" | awk '{print $1}'
}

bundle_epoch() {
  python3 - "$1" "$2" <<'PY'
import datetime as dt
import json
import sys
from pathlib import Path


def load(path: str) -> object:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def find_epoch(value: object) -> int | None:
    if isinstance(value, dict):
        for key, item in value.items():
            if str(key).lower() == "source_date_epoch":
                try:
                    return int(item)
                except (TypeError, ValueError):
                    pass
        for item in value.values():
            found = find_epoch(item)
            if found is not None:
                return found
    elif isinstance(value, list):
        for item in value:
            found = find_epoch(item)
            if found is not None:
                return found
    return None


def parse_time(value: object) -> int | None:
    if not isinstance(value, str) or not value:
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = dt.datetime.fromisoformat(text)
    except ValueError:
        try:
            parsed = dt.datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=dt.timezone.utc)
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return int(parsed.timestamp())


lock = load(sys.argv[1])
manifest = load(sys.argv[2])
epoch = find_epoch(lock)
if epoch is None and isinstance(manifest, dict):
    for key in ("SOURCE_DATE_EPOCH", "source_date_epoch"):
        if key in manifest:
            try:
                epoch = int(manifest[key])
                break
            except (TypeError, ValueError):
                pass
    if epoch is None:
        for key in ("build_time", "built_at", "created_utc", "created", "generated_utc", "generated"):
            epoch = parse_time(manifest.get(key))
            if epoch is not None:
                break
print(epoch if epoch is not None else 0)
PY
}

write_sidecar() {
  local out=$1 line=$2 digest=$3 archive=$4 size=$5 lock=$6 manifest=$7 epoch=$8
  python3 - "$out" "$line" "$digest" "$archive" "$size" "$lock" "$manifest" "$epoch" <<'PY'
import datetime as dt
import hashlib
import json
import os
import sys
from pathlib import Path

out, line, digest, archive, size, lock, manifest, epoch = sys.argv[1:]
rootfs_bundle = json.loads(Path(manifest).read_text(encoding="utf-8"))
doc = {
    "schema_version": 1,
    "line": line,
    "digest": digest,
    "sha256": digest,
    "size": int(size),
    "archive": Path(archive).name,
    "lock_sha256": hashlib.sha256(Path(lock).read_bytes()).hexdigest(),
    "rootfs_bundle": rootfs_bundle,
    "source_date_epoch": int(epoch),
    "created_utc": dt.datetime.fromtimestamp(int(epoch), dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
}
path = Path(out)
path.parent.mkdir(parents=True, exist_ok=True)
tmp = path.with_name(f"{path.name}.tmp.{os.getpid()}")
tmp.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n", encoding="utf-8")
tmp.replace(path)
PY
}

write_oci_image() {
  local line=$1 digest=$2 rootfs_dir=$3 out_dir=$4 epoch=$5
  local tmp_rootfs_tar image docker_tar
  if ! command -v docker >/dev/null 2>&1; then
    echo "rootfs bundle: docker not found; skipped --oci export" >&2
    return 0
  fi
  tmp_rootfs_tar="$out_dir/rootfs-$line-$digest.oci-rootfs.tar.tmp.$$"
  docker_tar="$out_dir/rootfs-$line-$digest.docker.tar"
  rm -f "$tmp_rootfs_tar" "$docker_tar"
  tar \
    --sort=name \
    --format=posix \
    --pax-option=exthdr.name=%d/PaxHeaders/%f,delete=atime,delete=ctime \
    --mtime="@$epoch" \
    --owner=0 \
    --group=0 \
    --numeric-owner \
    --xattrs \
    --xattrs-exclude='*' \
    --xattrs-include='security.capability' \
    -C "$rootfs_dir" \
    -cf "$tmp_rootfs_tar" \
    .
  image="vaso/rootfs:$line-$digest"
  docker import "$tmp_rootfs_tar" "$image" >/dev/null
  docker save "$image" -o "$docker_tar"
  rm -f "$tmp_rootfs_tar"
  echo "$docker_tar"
}

line_from_bundle_name() {
  local base
  base="$(basename "$1")"
  if [[ "$base" =~ ^rootfs-([A-Za-z0-9._-]+)-([0-9a-f]{64})\.tar\.zst$ ]]; then
    printf '%s\n' "${BASH_REMATCH[1]}"
    return 0
  fi
  return 1
}

digest_from_bundle_name() {
  local base
  base="$(basename "$1")"
  if [[ "$base" =~ ^rootfs-[A-Za-z0-9._-]+-([0-9a-f]{64})\.tar\.zst$ ]]; then
    printf '%s\n' "${BASH_REMATCH[1]}"
    return 0
  fi
  return 1
}

build_bundle() {
  [[ -n "$LINE" ]] || { echo "--line is required" >&2; usage; return 2; }
  validate_line "$LINE"
  [[ -n "$ESTATE_ROOT" ]] || { echo "--estate-root or VASO_ESTATE_ROOT is required" >&2; return 2; }
  local line_dir rootfs_dir rootfs_manifest epoch out_dir tmp_archive digest final_archive sidecar size
  line_dir="$(readlink -m "$ESTATE_ROOT/rootfs-lines/$LINE")"
  rootfs_dir="$line_dir/rootfs"
  rootfs_manifest="$line_dir/rootfs-bundle.json"
  [[ -d "$rootfs_dir" ]] || { echo "missing rootfs directory: $rootfs_dir" >&2; return 1; }
  [[ -f "$rootfs_manifest" ]] || { echo "missing rootfs manifest: $rootfs_manifest" >&2; return 1; }
  [[ -f "$LOCK" ]] || { echo "missing rootfs lock: $LOCK" >&2; return 1; }
  out_dir="${OUT_DIR:-$line_dir/bundles}"
  out_dir="$(readlink -m "$out_dir")"
  mkdir -p "$out_dir"
  epoch="$(bundle_epoch "$LOCK" "$rootfs_manifest")"
  tmp_archive="$out_dir/rootfs-$LINE.tar.zst.tmp.$$"
  rm -f "$tmp_archive"
  # zstd multithreaded output is identical for any worker count (checked on 1.5.7), so use every core.
  tar \
    --sort=name \
    --format=posix \
    --pax-option=exthdr.name=%d/PaxHeaders/%f,delete=atime,delete=ctime \
    --mtime="@$epoch" \
    --owner=0 \
    --group=0 \
    --numeric-owner \
    --xattrs \
    --xattrs-exclude='*' \
    --xattrs-include='security.capability' \
    -C "$line_dir" \
    -cf - \
    rootfs \
    rootfs-bundle.json | zstd -q -19 --threads=0 -o "$tmp_archive" -
  digest="$(sha256_file "$tmp_archive")"
  final_archive="$out_dir/rootfs-$LINE-$digest.tar.zst"
  mv -f "$tmp_archive" "$final_archive"
  size="$(wc -c <"$final_archive" | awk '{print $1}')"
  sidecar="$out_dir/rootfs-$LINE.json"
  write_sidecar "$sidecar" "$LINE" "$digest" "$final_archive" "$size" "$LOCK" "$rootfs_manifest" "$epoch"
  if [[ "$OCI" == "1" ]]; then
    write_oci_image "$LINE" "$digest" "$rootfs_dir" "$out_dir" "$epoch" >/dev/null
  fi
  echo "$final_archive"
}

unbundle() (
  [[ -n "$BUNDLE" ]] || { echo "--bundle is required" >&2; usage; return 2; }
  [[ -n "$DEST" ]] || { echo "--dest is required" >&2; usage; return 2; }
  [[ -f "$BUNDLE" ]] || { echo "missing bundle: $BUNDLE" >&2; return 1; }
  local line expected actual dest parent tmp
  line="$LINE"
  if [[ -z "$line" ]]; then
    line="$(line_from_bundle_name "$BUNDLE" || true)"
  fi
  [[ -n "$line" ]] || { echo "could not infer line from bundle name: $BUNDLE" >&2; return 2; }
  validate_line "$line"
  expected="$EXPECTED_DIGEST"
  if [[ -z "$expected" ]]; then
    expected="$(digest_from_bundle_name "$BUNDLE" || true)"
  fi
  if [[ ! "$expected" =~ ^[0-9a-f]{64}$ ]]; then
    echo "could not infer expected sha256 digest from bundle name; pass --digest" >&2
    return 2
  fi
  actual="$(sha256_file "$BUNDLE")"
  if [[ "$actual" != "$expected" ]]; then
    echo "digest mismatch for $BUNDLE: expected $expected actual $actual" >&2
    return 1
  fi
  dest="$(readlink -m "$DEST")"
  parent="$(dirname "$dest")"
  mkdir -p "$parent"
  # Stage next to the destination so the final rename stays on one filesystem.
  local TMPDIR="$parent"
  tmp="$(mktemp -d -p "$TMPDIR" ".rootfs-unbundle.$line.XXXXXX")"
  cleanup() {
    rm -rf -- "$tmp"
  }
  # Keep cleanup scoped to extraction, including an errexit from tar.
  trap cleanup EXIT
  tar --use-compress-program='zstd -d -q' -xf "$BUNDLE" -C "$tmp"
  [[ -d "$tmp/rootfs" ]] || { echo "bundle missing top-level rootfs/" >&2; return 1; }
  [[ -f "$tmp/rootfs-bundle.json" ]] || { echo "bundle missing top-level rootfs-bundle.json" >&2; return 1; }
  rm -rf "$dest"
  mv "$tmp" "$dest"
  trap - EXIT
  echo "$dest/rootfs"
)

case "$MODE" in
  build) build_bundle ;;
  unbundle) unbundle ;;
  *) echo "unknown mode: $MODE" >&2; exit 2 ;;
esac
