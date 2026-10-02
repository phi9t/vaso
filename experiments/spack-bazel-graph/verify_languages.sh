#!/usr/bin/env bash
# Verify bash / C++ / Python / Rust / CUDA can build and run INSIDE the insula.
#
# This is a runtime capability check using the host toolchains projected through
# the insula (compilers, python, cargo, nvcc), not a Bazel build. It proves the
# sealed/host-fallback insula presents a usable multi-language dev environment
# and, for CUDA, that GPU device nodes + driver DSOs are projected correctly.
#
# Each check emits a JSON line; the harness asserts ok:true for every enabled
# language. CUDA is skipped (not failed) when no GPU/driver is present.
set -euo pipefail

EXP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$EXP_DIR/../.." && pwd)"
INSULA_SH="$REPO_ROOT/scripts/insula/insula.sh"
. "$INSULA_SH"
ESTATE_ROOT="${VASO_ESTATE_ROOT:-$(python3 "$EXP_DIR/tools/estate.py" --required-gib 1 --prefer "$(df -P "$EXP_DIR" | awk 'NR==2{print $6}')")}"

mapfile -t CANONICAL_DIRS < <(python3 "$EXP_DIR/tools/estate.py" --root "$ESTATE_ROOT" --print-sandbox-dirs)
MP_ARGS=(); for d in "${CANONICAL_DIRS[@]}"; do MP_ARGS+=(--mountpoint "$d"); done
mapfile -t ROOT_PLAN < <(python3 "$EXP_DIR/tools/overlay_root.py" --base / "${MP_ARGS[@]}")

VASO_HOST="$ESTATE_ROOT/vaso"
HOME_HOST="$ESTATE_ROOT/home/kvothe"
TOOLS_BIN_HOST="$VASO_HOST/tools/bin"
. "$EXP_DIR/tools/insula_io.sh"
init_insula_io "$VASO_HOST"
trap cleanup_insula_io EXIT
echo "insula tmp: $INSULA_TMP_SB (private tmpfs size: $INSULA_TMPFS_SIZE bytes)"

# --- host toolchain discovery (projected read-only into the insula) ----------
CC_BIN="$(command -v gcc || true)"
CXX_BIN="$(command -v g++ || true)"
PY_BIN="$(command -v python3 || true)"
# rustc via rustup proxy needs rustup's config/HOME; resolve the actual toolchain
# rustc so the insula can run it without a rustup default being configured.
# Rust: resolve the actual toolchain rustc, not the rustup proxy. The proxy
# refuses to run in the scrubbed insula env (no default toolchain resolvable),
# so prefer `rustup which` which points at a concrete toolchain binary.
RUSTC_BIN="$(command -v rustc || true)"
if command -v rustup >/dev/null 2>&1; then
  RUSTC_BIN="$(rustup which rustc 2>/dev/null || echo "$RUSTC_BIN")"
fi
NVCC_BIN="$(ls /usr/local/cuda*/bin/nvcc 2>/dev/null | head -1 || true)"
# nvcc may be a symlink into another volume; resolve so we bind the real toolkit.
NVCC_REAL="$(readlink -f "$NVCC_BIN" 2>/dev/null || echo "$NVCC_BIN")"
CUDA_HOME="${NVCC_REAL%/bin/nvcc}"

# Toolchain dirs to expose on the insula PATH (deduped).
declare -A TOOLDIRS=()
for b in "$CC_BIN" "$CXX_BIN" "$PY_BIN" "$RUSTC_BIN" "$NVCC_BIN"; do
  [[ -n "$b" ]] && TOOLDIRS["$(dirname "$(readlink -f "$b")")"]=1
done
INNER_PATH="/vaso/tools/bin"
for d in "${!TOOLDIRS[@]}"; do INNER_PATH+=":$d"; done
INNER_PATH+=":/usr/bin:/bin"

