#!/usr/bin/env bash
# Host entrypoint for the live triumvirate acceptance gate.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
EXP_DIR="$ROOT/foundry"
. "$EXP_DIR/tools/native_determinism_args.sh"

: "${VASO_ESTATE_ROOT:?set VASO_ESTATE_ROOT}"

line=""
profile=""
has_commit=0
commit_input=""
args=()
stage_root_host=""
stage_root_insula=""
acceptance_out_host=""
acceptance_out_stage_host=""
out_dir_host=""
out_dir_stage_host=""

ensure_stage_root() {
  [[ -n "$stage_root_host" ]] && return 0

  local agent="${VASO_AGENT:-trae}"
  if [[ ! "$agent" =~ ^[A-Za-z0-9_.-]+$ ]]; then
    echo "VASO_AGENT must contain only letters, numbers, dot, underscore or dash, got: $agent" >&2
    exit 2
  fi

  local tmpdir="$VASO_ESTATE_ROOT/agents/$agent/tmp"
  mkdir -p "$tmpdir"
  local TMPDIR="$tmpdir"
  stage_root_host="$(mktemp -d -p "$TMPDIR" triumvirate-gate.XXXXXX)"
  stage_root_insula="/vaso/agents/$agent/tmp/$(basename "$stage_root_host")"
}

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
      commit_input="$2"
      shift 2
      ;;
    --out-dir|--acceptance-out)
      [[ "$#" -ge 2 ]] || { echo "$1 requires a value" >&2; exit 2; }
      ensure_stage_root
      case "$1" in
        --acceptance-out)
          acceptance_out_host="$(readlink -m "$2")"
          acceptance_out_stage_host="$stage_root_host/acceptance.json"
          args+=("$1" "$stage_root_insula/acceptance.json")
          ;;
        --out-dir)
          out_dir_host="$(readlink -m "$2")"
          out_dir_stage_host="$stage_root_host/out-dir"
          args+=("$1" "$stage_root_insula/out-dir")
          ;;
      esac
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
  commit_input="HEAD"
fi
release_git_sha="$(native_determinism_resolve_release_git_sha "$ROOT" "$commit_input")"
release_source_date_epoch="$(native_determinism_source_date_epoch "$ROOT" "$release_git_sha")"
args+=("--commit" "$release_git_sha" "--source-date-epoch" "$release_source_date_epoch")

export VASO_CUDA_LINE="$line"
export VASO_PROFILE="$profile"
cd "$EXP_DIR"
set +e
./run.sh --insula-cmd env VASO_ESTATE_ROOT=/vaso bash scripts/agents/triumvirate-gate-insula.sh "${args[@]}"
run_rc=$?
set -e

copy_rc=0
if [[ -n "$acceptance_out_host" ]]; then
  if [[ -f "$acceptance_out_stage_host" ]]; then
    mkdir -p "$(dirname "$acceptance_out_host")"
    cp "$acceptance_out_stage_host" "$acceptance_out_host" || copy_rc=$?
  elif [[ "$run_rc" == "0" ]]; then
    echo "acceptance output was not produced inside the insula: $stage_root_insula/acceptance.json" >&2
    copy_rc=1
  fi
fi
if [[ -n "$out_dir_host" ]]; then
  if [[ -d "$out_dir_stage_host" ]]; then
    mkdir -p "$out_dir_host"
    cp -a "$out_dir_stage_host"/. "$out_dir_host"/ || copy_rc=$?
  elif [[ "$run_rc" == "0" ]]; then
    echo "output directory was not produced inside the insula: $stage_root_insula/out-dir" >&2
    copy_rc=1
  fi
fi
if [[ "$copy_rc" == "0" && -n "$stage_root_host" ]]; then
  rm -rf "$stage_root_host"
fi
if [[ "$run_rc" == "0" && "$copy_rc" != "0" ]]; then
  exit "$copy_rc"
fi
exit "$run_rc"
