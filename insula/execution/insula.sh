#!/usr/bin/env bash
# Shared vaso insula entry point.
#
# Source this file to get the `insula` function, or execute it directly:
#
#   insula/run.sh --line cu129 -- bash -lc 'echo inside'
#
# `--print-argv` renders the bwrap argv as JSON and does not execute it.

if [[ -z "${BASH_VERSION:-}" ]]; then
  echo "insula.sh requires bash" >&2
  exit 2
fi

INSULA_SH_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSULA_REPO_ROOT="$(cd "$INSULA_SH_DIR/../.." && pwd)"
INSULA_LEASE_PY="$INSULA_SH_DIR/lease.py"

_insula_array_exists() {
  local name="$1"
  declare -p "$name" >/dev/null 2>&1
}

_insula_append_array() {
  local target="$1"
  local source="$2"
  _insula_array_exists "$source" || return 0
  eval "$target"'+=("${'"$source"'[@]}")'
}

_insula_append_setenv() {
  local target="$1"
  local key="$2"
  local value="$3"
  eval "$target"'+=(--setenv "$key" "$value")'
}

INSULA_NVIDIA_DRIVER_LIBRARY_PATTERNS=(
  libcuda.so.1
  libnvidia-ml.so.1
  libnvidia-ptxjitcompiler.so.1
  libnvidia-nvvm.so.4
  "libnvidia-gpucomp.so.*"
)
INSULA_NVIDIA_DRIVER_LIBRARY_DIRS=(
  /usr/lib/x86_64-linux-gnu
  /usr/lib64
  /lib/x86_64-linux-gnu
)

insula_append_cuda_driver_binds() {
  local out_name="$1"
  local -n out="$out_name"
  local -a driver_dirs=("${INSULA_NVIDIA_DRIVER_LIBRARY_DIRS[@]}")
  if _insula_array_exists INSULA_DRIVER_LIB_DIRS; then
    driver_dirs=("${INSULA_DRIVER_LIB_DIRS[@]}")
  fi

  local pattern lib real dir
  for pattern in "${INSULA_NVIDIA_DRIVER_LIBRARY_PATTERNS[@]}"; do
    lib=""
    for dir in "${driver_dirs[@]}"; do
      for candidate in "$dir"/$pattern; do
        [[ -e "$candidate" ]] || continue
        lib="$candidate"
        break 2
      done
    done
    if [[ -n "$lib" ]]; then
      out+=(--ro-bind "$lib" "/run/nvidia-driver/lib/$(basename "$lib")")
      real="$(readlink -f "$lib")"
      if [[ "$real" != "$lib" ]]; then
        out+=(--ro-bind "$real" "/run/nvidia-driver/lib/$(basename "$real")")
      fi
    fi
  done
}

