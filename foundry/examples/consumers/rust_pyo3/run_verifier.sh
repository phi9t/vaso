#!/usr/bin/env bash
set -euo pipefail

python_interpreter="$1"
wheel="$2"
verifier="$3"

if [[ ! -x "$python_interpreter" ]]; then
  echo "profile Python interpreter is not executable: $python_interpreter" >&2
  exit 2
fi

if [[ "$python_interpreter" = */bin/python3.13 ]]; then
  python_prefix="${python_interpreter%/bin/python3.13}"
  export LD_LIBRARY_PATH="$python_prefix/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
fi

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
if ((${#dependency_sites[@]})); then
  dependency_path="$(IFS=:; printf '%s' "${dependency_sites[*]}")"
  export PYTHONPATH="$dependency_path${PYTHONPATH:+:$PYTHONPATH}"
fi
if ((${#dependency_libs[@]})); then
  dependency_lib_path="$(IFS=:; printf '%s' "${dependency_libs[*]}")"
  export LD_LIBRARY_PATH="$dependency_lib_path${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
fi

exec "$python_interpreter" "$verifier" "$wheel"
