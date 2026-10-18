#!/usr/bin/env bash
# Shared derivation for tokened native-action release build settings.

native_determinism_resolve_release_git_sha() {
  local repo_root="$1"
  local git_sha_input="$2"
  local release_git_sha
  if ! release_git_sha="$(git -C "$repo_root" rev-parse "$git_sha_input" 2>/dev/null)"; then
    if [[ "$git_sha_input" =~ ^[0-9a-fA-F]{40}$ ]]; then
      release_git_sha="$git_sha_input"
    else
      echo "could not resolve release git sha: $git_sha_input" >&2
      return 2
    fi
  fi
  printf '%s\n' "$release_git_sha"
}

native_determinism_source_date_epoch() {
  local repo_root="$1"
  local release_git_sha="$2"
  local fallback_source_date_epoch="${3:-}"
  local source_date_epoch
  source_date_epoch="$(git -C "$repo_root" show -s --format=%ct "$release_git_sha" 2>/dev/null || true)"
  if [[ -z "$source_date_epoch" && -n "$fallback_source_date_epoch" ]]; then
    source_date_epoch="$fallback_source_date_epoch"
  fi
  if [[ ! "$source_date_epoch" =~ ^[0-9]+$ ]]; then
    echo "could not derive SOURCE_DATE_EPOCH from git sha $release_git_sha" >&2
    return 2
  fi
  printf '%s\n' "$source_date_epoch"
}

native_determinism_fixed_output_base_mount() {
  printf "%s\n" "/vaso-bazel-ob"
}

native_bazel_disk_cache_gc_max_size() { printf "%s\n" "80G"; }
native_bazel_disk_cache_gc_max_age() { printf "%s\n" "72h"; }
append_native_bazel_disk_cache_gc_args() { local -n out="$1"; out+=("--experimental_disk_cache_gc_max_size=$(native_bazel_disk_cache_gc_max_size)" "--experimental_disk_cache_gc_max_age=$(native_bazel_disk_cache_gc_max_age)"); }

native_action_cuda_home() {
  local output_base="$1"
  printf '%s\n' "$output_base/external/+cuda_native+cuda_native/prefix"
}

native_determinism_prefix_map_roots() {
  local output_base="$1"
  local native_work_dir="$2"
  printf '%s,%s\n' "$output_base/execroot/_main" "$native_work_dir"
}

append_native_determinism_bazel_args() {
  local -n out="$1"
  local release_line="$2"
  local repo_root="$3"
  local git_sha_input="$4"
  local output_base="$5"
  local native_work_dir="$6"
  local source_date_epoch_fallback="${7:-}"
  local release_git_sha source_date_epoch prefix_map_roots

  release_git_sha="$(native_determinism_resolve_release_git_sha "$repo_root" "$git_sha_input")" || return
  source_date_epoch="$(native_determinism_source_date_epoch "$repo_root" "$release_git_sha" "$source_date_epoch_fallback")" || return
  prefix_map_roots="$(native_determinism_prefix_map_roots "$output_base" "$native_work_dir")"
  out+=(
    --repo_env=CC=gcc
    --repo_env=CXX=g++
    "--//native/common:release_line=$release_line"
    "--//native/common:release_git_sha=$release_git_sha"
    "--//native/common:source_date_epoch=$source_date_epoch"
    "--//native/common:deterministic_seed=$release_git_sha"
    "--//native/common:deterministic_prefix_map_roots=$prefix_map_roots"
    "--//native/common:native_action_cuda_home=$(native_action_cuda_home "$output_base")"
  )
}

# Repository-rule builds (the native deps) get one fixed epoch, never the commit time:
# a re-run dep must produce identical bytes, or every consumer's key changes (FC-N1-5).
native_repo_source_date_epoch() { printf "%s\n" "315532800"; }
