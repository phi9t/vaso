#!/usr/bin/env bash
# Build and test downstream consumer packages inside the selected insula line.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
EXPERIMENT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
REPO_ROOT="$(cd "$EXPERIMENT_ROOT/.." && pwd)"
INSULA_SH="$REPO_ROOT/foundry/insula.sh"

line="${VASO_CUDA_LINE:-}"
profile="${VASO_PROFILE:-torch}"
config="${VASO_CONSUMER_CONFIG:-}"
release_dir=""
print_release_commands_for_test=0
extra_args=()

while [[ "$#" -gt 0 ]]; do
  case "$1" in
    --line)
      [[ "$#" -ge 2 ]] || { echo "--line requires a value" >&2; exit 2; }
      line="$2"
      shift 2
      ;;
    --config)
      [[ "$#" -ge 2 ]] || { echo "--config requires a value" >&2; exit 2; }
      config="$2"
      shift 2
      ;;
    --profile)
      [[ "$#" -ge 2 ]] || { echo "--profile requires a value" >&2; exit 2; }
      profile="$2"
      shift 2
      ;;
    --release-dir)
      [[ "$#" -ge 2 ]] || { echo "--release-dir requires a value" >&2; exit 2; }
      release_dir="$2"
      shift 2
      ;;
    --print-release-commands-for-test)
      print_release_commands_for_test=1
      shift
      ;;
    --)
      shift
      extra_args+=("$@")
      break
      ;;
    *)
      extra_args+=("$1")
      shift
      ;;
  esac
done

case "$line" in
  cu129|cu130) ;;
  "") echo "--line is required unless VASO_CUDA_LINE is set" >&2; exit 2 ;;
  *) echo "unknown line=$line; expected cu129 or cu130" >&2; exit 2 ;;
esac
case "$profile" in
  torch|jax) ;;
  *) echo "unknown profile=$profile; expected torch or jax" >&2; exit 2 ;;
esac
if [[ -z "$config" ]]; then
  case "$profile" in
    torch) config="vaso_rootfs" ;;
    jax) config="vaso_jax_rootfs" ;;
  esac
fi
bazel_config_arg="--config=$config"
bazel_line_arg="--@vaso_graph//native/common:release_line=$line"

release_runner_path() {
  if [[ -n "${VASO_RELEASE_INSULA_SH:-}" ]]; then
    printf '%s\n' "$VASO_RELEASE_INSULA_SH"
  elif [[ -x /workspace/repo/foundry/insula.sh ]]; then
    printf '%s\n' /workspace/repo/foundry/insula.sh
  else
    printf '%s\n' "$INSULA_SH"
  fi
}

release_python_consumer_argv() {
  local out_name="$1"
  local selected_profile="$2"
  local -n out="$out_name"
  local module runner python_prefix_host estate_root
  case "$selected_profile" in
    torch) module="python_consumers.verify_torch" ;;
    jax) module="python_consumers.verify_jax" ;;
    *) echo "unknown profile=$selected_profile; expected torch or jax" >&2; return 2 ;;
  esac
  [[ -n "$release_dir" ]] || { echo "--release-dir is required for Python release consumers" >&2; return 2; }
  runner="$(release_runner_path)"
  if [[ -n "${INSULA_RELEASE_PYTHON_PREFIX_HOST:-}" ]]; then
    python_prefix_host="$INSULA_RELEASE_PYTHON_PREFIX_HOST"
  else
    python_prefix_host=""
    for candidate in \
      "${VASO_BAZEL_OB:-/vaso-bazel-ob}/external/+python_native+python_313_native/prefix" \
      "/vaso/lines/$line/cache/bazel/output-base/external/+python_native+python_313_native/prefix"; do
      if [[ -x "$candidate/bin/python3" || -x "$candidate/bin/python3.13" ]]; then
        python_prefix_host="$candidate"
        break
      fi
    done
    python_prefix_host="${python_prefix_host:-${VASO_BAZEL_OB:-/vaso-bazel-ob}/external/+python_native+python_313_native/prefix}"
  fi
  estate_root="${VASO_ESTATE_ROOT:-/vaso}"
  # The release runner starts the sandbox with a cleared environment, so the module
  # path is set by the command that runs inside it.
  out=(
    env
    "VASO_ESTATE_ROOT=$estate_root"
    "INSULA_RELEASE_PYTHON_PREFIX_HOST=$python_prefix_host"
    "$runner"
    run
    --line "$line"
    --release "$release_dir"
    --gpus 1
    --
    env
    "PYTHONPATH=/workspace/experiment/examples/consumers"
    python3
    -m "$module"
  )
}

print_release_commands_json() {
  python3 - "$@" <<'PY'
import json
import shlex
import sys

print(json.dumps([" ".join(shlex.quote(arg) for arg in sys.argv[1:])]))
PY
}

run_release_python_consumers() {
  [[ -n "$release_dir" ]] || return 0
  local release_argv=()
  release_python_consumer_argv release_argv "$profile"
  "${release_argv[@]}"
}

