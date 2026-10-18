from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


MODULE = Path(__file__).with_name("rootfs_spack_externals.py")
REPO_ROOT = Path(__file__).resolve().parents[1]
LLVM_COMMIT = "35901313800ea6e6cbeb9226e51c7c4b29bfc40e"
SPEC = importlib.util.spec_from_file_location("rootfs_spack_externals", MODULE)
assert SPEC and SPEC.loader
rootfs_spack_externals = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = rootfs_spack_externals
SPEC.loader.exec_module(rootfs_spack_externals)


def manifest(line: str, cuda: str) -> dict[str, object]:
    return {
        "schema_version": 2,
        "line": line,
        "verified_versions": {
            "components": {
                "cuda_toolkit": {"expected": cuda},
                "cudnn": {"expected": "9.24.0.43"},
                "cudss": {"expected": "0.7.1.4"},
                "cusparselt": {"expected": "0.8.1.1"},
                "nccl": {"expected": "2.30.7"},
                "nvshmem": {"expected": "3.4.5"},
                "llvm": {
                    "expected": "23.0.0git",
                    "commit": LLVM_COMMIT,
                    "prefix": "/usr/lib/llvm-23",
                },
            },
        },
    }


class RootfsSpackExternalsTest(unittest.TestCase):
    def test_cu129_external_specs_are_recipe_compatible(self) -> None:
        entries = rootfs_spack_externals.externals_from_manifest(manifest("cu129", "12.9.1"))

        self.assertEqual(
            entries,
            [
                rootfs_spack_externals.External("cuda", "cuda@12.9.1", "/usr/local/cuda"),
                rootfs_spack_externals.External(
                    "cuda",
                    "cuda@12.9.1 +allow-unsupported-compilers",
                    "/usr/local/cuda",
                ),
                rootfs_spack_externals.External("cudnn", "cudnn@9.24.0.43-12", "/usr/local/cuda"),
                rootfs_spack_externals.External(
                    "nccl",
                    "nccl@2.30.7-1 +cuda cuda_arch=100 fabrics=verbs",
                    "/usr/local/cuda",
                ),
                rootfs_spack_externals.External("cusparselt", "cusparselt@0.8.1-cuda120", "/usr/local/cuda"),
                rootfs_spack_externals.External("cudss", "cudss@0.7.1", "/usr/local/cuda"),
                rootfs_spack_externals.External(
                    "nvshmem",
                    "nvshmem@3.4.5 +cuda +ucx +gdrcopy ~mpi ~nccl ~shmem ~libfabric cuda_arch=100",
                    "/usr/local/cuda",
                ),
                rootfs_spack_externals.External("nvtx", "nvtx@3.3.0 ~python", "/usr/local/cuda/targets/x86_64-linux"),
                rootfs_spack_externals.External(
                    "llvm",
                    "llvm@23.0.0 +clang +lld +mlir",
                    "/usr/lib/llvm-23",
                    {
                        "compilers": {
                            "c": "/usr/lib/llvm-23/bin/clang",
                            "cxx": "/usr/lib/llvm-23/bin/clang++",
                        },
                    },
                ),
                rootfs_spack_externals.External("bash", "bash@5.2", "/usr"),
                rootfs_spack_externals.External("zip", "zip@3.0", "/usr"),
                rootfs_spack_externals.External("bazel", "bazel@7.7.0", "/opt/vaso/bazel-7.7.0"),
            ],
        )

    def test_cu130_external_specs_use_the_cuda13_recipe_names(self) -> None:
        entries = rootfs_spack_externals.externals_from_manifest(manifest("cu130", "13.0.3"))

        self.assertIn(
            rootfs_spack_externals.External(
                "cuda",
                "cuda@13.0.3 +allow-unsupported-compilers",
                "/usr/local/cuda",
            ),
            entries,
        )
        self.assertIn(rootfs_spack_externals.External("cudnn", "cudnn@9.24.0.43-13", "/usr/local/cuda"), entries)
        self.assertIn(rootfs_spack_externals.External("cusparselt", "cusparselt@0.8.1-cuda130", "/usr/local/cuda"), entries)

    def test_yaml_fragment_marks_every_external_non_buildable(self) -> None:
        fragment = rootfs_spack_externals.packages_yaml_fragment(
            rootfs_spack_externals.externals_from_manifest(manifest("cu129", "12.9.1"))
        )

        self.assertEqual(
            fragment,
            (
                '  cuda:\n'
                '    externals:\n'
                '    - spec: "cuda@12.9.1"\n'
                '      prefix: /usr/local/cuda\n'
                '    - spec: "cuda@12.9.1 +allow-unsupported-compilers"\n'
                '      prefix: /usr/local/cuda\n'
                '    buildable: false\n'
                '  cudnn:\n'
                '    externals:\n'
                '    - spec: "cudnn@9.24.0.43-12"\n'
                '      prefix: /usr/local/cuda\n'
                '    buildable: false\n'
                '  nccl:\n'
                '    externals:\n'
                '    - spec: "nccl@2.30.7-1 +cuda cuda_arch=100 fabrics=verbs"\n'
                '      prefix: /usr/local/cuda\n'
                '    buildable: false\n'
                '  cusparselt:\n'
                '    externals:\n'
                '    - spec: "cusparselt@0.8.1-cuda120"\n'
                '      prefix: /usr/local/cuda\n'
                '    buildable: false\n'
                '  cudss:\n'
                '    externals:\n'
                '    - spec: "cudss@0.7.1"\n'
                '      prefix: /usr/local/cuda\n'
                '    buildable: false\n'
                '  nvshmem:\n'
                '    externals:\n'
                '    - spec: "nvshmem@3.4.5 +cuda +ucx +gdrcopy ~mpi ~nccl ~shmem ~libfabric cuda_arch=100"\n'
                '      prefix: /usr/local/cuda\n'
                '    buildable: false\n'
                '  nvtx:\n'
                '    externals:\n'
                '    - spec: "nvtx@3.3.0 ~python"\n'
                '      prefix: /usr/local/cuda/targets/x86_64-linux\n'
                '    buildable: false\n'
                '  llvm:\n'
                '    externals:\n'
                '    - spec: "llvm@23.0.0 +clang +lld +mlir"\n'
                '      prefix: /usr/lib/llvm-23\n'
                '      extra_attributes:\n'
                '        compilers:\n'
                '          c: /usr/lib/llvm-23/bin/clang\n'
                '          cxx: /usr/lib/llvm-23/bin/clang++\n'
                '    buildable: false\n'
                '  bash:\n'
                '    externals:\n'
                '    - spec: "bash@5.2"\n'
                '      prefix: /usr\n'
                '    buildable: false\n'
                '  zip:\n'
                '    externals:\n'
                '    - spec: "zip@3.0"\n'
                '      prefix: /usr\n'
                '    buildable: false\n'
                '  bazel:\n'
                '    externals:\n'
                '    - spec: "bazel@7.7.0"\n'
                '      prefix: /opt/vaso/bazel-7.7.0\n'
                '    buildable: false\n'
            ),
        )

    def test_missing_manifest_component_fails_loudly(self) -> None:
        data = manifest("cu129", "12.9.1")
        del data["verified_versions"]["components"]["nccl"]

        with self.assertRaisesRegex(ValueError, "missing verified rootfs component: nccl"):
            rootfs_spack_externals.externals_from_manifest(data)

    def test_llvm_compiler_external_uses_numeric_version(self) -> None:
        entries = [
            entry
            for entry in rootfs_spack_externals.externals_from_manifest(manifest("cu130", "13.0.3"))
            if entry.package == "llvm"
        ]

        self.assertEqual(len(entries), 1)
        compiler_entries = [entry for entry in entries if entry.extra_attributes]
        self.assertEqual(len(compiler_entries), 1)
        self.assertEqual(compiler_entries[0].spec, "llvm@23.0.0 +clang +lld +mlir")
        self.assertNotIn("git.", compiler_entries[0].spec)
        self.assertFalse(
            any(entry.spec.startswith("llvm@git.") for entry in entries),
            "GitVersion llvm externals are compiler-package records in Spack's store "
            "and crash target-default solving",
        )

    def test_lock_components_with_spack_packages_are_all_generated_externals(self) -> None:
        lock = {
            "components": {
                "llvm": {"version": "23.0.0git"},
            },
            "lines": {
                "cu129": {
                    "components": {
                        "cuda_toolkit": {"version": "12.9.1"},
                        "cudnn": {"version": "9.24.0.43"},
                        "cudss": {"version": "0.7.1.4"},
                        "cusparselt": {"version": "0.8.1.1"},
                        "nccl": {"version": "2.30.7"},
                        "nvshmem": {"version": "3.4.5"},
                        "tensorrt": {"version": "11.1.0.106"},
                    },
                },
            },
        }

        self.assertEqual(
            rootfs_spack_externals.lock_components_requiring_externals(lock),
            {"cuda", "cudnn", "cudss", "cusparselt", "llvm", "nccl", "nvshmem"},
        )

    def test_overlay_declares_manifest_versions_missing_from_builtin_recipes(self) -> None:
        overlay = REPO_ROOT / "spack_overlays/vaso/spack_repo/vaso_overlay/packages"
        required = {
            "cuda": ['version("13.0.3"'],
            "cudnn": ['version("9.24.0.43-12"', 'version("9.24.0.43-13"'],
            "nccl": ['version("2.30.7-1"'],
            "cusparselt": ['version("0.8.1-cuda130"', 'depends_on("cuda@13"'],
            "nvtx": ['from spack_repo.builtin.build_systems.generic import Package', 'class Nvtx(Package):'],
        }

        for package, needles in required.items():
            text = (overlay / package / "package.py").read_text(encoding="utf-8")
            for needle in needles:
                self.assertIn(needle, text, f"{package} overlay must declare {needle}")

    def test_cli_can_emit_solver_spec_arguments(self) -> None:
        base = os.environ.get("TEST_TMPDIR") or os.environ.get("VASO_AGENT_IO_ROOT")
        with tempfile.TemporaryDirectory(dir=base) as tmp:
            path = Path(tmp) / "manifest.json"
            path.write_text(json.dumps(manifest("cu130", "13.0.3")), encoding="utf-8")
            result = subprocess.run(
                [sys.executable, str(MODULE), "--manifest", str(path), "--format", "specs"],
                check=True,
                text=True,
                stdout=subprocess.PIPE,
            )

        self.assertEqual(
            result.stdout.splitlines(),
            [
                "cuda@13.0.3",
                "cuda@13.0.3 +allow-unsupported-compilers",
                "cudnn@9.24.0.43-13",
                "nccl@2.30.7-1 +cuda cuda_arch=100 fabrics=verbs",
                "cusparselt@0.8.1-cuda130",
                "cudss@0.7.1",
                "nvshmem@3.4.5 +cuda +ucx +gdrcopy ~mpi ~nccl ~shmem ~libfabric cuda_arch=100",
                "nvtx@3.3.0 ~python",
                "llvm@23.0.0 +clang +lld +mlir",
                "bash@5.2",
                "zip@3.0",
                "bazel@7.7.0",
            ],
        )


if __name__ == "__main__":
    unittest.main()
