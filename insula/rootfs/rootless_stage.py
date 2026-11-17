#!/usr/bin/env python3
"""Run canonical rootfs construction stages in a private, unpacked OCI rootfs.

This does not acquire an image or certify a finished capsule. The caller supplies
the pinned base reference and its already-unpacked private writable tree. LLVM
and CUDA stages use their respective locked bases; they are not interchangeable.
No Docker daemon, host package installation, or host driver mutation is used.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import shlex
import subprocess
import sys

import llvm_rootfs_cache

HERE = Path(__file__).resolve().parent


def quoted(argv):
    return " ".join(shlex.quote(str(arg)) for arg in argv)


def stage_script(lock: dict, stage: str, line: str, jobs: int) -> tuple[str, str]:
    if jobs < 1:
        raise ValueError("jobs must be positive")
    llvm = lock["components"]["llvm"]
    if stage == "llvm":
        base = llvm_rootfs_cache.pinned_base_image(llvm["build"]["base_image"])["reference"]
        limit = min(jobs, int(llvm["build"]["max_jobs"]))
        source = llvm["source"]
        # Inputs are acquired separately, with existing archive receipts. No
        # second source/archive pass or per-patch content hashes here.
        commands = [
            "apt-get update",
            "apt-get install -y --no-install-recommends ca-certificates build-essential cmake ninja-build python3 patch xz-utils bzip2",
            "mkdir -p /build/src /build/tmp",
            "test -f /build/llvm-project.tar.gz",
            "tar -xzf /build/llvm-project.tar.gz -C /build/src",
        ]
        source_dir = "/build/src/" + source["strip_prefix"]
        for patch in llvm.get("patches", []):
            name = patch["name"]
            if Path(name).name != name or name in {".", ".."}:
                raise ValueError("LLVM patch name must be a basename")
            strip = patch.get("strip", "-p1")
            if strip not in ("-p0", "-p1", "-p2"):
                raise ValueError("unsupported patch strip")
            commands.append(quoted(["patch", "-d", source_dir, strip, "-i", "/build/llvm-patches/" + name]))
        commands += [
            quoted(["cmake", "-S", source_dir + "/llvm", "-B", "/build/llvm-build",
                    "-G", "Ninja", *llvm_rootfs_cache.cmake_args(llvm)]),
            quoted(["cmake", "--build", "/build/llvm-build", "--parallel", limit]),
            "cmake --install /build/llvm-build",
        ]
        prefix = llvm_rootfs_cache.install_prefix(llvm)
        for artifact in llvm["build"]["install_artifacts"]:
            commands.append(quoted(["test", "-e", prefix + "/" + artifact]))
        commands.append(quoted([prefix + "/bin/clang", "--version"]))
        # Keep source/build tree for same-revision Cuspy bindings/libclang
        # staging. No claim that the existing lock declares their full closure.
    elif stage == "cuda":
        config = lock["lines"][line]
        base = config["base_image"]["reference"]
        apt = config["apt"]
        if apt.get("unresolved") or not apt.get("packages"):
            raise ValueError("rootless CUDA stage requires resolved APT lock")
        snapshot = apt["snapshot"]
        import re
        if re.fullmatch(r"20[0-9]{6}T[0-9]{6}Z", snapshot) is None:
            raise ValueError("invalid APT snapshot")
        apt_sources = (
            "Types: deb\nURIs: https://snapshot.ubuntu.com/ubuntu/" + snapshot +
            "\nSuites: noble noble-updates noble-security\n"
            "Components: main restricted universe multiverse\n"
            "Signed-By: /usr/share/keyrings/ubuntu-archive-keyring.gpg\n"
            "Check-Valid-Until: no\n"
        )
        commands = [
            "rm -f /etc/apt/sources.list",
            "printf %s " + shlex.quote(apt_sources) + " > /etc/apt/sources.list.d/ubuntu.sources",
            "apt-get update",
            """purge_pkgs="$(dpkg-query -W -f='${binary:Package}\n' 'libnccl*' 'libcudnn*' 'clang*' 'lld*' 'llvm*' 'libclang*' 'libllvm*' 'mlir*' 2>/dev/null || true)"