if [[ "$print_release_commands_for_test" == "1" ]]; then
  release_argv=()
  release_python_consumer_argv release_argv "$profile"
  print_release_commands_json "${release_argv[@]}"
  exit 0
fi

if [[ "${VASO_IN_INSULA:-}" != "1" ]]; then
  : "${VASO_ESTATE_ROOT:?set VASO_ESTATE_ROOT}"
  [[ -x "$INSULA_SH" ]] || { echo "missing insula runner: $INSULA_SH" >&2; exit 2; }
  pass_args=(--line "$line" --config "$config" --profile "$profile")
  if [[ -n "$release_dir" ]]; then
    pass_args+=(--release-dir "$release_dir")
  fi
  pass_args+=(-- "${extra_args[@]}")
  exec env \
    VASO_CUDA_LINE="$line" \
    VASO_GPUS=0 \
    VASO_PROFILE="$profile" \
    "$EXPERIMENT_ROOT/run.sh" \
    --insula-cmd \
    /workspace/experiment/examples/consumers/run.sh \
    "${pass_args[@]}"
fi

bazel_exe="${VASO_BAZEL_EXE:-/vaso/tools/bin/bazel}"
if [[ ! -x "$bazel_exe" ]]; then
  bazel_exe="bazel"
fi

cd /workspace/experiment/examples/consumers

if [[ "$profile" == "jax" ]]; then
  if [[ -z "$release_dir" ]]; then
    echo "jax profile consumer conformance requires --release-dir" >&2
    exit 2
  fi
  run_release_python_consumers
  exit 0
fi

consumer_ob="${VASO_CONSUMER_BAZEL_OB:-/vaso/lines/$line/cache/bazel/consumer-output-base}"
mkdir -p "$consumer_ob" "/vaso/tmp/bazel-consumers-$line"

if [[ -n "${VASO_TORCH_PREFIX:-}" ]]; then
  torch_prefix="$VASO_TORCH_PREFIX"
