#!/usr/bin/env bash
# Shared vaso insula entry point.
#
# Source this file to get the `insula` function, or execute it directly:
#
#   scripts/insula/insula.sh --line cu129 -- bash -lc 'echo inside'
#
# `--print-argv` renders the bwrap argv as JSON and does not execute it.

if [[ -z "${BASH_VERSION:-}" ]]; then
  echo "insula.sh requires bash" >&2
  exit 2
fi

INSULA_SH_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSULA_REPO_ROOT="$(cd "$INSULA_SH_DIR/../.." && pwd)"
INSULA_LEASE_PY="$INSULA_REPO_ROOT/scripts/insula/lease.py"

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
  _insula_append_array out CUDA_DRIVER_BINDS
  _insula_append_array out INSULA_ENV_ARGS
  if [[ -n "${INSULA_CHDIR:-}" ]]; then
    out+=(--chdir "$INSULA_CHDIR")
  fi
  out+=(-- "${command_argv[@]}")
}

insula_build_command_argv() {
  local out_name="$1"
  local phase="$2"
  shift 2
  local -n out="$out_name"
  out=("$@")
  [[ "${#out[@]}" -gt 0 ]] || return 0
  [[ "$phase" != "fetch" ]] || return 0
  case "${out[0]}" in
    bazel|*/bazel) ;;
    *) return 0 ;;
  esac
  local arg
  for arg in "${out[@]}"; do
    [[ "$arg" == "--repository_disable_download" ]] && return 0
  done
  local -a with_flag=()
  local inserted=0
  for arg in "${out[@]}"; do
    if [[ "$inserted" == "0" && "$arg" == --repository_cache=* ]]; then
      with_flag+=(--repository_disable_download)
      inserted=1
    fi
    with_flag+=("$arg")
  done
  if [[ "$inserted" == "0" ]]; then
    with_flag+=(--repository_disable_download)
  fi
  out=("${with_flag[@]}")
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
  [[ -n "${VASO_CUDA_LINE:-}" ]] && args+=(--line "$VASO_CUDA_LINE")
  [[ -n "${INSULA_ROOTFS_LOCK:-}" ]] && args+=(--rootfs-lock "$INSULA_ROOTFS_LOCK")
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

