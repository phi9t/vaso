#!/usr/bin/env bash
# Host entrypoint for the live triumvirate acceptance gate.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
EXP_DIR="$ROOT/experiments/spack-bazel-graph"

line=""
profile=""
has_commit=0
args=()
while [[ "$#" -gt 0 ]]; do
  case "$1" in
    --profile)
      [[ "$#" -ge 2 ]] || { echo "--profile requires a value" >&2; exit 2; }
      profile="$2"
      args+=("$1" "$2")
      shift 2
      ;;
    --line)
      [[ "$#" -ge 2 ]] || { echo "--line requires a value" >&2; exit 2; }
      line="$2"
      args+=("$1" "$2")
      shift 2
      ;;
    --commit)
      [[ "$#" -ge 2 ]] || { echo "--commit requires a value" >&2; exit 2; }
      has_commit=1
      args+=("$1" "$2")
      shift 2
      ;;
    *)
      args+=("$1")
      shift
      ;;
  esac
done

case "$line" in
  cu129|cu130) ;;
  "") echo "--line is required" >&2; exit 2 ;;
  *) echo "unknown --line=$line; expected cu129 or cu130" >&2; exit 2 ;;
esac
case "$profile" in
  torch|jax) ;;
  "") echo "--profile is required" >&2; exit 2 ;;
  *) echo "unknown --profile=$profile; expected torch or jax" >&2; exit 2 ;;
esac

if [[ "$has_commit" == "0" ]]; then
  args+=("--commit" "$(git -C "$ROOT" rev-parse HEAD)")
fi

export VASO_CUDA_LINE="$line"
export VASO_PROFILE="$profile"
cd "$EXP_DIR"
exec ./run.sh --insula-cmd bash scripts/agents/triumvirate-gate-insula.sh "${args[@]}"
