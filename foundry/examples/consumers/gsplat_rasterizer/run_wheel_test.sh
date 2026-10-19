#!/usr/bin/env bash
set -euo pipefail

device="cuda"
python="python3"
torch_prefix_file=""
for arg in "$@"; do
  case "$arg" in
    --device=*) device="${arg#--device=}" ;;
    --python=*) python="${arg#--python=}" ;;
    --torch-prefix-file=*) torch_prefix_file="${arg#--torch-prefix-file=}" ;;
    *) echo "unknown argument: $arg" >&2; exit 2 ;;
  esac
done

runfiles="${RUNFILES_DIR:-${TEST_SRCDIR:-}}"
if [[ -z "$runfiles" || ! -d "$runfiles" ]]; then
  echo "missing Bazel runfiles directory" >&2
  exit 2
fi
if [[ ! -x "$python" ]]; then
  echo "missing profile Python executable: $python" >&2
  exit 2
fi
if [[ "$python" = */bin/python3.13 ]]; then
  python_prefix="${python%/bin/python3.13}"
  export LD_LIBRARY_PATH="$python_prefix/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
fi

runfile_path() {
  local path="$1"
  if [[ "$path" = /* || -e "$path" ]]; then
    printf '%s\n' "$path"
  else
    printf '%s\n' "$runfiles/$path"
  fi
}

site="${TEST_TMPDIR:-$(pwd)}/gsplat_rasterizer_site"
rm -rf "$site"
mkdir -p "$site"

wheelhouse="$(find "$runfiles" \( -type d -o -type l \) -name 'gsplat_rasterizer_wheelhouse' | sort | head -n 1)"
wheel=""
if [[ -n "$wheelhouse" ]]; then
  wheel="$(find -L "$wheelhouse" -type f -name 'vaso_gsplat_rasterizer-*.whl' | sort | head -n 1)"
fi
if [[ -z "$wheel" ]]; then
  echo "missing vaso_gsplat_rasterizer wheel in runfiles" >&2
  exit 2
fi

test_script="$(find -L "$runfiles" -maxdepth 5 -type f -path '*/gsplat_rasterizer/tests/gsplat_rasterizer_test.py' | sort | head -n 1)"
if [[ -z "$test_script" ]]; then
  echo "missing gsplat rasterizer test script in runfiles" >&2
  exit 2
fi
reference_image="$(find -L "$runfiles" -maxdepth 5 -type f -path '*/gsplat_rasterizer/tests/reference_image.pt' | sort | head -n 1)"
if [[ -z "$reference_image" ]]; then
  echo "missing gsplat rasterizer reference image in runfiles" >&2
  exit 2
fi

"$python" - "$wheel" "$site" <<'PY'
import sys
import zipfile
from pathlib import Path

wheel = Path(sys.argv[1])
site = Path(sys.argv[2])
with zipfile.ZipFile(wheel) as zf:
    zf.extractall(site)
PY

if [[ -n "$torch_prefix_file" ]]; then
  torch_prefix_file="$(runfile_path "$torch_prefix_file")"
  if [[ ! -f "$torch_prefix_file" ]]; then
    echo "missing torch artifact prefix file: $torch_prefix_file" >&2
    exit 2
  fi
  torch_prefix="$(<"$torch_prefix_file")"
elif [[ -n "${VASO_TORCH_PREFIX:-}" ]]; then
  torch_prefix="$VASO_TORCH_PREFIX"
else
  torch_prefix="$("$python" - "$runfiles" <<'PY'
import sys
from pathlib import Path

root = Path(sys.argv[1])
for path in sorted(root.rglob("*")):
    if not path.is_dir():
        continue
    matches = sorted((path / "lib").glob("python*/site-packages/torch"))
    if matches:
        print(path)
        raise SystemExit(0)
raise SystemExit(1)
PY
)"
fi

torch_site="$("$python" - "$torch_prefix" <<'PY'
import sys
from pathlib import Path

prefix = Path(sys.argv[1])
matches = sorted((prefix / "lib").glob("python*/site-packages"))
if len(matches) != 1:
    raise SystemExit(f"{prefix} must have exactly one site-packages tree")
print(matches[0])
PY
)"

torch_lib="$torch_site/torch/lib"
dependency_sites=()
dependency_libs=()
if [[ -n "${python_prefix:-}" ]]; then
  python_external="${python_prefix%/external/*}/external"
  if [[ "$python_external" != "$python_prefix" && -d "$python_external" ]]; then
    while IFS= read -r dependency_site; do
      dependency_sites+=("$dependency_site")
    done < <(find "$python_external" -mindepth 1 -maxdepth 1 -type d -name '+py_*_native+*_native' -exec sh -c 'for repo do for site in "$repo"/prefix/lib/python*/site-packages; do [ -d "$site" ] && printf "%s\n" "$site"; done; done' sh {} + | sort)
    while IFS= read -r dependency_lib; do
      dependency_libs+=("$dependency_lib")
    done < <(for repo in "$python_external"/*; do
      for lib_dir in "$repo"/prefix/lib "$repo"/prefix/lib64; do
        [[ -d "$lib_dir" ]] && printf '%s\n' "$lib_dir"
      done
    done | sort)
  fi
fi
dependency_path=""
if ((${#dependency_sites[@]})); then
  dependency_path="$(IFS=:; printf '%s' "${dependency_sites[*]}")"
fi
dependency_lib_path=""
if ((${#dependency_libs[@]})); then
  dependency_lib_path="$(IFS=:; printf '%s' "${dependency_libs[*]}")"
fi
export PYTHONPATH="$site:$torch_site${dependency_path:+:$dependency_path}${PYTHONPATH:+:$PYTHONPATH}"
export LD_LIBRARY_PATH="$torch_prefix/lib:$torch_prefix/lib64:$torch_lib${dependency_lib_path:+:$dependency_lib_path}:/usr/local/cuda/lib64:/run/nvidia-driver/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
export PATH="$torch_prefix/bin:${PATH:-}"

exec "$python" "$test_script" --device "$device" --reference "$reference_image"
