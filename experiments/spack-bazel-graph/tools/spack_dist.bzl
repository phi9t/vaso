"""Bazel-owned hermetic Spack distribution.

`spack_dist` fetches a pinned Spack release archive into a Bazel external repo
and exposes it as:

  - `@spack_dist//:spack`            a runnable `sh_binary` wrapper that invokes
                                     the vendored Spack with a hermetic,
                                     insula-relative config (no host ~/.spack,
                                     install tree under /vaso/cache);
  - `@spack_dist//:dist`             a filegroup of the whole Spack tree.

Because the archive is content-addressed by sha256 and the config is generated
here, the Spack *tool* is owned by the Bazel module graph, not by an ambient
host checkout. `vaso spack build` runs this wrapper inside the CUDA rootfs.
"""

_SPACK_WRAPPER = """\
#!/usr/bin/env bash
# GENERATED wrapper for the Bazel-vendored, hermetic Spack.
set -euo pipefail
SCRIPT="${BASH_SOURCE[0]}"
SELF="$(cd "$(dirname "$SCRIPT")" && pwd)"
SCRIPT_BASENAME="$(basename "$SCRIPT")"

# Hermetic, insula-relative Spack state. These default under /vaso (a vaso
# writable surface) but can be overridden by the caller/profile.
export SPACK_USER_CONFIG_PATH="${SPACK_USER_CONFIG_PATH:-/vaso/state/spack/user-config}"
export SPACK_USER_CACHE_PATH="${SPACK_USER_CACHE_PATH:-/vaso/cache/spack/user}"
unset SPACK_DISABLE_LOCAL_CONFIG
export SPACK_SOURCE_CACHE="${SPACK_SOURCE_CACHE:-/vaso/cache/spack/source-cache}"
export SPACK_MISC_CACHE="${SPACK_MISC_CACHE:-/vaso/cache/spack/misc-cache}"
mkdir -p \
  "$SPACK_USER_CONFIG_PATH" \
  "$SPACK_USER_CACHE_PATH" \
  "$SPACK_SOURCE_CACHE" \
  "$SPACK_MISC_CACHE" \
  /vaso/cache/spack/opt/spack \
  /vaso/tmp/spack-stage

find_runfile() {
  local rel="$1"
  local base
  for base in \
    "${RUNFILES_DIR:-}" \
    "${SCRIPT}.runfiles" \
    "${0}.runfiles" \
    "$SELF/$SCRIPT_BASENAME.runfiles" \
    "$SELF/spack.runfiles" \
    "$PWD"; do
    [[ -n "$base" && -d "$base" ]] || continue
    if [[ -e "$base/$rel" ]]; then
      echo "$base/$rel"
      return 0
    fi
  done
  return 1
}

DIST="$SELF/dist"
if [[ ! -x "$DIST/bin/spack" ]]; then
  DIST=""
  for rel in \
    "+spack_toolchain+spack_dist/dist/bin/spack" \
    "spack_dist/dist/bin/spack" \
    "vaso_spack_bazel_graph_synthetic/external/+spack_toolchain+spack_dist/dist/bin/spack"; do
    if hit="$(find_runfile "$rel")"; then
      DIST="$(cd "$(dirname "$hit")/.." && pwd)"
      break
    fi
  done
  if [[ -z "$DIST" ]]; then
    echo "failed to locate Spack dist in Bazel runfiles" >&2
    exit 2
  fi
fi

overlay_root="${VASO_SPACK_OVERLAY_ROOTS:-}"
if [[ -z "$overlay_root" ]]; then
  for rel in \
    "vaso_spack_bazel_graph_synthetic/spack_overlays/vaso/spack_repo/vaso_overlay/repo.yaml" \
    "_main/spack_overlays/vaso/spack_repo/vaso_overlay/repo.yaml" \
    "spack_overlays/vaso/spack_repo/vaso_overlay/repo.yaml"; do
    if hit="$(find_runfile "$rel")"; then
      overlay_root="$(cd "$(dirname "$hit")" && pwd)"
      break
    fi
  done
fi
if [[ -n "$overlay_root" ]]; then
  export VASO_SPACK_OVERLAY_ROOTS="$overlay_root"
  cat > "$SPACK_USER_CONFIG_PATH/repos.yaml" <<YAML
repos:
  vaso_overlay: $overlay_root
  builtin:
    git: https://github.com/spack/spack-packages.git
    branch: releases/v2026.06
YAML
fi

cat > "$SPACK_USER_CONFIG_PATH/config.yaml" <<'YAML'
config:
  install_tree:
    root: /vaso/cache/spack/opt/spack
  source_cache: /vaso/cache/spack/source-cache
  misc_cache: /vaso/cache/spack/misc-cache
  build_stage:
  - /vaso/tmp/spack-stage
YAML

cat > "$SPACK_USER_CONFIG_PATH/packages.yaml" <<'YAML'
packages:
  python:
    require: "@3.13.13"
YAML

if command -v gcc >/dev/null 2>&1 && command -v g++ >/dev/null 2>&1; then
  gcc_version="$(gcc -dumpfullversion -dumpversion)"
  os_id="linux"
  os_version=""
  if [[ -r /etc/os-release ]]; then
    . /etc/os-release
    os_id="${ID:-linux}"
    os_version="${VERSION_ID:-}"
  fi
  os_name="${os_id}${os_version}"
  if command -v gfortran >/dev/null 2>&1; then
    compiler_languages="c,c++,fortran"
    fortran_compilers="
          fortran: /usr/bin/gfortran
          f77: /usr/bin/gfortran
          fc: /usr/bin/gfortran"
  else
    compiler_languages="c,c++"
    fortran_compilers=""
  fi
  cat >> "$SPACK_USER_CONFIG_PATH/packages.yaml" <<YAML
  gcc:
    externals:
    - spec: "gcc@${gcc_version} languages='${compiler_languages}' os=${os_name}"
      prefix: /usr
      extra_attributes:
        compilers:
          c: /usr/bin/gcc
          cxx: /usr/bin/g++${fortran_compilers}
    buildable: false
YAML
fi

if [[ -n "${VASO_CUDA_HOME:-}" && -x "${VASO_CUDA_HOME}/bin/nvcc" ]]; then
  if [[ -z "${VASO_ROOTFS_BUNDLE_MANIFEST:-}" || ! -f "$VASO_ROOTFS_BUNDLE_MANIFEST" ]]; then
    echo "VASO_ROOTFS_BUNDLE_MANIFEST must name the selected CUDA rootfs manifest" >&2
    exit 2
  fi
  rootfs_externals_tool=""
  for rel in \
    "vaso_spack_bazel_graph_synthetic/tools/rootfs_spack_externals.py" \
    "_main/tools/rootfs_spack_externals.py" \
    "tools/rootfs_spack_externals.py"; do
    if hit="$(find_runfile "$rel")"; then
      rootfs_externals_tool="$hit"
      break
    fi
  done
  if [[ -z "$rootfs_externals_tool" ]]; then
    echo "failed to locate rootfs_spack_externals.py in Bazel runfiles" >&2
    exit 2
  fi
  cuda_ecosystem_lock=""
  for rel in \
    "vaso_spack_bazel_graph_synthetic/rootfs/cuda_ecosystem.lock.json" \
    "_main/rootfs/cuda_ecosystem.lock.json" \
    "rootfs/cuda_ecosystem.lock.json"; do
    if hit="$(find_runfile "$rel")"; then
      cuda_ecosystem_lock="$hit"
      break
    fi
  done
  if [[ -z "$cuda_ecosystem_lock" ]]; then
    echo "failed to locate rootfs/cuda_ecosystem.lock.json in Bazel runfiles" >&2
    exit 2
  fi
  if [[ ! -s "$SPACK_USER_CONFIG_PATH/packages.yaml" ]]; then
    printf 'packages:\\n' > "$SPACK_USER_CONFIG_PATH/packages.yaml"
  fi
  python3 "$rootfs_externals_tool" \
    --manifest "$VASO_ROOTFS_BUNDLE_MANIFEST" \
    --lock "$cuda_ecosystem_lock" \
    --cuda-prefix "$VASO_CUDA_HOME" \
    >> "$SPACK_USER_CONFIG_PATH/packages.yaml"
fi

exec "$DIST/bin/spack" "$@"
"""

