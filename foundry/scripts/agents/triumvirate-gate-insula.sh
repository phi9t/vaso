#!/usr/bin/env bash
# Insula-side implementation of the triumvirate acceptance gate.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
EXPERIMENT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
REPO_ROOT="${VASO_REPO_ROOT:-/workspace/repo}"
. "$EXPERIMENT_ROOT/tools/native_determinism_args.sh"

line="${VASO_CUDA_LINE:-}"
profile="${VASO_PROFILE:-}"
commit=""
source_date_epoch=""
out_dir=""
acceptance_out=""
waivers="acceptance-waivers.json"
python_prefix=""
release_mode=0
torch_prefix=""
torch_ref_prefix=""
torchvision_prefix=""
torchaudio_prefix=""
triton_prefix=""
jax_prefix=""
jaxlib_prefix=""
runtime_prefixes=()
torch_prefix_overridden=0
torch_ref_prefix_overridden=0
torchvision_prefix_overridden=0
torchaudio_prefix_overridden=0
triton_prefix_overridden=0
jaxlib_prefix_overridden=0

: "${VASO_ESTATE_ROOT:?set VASO_ESTATE_ROOT}"

while [[ "$#" -gt 0 ]]; do
  case "$1" in
    --profile)
      [[ "$#" -ge 2 ]] || { echo "--profile requires a value" >&2; exit 2; }
      profile="$2"
      shift 2
      ;;
    --line)
      [[ "$#" -ge 2 ]] || { echo "--line requires a value" >&2; exit 2; }
      line="$2"
      shift 2
      ;;
    --commit)
      [[ "$#" -ge 2 ]] || { echo "--commit requires a value" >&2; exit 2; }
      commit="$2"
      shift 2
      ;;
    --source-date-epoch)
      [[ "$#" -ge 2 ]] || { echo "--source-date-epoch requires a value" >&2; exit 2; }
      source_date_epoch="$2"
      shift 2
      ;;
    --out-dir)
      [[ "$#" -ge 2 ]] || { echo "--out-dir requires a value" >&2; exit 2; }
      out_dir="$2"
      shift 2
      ;;
    --acceptance-out)
      [[ "$#" -ge 2 ]] || { echo "--acceptance-out requires a value" >&2; exit 2; }
      acceptance_out="$2"
      shift 2
      ;;
    --release)
      release_mode=1
      shift
      ;;
    --python-prefix)
      [[ "$#" -ge 2 ]] || { echo "--python-prefix requires a value" >&2; exit 2; }
      python_prefix="$2"
      shift 2
      ;;
    --torch-prefix)
      [[ "$#" -ge 2 ]] || { echo "--torch-prefix requires a value" >&2; exit 2; }
      torch_prefix="$2"
      torch_prefix_overridden=1
      shift 2
      ;;
    --torch-ref-prefix)
      [[ "$#" -ge 2 ]] || { echo "--torch-ref-prefix requires a value" >&2; exit 2; }
      torch_ref_prefix="$2"
      torch_ref_prefix_overridden=1
      shift 2
      ;;
    --torchvision-prefix|--vision-prefix)
      [[ "$#" -ge 2 ]] || { echo "$1 requires a value" >&2; exit 2; }
      torchvision_prefix="$2"
      torchvision_prefix_overridden=1
      shift 2
      ;;
    --torchaudio-prefix|--audio-prefix)
      [[ "$#" -ge 2 ]] || { echo "$1 requires a value" >&2; exit 2; }
      torchaudio_prefix="$2"
      torchaudio_prefix_overridden=1
      shift 2
      ;;
    --triton-prefix)
      [[ "$#" -ge 2 ]] || { echo "--triton-prefix requires a value" >&2; exit 2; }
      triton_prefix="$2"
      triton_prefix_overridden=1
      shift 2
      ;;
    --jax-prefix)
      [[ "$#" -ge 2 ]] || { echo "--jax-prefix requires a value" >&2; exit 2; }
      jax_prefix="$2"
      shift 2
      ;;
    --jaxlib-prefix)
      [[ "$#" -ge 2 ]] || { echo "--jaxlib-prefix requires a value" >&2; exit 2; }
      jaxlib_prefix="$2"
      jaxlib_prefix_overridden=1
      shift 2
      ;;
    --runtime-prefix)
      [[ "$#" -ge 2 ]] || { echo "--runtime-prefix requires a value" >&2; exit 2; }
      runtime_prefixes+=("$2")
      shift 2
      ;;
    *)
      echo "unknown argument: $1" >&2
      exit 2
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
  "") echo "--profile is required unless VASO_PROFILE is set" >&2; exit 2 ;;
  *) echo "unknown profile=$profile; expected torch or jax" >&2; exit 2 ;;
esac
if [[ "${VASO_IN_INSULA:-}" != "1" ]]; then
  echo "triumvirate gate must run inside run.sh --insula-cmd (VASO_IN_INSULA=1)" >&2
  exit 2
fi
if [[ -z "$commit" ]]; then
  echo "--commit is required inside the insula; use scripts/agents/triumvirate-gate.sh from the repo root" >&2
  exit 2
fi

ob="$(native_determinism_fixed_output_base_mount)"
python_prefix="${python_prefix:-$ob/external/+python_native+python_313_native/prefix}"
jax_prefix="${jax_prefix:-$ob/external/+py_jax_native+py_jax_native/prefix}"
estate_root="/vaso"
export VASO_ESTATE_ROOT="$estate_root"
release_bazel_ob="$ob"
release_bazel_bin="$release_bazel_ob/execroot/_main/bazel-out/k8-fastbuild/bin"
release_python_prefix="$release_bazel_ob/external/+python_native+python_313_native/prefix"
source_date_epoch="$(native_determinism_source_date_epoch "$REPO_ROOT" "$commit" "${source_date_epoch:-${VASO_SOURCE_DATE_EPOCH:-}}")" || exit 2

