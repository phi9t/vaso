#!/usr/bin/env bash
# Proves the Bazel-owned hermetic Spack tool runs. Bazel passes the runfiles
# path to the @spack_dist//:spack wrapper as $1.
set -euo pipefail
SPACK="$1"
VER="$("$SPACK" --version)"
echo "hermetic spack (Bazel-owned) version: $VER"
# The version string starts with the pinned release.
case "$VER" in
  1.2.2*) exit 0 ;;
  *) echo "unexpected spack version: $VER" >&2; exit 1 ;;
esac