else
  torch_artifact_prefix_file="$(
    "$bazel_exe" \
      --output_base="$consumer_ob" \
      "--host_jvm_args=-Djava.io.tmpdir=/vaso/tmp/bazel-consumers-$line" \
      --batch \
      cquery \
      "$bazel_config_arg" \
      "$bazel_line_arg" \
      --noshow_progress \
      --output=files \
      "@vaso_torch_artifact_$line//:prefix_path.txt" \
      --repository_cache=/vaso/cache/bazel/repository-cache
  )"
  torch_artifact_prefix_file="${torch_artifact_prefix_file//$'\r'/}"
  torch_artifact_prefix_file="${torch_artifact_prefix_file##*$'\n'}"
  if [[ "$torch_artifact_prefix_file" != /* ]]; then
    torch_artifact_prefix_relative="$torch_artifact_prefix_file"
    if [[ -f "$consumer_ob/$torch_artifact_prefix_relative" ]]; then
      torch_artifact_prefix_file="$consumer_ob/$torch_artifact_prefix_relative"
    else
      bazel_execroot="$(
        "$bazel_exe" \
          --output_base="$consumer_ob" \
          "--host_jvm_args=-Djava.io.tmpdir=/vaso/tmp/bazel-consumers-$line" \
          --batch \
          info \
          execution_root
      )"
      bazel_execroot="${bazel_execroot//$'\r'/}"
      bazel_execroot="${bazel_execroot##*$'\n'}"
      torch_artifact_prefix_file="$bazel_execroot/$torch_artifact_prefix_relative"
    fi
  fi
  if [[ ! -f "$torch_artifact_prefix_file" ]]; then
    echo "missing torch artifact prefix marker: $torch_artifact_prefix_file" >&2
    echo "expected @vaso_torch_artifact_$line//:prefix_path.txt to resolve from the pinned artifact" >&2
    exit 2
  fi
  torch_prefix="$(< "$torch_artifact_prefix_file")"
  torch_prefix="${torch_prefix//$'\r'/}"
  torch_prefix="${torch_prefix%%$'\n'*}"
fi
torch_include="$torch_prefix/lib/python3.13/site-packages/torch/include"
torch_aten="$torch_include/ATen/ATen.h"
if [[ ! -f "$torch_aten" ]]; then
  echo "missing torch artifact include root for @vaso_torch_artifact_$line" >&2
  echo "last checked include root: $torch_include" >&2
  echo "refresh the pinned torch artifact or set VASO_TORCH_PREFIX" >&2
  exit 2
fi

if [[ -n "${VASO_PYTHON_PREFIX:-}" ]]; then
  python_prefix="$VASO_PYTHON_PREFIX"
else
  python_prefix=""
  for candidate in \
    "/vaso-bazel-ob/external/+python_native+python_313_native/prefix" \
    "/vaso/lines/$line/cache/bazel/output-base/external/+python_native+python_313_native/prefix"; do
    if [[ -x "$candidate/bin/python3.13" ]]; then
      python_prefix="$candidate"
      break
    fi
  done
fi
if [[ ! -x "$python_prefix/bin/python3.13" ]]; then
  echo "missing profile Python 3.13.13 prefix under /vaso-bazel-ob or /vaso/lines/$line/cache/bazel/output-base" >&2
  echo "last checked Python prefix: $python_prefix" >&2
  echo "build the profile Python prefix first or set VASO_PYTHON_PREFIX" >&2
  exit 2
fi

zstd_prefix="/vaso-bazel-ob/external/+zstd_native+zstd_native/prefix"
if [[ ! -f "$zstd_prefix/include/zstd.h" || ! -f "$zstd_prefix/lib/libzstd.so" ]]; then
  echo "missing selected profile zstd headers/library: $zstd_prefix" >&2
  exit 2
fi
zstd_profile_repo="/vaso/tmp/zstd-profile-$line"
rm -rf "$zstd_profile_repo"
mkdir -p "$zstd_profile_repo"
cat > "$zstd_profile_repo/MODULE.bazel" <<'EOF'
module(name = "vaso_zstd", version = "0.0.0")
EOF
cat > "$zstd_profile_repo/BUILD.bazel" <<'EOF'
load("@rules_cc//cc:defs.bzl", "cc_library")

cc_library(
    name = "lib",
    srcs = ["lib/libzstd.so"],
    hdrs = glob(["include/**"], allow_empty = False),
    includes = ["include"],
    visibility = ["//visibility:public"],
)
EOF
ln -s "$zstd_prefix/include" "$zstd_profile_repo/include"
ln -s "$zstd_prefix/lib" "$zstd_profile_repo/lib"

torch_profile_repo="/vaso/tmp/libtorch-profile-$line"
rm -rf "$torch_profile_repo"
mkdir -p "$torch_profile_repo"
cat > "$torch_profile_repo/MODULE.bazel" <<'EOF'
module(name = "vaso_libtorch_profile", version = "0.0.0")
EOF
cat > "$torch_profile_repo/BUILD.bazel" <<'EOF'
load("@rules_cc//cc:defs.bzl", "cc_library")

package(default_visibility = ["//visibility:public"])

cc_library(
    name = "headers",
    hdrs = glob(["include/**"], allow_empty = False),
    includes = [
        "include",
        "include/torch/csrc/api/include",
    ],
)
EOF
ln -s "$torch_include" "$torch_profile_repo/include"
if grep -q "__cplusplus < 202002L" "$torch_aten"; then
  torch_cxx_standard="c++20"
elif grep -q "__cplusplus < 201703L" "$torch_aten"; then
  torch_cxx_standard="c++17"
else
  echo "could not infer torch C++ standard from $torch_aten" >&2
  exit 2
fi
torch_plan="/workspace/experiment/plans/torch/$line/pytorch.plan.json"
torch_cxx11_abi="$(
  python3 - "$torch_plan" <<'PY'
import re
import sys
from pathlib import Path

text = Path(sys.argv[1]).read_text(encoding="utf-8")
match = re.search(r"export _GLIBCXX_USE_CXX11_ABI=([01])\b", text)
if match is None:
    raise SystemExit("missing _GLIBCXX_USE_CXX11_ABI in committed torch plan")
print(match.group(1))
PY
)"
"$bazel_exe" \
  --output_base="$consumer_ob" \
  "--host_jvm_args=-Djava.io.tmpdir=/vaso/tmp/bazel-consumers-$line" \
  --batch \
  test \
  "$bazel_config_arg" \
  "$bazel_line_arg" \
  --keep_going \
  "--define=VASO_TORCH_PREFIX=$torch_prefix" \
  "--define=VASO_TORCH_CXX_STANDARD=$torch_cxx_standard" \
  "--define=VASO_TORCH_CXX11_ABI=$torch_cxx11_abi" \
  "--define=VASO_PYTHON_PREFIX=$python_prefix" \
  "--inject_repository=vaso_libtorch_profile=$torch_profile_repo" \
  "--inject_repository=vaso_zstd=$zstd_profile_repo" \
  --repository_cache=/vaso/cache/bazel/repository-cache \
  --test_tag_filters=-requires-gpu \
  //hello_cc:hello_cc \
  //hello_cc:hello_cc_test \
  //geom_core:geometry_test \
  //geom_core:camera_test \
  //geom_core:libtorch_cli \
  //geom_core:libtorch_cli_cpu_test \
  //geom_core:libtorch_cli_cuda_test \
  //geom_core:protobuf_coexistence_test \
  //python_consumers:reference_math_test \
  //rust_pyo3:rust_pyo3_wheel \
  //rust_pyo3:rust_pyo3_wheel_verifier \
  //rust_pyo3:rust_pyo3_host_toolchain_poison_test \
  //nvshmem_ep:nvshmem_ep_cuda_test \
  //voxel_scatter:voxel_scatter_cpu_test \
  //voxel_scatter:voxel_scatter_cuda_test \
  //cutlass_gemm:cutlass_gemm_cpu_test \
  //cutlass_gemm:cutlass_gemm_cuda_test \
  //gsplat_rasterizer:gsplat_rasterizer \
  //gsplat_rasterizer:gsplat_rasterizer_cuda_test \
  "${extra_args[@]}"

run_release_python_consumers