torch_runtime_prefixes=()
jaxlib_deps_prefixes=()
jax_runtime_prefixes=()
configure_default_runtime_prefixes() {
  if [[ "${#runtime_prefixes[@]}" -ne 0 ]]; then
    torch_runtime_prefixes=("${runtime_prefixes[@]}")
    jaxlib_deps_prefixes=("${runtime_prefixes[@]}")
    jax_runtime_prefixes=("${runtime_prefixes[@]}")
    return 0
  fi

  torch_runtime_prefixes=(
    "py-numpy=$ob/external/+py_numpy_native+py_numpy_native/prefix"
    "py-typing-extensions=$ob/external/+py_typing_extensions_native+py_typing_extensions_native/prefix"
    "py-sympy=$ob/external/+py_sympy_native+py_sympy_native/prefix"
    "py-mpmath=$ob/external/+py_mpmath_native+py_mpmath_native/prefix"
    "py-filelock=$ob/external/+py_filelock_native+py_filelock_native/prefix"
    "py-networkx=$ob/external/+py_networkx_native+py_networkx_native/prefix"
    "py-jinja2=$ob/external/+py_jinja2_native+py_jinja2_native/prefix"
    "py-markupsafe=$ob/external/+py_markupsafe_native+py_markupsafe_native/prefix"
    "py-fsspec=$ob/external/+py_fsspec_native+py_fsspec_native/prefix"
    "py-setuptools=$ob/external/+py_setuptools_native+py_setuptools_native/prefix"
    "py-lit=$ob/external/+py_lit_native+py_lit_native/prefix"
    "py-pillow=$ob/external/+py_pillow_native+py_pillow_native/prefix"
    "libjpeg-turbo=$ob/external/+libjpeg_turbo_native+libjpeg_turbo_native/prefix"
    "libpng=$ob/external/+libpng_native+libpng_native/prefix"
    "triton=$triton_prefix"
    "torchvision=$torchvision_prefix"
    "torchaudio=$torchaudio_prefix"
  )
  jaxlib_deps_prefixes=(
    "py-absl-py=$ob/external/+py_absl_py_native+py_absl_py_native/prefix"
    "py-beniget=$ob/external/+py_beniget_native+py_beniget_native/prefix"
    "py-build=$ob/external/+py_build_native+py_build_native/prefix"
    "py-calver=$ob/external/+py_calver_native+py_calver_native/prefix"
    "py-cython=$ob/external/+py_cython_native+py_cython_native/prefix"
    "py-flit-core=$ob/external/+py_flit_core_native+py_flit_core_native/prefix"
    "py-gast=$ob/external/+py_gast_native+py_gast_native/prefix"
    "py-hatch-fancy-pypi-readme=$ob/external/+py_hatch_fancy_pypi_readme_native+py_hatch_fancy_pypi_readme_native/prefix"
    "py-hatch-vcs=$ob/external/+py_hatch_vcs_native+py_hatch_vcs_native/prefix"
    "py-hatchling=$ob/external/+py_hatchling_native+py_hatchling_native/prefix"
    "py-meson-python=$ob/external/+py_meson_python_native+py_meson_python_native/prefix"
    "py-ml-dtypes=$ob/external/+py_ml_dtypes_native+py_ml_dtypes_native/prefix"
    "py-numpy=$ob/external/+py_numpy_native+py_numpy_native/prefix"
    "py-opt-einsum=$ob/external/+py_opt_einsum_native+py_opt_einsum_native/prefix"
    "py-packaging=$ob/external/+py_packaging_native+py_packaging_native/prefix"
    "py-pathspec=$ob/external/+py_pathspec_native+py_pathspec_native/prefix"
    "py-pip=$ob/external/+py_pip_native+py_pip_native/prefix"
    "py-pluggy=$ob/external/+py_pluggy_native+py_pluggy_native/prefix"
    "py-ply=$ob/external/+py_ply_native+py_ply_native/prefix"
    "py-pybind11=$ob/external/+py_pybind11_native+py_pybind11_native/prefix"
    "py-pyproject-hooks=$ob/external/+py_pyproject_hooks_native+py_pyproject_hooks_native/prefix"
    "py-pyproject-metadata=$ob/external/+py_pyproject_metadata_native+py_pyproject_metadata_native/prefix"
    "py-pythran=$ob/external/+py_pythran_native+py_pythran_native/prefix"
    "py-scikit-build-core=$ob/external/+py_scikit_build_core_native+py_scikit_build_core_native/prefix"
    "py-scipy=$ob/external/+py_scipy_native+py_scipy_native/prefix"
    "py-setuptools=$ob/external/+py_setuptools_native+py_setuptools_82_native/prefix"
    "py-setuptools-scm=$ob/external/+py_setuptools_scm_native+py_setuptools_scm_9_native/prefix"
    "py-trove-classifiers=$ob/external/+py_trove_classifiers_native+py_trove_classifiers_native/prefix"
    "py-wheel=$ob/external/+py_wheel_native+py_wheel_native/prefix"
  )
  jax_runtime_prefixes=("${jaxlib_deps_prefixes[@]}")
}

torch_graphs=(
  graphs/py_torch_214_full_cu130_build_graph.json
  graphs/py_torch_214_full_cu129_build_graph.json
  graphs/py_triton_pytorch_pin_cu130_build_graph.json
  graphs/py_triton_pytorch_pin_cu129_build_graph.json
)
jax_graphs=(
  graphs/py_jax_0102_cu130_build_graph.json
  graphs/py_jax_0102_cu129_build_graph.json
)
torch_workloads=(
  W1a
  W1b
  W1c
  W1d
  W2a
  W2b
  W2c
  W6a
  W6b
  W6c
  W6d
  W6e
  W6f
  W6g
  W6h
  W6i
  W6j
  W6k
  W6l
  W6m
  W6n
  W6o
  W6p
  W6q
  W6r
  W6s
  W6t
  W6u
  W6v
  W6w
)
jax_workloads=(
  W3a
  W3b
  W3c
)
jax_model_workloads=(
  W5a
  W5b
  W5c
  W5d
  W5e
  W5f
  W5g
)

ts="$(date -u +%Y%m%dT%H%M%SZ)"
out_dir="${out_dir:-/vaso/agents/trae/proofs/triumvirate-gate-$profile-$line-$ts}"
acceptance_out="${acceptance_out:-acceptance-$profile-$line.json}"
mkdir -p "$out_dir"
results_jsonl="$out_dir/sub-results.jsonl"
: > "$results_jsonl"
release_dir="$out_dir/release-dir"
release_workloads_run_id="triumvirate-release-workloads-$profile-$line-$ts"
release_gates_run_id="triumvirate-release-gates-$profile-$line-$ts"
release_workloads_dir="$estate_root/run/$release_workloads_run_id/workloads"
release_gates_dir="$estate_root/run/$release_gates_run_id/gates"
release_workloads_sb="/run/vaso/run/workloads"
release_gates_sb="/run/vaso/run/gates"
release_sbom_dir="$out_dir/sbom"
release_dpkg_list="$release_sbom_dir/dpkg-list.txt"
release_sbom_json="$release_sbom_dir/cyclonedx.json"
release_license_policy_json="$release_sbom_dir/license-policy.json"
release_acceptance_args=()
native_prefix_targets=()
release_wheel_targets=()
release_wheel_manifests=()
release_wheel_manifest_targets=()
release_runtime_wheel_manifests=()
release_runtime_wheel_manifest_targets=()
release_sbom_graphs=()
release_gate_workloads=()
release_gate_names=()
last_stage_rc=0
release_pipeline_failed=0

