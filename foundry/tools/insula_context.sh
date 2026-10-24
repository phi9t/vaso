#!/usr/bin/env bash
# Foundry-specific contexts for the canonical Foundry entrypoint.
# Generic execution is implemented in insula/execution/insula.sh.
. "$INSULA_REPO_ROOT/foundry/tools/native_determinism_args.sh"

# Keep relative compatibility links valid beside /workspace/experiment.
INSULA_SOURCE_BIND_ARGS=(
  --ro-bind "$INSULA_REPO_ROOT/insula" /workspace/insula
  --ro-bind "$INSULA_REPO_ROOT/foundry" /workspace/foundry
  --ro-bind "$INSULA_REPO_ROOT" /workspace/repo
)

foundry_disk_cache_host() {
  local path="${VASO_BAZEL_DISK_CACHE:-$1}"
  if [[ "$path" != /* ]]; then
    echo "Bazel disk cache must be an absolute host path: $path" >&2
    return 2
  fi
  path="$(readlink -m "$path")"
  case "$path" in
    /tmp/*|/var/tmp/*)
      echo "refusing Bazel disk cache on tmpfs-like shared scratch: $path" >&2
      return 2
      ;;
  esac
  printf '%s\n' "$path"
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
    bazel|*/bazel|bazel-real|*/bazel-real) ;;
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

insula_verify_release_dir() {
  local release_dir="$1"
  if [[ ! -d "$release_dir" ]]; then
    echo "release dir not found: $release_dir" >&2
    return 2
  fi
  if [[ ! -f "$release_dir/SHA256SUMS" ]]; then
    echo "release dir is missing SHA256SUMS: $release_dir" >&2
    return 2
  fi
  if ! (cd "$release_dir" && sha256sum -c SHA256SUMS >/dev/null); then
    echo "release SHA256SUMS verification FAILED: $release_dir/SHA256SUMS" >&2
    return 2
  fi
}

insula_release_requirements() {
  local release_dir="$1"
  local run_dir="$2"
  python3 - "$release_dir" "$run_dir" <<'PY'
from __future__ import annotations

import hashlib
import sys
from pathlib import Path

release_dir = Path(sys.argv[1]).resolve()
run_dir = Path(sys.argv[2])
for rel in (
    "requirements.txt",
    "requirements/release.txt",
    "requirements/runtime.txt",
    "requirements/locked.txt",
):
    if (release_dir / rel).is_file():
        print("/run/vaso/release/" + rel)
        raise SystemExit(0)

seen: set[Path] = set()
wheels: list[Path] = []
for rel in ("wheels", "wheelhouse", "."):
    directory = release_dir / rel
    if not directory.is_dir():
        continue
    for wheel in sorted(directory.glob("*.whl")):
        resolved = wheel.resolve()
        if resolved not in seen:
            seen.add(resolved)
            wheels.append(wheel)
if not wheels:
    raise SystemExit(f"release dir contains no wheels: {release_dir}")

requirements = run_dir / "install-requirements.txt"
requirements.parent.mkdir(parents=True, exist_ok=True)
lines: list[str] = []
for wheel in wheels:
    digest = hashlib.sha256(wheel.read_bytes()).hexdigest()
    rel = wheel.resolve().relative_to(release_dir).as_posix()
    lines.append(f"/run/vaso/release/{rel} --hash=sha256:{digest}")
requirements.write_text("\n".join(lines) + "\n", encoding="utf-8")
print("/run/vaso/run/install-requirements.txt")
PY
}

insula_release_expected_imports() {
  local release_dir="$1"
  python3 - "$release_dir" <<'PY'
from __future__ import annotations

import sys
from pathlib import Path

release_dir = Path(sys.argv[1]).resolve()
imports: list[str] = []
for wheel in sorted(release_dir.glob("**/*.whl")):
    name = wheel.name
    if name.startswith("torch-") and "torch" not in imports:
        imports.append("torch")
    if (name.startswith("jax-") or name.startswith("jaxlib-")) and "jax" not in imports:
        imports.append("jax")
print(",".join(imports))
PY
}

insula_write_release_entrypoint() {
  local run_dir="$1"
  cat > "$run_dir/release-entrypoint.sh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

if [[ -n "${VASO_RELEASE_EXPECT_IMPORTS:-}" ]]; then
  "${VASO_RELEASE_VENV:?}/bin/python3" - "$VASO_RELEASE_VENV" "$VASO_RELEASE_EXPECT_IMPORTS" <<'PY'
from __future__ import annotations

import importlib
import pathlib
import sys

venv = pathlib.Path(sys.argv[1]).resolve()
for name in [item for item in sys.argv[2].split(",") if item]:
    module = importlib.import_module(name)
    module_file = pathlib.Path(getattr(module, "__file__", "")).resolve()
    try:
        module_file.relative_to(venv)
    except ValueError:
        raise SystemExit(f"{name}.__file__={module_file} is outside release venv {venv}")
PY
fi

exec "$@"
EOF
  chmod 755 "$run_dir/release-entrypoint.sh"
}

insula_export_lease_env() {
  local lease_json="$1"
  local exports
  exports="$(python3 - "$lease_json" <<'PY'
from __future__ import annotations

import json
import shlex
import sys

lease = json.loads(sys.argv[1])
env = lease.get("env", {})
if not isinstance(env, dict):
    env = {}
for key in ("CUDA_VISIBLE_DEVICES", "VASO_GPU_SET", "VASO_HOST_CPU_JOBS"):
    if key in env:
        print(f"export {key}={shlex.quote(str(env[key]))}")
PY
)"
  [[ -z "$exports" ]] || eval "$exports"
}

insula_prepare_release_context() {
  local line="$1"
  local release_dir="$2"
  local run_id="$3"
  local bundle="${4:-}"
  : "${line:?line is required}"

  local estate="${VASO_ESTATE_ROOT:-${ESTATE_ROOT:-}}"
  if [[ -z "$estate" ]]; then
    echo "insula release run requires VASO_ESTATE_ROOT" >&2
    return 2
  fi
  estate="$(mkdir -p "$estate" && cd "$estate" && pwd -P)"
  export VASO_ESTATE_ROOT="$estate"
  ESTATE_ROOT="$estate"

  local rootfs rootfs_manifest
  if [[ -n "$bundle" ]]; then
    local bundle_base bundle_line
    bundle_base="$(basename "$bundle")"
    if [[ "$bundle_base" =~ ^rootfs-([A-Za-z0-9._-]+)-[0-9a-f]{64}\.tar\.zst$ ]]; then
      bundle_line="${BASH_REMATCH[1]}"
      if [[ "$bundle_line" != "$line" ]]; then
        echo "release rootfs bundle line $bundle_line does not match --line $line" >&2
        return 2
      fi
    fi
    insula_rootfs_from_bundle rootfs "$bundle" "$estate/rootfs-bundles" || return $?
    rootfs_manifest="$(dirname "$rootfs")/rootfs-bundle.json"
    INSULA_PREFLIGHT_LINE=""
  else
    rootfs="$estate/rootfs-lines/$line/rootfs"
    rootfs_manifest="$estate/rootfs-lines/$line/rootfs-bundle.json"
    INSULA_PREFLIGHT_LINE="$line"
  fi
  if [[ ! -d "$rootfs" ]]; then
    echo "release rootfs is missing for $line: $rootfs" >&2
    return 2
  fi
  if [[ ! -f "$rootfs_manifest" ]]; then
    echo "release rootfs manifest is missing for $line: $rootfs_manifest" >&2
    return 2
  fi

  local release_host
  release_host="$(cd "$release_dir" 2>/dev/null && pwd -P)" || {
    echo "release dir not found: $release_dir" >&2
    return 2
  }
  insula_verify_release_dir "$release_host" || return $?

  local run_host="$estate/run/$run_id"
  if [[ -e "$run_host" && "${INSULA_RELEASE_REUSE_RUN_DIR:-0}" != "1" ]]; then
    echo "release run dir already exists: $run_host" >&2
    return 2
  fi
  mkdir -p "$estate/agents/leases" "$run_host/home" "$run_host/tmp" "$run_host/cache"

  INSULA_RELEASE_RUN_HOST="$run_host"
  INSULA_RELEASE_RUN_SB="/run/vaso/run"
  INSULA_RELEASE_DIR_HOST="$release_host"
  INSULA_RELEASE_DIR_SB="/run/vaso/release"
  INSULA_RELEASE_VENV_SB="$INSULA_RELEASE_RUN_SB/venv"
  INSULA_RELEASE_REQUIREMENTS_SB="$(insula_release_requirements "$release_host" "$run_host")" || return $?
  INSULA_RELEASE_EXPECT_IMPORTS="$(insula_release_expected_imports "$release_host")" || return $?
  local python_prefix_override="${INSULA_RELEASE_PYTHON_PREFIX_HOST:-}"
  INSULA_RELEASE_PYTHON_PREFIX_HOST="${python_prefix_override:-$estate/vaso/lines/$line/cache/bazel/output-base/external/+python_native+python_313_native/prefix}"
  INSULA_RELEASE_PYTHON_PREFIX_SB="/opt/vaso/python"
  INSULA_RELEASE_PYTHON_SB="/usr/bin/python3"
  INSULA_PREFLIGHT_LAYOUT="release"
  INSULA_REQUIRE_DRIVER=1
  if [[ "${VASO_IN_INSULA:-}" == "1" ]]; then
    INSULA_DRIVER_LIB_DIRS=(/run/nvidia-driver/lib)
  fi
  local use_native_python=0 use_release_python=0
  if [[ -z "$python_prefix_override" && -x "$rootfs$INSULA_RELEASE_PYTHON_PREFIX_SB/bin/python3" ]]; then
    use_release_python=1
    INSULA_RELEASE_PYTHON_SB="$INSULA_RELEASE_PYTHON_PREFIX_SB/bin/python3"
  elif [[ -x "$INSULA_RELEASE_PYTHON_PREFIX_HOST/bin/python3" ]]; then
    use_native_python=1
    use_release_python=1
    INSULA_RELEASE_PYTHON_SB="$INSULA_RELEASE_PYTHON_PREFIX_SB/bin/python3"
  fi
  export INSULA_RELEASE_RUN_HOST INSULA_RELEASE_RUN_SB INSULA_RELEASE_DIR_HOST INSULA_RELEASE_DIR_SB
  export INSULA_RELEASE_VENV_SB INSULA_RELEASE_REQUIREMENTS_SB INSULA_RELEASE_EXPECT_IMPORTS
  export INSULA_RELEASE_PYTHON_PREFIX_HOST INSULA_RELEASE_PYTHON_PREFIX_SB INSULA_RELEASE_PYTHON_SB
  insula_write_release_entrypoint "$run_host"

  local exp_dir="$INSULA_REPO_ROOT/foundry"
  local -a mp_args
  mp_args=(
    --mountpoint /home/kvothe
    --mountpoint /workspace/experiment
    --mountpoint /workspace/third_party
    --mountpoint /run/vaso
    --mountpoint /run/vaso/release
    --mountpoint /run/vaso/run
    --mountpoint /run/nvidia-driver
    --mountpoint /run/nvidia-driver/lib
  )
  if [[ "$use_native_python" == "1" ]]; then
    mp_args+=(--mountpoint /opt/vaso --mountpoint "$INSULA_RELEASE_PYTHON_PREFIX_SB")
  fi
  mapfile -t ROOT_PLAN < <(python3 "$INSULA_REPO_ROOT/insula/estate/overlay_root.py" --base "$rootfs" "${mp_args[@]}")

  ROOTFS_BUNDLE_MANIFEST_SB="/run/vaso/rootfs-bundle.json"
  ROOTFS_MANIFEST_BIND=(--ro-bind "$rootfs_manifest" "$ROOTFS_BUNDLE_MANIFEST_SB")
  INSULA_DIR_ARGS=(--dir /run/vaso --dir /run/nvidia-driver --dir /run/nvidia-driver/lib)
  INSULA_BIND_ARGS=(
    --ro-bind "$release_host" "$INSULA_RELEASE_DIR_SB"
    --bind "$run_host" "$INSULA_RELEASE_RUN_SB"
    --bind "$run_host/home" /home/kvothe
    --ro-bind "$exp_dir" /workspace/experiment
    --ro-bind-try "$(dirname "$exp_dir")/third_party" /workspace/third_party
  )
  if [[ "$use_native_python" == "1" ]]; then
    INSULA_BIND_ARGS+=(--ro-bind "$INSULA_RELEASE_PYTHON_PREFIX_HOST" "$INSULA_RELEASE_PYTHON_PREFIX_SB")
  fi

  CUDA_DRIVER_BINDS=()
  local dev
  insula_append_cuda_driver_binds CUDA_DRIVER_BINDS
  for dev in /dev/nvidia*; do
    [[ -e "$dev" ]] && CUDA_DRIVER_BINDS+=(--dev-bind-try "$dev" "$dev")
  done
  [[ -d /proc/driver/nvidia ]] && CUDA_DRIVER_BINDS+=(--bind /proc/driver/nvidia /proc/driver/nvidia)
  [[ -d /sys ]] && CUDA_DRIVER_BINDS+=(--ro-bind-try /sys /sys)

  INSULA_CHDIR=/workspace/experiment
  local release_path="$INSULA_RELEASE_VENV_SB/bin:/usr/local/cuda/bin:/usr/bin:/bin"
  local release_ld="/run/nvidia-driver/lib:/usr/local/cuda/lib64:/usr/local/cuda/targets/x86_64-linux/lib"
  if [[ "$use_release_python" == "1" ]]; then
    release_path="$INSULA_RELEASE_VENV_SB/bin:$INSULA_RELEASE_PYTHON_PREFIX_SB/bin:/usr/local/cuda/bin:/usr/bin:/bin"
    release_ld="/run/nvidia-driver/lib:$INSULA_RELEASE_PYTHON_PREFIX_SB/lib:/usr/local/cuda/lib64:/usr/local/cuda/targets/x86_64-linux/lib"
  fi
  INSULA_ENV_ARGS=()
  _insula_append_setenv INSULA_ENV_ARGS PATH "$release_path"
  _insula_append_setenv INSULA_ENV_ARGS LD_LIBRARY_PATH "$release_ld"
  _insula_append_setenv INSULA_ENV_ARGS HOME /home/kvothe
  _insula_append_setenv INSULA_ENV_ARGS TMPDIR "$INSULA_RELEASE_RUN_SB/tmp"
  _insula_append_setenv INSULA_ENV_ARGS TEMP "$INSULA_RELEASE_RUN_SB/tmp"
  _insula_append_setenv INSULA_ENV_ARGS TMP "$INSULA_RELEASE_RUN_SB/tmp"
  _insula_append_setenv INSULA_ENV_ARGS USER kvothe
  _insula_append_setenv INSULA_ENV_ARGS LOGNAME kvothe
  _insula_append_setenv INSULA_ENV_ARGS TERM "${TERM:-xterm}"
  _insula_append_setenv INSULA_ENV_ARGS XDG_CACHE_HOME "$INSULA_RELEASE_RUN_SB/cache/xdg"
  _insula_append_setenv INSULA_ENV_ARGS CUDA_CACHE_PATH "$INSULA_RELEASE_RUN_SB/cache/cuda"
  _insula_append_setenv INSULA_ENV_ARGS VIRTUAL_ENV "$INSULA_RELEASE_VENV_SB"
  _insula_append_setenv INSULA_ENV_ARGS VASO_RELEASE_DIR "$INSULA_RELEASE_DIR_SB"
  _insula_append_setenv INSULA_ENV_ARGS VASO_RELEASE_RUN_DIR "$INSULA_RELEASE_RUN_SB"
  _insula_append_setenv INSULA_ENV_ARGS VASO_RELEASE_VENV "$INSULA_RELEASE_VENV_SB"
  _insula_append_setenv INSULA_ENV_ARGS VASO_RELEASE_EXPECT_IMPORTS "$INSULA_RELEASE_EXPECT_IMPORTS"
  _insula_append_setenv INSULA_ENV_ARGS CUDA_HOME /usr/local/cuda
  _insula_append_setenv INSULA_ENV_ARGS CUDA_PATH /usr/local/cuda
  _insula_append_setenv INSULA_ENV_ARGS VASO_CUDA_HOME /usr/local/cuda
  _insula_append_setenv INSULA_ENV_ARGS TRITON_CUDACRT_PATH /usr/local/cuda/include
  _insula_append_setenv INSULA_ENV_ARGS TRITON_CUDART_PATH /usr/local/cuda/include
  _insula_append_setenv INSULA_ENV_ARGS TRITON_CUOBJDUMP_PATH /usr/local/cuda/bin/cuobjdump
  _insula_append_setenv INSULA_ENV_ARGS TRITON_LIBCUDA_PATH /run/nvidia-driver/lib
  _insula_append_setenv INSULA_ENV_ARGS TRITON_LIBDEVICE_PATH /usr/local/cuda/nvvm/libdevice/libdevice.10.bc
  _insula_append_setenv INSULA_ENV_ARGS TRITON_NVDISASM_PATH /usr/local/cuda/bin/nvdisasm
  _insula_append_setenv INSULA_ENV_ARGS TRITON_PTXAS_BLACKWELL_PATH /usr/local/cuda/bin/ptxas
  _insula_append_setenv INSULA_ENV_ARGS TRITON_PTXAS_PATH /usr/local/cuda/bin/ptxas
  _insula_append_setenv INSULA_ENV_ARGS TRITON_CUPTI_LIB_PATH /usr/local/cuda/targets/x86_64-linux/lib
  _insula_append_setenv INSULA_ENV_ARGS TRITON_CUPTI_LIB_BLACKWELL_PATH /usr/local/cuda/targets/x86_64-linux/lib
  _insula_append_setenv INSULA_ENV_ARGS VASO_CUDA_LINE "$line"
  _insula_append_setenv INSULA_ENV_ARGS VASO_IN_INSULA 1
  _insula_append_setenv INSULA_ENV_ARGS VASO_ROOTFS_BUNDLE_MANIFEST "$ROOTFS_BUNDLE_MANIFEST_SB"
}

insula_append_release_gpu_env() {
  INSULA_ENV_ARGS+=(
    --setenv CUDA_VISIBLE_DEVICES "${CUDA_VISIBLE_DEVICES:-}"
    --setenv VASO_GPU_SET "${VASO_GPU_SET:-}"
  )
}

insula_run_release() {
  INSULA_UNSHARE_NET=1
  local line="$1"
  local release_dir="$2"
  local gpus="$3"
  local print_argv="$4"
  local bundle="$5"
  shift 5
  if [[ ! "$gpus" =~ ^[1-9][0-9]*$ ]]; then
    echo "--gpus must be a positive integer" >&2
    return 2
  fi
  if [[ "$#" -eq 0 ]]; then
    echo "insula release run requires a command" >&2
    return 2
  fi
  local run_id="${INSULA_RELEASE_RUN_ID:-release-$line-$(date -u +%Y%m%dT%H%M%SZ)-$$}"
  insula_prepare_release_context "$line" "$release_dir" "$run_id" "$bundle" || return $?

  local -a argv
  if [[ "$print_argv" == "1" ]]; then
    insula_append_release_gpu_env
    insula_build_argv argv run "$INSULA_RELEASE_RUN_SB/release-entrypoint.sh" "$@"
    insula_print_argv_json "${argv[@]}"
    return 0
  fi

  insula_preflight || return $?

  local -a venv_argv install_argv final_argv
  local rc errexit=0 gpu_lease_id=""
  case "$-" in *e*) errexit=1;; esac

  insula_build_argv venv_argv run /bin/bash -lc \
    'set -euo pipefail; "$1" - "$2" <<'"'"'PY'"'"'; "$1" -m venv --clear "$2"
import sys
if sys.version_info[:3] != (3, 13, 13):
    raise SystemExit(f"release runs require rootfs Python 3.13.13, got {sys.version.split()[0]}")
PY' \
    bash "$INSULA_RELEASE_PYTHON_SB" "$INSULA_RELEASE_VENV_SB"
  set +e
  "${venv_argv[@]}"
  rc=$?
  [[ "$errexit" == "1" ]] && set -e
  [[ "$rc" == "0" ]] || return "$rc"

  insula_build_argv install_argv run "$INSULA_RELEASE_VENV_SB/bin/python3" -m pip install \
    --no-deps \
    --no-index \
    --no-cache-dir \
    --require-hashes \
    --find-links "$INSULA_RELEASE_DIR_SB" \
    --find-links "$INSULA_RELEASE_DIR_SB/wheels" \
    --find-links "$INSULA_RELEASE_DIR_SB/wheelhouse" \
    -r "$INSULA_RELEASE_REQUIREMENTS_SB"
  set +e
  "${install_argv[@]}"
  rc=$?
  [[ "$errexit" == "1" ]] && set -e
  [[ "$rc" == "0" ]] || return "$rc"

  insula_acquire_lease gpu_lease_id gpu --amount "$gpus" || return $?
  insula_export_lease_env "$INSULA_LAST_LEASE_JSON"
  insula_append_release_gpu_env
  insula_build_argv final_argv run "$INSULA_RELEASE_RUN_SB/release-entrypoint.sh" "$@"
  set +e
  "${final_argv[@]}"
  rc=$?
  insula_release_lease "$gpu_lease_id"
  [[ "$errexit" == "1" ]] && set -e
  return "$rc"
}