if [ -n "$purge_pkgs" ]; then apt-mark unhold $purge_pkgs || true; apt-get purge -y --allow-change-held-packages $purge_pkgs; fi""",
            quoted(["apt-get", "install", "-y", "--no-install-recommends",
                    *[name + "=" + version for name, version in sorted(apt["packages"].items())]]),
            "cp /recipe/cuda_ecosystem.lock.json /build/cuda_ecosystem.lock.json",
            "python3 /recipe/install_cuda_ecosystem.py",
            "python3 /recipe/collect_cuda_versions.py > /usr/local/cuda/vaso-cuda-versions.json",
        ]
    else:
        raise ValueError("unknown construction stage")
    if "@sha256:" not in base:
        raise ValueError("base must be digest pinned")
    # Check only the selected OS identity, not an eager inventory.
    return base, "set -eu\n. /etc/os-release\ntest \"$ID\" = ubuntu\ntest \"$VERSION_ID\" = 24.04\n" + "\n".join(commands) + "\n"


def bwrap_argv(rootfs: Path, work: Path, recipe: Path, line: str, bwrap: str, jobs: int) -> list[str]:
    return [
        bwrap, "--die-with-parent", "--unshare-user", "--uid", "0", "--gid", "0",
        "--clearenv", "--bind", str(rootfs), "/", "--proc", "/proc", "--dev", "/dev",
        "--ro-bind", "/etc/resolv.conf", "/etc/resolv.conf",
        "--bind", str(work), "/build", "--ro-bind", str(recipe), "/recipe",
        "--setenv", "PATH", "/usr/local/cuda/bin:/usr/sbin:/usr/bin:/sbin:/bin",
        "--setenv", "HOME", "/root", "--setenv", "DEBIAN_FRONTEND", "noninteractive",
        "--setenv", "TMPDIR", "/build/tmp", "--setenv", "VASO_CUDA_LINE", line,
        "--setenv", "VASO_BUILD_JOBS", str(jobs),
        "--chdir", "/build", "--", "/bin/bash", "/recipe/stage.sh",
    ]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", choices=("llvm", "cuda"), required=True)
    parser.add_argument("--line", choices=("cu129", "cu130"), required=True)
    parser.add_argument("--lock", type=Path, default=HERE / "cuda_ecosystem.lock.json")
    parser.add_argument("--rootfs", type=Path, required=True)
    parser.add_argument("--base-reference", required=True)
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--jobs", type=int, default=32)
    parser.add_argument("--bwrap", default="bwrap")
    parser.add_argument("--execute", action="store_true", help="otherwise emit plan only")
    args = parser.parse_args(argv)
    lock = json.loads(args.lock.read_text())
    base, script = stage_script(lock, args.stage, args.line, args.jobs)
    if args.base_reference != base:
        raise ValueError("supplied base reference does not match selected locked stage")
    rootfs, work = args.rootfs.resolve(), args.work.resolve()
    if rootfs == Path("/") or rootfs == work or rootfs in work.parents or work in rootfs.parents:
        raise ValueError("rootfs and build workspace must be disjoint private directories")
    if not args.execute:
        print(json.dumps({"base_reference": base, "stage": args.stage, "script": script,
                          "rootfs": str(rootfs), "work": str(work), "executes": False}, indent=2))
        return 0
    if not (rootfs / "bin/bash").exists():
        raise ValueError("unpack the pinned base into a private rootfs first")
    work.mkdir(parents=True, exist_ok=True)
    (work / "tmp").mkdir(exist_ok=True)
    recipe = work / "rootless-recipe"
    recipe.mkdir(exist_ok=True)
    (recipe / "stage.sh").write_text(script)
    (recipe / "cuda_ecosystem.lock.json").write_bytes(args.lock.read_bytes())
    for name in ("install_cuda_ecosystem.py", "collect_cuda_versions.py"):
        (recipe / name).write_bytes((HERE / name).read_bytes())
    subprocess.run(bwrap_argv(rootfs, work, recipe, args.line, args.bwrap, args.jobs), check=True)
    print(json.dumps({"stage": args.stage, "completed": True, "capsule_ready": False}))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, KeyError) as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(2)