insula_acquire_lease() {
  local out_name="$1"
  local resource="$2"
  local -n out="$out_name"
  local estate="${VASO_ESTATE_ROOT:-${ESTATE_ROOT:-}}"
  if [[ -z "$estate" ]]; then
    echo "insula fetch phase requires VASO_ESTATE_ROOT for the network-fetch lease" >&2
    return 2
  fi
  local lease_json acquired_id
  lease_json="$(VASO_ESTATE_ROOT="$estate" python3 "$INSULA_LEASE_PY" acquire \
    --resource "$resource" \
    --holder "${VASO_AGENT:-unknown}" \
    --pid "$$" \
    --timeout "${VASO_LEASE_TIMEOUT:-60}" \
    --ttl "${VASO_LEASE_TTL:-86400}")" || return $?
  acquired_id="$(insula_json_field id <<<"$lease_json")"
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

insula() {
  local print_argv=0
  local phase="${INSULA_PHASE:-run}"
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
  INSULA_PHASE="$phase"
  if [[ "$print_argv" != "1" ]]; then
    insula_preflight
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

insula_prepare_experiment_context() {
  local exp_dir="$1"
  local line="$2"
  local required_gib="${3:-${VASO_REQUIRED_GIB:-40}}"
  : "${line:?line is required}"

  local prefer_dir
  prefer_dir="$(df -P "$exp_dir" | awk 'NR==2{print $6}')"
  ESTATE_ROOT="${VASO_ESTATE_ROOT:-$(python3 "$exp_dir/tools/estate.py" --required-gib "$required_gib" --prefer "$prefer_dir" --materialize)}"
  export VASO_ESTATE_ROOT="$ESTATE_ROOT"
  VASO_CUDA_LINE="$line"
  export VASO_CUDA_LINE

  VASO_HOST="$ESTATE_ROOT/vaso"
  HOME_HOST="$ESTATE_ROOT/home/kvothe"
  SOURCES_HOST="$ESTATE_ROOT/sources"
  TOOLS_BIN_HOST="$VASO_HOST/tools/bin"
  OPT_VASO_HOST="$ESTATE_ROOT/opt-vaso"
  HOME_SB="/home/kvothe"
  EXP_DIR="$exp_dir"
  EXP_SB="/workspace/experiment"
  INSULA_CHDIR="$EXP_SB"

  . "$exp_dir/tools/insula_io.sh"
  init_insula_io "$VASO_HOST"

  local cuda_rootfs="$ESTATE_ROOT/rootfs-lines/$line/rootfs"
  local rootfs_manifest="$ESTATE_ROOT/rootfs-lines/$line/rootfs-bundle.json"
  ROOTFS_BUNDLE_MANIFEST_SB="/run/vaso/rootfs-bundle.json"
  VASO_CUDA_HOME_SB="/usr/local/cuda"
  INNER_PATH="/vaso/tools/bin:$VASO_CUDA_HOME_SB/bin:/usr/bin:/bin"
  INSULA_LIBRARY_PATH="/run/nvidia-driver/lib:$VASO_CUDA_HOME_SB/lib64"
  INSULA_ROOTFS_LOCK="${INSULA_ROOTFS_LOCK:-$exp_dir/rootfs/cuda_ecosystem.lock.json}"
  INSULA_REQUIRE_DRIVER="${INSULA_REQUIRE_DRIVER:-1}"
  INSULA_ESTATE_MIN_FREE_GIB="${INSULA_ESTATE_MIN_FREE_GIB:-100}"

  local -a canonical_dirs mp_args
  mapfile -t canonical_dirs < <(python3 "$exp_dir/tools/estate.py" --root "$ESTATE_ROOT" --print-sandbox-dirs)
  mp_args=()
  local d
  for d in "${canonical_dirs[@]}"; do mp_args+=(--mountpoint "$d"); done
  mp_args+=(--mountpoint "$EXP_SB" --mountpoint /run/nvidia-driver --mountpoint /run/nvidia-driver/lib)
  mapfile -t ROOT_PLAN < <(python3 "$exp_dir/tools/overlay_root.py" --base "$cuda_rootfs" "${mp_args[@]}")

  ROOTFS_MANIFEST_BIND=(--ro-bind "$rootfs_manifest" "$ROOTFS_BUNDLE_MANIFEST_SB")
  INSULA_DIR_ARGS=(--dir /run/vaso --dir /run/nvidia-driver --dir /run/nvidia-driver/lib)
  INSULA_BIND_ARGS=(
    --bind "$VASO_HOST" /vaso
    --ro-bind "$SOURCES_HOST" /vaso/sources
    --ro-bind "$TOOLS_BIN_HOST" /vaso/tools/bin
    --ro-bind "$OPT_VASO_HOST" /opt/vaso
    --bind "$ESTATE_ROOT/workspace" /workspace
    --bind "$HOME_HOST" "$HOME_SB"
    --bind "$EXP_DIR" "$EXP_SB"
  )
  mkdir -p \
    "$VASO_HOST/cache/bazel/repository-cache" \
    "$VASO_HOST/sources" \
    "$VASO_HOST/lines/$line/cache/bazel/output-base" \
    "$VASO_HOST/lines/$line/cache/bazel/disk-cache" \
    "$VASO_HOST/lines/$line/state/native" \
    "$VASO_HOST/lines/$line/state/stamps" \
    "$SOURCES_HOST" \
    "$ESTATE_ROOT/home/kvothe" \
    "$ESTATE_ROOT/agents/leases"
  CUDA_DRIVER_BINDS=()
  local soname lib real dev
  for soname in libcuda.so.1 libnvidia-ml.so.1; do
    lib=""
    for d in /usr/lib/x86_64-linux-gnu /usr/lib64 /lib/x86_64-linux-gnu; do
      if [[ -e "$d/$soname" ]]; then lib="$d/$soname"; break; fi
    done
    if [[ -n "$lib" ]]; then
      CUDA_DRIVER_BINDS+=(--ro-bind "$lib" "/run/nvidia-driver/lib/$soname")
      real="$(readlink -f "$lib")"
      [[ "$real" != "$lib" ]] && CUDA_DRIVER_BINDS+=(--ro-bind "$real" "/run/nvidia-driver/lib/$(basename "$real")")
    fi
  done
  for dev in /dev/nvidia*; do
    [[ -e "$dev" ]] && CUDA_DRIVER_BINDS+=(--dev-bind-try "$dev" "$dev")
  done
  [[ -d /proc/driver/nvidia ]] && CUDA_DRIVER_BINDS+=(--bind /proc/driver/nvidia /proc/driver/nvidia)
  [[ -d /sys ]] && CUDA_DRIVER_BINDS+=(--ro-bind-try /sys /sys)

  INSULA_ENV_ARGS=()
  _insula_append_setenv INSULA_ENV_ARGS PATH "$INNER_PATH"
  _insula_append_setenv INSULA_ENV_ARGS LD_LIBRARY_PATH "$INSULA_LIBRARY_PATH"
  _insula_append_setenv INSULA_ENV_ARGS HOME "$HOME_SB"
  _insula_append_setenv INSULA_ENV_ARGS TMPDIR "$INSULA_TMP_SB"
  _insula_append_setenv INSULA_ENV_ARGS USER kvothe
  _insula_append_setenv INSULA_ENV_ARGS LOGNAME kvothe
  _insula_append_setenv INSULA_ENV_ARGS TERM "${TERM:-xterm}"
  _insula_append_setenv INSULA_ENV_ARGS XDG_CACHE_HOME "$HOME_SB/.cache"
  _insula_append_setenv INSULA_ENV_ARGS XDG_CONFIG_HOME "$HOME_SB/.config"
  _insula_append_setenv INSULA_ENV_ARGS VASO_HOME /vaso
  _insula_append_setenv INSULA_ENV_ARGS VASO_CUDA_HOME "$VASO_CUDA_HOME_SB"
  _insula_append_setenv INSULA_ENV_ARGS VASO_CUDA_LINE "$VASO_CUDA_LINE"
  _insula_append_setenv INSULA_ENV_ARGS CUDA_VISIBLE_DEVICES "${CUDA_VISIBLE_DEVICES:-}"
  _insula_append_setenv INSULA_ENV_ARGS VASO_GPU_SET "${VASO_GPU_SET:-}"
  _insula_append_setenv INSULA_ENV_ARGS VASO_HOST_CPU_JOBS "${VASO_HOST_CPU_JOBS:-}"
  _insula_append_setenv INSULA_ENV_ARGS VASO_IN_INSULA 1
  _insula_append_setenv INSULA_ENV_ARGS VASO_ROOTFS_BUNDLE_MANIFEST "$ROOTFS_BUNDLE_MANIFEST_SB"
  _insula_append_setenv INSULA_ENV_ARGS VASO_WORKSPACE_ROOT /workspace
  _insula_append_setenv INSULA_ENV_ARGS VASO_PREFIX /opt/vaso
}

insula_main() {
  local line="${VASO_CUDA_LINE:-}"
  local print_argv=0
  local phase="${INSULA_PHASE:-run}"
  while [[ "$#" -gt 0 ]]; do
    case "$1" in
      --line)
        line="${2:?missing value for --line}"
        shift 2
        ;;
      --phase)
        phase="${2:?missing value for --phase}"
        shift 2
        ;;
      --print-argv)
        print_argv=1
        shift
        ;;
      --)
        shift
        break
        ;;
      -h|--help)
        sed -n '2,8p' "$0"
        return 0
        ;;
      *)
        echo "unknown argument: $1" >&2
        return 2
        ;;
    esac
  done
  if [[ -z "$line" ]]; then
    echo "usage: $0 --line <cu129|cu130> [--phase PHASE] [--print-argv] -- <cmd> [args...]" >&2
    return 2
  fi
  if [[ "$#" -eq 0 ]]; then
    echo "usage: $0 --line <cu129|cu130> [--phase PHASE] [--print-argv] -- <cmd> [args...]" >&2
    return 2
  fi
  insula_prepare_experiment_context "$INSULA_REPO_ROOT/experiments/spack-bazel-graph" "$line"
  local args=()
  [[ "$print_argv" == "1" ]] && args+=(--print-argv)
  args+=(--phase "$phase" -- "$@")
  insula "${args[@]}"
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  insula_main "$@"
fi
