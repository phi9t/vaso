#!/usr/bin/env bash
set -euo pipefail

if [[ "${VASO_IN_INSULA:-0}" != "1" ]]; then
  echo "run me inside the insula: run.sh --insula-cmd bazel test //tools:pytorch_recipe_provenance_test" >&2
  exit 2
fi

TOOL_RUNFILE="$1"
SPACK_RUNFILE="$2"

runfile_path() {
  local rel="$1"
  if [[ "$rel" = /* && -e "$rel" ]]; then
    echo "$rel"
    return 0
  fi
  for base in "${RUNFILES_DIR:-}" "${0}.runfiles" "$PWD"; do
    [[ -n "$base" && -d "$base" ]] || continue
    if [[ -e "$base/$rel" ]]; then
      echo "$base/$rel"
      return 0
    fi
  done
  echo "could not locate runfile: $rel" >&2
  return 1
}

TOOL="$(runfile_path "$TOOL_RUNFILE")"
SPACK_BIN="$(runfile_path "$SPACK_RUNFILE")"
OUT="${TEST_TMPDIR:?TEST_TMPDIR must be set by Bazel}/pytorch_recipe_provenance.json"
CLASS_TMPDIR="${TEST_TMPDIR:-$(dirname "$OUT")}"

python3 "$TOOL" \
  --spack "$SPACK_BIN" \
  --out "$OUT" \
  --query torch \
  --query triton \
  --query jax \
  --require-present py-torch \
  --require-present py-triton \
  --require-present py-jax \
  --require-present py-jaxlib \
  --require-present py-protobuf \
  --require-present magma \
  --require-version py-protobuf@4.21.12 \
  --require-absent vendor-libtorch \
  --require-absent libtorch

python3 - "$OUT" <<'PY'
import json
import sys

data = json.load(open(sys.argv[1], encoding="utf-8"))
assert data["spack"]["version"].startswith("1.2."), data["spack"]
assert data["spack"]["executable"].endswith("/spack"), data["spack"]
assert data["packages"]["py-torch"]["present"], data["packages"]["py-torch"]
assert data["packages"]["py-torch"]["class"] == "PyTorch", data["packages"]["py-torch"]
assert "python" in data["packages"]["py-torch"]["build_systems"], data["packages"]["py-torch"]
assert "cuda" in data["packages"]["py-torch"]["build_systems"], data["packages"]["py-torch"]
assert "2.12.0" in data["packages"]["py-torch"]["versions"], data["packages"]["py-torch"]["versions"][:5]
assert "2.14.0" in data["packages"]["py-torch"]["versions"], data["packages"]["py-torch"]["versions"]
assert "cudss" in data["packages"]["py-torch"]["dependencies"], data["packages"]["py-torch"]["dependencies"]
assert "nvshmem+cuda~mpi+ucx~nccl+gdrcopy cuda_arch=100" in data["packages"]["py-torch"]["dependencies"], data["packages"]["py-torch"]["dependencies"]
assert "magma@2.6.1+cuda cuda_arch=100" in data["packages"]["py-torch"]["dependencies"], data["packages"]["py-torch"]["dependencies"]
assert "protobuf@21.12" in data["packages"]["py-torch"]["dependencies"], data["packages"]["py-torch"]["dependencies"]
assert "py-protobuf@4.21.12" in data["packages"]["py-torch"]["dependencies"], data["packages"]["py-torch"]["dependencies"]
assert data["packages"]["magma"]["present"], data["packages"]["magma"]
assert "2.6.1" in data["packages"]["magma"]["versions"], data["packages"]["magma"]["versions"]
assert "/spack_overlays/vaso/spack_repo/vaso_overlay/packages/magma/package.py" in data["packages"]["magma"]["recipe"], data["packages"]["magma"]["recipe"]
assert data["packages"]["py-triton"]["present"], data["packages"]["py-triton"]
assert "python" in data["packages"]["py-triton"]["build_systems"], data["packages"]["py-triton"]
assert data["packages"]["py-jax"]["present"], data["packages"]["py-jax"]
assert data["packages"]["py-jaxlib"]["present"], data["packages"]["py-jaxlib"]
assert any(dep.startswith("bazel@") for dep in data["packages"]["py-jaxlib"]["dependencies"]), data["packages"]["py-jaxlib"]["dependencies"]
assert data["packages"]["py-protobuf"]["present"], data["packages"]["py-protobuf"]
assert "4.21.12" in data["packages"]["py-protobuf"]["versions"], data["packages"]["py-protobuf"]["versions"]
assert "/spack_overlays/vaso/spack_repo/vaso_overlay/packages/py_protobuf/package.py" in data["packages"]["py-protobuf"]["recipe"], data["packages"]["py-protobuf"]["recipe"]
assert "vendor-libtorch" not in data["search"]["torch"], data["search"]["torch"]
assert not data["packages"]["vendor-libtorch"]["present"], data["packages"]["vendor-libtorch"]
assert not data["packages"]["libtorch"]["present"], data["packages"]["libtorch"]
print(json.dumps({
    "package_root": data["package_root"],
    "package_roots": data["package_roots"],
    "torch_hits": data["search"]["torch"],
    "triton_hits": data["search"]["triton"],
    "jax_hits": data["search"]["jax"],
}, sort_keys=True))
PY

CLASS_CHECK="$CLASS_TMPDIR/pytorch_recipe_class_metadata_check.py"
cat > "$CLASS_CHECK" <<'PY'
import json
import spack.repo


def dependency_rows(package_name):
    cls = spack.repo.PATH.get_pkg_class(package_name)
    return sorted(
        (str(when), dep_name, str(dep.spec))
        for when, deps in cls.dependencies.items()
        for dep_name, dep in deps.items()
    )


def conflict_rows(package_name):
    cls = spack.repo.PATH.get_pkg_class(package_name)
    return sorted(
        (str(when), str(conflict))
        for when, conflicts in cls.conflicts.items()
        for conflict, _ in conflicts
    )


torch_deps = dependency_rows("py-torch")
bad_torch_magma = [
    row
    for row in torch_deps
    if row[1] == "magma"
    and row[2] == "magma@:2.9+cuda"
    and (
        row[0] in {"+cuda+magma", "+magma+cuda"}
        or "@2.14" in row[0]
    )
]
if bad_torch_magma:
    raise SystemExit(
        "py-torch still exposes inherited broad CUDA MAGMA dependency to 2.14: "
        + json.dumps(bad_torch_magma)
    )

expected_torch_magma = [
    row
    for row in torch_deps
    if row[1] == "magma"
    and row[2] == "magma@2.6.1+cuda cuda_arch=100"
    and "@2.14.0" in row[0]
    and "+cuda" in row[0]
    and "+magma" in row[0]
]
if not expected_torch_magma:
    raise SystemExit(
        "py-torch does not expose the PyTorch 2.14 MAGMA provider edge: "
        + json.dumps([row for row in torch_deps if row[1] == "magma"])
    )

magma_conflicts = conflict_rows("magma")
bad_magma_conflicts = [
    row
    for row in magma_conflicts
    if row in {
        ("@:2.8.0", "^cuda@12.6:"),
        ("@:2.9.0", "^cuda@13:"),
    }
]
if bad_magma_conflicts:
    raise SystemExit(
        "magma still exposes broad inherited CUDA conflicts: "
        + json.dumps(bad_magma_conflicts)
    )

print(json.dumps({
    "magma_conflicts": [row for row in magma_conflicts if row[1].startswith("^cuda@1")],
    "torch_magma_deps": [row for row in torch_deps if row[1] == "magma"],
}, sort_keys=True))
PY

"$SPACK_BIN" python "$CLASS_CHECK"