insula_build_argv() {
  local out_name="$1"
  local phase="$2"
  shift 2
  local -n out="$out_name"
  local -a command_argv
  insula_build_command_argv command_argv "$phase" "$@"
  out=("${INSULA_BWRAP_BIN:-bwrap}")
  out+=(
    --die-with-parent
    --unshare-user
  )
  if [[ "$phase" != fetch && "${INSULA_UNSHARE_NET:-0}" == 1 ]]; then
    out+=(--unshare-net)
  fi
  if [[ -n "${INSULA_UID:-}" || -n "${INSULA_GID:-}" ]]; then
    : "${INSULA_UID:?INSULA_UID is required when INSULA_GID is set}"
    : "${INSULA_GID:?INSULA_GID is required when INSULA_UID is set}"
    out+=(--uid "$INSULA_UID" --gid "$INSULA_GID")
  fi
  if [[ "${INSULA_UNSHARE_IPC:-1}" != "0" ]]; then
    out+=(--unshare-ipc)
  fi
  if [[ "${INSULA_UNSHARE_PID:-0}" == "1" ]]; then
    out+=(--unshare-pid)
  fi
  if [[ "${INSULA_UNSHARE_UTS:-1}" != "0" ]]; then
    out+=(--unshare-uts)
  fi
  if [[ "${INSULA_UNSHARE_CGROUP_TRY:-0}" == "1" ]]; then
    out+=(--unshare-cgroup-try)
  fi
  if [[ "${INSULA_AS_PID_1:-0}" == "1" ]]; then
    out+=(--as-pid-1)
  fi
  if [[ "${INSULA_CLEAR_ENV:-1}" != "0" ]]; then
    out+=(--clearenv)
  fi
  out+=(--proc /proc)
  if _insula_array_exists INSULA_DEV_MODE; then
    _insula_append_array out INSULA_DEV_MODE
  else
    out+=(--dev /dev)
  fi
  if [[ -n "${INSULA_TMPFS_SIZE:-}" ]]; then
    out+=(--size "$INSULA_TMPFS_SIZE")
  fi
  out+=(--tmpfs /tmp --tmpfs /run)
  _insula_append_array out ROOT_PLAN
  if _insula_array_exists INSULA_DIR_ARGS; then
    _insula_append_array out INSULA_DIR_ARGS
  else
    out+=(--dir /run/vaso)
  fi
  _insula_append_array out ROOTFS_MANIFEST_BIND
  _insula_append_array out INSULA_BIND_ARGS
  _insula_append_array out INSULA_SOURCE_BIND_ARGS
  _insula_append_array out CUDA_DRIVER_BINDS
  _insula_append_array out INSULA_ENV_ARGS
  if [[ -n "${INSULA_CHDIR:-}" ]]; then
    out+=(--chdir "$INSULA_CHDIR")
  fi
  out+=(-- "${command_argv[@]}")
}

insula_build_command_argv() {
  local -n command_out="$1"
  shift 2
  command_out=("$@")
}

insula_print_argv_json() {
  python3 - "$@" <<'PY'
import json
import sys

print(json.dumps(sys.argv[1:], ensure_ascii=True))
PY
}

insula_preflight() {
  [[ "${INSULA_SKIP_PREFLIGHT:-0}" == "1" ]] && return 0
  local doctor="${INSULA_DOCTOR:-$INSULA_SH_DIR/doctor.sh}"
  if [[ ! -x "$doctor" ]]; then
    echo "insula preflight: doctor is not executable: $doctor" >&2
    return 2
  fi
  local -a args=(preflight)
  if [[ -n "${ESTATE_ROOT:-}" ]]; then
    args+=(--estate-root "$ESTATE_ROOT")
  elif [[ -n "${VASO_ESTATE_ROOT:-}" ]]; then
    args+=(--estate-root "$VASO_ESTATE_ROOT")
  fi
  local preflight_line="${INSULA_PREFLIGHT_LINE-${VASO_CUDA_LINE:-}}"
  [[ -n "$preflight_line" ]] && args+=(--line "$preflight_line")
  [[ -n "${INSULA_ROOTFS_LOCK:-}" ]] && args+=(--rootfs-lock "$INSULA_ROOTFS_LOCK")
  [[ -n "${INSULA_PREFLIGHT_LAYOUT:-}" ]] && args+=(--layout "$INSULA_PREFLIGHT_LAYOUT")
  [[ -n "${INSULA_ESTATE_MIN_FREE_GIB:-}" ]] && args+=(--min-estate-free-gib "$INSULA_ESTATE_MIN_FREE_GIB")
  [[ -n "${INSULA_DOCKER_ROOT:-}" ]] && args+=(--docker-root "$INSULA_DOCKER_ROOT")
  [[ -n "${INSULA_DOCKER_MIN_FREE_GIB:-}" ]] && args+=(--min-docker-free-gib "$INSULA_DOCKER_MIN_FREE_GIB")
  if [[ "${INSULA_REQUIRE_DRIVER:-0}" == "1" ]]; then
    args+=(--require-driver)
  else
    args+=(--no-require-driver)
  fi
  if _insula_array_exists INSULA_DRIVER_LIB_DIRS; then
    local dir
    for dir in "${INSULA_DRIVER_LIB_DIRS[@]}"; do
      args+=(--driver-lib-dir "$dir")
    done
  fi
  if _insula_array_exists INSULA_DEVICE_GLOBS; then
    local pattern
    for pattern in "${INSULA_DEVICE_GLOBS[@]}"; do
      args+=(--device-glob "$pattern")
    done
  fi
  "$doctor" "${args[@]}"
}