json_append_result() {
  local name="$1"; shift
  local stage="$1"; shift
  local rc="$1"; shift
  local stdout_path="$1"; shift
  local stderr_path="$1"; shift
  local started="$1"; shift
  local finished="$1"; shift
  local verdict="$1"; shift
  python3 - "$results_jsonl" "$name" "$stage" "$rc" "$stdout_path" "$stderr_path" "$started" "$finished" "$verdict" "$@" <<'PY'
import json
import sys

path, name, stage, rc, stdout, stderr, started, finished, verdict, *command = sys.argv[1:]
doc = {
    "name": name,
    "stage": stage,
    "command": command,
    "returncode": int(rc),
    "stdout": stdout,
    "stderr": stderr,
    "started_utc": started,
    "finished_utc": finished,
    "verdict": verdict,
}
with open(path, "a", encoding="utf-8") as fh:
    fh.write(json.dumps(doc, sort_keys=True) + "\n")
PY
}

run_stage() {
  local name="$1"; shift
  local stage="$1"; shift
  local stdout_path="$out_dir/$name.stdout.txt"
  local stderr_path="$out_dir/$name.stderr.txt"
  local started finished rc
  started="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  set +e
  "$@" >"$stdout_path" 2>"$stderr_path"
  rc="$?"
  set -e
  finished="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  last_stage_rc="$rc"
  json_append_result "$name" "$stage" "$rc" "$stdout_path" "$stderr_path" "$started" "$finished" "$([[ "$rc" == "0" ]] && echo passed || echo failed)" "$@"
}

skip_stage_upstream_failed() {
  local name="$1"; shift
  local stage="$1"; shift
  local stdout_path="$out_dir/$name.stdout.txt"
  local stderr_path="$out_dir/$name.stderr.txt"
  local started finished
  started="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  printf 'skipped (upstream failed)\n' >"$stdout_path"
  printf 'skipped (upstream failed)\n' >"$stderr_path"
  finished="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  last_stage_rc=1
  json_append_result "$name" "$stage" 1 "$stdout_path" "$stderr_path" "$started" "$finished" "skipped (upstream failed)" "$@"
}

run_release_stage() {
  local name="$1"; shift
  local stage="$1"; shift
  if [[ "$release_pipeline_failed" != "0" ]]; then
    skip_stage_upstream_failed "$name" "$stage" "$@"
    return 0
  fi
  run_stage "$name" "$stage" "$@"
  if [[ "$last_stage_rc" != "0" ]]; then
    release_pipeline_failed=1
  fi
}

join_csv() {
  local IFS=,
  echo "$*"
}

append_runtime_args() {
  local -n prefixes="$1"
  local -n out="$2"
  local item
  out=()
  for item in "${prefixes[@]}"; do
    out+=(--runtime-prefix "$item")
  done
}

RELEASE_MAX_JOBS_CAP=96
release_jobs() {
  local jobs="${VASO_HOST_CPU_JOBS:-}"
  if [[ ! "$jobs" =~ ^[1-9][0-9]*$ ]]; then
    jobs="$(nproc 2>/dev/null || echo 1)"
  fi
  if [[ ! "$jobs" =~ ^[1-9][0-9]*$ ]]; then
    jobs=1
  fi
  if (( jobs > RELEASE_MAX_JOBS_CAP )); then
    jobs="$RELEASE_MAX_JOBS_CAP"
  fi
  echo "$jobs"
}

release_max_jobs() {
  local env_name="$1"
  local requested="${!env_name:-}"
  if [[ -n "$requested" ]]; then
    if [[ ! "$requested" =~ ^[1-9][0-9]*$ ]]; then
      echo "$env_name must be a positive integer, got: $requested" >&2
      return 2
    fi
    if (( requested > RELEASE_MAX_JOBS_CAP )); then
      echo "$env_name must be <= $RELEASE_MAX_JOBS_CAP, got: $requested" >&2
      return 2
    fi
    echo "$requested"
    return 0
  fi
  release_jobs
}

add_release_tokens() {
  local -n out="$1"
  if [[ -n "${VASO_NATIVE_PYTORCH_TOKEN:-}" ]]; then
    out+=("--//native/pytorch:token=$VASO_NATIVE_PYTORCH_TOKEN")
  fi
  if [[ -n "${VASO_NATIVE_TORCHVISION_TOKEN:-}" ]]; then
    out+=("--//native/torchvision:token=$VASO_NATIVE_TORCHVISION_TOKEN")
  fi
  if [[ -n "${VASO_NATIVE_TORCHAUDIO_TOKEN:-}" ]]; then
    out+=("--//native/torchaudio:token=$VASO_NATIVE_TORCHAUDIO_TOKEN")
  fi
  if [[ -n "${VASO_NATIVE_TRITON_TOKEN:-}" ]]; then
    out+=("--//native/triton:token=$VASO_NATIVE_TRITON_TOKEN")
  fi
  local jaxlib_token="${VASO_NATIVE_JAXLIB_TOKEN:-${VASO_NATIVE_LLVM_TOKEN:-}}"
  if [[ -n "$jaxlib_token" ]]; then
    out+=("--//native/jaxlib:token=$jaxlib_token")
  fi
}

write_native_job_state() {
  local pytorch_jobs jaxlib_jobs
  pytorch_jobs="$(release_max_jobs VASO_PYTORCH_MAX_JOBS)"
  jaxlib_jobs="$(release_max_jobs VASO_JAXLIB_MAX_JOBS)"
  mkdir -p "/vaso/lines/$line/state/native"
  printf '%s\n' "$pytorch_jobs" > "/vaso/lines/$line/state/native/pytorch_jobs.txt"
  printf '%s\n' "$jaxlib_jobs" > "/vaso/lines/$line/state/native/jaxlib_jobs.txt"
}

require_token() {
  local env_name="$1"
  local expected="$2"
  local actual="${!env_name:-}"
  if [[ "$actual" != "$expected" ]]; then
    echo "$env_name must be '$expected' for --release profile=$profile line=$line" >&2
    return 2
  fi
}

require_release_tokens() {
  if [[ "$release_mode" != "1" ]]; then
    return 0
  fi

  case "$profile" in
    torch)
      require_token VASO_NATIVE_PYTORCH_TOKEN "build-native-pytorch"
      require_token VASO_NATIVE_TORCHVISION_TOKEN "build-native-torchvision"
      require_token VASO_NATIVE_TORCHAUDIO_TOKEN "build-native-torchaudio"
      require_token VASO_NATIVE_TRITON_TOKEN "build-native-triton"
      ;;
    jax)
      local jaxlib_token="${VASO_NATIVE_JAXLIB_TOKEN:-${VASO_NATIVE_LLVM_TOKEN:-}}"
      require_token jaxlib_token "build-native-llvm"
      ;;
  esac
}

