#!/usr/bin/env bash
set -euo pipefail

if [[ "${VASO_IN_INSULA:-0}" != "1" ]]; then
  echo "run me inside the insula: run.sh --insula-cmd bazel test //native/py_llvmlite:mechanism_guard_test" >&2
  exit 2
fi

guard="$1"
rule="$2"
python3 "$guard" --require-mechanisms python-pip-install "$rule"