insula_require_fetch_pin() {
  if [[ "${INSULA_FETCH_SHA256_PINNED:-0}" == "1" ]]; then
    return 0
  fi
  if [[ "${INSULA_FETCH_SHA256:-}" =~ ^[0-9a-f]{64}$ ]]; then
    return 0
  fi
  echo "insula fetch phase requires sha256-pinned source proof (set INSULA_FETCH_SHA256_PINNED=1 or INSULA_FETCH_SHA256=<64 hex>)" >&2
  return 2
}

insula_json_field() {
  local field="$1"
  python3 -c 'import json,sys; print(json.load(sys.stdin).get(sys.argv[1], ""))' "$field"
}

insula_json_nested_lease() {
  python3 -c '
from __future__ import annotations

import json
import sys

doc = json.load(sys.stdin)
lease = doc.get("lease")
if not isinstance(lease, dict):
    raise SystemExit(1)
print(json.dumps(lease, sort_keys=True))
'
}

insula_held_lease_id() {
  local resource="$1"
  local leases="${VASO_LEASES_HELD:-}"
  local entry held_resource held_id
  [[ -n "$leases" ]] || return 1
  local IFS=,
  for entry in $leases; do
    [[ "$entry" == *:* ]] || continue
    held_resource="${entry%:*}"
    held_id="${entry##*:}"
    if [[ "$held_resource" == "$resource" && -n "$held_id" ]]; then
      printf '%s\n' "$held_id"
      return 0
    fi
  done
  return 1
}

insula_acquire_lease() {
  local out_name="$1"
  local resource="$2"
  shift 2
  local -n out="$out_name"
  local estate="${VASO_ESTATE_ROOT:-${ESTATE_ROOT:-}}"
  if [[ -z "$estate" ]]; then
    echo "insula requires VASO_ESTATE_ROOT for leases" >&2
    return 2
  fi
  if [[ "$resource" == "gpu" ]]; then
    local inherited_id check_json inherited_amount requested_amount=1
    local -a lease_args=("$@")
    local idx
    for ((idx=0; idx<${#lease_args[@]}; idx++)); do
      if [[ "${lease_args[$idx]}" == "--amount" && "$((idx + 1))" -lt "${#lease_args[@]}" ]]; then
        requested_amount="${lease_args[$((idx + 1))]}"
      fi
    done
    if inherited_id="$(insula_held_lease_id "$resource")"; then
      if ! check_json="$(VASO_ESTATE_ROOT="$estate" python3 "$INSULA_LEASE_PY" check --id "$inherited_id" --resource "$resource")"; then
        echo "inherited lease is not live: resource=$resource id=$inherited_id" >&2
        return 75
      fi
      INSULA_LAST_LEASE_JSON="$(insula_json_nested_lease <<<"$check_json")" || return 75
      inherited_amount="$(insula_json_field amount <<<"$INSULA_LAST_LEASE_JSON")"
      if [[ "$requested_amount" =~ ^[0-9]+$ && "$inherited_amount" =~ ^[0-9]+$ ]] && (( inherited_amount < requested_amount )); then
        echo "inherited GPU lease is too small: requested=$requested_amount inherited=$inherited_amount id=$inherited_id" >&2
        return 75
      fi
      out=""
      return 0
    fi
  fi
  local lease_json acquired_id
  lease_json="$(VASO_ESTATE_ROOT="$estate" python3 "$INSULA_LEASE_PY" acquire \
    --resource "$resource" \
    --holder "${VASO_AGENT:-unknown}" \
    --pid "$$" \
    --timeout "${VASO_LEASE_TIMEOUT:-60}" \
    --ttl "${VASO_LEASE_TTL:-86400}" \
    "$@")" || return $?
  acquired_id="$(insula_json_field id <<<"$lease_json")"
  INSULA_LAST_LEASE_JSON="$lease_json"
  VASO_LEASES_HELD="${VASO_LEASES_HELD:+$VASO_LEASES_HELD,}$resource:$acquired_id"
  export VASO_LEASES_HELD
  out="$acquired_id"
}

insula_release_lease() {
  local lease_id="$1"
  local estate="${VASO_ESTATE_ROOT:-${ESTATE_ROOT:-}}"
  [[ -n "$lease_id" && -n "$estate" ]] || return 0
  VASO_ESTATE_ROOT="$estate" python3 "$INSULA_LEASE_PY" release --id "$lease_id" >/dev/null 2>&1 || true
}

insula_with_network_fetch_lease() {
  local lease_id rc errexit=0
  case "$-" in *e*) errexit=1;; esac
  insula_acquire_lease lease_id network-fetch || return $?
  set +e
  "$@"
  rc=$?
  insula_release_lease "$lease_id"
  [[ "$errexit" == "1" ]] && set -e
  return "$rc"
}

insula_network_fetch() {
  local sha256=""
  while [[ "$#" -gt 0 ]]; do
    case "$1" in
      --sha256)
        sha256="${2:?missing value for --sha256}"
        shift 2
        ;;
      --)
        shift
        break
        ;;
      *)
        echo "unknown insula_network_fetch argument: $1" >&2
        return 2
        ;;
    esac
  done
  if [[ ! "$sha256" =~ ^[0-9a-f]{64}$ ]]; then
    echo "network fetch requires a valid sha256 pin, got: ${sha256:-<empty>}" >&2
    return 2
  fi
  [[ "$#" -gt 0 ]] || { echo "network fetch requires a command" >&2; return 2; }
  INSULA_FETCH_SHA256="$sha256" insula_with_network_fetch_lease "$@"
}

