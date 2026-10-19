#!/usr/bin/env python3
"""Classify whether a change can rebuild native Bazel packages.

Fast mode is intentionally cheap: it compares changed Git paths with the
checked-in native input manifest and scans changed lines for environment leaks
that are known to enter native action keys today. Exact mode compares Bazel
``aquery --output=jsonproto`` action signatures when those are available.
"""

from __future__ import annotations

import argparse
import fnmatch
import getpass
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable


GRAPH_PREFIX = "foundry/"
PREVIOUS_GRAPH_PREFIXES = ("graph/", "experiments/spack-bazel-graph/")
GRAPH_PREFIXES = (GRAPH_PREFIX, *PREVIOUS_GRAPH_PREFIXES)
EXIT_UNCHANGED = 0
EXIT_ALLOWED_REBUILD = 10
EXIT_OTHER_REBUILD = 20
PACKAGE_ORDER = ("bzip2", "xz", "zstd", "zlib-ng", "pytorch", "torchvision", "torchaudio", "triton", "jaxlib")
MNEMONIC_BY_PACKAGE = {
    "bzip2": "Bzip2ActionPrefix",
    "xz": "XzActionPrefix",
    "zstd": "ZstdActionPrefix",
    "zlib-ng": "ZlibNgActionPrefix",
    "pytorch": "PytorchNativePrefix",
    "torchvision": "TorchvisionNativePrefix",
    "torchaudio": "TorchaudioNativePrefix",
    "triton": "TritonNativePrefix",
    "jaxlib": "JaxlibNativePrefix",
}
PACKAGE_BY_MNEMONIC = {mnemonic: package for package, mnemonic in MNEMONIC_BY_PACKAGE.items()}
HOST_EXACT_PACKAGES = frozenset({"bzip2", "xz", "zlib-ng", "zstd"})
DEFAULT_HOST_TARGETS = {
    "bzip2": "//native/bzip2:bzip2_action_prefix",
    "xz": "//native/xz:xz_action_prefix",
    "zlib-ng": "//native/zlib_ng:zlib_ng_action_prefix",
    "zstd": "//native/zstd:zstd_action_prefix",
}
DEFAULT_INSULA_TARGETS = {
    "bzip2": "//native/bzip2:bzip2_action_prefix",
    "xz": "//native/xz:xz_action_prefix",
    "zlib-ng": "//native/zlib_ng:zlib_ng_action_prefix",
    "zstd": "//native/zstd:zstd_action_prefix",
    "pytorch": "//native/pytorch:pytorch_action",
    "torchvision": "//native/torchvision:torchvision_action",
    "torchaudio": "//native/torchaudio:torchaudio_action",
    "triton": "//native/triton:triton_action",
    "jaxlib": "//native/jaxlib:jaxlib_action",
}
ESTATE_DISTDIRS = (
    "sources/bazel/distdir",
    "sources/native/distdir",
    "sources/triton/distdir",
    "sources/jax/distdir",
    "sources/pytorch/distdir",
)
PACKAGE_DIRS = {
    "bzip2": "native/bzip2",
    "xz": "native/xz",
    "zstd": "native/zstd",
    "zlib-ng": "native/zlib_ng",
    "pytorch": "native/pytorch",
    "torchvision": "native/torchvision",
    "torchaudio": "native/torchaudio",
    "triton": "native/triton",
    "jaxlib": "native/jaxlib",
}
REPOSITORY_RULE_NATIVE_MNEMONIC = "RepositoryRuleNative"
NATIVE_REPO_LABEL_RE = re.compile(r"@([A-Za-z0-9_]+_native)//")
USE_REPO_RULE_RE = re.compile(
    r'(?m)^(?P<var>[A-Za-z_][A-Za-z0-9_]*)\s*=\s*use_repo_rule\(\s*"//native/(?P<dir>[^:"]+):[^"]+"\s*,\s*"(?P<rule>[A-Za-z_][A-Za-z0-9_]*)"\s*\)'
)
NAME_ATTR_RE = re.compile(r'(?m)^\s*name\s*=\s*"([^"]+)"')
CAUSE_RANK = {"source": 1, "toolchain": 2, "flags": 3, "other": 4}
STATUS_RANK = {"unchanged": 0, "needs-insula": 0, "may-rebuild": 1, "rebuild": 2}
ENV_FLAG_RE = re.compile(r"--(?:action_env|repo_env)=")
PER_COMMIT_RE = re.compile(r"\b(?:release_git_sha|deterministic_seed|source_date_epoch|SOURCE_DATE_EPOCH)\b")
JOB_RE = re.compile(r"\b(?:max_jobs|MAX_JOBS|local_resources|VASO_HOST_CPU_JOBS)\b")
PATH_LEAK_RE = re.compile(r"(/tmp|/var/tmp|/vaso/lines/|output_base|VASO_BAZEL_OB|\$PWD|execroot)")
TOOLCHAIN_RE = re.compile(r"\b(?:CC|CXX|CMAKE|CUDA|CUDNN|NCCL|NVSHMEM|compiler|toolchain|rootfs)\b", re.I)
FLAG_RE = re.compile(r"\b(?:CFLAGS|CXXFLAGS|LDFLAGS|NVCC_FLAGS|copt|cxxopt|linkopt|cuda_arch|TORCH_CUDA_ARCH_LIST)\b")
BUILD_SETTING_RE = re.compile(r"^--(?P<label>//[^=]+)=")
BZLMOD_SOURCE_REPO_RE = re.compile(
    r"\b(?:http_archive|http_file|git_repository|local_repository|archive_override|git_override|"
    r"local_path_override|use_repo_rule|use_repo|bazel_dep)\s*\(|\b(?:sha256|integrity|urls?|downloaded_file_path|patches)\s*=",
)
GENERATED_PREFIX_MANIFEST_PACKAGES = ("pytorch", "torchvision", "torchaudio", "triton", "jaxlib")
GENERATED_PREFIX_MANIFEST_DETAIL = (
    "Bzlmod source-repo changes can rematerialize native repository-rule prefixes; "
    "their generated prefix_manifest.json files are declared action inputs"
)
LEAF_ACTION_ARCHIVE_MARKERS = {
    "bzip2": (
        "bzip2_action_archive",
        "bzip2-1.0.8.tar.gz",
        "ab5a03176ee106d3f0fa90e381da478ddae405918153cca248e682cd0c4a2269",
    ),
    "xz": (
        "xz_action_archive",
        "xz-5.8.3.tar.gz",
        "3d3a1b973af218114f4f889bbaa2f4c037deaae0c8e815eec381c3d546b974a0",
    ),
    "zstd": (
        "zstd_action_archive",
        "zstd-1.5.7.tar.gz",
        "v1.5.7.tar.gz",
        "37d7284556b20954e56e1ca85b80226768902e2edabd3b649e9e72c0c9012ee3",
    ),
}
ESTATE_ONLY_LINE_RE = re.compile(
    r"""^\s*(?:$|#.*|:\s*"\$\{VASO_ESTATE_ROOT:\?set VASO_ESTATE_ROOT\}"|--root\s+"\$VASO_ESTATE_ROOT"\s*\\?|exec\s+\./run\.sh\s+--insula-cmd\s+(?:env\s+VASO_ESTATE_ROOT=/vaso\s+)?bash\s+scripts/agents/triumvirate-gate-insula\.sh\s+"\$\{args\[@\]\}")$"""
)
HOST_GATE_COPYBACK_DISTINCTIVE_LINE_RE = re.compile(
    r"(ensure_stage_root|stage_root_|acceptance_out_stage_host|out_dir_stage_host|copy_rc|map_estate_path_for_insula)"
)
HOST_GATE_COPYBACK_ALLOWED_LINES = {
    "",
    'stage_root_host=""',
    'stage_root_insula=""',
    'acceptance_out_host=""',
    'acceptance_out_stage_host=""',
    'out_dir_host=""',
    'out_dir_stage_host=""',
    "map_estate_path_for_insula() {",
    'local raw_path="$1"',
    'local estate_root="${VASO_ESTATE_ROOT:-}"',
    '[[ -n "$estate_root" && "$raw_path" == /* ]] || { printf \'%s\\n\' "$raw_path"; return 0; }',
    'raw_path="$(readlink -m "$raw_path")"',
    "local estate_agents_root",
    'estate_agents_root="$(readlink -m "$estate_root/agents")"',
    'if [[ "$raw_path" == "$estate_agents_root" ]]; then',
    "printf '/vaso/agents\\n'",
    'elif [[ "$raw_path" == "$estate_agents_root/"* ]]; then',
    'printf \'/vaso/agents/%s\\n\' "${raw_path#"$estate_agents_root/"}"',
    "else",
    'printf \'%s\\n\' "$raw_path"',
    "fi",
    "}",
    "ensure_stage_root() {",
    '[[ -n "$stage_root_host" ]] && return 0',
    'local agent="${VASO_AGENT:-trae}"',
    'if [[ ! "$agent" =~ ^[A-Za-z0-9_.-]+$ ]]; then',
    'echo "VASO_AGENT must contain only letters, numbers, dot, underscore or dash, got: $agent" >&2',
    "exit 2",
    'local tmpdir="$VASO_ESTATE_ROOT/agents/$agent/tmp"',
    'mkdir -p "$tmpdir"',
    'local TMPDIR="$tmpdir"',
    'stage_root_host="$(mktemp -d -p "$TMPDIR" triumvirate-gate.XXXXXX)"',
    'stage_root_insula="/vaso/agents/$agent/tmp/$(basename "$stage_root_host")"',
    'args+=("$1" "$(map_estate_path_for_insula "$2")")',
    "ensure_stage_root",
    'case "$1" in',
    "--acceptance-out)",
    'acceptance_out_host="$(readlink -m "$2")"',
    'acceptance_out_stage_host="$stage_root_host/acceptance.json"',
    'args+=("$1" "$stage_root_insula/acceptance.json")',
    ";;",
    "--out-dir)",
    'out_dir_host="$(readlink -m "$2")"',
    'out_dir_stage_host="$stage_root_host/out-dir"',
    'args+=("$1" "$stage_root_insula/out-dir")',
    "esac",
    'exec ./run.sh --insula-cmd env VASO_ESTATE_ROOT=/vaso bash scripts/agents/triumvirate-gate-insula.sh "${args[@]}"',
    "set +e",
    './run.sh --insula-cmd env VASO_ESTATE_ROOT=/vaso bash scripts/agents/triumvirate-gate-insula.sh "${args[@]}"',
    "run_rc=$?",
    "set -e",
    "copy_rc=0",
    'if [[ -n "$acceptance_out_host" ]]; then',
    'if [[ -f "$acceptance_out_stage_host" ]]; then',
    'mkdir -p "$(dirname "$acceptance_out_host")"',
    'cp "$acceptance_out_stage_host" "$acceptance_out_host" || copy_rc=$?',
    'elif [[ "$run_rc" == "0" ]]; then',
    'echo "acceptance output was not produced inside the insula: $stage_root_insula/acceptance.json" >&2',
    "copy_rc=1",
    'if [[ -n "$out_dir_host" ]]; then',
    'if [[ -d "$out_dir_stage_host" ]]; then',
    'mkdir -p "$out_dir_host"',
    'cp -a "$out_dir_stage_host"/. "$out_dir_host"/ || copy_rc=$?',
    'echo "output directory was not produced inside the insula: $stage_root_insula/out-dir" >&2',
    'if [[ "$copy_rc" == "0" && -n "$stage_root_host" ]]; then',
    'rm -rf "$stage_root_host"',
    'if [[ "$run_rc" == "0" && "$copy_rc" != "0" ]]; then',
    'exit "$copy_rc"',
    'exit "$run_rc"',
}
GRAPH_RUN_ROOT_RELOCATION_ALLOWED_LINES = {
    'REPO_ROOT="$(cd "$EXP_DIR/../.." && pwd)"',
    'REPO_ROOT="$(cd "$EXP_DIR/.." && pwd)"',
}
GRAPH_GATE_ROOT_RELOCATION_ALLOWED_LINES = {
    'EXP_DIR="$ROOT/experiments/spack-bazel-graph"',
    'EXP_DIR="$ROOT/graph"',
    'EXP_DIR="$ROOT/foundry"',
}
GRAPH_ROOT_RELOCATION_DISTINCTIVE_LINE_RE = re.compile(
    r'^\s*(?:REPO_ROOT="\$\(cd "\$EXP_DIR/(?:\.\.|\.\./\.\.)" && pwd\)"|EXP_DIR="\$ROOT/(?:experiments/spack-bazel-graph|graph|foundry)")$'
)
# The run script gained a read-only bind of the repo-root third_party/ so the
# consumers module can reach its vendored modules. A mount is not a Bazel flag
# and does not enter any native action key.
THIRD_PARTY_MOUNT_ALLOWED_LINES = {
    "MP_ARGS+=(--mountpoint /workspace/third_party)",
    '--ro-bind "$REPO_ROOT/third_party" /workspace/third_party',
}
GRAPH_CAPTURE_RELOCATION_FILES = frozenset({
    "py_torch_214_full_cu130_build_graph.json",
    "py_torch_214_full_cu129_build_graph.json",
    "py_triton_pytorch_pin_cu130_build_graph.json",
    "py_triton_pytorch_pin_cu129_build_graph.json",
    "py_jax_0102_cu130_build_graph.json",
    "py_jax_0102_cu129_build_graph.json",
})
DISK_CACHE_GC_LINE_RE = re.compile(
    r"""^\s*(?:$|#.*|native_bazel_disk_cache_gc_max_size\(\) \{ printf "%s\\n" "80G"; \}|native_bazel_disk_cache_gc_max_age\(\) \{ printf "%s\\n" "72h"; \}|append_native_bazel_disk_cache_gc_args\(\) \{ local -n out="\$1"; out\+=\("--experimental_disk_cache_gc_max_size=\$\(native_bazel_disk_cache_gc_max_size\)" "--experimental_disk_cache_gc_max_age=\$\(native_bazel_disk_cache_gc_max_age\)"\); \}|bazel_disk_cache_gc_args=\(\)|append_native_bazel_disk_cache_gc_args bazel_disk_cache_gc_args|append_native_bazel_disk_cache_gc_args args|"\$\{bazel_disk_cache_gc_args\[@\]\}"\s*\\)$"""
)
COREUTILS_OFFLINE_FETCH_LINE_RE = re.compile(
    r"""^\s*(?:$|#.*|"@coreutils_native"|"\@coreutils_native"\)|FETCH_SOURCE_URL="https://ftpmirror\.gnu\.org/coreutils/coreutils-9\.10\.tar\.xz"|FETCH_SOURCE_SHA256="16535a9adf0b10037364e2d612aad3d9f4eca3a344949ced74d12faf4bd51d25"|FETCH_SOURCE_FILENAME="coreutils-9\.10\.tar\.xz"|FETCH_SOURCE_DISTDIR_HOST="\$NATIVE_SOURCE_DISTDIR_HOST"|FETCH_SOURCE_DISTDIR_SB="\$NATIVE_SOURCE_DISTDIR_SB"|;;|if \[\[ "\$\{1:-\}" == "--cubin-guard-offline-check" \]\]; then|shift|echo "== cubin_guard offline check \(inside insula, downloads disabled\) =="|bazel_insula test //tools:cubin_guard_test --test_output=all --announce_rc "\$@"|echo "cubin-guard-offline-check: ok line=\$VASO_CUDA_LINE"|exit 0|fi)$"""
)
COREUTILS_OFFLINE_FETCH_DISTINCTIVE_LINE_RE = re.compile(
    r"""(@coreutils_native|coreutils-9\.10\.tar\.xz|16535a9adf0b10037364e2d612aad3d9f4eca3a344949ced74d12faf4bd51d25|--cubin-guard-offline-check|cubin_guard_test)"""
)
LEAF_ACTION_OFFLINE_FETCH_LINE_RE = re.compile(
    r"""^\s*(?:$|#.*|"@(bzip2|xz|zstd)_action_archive"|"\@(bzip2|xz|zstd)_action_archive"\)|FETCH_SOURCE_URL="https://sourceware\.org/pub/bzip2/bzip2-1\.0\.8\.tar\.gz"|FETCH_SOURCE_URL="https://github\.com/tukaani-project/xz/releases/download/v5\.8\.3/xz-5\.8\.3\.tar\.gz"|FETCH_SOURCE_URL="https://github\.com/facebook/zstd/archive/v1\.5\.7\.tar\.gz"|FETCH_SOURCE_SHA256="ab5a03176ee106d3f0fa90e381da478ddae405918153cca248e682cd0c4a2269"|FETCH_SOURCE_SHA256="3d3a1b973af218114f4f889bbaa2f4c037deaae0c8e815eec381c3d546b974a0"|FETCH_SOURCE_SHA256="37d7284556b20954e56e1ca85b80226768902e2edabd3b649e9e72c0c9012ee3"|FETCH_SOURCE_FILENAME="(bzip2-1\.0\.8|xz-5\.8\.3|zstd-1\.5\.7)\.tar\.gz"|FETCH_SOURCE_DISTDIR_HOST="\$NATIVE_SOURCE_DISTDIR_HOST"|FETCH_SOURCE_DISTDIR_SB="\$NATIVE_SOURCE_DISTDIR_SB"|;;)$"""
)
LEAF_ACTION_OFFLINE_FETCH_DISTINCTIVE_LINE_RE = re.compile(
    r"""(@(bzip2|xz|zstd)_action_archive|(bzip2-1\.0\.8|xz-5\.8\.3|zstd-1\.5\.7)\.tar\.gz|ab5a03176ee106d3f0fa90e381da478ddae405918153cca248e682cd0c4a2269|3d3a1b973af218114f4f889bbaa2f4c037deaae0c8e815eec381c3d546b974a0|37d7284556b20954e56e1ca85b80226768902e2edabd3b649e9e72c0c9012ee3)"""
)
CONSUMER_CONFORMANCE_GATE_LINE_RE = re.compile(
    r"""^\s*(?:$|#.*|run_release_stage\s*\\|"consumer_conformance"\s*\\|env\s+VASO_GPUS=0\s+"\$EXPERIMENT_ROOT/examples/consumers/run\.sh"\s+--line\s+"\$line"|env\s+VASO_PROFILE="\$profile"\s+"INSULA_RELEASE_PYTHON_PREFIX_HOST=\$release_python_prefix"\s+"\$EXPERIMENT_ROOT/examples/consumers/run\.sh"\s*\\|--line\s+"\$line"\s*\\|--release-dir\s+"\$release_dir")$"""
)
CONSUMER_CONFORMANCE_GATE_DISTINCTIVE_LINE_RE = re.compile(
    r"""(consumer_conformance|examples/consumers/run\.sh)"""
)
NATIVE_PREFIX_CQUERY_GATE_DISTINCTIVE_LINE_RE = re.compile(
    r"""(bazel_cquery_single_file|resolve_default_action_prefixes|native_prefix_paths|_action_prefix|release_wheel_manifest_targets)"""
)
NATIVE_PREFIX_CQUERY_GATE_ALLOWED_LINES = frozenset(
    line.strip()
    for line in r'''
"$bazel_command"
"jax-runtime-deps=//tools:jax_runtime_release_wheelhouse_manifest"
"jaxlib=//tools:jaxlib_release_wheels_manifest"
"pytorch=//tools:pytorch_release_wheel_manifest"
"torch-runtime-deps=//tools:torch_runtime_release_wheelhouse_manifest"
"torchaudio=//tools:torchaudio_release_wheel_manifest"
"torchvision=//tools:torchvision_release_wheel_manifest"
"triton=//tools:triton_release_wheel_manifest"
)
:
[[ -n "$file" ]] || continue
actual="$(bazel_cquery_single_file "$target")" || return
add_release_tokens args
add_release_tokens out_args
append_native_bazel_disk_cache_gc_args args
append_native_bazel_disk_cache_gc_args out_args
append_native_determinism_bazel_args args "$line" "$REPO_ROOT" "$commit" "$release_bazel_ob" "/vaso/lines/$line/work" "$source_date_epoch"
append_native_determinism_bazel_args out_args "$line" "$REPO_ROOT" "$commit" "$release_bazel_ob" "/vaso/lines/$line/work" "$source_date_epoch"
append_runtime_args jax_runtime_prefixes jax_runtime_args
append_runtime_args jaxlib_deps_prefixes jaxlib_deps_runtime_args
append_runtime_args torch_runtime_prefixes torch_runtime_args
args+=(--nofetch --repository_disable_download --output=files)
assert_named_cquery_files release_runtime_wheel_manifest_targets release_runtime_wheel_manifests || return $?
assert_named_cquery_files release_wheel_manifest_targets release_wheel_manifests || return $?
assert_named_cquery_files() {
bazel_cquery_single_file() {
bazel_exe="$(bazel_executable)"
bazel_exe="bazel"
bazel_executable() {
bazel_profile_build_stage() {
bazel_profile_command_args args build
bazel_profile_command_args args cquery
bazel_profile_command_args() {
break
build
configure_default_runtime_prefixes
configure_default_runtime_prefixes() {
done
echo "cquery path drift for $name: expected $expected, got $actual" >&2
echo "expected exactly one cquery file for $target, got ${#nonempty[@]}" >&2
echo "missing expected path for cquery target $target_item" >&2
echo "no Bazel targets configured for $target_array_name" >&2
else
expected=""
expected="${path_item#*=}"
fi
file="${nonempty[0]}"
for file in "${files[@]}"; do
for path_item in "${paths[@]}"; do
for target_item in "${targets[@]}"; do
found=0
found=1
if [[ ! -x "$bazel_exe" ]]; then
if [[ "$actual" != "$expected" ]]; then
if [[ "$file" == /* ]]; then
if [[ "$found" != "1" ]]; then
if [[ "$jaxlib_prefix_overridden" == "0" ]]; then
if [[ "$last_stage_rc" != "0" ]]; then
if [[ "$profile" == "torch" ]]; then
if [[ "$torch_prefix_overridden" == "0" ]]; then
if [[ "$torch_ref_prefix_overridden" == "0" ]]; then
if [[ "$torchaudio_prefix_overridden" == "0" ]]; then
if [[ "$torchvision_prefix_overridden" == "0" ]]; then
if [[ "$triton_prefix_overridden" == "0" ]]; then
if [[ "${#build_targets[@]}" -eq 0 ]]; then
if [[ "${#nonempty[@]}" -ne 1 ]]; then
if [[ "${#runtime_prefixes[@]}" -eq 0 ]]; then
if [[ "${#runtime_prefixes[@]}" -ne 0 ]]; then
if [[ "${path_item%%=*}" == "$name" ]]; then
if query_output="$("$bazel_exe" "${args[@]}" "$target")"; then
jax_runtime_prefixes=("${runtime_prefixes[@]}")
jaxlib_deps_prefixes=("${runtime_prefixes[@]}")
jaxlib_prefix="$(bazel_cquery_single_file //tools:jaxlib_action_prefix)" || return
jaxlib_prefix="${jaxlib_prefix:-$ob/execroot/_main/bazel-out/k8-fastbuild/bin/native/jaxlib/jaxlib_action_prefix}"
jaxlib_prefix_overridden=0
jaxlib_prefix_overridden=1
local -n build_targets="$target_array_name"
local -n out_args="$1"
local -n paths="$path_array_name"
local -n targets="$target_array_name"
local args=(
local args=()
local bazel_command="$2"
local bazel_exe
local bazel_exe="${VASO_BAZEL_EXE:-/vaso/tools/bin/bazel}"
local file
local files=()
local nonempty=()
local path_array_name="$2"
local pytorch_jobs
local query_output
local target="$1"
local target_array_name="$1"
local target_item path_item name target expected actual found
mapfile -t files <<<"$query_output"
mkdir -p "/vaso/lines/$line/state/native"
name="${target_item%%=*}"
nonempty+=("$file")
out_args=(
printf '  %s\n' "${nonempty[@]}" >&2
printf '%s\n' "$bazel_exe"
printf '%s\n' "$file"
printf '%s\n' "$pytorch_jobs" > "/vaso/lines/$line/state/native/pytorch_jobs.txt"
printf '%s\n' "$release_bazel_ob/execroot/_main/$file"
pytorch_jobs="$(release_max_jobs VASO_PYTORCH_MAX_JOBS)"
release_pipeline_failed=1
release_runtime_wheel_manifest_targets=(
release_runtime_wheel_manifest_targets=()
release_wheel_manifest_targets=(
release_wheel_manifest_targets=()
resolve_default_action_prefixes() {
return $?
return 0
return 2
run_stage "native_prefix_paths" "native_prefixes" resolve_default_action_prefixes
target="${target_item#*=}"
torch_prefix="$(bazel_cquery_single_file //tools:pytorch_action_prefix)" || return
torch_prefix="${torch_prefix:-$ob/execroot/_main/bazel-out/k8-fastbuild/bin/native/pytorch/pytorch_action_prefix}"
torch_prefix_overridden=0
torch_prefix_overridden=1
torch_ref_prefix="$torch_prefix"
torch_ref_prefix="${torch_ref_prefix:-$torch_prefix}"
torch_ref_prefix_overridden=0
torch_ref_prefix_overridden=1
torch_runtime_prefixes=("${runtime_prefixes[@]}")
torchaudio_prefix="$(bazel_cquery_single_file //tools:torchaudio_action_prefix)" || return
torchaudio_prefix="${torchaudio_prefix:-$ob/execroot/_main/bazel-out/k8-fastbuild/bin/native/torchaudio/torchaudio_action_prefix}"
torchaudio_prefix_overridden=0
torchaudio_prefix_overridden=1
torchvision_prefix="$(bazel_cquery_single_file //tools:torchvision_action_prefix)" || return
torchvision_prefix="${torchvision_prefix:-$ob/execroot/_main/bazel-out/k8-fastbuild/bin/native/torchvision/torchvision_action_prefix}"
torchvision_prefix_overridden=0
torchvision_prefix_overridden=1
triton_prefix="$(bazel_cquery_single_file //tools:triton_action_prefix)" || return
triton_prefix="${triton_prefix:-$ob/execroot/_main/bazel-out/k8-fastbuild/bin/native/triton/triton_action_prefix}"
triton_prefix_overridden=0
triton_prefix_overridden=1
write_native_job_state || return
write_native_job_state() {
}
'''.splitlines()
)
NATIVE_PREFIX_QUERY_TARGETS = {
    "jaxlib": ("jaxlib_action_prefix_path", "jaxlib_action", "jax"),
    "pytorch": ("pytorch_action_prefix_path", "pytorch_action", "torch"),
    "torchaudio": ("torchaudio_action_prefix_path", "torchaudio_action", "torch"),
    "torchvision": ("torchvision_action_prefix_path", "torchvision_action", "torch"),
    "triton": ("triton_action_prefix_path", "triton_action", "torch"),
}


