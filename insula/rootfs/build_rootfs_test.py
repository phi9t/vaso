#!/usr/bin/env python3
"""Behavior tests for build_rootfs.sh apt lock handling."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOTFS_DIR = Path(__file__).resolve().parent
EXPERIMENT_ROOT = ROOTFS_DIR.parent
SCRIPT = ROOTFS_DIR / "build_rootfs.sh"
LOCK = ROOTFS_DIR / "cuda_ecosystem.lock.json"
LLVM_PLAN = ROOTFS_DIR / "llvm_rootfs_cache.py"


def write_executable(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def write_lock(path: Path, *, unresolved: bool) -> None:
    lock = json.loads(LOCK.read_text(encoding="utf-8"))
    lock["lines"]["cu129"]["apt"] = {
        "snapshot": "20261001T000000Z",
        "unresolved": unresolved,
        "packages": {} if unresolved else {"ca-certificates": "20240203", "python3": "3.12.3-0ubuntu2"},
    }
    path.write_text(json.dumps(lock, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_lock_without_llvm_base_digest(path: Path) -> None:
    lock = json.loads(LOCK.read_text(encoding="utf-8"))
    base_image = lock["components"]["llvm"]["build"]["base_image"]
    base_image["reference"] = "ubuntu:24.04"
    base_image.pop("digest", None)
    base_image.pop("sha256", None)
    path.write_text(json.dumps(lock, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def copy_minimal_checkout(root: Path, name: str) -> Path:
    experiment_root = root / name / "foundry"
    rootfs_dir = experiment_root / "rootfs"
    rootfs_dir.mkdir(parents=True)
    for src in (SCRIPT, LOCK, LLVM_PLAN):
        shutil.copy2(src, rootfs_dir / src.name)
    return experiment_root


def seed_llvm_cache(lock: Path, estate: Path) -> None:
    result = subprocess.run(
        [
            sys.executable,
            str(LLVM_PLAN),
            "--lock",
            str(lock),
            "--estate-root",
            str(estate),
            "--format",
            "json",
        ],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    )
    cache_dir = Path(json.loads(result.stdout)["cache_dir"])
    write_executable(cache_dir / "bin/clang", "#!/usr/bin/env bash\nexit 127\n")
    (cache_dir / ".vaso-llvm-cache-complete").write_text("ok\n", encoding="utf-8")


def seed_llvm_downloads(lock: Path, estate: Path) -> None:
    data = json.loads(lock.read_text(encoding="utf-8"))
    llvm = data["components"]["llvm"]
    downloads = estate / "rootfs-lines" / "_llvm" / "downloads"
    downloads.mkdir(parents=True, exist_ok=True)
    source = downloads / f"llvm-project-{llvm['commit']}.tar.gz"
    source.write_bytes(b"fake llvm source")
    llvm["source"]["sha256"] = hashlib.sha256(source.read_bytes()).hexdigest()
    for patch in llvm.get("patches", []):
        patch_file = downloads / patch["name"]
        patch_file.write_text("fake patch\n", encoding="utf-8")
        patch["sha256"] = hashlib.sha256(patch_file.read_bytes()).hexdigest()
    lock.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def seed_export_rootfs(path: Path) -> None:
    for rel in (
        "etc",
        "usr/bin",
        "usr/lib",
        "usr/lib64",
        "usr/local/cuda",
        "lib",
        "lib64",
        "var/lib/dpkg",
    ):
        (path / rel).mkdir(parents=True, exist_ok=True)
    (path / "etc/passwd").write_text("root:x:0:0:root:/root:/bin/bash\n", encoding="utf-8")
    (path / "etc/group").write_text("root:x:0:\n", encoding="utf-8")
    (path / "usr/local/cuda/vaso-cuda-versions.json").write_text("{}\n", encoding="utf-8")
    (path / "var/lib/dpkg/status").write_text("", encoding="utf-8")


def seed_volatile_rootfs_state(path: Path) -> None:
    for rel in (
        "var/log/apt/history.log",
        "var/log/apt/term.log",
        "var/log/dpkg.log",
        "var/log/alternatives.log",
        "var/cache/ldconfig/aux-cache",
    ):
        target = path / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(f"volatile {rel}\n", encoding="utf-8")


def write_fake_docker(
    path: Path,
    rootfs: Path,
    captured_dockerfile: Path,
    *,
    calls_log: Path | None = None,
    captured_llvm_dockerfile: Path | None = None,
    captured_install_script: Path | None = None,
) -> None:
    log_line = f"""printf '%s\\n' "$cmd $*" >> {str(calls_log)!r}""" if calls_log else ":"
    capture_install_script = (
        f"""      if [ -f "$ctx/install_cuda_ecosystem.py" ]; then cp "$ctx/install_cuda_ecosystem.py" {str(captured_install_script)!r}; fi
