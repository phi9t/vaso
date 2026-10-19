#!/usr/bin/env bash
set -euo pipefail

find_prefix() {
  local base hit
  for base in "${RUNFILES_DIR:-}" "$PWD" "$PWD/.." "$0.runfiles"; do
    [[ -n "$base" && -d "$base" ]] || continue
    hit="$(find "$base" -type d -path "*less_native*/prefix" 2>/dev/null | head -1)"
    if [[ -n "$hit" ]]; then
      echo "$hit"
      return 0
    fi
  done
  return 1
}

prefix="$(find_prefix)"
"$prefix/bin/less" --version | grep -q 'less 692'
"$prefix/bin/lesskey" -V | grep -q 'lesskey  version 692'
[[ "$("$prefix/bin/lessecho" alpha "two words")" == "alpha two words" ]]