@dataclass(frozen=True)
class InputRule:
    path: str | None
    glob: str | None
    cause: str
    status: str
    detail: str


@dataclass
class PackageResult:
    package: str
    mnemonic: str
    status: str = "unchanged"
    causes: list[dict[str, str]] = field(default_factory=list)
    action_key: dict[str, str | None] | None = None
    note: str | None = None

    def add(self, *, path: str, cause: str, detail: str, status: str = "rebuild") -> None:
        cause = _normalise_cause(cause)
        status = _normalise_status(status)
        item = {"path": path, "class": cause, "detail": detail}
        if item not in self.causes:
            self.causes.append(item)
        if STATUS_RANK[status] > STATUS_RANK.get(self.status, 0):
            self.status = status

    def as_json(self) -> dict[str, object]:
        doc: dict[str, object] = {
            "mnemonic": self.mnemonic,
            "status": self.status,
            "causes": self.causes,
        }
        if self.action_key is not None:
            doc["action_key"] = self.action_key
        if self.note is not None:
            doc["note"] = self.note
        return doc


@dataclass(frozen=True)
class ActionSnapshot:
    mnemonic: str
    label: str
    action_key: str | None
    inputs: frozenset[str]
    arguments: tuple[str, ...]
    env: dict[str, str]
    input_digests: dict[str, str] = field(default_factory=dict)
    unverified_inputs: dict[str, str] = field(default_factory=dict)


