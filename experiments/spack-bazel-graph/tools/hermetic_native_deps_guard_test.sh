#!/usr/bin/env bash
set -euo pipefail

mapfile -t synthetic_shells < <(find -L synthetic -maxdepth 1 -type f -name '*.sh' | sort)

python3 tools/native_build_mechanism_guard.py \
  --require-mechanisms autotools,boost-build,cmake,generic,makefile,meson,perl,python-bootstrap-pip,python-bootstrap-tool,python-pip-install,python-wheel,sdk-boundary \
  --rootfs-lock rootfs/cuda_ecosystem.lock.json \
  --toolchain-allowlist tools/toolchain_setting_allowlist.txt \
  .bazelrc MODULE.bazel run.sh tools/*.sh "${synthetic_shells[@]}" \
  native/*/*.bzl

python3 tools/python_abi_literal_guard.py \
  --allowlist tools/python_abi_literal_allowlist.txt \
  --coverage-anchor tools/python_abi_literal_allowlist.txt \
  native/*/*.bzl native/*/*.py
