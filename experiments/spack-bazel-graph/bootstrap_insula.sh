#!/usr/bin/env bash
# Milestone 1: a working vaso insula bundle that can run Bazel and persist its
# output/cache across runs.
#
# This script is the codified, from-clean-slate bootstrap. It:
#   1. seats the host estate (placement discovery) on a suitable volume;
#   2. materializes the canonical host dir structure;
#   3. plans the base-root binds + canonical sandbox namespace;
#   4. projects host bazel as a thin shim into the estate;
#   5. runs a trivial Bazel build INSIDE the insula against the persisted
#      output base / disk cache / repository cache;
#   6. asserts the persisted caches are non-empty after the run.
#
# It is intentionally minimal (no Spack graph, no CUDA) so it can be run
# repeatedly from a clean slate to prove the bootstrap is deterministic. See
# run.sh for the full Spack->Bazel experiment layered on top.
#
# Usage:
#   bootstrap_insula.sh            # bootstrap + verify once
#   bootstrap_insula.sh --clean    # wipe the estate first (clean slate)
set -euo pipefail

EXP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$EXP_DIR/../.." && pwd)"
INSULA_SH="$REPO_ROOT/scripts/insula/insula.sh"
. "$INSULA_SH"
REQUIRED_GIB="${VASO_REQUIRED_GIB:-40}"