@dataclass
class InputContentContext:
    repo_root: Path
    ref: str | None = None
    tree: Path | None = None
    output_base: Path | None = None
    _ref_blob_cache: dict[str, str | None] = field(default_factory=dict)
    _worktree_blob_cache: dict[Path, str | None] = field(default_factory=dict)
    _file_hash_cache: dict[Path, str | None] = field(default_factory=dict)

    def digest_input(self, path: str) -> tuple[str | None, str | None]:
        normalized = path.strip()
        for repo_path in source_repo_candidates(normalized):
            try:
                if self.ref is not None:
                    blob_id = self._git_blob_at_ref(repo_path)
                    if blob_id is not None:
                        return f"git-blob:{blob_id}", None
                else:
                    source_path = self._worktree_source_path(repo_path)
                    if source_path is not None:
                        blob_id = self._git_blob_for_worktree_file(source_path)
                        if blob_id is not None:
                            return f"git-blob:{blob_id}", None
            except (OSError, RuntimeError):
                return None, "unverified-content"
        file_hash = self._hash_available_file(path.strip())
        if file_hash is not None:
            return file_hash, None
        return None, "unverified-content"

    def digest_inputs(self, inputs: Iterable[str]) -> tuple[dict[str, str], dict[str, str]]:
        digests: dict[str, str] = {}
        unverified: dict[str, str] = {}
        for path in sorted(inputs):
            digest, reason = self.digest_input(path)
            if digest is not None:
                digests[path] = digest
            else:
                unverified[path] = reason or "unverified-content"
        return digests, unverified

    def _git_blob_at_ref(self, repo_path: str, seen: frozenset[str] = frozenset()) -> str | None:
        if repo_path in seen:
            raise OSError(f"cyclic source input: {repo_path}")
        if repo_path not in self._ref_blob_cache:
            proc = subprocess.run(
                ["git", "-C", str(self.repo_root), "ls-tree", "-z", self.ref, "--", repo_path],
                capture_output=True,
                text=True,
            )
            value = None
            if proc.returncode == 0 and proc.stdout:
                mode, kind, object_id = proc.stdout.split("\t", 1)[0].split()
                if mode == "120000":
                    target = subprocess.run(
                        ["git", "-C", str(self.repo_root), "cat-file", "blob", object_id],
                        capture_output=True, text=True, check=True,
                    ).stdout
                    referent = os.path.normpath(os.path.join(os.path.dirname(repo_path), target))
                    if os.path.isabs(referent) or referent.startswith("../"):
                        raise OSError(f"source input escapes Git snapshot: {repo_path}")
                    value = self._git_blob_at_ref(referent, seen | {repo_path})
                    if value is None:
                        raise OSError(f"missing source input referent: {repo_path}")
                elif kind == "blob":
                    value = f"{object_id}:executable={int(mode == '100755')}"
                else:
                    raise OSError(f"unsupported source input type: {repo_path}")
            self._ref_blob_cache[repo_path] = value
        return self._ref_blob_cache[repo_path]

    def _worktree_source_path(self, repo_path: str) -> Path | None:
        candidates = [self.repo_root / repo_path]
        relative = graph_relative_path(repo_path)
        if self.tree is not None and relative is not None:
            candidates.append(self.tree / relative)
        for candidate in candidates:
            if candidate.is_file() or candidate.is_symlink():
                return candidate
        return None

    def _git_blob_for_worktree_file(self, path: Path) -> str | None:
        resolved = path.resolve(strict=False)
        if resolved not in self._worktree_blob_cache:
            proc = subprocess.run(
                ["git", "-C", str(self.repo_root), "hash-object", "--no-filters", "--", str(path)],
                capture_output=True,
                text=True,
            )
            self._worktree_blob_cache[resolved] = (
                f"{proc.stdout.strip().splitlines()[0]}:executable={int(bool(path.stat().st_mode & 0o111))}"
                if proc.returncode == 0 and proc.stdout.strip() else None
            )
        return self._worktree_blob_cache[resolved]

    def _hash_available_file(self, path: str) -> str | None:
        for candidate in self._available_file_candidates(path):
            try:
                normalized = candidate.resolve(strict=False)
                if normalized not in self._file_hash_cache:
                    self._file_hash_cache[normalized] = materialized_input_digest(candidate)
                digest = self._file_hash_cache[normalized]
            except (OSError, RuntimeError):
                # Absent inputs, unsupported file types, and symlink cycles
                # cannot establish an unchanged input closure.
                digest = None
            if digest is not None:
                return digest
        return None

    def _available_file_candidates(self, path: str) -> Iterable[Path]:
        value = Path(path)
        if value.is_absolute():
            yield value
            return
        if self.output_base is not None:
            if path.startswith("../"):
                yield self.output_base / "external" / path[3:]
            else:
                yield self.output_base / path
            for execroot in ("_main", "__main__", "main"):
                yield self.output_base / "execroot" / execroot / path
            if path.startswith(("external/", "bazel-out/", "../")):
                # A checkout's bazel-out/external links may point at the other
                # snapshot. Missing materialized inputs must not fall back to it.
                return
        if self.tree is not None:
            yield self.tree / path
            relative = graph_relative_path(path)
            if relative is not None:
                yield self.tree / relative


def materialized_input_digest(path: Path, active: frozenset[Path] = frozenset()) -> str:
    """Hash actual file/tree contents without host paths, owners, or timestamps.

    Execroot links locating the input are transparent. Links within a declared
    tree retain their target text and the contents they expose to the action.
    """
    resolved = path.resolve(strict=True)
    mode = resolved.stat().st_mode
    if stat.S_ISREG(mode):
        return f"file-sha256:{sha256_file(resolved)}:executable={int(bool(mode & 0o111))}"
    if not stat.S_ISDIR(mode):
        raise OSError(f"unsupported input type: {path}")
    if resolved in active:
        raise OSError(f"cyclic input directory: {path}")
    active = active | {resolved}
    entries = []
    for child in sorted(resolved.iterdir(), key=lambda item: item.name):
        if child.is_symlink():
            target = os.readlink(child)
            try:
                content = materialized_input_digest(child, active)
            except FileNotFoundError:
                content = "missing-target"
            entries.append([child.name, "symlink", target, content])
        else:
            entries.append([child.name, materialized_input_digest(child, active)])
    encoded = json.dumps(entries, ensure_ascii=True, separators=(",", ":")).encode()
    return "tree-sha256:" + hashlib.sha256(encoded).hexdigest()


def _normalise_cause(value: str) -> str:
    value = str(value or "other")
    return value if value in CAUSE_RANK else "other"


def _normalise_status(value: str) -> str:
    value = str(value or "rebuild")
    return value if value in STATUS_RANK else "rebuild"


def graph_root_from_tool() -> Path:
    return Path(__file__).resolve().parents[1]


def repo_root_from_graph(graph_root: Path) -> Path:
    if graph_root.parent.name == "experiments":
        return graph_root.parents[1]
    return graph_root.parent


def git(repo: Path, *args: str, check: bool = True, env: dict[str, str] | None = None) -> str:
    proc = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, env=env)
    if check and proc.returncode != 0:
        raise SystemExit(proc.stderr.strip() or proc.stdout.strip() or f"git {' '.join(args)} failed")
    return proc.stdout


def repo_relative(repo_root: Path, path: str | Path) -> str:
    p = Path(path)
    if p.is_absolute():
        try:
            p = p.resolve().relative_to(repo_root.resolve())
        except ValueError:
            return p.as_posix()
    return p.as_posix().lstrip("./")


def candidate_paths(path: str) -> tuple[str, ...]:
    path = path.strip().lstrip("./")
    values = [path]
    rel = path
    for prefix in GRAPH_PREFIXES:
        if path.startswith(prefix):
            rel = path[len(prefix):]
            values.append(rel)
            break
    for prefix in GRAPH_PREFIXES:
        values.append(prefix + rel)
    return tuple(dict.fromkeys(values))


def graph_relative_path(path: str) -> str | None:
    normalized = path.strip().lstrip("./")
    for prefix in GRAPH_PREFIXES:
        if normalized.startswith(prefix):
            return normalized[len(prefix):]
    return None


def git_blob(repo: Path, ref: str, path: str) -> bytes | None:
    spec = f":{path}" if ref == ":" else f"{ref}:{path}"
    proc = subprocess.run(["git", "-C", str(repo), "show", spec], capture_output=True)
    if proc.returncode != 0:
        return None
    return proc.stdout


def comparison_blob(args: argparse.Namespace, path: str) -> bytes | None:
    if args.staged:
        return git_blob(args.repo_root, ":", path)
    if args.head:
        return git_blob(args.repo_root, args.head, path)
    worktree_path = args.repo_root / path
    if not worktree_path.is_file():
        return None
    return worktree_path.read_bytes()


def graph_dir(root: Path) -> Path:
    """The workspace directory under a checkout, whichever name it carries there."""
    for prefix in GRAPH_PREFIXES:
        candidate = root / prefix.rstrip("/")
        if candidate.is_dir():
            return candidate
    return root / GRAPH_PREFIX.rstrip("/")


def path_only_graph_root_relocation(args: argparse.Namespace, path: str) -> bool:
    rel = graph_relative_path(path)
    if rel is None:
        return False
    if rel == "run.sh" and not valid_relocated_script(args, path):
        return False
    base_blob = next(
        (blob for prefix in GRAPH_PREFIXES if (blob := git_blob(args.repo_root, args.base, prefix + rel)) is not None),
        None,
    )
    head_blob = next(
        (blob for prefix in GRAPH_PREFIXES if (blob := comparison_blob(args, prefix + rel)) is not None),
        None,
    )
    return base_blob is not None and head_blob is not None and base_blob == head_blob


def source_repo_candidates(path: str) -> tuple[str, ...]:
    normalized = path.strip()
    while normalized.startswith("./"):
        normalized = normalized[2:]
    if (
        not normalized
        or Path(normalized).is_absolute()
        or normalized.startswith(("../", "bazel-out/", "external/"))
    ):
        return ()
    return candidate_paths(normalized)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def path_matches(rule: InputRule, changed_path: str) -> bool:
    candidates = candidate_paths(changed_path)
    if rule.path:
        exact = rule.path.strip().lstrip("./")
        return any(candidate == exact or candidate.startswith(exact.rstrip("/") + "/") for candidate in candidates)
    if rule.glob:
        pattern = rule.glob.strip().lstrip("./")
        return any(fnmatch.fnmatchcase(candidate, pattern) for candidate in candidates)
    return False


def input_rules(raw: Iterable[object]) -> list[InputRule]:
    rules: list[InputRule] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        path = item.get("path")
        glob = item.get("glob")
        if not isinstance(path, str):
            path = None
        if not isinstance(glob, str):
            glob = None
        if path is None and glob is None:
            continue
        cause = _normalise_cause(str(item.get("cause") or "other"))
        status = _normalise_status(str(item.get("status") or "rebuild"))
        detail = str(item.get("detail") or f"matched native input {path or glob}")
        rules.append(InputRule(path=path, glob=glob, cause=cause, status=status, detail=detail))
    return rules


