#!/usr/bin/env bash
# Build release wheels twice with fresh Bazel output bases and compare contents.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
RUN_SH="${VASO_DETERMINISM_RUN_SH:-$REPO_ROOT/foundry/run.sh}"
COMPARE_PY="$SCRIPT_DIR/determinism_compare.py"
ALLOWLIST="$SCRIPT_DIR/determinism_allowlist.json"
. "$REPO_ROOT/foundry/tools/native_determinism_args.sh"

PROFILE=""
LINE=""
OUT=""
WORK_DIR=""
GIT_SHA_INPUT="${VASO_RELEASE_GIT_SHA:-HEAD}"

usage() {
  cat >&2 <<'EOF'
usage: determinism_check.sh --profile <torch|jax> --line <cu129|cu130> [--out PATH] [--work-dir PATH] [--allowlist PATH] [--git-sha SHA]
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --profile)
      PROFILE="${2:?missing --profile value}"
      shift 2
      ;;
    --line)
      LINE="${2:?missing --line value}"
      shift 2
      ;;
    --out)
      OUT="${2:?missing --out value}"
      shift 2
      ;;
    --work-dir)
      WORK_DIR="${2:?missing --work-dir value}"
      shift 2
      ;;
    --allowlist)
      ALLOWLIST="${2:?missing --allowlist value}"
      shift 2
      ;;
    --git-sha)
      GIT_SHA_INPUT="${2:?missing --git-sha value}"
      shift 2
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

case "$PROFILE" in
  torch|jax) ;;
  "")
    echo "--profile is required" >&2
    usage
    exit 2
    ;;
  *)
    echo "unknown profile: $PROFILE" >&2
    exit 2
    ;;
esac

case "$LINE" in
  cu129|cu130) ;;
  "")
    echo "--line is required" >&2
    usage
    exit 2
    ;;
  *)
    echo "unknown line: $LINE" >&2
    exit 2
    ;;
esac

: "${VASO_ESTATE_ROOT:?set VASO_ESTATE_ROOT}"

GIT_SHA="$(native_determinism_resolve_release_git_sha "$REPO_ROOT" "$GIT_SHA_INPUT")"
if [[ -z "$WORK_DIR" ]]; then
  ts="$(date -u +%Y%m%dT%H%M%SZ)"
  WORK_DIR="$VASO_ESTATE_ROOT/agents/${VASO_AGENT:-trae}/determinism/$PROFILE-$LINE-${GIT_SHA:0:12}-$ts-$$"