insula_prepare_experiment_context() {
  local exp_dir="${1:-}"
  local line="${2:-}"
  local required_gib="${3:-${VASO_REQUIRED_GIB:-40}}"
  if [[ -z "$line" ]]; then
    echo "Foundry context: line is required" >&2
    return 2
  fi
  if [[ -z "$exp_dir" ]]; then
    echo "Foundry context: experiment directory is required" >&2
    return 2
  fi
  if [[ -z "${VASO_ESTATE_ROOT:-}" ]]; then
    echo "Foundry context: set VASO_ESTATE_ROOT" >&2
    return 2
  fi
  ESTATE_ROOT="$(python3 "$INSULA_REPO_ROOT/insula/estate/estate.py" --root "$VASO_ESTATE_ROOT" --required-gib "$required_gib" --materialize)" || return $?
  export VASO_ESTATE_ROOT="$ESTATE_ROOT"
  VASO_CUDA_LINE="$line"
  export VASO_CUDA_LINE

  VASO_HOST="$ESTATE_ROOT/vaso"
  HOME_HOST="$ESTATE_ROOT/home/kvothe"
  SOURCES_HOST="$ESTATE_ROOT/sources"
  TOOLS_BIN_HOST="$ESTATE_ROOT/rootfs-lines/$line/rootfs/opt/vaso/compat-bin"
  OPT_VASO_HOST="$ESTATE_ROOT/rootfs-lines/$line/rootfs/opt/vaso"
  BAZEL_OUTPUT_BASE_HOST="${VASO_BAZEL_OB:-$VASO_HOST/lines/$line/cache/bazel/output-base}"
  if [[ "$BAZEL_OUTPUT_BASE_HOST" != /* ]]; then
    echo "Bazel output base must be an absolute host path: $BAZEL_OUTPUT_BASE_HOST" >&2
    return 2
  fi
  BAZEL_OUTPUT_BASE_HOST="$(readlink -m "$BAZEL_OUTPUT_BASE_HOST")"
  case "$BAZEL_OUTPUT_BASE_HOST" in
    /tmp/*|/var/tmp/*)
      echo "refusing Bazel output base on tmpfs-like shared scratch: $BAZEL_OUTPUT_BASE_HOST" >&2
      return 2
      ;;
  esac
  BAZEL_OUTPUT_BASE_SB="$(native_determinism_fixed_output_base_mount)"
  BAZEL_DISK_CACHE_HOST="$(foundry_disk_cache_host "$VASO_HOST/lines/$line/cache/bazel/disk-cache")" || return $?
  BAZEL_DISK_CACHE_SB="/vaso/lines/$line/cache/bazel/disk-cache"
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
  INSULA_REQUIRE_DRIVER="${INSULA_REQUIRE_DRIVER:-0}"
  INSULA_ESTATE_MIN_FREE_GIB="${INSULA_ESTATE_MIN_FREE_GIB:-100}"

  local -a canonical_dirs mp_args
  local canonical_dirs_text
  canonical_dirs_text="$(python3 "$INSULA_REPO_ROOT/insula/estate/estate.py" --root "$ESTATE_ROOT" --print-sandbox-dirs)" || return $?
  mapfile -t canonical_dirs <<< "$canonical_dirs_text"
  mp_args=()
  local d
  for d in "${canonical_dirs[@]}"; do mp_args+=(--mountpoint "$d"); done
  mp_args+=(--mountpoint "$EXP_SB" --mountpoint /workspace/repo --mountpoint /workspace/third_party --mountpoint "$BAZEL_OUTPUT_BASE_SB" --mountpoint /run/nvidia-driver --mountpoint /run/nvidia-driver/lib)
  mapfile -t ROOT_PLAN < <(python3 "$INSULA_REPO_ROOT/insula/estate/overlay_root.py" --base "$cuda_rootfs" "${mp_args[@]}")

  ROOTFS_MANIFEST_BIND=(--ro-bind "$rootfs_manifest" "$ROOTFS_BUNDLE_MANIFEST_SB")
  INSULA_DIR_ARGS=(--dir /run/vaso --dir /run/nvidia-driver --dir /run/nvidia-driver/lib)
  INSULA_BIND_ARGS=(
    --bind "$VASO_HOST" /vaso
    --bind "$ESTATE_ROOT/agents" /vaso/agents
    --ro-bind "$SOURCES_HOST" /vaso/sources
    --bind "$BAZEL_OUTPUT_BASE_HOST" "$BAZEL_OUTPUT_BASE_SB"
    --bind "$BAZEL_DISK_CACHE_HOST" "$BAZEL_DISK_CACHE_SB"
    --ro-bind "$TOOLS_BIN_HOST" /vaso/tools/bin
    --ro-bind "$OPT_VASO_HOST" /opt/vaso
    --bind "$ESTATE_ROOT/workspace" /workspace
    --bind "$HOME_HOST" "$HOME_SB"
    --bind "$EXP_DIR" "$EXP_SB"
    --ro-bind-try "$(dirname "$EXP_DIR")/third_party" /workspace/third_party
  )
  mkdir -p \
    "$VASO_HOST/cache/bazel/repository-cache" \
    "$VASO_HOST/sources" \
    "$BAZEL_OUTPUT_BASE_HOST" \
    "$BAZEL_DISK_CACHE_HOST" \
    "$VASO_HOST/lines/$line/state/native" \
    "$VASO_HOST/lines/$line/state/stamps" \
    "$SOURCES_HOST" \
    "$ESTATE_ROOT/home/kvothe" \
    "$ESTATE_ROOT/agents/leases"
  CUDA_DRIVER_BINDS=()
  local dev
  insula_append_cuda_driver_binds CUDA_DRIVER_BINDS
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
  _insula_append_setenv INSULA_ENV_ARGS VASO_BAZEL_OB "$BAZEL_OUTPUT_BASE_SB"
  _insula_append_setenv INSULA_ENV_ARGS VASO_CUDA_HOME "$VASO_CUDA_HOME_SB"
  _insula_append_setenv INSULA_ENV_ARGS VASO_CUDA_LINE "$VASO_CUDA_LINE"
  _insula_append_setenv INSULA_ENV_ARGS CUDA_VISIBLE_DEVICES "${CUDA_VISIBLE_DEVICES:-}"
  _insula_append_setenv INSULA_ENV_ARGS VASO_GPU_SET "${VASO_GPU_SET:-}"
  _insula_append_setenv INSULA_ENV_ARGS VASO_HOST_CPU_JOBS "${VASO_HOST_CPU_JOBS:-}"
  _insula_append_setenv INSULA_ENV_ARGS VASO_LEASES_HELD "${VASO_LEASES_HELD:-}"
  _insula_append_setenv INSULA_ENV_ARGS VASO_IN_INSULA 1
  _insula_append_setenv INSULA_ENV_ARGS VASO_ROOTFS_BUNDLE_MANIFEST "$ROOTFS_BUNDLE_MANIFEST_SB"
  _insula_append_setenv INSULA_ENV_ARGS VASO_WORKSPACE_ROOT /workspace
  _insula_append_setenv INSULA_ENV_ARGS VASO_PREFIX /opt/vaso
}

foundry_insula_usage() {
  cat <<'EOF'
usage:
  foundry/insula.sh [run] --line <cu129|cu130> [--phase run|fetch] [--print-argv] -- COMMAND [ARG...]
  foundry/insula.sh [run] --line <cu129|cu130> --release DIR [--bundle TAR.ZST] [--gpus N] [--print-argv] -- COMMAND [ARG...]

Set VASO_ESTATE_ROOT to an owned estate with a prepared rootfs.
--line defaults to VASO_CUDA_LINE when set.
--phase defaults to run (or INSULA_PHASE); fetch requires SHA-256-pinned inputs.
--release runs shipped wheels offline using the bundled Python runtime.
--bundle selects a rootfs archive for release execution; it requires --release.
--gpus reserves N GPUs for release execution (default: 1 or INSULA_RELEASE_GPUS).
--print-argv prints the planned command without preflight or execution;
context preparation still runs and may create estate state.

For unattended wheel builds, use foundry/build.sh --help.
See docs/operator-guide.md and docs/foundry.md for preparation and examples.
EOF
}

foundry_insula_main() {
  local line="${VASO_CUDA_LINE:-}"
  local print_argv=0
  local phase="${INSULA_PHASE:-run}"
  local release_dir=""
  local release_bundle="${INSULA_RELEASE_BUNDLE:-}"
  local release_gpus="${INSULA_RELEASE_GPUS:-1}"
  if [[ "${1:-}" == "run" ]]; then
    shift
  fi
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
      -h|--help)
        foundry_insula_usage
        return 0
        ;;
      *)
        echo "unknown argument: $1" >&2
        return 2
        ;;
    esac
  done
  if [[ -z "$line" ]]; then
    echo "Foundry execution requires --line or VASO_CUDA_LINE" >&2
    foundry_insula_usage >&2
    return 2
  fi
  if [[ "$#" -eq 0 ]]; then
    echo "Foundry execution requires a command after --" >&2
    foundry_insula_usage >&2
    return 2
  fi
  if [[ -n "$release_dir" ]]; then
    VASO_CUDA_LINE="$line"
    export VASO_CUDA_LINE
    insula_run_release "$line" "$release_dir" "$release_gpus" "$print_argv" "$release_bundle" "$@"
    return $?
  fi
  if [[ -n "$release_bundle" ]]; then
    echo "insula --bundle requires --release" >&2
    return 2
  fi
  insula_prepare_experiment_context "$INSULA_REPO_ROOT/foundry" "$line" || return $?
  if [[ "$print_argv" == 0 ]]; then
    python3 "$INSULA_REPO_ROOT/insula/rootfs/foundry_tools.py" --rootfs "$ESTATE_ROOT/rootfs-lines/$line/rootfs" --verify >/dev/null || return $?
  fi
  local args=()
  [[ "$print_argv" == "1" ]] && args+=(--print-argv)
  INSULA_UNSHARE_NET=1
  [[ "$phase" != fetch ]] || INSULA_UNSHARE_NET=0
  args+=(--phase "$phase" -- "$@")
  insula "${args[@]}"
}