def default_manifest() -> dict[str, object]:
    common = [
        {
            "path": "run.sh",
            "cause": "flags",
            "status": "may-rebuild",
            "detail": "release Bazel arguments can reach native action keys",
        },
        {
            "path": "scripts/agents/triumvirate-gate.sh",
            "cause": "flags",
            "status": "may-rebuild",
            "detail": "gate wrapper forwards release build settings",
        },
        {
            "path": "scripts/agents/triumvirate-gate-insula.sh",
            "cause": "flags",
            "status": "may-rebuild",
            "detail": "gate Bazel arguments can reach native action keys",
        },
        {
            "path": "tools/native_determinism_args.sh",
            "cause": "other",
            "status": "may-rebuild",
            "detail": "per-commit determinism settings currently feed native action keys",
        },
        {
            "path": "native/common/native_action_toolchain.bzl",
            "cause": "other",
            "status": "may-rebuild",
            "detail": "native toolchain helper can change action env, args, and declared inputs",
        },
        {
            "path": "native/common/build_settings.bzl",
            "cause": "flags",
            "status": "may-rebuild",
            "detail": "native build settings can change action arguments",
        },
        {
            "glob": "toolchains/**",
            "cause": "toolchain",
            "status": "may-rebuild",
            "detail": "rootfs toolchain identity can reach native action keys",
        },
        {
            "path": "rootfs/cuda_ecosystem.lock.json",
            "cause": "toolchain",
            "status": "may-rebuild",
            "detail": "CUDA/rootfs identity can reach native action keys",
        },
        {
            "glob": "locks/*.lock.json",
            "cause": "source",
            "status": "may-rebuild",
            "detail": "profile locks can change native source or dependency pins",
        },
    ]
    packages: dict[str, dict[str, object]] = {}
    for package in PACKAGE_ORDER:
        package_dir = PACKAGE_DIRS[package]
        exact_targets = DEFAULT_INSULA_TARGETS[package]
        packages[package] = {
            "mnemonic": MNEMONIC_BY_PACKAGE[package],
            "host_exact": package in HOST_EXACT_PACKAGES,
            "host_targets": [DEFAULT_HOST_TARGETS[package]] if package in DEFAULT_HOST_TARGETS else [],
            "insula_targets": [exact_targets],
            "inputs": [
                {
                    "path": f"{package_dir}/BUILD.bazel",
                    "cause": "flags",
                    "status": "may-rebuild",
                    "detail": "native action target attributes can change build flags or inputs",
                },
                {
                    "glob": f"{package_dir}/patches/**",
                    "cause": "source",
                    "status": "rebuild",
                    "detail": "native source patch content changed",
                },
            ],
        }
    packages["zlib-ng"]["inputs"].extend([
        {
            "path": "native/zlib_ng/zlib_ng_action.bzl",
            "cause": "other",
            "status": "rebuild",
            "detail": "zlib-ng action implementation changed",
        },
    ])
    for package in ("bzip2", "xz", "zstd"):
        packages[package]["inputs"].extend([
            {
                "path": f"native/{package}/{package}_action.bzl",
                "cause": "flags",
                "status": "rebuild",
                "detail": f"{package} action wrapper changed",
            },
            {
                "path": "native/common/committed_plan_prefix.bzl",
                "cause": "flags",
                "status": "rebuild",
                "detail": f"{package} shared committed-plan prefix rule changed",
            },
            {
                "path": "native/common/native_action_inputs.bzl",
                "cause": "flags",
                "status": "rebuild",
                "detail": f"{package} action prefix provider contract changed",
            },
            {
                "path": "native/common/prefix_manifest.py",
                "cause": "other",
                "status": "rebuild",
                "detail": f"{package} action content manifest writer changed",
            },
            {
                "glob": f"plans/torch/*/{package}.plan.json",
                "cause": "flags",
                "status": "rebuild",
                "detail": f"{package} committed plan changed configure/build flags or source identity",
            },
        ])
    packages["pytorch"]["inputs"].extend([
        {
            "path": "native/zstd/BUILD.bazel",
            "cause": "flags",
            "status": "rebuild",
            "detail": "PyTorch consumes the zstd action prefix target",
        },
        {
            "path": "native/zstd/zstd_action.bzl",
            "cause": "flags",
            "status": "rebuild",
            "detail": "PyTorch consumes the zstd action prefix provider",
        },
        {
            "path": "native/common/committed_plan_prefix.bzl",
            "cause": "flags",
            "status": "rebuild",
            "detail": "PyTorch consumes zstd built by the shared committed-plan prefix rule",
        },
        {
            "path": "native/common/native_action_inputs.bzl",
            "cause": "flags",
            "status": "rebuild",
            "detail": "PyTorch resolves zstd through the action prefix provider contract",
        },
        {
            "path": "native/common/prefix_manifest.py",
            "cause": "other",
            "status": "rebuild",
            "detail": "PyTorch consumes the zstd action content manifest",
        },
        {
            "glob": "plans/torch/*/zstd.plan.json",
            "cause": "source",
            "status": "rebuild",
            "detail": "PyTorch consumes the zstd action prefix key",
        },
    ])
    for package in ("pytorch", "jaxlib"):
        package_dir = PACKAGE_DIRS[package]
        action_file = f"{package_dir}/{package if package != 'pytorch' else 'pytorch'}_action.bzl"
        if package == "torchvision":
            action_file = f"{package_dir}/torchvision_action.bzl"
        elif package == "jaxlib":
            action_file = f"{package_dir}/jaxlib_action.bzl"
        packages[package]["inputs"].extend([
            {
                "path": f"{package_dir}/action_driver.py",
                "cause": "other",
                "status": "rebuild",
                "detail": f"{package} native action driver is a declared action tool",
            },
            {
                "path": action_file,
                "cause": "other",
                "status": "may-rebuild",
                "detail": f"{package} native action definition changed",
            },
            {
                "path": f"{package_dir}/plan.py",
                "cause": "flags",
                "status": "may-rebuild",
                "detail": f"{package} plan can change configure/build flags",
            },
            {
                "path": f"{package_dir}/upstream_pins.json",
                "cause": "source",
                "status": "rebuild",
                "detail": f"{package} upstream source pins changed",
            },
        ])
    packages["triton"]["inputs"].extend([
        {
            "path": "native/triton/triton_action.bzl",
            "cause": "flags",
            "status": "may-rebuild",
            "detail": "Triton committed-plan action attributes can change native action flags or inputs",
        },
        {
            "glob": "plans/torch/*/triton.plan.json",
            "cause": "flags",
            "status": "rebuild",
            "detail": "Triton committed plan changed configure/build flags",
        },
        {
            "path": "native/triton/upstream_pins.json",
            "cause": "source",
            "status": "rebuild",
            "detail": "triton upstream source pins changed",
        },
    ])
    packages["torchvision"]["inputs"].extend([
        {
            "path": "native/torchvision/torchvision_action.bzl",
            "cause": "flags",
            "status": "may-rebuild",
            "detail": "torchvision committed-plan action attributes can change native action flags or inputs",
        },
        {
            "glob": "plans/torch/*/torchvision.plan.json",
            "cause": "flags",
            "status": "rebuild",
            "detail": "torchvision committed plan changed configure/build flags",
        },
        {
            "path": "native/torchvision/upstream_pins.json",
            "cause": "source",
            "status": "rebuild",
            "detail": "torchvision upstream source pins changed",
        },
    ])
    packages["torchaudio"]["inputs"].extend([
        {
            "path": "native/torchaudio/torchaudio_action.bzl",
            "cause": "flags",
            "status": "may-rebuild",
            "detail": "torchaudio committed-plan action attributes can change native action flags or inputs",
        },
        {
            "glob": "plans/torch/*/torchaudio.plan.json",
            "cause": "flags",
            "status": "rebuild",
            "detail": "torchaudio committed plan changed configure/build flags",
        },
        {
            "path": "native/torchaudio/upstream_pins.json",
            "cause": "source",
            "status": "rebuild",
            "detail": "torchaudio upstream source pins changed",
        },
    ])
    packages["pytorch"]["inputs"].append({
        "path": "native/pytorch/pytorch-v2.14.0-2b3ec348-submodules.json",
        "cause": "source",
        "status": "rebuild",
        "detail": "PyTorch recursive source manifest changed",
    })
    packages["jaxlib"]["inputs"].append({
        "path": "native/jaxlib/nested_http_archives.json",
        "cause": "source",
        "status": "rebuild",
        "detail": "jaxlib nested Bazel archive pins changed",
    })
    doc = {
        "schema_version": 1,
        "generated_by": "tools/rebuild_impact.py --refresh-manifest",
        "native_mnemonics": list(MNEMONIC_BY_PACKAGE.values()),
        "common_inputs": common,
        "packages": packages,
    }
    augment_repository_rule_native_manifest(doc, graph_root_from_tool())
    return doc


def native_package_name_for_dir(dirname: str) -> str:
    directory = f"native/{dirname}"
    for package, package_dir in PACKAGE_DIRS.items():
        if package_dir == directory:
            return package
    return dirname


def native_package_dirs(graph_root: Path) -> dict[str, str]:
    native_root = graph_root / "native"
    if not native_root.is_dir():
        return {}
    out: dict[str, str] = {}
    for child in sorted(native_root.iterdir(), key=lambda item: item.name):
        if not child.is_dir() or child.name == "common":
            continue
        if not (child / "BUILD.bazel").exists() and not (child / "plan.py").exists() and not list(child.glob("*.bzl")):
            continue
        out[native_package_name_for_dir(child.name)] = f"native/{child.name}"
    return out


def module_graph_files(graph_root: Path) -> list[Path]:
    return [path for path in (graph_root / "MODULE.bazel", graph_root / "dev.MODULE.bazel") if path.is_file()]