insula_rootfs_from_bundle() {
  local out_name="$1"
  local bundle="${2:?bundle path is required}"
  local dest_parent="${3:-${VASO_ESTATE_ROOT:-${ESTATE_ROOT:-}}/rootfs-bundles}"
  local -n out="$out_name"
  local base line digest dest extracted_rootfs

  base="$(basename "$bundle")"
  if [[ ! "$base" =~ ^rootfs-([A-Za-z0-9._-]+)-([0-9a-f]{64})\.tar\.zst$ ]]; then
    echo "insula_rootfs_from_bundle: bundle name must be rootfs-<line>-<sha256>.tar.zst: $bundle" >&2
    return 2
  fi
  line="${BASH_REMATCH[1]}"
  digest="${BASH_REMATCH[2]}"
  case "$line" in
    cu129|cu130) ;;
    *)
      echo "insula_rootfs_from_bundle: unknown line in bundle name: $line" >&2
      return 2
      ;;
  esac
  if [[ -z "$dest_parent" || "$dest_parent" == "/rootfs-bundles" ]]; then
    echo "insula_rootfs_from_bundle: VASO_ESTATE_ROOT or destination parent is required" >&2
    return 2
  fi
  dest="$(readlink -m "$dest_parent/$line")"
  extracted_rootfs="$("$INSULA_REPO_ROOT/insula/rootfs/bundle.sh" \
    unbundle \
    --bundle "$bundle" \
    --dest "$dest" \
    --line "$line" \
    --digest "$digest")" || return $?
  # Publish the caller's output only after verification and extraction succeed.
  out="$extracted_rootfs"
}