_is_estate_shim() { case "$1" in */.vaso-estate/*|*/vaso/tools/bin/*) return 0;; esac; return 1; }

resolve_tool() {  # $1=candidates...  -> prints real, non-shim, executable path
  for cand in "$@"; do
    local p r
    p="$(command -v "$cand" 2>/dev/null || true)"
    [[ -n "$p" ]] || continue
    r="$(readlink -f "$p" 2>/dev/null || echo "$p")"
    if [[ -x "$r" ]] && ! _is_estate_shim "$r"; then echo "$r"; return 0; fi
  done
  return 1
}

BAZEL_BIN="${BAZEL_BIN:-$(resolve_tool bazel bazelisk bazel-9.2.0 || true)}"
[[ -n "$BAZEL_BIN" && -x "$BAZEL_BIN" ]] || { echo "usable bazel not found; set BAZEL_BIN" >&2; exit 1; }

# --- clean slate (optional) --------------------------------------------------
if [[ "${1:-}" == "--clean" ]]; then
  EST="$(python3 tools/estate.py --required-gib 1 --prefer "$(df -P "$EXP_DIR" | awk 'NR==2{print $6}')" 2>/dev/null || true)"
  if [[ -n "$EST" && -d "$EST" ]]; then
    echo "clean slate: removing $EST"
    rm -rf "$EST"
  fi
fi

# --- 1+2. seat + materialize the estate --------------------------------------
PREFER_DIR="$(df -P "$EXP_DIR" | awk 'NR==2{print $6}')"
ESTATE_ROOT="$(python3 tools/estate.py --required-gib "$REQUIRED_GIB" --prefer "$PREFER_DIR" --materialize)"
echo "estate root: $ESTATE_ROOT"

VASO_HOST="$ESTATE_ROOT/vaso"
HOME_HOST="$ESTATE_ROOT/home/kvothe"
TOOLS_BIN_HOST="$VASO_HOST/tools/bin"
OPT_VASO_HOST="$ESTATE_ROOT/opt-vaso"
BAZEL_REAL_HOST="$OPT_VASO_HOST/bin/bazel-real"
BAZEL_REAL_SB="/opt/vaso/bin/bazel-real"
. "$EXP_DIR/tools/insula_io.sh"
init_insula_io "$VASO_HOST"
trap cleanup_insula_io EXIT
echo "insula tmp: $INSULA_TMP_SB (private tmpfs size: $INSULA_TMPFS_SIZE bytes)"

# --- 3. canonical namespace + base-root bind plan ----------------------------
mapfile -t CANONICAL_DIRS < <(python3 tools/estate.py --root "$ESTATE_ROOT" --print-sandbox-dirs)
MP_ARGS=(); for d in "${CANONICAL_DIRS[@]}"; do MP_ARGS+=(--mountpoint "$d"); done
mapfile -t ROOT_PLAN < <(python3 tools/overlay_root.py --base / "${MP_ARGS[@]}")

# --- 4. project host bazel as a thin shim (atomic; never self-referential) ---
case "$BAZEL_BIN" in "$TOOLS_BIN_HOST"/*) echo "refusing self-shim" >&2; exit 1;; esac
_write_shim() { local tmp="$1.tmp.$$"; printf '#!/usr/bin/env bash\nexec %q "$@"\n' "$2" > "$tmp"; chmod +x "$tmp"; mv -f "$tmp" "$1"; }
mkdir -p "$(dirname "$BAZEL_REAL_HOST")"
if [[ ! -x "$BAZEL_REAL_HOST" ]] || ! cmp -s "$BAZEL_BIN" "$BAZEL_REAL_HOST"; then
  cp "$BAZEL_BIN" "$BAZEL_REAL_HOST"
  chmod +x "$BAZEL_REAL_HOST"
fi
_write_shim "$TOOLS_BIN_HOST/bazel" "$BAZEL_REAL_SB"

BAZEL_OUTPUT_BASE_SB="/vaso/cache/bazel/output-base"
BAZEL_REPO_CACHE_SB="/vaso/cache/bazel/repository-cache"
BAZEL_DISK_CACHE_SB="/vaso/cache/bazel/disk-cache"
INSULA_DIR_ARGS=(--dir /run/vaso)
INSULA_BIND_ARGS=(
  --bind "$VASO_HOST" /vaso
  --ro-bind "$TOOLS_BIN_HOST" /vaso/tools/bin
  --ro-bind "$ESTATE_ROOT/opt-vaso" /opt/vaso
  --bind "$ESTATE_ROOT/workspace" /workspace
  --bind "$HOME_HOST" /home/kvothe
  --bind "$EXP_DIR" /workspace/experiment
)
INSULA_ENV_ARGS=(
  --setenv PATH "/vaso/tools/bin:/usr/bin:/bin"
  --setenv HOME /home/kvothe
  --setenv TMPDIR "$INSULA_TMP_SB"
  --setenv USER kvothe
  --setenv LOGNAME kvothe
  --setenv TERM "${TERM:-xterm}"
  --setenv XDG_CACHE_HOME /home/kvothe/.cache
  --setenv VASO_HOME /vaso
  --setenv VASO_WORKSPACE_ROOT /workspace
  --setenv VASO_IN_INSULA 1
  --setenv VASO_PREFIX /opt/vaso
)
INSULA_CHDIR=/workspace/experiment

# --- 5. run a trivial Bazel build inside the insula --------------------------
echo "== bazel build //bootstrap:touchstone (inside insula) =="
insula /vaso/tools/bin/bazel \
  --output_base="$BAZEL_OUTPUT_BASE_SB" \
  --host_jvm_args="-Djava.io.tmpdir=$INSULA_TMP_SB" \
  --batch \
  build //bootstrap:touchstone \
  --repository_cache="$BAZEL_REPO_CACHE_SB" \
  --disk_cache="$BAZEL_DISK_CACHE_SB" \
  --spawn_strategy=local --curses=no --color=no

# --- 6. assert caches persisted ----------------------------------------------
ob_bytes=$(du -sb "$VASO_HOST/cache/bazel/output-base" 2>/dev/null | awk '{print $1}')
echo "persisted output-base bytes: ${ob_bytes:-0}"
if [[ "${ob_bytes:-0}" -lt 100000 ]]; then
  echo "FAIL: bazel output base did not persist to the estate" >&2
  exit 1
fi
echo "MILESTONE 1 OK: insula ran bazel and persisted output/cache at $ESTATE_ROOT"