"""
        if captured_install_script
        else ""
    )
    capture_build = f"""
    if [ -f "$ctx/Dockerfile" ] && grep -q 'org.vaso.llvm.cache="true"' "$ctx/Dockerfile"; then
      cp "$ctx/Dockerfile" {str(captured_llvm_dockerfile or captured_dockerfile)!r}
    else
      cp "$ctx/Dockerfile" {str(captured_dockerfile)!r}
{capture_install_script.rstrip()}
    fi
"""
    write_executable(
        path,
        f"""#!/usr/bin/env bash
set -euo pipefail
cmd="${{1:-}}"
if [ "$#" -gt 0 ]; then shift; fi
{log_line}
case "$cmd" in
  pull)
    exit 0
    ;;
  run)
    if [ "${{1:-}}" = "--rm" ] && [ "${{3:-}}" = "/usr/lib/llvm-23/bin/clang" ]; then
      echo "clang version 23.0.0git (https://github.com/llvm/llvm-project 35901313800ea6e6cbeb9226e51c7c4b29bfc40e)"
    else
      echo "24.04"
    fi
    exit 0
    ;;
  build)
    ctx="${{@: -1}}"
{capture_build}
    exit 0
    ;;
  cp)
    dst="${{2:?}}"
    mkdir -p "$dst/bin" "$dst/lib/cmake/llvm" "$dst/lib/cmake/mlir"
    touch "$dst/bin/clang" "$dst/bin/clang++" "$dst/bin/ld.lld" "$dst/bin/mlir-tblgen"
    chmod +x "$dst/bin/clang" "$dst/bin/clang++" "$dst/bin/ld.lld" "$dst/bin/mlir-tblgen"
    touch "$dst/lib/cmake/llvm/LLVMConfig.cmake" "$dst/lib/cmake/mlir/MLIRConfig.cmake"
    exit 0
    ;;
  create)
    echo fake-container
    exit 0
    ;;
  export)
    tar -C {str(rootfs)!r} -cf - .
    exit 0
    ;;
  rm)
    exit 0
    ;;
  image)
    if [ "${{1:-}}" = "inspect" ]; then
      echo "sha256:fake-image"
      exit 0
    fi
    ;;