bazel_executable() {
  local bazel_exe="${VASO_BAZEL_EXE:-/vaso/tools/bin/bazel}"
  if [[ ! -x "$bazel_exe" ]]; then
    bazel_exe="bazel"
  fi
  printf '%s\n' "$bazel_exe"
}

bazel_profile_command_args() {
  local -n out_args="$1"
  local bazel_command="$2"
  local jobs pytorch_jobs jaxlib_jobs torchvision_jobs torchaudio_jobs
  jobs="$(release_jobs)"
  pytorch_jobs="$(release_max_jobs VASO_PYTORCH_MAX_JOBS)"
  jaxlib_jobs="$(release_max_jobs VASO_JAXLIB_MAX_JOBS)"
  torchvision_jobs="$(release_max_jobs VASO_TORCHVISION_MAX_JOBS)"
  torchaudio_jobs="$(release_max_jobs VASO_TORCHAUDIO_MAX_JOBS)"
  out_args=(
    "--output_base=$release_bazel_ob"
    "--host_jvm_args=-Djava.io.tmpdir=${TMPDIR:-/vaso/tmp}"
    --batch
    "$bazel_command"
    --repo_env=VASO_IN_INSULA=1
    "--repo_env=TMPDIR=/vaso/tmp/bazel-$line"
    "--repo_env=VASO_CUDA_HOME=${VASO_CUDA_HOME:-/usr/local/cuda}"
    "--repo_env=VASO_CUDA_LINE=$line"
    "--repo_env=VASO_ROOTFS_BUNDLE_MANIFEST=$VASO_ROOTFS_BUNDLE_MANIFEST"
    "--repo_env=VASO_BAZEL=${VASO_BAZEL:-/opt/vaso/bazel-7.7.0/bin/bazel-real}"
    "--repo_env=SOURCE_DATE_EPOCH=$(native_repo_source_date_epoch)"
    --action_env=VASO_IN_INSULA=1
    "--action_env=TMPDIR=/vaso/tmp/bazel-$line"
    "--action_env=VASO_CUDA_HOME=${VASO_CUDA_HOME:-/usr/local/cuda}"
    "--action_env=VASO_CUDA_LINE=$line"
    --action_env=VASO_HOME=/vaso
    "--action_env=VASO_BAZEL_OB=$release_bazel_ob"
    "--action_env=VASO_JAXLIB_MAX_JOBS=$jaxlib_jobs"
    "--action_env=VASO_TORCHVISION_MAX_JOBS=$torchvision_jobs"
    "--action_env=VASO_TORCHAUDIO_MAX_JOBS=$torchaudio_jobs"
    "--action_env=VASO_HOST_CPU_JOBS=${VASO_HOST_CPU_JOBS:-}"
    "--action_env=CUDA_VISIBLE_DEVICES=${CUDA_VISIBLE_DEVICES:-}"
    "--action_env=VASO_GPU_SET=${VASO_GPU_SET:-}"
    "--action_env=VASO_ROOTFS_BUNDLE_MANIFEST=$VASO_ROOTFS_BUNDLE_MANIFEST"
    --registry=file:///vaso/sources/bcr/registry/current
    --repository_disable_download
    --lockfile_mode=off
    --distdir=/opt/vaso/distdir
    --distdir=/vaso/sources/bazel/distdir
    --distdir=/vaso/sources/native/distdir
    --distdir=/vaso/sources/triton/distdir
    --distdir=/vaso/sources/jax/distdir
    --distdir=/vaso/sources/pytorch/distdir
    --repository_cache=/vaso/cache/bazel/repository-cache
    "--disk_cache=/vaso/lines/$line/cache/bazel/disk-cache"
    "--local_resources=cpu=$jobs"
    --spawn_strategy=local
    --curses=no
    --color=no
  )
  append_native_bazel_disk_cache_gc_args out_args
  append_native_determinism_bazel_args out_args "$line" "$REPO_ROOT" "$commit" "$release_bazel_ob" "/vaso/lines/$line/work" "$source_date_epoch"
  add_release_tokens out_args
  # Constant per line (lease-exclusive): a per-run TMPDIR would change native keys every run.
  mkdir -p "/vaso/tmp/bazel-$line"
}

bazel_profile_build_stage() {
  local target_array_name="$1"
  local -n build_targets="$target_array_name"
  if [[ "${#build_targets[@]}" -eq 0 ]]; then
    echo "no Bazel targets configured for $target_array_name" >&2
    return 2
  fi

  local bazel_exe
  bazel_exe="$(bazel_executable)"
  local args=()
  bazel_profile_command_args args build
  write_native_job_state || return
  "$bazel_exe" "${args[@]}" "${build_targets[@]}"
}