insula() {
  local print_argv=0
  local phase="${INSULA_PHASE:-run}"
  local release_dir=""
  local release_bundle="${INSULA_RELEASE_BUNDLE:-}"
  local release_gpus="${INSULA_RELEASE_GPUS:-1}"
  while [[ "$#" -gt 0 ]]; do
    case "$1" in
      --print-argv)
        print_argv=1
        shift
        ;;
      --phase)
        phase="${2:?missing value for --phase}"
        shift 2
        ;;
      --line)
        VASO_CUDA_LINE="${2:?missing value for --line}"
        export VASO_CUDA_LINE
        shift 2
        ;;
      --release)
        release_dir="${2:?missing value for --release}"
        shift 2
        ;;
      --gpus)
        release_gpus="${2:?missing value for --gpus}"
        shift 2
        ;;
      --bundle)
        release_bundle="${2:?missing value for --bundle}"
        shift 2
        ;;
      --)
        shift
        break
        ;;
      *)
        break
        ;;
    esac
  done
  if [[ "$#" -eq 0 ]]; then
    echo "insula: missing command" >&2
    return 2
  fi
  if [[ -n "$release_dir" ]]; then
    if ! declare -F insula_run_release >/dev/null; then
      echo "release execution requires a caller-provided release context" >&2
      return 2
    fi
    if [[ -z "${VASO_CUDA_LINE:-}" ]]; then
      echo "insula --release requires --line or VASO_CUDA_LINE" >&2
      return 2
    fi
    insula_run_release "$VASO_CUDA_LINE" "$release_dir" "$release_gpus" "$print_argv" "$release_bundle" "$@"
    return $?
  fi
  if [[ -n "$release_bundle" ]]; then
    echo "insula --bundle requires --release" >&2
    return 2
  fi
  INSULA_PHASE="$phase"
  if [[ "$print_argv" != "1" ]]; then
    insula_preflight >&2 || return $?
  fi
  local -a argv
  insula_build_argv argv "$phase" "$@"
  if [[ "$print_argv" == "1" ]]; then
    insula_print_argv_json "${argv[@]}"
    return 0
  fi
  if [[ "$phase" == "fetch" ]]; then
    insula_require_fetch_pin || return $?
    insula_with_network_fetch_lease "${argv[@]}"
    return $?
  fi
  "${argv[@]}"
}

insula_prepare_context() {
  local rootfs="$1" workspace="$2" line="$3"
  : "${VASO_ESTATE_ROOT:?set VASO_ESTATE_ROOT}"
  python3 - "$INSULA_REPO_ROOT/insula/rootfs" "$rootfs" <<'PY_SHELL' || {
import os
from pathlib import Path
import sys

sys.path.insert(0, sys.argv[1])
from verify_cuda_ecosystem import resolve_rootfs_path

try:
    rootfs = Path(sys.argv[2])
    shell = resolve_rootfs_path(rootfs, "/bin/sh")
    usable = rootfs.is_dir() and shell.is_file() and os.access(shell, os.X_OK)
except (OSError, RuntimeError):
    usable = False
sys.exit(0 if usable else 1)
PY_SHELL
    echo "insula rootfs is missing or has no executable /bin/sh: $rootfs" >&2
    return 2
  }
  [[ -n "$workspace" && -d "$workspace" && -r "$workspace" && -x "$workspace" ]] || {
    echo "insula --workspace requires a readable, searchable directory: ${workspace:-<empty>}; select --workspace DIR" >&2
    return 2
  }
  workspace="$(readlink -f "$workspace")" || return $?
  ESTATE_ROOT="$(python3 "$INSULA_REPO_ROOT/insula/estate/estate.py" \
    --root "$VASO_ESTATE_ROOT" --required-gib "${VASO_REQUIRED_GIB:-0}" --materialize)" || return $?
  export VASO_ESTATE_ROOT="$ESTATE_ROOT"
  mkdir -p "$ESTATE_ROOT/sources" "$ESTATE_ROOT/agents/leases" || return $?
  local -a mountpoints=()
  local path directories
  directories="$(python3 "$INSULA_REPO_ROOT/insula/estate/estate.py" \
    --root "$ESTATE_ROOT" --print-sandbox-dirs)" || return $?
  while IFS= read -r path; do mountpoints+=(--mountpoint "$path"); done <<< "$directories"
  mountpoints+=(--mountpoint /workspace/vaso --mountpoint /run/vaso)
  local plan
  plan="$(python3 "$INSULA_REPO_ROOT/insula/estate/overlay_root.py" \
    --base "$rootfs" "${mountpoints[@]}")" || return $?
  mapfile -t ROOT_PLAN <<<"$plan"
  INSULA_BIND_ARGS=(
    --bind "$ESTATE_ROOT/vaso" /vaso
    --bind "$ESTATE_ROOT/home/kvothe" /home/kvothe
    --ro-bind "$workspace" /workspace/vaso
  )
  if [[ -d "$rootfs/opt/vaso" ]]; then
    INSULA_BIND_ARGS+=(--ro-bind "$rootfs/opt/vaso" /opt/vaso)
  fi
  ROOTFS_MANIFEST_BIND=()
  if [[ -f "$rootfs/../rootfs-bundle.json" ]]; then
    ROOTFS_MANIFEST_BIND=(--ro-bind "$(readlink -f "$rootfs/../rootfs-bundle.json")" /run/vaso/rootfs-bundle.json)
  fi
  INSULA_ENV_ARGS=(
    --setenv PATH /opt/vaso/compat-bin:/opt/vaso/bin:/usr/local/cuda/bin:/usr/local/bin:/usr/bin:/bin
    --setenv HOME /home/kvothe
    --setenv TMPDIR /tmp
    --setenv VASO_IN_INSULA 1
    --setenv VASO_ESTATE_ROOT /vaso
  )
  INSULA_CHDIR=/workspace/vaso
  INSULA_PREFLIGHT_LINE="$line"
  INSULA_REQUIRE_DRIVER=0
  INSULA_ESTATE_MIN_FREE_GIB="${VASO_REQUIRED_GIB:-0}"
}