_BUILD = """\
load("@rules_shell//shell:sh_binary.bzl", "sh_binary")

package(default_visibility = ["//visibility:public"])

filegroup(
    name = "dist",
    srcs = glob(["dist/**"], allow_empty = False),
)

sh_binary(
    name = "spack",
    srcs = ["spack_wrapper.sh"],
    data = [
        ":dist",
        "@//:rootfs/cuda_ecosystem.lock.json",
        "@//native/triton:patches/0001-llvm-35901313.patch",
        "@//spack_overlays/vaso:repo",
        "@//tools:rootfs_spack_externals.py",
    ],
)
"""

def _spack_dist_impl(repository_ctx):
    attr = repository_ctx.attr
    repository_ctx.download_and_extract(
        url = attr.urls,
        sha256 = attr.sha256,
        stripPrefix = attr.strip_prefix,
        output = "dist",
    )
    repository_ctx.file("spack_wrapper.sh", _SPACK_WRAPPER, executable = True)
    repository_ctx.file("BUILD.bazel", _BUILD)

spack_dist = repository_rule(
    implementation = _spack_dist_impl,
    attrs = {
        "urls": attr.string_list(mandatory = True),
        "sha256": attr.string(mandatory = True),
        "strip_prefix": attr.string(mandatory = True),
    },
)

def _spack_toolchain_impl(module_ctx):
    for mod in module_ctx.modules:
        for dist in mod.tags.dist:
            spack_dist(
                name = "spack_dist",
                urls = dist.urls,
                sha256 = dist.sha256,
                strip_prefix = dist.strip_prefix,
            )

_dist_tag = tag_class(attrs = {
    "urls": attr.string_list(mandatory = True),
    "sha256": attr.string(mandatory = True),
    "strip_prefix": attr.string(mandatory = True),
})

spack_toolchain = module_extension(
    implementation = _spack_toolchain_impl,
    tag_classes = {"dist": _dist_tag},
)