fi
WORK_DIR="$(readlink -m "$WORK_DIR")"
case "$WORK_DIR" in
  /tmp|/tmp/*|/var/tmp|/var/tmp/*)
    echo "refusing determinism work dir on tmpfs-like shared scratch: $WORK_DIR" >&2
    exit 2
    ;;
esac
mkdir -p "$WORK_DIR"

if [[ -z "$OUT" ]]; then
  OUT="$WORK_DIR/determinism.json"
fi
OUT="$(readlink -m "$OUT")"

FIRST_OB="$WORK_DIR/output-base-first"
SECOND_OB="$WORK_DIR/output-base-second"
FIRST_WHEELHOUSE="$WORK_DIR/first-wheelhouse"
SECOND_WHEELHOUSE="$WORK_DIR/second-wheelhouse"

case "$PROFILE" in
  torch)
    TARGETS=(
      "//native/pytorch:pytorch_release_wheel"
      "//native/torchvision:torchvision_release_wheel"
      "//native/torchaudio:torchaudio_release_wheel"
      "//native/triton:triton_release_wheel"
    )
    OUTPUT_DIRS=(
      "native/pytorch/pytorch_release_wheel_wheelhouse"
      "native/torchvision/torchvision_release_wheel_wheelhouse"
      "native/torchaudio/torchaudio_release_wheel_wheelhouse"
      "native/triton/triton_release_wheel_wheelhouse"
    )
    ;;
  jax)
    TARGETS=("//native/jaxlib:jaxlib_release_wheels")
    OUTPUT_DIRS=("native/jaxlib/jaxlib_release_wheels_wheelhouse")
    ;;
esac

BAZEL_FLAGS=(
  --remote_download_outputs=all
)
append_native_determinism_bazel_args \
  BAZEL_FLAGS \
  "$LINE" \
  "$REPO_ROOT" \
  "$GIT_SHA" \
  "$(native_determinism_fixed_output_base_mount)" \
  "/vaso/lines/$LINE/work"
if [[ -n "${VASO_NATIVE_PYTORCH_TOKEN:-}" ]]; then
  BAZEL_FLAGS+=("--//native/pytorch:token=$VASO_NATIVE_PYTORCH_TOKEN")
fi
if [[ -n "${VASO_NATIVE_TORCHVISION_TOKEN:-}" ]]; then
  BAZEL_FLAGS+=("--//native/torchvision:token=$VASO_NATIVE_TORCHVISION_TOKEN")
fi
if [[ -n "${VASO_NATIVE_TORCHAUDIO_TOKEN:-}" ]]; then
  BAZEL_FLAGS+=("--//native/torchaudio:token=$VASO_NATIVE_TORCHAUDIO_TOKEN")
fi
if [[ -n "${VASO_NATIVE_TRITON_TOKEN:-}" ]]; then
  BAZEL_FLAGS+=("--//native/triton:token=$VASO_NATIVE_TRITON_TOKEN")
fi
JAXLIB_TOKEN="${VASO_NATIVE_JAXLIB_TOKEN:-${VASO_NATIVE_LLVM_TOKEN:-}}"
if [[ -n "$JAXLIB_TOKEN" ]]; then
  BAZEL_FLAGS+=("--//native/jaxlib:token=$JAXLIB_TOKEN")
fi

run_build() {
  local label="$1"
  local output_base="$2"
  local wheelhouse="$3"
  local output_dir source_dir wheel

  rm -rf "$output_base" "$wheelhouse"
  mkdir -p "$output_base" "$wheelhouse"
  echo "determinism: build $label profile=$PROFILE line=$LINE output_base=$output_base"
  # run.sh resolves its helpers (tools/run_result.py, ...) relative to its own directory.
  (
    cd "$(dirname "$RUN_SH")"
    VASO_CUDA_LINE="$LINE" \
    VASO_BAZEL_OB="$output_base" \
    VASO_RELEASE_GIT_SHA="$GIT_SHA" \
    "$RUN_SH" --bazel-build "${TARGETS[@]}" "${BAZEL_FLAGS[@]}"
  )

  for output_dir in "${OUTPUT_DIRS[@]}"; do
    source_dir="$output_base/execroot/_main/bazel-out/k8-fastbuild/bin/$output_dir"
    if [[ ! -d "$source_dir" ]]; then
      echo "missing release wheelhouse after $label build: $source_dir" >&2
      exit 1
    fi
    copied=0
    shopt -s nullglob
    for wheel in "$source_dir"/*.whl; do
      cp "$wheel" "$wheelhouse/"
      copied=$((copied + 1))
    done
    shopt -u nullglob
    if (( copied == 0 )); then
      echo "no release wheels found after $label build in $source_dir" >&2
      exit 1
    fi
  done
  if ! compgen -G "$wheelhouse/*.whl" >/dev/null; then
    echo "no release wheels copied after $label build into $wheelhouse" >&2
    exit 1
  fi
}

run_build first "$FIRST_OB" "$FIRST_WHEELHOUSE"
run_build second "$SECOND_OB" "$SECOND_WHEELHOUSE"

/usr/bin/python3 "$COMPARE_PY" \
  --profile "$PROFILE" \
  --line "$LINE" \
  --first "$FIRST_WHEELHOUSE" \
  --second "$SECOND_WHEELHOUSE" \
  --first-output-base "$FIRST_OB" \
  --second-output-base "$SECOND_OB" \
  --allowlist "$ALLOWLIST" \
  --out "$OUT"

echo "determinism: report $OUT"