insula_main() {
  local line="${VASO_CUDA_LINE:-}" rootfs="" workspace="$PWD" print_argv=0 phase=run
  [[ "${1:-}" != run ]] || shift
  while [[ "$#" -gt 0 ]]; do
    case "$1" in
      --line) line="${2:?missing line}"; shift 2 ;;
      --rootfs) rootfs="${2:?missing rootfs}"; shift 2 ;;
      --workspace)
        [[ "$#" -ge 2 ]] || { echo "insula --workspace requires a readable, searchable directory; select --workspace DIR" >&2; return 2; }
        workspace="$2"; shift 2 ;;
      --print-argv) print_argv=1; shift ;;
      --phase) phase="${2:?missing phase}"; shift 2 ;;
      --) shift; break ;;
      -h|--help)
        echo "usage: $0 [run] (--line cu129|cu130 | --rootfs DIR) [--workspace DIR] [--print-argv] [--phase run|fetch] -- COMMAND [ARG...]"
        return 0 ;;
      *) echo "unknown argument: $1" >&2; return 2 ;;
    esac
  done
  : "${VASO_ESTATE_ROOT:?set VASO_ESTATE_ROOT}"
  [[ "$#" -gt 0 ]] || { echo "insula: missing command" >&2; return 2; }
  case "$line" in ""|cu129|cu130) ;; *) echo "unknown CUDA line: $line" >&2; return 2 ;; esac
  case "$phase" in run|fetch) ;; *) echo "unknown phase: $phase" >&2; return 2 ;; esac
  INSULA_UNSHARE_NET=1
  [[ "$phase" != fetch ]] || INSULA_UNSHARE_NET=0
  if [[ -z "$rootfs" ]]; then
    [[ -n "$line" ]] || { echo "select --line or --rootfs" >&2; return 2; }
    rootfs="$VASO_ESTATE_ROOT/rootfs-lines/$line/rootfs"
  else
    # An explicitly supplied rootfs need not occupy a line-specific estate path.
    line=""
  fi
  insula_prepare_context "$(readlink -m "$rootfs")" "$workspace" "$line" || return $?
  local -a options=(--phase "$phase")
  [[ "$print_argv" != 1 ]] || options+=(--print-argv)
  insula "${options[@]}" -- "$@"
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  set -euo pipefail
  insula_main "$@"
fi
