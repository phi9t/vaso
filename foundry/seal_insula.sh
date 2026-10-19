#!/usr/bin/env bash
# Record and check the prepared rootfs tool surfaces, then prove an offline
# build with read-only estate inputs and writable owned caches and temporary
# state. Provisioning precedes sealing; execution never repairs tool drift.
set -euo pipefail

EXP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$EXP_DIR/.." && pwd)"
cd "$EXP_DIR"
INSULA_SH="$REPO_ROOT/foundry/insula.sh"
. "$INSULA_SH"

: "${VASO_ESTATE_ROOT:?set VASO_ESTATE_ROOT}"
: "${VASO_CUDA_LINE:?set VASO_CUDA_LINE to cu129 or cu130}"
case "$VASO_CUDA_LINE" in cu129|cu130) ;; *) echo "unknown CUDA line: $VASO_CUDA_LINE" >&2; exit 2;; esac
ESTATE_ROOT="$VASO_ESTATE_ROOT"
[[ -d "$ESTATE_ROOT" ]] || { echo "no estate at $ESTATE_ROOT; run bootstrap_insula.sh first" >&2; exit 1; }

VASO_HOST="$ESTATE_ROOT/vaso"
HOME_HOST="$ESTATE_ROOT/home/kvothe"
CUDA_ROOTFS="$ESTATE_ROOT/rootfs-lines/$VASO_CUDA_LINE/rootfs"
OPT_VASO_HOST="$CUDA_ROOTFS/opt/vaso"
TOOLS_BIN_HOST="$OPT_VASO_HOST/compat-bin"
BAZEL_REAL_SB="/opt/vaso/bin/bazel-real"
SEAL_DIR="$ESTATE_ROOT/seal"
SEAL_JSON="$SEAL_DIR/seal.json"
. "$EXP_DIR/tools/insula_io.sh"
init_insula_io "$VASO_HOST"
trap cleanup_insula_io EXIT
echo "insula tmp: $INSULA_TMP_SB (private tmpfs size: $INSULA_TMPFS_SIZE bytes)"

action="${1:-seal}"

python3 "$REPO_ROOT/insula/rootfs/foundry_tools.py" --rootfs "$CUDA_ROOTFS" --verify >/dev/null

# Tool inputs covered by the seal. Their contents are hashed, so equal-sized
# modifications cannot pass as an unchanged seal.
ro_surfaces=(
  "$OPT_VASO_HOST"
)

seal_digest() {
  # Deterministic digest of the read-only tool contents.
  {
    for s in "${ro_surfaces[@]}"; do
      [[ -e "$s" ]] || continue
      (cd "$s" && find . -type f -print0 | sort -z | xargs -0 -r sha256sum)
    done
  } | sha256sum | awk '{print $1}'
}

do_seal() {
  mkdir -p "$SEAL_DIR"
  local digest; digest="$(seal_digest)"
  python3 - "$SEAL_JSON" "$ESTATE_ROOT" "$digest" "$BAZEL_REAL_SB" "$REPO_ROOT" <<'PY'
import json, sys, subprocess
seal_json, estate, digest, bazel, repo_root = sys.argv[1:6]
dirs = subprocess.run(
    ["python3", repo_root + "/insula/estate/estate.py", "--root", estate, "--print-sandbox-dirs"],
    capture_output=True, text=True, check=True,
).stdout.split()
manifest = {
    "schema_version": 1,
    "estate_root": estate,
    "sealed_digest": digest,
    "canonical_sandbox_dirs": dirs,
    "bazel_shim_target": bazel,
    "ro_surfaces": ["rootfs-lines/" + __import__("os").environ["VASO_CUDA_LINE"] + "/rootfs/opt/vaso"],
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
  mkdir -p "$ESTATE_ROOT/workspace/"{repo,experiment,insula,foundry}
  mapfile -t CANONICAL_DIRS < <(python3 "$REPO_ROOT/insula/estate/estate.py" --root "$ESTATE_ROOT" --print-sandbox-dirs)
  MP_ARGS=(); for d in "${CANONICAL_DIRS[@]}"; do MP_ARGS+=(--mountpoint "$d"); done
  MP_ARGS+=(--mountpoint /workspace/experiment)
  mapfile -t ROOT_PLAN < <(python3 "$REPO_ROOT/insula/estate/overlay_root.py" --base "$CUDA_ROOTFS" "${MP_ARGS[@]}")

  # Sealed run: /vaso is ro EXCEPT the two writable incremental surfaces.
  ROOTFS_MANIFEST_BIND=(--ro-bind "$ESTATE_ROOT/rootfs-lines/$VASO_CUDA_LINE/rootfs-bundle.json" /run/vaso/rootfs-bundle.json)
  INSULA_UNSHARE_NET=1
  INSULA_UID="$(id -u)"
  INSULA_GID="$(id -g)"
  INSULA_UNSHARE_IPC=0
  INSULA_DIR_ARGS=(--dir /run/vaso)
  INSULA_BIND_ARGS=(
    --ro-bind "$VASO_HOST" /vaso
    --ro-bind "$TOOLS_BIN_HOST" /vaso/tools/bin
    --ro-bind "$ESTATE_ROOT/sources" /vaso/sources
    --bind "$VASO_HOST/cache/bazel" /vaso/cache/bazel
    --bind "$VASO_HOST/tmp" /vaso/tmp
    --ro-bind "$OPT_VASO_HOST" /opt/vaso
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
       --repository_disable_download --lockfile_mode=off \
       --registry=file:///vaso/sources/bcr/registry/current \
       --distdir=/opt/vaso/distdir --symlink_prefix=/vaso/tmp/sealed-links/ \
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