def top_level_call_blocks(text: str) -> Iterable[tuple[str, str]]:
    lines = text.splitlines()
    index = 0
    while index < len(lines):
        match = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)\(\s*$", lines[index])
        if match is None:
            index += 1
            continue
        callee = match.group(1)
        index += 1
        body: list[str] = []
        while index < len(lines) and not re.match(r"^\)\s*$", lines[index]):
            body.append(lines[index])
            index += 1
        if index < len(lines):
            index += 1
        yield callee, "\n".join(body)


def derive_repository_rule_native_graph(graph_root: Path) -> dict[str, object]:
    package_dirs = native_package_dirs(graph_root)
    dir_to_package = {directory.rsplit("/", 1)[-1]: package for package, directory in package_dirs.items()}
    repo_to_package = {
        f"{directory.rsplit('/', 1)[-1]}_native": package
        for package, directory in package_dirs.items()
    }
    var_to_package: dict[str, str] = {}
    module_texts: list[str] = []

    for path in module_graph_files(graph_root):
        text = path.read_text(encoding="utf-8")
        module_texts.append(text)
        for match in USE_REPO_RULE_RE.finditer(text):
            package = dir_to_package.get(match.group("dir"))
            if package is None:
                continue
            var_to_package[match.group("var")] = package
            rule = match.group("rule")
            if rule.endswith("_native"):
                repo_to_package[rule] = package

    for text in module_texts:
        for callee, body in top_level_call_blocks(text):
            package = var_to_package.get(callee)
            if package is None:
                continue
            name = NAME_ATTR_RE.search(body)
            if name is not None and name.group(1).endswith("_native"):
                repo_to_package[name.group(1)] = package

    direct_dependents: dict[str, set[str]] = {package: set() for package in package_dirs}

    def add_edges_for_text(package: str, text: str) -> None:
        for repo in NATIVE_REPO_LABEL_RE.findall(text):
            dependency = repo_to_package.get(repo)
            if dependency is None or dependency == package:
                continue
            direct_dependents.setdefault(dependency, set()).add(package)

    for text in module_texts:
        for callee, body in top_level_call_blocks(text):
            package = var_to_package.get(callee)
            if package is not None:
                add_edges_for_text(package, body)

    for package, directory in package_dirs.items():
        package_root = graph_root / directory
        for path in sorted(package_root.iterdir(), key=lambda item: item.name):
            if not path.is_file():
                continue
            if path.name not in {"BUILD", "BUILD.bazel"} and path.suffix != ".bzl":
                continue
            add_edges_for_text(package, path.read_text(encoding="utf-8"))

    return {
        "package_dirs": package_dirs,
        "dependency_edges": {
            package: sorted(dependents)
            for package, dependents in sorted(direct_dependents.items())
            if dependents
        },
        "repo_to_package": dict(sorted(repo_to_package.items())),
    }


def augment_repository_rule_native_manifest(doc: dict[str, object], graph_root: Path) -> None:
    graph = derive_repository_rule_native_graph(graph_root)
    package_dirs = graph.get("package_dirs")
    if not isinstance(package_dirs, dict):
        return
    packages = doc.setdefault("packages", {})
    if not isinstance(packages, dict):
        return
    for package in sorted(str(package) for package in package_dirs):
        package_doc = packages.setdefault(package, {})
        if not isinstance(package_doc, dict):
            packages[package] = package_doc = {}
        package_doc.setdefault("mnemonic", MNEMONIC_BY_PACKAGE.get(package, REPOSITORY_RULE_NATIVE_MNEMONIC))
        package_doc.setdefault("inputs", [])
    order: list[str] = []
    for package in PACKAGE_ORDER:
        if package in packages and package not in order:
            order.append(package)
    for package in sorted(packages):
        if package not in order:
            order.append(str(package))
    doc["package_order"] = order
    doc["repository_rule_native_graph"] = graph


def load_manifest(path: Path) -> dict[str, object]:
    if not path.exists():
        raise SystemExit(f"native input manifest not found: {path}")
    with path.open(encoding="utf-8") as handle:
        doc = json.load(handle)
    if not isinstance(doc, dict) or doc.get("schema_version") != 1:
        raise SystemExit(f"{path} must be a schema_version=1 JSON object")
    packages = doc.get("packages")
    if not isinstance(packages, dict):
        raise SystemExit(f"{path} must contain a packages object")
    merge_plan_key_inputs(doc, path.parent / "keys.json")
    graph_root = path.parent.parent if path.parent.name == "plans" else path.parent
    augment_repository_rule_native_manifest(doc, graph_root)
    return doc


KEY_SNAPSHOT_PACKAGES = {
    "bzip2": "bzip2",
    "py-torch": "pytorch",
    "py-triton": "triton",
    "py-torchvision": "torchvision",
    "py-torchaudio": "torchaudio",
    "py-jaxlib": "jaxlib",
    "xz": "xz",
    "zlib-ng": "zlib-ng",
    "zstd": "zstd",
}
KEY_SNAPSHOT_CAUSES = {
    "plan": "flags",
    "schema": "toolchain",
    "executor": "toolchain",
    "input": "source",
    "patch": "source",
    "rootfs": "toolchain",
    "wrapper": "toolchain",
    "action-wrapper": "flags",
}


def merge_plan_key_inputs(doc: dict[str, object], keys_path: Path) -> None:
    """Every input recorded in plans/keys.json is a definite rebuild input.

    native-inputs.json is refreshed by hand and went stale as packages moved onto the
    plan executor (plan_executor.py was missing for every package, 2026-10-07). keys.json
    is kept exact by the standing key-snapshot check, so it is the authoritative list.
    """
    if not keys_path.is_file():
        return
    try:
        keys = json.loads(keys_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    entries = keys.get("packages", keys) if isinstance(keys, dict) else {}
    packages = doc["packages"]
    for key, value in entries.items() if isinstance(entries, dict) else []:
        package = KEY_SNAPSHOT_PACKAGES.get(str(key).rsplit("/", 1)[-1])
        if package is None or not isinstance(value, dict):
            continue
        package_doc = packages.setdefault(package, {"inputs": []})
        inputs = package_doc.setdefault("inputs", [])
        known = {item.get("path") for item in inputs if isinstance(item, dict)}
        for item in _as_list(value.get("inputs")):
            if not isinstance(item, dict):
                continue
            rel = item.get("path")
            if not isinstance(rel, str) or rel.startswith("source:") or rel in known:
                continue
            known.add(rel)
            inputs.append({
                "cause": KEY_SNAPSHOT_CAUSES.get(str(item.get("kind")), "toolchain"),
                "detail": f"plans/keys.json {item.get('kind')} input of {key}",
                "path": rel,
                "status": "rebuild",
            })


def manifest_package_order(manifest: dict[str, object]) -> list[str]:
    packages = manifest.get("packages")
    if not isinstance(packages, dict):
        return list(PACKAGE_ORDER)
    order: list[str] = []
    for package in _as_list(manifest.get("package_order")):
        if isinstance(package, str) and package in packages and package not in order:
            order.append(package)
    for package in PACKAGE_ORDER:
        if package in packages and package not in order:
            order.append(package)
    for package in sorted(packages):
        if isinstance(package, str) and package not in order:
            order.append(package)
    return order


def manifest_package_dirs(manifest: dict[str, object]) -> dict[str, str]:
    out = dict(PACKAGE_DIRS)
    graph = manifest.get("repository_rule_native_graph")
    package_dirs = graph.get("package_dirs") if isinstance(graph, dict) else None
    if isinstance(package_dirs, dict):
        for package, directory in package_dirs.items():
            if isinstance(package, str) and isinstance(directory, str):
                out[package] = directory
    return out


def manifest_dependency_edges(manifest: dict[str, object]) -> dict[str, list[str]]:
    graph = manifest.get("repository_rule_native_graph")
    raw = graph.get("dependency_edges") if isinstance(graph, dict) else None
    out: dict[str, list[str]] = {}
    if not isinstance(raw, dict):
        return out
    for package, dependents in raw.items():
        if not isinstance(package, str):
            continue
        out[package] = [item for item in _as_list(dependents) if isinstance(item, str)]
    return out


def package_mnemonic(manifest: dict[str, object], package: str) -> str:
    value = package_manifest(manifest, package).get("mnemonic")
    if isinstance(value, str) and value:
        return value
    return MNEMONIC_BY_PACKAGE.get(package, REPOSITORY_RULE_NATIVE_MNEMONIC)


def rules_for_package(manifest: dict[str, object], package: str) -> list[InputRule]:
    packages = manifest.get("packages")
    package_doc = packages.get(package, {}) if isinstance(packages, dict) else {}
    common = manifest.get("common_inputs", [])
    package_inputs = package_doc.get("inputs", []) if isinstance(package_doc, dict) else []
    return input_rules([*_as_list(common), *_as_list(package_inputs)])


def package_manifest(manifest: dict[str, object], package: str) -> dict[str, object]:
    packages = manifest.get("packages")
    value = packages.get(package, {}) if isinstance(packages, dict) else {}
    return value if isinstance(value, dict) else {}


def _as_list(value: object) -> list[object]:
    return value if isinstance(value, list) else []


def changed_paths(args: argparse.Namespace) -> list[str]:
    repo = args.repo_root
    paths = [repo_relative(repo, path) for path in args.paths]
    selector = ["--", *paths] if paths else []
    base = args.base
    if args.staged:
        out = git(repo, "diff", "--cached", "--no-renames", "--name-only", "-z", "--diff-filter=ACMRTD", base, *selector)
    elif args.head:
        out = git(repo, "diff", "--no-renames", "--name-only", "-z", "--diff-filter=ACMRTD", f"{base}..{args.head}", *selector)
    else:
        out = git(repo, "diff", "--no-renames", "--name-only", "-z", "--diff-filter=ACMRTD", base, *selector)
        untracked = git(repo, "ls-files", "--others", "--exclude-standard", "-z", *selector)
        out = out + untracked
    return sorted({path for path in out.split("\0") if path})


def changed_diff(args: argparse.Namespace, paths: list[str]) -> str:
    repo = args.repo_root
    explicit_paths = [repo_relative(repo, path) for path in args.paths]
    selector = ["--", *explicit_paths] if explicit_paths else []
    if args.staged:
        return git(repo, "diff", "--cached", "--find-renames=20%", "--unified=0", args.base, *selector)
    if args.head:
        return git(repo, "diff", "--find-renames=20%", "--unified=0", f"{args.base}..{args.head}", *selector)
    return git(repo, "diff", "--find-renames=20%", "--unified=0", args.base, *selector)


def changed_lines_by_path(diff_text: str, *, keep_sign: bool = False) -> dict[str, list[str]]:
    current = ""
    lines: dict[str, list[str]] = {}
    for line in diff_text.splitlines():
        if line.startswith("diff --git "):
            parts = line.split()
            if len(parts) >= 4:
                current = parts[3][2:] if parts[3].startswith("b/") else parts[3]
            continue
        if line.startswith("+++ "):
            path = line[4:].strip()
            if path.startswith("b/"):
                current = path[2:]
            continue
        if not current or line.startswith(("+++", "---", "@@")):
            continue
        if not (line.startswith("+") or line.startswith("-")):
            continue
        lines.setdefault(current, []).append(line if keep_sign else line[1:])
    return lines


def changed_lines_for_path(path: str, changed_by_path: dict[str, list[str]]) -> list[str]:
    lines: list[str] = []
    path_candidates = set(candidate_paths(path))
    for diff_path, diff_lines in changed_by_path.items():
        if path_candidates & set(candidate_paths(diff_path)):
            lines.extend(diff_lines)
    return lines


def estate_only_script_change(path: str, changed_by_path: dict[str, list[str]]) -> bool:
    if not run_or_gate_script(path):
        return False
    lines = changed_lines_for_path(path, changed_by_path)
    return bool(lines) and all(ESTATE_ONLY_LINE_RE.match(line) for line in lines)


def host_gate_copyback_only_script_change(path: str, changed_by_path: dict[str, list[str]]) -> bool:
    if not any(candidate == "scripts/agents/triumvirate-gate.sh" for candidate in candidate_paths(path)):
        return False
    lines = [line.strip() for line in changed_lines_for_path(path, changed_by_path)]
    return (
        bool(lines)
        and any(HOST_GATE_COPYBACK_DISTINCTIVE_LINE_RE.search(line) for line in lines)
        and all(line in HOST_GATE_COPYBACK_ALLOWED_LINES for line in lines)
    )


def valid_relocated_script(args: argparse.Namespace, path: str) -> bool:
    """Exempt only supported files whose final root is correct for that file."""
    is_graph_run = path in {prefix + "run.sh" for prefix in GRAPH_PREFIXES}
    if not is_graph_run and path != "scripts/agents/triumvirate-gate.sh":
        return False
    final = comparison_blob(args, path)
    if is_graph_run:
        locations = [prefix + "run.sh" for prefix in reversed(GRAPH_PREFIXES)]
        origins = [location for location in locations if git_blob(args.repo_root, args.base, location) is not None]
        destinations = [(location, blob) for location in locations if (blob := comparison_blob(args, location)) is not None]
        if len(origins) != 1 or len(destinations) != 1:
            return False
        destination, final = destinations[0]
        if destination == locations[0] or locations.index(destination) < locations.index(origins[0]):
            return False
    if final is None:
        return False
    lines = [line.strip() for line in final.decode("utf-8").splitlines()]
    if is_graph_run:
        return [line for line in lines if line.startswith("REPO_ROOT=")] == [
            'REPO_ROOT="$(cd "$EXP_DIR/.." && pwd)"',
        ]
    if path == "scripts/agents/triumvirate-gate.sh":
        before = git_blob(args.repo_root, args.base, path)
        if before is None:
            return False
        locations = ['EXP_DIR="$ROOT/' + prefix.rstrip("/") + '"' for prefix in reversed(GRAPH_PREFIXES)]
        old = [line.strip() for line in before.decode("utf-8").splitlines() if line.strip().startswith("EXP_DIR=")]
        new = [line for line in lines if line.startswith("EXP_DIR=")]
        return len(old) == len(new) == 1 and old[0] in locations and new[0] in locations and locations.index(old[0]) < locations.index(new[0])
    return False


def graph_root_relocation_only_script_change(path: str, changed_by_path: dict[str, list[str]], args: argparse.Namespace) -> bool:
    if not valid_relocated_script(args, path):
        return False
    lines = [line.strip() for line in changed_lines_for_path(path, changed_by_path)]
    allowed = GRAPH_GATE_ROOT_RELOCATION_ALLOWED_LINES if path == "scripts/agents/triumvirate-gate.sh" else GRAPH_RUN_ROOT_RELOCATION_ALLOWED_LINES
    return (
        bool(lines)
        and any(GRAPH_ROOT_RELOCATION_DISTINCTIVE_LINE_RE.match(line) for line in lines)
        and all(line in allowed for line in lines)
    )


def third_party_mount_only_script_change(path: str, changed_by_path: dict[str, list[str]], args: argparse.Namespace) -> bool:
    if path not in {prefix + "run.sh" for prefix in GRAPH_PREFIXES} or not valid_relocated_script(args, path):
        return False
    lines = [line.strip() for line in changed_lines_for_path(path, changed_by_path)]
    # The same commit range may also carry the graph-root relocation lines.
    allowed = THIRD_PARTY_MOUNT_ALLOWED_LINES | GRAPH_RUN_ROOT_RELOCATION_ALLOWED_LINES
    return (
        bool(lines)
        and any(line in THIRD_PARTY_MOUNT_ALLOWED_LINES for line in lines)
        and all(line in allowed for line in lines)
    )


def graph_capture_path_relocation_only_script_change(path: str, changed_by_path: dict[str, list[str]]) -> bool:
    if not any(candidate == "scripts/agents/triumvirate-gate-insula.sh" for candidate in candidate_paths(path)):
        return False
    lines = changed_lines_for_path(path, changed_by_path)
    if not lines:
        return False

    removed: list[str] = []
    added: list[str] = []
    for line in lines:
        content = line[1:].strip()
        if line.startswith("+") and content.startswith("graphs/"):
            filename = content[len("graphs/") :]
            added.append(filename)
        elif line.startswith("-") and not content.startswith("graphs/"):
            filename = content
            removed.append(filename)
        else:
            return False
        if filename not in GRAPH_CAPTURE_RELOCATION_FILES:
            return False
    return bool(removed) and sorted(removed) == sorted(added)


def disk_cache_gc_only_script_change(path: str, changed_by_path: dict[str, list[str]]) -> bool:
    if not any(
        run_or_gate_script(candidate) or candidate == "tools/native_determinism_args.sh"
        for candidate in candidate_paths(path)
    ):
        return False
    lines = changed_lines_for_path(path, changed_by_path)
    return bool(lines) and all(DISK_CACHE_GC_LINE_RE.match(line) for line in lines)


def coreutils_offline_fetch_only_script_change(path: str, changed_by_path: dict[str, list[str]]) -> bool:
    if not any(candidate == "run.sh" for candidate in candidate_paths(path)):
        return False
    lines = changed_lines_for_path(path, changed_by_path)
    return (
        bool(lines)
        and any(COREUTILS_OFFLINE_FETCH_DISTINCTIVE_LINE_RE.search(line) for line in lines)
        and all(COREUTILS_OFFLINE_FETCH_LINE_RE.match(line) for line in lines)
    )


def leaf_action_offline_fetch_only_script_change(path: str, changed_by_path: dict[str, list[str]]) -> bool:
    if not any(candidate == "run.sh" for candidate in candidate_paths(path)):
        return False
    lines = changed_lines_for_path(path, changed_by_path)
    return (
        bool(lines)
        and any(LEAF_ACTION_OFFLINE_FETCH_DISTINCTIVE_LINE_RE.search(line) for line in lines)
        and all(LEAF_ACTION_OFFLINE_FETCH_LINE_RE.match(line) for line in lines)
    )


def consumer_conformance_gate_only_script_change(path: str, changed_by_path: dict[str, list[str]]) -> bool:
    if not any(candidate == "scripts/agents/triumvirate-gate-insula.sh" for candidate in candidate_paths(path)):
        return False
    lines = changed_lines_for_path(path, changed_by_path)
    return (
        bool(lines)
        and any(CONSUMER_CONFORMANCE_GATE_DISTINCTIVE_LINE_RE.search(line) for line in lines)
        and all(CONSUMER_CONFORMANCE_GATE_LINE_RE.match(line) for line in lines)
    )


def native_prefix_cquery_gate_only_script_change(path: str, changed_by_path: dict[str, list[str]]) -> bool:
    if not any(candidate == "scripts/agents/triumvirate-gate-insula.sh" for candidate in candidate_paths(path)):
        return False
    lines = changed_lines_for_path(path, changed_by_path)
    stripped = [line.strip() for line in lines]
    return (
        bool(stripped)
        and any(NATIVE_PREFIX_CQUERY_GATE_DISTINCTIVE_LINE_RE.search(line) for line in stripped)
        and all(line in NATIVE_PREFIX_CQUERY_GATE_ALLOWED_LINES for line in stripped)
    )


def native_prefix_path_query_build_only_change(path: str, changed_by_path: dict[str, list[str]]) -> bool:
    selected: tuple[str, str, str] | None = None
    for candidate in candidate_paths(path):
        for package_dir, config in NATIVE_PREFIX_QUERY_TARGETS.items():
            if candidate == f"native/{package_dir}/BUILD.bazel":
                selected = config
                break
        if selected is not None:
            break
    if selected is None:
        return False

    name, output_basename, profile = selected
    allowed = {
        "",
        'load("//native/common:native_output_path_query.bzl", "native_action_prefix_path")',
        "native_action_prefix_path(",
        f'name = "{name}",',
        f'output_basename = "{output_basename}",',
        f'profile = "{profile}",',
        'tags = ["manual"],',
        ")",
    }
    lines = [line.strip() for line in changed_lines_for_path(path, changed_by_path)]
    return bool(lines) and any("native_action_prefix_path" in line for line in lines) and all(
        line in allowed for line in lines
    )


def native_prefix_cquery_only_change(path: str, changed_by_path: dict[str, list[str]]) -> bool:
    return native_prefix_cquery_gate_only_script_change(
        path,
        changed_by_path,
    ) or native_prefix_path_query_build_only_change(path, changed_by_path)


def package_for_changed_path(path: str, manifest: dict[str, object] | None = None) -> str | None:
    package_dirs = manifest_package_dirs(manifest) if manifest is not None else PACKAGE_DIRS
    for package, directory in package_dirs.items():
        if any(candidate == directory or candidate.startswith(directory + "/") for candidate in candidate_paths(path)):
            return package
    return None


def repository_rule_native_definition_package(manifest: dict[str, object], path: str) -> str | None:
    for package, directory in manifest_package_dirs(manifest).items():
        for candidate in candidate_paths(path):
            if not (candidate == directory or candidate.startswith(directory + "/")):
                continue
            tail = candidate[len(directory):].lstrip("/")
            if not tail or "/" in tail:
                continue
            if tail in {"BUILD", "BUILD.bazel", "plan.py"} or tail.endswith(".bzl"):
                return package
    return None


def transitive_native_dependents(manifest: dict[str, object], package: str) -> list[str]:
    edges = manifest_dependency_edges(manifest)
    order = manifest_package_order(manifest)
    order_index = {item: index for index, item in enumerate(order)}
    seen: set[str] = set()
    queue = list(edges.get(package, []))
    while queue:
        current = queue.pop(0)
        if current in seen:
            continue
        seen.add(current)
        queue.extend(edges.get(current, []))
    return sorted(seen, key=lambda item: (order_index.get(item, len(order_index)), item))


def result_has_path(result: PackageResult, path: str) -> bool:
    return any(cause.get("path") == path for cause in result.causes)


def add_repository_rule_native_impacts(
    manifest: dict[str, object],
    results: dict[str, PackageResult],
    *,
    path: str,
) -> None:
    package = repository_rule_native_definition_package(manifest, path)
    if package is None:
        return
    affected = [package, *transitive_native_dependents(manifest, package)]
    for affected_package in affected:
        if affected_package not in results:
            results[affected_package] = PackageResult(
                package=affected_package,
                mnemonic=package_mnemonic(manifest, affected_package),
            )
        result = results[affected_package]
        if result_has_path(result, path):
            continue
        if affected_package == package:
            detail = "repository-rule native package definition changed"
        else:
            detail = f"depends on repository-rule native {package}"
        result.add(path=path, cause="flags", detail=detail, status="rebuild")


def run_or_gate_script(path: str) -> bool:
    return any(
        candidate in {
            "run.sh",
            "scripts/agents/triumvirate-gate-insula.sh",
            "scripts/agents/triumvirate-gate.sh",
        }
        for candidate in candidate_paths(path)
    )


def dev_module_file(path: str) -> bool:
    return any(candidate == "dev.MODULE.bazel" for candidate in candidate_paths(path))


def starlark_or_build_file(path: str) -> bool:
    return any(
        candidate.endswith(".bzl") or candidate.endswith(".bazel") or candidate.endswith("/BUILD")
        for candidate in candidate_paths(path)
    )


def add_common_hazard(results: dict[str, PackageResult], *, path: str, cause: str, detail: str) -> None:
    for result in results.values():
        result.add(path=path, cause=cause, detail=detail, status="may-rebuild")


def add_generated_prefix_manifest_hazard(results: dict[str, PackageResult], *, path: str) -> None:
    for package in GENERATED_PREFIX_MANIFEST_PACKAGES:
        results[package].add(
            path=path,
            cause="other",
            detail=GENERATED_PREFIX_MANIFEST_DETAIL,
            status="rebuild",
        )


def dev_module_leaf_action_archive_packages(lines: list[str]) -> set[str] | None:
    significant = [line.strip() for line in lines if line.strip() and not line.lstrip().startswith("#")]
    if not significant:
        return set()
    packages: set[str] = set()
    for line in significant:
        matched = {
            package
            for package, markers in LEAF_ACTION_ARCHIVE_MARKERS.items()
            if any(marker in line for marker in markers)
        }
        if matched:
            packages.update(matched)
            continue
        if line in {"http_file(", "dev_dependency = True,", ")"}:
            continue
        if BZLMOD_SOURCE_REPO_RE.search(line):
            return None
        return None
    return packages or None


def scan_diff_hazards(diff_text: str, results: dict[str, PackageResult], manifest: dict[str, object]) -> None:
    changed_by_path = changed_lines_by_path(diff_text)
    signed_by_path = changed_lines_by_path(diff_text, keep_sign=True)
    action_archive_dev_module_paths: set[str] = set()
    for path, lines in changed_by_path.items():
        if native_prefix_cquery_only_change(path, changed_by_path):
            continue
        if host_gate_copyback_only_script_change(path, changed_by_path):
            continue
        if graph_capture_path_relocation_only_script_change(path, signed_by_path):
            continue
        if not dev_module_file(path):
            continue
        packages = dev_module_leaf_action_archive_packages(lines)
        if packages is None:
            continue
        action_archive_dev_module_paths.add(path)
        for package in packages:
            results[package].add(
                path=path,
                cause="source",
                detail=f"{package} action source archive pin changed",
                status="rebuild",
            )
            if package == "zstd":
                results["pytorch"].add(
                    path=path,
                    cause="source",
                    detail="PyTorch consumes the zstd action prefix key",
                    status="rebuild",
                )

    current = ""
    for line in diff_text.splitlines():
        if line.startswith("diff --git "):
            parts = line.split()
            if len(parts) >= 4:
                current = parts[3][2:] if parts[3].startswith("b/") else parts[3]
            continue
        if line.startswith("+++ "):
            path = line[4:].strip()
            if path.startswith("b/"):
                current = path[2:]
            continue
        if not current or line.startswith(("+++", "---")):
            continue
        if not (line.startswith("+") or line.startswith("-")):
            continue
        changed = line[1:]
        if native_prefix_cquery_only_change(current, changed_by_path):
            continue
        if host_gate_copyback_only_script_change(current, changed_by_path):
            continue
        if graph_capture_path_relocation_only_script_change(current, signed_by_path):
            continue
        if dev_module_file(current) and current in action_archive_dev_module_paths:
            continue
        if dev_module_file(current) and not changed.lstrip().startswith("#") and BZLMOD_SOURCE_REPO_RE.search(changed):
            add_generated_prefix_manifest_hazard(results, path=current)
        if run_or_gate_script(current) and ENV_FLAG_RE.search(changed):
            add_common_hazard(
                results,
                path=current,
                cause="other",
                detail=f"Bazel environment option changed: {ENV_FLAG_RE.search(changed).group(0)}",
            )
        if PER_COMMIT_RE.search(changed) and any(
            candidate in {
                "tools/native_determinism_args.sh",
                "native/common/native_action_toolchain.bzl",
                "run.sh",
                "scripts/agents/triumvirate-gate-insula.sh",
                "scripts/agents/triumvirate-gate.sh",
            }
            for candidate in candidate_paths(current)
        ):
            add_common_hazard(
                results,
                path=current,
                cause="other",
                detail="per-commit determinism input changed: seed/source date/git sha",
            )
        if line.startswith("+") and starlark_or_build_file(current) and "use_default_shell_env" in changed and "True" in changed:
            package = package_for_changed_path(current, manifest)
            if package is None:
                add_common_hazard(
                    results,
                    path=current,
                    cause="other",
                    detail="new use_default_shell_env=True can admit ambient action_env into native keys",
                )
            else:
                results[package].add(
                    path=current,
                    cause="other",
                    detail="new use_default_shell_env=True can admit ambient action_env into native keys",
                    status="may-rebuild",
                )


def fast_impact(args: argparse.Namespace, manifest: dict[str, object]) -> dict[str, object]:
    paths = [path for path in changed_paths(args) if not path_only_graph_root_relocation(args, path)]
    diff_text = changed_diff(args, paths) if paths else ""
    changed_by_path = changed_lines_by_path(diff_text)
    signed_by_path = changed_lines_by_path(diff_text, keep_sign=True)
    results = {
        package: PackageResult(
            package=package,
            mnemonic=package_mnemonic(manifest, package),
        )
        for package in manifest_package_order(manifest)
    }
    for path in paths:
        if native_prefix_cquery_only_change(path, changed_by_path):
            continue
        if host_gate_copyback_only_script_change(path, changed_by_path):
            continue
        if graph_root_relocation_only_script_change(path, changed_by_path, args):
            continue
        if third_party_mount_only_script_change(path, changed_by_path, args):
            continue
        if graph_capture_path_relocation_only_script_change(path, signed_by_path):
            continue
        for package, result in results.items():
            for rule in rules_for_package(manifest, package):
                if path_matches(rule, path):
                    if graph_root_relocation_only_script_change(path, changed_by_path, args):
                        continue
                    if third_party_mount_only_script_change(path, changed_by_path, args):
                        continue
                    if graph_capture_path_relocation_only_script_change(path, signed_by_path):
                        continue
                    if estate_only_script_change(path, changed_by_path) and run_or_gate_script(path):
                        continue
                    if disk_cache_gc_only_script_change(path, changed_by_path):
                        continue
                    if coreutils_offline_fetch_only_script_change(path, changed_by_path):
                        continue
                    if leaf_action_offline_fetch_only_script_change(path, changed_by_path):
                        continue
                    if consumer_conformance_gate_only_script_change(path, changed_by_path):
                        continue
                    result.add(path=path, cause=rule.cause, detail=rule.detail, status=rule.status)
        add_repository_rule_native_impacts(manifest, results, path=path)
    if paths:
        scan_diff_hazards(diff_text, results, manifest)
    return make_report("fast", results, changed_paths=paths, base=args.base, head=head_label(args))


def head_label(args: argparse.Namespace) -> str:
    if args.staged:
        return "staged"
    if args.head:
        return args.head
    return "worktree"


def classify_path(path: str, manifest: dict[str, object] | None = None, package: str | None = None) -> str:
    if manifest is not None and package is not None:
        for rule in rules_for_package(manifest, package):
            if path_matches(rule, path):
                return rule.cause
    candidates = candidate_paths(path)
    if any(candidate.startswith("toolchains/") or candidate == "rootfs/cuda_ecosystem.lock.json" for candidate in candidates):
        return "toolchain"
    if any(candidate.startswith("locks/") or candidate.endswith("upstream_pins.json") for candidate in candidates):
        return "source"
    if any("/patches/" in candidate or candidate.endswith("-submodules.json") or candidate.endswith("nested_http_archives.json") for candidate in candidates):
        return "source"
    if any(
        candidate.endswith("plan.py")
        or candidate.endswith(".plan.json")
        or candidate.endswith("BUILD.bazel")
        for candidate in candidates
    ):
        return "flags"
    if any(run_or_gate_script(candidate) or candidate == "tools/native_determinism_args.sh" for candidate in candidates):
        return "flags"
    return "other"


def classify_arg_or_env(name: str, value: str) -> str:
    joined = f"{name}={value}"
    if ENV_FLAG_RE.search(joined):
        return "other"
    if PER_COMMIT_RE.search(joined) or JOB_RE.search(joined) or PATH_LEAK_RE.search(joined):
        return "other"
    if TOOLCHAIN_RE.search(joined):
        return "toolchain"
    if FLAG_RE.search(joined):
        return "flags"
    return "flags"


def _id(value: object) -> str:
    return str(value)


def path_fragments(doc: dict[str, object]) -> dict[str, tuple[str, str | None]]:
    out: dict[str, tuple[str, str | None]] = {}
    for item in _as_list(doc.get("pathFragments")):
        if not isinstance(item, dict) or "id" not in item:
            continue
        label = str(item.get("label") or "")
        parent = item.get("parentId")
        out[_id(item["id"])] = (label, _id(parent) if parent is not None else None)
    return out


def resolve_fragment(fragment_id: str, fragments: dict[str, tuple[str, str | None]]) -> str:
    parts: list[str] = []
    seen: set[str] = set()
    current: str | None = fragment_id
    while current and current in fragments and current not in seen:
        seen.add(current)
        label, parent = fragments[current]
        if label:
            parts.append(label)
        current = parent
    return "/".join(reversed(parts))


def artifact_paths(doc: dict[str, object]) -> dict[str, str]:
    fragments = path_fragments(doc)
    out: dict[str, str] = {}
    for item in _as_list(doc.get("artifacts")):
        if not isinstance(item, dict) or "id" not in item:
            continue
        path = item.get("execPath") or item.get("path")
        if not path and item.get("pathFragmentId") is not None:
            path = resolve_fragment(_id(item["pathFragmentId"]), fragments)
        if path:
            out[_id(item["id"])] = str(path)
    return out


def depset_inputs(doc: dict[str, object]) -> dict[str, frozenset[str]]:
    artifacts = artifact_paths(doc)
    dep_sets = {
        _id(item["id"]): item
        for item in _as_list(doc.get("depSetOfFiles"))
        if isinstance(item, dict) and "id" in item
    }
    memo: dict[str, frozenset[str]] = {}

    def visit(dep_id: str) -> frozenset[str]:
        if dep_id in memo:
            return memo[dep_id]
        item = dep_sets.get(dep_id, {})
        paths: set[str] = set()
        for artifact_id in _as_list(item.get("directArtifactIds")):
            path = artifacts.get(_id(artifact_id))
            if path:
                paths.add(path)
        for child in _as_list(item.get("transitiveDepSetIds")):
            paths.update(visit(_id(child)))
        memo[dep_id] = frozenset(paths)
        return memo[dep_id]

    return {dep_id: visit(dep_id) for dep_id in dep_sets}


def target_labels(doc: dict[str, object]) -> dict[str, str]:
    return {
        _id(item["id"]): str(item.get("label") or "")
        for item in _as_list(doc.get("targets"))
        if isinstance(item, dict) and "id" in item
    }


def env_map(action: dict[str, object]) -> dict[str, str]:
    out: dict[str, str] = {}
    for item in _as_list(action.get("environmentVariables")):
        if isinstance(item, dict):
            key = item.get("name", item.get("key"))
            if key is not None:
                out[str(key)] = str(item.get("value") or "")
    return out


def parse_aquery(path: Path, content_context: InputContentContext | None = None) -> dict[str, ActionSnapshot]:
    with path.open(encoding="utf-8") as handle:
        doc = json.load(handle)
    if not isinstance(doc, dict):
        raise SystemExit(f"{path} must contain a JSON object")
    labels = target_labels(doc)
    dep_inputs = depset_inputs(doc)
    by_mnemonic: dict[str, list[ActionSnapshot]] = {}
    for action in _as_list(doc.get("actions")):
        if not isinstance(action, dict):
            continue
        mnemonic = str(action.get("mnemonic") or "")
        if mnemonic not in PACKAGE_BY_MNEMONIC:
            continue
        inputs: set[str] = set()
        for dep_id in _as_list(action.get("inputDepSetIds")):
            inputs.update(dep_inputs.get(_id(dep_id), frozenset()))
        input_digests: dict[str, str] = {}
        unverified_inputs: dict[str, str] = {}
        if content_context is not None:
            input_digests, unverified_inputs = content_context.digest_inputs(inputs)
        target_id = action.get("targetId")
        label = labels.get(_id(target_id), "") if target_id is not None else ""
        by_mnemonic.setdefault(mnemonic, []).append(
            ActionSnapshot(
                mnemonic=mnemonic,
                label=label,
                action_key=str(action.get("actionKey") or "") or None,
                inputs=frozenset(inputs),
                arguments=tuple(str(arg) for arg in _as_list(action.get("arguments"))),
                env=env_map(action),
                input_digests=input_digests,
                unverified_inputs=unverified_inputs,
            )
        )
    snapshots: dict[str, ActionSnapshot] = {}
    for mnemonic, actions in by_mnemonic.items():
        actions = sorted(actions, key=lambda item: item.label)
        if len(actions) == 1:
            snapshots[mnemonic] = actions[0]
            continue
        digest = hashlib.sha256()
        inputs: set[str] = set()
        arguments: list[str] = []
        env: dict[str, str] = {}
        input_digests: dict[str, str] = {}
        unverified_inputs: dict[str, str] = {}
        for action in actions:
            digest.update((action.label + "\0" + str(action.action_key) + "\0").encode("utf-8"))
            inputs.update(action.inputs)
            for path, input_digest in sorted(action.input_digests.items()):
                digest.update((path + "\0" + input_digest + "\0").encode("utf-8"))
                input_digests[path] = input_digest
            for path, reason in sorted(action.unverified_inputs.items()):
                digest.update((path + "\0" + reason + "\0").encode("utf-8"))
                unverified_inputs[path] = reason
            arguments.extend(action.arguments)
            for key, value in action.env.items():
                env[f"{action.label}:{key}"] = value
        snapshots[mnemonic] = ActionSnapshot(
            mnemonic=mnemonic,
            label=",".join(action.label for action in actions),
            action_key=digest.hexdigest(),
            inputs=frozenset(inputs),
            arguments=tuple(arguments),
            env=env,
            input_digests=input_digests,
            unverified_inputs=unverified_inputs,
        )
    return snapshots


def first_diff(before: Iterable[str], after: Iterable[str]) -> tuple[list[str], list[str]]:
    before_list = list(before)
    after_list = list(after)
    before_set = set(before_list)
    after_set = set(after_list)
    removed = [item for item in before_list if item not in after_set]
    added = [item for item in after_list if item not in before_set]
    return removed, added


def add_input_content_causes(
    result: PackageResult,
    before: ActionSnapshot,
    after: ActionSnapshot,
    manifest: dict[str, object],
    package: str,
) -> None:
    for path in sorted(before.inputs & after.inputs):
        old = before.input_digests.get(path)
        new = after.input_digests.get(path)
        if old is not None and new is not None and old != new:
            result.add(
                path=path,
                cause=classify_path(path, manifest, package),
                detail=f"input content changed: {path}",
            )
    unverified = sorted(set(before.unverified_inputs) | set(after.unverified_inputs))
    if not unverified:
        return
    base_count = sum(1 for path in unverified if path in before.unverified_inputs)
    head_count = sum(1 for path in unverified if path in after.unverified_inputs)
    examples = ", ".join(unverified[:5])
    if len(unverified) > 5:
        examples += f", ... (+{len(unverified) - 5} more)"
    result.add(
        path=before.label or after.label,
        cause="other",
        detail=(
            f"unverified-content: {len(unverified)} input(s) could not be digested "
            f"(base={base_count}, head={head_count}; examples: {examples})"
        ),
        status="may-rebuild",
    )


def compare_exact_actions(
    base_actions: dict[str, ActionSnapshot],
    head_actions: dict[str, ActionSnapshot],
    manifest: dict[str, object],
    *,
    insula: bool,
) -> dict[str, object]:
    results = {
        package: PackageResult(package=package, mnemonic=package_mnemonic(manifest, package))
        for package in manifest_package_order(manifest)
    }
    for package, result in results.items():
        if result.mnemonic not in PACKAGE_BY_MNEMONIC:
            continue
        mnemonic = result.mnemonic
        before = base_actions.get(mnemonic)
        after = head_actions.get(mnemonic)
        if before is None and after is None:
            if not insula and package not in HOST_EXACT_PACKAGES:
                result.status = "needs-insula"
                result.note = "host aquery cannot analyse this native action; rerun --exact --insula inside foundry/insula.sh"
            else:
                result.add(path=mnemonic, cause="other", status="may-rebuild",
                           detail="native action is missing from both snapshots; absence is not proof of unchanged inputs")
            continue
        result.action_key = {
            "base": before.action_key if before is not None else None,
            "head": after.action_key if after is not None else None,
        }
        if before is None or after is None:
            result.add(
                path=result.label if hasattr(result, "label") else "",
                cause="other",
                detail="native action is present on only one side of the comparison",
            )
            continue
        for path in sorted(before.inputs.symmetric_difference(after.inputs)):
            result.add(
                path=path,
                cause=classify_path(path, manifest, package),
                detail=f"input changed: {path}",
            )
        add_input_content_causes(result, before, after, manifest, package)
        removed_args, added_args = first_diff(before.arguments, after.arguments)
        if removed_args or added_args:
            detail = "args changed: " + "; ".join(
                [*(f"- {arg}" for arg in removed_args[:5]), *(f"+ {arg}" for arg in added_args[:5])]
            )
            cause = "other" if any(classify_arg_or_env("arg", value) == "other" for value in removed_args + added_args) else "flags"
            result.add(path=before.label or after.label, cause=cause, detail=detail)
        keys = sorted(set(before.env) | set(after.env))
        for key in keys:
            old = before.env.get(key)
            new = after.env.get(key)
            if old == new:
                continue
            result.add(
                path=before.label or after.label,
                cause=classify_arg_or_env(key, f"{old}->{new}"),
                detail=f"env {key} changed: {old} -> {new}",
            )
        if before.action_key != after.action_key and not result.causes:
            result.add(
                path=before.label or after.label,
                cause="other",
                detail="action key changed without visible input, argument, or environment diff",
            )
    return make_report("exact", results, changed_paths=[], base="", head="")


def make_report(
    mode: str,
    results: dict[str, PackageResult],
    *,
    changed_paths: list[str],
    base: str,
    head: str,
) -> dict[str, object]:
    package_order = list(results)
    package_docs = {package: results[package].as_json() for package in package_order}
    cause_classes = [
        cause["class"]
        for result in results.values()
        for cause in result.causes
    ]
    statuses = [result.status for result in results.values()]
    exit_code = EXIT_UNCHANGED
    if any(cause == "other" for cause in cause_classes):
        exit_code = EXIT_OTHER_REBUILD
    elif any(status in {"may-rebuild", "rebuild"} for status in statuses):
        exit_code = EXIT_ALLOWED_REBUILD
    highest_cause = "none"
    if cause_classes:
        highest_cause = max(cause_classes, key=lambda item: CAUSE_RANK.get(item, 0))
    highest_status = max(statuses, key=lambda item: STATUS_RANK.get(item, 0)) if statuses else "unchanged"
    return {
        "mode": mode,
        "base": base,
        "head": head,
        "changed_paths": changed_paths,
        "packages": package_docs,
        "summary": {
            "exit_code": exit_code,
            "highest_status": highest_status,
            "highest_cause_class": highest_cause,
        },
    }


def exact_content_contexts(
    args: argparse.Namespace,
    *,
    base_tree: Path | None = None,
    head_tree: Path | None = None,
    base_output_base: Path | None = None,
    head_output_base: Path | None = None,
) -> tuple[InputContentContext, InputContentContext]:
    base_context = InputContentContext(
        repo_root=args.repo_root,
        ref=args.base or None,
        tree=base_tree,
        output_base=base_output_base,
    )
    head_ref = args.head if args.head else None
    if head_tree is None and head_ref is None and getattr(args, "worktree", False):
        head_tree = graph_dir(args.repo_root)
    head_context = InputContentContext(
        repo_root=args.repo_root,
        ref=head_ref,
        tree=head_tree,
        output_base=head_output_base,
    )
    return base_context, head_context


def exact_from_json(args: argparse.Namespace, manifest: dict[str, object]) -> dict[str, object]:
    assert args.aquery_json_base is not None and args.aquery_json_head is not None
    base_context, head_context = exact_content_contexts(
        args,
        base_output_base=getattr(args, "aquery_output_base_base", None),
        head_output_base=getattr(args, "aquery_output_base_head", None),
    )
    base_actions = parse_aquery(args.aquery_json_base, base_context)
    head_actions = parse_aquery(args.aquery_json_head, head_context)
    report = compare_exact_actions(
        base_actions,
        head_actions,
        manifest,
        insula=args.insula,
    )
    report["base"] = str(args.aquery_json_base)
    report["head"] = str(args.aquery_json_head)
    if getattr(args, "snapshot_dir", None):
        report["snapshots"] = write_action_snapshots(args.snapshot_dir, base_actions, head_actions)
    return report


def write_action_snapshots(root: Path, base: dict[str, ActionSnapshot], head: dict[str, ActionSnapshot]) -> dict[str, str]:
    """Retain the actual signatures and input contents used by an exact check."""
    if root.exists():
        raise SystemExit(f"snapshot evidence directory already exists: {root}")
    root.mkdir(parents=True, exist_ok=False)
    paths = {}
    for side, actions in (("base", base), ("head", head)):
        data = {name: {
            "label": value.label, "action_key": value.action_key,
            "arguments": list(value.arguments), "env": value.env,
            "inputs": sorted(value.inputs), "input_digests": value.input_digests,
            "unverified_inputs": value.unverified_inputs,
        } for name, value in sorted(actions.items())}
        path = root / f"{side}-actions.json"
        path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        paths[side] = str(path)
    return paths


def bazel_binary(args: argparse.Namespace) -> str:
    if args.bazel:
        return str(args.bazel)
    env = os.environ.get("BAZEL_BIN") or os.environ.get("BAZEL_REAL")
    if env:
        return env
    return "/opt/vaso/bin/bazel-real"


def default_output_root(args: argparse.Namespace) -> Path:
    if args.output_root:
        return args.output_root
    estate = os.environ.get("VASO_ESTATE_ROOT")
    agent = os.environ.get("VASO_AGENT") or getpass.getuser() or "rebuild-impact"
    if estate:
        return Path(estate) / "agents" / agent / "rebuild-impact"
    return args.repo_root / ".scratch" / "rebuild-impact"


def materialize_ref(repo_root: Path, ref: str, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    proc = subprocess.Popen(
        ["git", "-C", str(repo_root), "archive", "--format=tar", ref],
        stdout=subprocess.PIPE,
    )
    assert proc.stdout is not None
    try:
        with tarfile.open(fileobj=proc.stdout, mode="r|") as archive:
            archive.extractall(destination)
    finally:
        proc.stdout.close()
    rc = proc.wait()
    if rc != 0:
        raise SystemExit(f"git archive {ref} failed")


def exact_targets(manifest: dict[str, object], *, insula: bool) -> list[str]:
    targets: list[str] = []
    for package in manifest_package_order(manifest):
        package_doc = package_manifest(manifest, package)
        key = "insula_targets" if insula else "host_targets"
        for target in _as_list(package_doc.get(key)):
            if isinstance(target, str):
                targets.append(target)
    return targets


def build_target_exists(tree: Path, label: str) -> bool:
    if not label.startswith("//"):
        return True
    package, _, target = label[2:].partition(":")
    if not target:
        target = Path(package).name
    build_file = tree / package / "BUILD.bazel"
    if not build_file.exists():
        return False
    text = build_file.read_text(encoding="utf-8")
    return re.search(rf"name\s*=\s*['\"]{re.escape(target)}['\"]", text) is not None


def release_metadata(args: argparse.Namespace, ref: str | None) -> tuple[str, str]:
    if getattr(args, "release_metadata", "fixed") == "ref" and ref is not None:
        sha = git(args.repo_root, "rev-parse", ref).strip()
        epoch = git(args.repo_root, "show", "-s", "--format=%ct", sha).strip()
        if epoch.isdigit():
            return sha, epoch
    return "0000000000000000000000000000000000000000", "1700000000"


def fixed_native_settings(args: argparse.Namespace, tree: Path | None = None, ref: str | None = None) -> list[str]:
    seed, source_date_epoch = release_metadata(args, ref)
    settings = [
        f"--//native/common:release_line={args.line}",
        f"--//native/common:release_git_sha={seed}",
        f"--//native/common:source_date_epoch={source_date_epoch}",
        f"--//native/common:deterministic_seed={seed}",
        "--//native/common:deterministic_prefix_map_roots=/vaso-bazel-ob/execroot/_main,/vaso/rebuild-impact/work",
        "--//native/common:native_action_cuda_home=/vaso-bazel-ob/external/+cuda_native+cuda_native/prefix",
    ]
    if tree is None:
        return settings
    filtered: list[str] = []
    for setting in settings:
        match = BUILD_SETTING_RE.match(setting)
        if match is None or build_target_exists(tree, match.group("label")):
            filtered.append(setting)
    return filtered


def aquery_distdirs(args: argparse.Namespace) -> list[Path]:
    values: list[Path] = []
    estate = os.environ.get("VASO_ESTATE_ROOT")
    if estate:
        for relative in ESTATE_DISTDIRS:
            path = Path(estate) / relative
            if path.exists():
                values.append(path)
    values.extend(Path(path) for path in getattr(args, "distdir", []))

    deduped: list[Path] = []
    seen: set[str] = set()
    for path in values:
        if not path.is_absolute():
            path = (Path.cwd() / path).resolve()
        key = os.fspath(path)
        if key not in seen:
            seen.add(key)
            deduped.append(path)
    return deduped


def aquery_repository_cache(args: argparse.Namespace) -> Path | None:
    if args.repository_cache:
        return Path(args.repository_cache)
    estate = os.environ.get("VASO_ESTATE_ROOT")
    if not estate:
        return None
    path = Path(estate) / "vaso/cache/bazel/repository-cache"
    return path if path.exists() else None


def aquery_command(
    output_base: Path,
    args: argparse.Namespace,
    manifest: dict[str, object],
    tree: Path | None = None,
    ref: str | None = None,
) -> list[str]:
    targets = exact_targets(manifest, insula=args.insula)
    mnemonics = []
    for package in manifest_package_order(manifest):
        if not (args.insula or package in HOST_EXACT_PACKAGES):
            continue
        package_doc = package_manifest(manifest, package)
        key = "insula_targets" if args.insula else "host_targets"
        if not _as_list(package_doc.get(key)):
            continue
        mnemonic = package_mnemonic(manifest, package)
        if mnemonic in PACKAGE_BY_MNEMONIC:
            mnemonics.append(mnemonic)
    mnemonic = "|".join(mnemonics)
    expr = f'mnemonic("{mnemonic}", deps({" + ".join(targets)}))'
    command = [
        bazel_binary(args),
        f"--output_base={output_base}",
        "aquery",
        "--output=jsonproto",
        "--lockfile_mode=off",
        *fixed_native_settings(args, tree=tree, ref=ref),
    ]
    for distdir in aquery_distdirs(args):
        command.append(f"--distdir={distdir}")
    if args.registry:
        command.append(f"--registry={args.registry}")
    repository_cache = aquery_repository_cache(args)
    if repository_cache:
        command.append(f"--repository_cache={repository_cache}")
    if args.repository_disable_download:
        command.append("--repository_disable_download")
    command.append(expr)
    return command


def run_aquery(
    tree: Path,
    output_base: Path,
    out_json: Path,
    args: argparse.Namespace,
    manifest: dict[str, object],
    *,
    ref: str | None = None,
) -> None:
    if os.environ.get("VASO_IN_INSULA") != "1":
        raise SystemExit("Bazel aquery must run under insula; use foundry/run.sh --bazel-aquery or supply retained aquery JSON")
    if not exact_targets(manifest, insula=args.insula):
        out_json.write_text('{"actions":[],"targets":[],"depSetOfFiles":[],"artifacts":[]}\n', encoding="utf-8")
        return
    command = aquery_command(output_base, args, manifest, tree=tree, ref=ref)
    out_json.parent.mkdir(parents=True, exist_ok=True)
    with out_json.open("w", encoding="utf-8") as out:
        proc = subprocess.run(command, cwd=tree, text=True, stdout=out, stderr=subprocess.PIPE)
    if proc.returncode != 0:
        raise SystemExit(proc.stderr.strip() or f"bazel aquery failed rc={proc.returncode}")


def exact_by_running_aquery(args: argparse.Namespace, manifest: dict[str, object]) -> dict[str, object]:
    if not args.base:
        raise SystemExit("--exact requires --base unless aquery JSON files are supplied")
    root = default_output_root(args)
    run_id = f"{int(time.time())}-{os.getpid()}"
    work = root / run_id
    base_tree = work / "base-tree"
    head_tree = work / "head-tree"
    base_json = work / "base-aquery.json"
    head_json = work / "head-aquery.json"
    base_output_base = root / "aquery-ob-base"
    head_output_base = root / "aquery-ob-head"
    materialize_ref(args.repo_root, args.base, base_tree)
    if args.head:
        materialize_ref(args.repo_root, args.head, head_tree)
        head_source = graph_dir(head_tree)
    elif args.worktree:
        head_source = graph_dir(args.repo_root)
    else:
        raise SystemExit("--exact requires --head or --worktree unless aquery JSON files are supplied")
    run_aquery(graph_dir(base_tree), base_output_base, base_json, args, manifest, ref=args.base)
    run_aquery(head_source, head_output_base, head_json, args, manifest, ref=args.head or "HEAD")
    base_context, head_context = exact_content_contexts(
        args,
        base_tree=graph_dir(base_tree),
        head_tree=head_source,
        base_output_base=base_output_base,
        head_output_base=head_output_base,
    )
    base_actions = parse_aquery(base_json, base_context)
    head_actions = parse_aquery(head_json, head_context)
    report = compare_exact_actions(
        base_actions,
        head_actions,
        manifest,
        insula=args.insula,
    )
    report["base"] = args.base
    report["head"] = head_label(args)
    report["aquery"] = {"base": str(base_json), "head": str(head_json)}
    if getattr(args, "snapshot_dir", None):
        report["snapshots"] = write_action_snapshots(args.snapshot_dir, base_actions, head_actions)
    return report


def exact_impact(args: argparse.Namespace, manifest: dict[str, object]) -> dict[str, object]:
    if args.aquery_json_base or args.aquery_json_head:
        if not args.aquery_json_base or not args.aquery_json_head:
            raise SystemExit("--aquery-json-base and --aquery-json-head must be supplied together")
        return exact_from_json(args, manifest)
    return exact_by_running_aquery(args, manifest)


def merge_aquery_inputs_into_manifest(manifest: dict[str, object], aquery_json: Path) -> dict[str, object]:
    actions = parse_aquery(aquery_json)
    packages = manifest.get("packages")
    if not isinstance(packages, dict):
        return manifest
    for mnemonic, snapshot in actions.items():
        package = PACKAGE_BY_MNEMONIC.get(mnemonic)
        if package is None:
            continue
        package_doc = packages.get(package)
        if not isinstance(package_doc, dict):
            continue
        existing = input_rules(package_doc.get("inputs", []))
        seen = {(rule.path, rule.glob) for rule in existing}
        generated = list(_as_list(package_doc.get("inputs")))
        for path in sorted(snapshot.inputs):
            if path.startswith("../") or path.startswith("external/") or path.startswith("bazel-out/"):
                continue
            candidates = candidate_paths(path)
            graph_path = candidates[1] if graph_relative_path(candidates[0]) is not None else candidates[0]
            if not graph_path.startswith(("native/", "tools/", "run.sh", "scripts/agents/", "rootfs/", "toolchains/", "locks/")):
                continue
            key = (graph_path, None)
            if key in seen:
                continue
            seen.add(key)
            generated.append({
                "path": graph_path,
                "cause": classify_path(graph_path, manifest, package),
                "status": "rebuild",
                "detail": "declared native action input from aquery",
            })
        package_doc["inputs"] = generated
    return manifest


def refresh_manifest(args: argparse.Namespace, manifest: dict[str, object]) -> dict[str, object]:
    doc = default_manifest()
    if args.aquery_json_head:
        doc = merge_aquery_inputs_into_manifest(doc, args.aquery_json_head)
    elif not args.no_aquery:
        output_root = default_output_root(args)
        output_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=str(output_root)) as tmp:
            out_json = Path(tmp) / "aquery.json"
            run_aquery(graph_dir(args.repo_root), Path(tmp) / "aquery-ob", out_json, args, doc)
            doc = merge_aquery_inputs_into_manifest(doc, out_json)
    target = args.manifest
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {"mode": "refresh-manifest", "manifest": str(target), "summary": {"exit_code": 0}}


def print_text(report: dict[str, object]) -> None:
    summary = report.get("summary", {})
    print(
        f"REBUILD_IMPACT mode={report.get('mode')} base={report.get('base', '')} "
        f"head={report.get('head', '')} exit={summary.get('exit_code')} "
        f"highest={summary.get('highest_status')} cause={summary.get('highest_cause_class')}"
    )
    packages = report.get("packages")
    if isinstance(packages, dict):
        raw_order = report.get("package_order")
        order = [item for item in _as_list(raw_order) if isinstance(item, str)]
        if not order:
            order = list(packages)
        for package in order:
            item = packages.get(package)
            if not isinstance(item, dict):
                continue
            if item.get("status") == "unchanged":
                continue
            print(f"PACKAGE {package} {item.get('status')}")
            note = item.get("note")
            if note:
                print(f"  note: {note}")
            action_key = item.get("action_key")
            if isinstance(action_key, dict):
                print(f"  action_key: {action_key.get('base')} -> {action_key.get('head')}")
            for cause in _as_list(item.get("causes")):
                if not isinstance(cause, dict):
                    continue
                print(f"  {cause.get('class')} {cause.get('path')}: {cause.get('detail')}")


def build_parser() -> argparse.ArgumentParser:
    graph_root = graph_root_from_tool()
    repo_root = repo_root_from_graph(graph_root)
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--fast", action="store_true", help="classify from Git diff paths")
    mode.add_argument("--exact", action="store_true", help="compare Bazel aquery action signatures")
    mode.add_argument("--refresh-manifest", action="store_true", help="refresh plans/native-inputs.json")
    parser.add_argument("--repo-root", type=Path, default=repo_root)
    parser.add_argument("--base", default="HEAD")
    parser.add_argument("--head")
    parser.add_argument("--worktree", action="store_true")
    parser.add_argument("--staged", action="store_true")
    parser.add_argument("--paths", nargs="*", default=[])
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--format", choices=("text", "json"), default="text")
    parser.add_argument("--aquery-json-base", type=Path)
    parser.add_argument("--aquery-json-head", type=Path)
    parser.add_argument("--aquery-output-base-base", type=Path, help="materialized output base for base JSON input digests")
    parser.add_argument("--aquery-output-base-head", type=Path, help="materialized output base for head JSON input digests")
    parser.add_argument("--snapshot-dir", type=Path, help="new evidence directory retaining exact signatures and input digests")
    parser.add_argument("--package", action="append", default=[], help="limit the check to named packages; repeatable")
    parser.add_argument("--output-root", type=Path)
    parser.add_argument("--bazel", type=Path)
    parser.add_argument("--line", default="cu129", choices=("cu129", "cu130"))
    parser.add_argument("--insula", action="store_true", help="include native targets that require the insula")
    parser.add_argument("--registry", help="Bzlmod registry URL/path for exact aquery")
    parser.add_argument("--repository-cache", help="Bazel repository cache for exact aquery")
    parser.add_argument("--distdir", type=Path, action="append", default=[], help="additional Bazel distdir for exact aquery")
    parser.add_argument("--repository-disable-download", action="store_true", default=True)
    parser.add_argument("--no-repository-disable-download", dest="repository_disable_download", action="store_false")
    parser.add_argument(
        "--release-metadata",
        choices=("fixed", "ref"),
        default="fixed",
        help="use fixed placeholder release metadata, or per-ref metadata for calibration",
    )
    parser.add_argument("--no-aquery", action="store_true", help="with --refresh-manifest, write static defaults only")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    args.repo_root = args.repo_root.resolve()
    if args.manifest is None:
        args.manifest = graph_dir(args.repo_root) / "plans/native-inputs.json"
    elif not args.manifest.is_absolute():
        args.manifest = args.repo_root / args.manifest
    if not args.fast and not args.exact and not args.refresh_manifest:
        args.fast = True
    if args.head and (args.worktree or args.staged):
        raise SystemExit("--head cannot be combined with --worktree or --staged")
    if args.staged and args.worktree:
        raise SystemExit("--staged cannot be combined with --worktree")
    if not args.head and not args.staged:
        args.worktree = True
    if args.refresh_manifest:
        report = refresh_manifest(args, default_manifest())
    else:
        manifest = load_manifest(args.manifest)
        if args.package:
            packages = manifest.get("packages", {})
            unknown = sorted(set(args.package) - set(packages))
            if unknown:
                raise SystemExit("unknown native packages: " + ", ".join(unknown))
            manifest = {**manifest, "package_order": list(dict.fromkeys(args.package)),
                        "packages": {name: packages[name] for name in dict.fromkeys(args.package)}}
        report = fast_impact(args, manifest) if args.fast else exact_impact(args, manifest)
    if args.format == "json":
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print_text(report)
    summary = report.get("summary", {})
    return int(summary.get("exit_code") or 0)


if __name__ == "__main__":
    raise SystemExit(main())