bazel_cquery_single_file() {
  local target="$1"
  local bazel_exe
  bazel_exe="$(bazel_executable)"
  local args=()
  bazel_profile_command_args args cquery
  args+=(--nofetch --repository_disable_download --output=files)

  local query_output
  if query_output="$("$bazel_exe" "${args[@]}" "$target")"; then
    :
  else
    return $?
  fi
  local files=()
  mapfile -t files <<<"$query_output"
  local nonempty=()
  local file
  for file in "${files[@]}"; do
    [[ -n "$file" ]] || continue
    nonempty+=("$file")
  done
  if [[ "${#nonempty[@]}" -ne 1 ]]; then
    echo "expected exactly one cquery file for $target, got ${#nonempty[@]}" >&2
    printf '  %s\n' "${nonempty[@]}" >&2
    return 2
  fi
  file="${nonempty[0]}"
  if [[ "$file" == /* ]]; then
    printf '%s\n' "$file"
  else
    printf '%s\n' "$release_bazel_ob/execroot/_main/$file"
  fi
}

resolve_default_action_prefixes() {
  if [[ "$profile" == "torch" ]]; then
    if [[ "$torch_prefix_overridden" == "0" ]]; then
      torch_prefix="$(bazel_cquery_single_file //tools:pytorch_action_prefix)" || return
    fi
    if [[ "$torchvision_prefix_overridden" == "0" ]]; then
      torchvision_prefix="$(bazel_cquery_single_file //tools:torchvision_action_prefix)" || return
    fi
    if [[ "$torchaudio_prefix_overridden" == "0" ]]; then
      torchaudio_prefix="$(bazel_cquery_single_file //tools:torchaudio_action_prefix)" || return
    fi
    if [[ "$triton_prefix_overridden" == "0" ]]; then
      triton_prefix="$(bazel_cquery_single_file //tools:triton_action_prefix)" || return
    fi
    if [[ "$torch_ref_prefix_overridden" == "0" ]]; then
      torch_ref_prefix="$torch_prefix"
    fi
  else
    if [[ "$jaxlib_prefix_overridden" == "0" ]]; then
      jaxlib_prefix="$(bazel_cquery_single_file //tools:jaxlib_action_prefix)" || return
    fi
  fi
}

assert_named_cquery_files() {
  local target_array_name="$1"
  local path_array_name="$2"
  local -n targets="$target_array_name"
  local -n paths="$path_array_name"
  local target_item path_item name target expected actual found
  for target_item in "${targets[@]}"; do
    name="${target_item%%=*}"
    target="${target_item#*=}"
    found=0
    expected=""
    for path_item in "${paths[@]}"; do
      if [[ "${path_item%%=*}" == "$name" ]]; then
        expected="${path_item#*=}"
        found=1
        break
      fi
    done
    if [[ "$found" != "1" ]]; then
      echo "missing expected path for cquery target $target_item" >&2
      return 2
    fi
    actual="$(bazel_cquery_single_file "$target")" || return
    if [[ "$actual" != "$expected" ]]; then
      echo "cquery path drift for $name: expected $expected, got $actual" >&2
      return 2
    fi
  done
}

bazel_native_prefix_build_stage() {
  bazel_profile_build_stage native_prefix_targets
}

bazel_release_build_stage() {
  bazel_profile_build_stage release_wheel_targets
}

clear_release_wheel_outputs() {
  local item manifest wheelhouse
  for item in "${release_wheel_manifests[@]}" "${release_runtime_wheel_manifests[@]}"; do
    manifest="${item#*=}"
    if [[ "$manifest" != "$release_bazel_bin/"* ]]; then
      echo "refusing to clear release output outside bazel-bin: $manifest" >&2
      return 2
    fi
    if [[ "$manifest" != *_manifest.json ]]; then
      echo "cannot infer wheelhouse for release manifest: $manifest" >&2
      return 2
    fi
    wheelhouse="${manifest%_manifest.json}_wheelhouse"
    rm -rf "$manifest" "$wheelhouse"
    mkdir -p "$(dirname "$manifest")" "$(dirname "$wheelhouse")"
  done
}

release_wheels_stage() {
  clear_release_wheel_outputs || return $?
  bazel_release_build_stage || return $?
  assert_named_cquery_files release_wheel_manifest_targets release_wheel_manifests || return $?
  assert_named_cquery_files release_runtime_wheel_manifest_targets release_runtime_wheel_manifests || return $?
  python3 tools/release_wheel_identity.py \
    --commit "$commit" \
    --require-manifest-wheelhouse \
    "${release_wheel_manifests[@]}"
}

release_manifest_wheels() {
  python3 - "${release_wheel_manifests[@]}" <<'PY'
import json
import sys
from pathlib import Path

for item in sys.argv[1:]:
    package, _, raw_path = item.partition("=")
    path = Path(raw_path)
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise SystemExit(f"missing release wheel manifest for {package}: {path}")
    except json.JSONDecodeError as exc:
        raise SystemExit(f"invalid release wheel manifest for {package}: {path}: {exc}")
    wheels = doc.get("wheels", [])
    if not isinstance(wheels, list) or not wheels:
        raise SystemExit(f"release wheel manifest for {package} contains no wheels: {path}")
    for wheel in wheels:
        if not isinstance(wheel, dict) or not wheel.get("path"):
            raise SystemExit(f"invalid release wheel entry for {package}: {path}")
        print(wheel["path"])
PY
}

release_relocatability_stage() {
  local wheels=()
  mapfile -t wheels < <(release_manifest_wheels)
  if [[ "${#wheels[@]}" -eq 0 ]]; then
    echo "release wheel manifests produced no wheels" >&2
    return 2
  fi
  python3 tools/relocatability_guard.py \
    --line "$line" \
    --lock rootfs/cuda_ecosystem.lock.json \
    "${wheels[@]}"
}

release_cubin_guard_stage() {
  local wheels=()
  mapfile -t wheels < <(release_manifest_wheels)
  if [[ "${#wheels[@]}" -eq 0 ]]; then
    echo "release wheel manifests produced no wheels" >&2
    return 2
  fi
  local cuda_prefix_file="$release_bazel_ob/external/+cuda_native+cuda_native/prefix_path.txt"
  if [[ ! -f "$cuda_prefix_file" ]]; then
    echo "missing CUDA provider prefix file for cubin guard: $cuda_prefix_file" >&2
    return 2
  fi
  python3 tools/cubin_guard.py \
    --cuda-prefix-file "$cuda_prefix_file" \
    "${wheels[@]}"
}

release_sbom_stage() {
  mkdir -p "$release_sbom_dir"
  dpkg -l > "$release_dpkg_list"
  local args=(
    --profile "$profile"
    --line "$line"
    --spack-lock "locks/$profile.lock.json"
    --rootfs-lock rootfs/cuda_ecosystem.lock.json
    --dpkg-list "$release_dpkg_list"
    --license-policy tools/license_policy.json
    --policy-out "$release_license_policy_json"
    --out "$release_sbom_json"
  )
  local graph
  for graph in "${release_sbom_graphs[@]}"; do
    args+=(--build-graph "$graph")
  done
  python3 tools/sbom.py "${args[@]}"
}

release_dir_stage() {
  local args=(
    --release-dir "$release_dir"
    --profile "$profile"
    --line "$line"
    --commit "$commit"
    --sbom "$release_sbom_json"
    --license-policy "$release_license_policy_json"
  )
  local item
  for item in "${release_wheel_manifests[@]}"; do
    args+=(--wheel-manifest "$item")
  done
  for item in "${release_runtime_wheel_manifests[@]}"; do
    args+=(--runtime-wheel-manifest "$item")
  done
  python3 tools/release_dir.py "${args[@]}"
}

export CC="/usr/bin/gcc"
export CXX="/usr/bin/g++"

run_stage \
  "rootfs_static" \
  "rootfs" \
  python3 rootfs/verify_cuda_ecosystem.py / \
    --lock rootfs/cuda_ecosystem.lock.json \
    --line "$line"

run_stage \
  "rootfs_gpu" \
  "rootfs" \
  python3 rootfs/verify_cuda_ecosystem.py / \
    --lock rootfs/cuda_ecosystem.lock.json \
    --line "$line" \
    --gpu

case "$profile" in
  torch)
    selected_graphs=("${torch_graphs[@]}")
    ;;
  jax)
    selected_graphs=("${jax_graphs[@]}")
    ;;
esac

run_stage "graph_guard_python_llvm" "graph_guards" python3 tools/one_llvm_graph_guard.py "${selected_graphs[@]}"
run_stage "graph_guard_single_copy_cuda" "graph_guards" python3 tools/single_copy_cuda_graph_guard.py "${selected_graphs[@]}"
run_stage "profile_guard" "graph_guards" python3 tools/profile_guard.py --profile "$profile" "${selected_graphs[@]}"
compiler_pathway_args=(
  python3 tools/compiler_pathway_guard.py
  --policy tools/compiler_pathways.json
  --profile "$profile"
)
run_stage "compiler_pathway_guard" "compiler_pathway" "${compiler_pathway_args[@]}"

if [[ "$profile" == "torch" ]]; then
  torch_runtime_args=()
  jaxlib_deps_runtime_args=()
  release_wheel_targets=(
    //native/pytorch:pytorch_release_wheel
    //native/torchvision:torchvision_release_wheel
    //native/torchaudio:torchaudio_release_wheel
    //native/triton:triton_release_wheel
    //native/common:torch_runtime_release_wheelhouse
  )
  native_prefix_targets=(
    @python_313_native//:prefix
    @py_numpy_native//:prefix
    @py_typing_extensions_native//:prefix
    @py_sympy_native//:prefix
    @py_mpmath_native//:prefix
    @py_filelock_native//:prefix
    @py_networkx_native//:prefix
    @py_jinja2_native//:prefix
    @py_markupsafe_native//:prefix
    @py_fsspec_native//:prefix
    @py_lit_native//:prefix
    @py_pillow_native//:prefix
    @libjpeg_turbo_native//:prefix
    @libpng_native//:prefix
    @py_absl_py_native//:prefix
    @py_beniget_native//:prefix
    @py_build_native//:prefix
    @py_calver_native//:prefix
    @py_cython_native//:prefix
    @py_flit_core_native//:prefix
    @py_gast_native//:prefix
    @py_hatch_fancy_pypi_readme_native//:prefix
    @py_hatch_vcs_native//:prefix
    @py_hatchling_native//:prefix
    @py_meson_python_native//:prefix
    @py_ml_dtypes_native//:prefix
    @py_opt_einsum_native//:prefix
    @py_packaging_native//:prefix
    @py_pathspec_native//:prefix
    @py_pip_native//:prefix
    @py_pluggy_native//:prefix
    @py_ply_native//:prefix
    @py_pybind11_native//:prefix
    @py_pyproject_hooks_native//:prefix
    @py_pyproject_metadata_native//:prefix
    @py_pythran_native//:prefix
    @py_scikit_build_core_native//:prefix
    @py_scipy_native//:prefix
    @py_setuptools_native//:prefix
    @py_setuptools_82_native//:prefix
    @py_setuptools_scm_9_native//:prefix
    @py_trove_classifiers_native//:prefix
    @py_wheel_native//:prefix
    //native/pytorch:pytorch_action
    //native/torchvision:torchvision_action
    //native/torchaudio:torchaudio_action
    //native/triton:triton_action
  )
  release_wheel_manifests=(
    "pytorch=$release_bazel_bin/native/pytorch/pytorch_release_wheel_manifest.json"
    "torchvision=$release_bazel_bin/native/torchvision/torchvision_release_wheel_manifest.json"
    "torchaudio=$release_bazel_bin/native/torchaudio/torchaudio_release_wheel_manifest.json"
    "triton=$release_bazel_bin/native/triton/triton_release_wheel_manifest.json"
  )
  release_wheel_manifest_targets=(
    "pytorch=//tools:pytorch_release_wheel_manifest"
    "torchvision=//tools:torchvision_release_wheel_manifest"
    "torchaudio=//tools:torchaudio_release_wheel_manifest"
    "triton=//tools:triton_release_wheel_manifest"
  )
  release_runtime_wheel_manifests=(
    "torch-runtime-deps=$release_bazel_bin/native/common/torch_runtime_release_wheelhouse_manifest.json"
  )
  release_runtime_wheel_manifest_targets=(
    "torch-runtime-deps=//tools:torch_runtime_release_wheelhouse_manifest"
  )
  release_sbom_graphs=("${torch_graphs[@]}")
  release_gate_workloads=("${torch_workloads[@]}")
  release_gate_names=(
    nccl_distributed
    gloo_distributed
    cudnn_conv
    torch_compile
    sdpa_flash
    torchvision_ops
    torchaudio_ops
  )

  if [[ "$release_mode" == "1" ]]; then
    require_release_tokens
    run_stage "native_prefixes" "native_prefixes" bazel_native_prefix_build_stage
    if [[ "$last_stage_rc" != "0" ]]; then
      release_pipeline_failed=1
    fi
  fi
  run_stage "native_prefix_paths" "native_prefixes" resolve_default_action_prefixes
  if [[ "$last_stage_rc" != "0" ]]; then
    release_pipeline_failed=1
  fi
  configure_default_runtime_prefixes
  append_runtime_args torch_runtime_prefixes torch_runtime_args
  append_runtime_args jaxlib_deps_prefixes jaxlib_deps_runtime_args

  run_stage \
    "compiler_provenance_torch" \
    "compiler_provenance" \
    python3 tools/compiler_provenance.py "$torch_prefix" \
      --consumer torch \
      --profile torch \
      --policy tools/compiler_pathways.json

  run_stage \
    "compiler_provenance_torchvision" \
    "compiler_provenance" \
    python3 tools/compiler_provenance.py "$torchvision_prefix" \
      --consumer torchvision \
      --profile torch \
      --policy tools/compiler_pathways.json

  run_stage \
    "compiler_provenance_torchaudio" \
    "compiler_provenance" \
    python3 tools/compiler_provenance.py "$torchaudio_prefix" \
      --consumer torchaudio \
      --profile torch \
      --policy tools/compiler_pathways.json

  run_stage \
    "compiler_provenance_triton" \
    "compiler_provenance" \
    python3 tools/compiler_provenance.py "$triton_prefix" \
      --consumer triton \
      --profile torch \
      --policy tools/compiler_pathways.json

  run_stage \
    "runtime_compilers" \
    "runtime_compilers" \
    "$python_prefix/bin/python3" tools/runtime_compiler_assertions.py \
      --python-prefix "$python_prefix" \
      --torch-prefix "$torch_prefix" \
      --triton-prefix "$triton_prefix" \
      "${torch_runtime_args[@]}" \
      --out-dir "$out_dir/runtime-compilers" \
      --json-out "$out_dir/runtime-compilers/result.json"

  run_stage \
    "abi_invariants_static" \
    "abi_invariants" \
    python3 tools/abi_invariants.py \
      --profile torch \
      --line "$line" \
      --prefix "torch=$torch_prefix" \
      --prefix "torchvision=$torchvision_prefix" \
      --prefix "torchaudio=$torchaudio_prefix" \
      --prefix "triton=$triton_prefix" \
      --llvm-prefix /usr/lib/llvm-23 \
      --json-out "$out_dir/abi-invariants-static.json"

  run_stage \
    "abi_invariants_probe" \
    "abi_invariants" \
    "$python_prefix/bin/python3" tools/abi_invariants.py \
      --profile torch \
      --probe \
      --line "$line" \
      --python-prefix "$python_prefix" \
      --prefix "torch=$torch_prefix" \
      --prefix "torchvision=$torchvision_prefix" \
      --prefix "torchaudio=$torchaudio_prefix" \
      --prefix "triton=$triton_prefix" \
      "${torch_runtime_args[@]}" \
      --out-dir "$out_dir/abi-invariants-probe" \
      --json-out "$out_dir/abi-invariants-probe/result.json"

  run_stage \
    "np13_gates" \
    "np13_gates" \
    "$python_prefix/bin/python3" native/pytorch/gates.py \
      --torch-prefix "$torch_prefix" \
      --torch-ref-prefix "$torch_ref_prefix" \
      --python-prefix "$python_prefix" \
      "${torch_runtime_args[@]}" \
      --out-dir "$out_dir/np13-gates" \
      --results-out "$out_dir/np13-gates/results.json" \
      --only "import_cuda,feature_parity,b200_matmul,cudnn_conv,nccl_distributed,gloo_distributed,torch_compile,sdpa_flash,torchvision_ops,torchaudio_ops,collect_env" \
      --block-group core \
      --block-group compile \
      --block-group vision_audio \
      --execute

  run_stage \
    "jaxlib_deps_import" \
    "jaxlib_deps" \
    "$python_prefix/bin/python3" tools/jaxlib_deps_import_check.py \
      --python-prefix "$python_prefix" \
      "${jaxlib_deps_runtime_args[@]}" \
      --json-out "$out_dir/jaxlib-deps-import.json"

  run_stage \
    "workloads" \
    "workloads" \
    "$python_prefix/bin/python3" -m workloads.run_workloads \
      --line "$line" \
      --gpus 8 \
      --out "$out_dir/workloads" \
      --python-prefix "$python_prefix" \
      --torch-prefix "$torch_prefix" \
      --triton-prefix "$triton_prefix" \
      "${torch_runtime_args[@]}" \
      --only "$(join_csv "${torch_workloads[@]}")"

  acceptance_prefix_args=(
    --prefix "python=$python_prefix"
    --prefix "torch=$torch_prefix"
    --prefix "torchvision=$torchvision_prefix"
    --prefix "torchaudio=$torchaudio_prefix"
    --prefix "triton=$triton_prefix"
  )
  for item in "${jaxlib_deps_prefixes[@]}"; do
    acceptance_prefix_args+=(--prefix "$item")
  done
else
  jax_runtime_args=()
  release_wheel_targets=(
    //native/jaxlib:jaxlib_release_wheels
    //native/common:jax_runtime_release_wheelhouse
  )
  native_prefix_targets=(
    @python_313_native//:prefix
    @py_jax_native//:prefix
    @py_absl_py_native//:prefix
    @py_beniget_native//:prefix
    @py_build_native//:prefix
    @py_calver_native//:prefix
    @py_cython_native//:prefix
    @py_flit_core_native//:prefix
    @py_gast_native//:prefix
    @py_hatch_fancy_pypi_readme_native//:prefix
    @py_hatch_vcs_native//:prefix
    @py_hatchling_native//:prefix
    @py_meson_python_native//:prefix
    @py_ml_dtypes_native//:prefix
    @py_numpy_native//:prefix
    @py_opt_einsum_native//:prefix
    @py_packaging_native//:prefix
    @py_pathspec_native//:prefix
    @py_pip_native//:prefix
    @py_pluggy_native//:prefix
    @py_ply_native//:prefix
    @py_pybind11_native//:prefix
    @py_pyproject_hooks_native//:prefix
    @py_pyproject_metadata_native//:prefix
    @py_pythran_native//:prefix
    @py_scikit_build_core_native//:prefix
    @py_scipy_native//:prefix
    @py_setuptools_82_native//:prefix
    @py_setuptools_scm_9_native//:prefix
    @py_trove_classifiers_native//:prefix
    @py_wheel_native//:prefix
    //native/jaxlib:jaxlib_action
  )
  release_wheel_manifests=(
    "jaxlib=$release_bazel_bin/native/jaxlib/jaxlib_release_wheels_manifest.json"
  )
  release_wheel_manifest_targets=(
    "jaxlib=//tools:jaxlib_release_wheels_manifest"
  )
  release_runtime_wheel_manifests=(
    "jax-runtime-deps=$release_bazel_bin/native/common/jax_runtime_release_wheelhouse_manifest.json"
  )
  release_runtime_wheel_manifest_targets=(
    "jax-runtime-deps=//tools:jax_runtime_release_wheelhouse_manifest"
  )
  release_sbom_graphs=("${jax_graphs[@]}")
  release_gate_workloads=("${jax_workloads[@]}" "${jax_model_workloads[@]}")
  release_gate_names=(jax_psum_2gpu)

  if [[ "$release_mode" == "1" ]]; then
    require_release_tokens
    run_stage "native_prefixes" "native_prefixes" bazel_native_prefix_build_stage
    if [[ "$last_stage_rc" != "0" ]]; then
      release_pipeline_failed=1
    fi
  fi
  run_stage "native_prefix_paths" "native_prefixes" resolve_default_action_prefixes
  if [[ "$last_stage_rc" != "0" ]]; then
    release_pipeline_failed=1
  fi
  configure_default_runtime_prefixes
  append_runtime_args jax_runtime_prefixes jax_runtime_args

  run_stage \
    "compiler_provenance_jaxlib" \
    "compiler_provenance" \
    python3 tools/compiler_provenance.py "$jaxlib_prefix" \
      --consumer jaxlib \
      --profile jax \
      --policy tools/compiler_pathways.json

  run_stage \
    "runtime_compilers" \
    "runtime_compilers" \
    "$python_prefix/bin/python3" tools/runtime_compiler_assertions.py \
      --profile jax \
      --python-prefix "$python_prefix" \
      --jax-prefix "$jax_prefix" \
      --jaxlib-prefix "$jaxlib_prefix" \
      "${jax_runtime_args[@]}" \
      --out-dir "$out_dir/runtime-compilers" \
      --json-out "$out_dir/runtime-compilers/result.json"

  run_stage \
    "abi_invariants_static" \
    "abi_invariants" \
    python3 tools/abi_invariants.py \
      --profile jax \
      --line "$line" \
      --prefix "jaxlib=$jaxlib_prefix" \
      --json-out "$out_dir/abi-invariants-static.json"

  run_stage \
    "abi_invariants_probe" \
    "abi_invariants" \
    "$python_prefix/bin/python3" tools/abi_invariants.py \
      --profile jax \
      --probe \
      --line "$line" \
      --python-prefix "$python_prefix" \
      --prefix "jax=$jax_prefix" \
      --prefix "jaxlib=$jaxlib_prefix" \
      "${jax_runtime_args[@]}" \
      --out-dir "$out_dir/abi-invariants-probe" \
      --json-out "$out_dir/abi-invariants-probe/result.json"

  run_stage \
    "jax_gates" \
    "jax_gates" \
    "$python_prefix/bin/python3" native/pytorch/gates.py \
      --python-prefix "$python_prefix" \
      --jax-prefix "$jax_prefix" \
      --jaxlib-prefix "$jaxlib_prefix" \
      "${jax_runtime_args[@]}" \
      --out-dir "$out_dir/jax-gates" \
      --results-out "$out_dir/jax-gates/results.json" \
      --only "jax_import,jax_matmul,jax_psum_2gpu" \
      --block-group jax \
      --execute

  run_stage \
    "workloads" \
    "workloads" \
    "$python_prefix/bin/python3" -m workloads.run_workloads \
      --line "$line" \
      --gpus 8 \
      --out "$out_dir/workloads" \
      --python-prefix "$python_prefix" \
      --jax-prefix "$jax_prefix" \
      --jaxlib-prefix "$jaxlib_prefix" \
      "${jax_runtime_args[@]}" \
      --only "$(join_csv "${jax_workloads[@]}")"

  run_stage \
    "jax_model_workloads" \
    "jax_model_workloads" \
    "$python_prefix/bin/python3" -m workloads.run_workloads \
      --line "$line" \
      --gpus 8 \
      --out "$out_dir/jax-model-workloads" \
      --python-prefix "$python_prefix" \
      --jax-prefix "$jax_prefix" \
      --jaxlib-prefix "$jaxlib_prefix" \
      "${jax_runtime_args[@]}" \
      --only "$(join_csv "${jax_model_workloads[@]}")"

  acceptance_prefix_args=(
    --prefix "python=$python_prefix"
    --prefix "jax=$jax_prefix"
    --prefix "jaxlib=$jaxlib_prefix"
  )
  for item in "${jax_runtime_prefixes[@]}"; do
    acceptance_prefix_args+=(--prefix "$item")
  done
fi

if [[ "$release_mode" == "1" ]]; then
  release_runner="${VASO_RELEASE_INSULA_SH:-/workspace/repo/foundry/insula.sh}"
  if [[ ! -x "$release_runner" && -x ../foundry/insula.sh ]]; then
    release_runner="../foundry/insula.sh"
  fi
  if [[ "$release_pipeline_failed" == "0" && ! -x "$release_runner" ]]; then
    echo "missing release insula runner: $release_runner" >&2
    exit 2
  fi
  run_release_stage "release_wheels" "release_wheels" release_wheels_stage
  run_release_stage "cubin_guard" "cubin_guard" release_cubin_guard_stage
  run_release_stage "relocatability" "relocatability" release_relocatability_stage
  run_release_stage "sbom" "sbom" release_sbom_stage
  run_release_stage "release_dir" "release_dir" release_dir_stage
  run_release_stage \
    "release_workloads" \
    "release_workloads" \
    env "INSULA_RELEASE_RUN_ID=$release_workloads_run_id" \
    "INSULA_RELEASE_PYTHON_PREFIX_HOST=$release_python_prefix" \
    "$release_runner" run \
      --line "$line" \
      --release "$release_dir" \
      --gpus 8 \
      -- \
      python3 -m workloads.run_workloads \
        --from-release \
        --line "$line" \
        --gpus 8 \
        --out "$release_workloads_sb" \
        --only "$(join_csv "${release_gate_workloads[@]}")"
  if [[ "${#release_gate_names[@]}" -gt 0 ]]; then
    release_gate_args=(
      --from-release
      --out-dir "$release_gates_sb"
      --results-out "$release_gates_sb/results.json"
      --only "$(join_csv "${release_gate_names[@]}")"
      --execute
    )
    if [[ "$profile" == "torch" ]]; then
      release_gate_args+=(--block-group core --block-group compile --block-group vision_audio)
    else
      release_gate_args+=(--block-group jax)
    fi
    run_release_stage \
      "release_gates" \
      "release_gates" \
      env "INSULA_RELEASE_RUN_ID=$release_gates_run_id" \
      "INSULA_RELEASE_PYTHON_PREFIX_HOST=$release_python_prefix" \
      "$release_runner" run \
        --line "$line" \
        --release "$release_dir" \
        --gpus 8 \
        -- \
        python3 native/pytorch/gates.py "${release_gate_args[@]}"
  fi
  consumer_prefix_env=("VASO_PYTHON_PREFIX=$python_prefix")
  if [[ "$profile" == "torch" ]]; then
    consumer_prefix_env+=("VASO_TORCH_PREFIX=$torch_prefix")
  fi
  run_release_stage \
    "consumer_conformance" \
    "consumer_conformance" \
    env VASO_PROFILE="$profile" "${consumer_prefix_env[@]}" "INSULA_RELEASE_PYTHON_PREFIX_HOST=$release_python_prefix" "$EXPERIMENT_ROOT/examples/consumers/run.sh" \
      --line "$line" \
      --release-dir "$release_dir"

  if [[ "$release_pipeline_failed" == "0" ]]; then
    for item in "${release_wheel_manifests[@]}"; do
      release_acceptance_args+=(--release-wheel-manifest "$item")
    done
    release_acceptance_args+=(
      --release-workload-summary "release_workloads=$release_workloads_dir/summary.json"
      --release-gate-results "release_gates=$release_gates_dir/results.json"
      --release-sbom "$release_sbom_json"
      --release-license-policy "$release_license_policy_json"
    )
  fi
fi

python3 tools/triumvirate_acceptance.py \
  --out "$acceptance_out" \
  --profile "$profile" \
  --line "$line" \
  --commit "$commit" \
  --run-dir "$out_dir" \
  --rootfs-manifest "$VASO_ROOTFS_BUNDLE_MANIFEST" \
  --lock rootfs/cuda_ecosystem.lock.json \
  --waivers "$waivers" \
  --sub-results-jsonl "$results_jsonl" \
  "${acceptance_prefix_args[@]}" \
  "${release_acceptance_args[@]}"
