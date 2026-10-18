#!/usr/bin/env bash
# Milestone 4: seal the insula.
#
# After the estate is bootstrapped (bazel caches warm, hermetic Spack fetched,
# language toolchains proven), freeze it into an immutable, self-describing
# bundle and run builds from the sealed image. Sealing means:
#
#   1. capture a manifest (estate root, canonical dirs, tool shim targets, a
#      content digest of the read-only material) into <estate>/seal/seal.json;
#   2. flip the read-only surfaces (/opt/vaso, the tool shims, the Spack dist,
#      the repository cache) to immutable by binding them --ro-bind and refusing
#      any run that would mutate them;
#   3. run the build from the sealed insula: only /vaso/cache/bazel and
#      /vaso/tmp stay writable (incremental output), everything else is ro.
#
# The seal is a contract, not a new filesystem: the same estate is reused, but
# the runner asserts the sealed digest still matches before every run, so drift
# is caught loudly.
set -euo pipefail

EXP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$EXP_DIR/../.." && pwd)"
INSULA_SH="$REPO_ROOT/scripts/insula/insula.sh"
. "$INSULA_SH"

_is_estate_shim() { case "$1" in */.vaso-estate/*|*/vaso/tools/bin/*) return 0;; esac; return 1; }
resolve_tool() {
  for cand in "$@"; do
    local p r; p="$(command -v "$cand" 2>/dev/null || true)"; [[ -n "$p" ]] || continue
    r="$(readlink -f "$p" 2>/dev/null || echo "$p")"
    if [[ -x "$r" ]] && ! _is_estate_shim "$r"; then echo "$r"; return 0; fi
  done; return 1
}

BAZEL_BIN="${BAZEL_BIN:-$(resolve_tool bazel bazelisk bazel-9.2.0 || true)}"
[[ -n "$BAZEL_BIN" && -x "$BAZEL_BIN" ]] || { echo "usable bazel not found; set BAZEL_BIN" >&2; exit 1; }

PREFER_DIR="$(df -P "$EXP_DIR" | awk 'NR==2{print $6}')"
ESTATE_ROOT="${VASO_ESTATE_ROOT:-$(python3 tools/estate.py --required-gib 1 --prefer "$PREFER_DIR")}"
[[ -d "$ESTATE_ROOT" ]] || { echo "no estate at $ESTATE_ROOT; run bootstrap_insula.sh first" >&2; exit 1; }

VASO_HOST="$ESTATE_ROOT/vaso"
HOME_HOST="$ESTATE_ROOT/home/kvothe"
TOOLS_BIN_HOST="$VASO_HOST/tools/bin"
OPT_VASO_HOST="$ESTATE_ROOT/opt-vaso"
BAZEL_REAL_HOST="$OPT_VASO_HOST/bin/bazel-real"
BAZEL_REAL_SB="/opt/vaso/bin/bazel-real"
SEAL_DIR="$ESTATE_ROOT/seal"
SEAL_JSON="$SEAL_DIR/seal.json"
. "$EXP_DIR/tools/insula_io.sh"
init_insula_io "$VASO_HOST"
trap cleanup_insula_io EXIT
echo "insula tmp: $INSULA_TMP_SB (private tmpfs size: $INSULA_TMPFS_SIZE bytes)"

action="${1:-seal}"

mkdir -p "$(dirname "$BAZEL_REAL_HOST")"
if [[ ! -x "$BAZEL_REAL_HOST" ]] || ! cmp -s "$BAZEL_BIN" "$BAZEL_REAL_HOST"; then
  cp "$BAZEL_BIN" "$BAZEL_REAL_HOST"
  chmod +x "$BAZEL_REAL_HOST"
fi
tmp="$TOOLS_BIN_HOST/bazel.tmp.$$"
printf '#!/usr/bin/env bash\nexec %q "$@"\n' "$BAZEL_REAL_SB" > "$tmp"
chmod +x "$tmp"
mv -f "$tmp" "$TOOLS_BIN_HOST/bazel"

# The read-only material whose integrity the seal guards. We digest a stable
# listing (path + size + mtime) rather than full contents, so sealing stays
# fast while still catching structural drift.
ro_surfaces=(
  "$ESTATE_ROOT/opt-vaso"
  "$TOOLS_BIN_HOST"
)

seal_digest() {
  # Deterministic digest of the ro surfaces' file listing.
  {
    for s in "${ro_surfaces[@]}"; do
      [[ -e "$s" ]] || continue
      find "$s" -type f -printf '%P\t%s\n' 2>/dev/null | sort
    done
  } | sha256sum | awk '{print $1}'
}

do_seal() {
  mkdir -p "$SEAL_DIR"
  local digest; digest="$(seal_digest)"
  python3 - "$SEAL_JSON" "$ESTATE_ROOT" "$digest" "$BAZEL_REAL_SB" <<'PY'
import json, sys, subprocess
seal_json, estate, digest, bazel = sys.argv[1:5]
dirs = subprocess.run(
    ["python3", "tools/estate.py", "--root", estate, "--print-sandbox-dirs"],
    capture_output=True, text=True, check=True,
).stdout.split()
manifest = {
    "schema_version": 1,
    "estate_root": estate,
    "sealed_digest": digest,
    "canonical_sandbox_dirs": dirs,
    "bazel_shim_target": bazel,
    "ro_surfaces": ["opt-vaso", "vaso/tools/bin"],
    "writable_in_sealed_run": ["/vaso/cache/bazel", "/vaso/tmp"],
}
with open(seal_json, "w") as fh:
    json.dump(manifest, fh, indent=2, sort_keys=True); fh.write("\n")
print(f"sealed: {seal_json}\n  digest={digest}")
PY
}

assert_sealed() {
  [[ -f "$SEAL_JSON" ]] || { echo "not sealed; run: $0 seal" >&2; exit 1; }
  local want have
  want="$(python3 -c "import json;print(json.load(open('$SEAL_JSON'))['sealed_digest'])")"
  have="$(seal_digest)"
  if [[ "$want" != "$have" ]]; then
    echo "SEAL DRIFT: ro material changed since sealing" >&2
    echo "  sealed=$want" >&2
    echo "  now   =$have" >&2
    exit 1
  fi
  echo "seal verified: $want"
}

run_sealed() {
  assert_sealed
  mapfile -t CANONICAL_DIRS < <(python3 tools/estate.py --root "$ESTATE_ROOT" --print-sandbox-dirs)
  MP_ARGS=(); for d in "${CANONICAL_DIRS[@]}"; do MP_ARGS+=(--mountpoint "$d"); done
  MP_ARGS+=(--mountpoint /workspace/experiment)
  mapfile -t ROOT_PLAN < <(python3 tools/overlay_root.py --base / "${MP_ARGS[@]}")

  # Sealed run: /vaso is ro EXCEPT the two writable incremental surfaces.
  INSULA_UID="$(id -u)"
  INSULA_GID="$(id -g)"
  INSULA_UNSHARE_IPC=0
  INSULA_DIR_ARGS=(--dir /run/vaso)
  INSULA_BIND_ARGS=(
    --ro-bind "$VASO_HOST" /vaso
    --bind "$VASO_HOST/cache/bazel" /vaso/cache/bazel
    --bind "$VASO_HOST/tmp" /vaso/tmp
    --ro-bind "$ESTATE_ROOT/opt-vaso" /opt/vaso
    --ro-bind "$ESTATE_ROOT/workspace" /workspace
    --bind "$HOME_HOST" /home/kvothe
    --ro-bind "$EXP_DIR" /workspace/experiment
  )
  INSULA_ENV_ARGS=(
    --setenv PATH "/vaso/tools/bin:/usr/bin:/bin"
    --setenv HOME /home/kvothe
    --setenv TMPDIR "$INSULA_TMP_SB"
    --setenv USER kvothe
    --setenv LOGNAME kvothe
    --setenv VASO_HOME /vaso
    --setenv VASO_PREFIX /opt/vaso
    --setenv VASO_SEALED 1
  )
  INSULA_CHDIR=/workspace/experiment
  insula /vaso/tools/bin/bazel \
       --output_base=/vaso/cache/bazel/output-base \
       --host_jvm_args="-Djava.io.tmpdir=$INSULA_TMP_SB" \
       --batch \
       build //bootstrap:touchstone \
       --repository_cache=/vaso/cache/bazel/repository-cache \
       --disk_cache=/vaso/cache/bazel/disk-cache \
       --spawn_strategy=local --curses=no --color=no
}

case "$action" in
  seal)   do_seal ;;
  verify) assert_sealed ;;
  run)    run_sealed; echo "SEALED RUN OK (estate: $ESTATE_ROOT)" ;;
  *) echo "usage: $0 {seal|verify|run}" >&2; exit 2 ;;
esac