esac
echo "unexpected docker invocation: $cmd $*" >&2
exit 98
""",
    )


class BuildRootfsAptLockTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(dir=os.environ.get("TEST_TMPDIR"))
        self.root = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def checkout_with_lock(self, name: str, *, unresolved: bool) -> tuple[Path, Path]:
        experiment_root = copy_minimal_checkout(self.root, name)
        lock = experiment_root / "rootfs" / "cuda_ecosystem.lock.json"
        write_lock(lock, unresolved=unresolved)
        return experiment_root, lock

    def run_build(
        self,
        lock: Path,
        *,
        fake_docker: Path | None = None,
        allow_unpinned: bool = False,
        no_cache: bool = False,
        dest: Path | None = None,
        estate_root: Path | None = None,
        script: Path = SCRIPT,
        cwd: Path = EXPERIMENT_ROOT,
    ) -> subprocess.CompletedProcess[str]:
        estate = estate_root or self.root / "estate"
        env = {
            **os.environ,
            "VASO_ESTATE_ROOT": str(estate),
            "VASO_AGENT_IO_ROOT": str(self.root / "agent"),
            "TMPDIR": str(self.root / "tmp"),
        }
        if fake_docker is not None:
            env["PATH"] = f"{fake_docker.parent}:{env['PATH']}"
        args = [
            "bash",
            str(script),
            "--line",
            "cu129",
            "--lock",
            str(lock),
            "--dest",
            str(dest or self.root / "dest"),
        ]
        if allow_unpinned:
            args.append("--allow-unpinned-apt")
        if no_cache:
            args.append("--no-cache")
        return subprocess.run(
            args,
            cwd=cwd,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

    def test_unresolved_apt_lock_refuses_before_docker(self) -> None:
        experiment_root, lock = self.checkout_with_lock("checkout", unresolved=True)
        fake_docker = self.root / "bin/docker"
        write_executable(fake_docker, "#!/usr/bin/env bash\necho docker should not run >&2\nexit 98\n")

        result = self.run_build(
            lock,
            fake_docker=fake_docker,
            script=experiment_root / "rootfs" / "build_rootfs.sh",
            cwd=experiment_root,
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("apt lock for cu129 is unresolved", result.stderr)
        self.assertIn("--allow-unpinned-apt", result.stderr)
        self.assertNotIn("docker should not run", result.stderr)

    def test_llvm_base_image_without_digest_refuses_before_docker(self) -> None:
        experiment_root = copy_minimal_checkout(self.root, "checkout")
        lock = experiment_root / "rootfs" / "cuda_ecosystem.lock.json"
        write_lock_without_llvm_base_digest(lock)
        fake_docker = self.root / "bin/docker"
        write_executable(fake_docker, "#!/usr/bin/env bash\necho docker should not run >&2\nexit 98\n")

        result = self.run_build(
            lock,
            fake_docker=fake_docker,
            allow_unpinned=True,
            script=experiment_root / "rootfs" / "build_rootfs.sh",
            cwd=experiment_root,
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("components.llvm.build.base_image", result.stderr)
        self.assertIn("digest", result.stderr)
        self.assertNotIn("docker should not run", result.stderr)

    def test_allow_unpinned_apt_logs_and_keeps_unpinned_install(self) -> None:
        experiment_root, lock = self.checkout_with_lock("checkout", unresolved=True)
        estate = self.root / "estate"
        seed_llvm_cache(lock, estate)
        export_rootfs = self.root / "export-rootfs"
        seed_export_rootfs(export_rootfs)
        captured_dockerfile = self.root / "captured.Dockerfile"
        fake_docker = self.root / "bin/docker"
        write_fake_docker(fake_docker, export_rootfs, captured_dockerfile)

        result = self.run_build(
            lock,
            fake_docker=fake_docker,
            allow_unpinned=True,
            script=experiment_root / "rootfs" / "build_rootfs.sh",
            cwd=experiment_root,
        )

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("ALLOW_UNPINNED_APT line=cu129", result.stderr)
        dockerfile = captured_dockerfile.read_text(encoding="utf-8")
        self.assertIn("apt-get install -y --no-install-recommends ca-certificates", dockerfile)
        self.assertNotIn("snapshot.ubuntu.com", dockerfile)

    def test_resolved_apt_lock_uses_snapshot_sources_and_pinned_packages(self) -> None:
        experiment_root, lock = self.checkout_with_lock("checkout", unresolved=False)
        estate = self.root / "estate"
        seed_llvm_cache(lock, estate)
        export_rootfs = self.root / "export-rootfs"
        seed_export_rootfs(export_rootfs)
        captured_dockerfile = self.root / "captured.Dockerfile"
        fake_docker = self.root / "bin/docker"
        write_fake_docker(fake_docker, export_rootfs, captured_dockerfile)

        result = self.run_build(
            lock,
            fake_docker=fake_docker,
            script=experiment_root / "rootfs" / "build_rootfs.sh",
            cwd=experiment_root,
        )

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        dockerfile = captured_dockerfile.read_text(encoding="utf-8")
        self.assertIn("https://snapshot.ubuntu.com/ubuntu/20261001T000000Z", dockerfile)
        self.assertIn("ca-certificates=20240203", dockerfile)
        self.assertIn("python3=3.12.3-0ubuntu2", dockerfile)
        self.assertNotIn("apt-get install -y --no-install-recommends ca-certificates python3", dockerfile)

    def test_manifest_is_identical_from_different_checkout_paths(self) -> None:
        manifests = []
        for name in ("checkout-a", "checkout-b"):
            experiment_root, lock = self.checkout_with_lock(name, unresolved=True)
            estate = self.root / f"estate-{name}"
            seed_llvm_cache(lock, estate)
            export_rootfs = self.root / f"export-rootfs-{name}"
            seed_export_rootfs(export_rootfs)
            captured_dockerfile = self.root / f"captured-{name}.Dockerfile"
            fake_docker = self.root / "bin" / name / "docker"
            write_fake_docker(fake_docker, export_rootfs, captured_dockerfile)

            dest = self.root / f"dest-{name}"
            result = self.run_build(
                lock,
                fake_docker=fake_docker,
                allow_unpinned=True,
                dest=dest,
                estate_root=estate,
                script=experiment_root / "rootfs" / "build_rootfs.sh",
                cwd=experiment_root,
            )

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            manifests.append(json.loads((dest / "rootfs-bundle.json").read_text(encoding="utf-8")))

        self.assertEqual(manifests[0], manifests[1])
        self.assertEqual(
            manifests[0]["lock"]["path"],
            "foundry/rootfs/cuda_ecosystem.lock.json",
        )
        self.assertNotIn("checkout-a", manifests[0]["lock"]["path"])

    def test_lock_outside_repo_is_refused_for_manifest_path(self) -> None:
        experiment_root = copy_minimal_checkout(self.root, "checkout")
        lock = self.root / "outside-lock.json"
        estate = self.root / "estate"
        write_lock(lock, unresolved=True)
        seed_llvm_cache(lock, estate)
        export_rootfs = self.root / "export-rootfs"
        seed_export_rootfs(export_rootfs)
        captured_dockerfile = self.root / "captured.Dockerfile"
        fake_docker = self.root / "bin/docker"
        write_fake_docker(fake_docker, export_rootfs, captured_dockerfile)

        result = self.run_build(
            lock,
            fake_docker=fake_docker,
            allow_unpinned=True,
            script=experiment_root / "rootfs" / "build_rootfs.sh",
            cwd=experiment_root,
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("lock path must be inside the repository", result.stderr)

    def test_manifest_omits_derived_image_id(self) -> None:
        experiment_root, lock = self.checkout_with_lock("checkout", unresolved=True)
        estate = self.root / "estate"
        seed_llvm_cache(lock, estate)
        export_rootfs = self.root / "export-rootfs"
        seed_export_rootfs(export_rootfs)
        captured_dockerfile = self.root / "captured.Dockerfile"
        fake_docker = self.root / "bin/docker"
        write_fake_docker(fake_docker, export_rootfs, captured_dockerfile)

        dest = self.root / "dest"
        result = self.run_build(
            lock,
            fake_docker=fake_docker,
            allow_unpinned=True,
            dest=dest,
            estate_root=estate,
            script=experiment_root / "rootfs" / "build_rootfs.sh",
            cwd=experiment_root,
        )

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        manifest = json.loads((dest / "rootfs-bundle.json").read_text(encoding="utf-8"))
        self.assertNotIn("derived_image_id", manifest)

    def test_exported_rootfs_removes_volatile_logs_and_caches(self) -> None:
        experiment_root, lock = self.checkout_with_lock("checkout", unresolved=True)
        estate = self.root / "estate"
        seed_llvm_cache(lock, estate)
        export_rootfs = self.root / "export-rootfs"
        seed_export_rootfs(export_rootfs)
        seed_volatile_rootfs_state(export_rootfs)
        captured_dockerfile = self.root / "captured.Dockerfile"
        fake_docker = self.root / "bin/docker"
        write_fake_docker(fake_docker, export_rootfs, captured_dockerfile)

        dest = self.root / "dest"
        result = self.run_build(
            lock,
            fake_docker=fake_docker,
            allow_unpinned=True,
            dest=dest,
            estate_root=estate,
            script=experiment_root / "rootfs" / "build_rootfs.sh",
            cwd=experiment_root,
        )

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        for rel in (
            "var/log/apt/history.log",
            "var/log/apt/term.log",
            "var/log/dpkg.log",
            "var/log/alternatives.log",
            "var/cache/ldconfig/aux-cache",
        ):
            self.assertFalse((dest / "rootfs" / rel).exists(), rel)

    def test_nccl_build_uses_deterministic_nvcc_wrapper(self) -> None:
        experiment_root, lock = self.checkout_with_lock("checkout", unresolved=True)
        estate = self.root / "estate"
        seed_llvm_cache(lock, estate)
        export_rootfs = self.root / "export-rootfs"
        seed_export_rootfs(export_rootfs)
        captured_dockerfile = self.root / "captured.Dockerfile"
        captured_install_script = self.root / "install_cuda_ecosystem.py"
        fake_docker = self.root / "bin/docker"
        write_fake_docker(
            fake_docker,
            export_rootfs,
            captured_dockerfile,
            captured_install_script=captured_install_script,
        )

        result = self.run_build(
            lock,
            fake_docker=fake_docker,
            allow_unpinned=True,
            script=experiment_root / "rootfs" / "build_rootfs.sh",
            cwd=experiment_root,
        )

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        install_script = captured_install_script.read_text(encoding="utf-8")
        self.assertIn('env["NVCC"]', install_script)
        self.assertIn("--objdir-as-tempdir", install_script)
        self.assertIn("-frandom-seed=", install_script)

    def test_no_cache_passes_flags_to_both_docker_builds(self) -> None:
        experiment_root, lock = self.checkout_with_lock("checkout", unresolved=True)
        estate = self.root / "estate"
        seed_llvm_downloads(lock, estate)
        export_rootfs = self.root / "export-rootfs"
        seed_export_rootfs(export_rootfs)
        captured_dockerfile = self.root / "captured.Dockerfile"
        captured_llvm_dockerfile = self.root / "captured-llvm.Dockerfile"
        calls_log = self.root / "docker.calls"
        fake_docker = self.root / "bin/docker"
        write_fake_docker(
            fake_docker,
            export_rootfs,
            captured_dockerfile,
            calls_log=calls_log,
            captured_llvm_dockerfile=captured_llvm_dockerfile,
        )

        result = self.run_build(
            lock,
            fake_docker=fake_docker,
            allow_unpinned=True,
            no_cache=True,
            script=experiment_root / "rootfs" / "build_rootfs.sh",
            cwd=experiment_root,
        )

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        calls = calls_log.read_text(encoding="utf-8").splitlines()
        builds = [line for line in calls if line.startswith("build ")]
        self.assertEqual(len(builds), 2, calls)
        for build in builds:
            self.assertIn("--no-cache", build)
            self.assertIn("--pull=false", build)
        llvm_dockerfile = captured_llvm_dockerfile.read_text(encoding="utf-8")
        self.assertIn(
            "FROM ubuntu:24.04@sha256:496754492fb28b4d3049432f2ca787449331e23fb14f0dd3fffea86bf5a93eb4",
            llvm_dockerfile,
        )


if __name__ == "__main__":
    unittest.main()