# CUDA driver DSOs + device nodes for the CUDA smoke.
CUDA_BINDS=()
GPU_AVAILABLE=0
DEV_MODE=(--dev /dev)  # fresh /dev by default
if [[ -n "$NVCC_BIN" ]] && command -v nvidia-smi >/dev/null 2>&1 && nvidia-smi -L >/dev/null 2>&1; then
  GPU_AVAILABLE=1
  # A fresh --dev tmpfs plus selective --dev-bind-try does not give the NVIDIA
  # runtime a coherent enough /dev for device init here; bind the host /dev
  # wholesale for GPU runs (still inside the user namespace).
  DEV_MODE=(--dev-bind /dev /dev)
  # Host driver DSOs (userspace libcuda / NVML). ldconfig may be unusable on
  # Nix-provisioned hosts, so probe the standard driver lib dirs directly.
  for soname in libcuda.so.1 libnvidia-ml.so.1; do
    lib=""
    for d in /usr/lib/x86_64-linux-gnu /usr/lib64 /lib/x86_64-linux-gnu; do
      if [[ -e "$d/$soname" ]]; then lib="$d/$soname"; break; fi
    done
    if [[ -n "$lib" ]]; then
      CUDA_BINDS+=(--ro-bind "$lib" "/run/nvidia-driver/lib/$soname")
      real="$(readlink -f "$lib")"
      [[ "$real" != "$lib" ]] && CUDA_BINDS+=(--ro-bind "$real" "/run/nvidia-driver/lib/$(basename "$real")")
    fi
  done
  [[ -d "$CUDA_HOME" ]] && CUDA_BINDS+=(--ro-bind "$CUDA_HOME" "$CUDA_HOME")
  # The NVIDIA userspace driver reads /proc/driver/nvidia and /sys; a fresh
  # --proc hides the former and the base plan skips /sys, so bind both.
  [[ -d /proc/driver/nvidia ]] && CUDA_BINDS+=(--bind /proc/driver/nvidia /proc/driver/nvidia)
  [[ -d /sys ]] && CUDA_BINDS+=(--ro-bind-try /sys /sys)
fi

# CUDA device init also fails as namespace-root; map the host uid/gid.
HUID="$(id -u)"; HGID="$(id -g)"
INSULA_UID="$HUID"
INSULA_GID="$HGID"
INSULA_UNSHARE_IPC=0
INSULA_REQUIRE_DRIVER="$GPU_AVAILABLE"
INSULA_DEV_MODE=("${DEV_MODE[@]}")
INSULA_DIR_ARGS=(--dir /run/vaso --dir /run/nvidia-driver --dir /run/nvidia-driver/lib)
INSULA_BIND_ARGS=(
  --bind "$VASO_HOST" /vaso
  --ro-bind "$TOOLS_BIN_HOST" /vaso/tools/bin
  --ro-bind "$ESTATE_ROOT/opt-vaso" /opt/vaso
  --bind "$ESTATE_ROOT/workspace" /workspace
  --bind "$HOME_HOST" /home/kvothe
  --bind "$EXP_DIR" /workspace/experiment
)
CUDA_DRIVER_BINDS=("${CUDA_BINDS[@]}")
INSULA_ENV_ARGS=(
  --setenv PATH "$INNER_PATH"
  --setenv HOME /home/kvothe
  --setenv TMPDIR "$INSULA_TMP_SB"
  --setenv USER kvothe
  --setenv LOGNAME kvothe
  --setenv TERM "${TERM:-xterm}"
  --setenv LD_LIBRARY_PATH "/usr/lib/x86_64-linux-gnu:/run/nvidia-driver/lib${CUDA_HOME:+:$CUDA_HOME/lib64}"
  --setenv CUDA_HOME "${CUDA_HOME:-}"
)
INSULA_CHDIR=/workspace/experiment

pass=0; fail=0
check() {  # $1=label  $2..=command
  local label="$1"; shift
  echo "-- $label --"
  if insula "$@"; then
    pass=$((pass + 1))
  else
    echo "{\"lang\": \"$label\", \"ok\": false, \"stage\": \"insula-exec\"}"
    fail=$((fail + 1))
  fi
}

echo "estate: $ESTATE_ROOT   gpu_available=$GPU_AVAILABLE"

# bash
check bash bash -c 'echo "{\"lang\": \"bash\", \"sum\": $((20+22)), \"ok\": true}"'

# C++
check cpp bash -c 'g++ -O2 -o "$TMPDIR/hello_cpp" lang/cpp/hello.cc && "$TMPDIR/hello_cpp"'

# Python
check python bash -c 'python3 lang/python/hello.py'

# Rust: invoke the resolved toolchain rustc by absolute path (the rustup proxy
# on PATH refuses to run without a configured default inside the scrubbed env).
check rust bash -c "'$RUSTC_BIN' -O -o \"\$TMPDIR/hello_rust\" lang/rust/hello.rs && \"\$TMPDIR/hello_rust\""

# CUDA (skip cleanly when no GPU/driver). Build for the detected arch; `native`
# lets nvcc target the present GPU (B200 = sm_100) without a hardcoded arch.
if [[ "$GPU_AVAILABLE" -eq 1 ]]; then
  check cuda bash -c 'nvcc -arch=native -o "$TMPDIR/hello_cuda" lang/cuda/hello.cu && "$TMPDIR/hello_cuda"'
else
  echo '{"lang": "cuda", "ok": true, "skipped": "no gpu/driver on host"}'
fi

echo "== language checks: pass=$pass fail=$fail =="
[[ "$fail" -eq 0 ]]
