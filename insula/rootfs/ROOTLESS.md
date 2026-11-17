# Rootless constructor stages

This opt-in backend runs canonical LLVM and CUDA installation bodies without a
Docker daemon. It does not restore a published capsule or replace the full
Foundry graph. Existing Docker construction remains the default.

Acquire and unpack the exact OCI base reference from the selected lock using a
trusted daemonless OCI tool. Layer whiteouts and ordering must be handled by the
unpacker, not by simply extracting layer tarballs. Use fresh private writable
trees, never an active capsule or host root. The supplied base reference is a
caller acquisition assertion, not an independent scan of the unpacked content.

LLVM uses components.llvm.build.base_image; CUDA uses lines.LINE.base_image.
These are distinct rootfs trees. A Debian host is fine; the locked guest OS is
Ubuntu 24.04. Put already-acquired LLVM source at WORK/llvm-project.tar.gz and
locked patches at WORK/llvm-patches/NAME. Existing acquisition receipts apply;
the runner does not rehash those inputs. It retains LLVM source/build outputs
for later same-revision Cuspy binding/library staging.

Example (plan only by default; add --execute after review):

    python3 insula/rootfs/rootless_stage.py --stage llvm --line cu130 \
      --rootfs "$LLVM_ROOTFS" --work "$LLVM_WORK" \
      --base-reference "$LOCKED_LLVM_BASE" --jobs 64

    python3 insula/rootfs/rootless_stage.py --stage cuda --line cu130 \
      --rootfs "$CUDA_ROOTFS" --work "$CUDA_WORK" \
      --base-reference "$LOCKED_CUDA_BASE"

Execution maps the caller to root inside an ordinary bwrap user namespace. It
writes the private rootfs and build workspace, not host packages or drivers.
Networking stays shared; recipe and DNS mounts are read-only.

This is a construction-stage interface, not a completed capsule pipeline.
Remaining steps are LLVM prefix staging into the CUDA rootfs, existing pinned
Foundry-tool provisioning, correct capsule receipts/manifest, then source-built
Python 3.13.13 and the full Spack-derived dependency graph. A distinct cu133 lock
and matching libclang/Python bindings closure are still needed for Torch 2.15
Cuspy. No cu133 selector aliases cu130. The current LLVM stage preserves canonical
compiler flags and install outputs, without claiming that these already close
the Cuspy dependencies.
