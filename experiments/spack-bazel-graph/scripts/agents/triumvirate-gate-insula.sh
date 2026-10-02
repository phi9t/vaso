#!/usr/bin/env bash
# Insula-side implementation of the triumvirate acceptance gate.
set -euo pipefail

line="${VASO_CUDA_LINE:-}"
profile="${VASO_PROFILE:-}"
commit=""
out_dir=""
acceptance_out=""
waivers="acceptance-waivers.json"
python_prefix=""
torch_prefix=""
torch_ref_prefix=""
torchvision_prefix=""
torchaudio_prefix=""
triton_prefix=""
jax_prefix=""
jaxlib_prefix=""
runtime_prefixes=()

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
    --python-prefix)
      [[ "$#" -ge 2 ]] || { echo "--python-prefix requires a value" >&2; exit 2; }
      python_prefix="$2"
      shift 2
      ;;
    --torch-prefix)
      [[ "$#" -ge 2 ]] || { echo "--torch-prefix requires a value" >&2; exit 2; }
      torch_prefix="$2"
      shift 2
      ;;
    --torch-ref-prefix)
      [[ "$#" -ge 2 ]] || { echo "--torch-ref-prefix requires a value" >&2; exit 2; }
      torch_ref_prefix="$2"
      shift 2
      ;;
    --torchvision-prefix|--vision-prefix)
      [[ "$#" -ge 2 ]] || { echo "$1 requires a value" >&2; exit 2; }
      torchvision_prefix="$2"
      shift 2
      ;;
    --torchaudio-prefix|--audio-prefix)
      [[ "$#" -ge 2 ]] || { echo "$1 requires a value" >&2; exit 2; }
      torchaudio_prefix="$2"
      shift 2
      ;;
    --triton-prefix)
      [[ "$#" -ge 2 ]] || { echo "--triton-prefix requires a value" >&2; exit 2; }
      triton_prefix="$2"
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

ob="/vaso/lines/$line/cache/bazel/output-base"
python_prefix="${python_prefix:-$ob/external/+python_native+python_313_native/prefix}"
torch_prefix="${torch_prefix:-$ob/execroot/_main/bazel-out/k8-fastbuild/bin/native/pytorch/pytorch_action_prefix}"
torch_ref_prefix="${torch_ref_prefix:-$torch_prefix}"
torchvision_prefix="${torchvision_prefix:-$ob/execroot/_main/bazel-out/k8-fastbuild/bin/native/torchvision/torchvision_action_prefix}"
torchaudio_prefix="${torchaudio_prefix:-$ob/execroot/_main/bazel-out/k8-fastbuild/bin/native/torchaudio/torchaudio_action_prefix}"
triton_prefix="${triton_prefix:-$ob/execroot/_main/bazel-out/k8-fastbuild/bin/native/triton/triton_action_prefix}"
jax_prefix="${jax_prefix:-$ob/external/+py_jax_native+py_jax_native/prefix}"
jaxlib_prefix="${jaxlib_prefix:-$ob/execroot/_main/bazel-out/k8-fastbuild/bin/native/jaxlib/jaxlib_action_prefix}"

torch_runtime_prefixes=()
jaxlib_deps_prefixes=()
jax_runtime_prefixes=()
if [[ "${#runtime_prefixes[@]}" -eq 0 ]]; then
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
    "py-lit=$ob/external/+py_lit_native+py_lit_native/prefix"
    "py-pillow=$ob/external/+py_pillow_native+py_pillow_native/prefix"
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
else
  torch_runtime_prefixes=("${runtime_prefixes[@]}")
  jaxlib_deps_prefixes=("${runtime_prefixes[@]}")
  jax_runtime_prefixes=("${runtime_prefixes[@]}")
fi

torch_graphs=(
  py_torch_214_full_cu130_build_graph.json
  py_torch_214_full_cu129_build_graph.json
  py_triton_pytorch_pin_cu130_build_graph.json
  py_triton_pytorch_pin_cu129_build_graph.json
)
jax_graphs=(
  py_jax_0102_cu130_build_graph.json
  py_jax_0102_cu129_build_graph.json
)
torch_workloads=(
  W1a
  W1b
  W1c
  W1d
  W2a
  W2b
  W2c
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
)

ts="$(date -u +%Y%m%dT%H%M%SZ)"
out_dir="${out_dir:-/vaso/agents/trae/proofs/triumvirate-gate-$profile-$line-$ts}"
acceptance_out="${acceptance_out:-acceptance-$profile-$line.json}"
mkdir -p "$out_dir"
results_jsonl="$out_dir/sub-results.jsonl"
: > "$results_jsonl"

json_append_result() {
  local name="$1"; shift
  local stage="$1"; shift
  local rc="$1"; shift
  local stdout_path="$1"; shift
  local stderr_path="$1"; shift
  local started="$1"; shift
  local finished="$1"; shift
  python3 - "$results_jsonl" "$name" "$stage" "$rc" "$stdout_path" "$stderr_path" "$started" "$finished" "$@" <<'PY'
import json
import sys

path, name, stage, rc, stdout, stderr, started, finished, *command = sys.argv[1:]
doc = {
    "name": name,
    "stage": stage,
    "command": command,
    "returncode": int(rc),
    "stdout": stdout,
    "stderr": stderr,
    "started_utc": started,
    "finished_utc": finished,
    "verdict": "passed" if int(rc) == 0 else "failed",
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
  json_append_result "$name" "$stage" "$rc" "$stdout_path" "$stderr_path" "$started" "$finished" "$@"
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
if [[ "$profile" == "torch" ]]; then
  compiler_pathway_args+=(--expect-failure "triton:TRITON_BUILD_WITH_CLANG_LLD.*1")
fi
run_stage "compiler_pathway_guard" "compiler_pathway" "${compiler_pathway_args[@]}"

if [[ "$profile" == "torch" ]]; then
  torch_runtime_args=()
  jaxlib_deps_runtime_args=()
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
  append_runtime_args jax_runtime_prefixes jax_runtime_args

  run_stage \
    "compiler_provenance_jaxlib" \
    "compiler_provenance" \
    python3 tools/compiler_provenance.py "$jaxlib_prefix" \
      --consumer jaxlib \
      --profile jax \
      --policy tools/compiler_pathways.json

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
  "${acceptance_prefix_args[@]}"
