#!/usr/bin/env bash
set -euo pipefail

profile=""
out=""
only=""
while [[ "$#" -gt 0 ]]; do
  case "$1" in
    --profile)
      profile="${2:?--profile requires a value}"
      shift 2
      ;;
    --out)
      out="${2:?--out requires a value}"
      shift 2
      ;;
    --only)
      only="${2:?--only requires a value}"
      shift 2
      ;;
    *)
      echo "unknown argument: $1" >&2
      exit 2
      ;;
  esac
done

case "$profile" in
  torch|jax) ;;
  *) echo "--profile must be torch or jax" >&2; exit 2 ;;
esac
[[ -n "$out" ]] || { echo "--out is required" >&2; exit 2; }
[[ -n "$only" ]] || { echo "--only is required" >&2; exit 2; }
: "${VASO_BAZEL_OB:?run through foundry/run.sh --insula-cmd}"
: "${VASO_CUDA_LINE:?select cu129 or cu130}"

one_path() {
  local description="$1"
  shift
  local matches=()
  local pattern match
  shopt -s nullglob
  for pattern in "$@"; do
    for match in $pattern; do
      matches+=("$match")
    done
  done
  shopt -u nullglob
  if [[ "${#matches[@]}" -ne 1 ]]; then
    echo "expected exactly one $description, found ${#matches[@]}" >&2
    printf '  %s\n' "${matches[@]}" >&2
    return 2
  fi
  printf '%s\n' "${matches[0]}"
}

runtime_args=()
add_runtime_prefix() {
  local name="$1"
  local pattern="$2"
  local matches=()
  local match
  shopt -s nullglob
  for match in $pattern; do
    matches+=("$match")
  done
  shopt -u nullglob
  if [[ "${#matches[@]}" -gt 1 ]]; then
    echo "runtime prefix $name matched more than once" >&2
    printf '  %s\n' "${matches[@]}" >&2
    return 2
  fi
  if [[ "${#matches[@]}" -eq 1 ]]; then
    runtime_args+=(--runtime-prefix "$name=${matches[0]}")
  fi
}

python_prefix="$(one_path Python "$VASO_BAZEL_OB/external/+python_native+python_313_native/prefix")"
common_runtime_repos=(
  py_numpy_native
  py_typing_extensions_native
  py_absl_py_native
  py_ml_dtypes_native
  py_opt_einsum_native
  py_scipy_native
)
for repo in "${common_runtime_repos[@]}"; do
  add_runtime_prefix "$repo" "$VASO_BAZEL_OB/external/+${repo}+*/prefix"
done

runner=(
  "$python_prefix/bin/python3" -m workloads.run_workloads
  --line "$VASO_CUDA_LINE"
  --gpus 1
  --out "$out"
  --python-prefix "$python_prefix"
  --only "$only"
)

if [[ "$profile" == "torch" ]]; then
  torch_prefix="$(one_path PyTorch "$VASO_BAZEL_OB/execroot/_main/bazel-out"/*/bin/native/pytorch/pytorch_action_prefix)"
  triton_prefix="$(one_path Triton "$VASO_BAZEL_OB/execroot/_main/bazel-out"/*/bin/native/triton/triton_action_prefix)"
  for repo in py_sympy_native py_mpmath_native py_filelock_native py_networkx_native py_jinja2_native py_markupsafe_native py_fsspec_native py_lit_native; do
    add_runtime_prefix "$repo" "$VASO_BAZEL_OB/external/+${repo}+*/prefix"
  done
  runner+=(--torch-prefix "$torch_prefix" --triton-prefix "$triton_prefix")
else
  jaxlib_prefix="$(one_path JAXlib "$VASO_BAZEL_OB/execroot/_main/bazel-out"/*/bin/native/jaxlib/jaxlib_action_prefix)"
  jax_prefix="$VASO_BAZEL_OB/external/+py_jax_native+py_jax_native/prefix"
  if [[ ! -d "$jax_prefix" ]]; then
    jax_prefix="$jaxlib_prefix"
  fi
  jax_runtime_prefix="$out/jax-runtime-prefix"
  "$python_prefix/bin/python3" -m workloads.nanogpt_speedrun.wheel_runtime \
    --manifest native/jaxlib/nested_http_archives.json \
    --distdir /vaso/sources/jax/distdir \
    --prefix "$jax_runtime_prefix"
  export VASO_JAX_RUNTIME_MANIFEST="$jax_runtime_prefix/runtime-wheels.json"
  runtime_args+=(--runtime-prefix "jax-wheel-runtime=$jax_runtime_prefix")
  runner+=(--jax-prefix "$jax_prefix" --jaxlib-prefix "$jaxlib_prefix")
fi

runner+=("${runtime_args[@]}")
exec "${runner[@]}"
