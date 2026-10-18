#!/usr/bin/env python3
"""Behavior tests for verify_cuda_ecosystem.py over fake rootfs trees."""

from __future__ import annotations

import importlib.util
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path


ROOTFS_DIR = Path(__file__).resolve().parent
SCRIPT = ROOTFS_DIR / "verify_cuda_ecosystem.py"
LOCK = ROOTFS_DIR / "cuda_ecosystem.lock.json"
LLVM_COMMIT = "35901313800ea6e6cbeb9226e51c7c4b29bfc40e"

SPEC = importlib.util.spec_from_file_location("verify_cuda_ecosystem", SCRIPT)
assert SPEC is not None
assert SPEC.loader is not None
verify = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = verify
SPEC.loader.exec_module(verify)


def _version_parts(version: str) -> list[int]:
    return [int(part) for part in version.split(".")]


def _write_executable(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def _write_status(rootfs: Path, packages: list[dict[str, str]]) -> None:
    status = rootfs / "var/lib/dpkg/status"
    status.parent.mkdir(parents=True, exist_ok=True)
    paragraphs = []
    for package in packages:
        name, version = package["package"].split("=", 1)
        paragraphs.append(f"Package: {name}\nStatus: hold ok installed\nVersion: {version}\n")
    status.write_text("\n".join(paragraphs), encoding="utf-8")


def make_matching_rootfs(rootfs: Path, line: str = "cu129") -> None:
    lock = json.loads(LOCK.read_text(encoding="utf-8"))
    components = lock["lines"][line]["components"]
    cuda_real = rootfs / "usr/local/cuda-12.9"
    alternatives = rootfs / "etc/alternatives"
    alternatives.mkdir(parents=True)
    (rootfs / "usr/local").mkdir(parents=True)
    (rootfs / "usr/local/cuda").symlink_to("/etc/alternatives/cuda")
    (alternatives / "cuda").symlink_to("/usr/local/cuda-12.9")
    cuda = cuda_real
    include = cuda / "include"
    lib64 = cuda / "lib64"
    include.mkdir(parents=True)
    lib64.mkdir(parents=True)

    toolkit_expect = components["cuda_toolkit"]["verify"]["expect"]
    major, minor, *_ = _version_parts(components["cuda_toolkit"]["version"])
    (include / "cuda.h").write_text(f"#define CUDA_VERSION {major * 1000 + minor * 10}\n", encoding="utf-8")
    _write_executable(
        cuda / "bin/nvcc",
        "#!/usr/bin/env bash\n"
        f"echo 'Cuda compilation tools, release {major}.{minor}, V{toolkit_expect['nvcc']}'\n",
    )

    cudnn = _version_parts(components["cudnn"]["version"])
    (include / "cudnn_version.h").write_text(
        "\n".join([
            f"#define CUDNN_MAJOR {cudnn[0]}",
            f"#define CUDNN_MINOR {cudnn[1]}",
            f"#define CUDNN_PATCHLEVEL {cudnn[2]}",
            f"#define CUDNN_BUILD_VERSION {cudnn[3]}",
            "",
        ]),
        encoding="utf-8",
    )

    cusparselt = _version_parts(components["cusparselt"]["version"])
    (include / "cusparseLt.h").write_text(
        "\n".join([
            f"#define CUSPARSELT_VER_MAJOR {cusparselt[0]}",
            f"#define CUSPARSELT_VER_MINOR {cusparselt[1]}",
            f"#define CUSPARSELT_VER_PATCH {cusparselt[2]}",
            f"#define CUSPARSELT_VER_BUILD {cusparselt[3]}",
            "",
        ]),
        encoding="utf-8",
    )

    cudss = _version_parts(components["cudss"]["version"])
    (include / "cudss.h").write_text(
        "\n".join([
            f"#define CUDSS_VER_MAJOR {cudss[0]}",
            f"#define CUDSS_VER_MINOR {cudss[1]}",
            f"#define CUDSS_VER_PATCH {cudss[2]}",
            f"#define CUDSS_VER_BUILD {cudss[3]}",
            "",
        ]),
        encoding="utf-8",
    )

    nvshmem = _version_parts(components["nvshmem"]["version"])
    (include / "nvshmem_version.h").write_text(
        "\n".join([
            f"#define NVSHMEM_MAJOR_VERSION {nvshmem[0]}",
            f"#define NVSHMEM_MINOR_VERSION {nvshmem[1]}",
            f"#define NVSHMEM_PATCH_VERSION {nvshmem[2]}",
            "",
        ]),
        encoding="utf-8",
    )

    nccl = _version_parts(components["nccl"]["version"])
    (include / "nccl.h").write_text(
        "\n".join([
            f"#define NCCL_MAJOR {nccl[0]}",
            f"#define NCCL_MINOR {nccl[1]}",
            f"#define NCCL_PATCH {nccl[2]}",
            "",
        ]),
        encoding="utf-8",
    )

    trt = _version_parts(components["tensorrt"]["version"])
    trt_include = rootfs / "usr/include/x86_64-linux-gnu"
    trt_include.mkdir(parents=True)
    (trt_include / "NvInferVersion.h").write_text(
        "\n".join([
            f"#define NV_TENSORRT_MAJOR {trt[0]}",
            f"#define NV_TENSORRT_MINOR {trt[1]}",
            f"#define NV_TENSORRT_PATCH {trt[2]}",
            f"#define NV_TENSORRT_BUILD {trt[3]}",
            "",
        ]),
        encoding="utf-8",
    )
    _write_status(rootfs, components["tensorrt"]["source"]["packages"])

    for path in [
        lib64 / "libcudart.so.12",
        lib64 / "libcublas.so.12",
        lib64 / "libcudnn.so.9",
        lib64 / "libcusparseLt.so.0",
        lib64 / "libcudss.so.0",
        lib64 / "libnccl.so.2",
        lib64 / "libnvshmem_host.so.3",
        rootfs / "usr/lib/x86_64-linux-gnu/libnvinfer.so.11",
    ]:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"fake\n")
    stubs = lib64 / "stubs"
    stubs.mkdir()
    (stubs / "libcuda.so.1").write_bytes(b"stub\n")
    (stubs / "libcublas.so").write_bytes(b"stub\n")

    usr_bin = rootfs / "usr/bin"
    _write_executable(
        usr_bin / "gcc",
        "#!/usr/bin/env bash\n"
        "echo 'gcc (Ubuntu 13.3.0-6ubuntu2~24.04) 13.3.0'\n",
    )
    _write_executable(
        usr_bin / "g++",
        "#!/usr/bin/env bash\n"
        "echo 'g++ (Ubuntu 13.3.0-6ubuntu2~24.04) 13.3.0'\n",
    )
    (usr_bin / "cc").symlink_to("gcc")
    (usr_bin / "c++").symlink_to("g++")

    llvm = rootfs / "usr/lib/llvm-23"
    _write_executable(
        llvm / "bin/clang",
        "#!/usr/bin/env bash\n"
        f"echo 'clang version 23.0.0git ({LLVM_COMMIT})'\n",
    )
    _write_executable(
        llvm / "bin/clang++",
        "#!/usr/bin/env bash\n"
        f"echo 'clang version 23.0.0git ({LLVM_COMMIT})'\n",
    )
    _write_executable(llvm / "bin/ld.lld", "#!/usr/bin/env bash\necho 'LLD 23.0.0git'\n")
    _write_executable(llvm / "bin/mlir-tblgen", "#!/usr/bin/env bash\necho 'mlir-tblgen'\n")
    (llvm / "lib/cmake/llvm").mkdir(parents=True)
    (llvm / "lib/cmake/llvm/LLVMConfig.cmake").write_text("# fake\n", encoding="utf-8")
    (llvm / "lib/cmake/mlir").mkdir(parents=True)
    (llvm / "lib/cmake/mlir/MLIRConfig.cmake").write_text("# fake\n", encoding="utf-8")
    (llvm / "lib/libLLVMCore.a").write_bytes(b"fake\n")
    (llvm / "lib/libMLIRIR.a").write_bytes(b"fake\n")
    _write_executable(cuda / "nvvm/bin/cicc", "#!/usr/bin/env bash\necho 'NVIDIA NVVM'\n")
    (cuda / "nvvm/lib64").mkdir(parents=True)
    (cuda / "nvvm/lib64/libLLVMNVVM.so").write_bytes(b"vendor\n")


class VerifyCudaEcosystemTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(dir=os.environ.get("TEST_TMPDIR"))
        self.rootfs = Path(self.tmp.name) / "rootfs"
        make_matching_rootfs(self.rootfs)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def run_verify(self) -> tuple[subprocess.CompletedProcess[str], dict[str, object]]:
        result = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                str(self.rootfs),
                "--lock",
                str(LOCK),
                "--line",
                "cu129",
            ],
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        try:
            report = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise AssertionError(f"verifier did not emit JSON\nstdout={result.stdout}\nstderr={result.stderr}") from exc
        return result, report

    def test_matching_tree_passes(self) -> None:
        result, report = self.run_verify()
        self.assertEqual(result.returncode, 0, report)
        self.assertTrue(report["ok"])
        self.assertEqual(report["line"], "cu129")
        self.assertEqual(
            report["components"]["llvm"]["static_version"]["commit"],
            LLVM_COMMIT,
        )

    def test_wrong_cudnn_header_fails(self) -> None:
        (self.rootfs / "usr/local/cuda-12.9/include/cudnn_version.h").write_text(
            "#define CUDNN_MAJOR 9\n#define CUDNN_MINOR 23\n#define CUDNN_PATCHLEVEL 0\n",
            encoding="utf-8",
        )
        result, report = self.run_verify()
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(report["ok"])
        self.assertIn("cudnn.static_version", {issue["code"] for issue in report["issues"]})

    def test_two_real_nccl_copies_fail(self) -> None:
        duplicate = self.rootfs / "opt/extra/lib/libnccl.so.2"
        duplicate.parent.mkdir(parents=True)
        duplicate.write_bytes(b"duplicate\n")
        result, report = self.run_verify()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("single_copy.libnccl", {issue["code"] for issue in report["issues"]})

    def test_merged_usr_aliases_are_not_second_rootfs_copies(self) -> None:
        libnvinfer = self.rootfs / "usr/lib/x86_64-linux-gnu/libnvinfer.so.11"
        clang = self.rootfs / "usr/lib/llvm-23/bin/clang"
        lib_alias = self.rootfs / "lib/x86_64-linux-gnu/libnvinfer.so.11"
        clang_alias = self.rootfs / "lib/llvm-23/bin/clang"
        lib_alias.parent.mkdir(parents=True)
        clang_alias.parent.mkdir(parents=True)
        os.link(libnvinfer, lib_alias)
        os.link(clang, clang_alias)

        result, report = self.run_verify()

        self.assertEqual(result.returncode, 0, report)
        self.assertTrue(report["ok"])

    def test_virtual_mount_payloads_are_not_part_of_rootfs_scans(self) -> None:
        for path in (
            self.rootfs / "proc/4690/task/4690/net/libnccl.so.2",
            self.rootfs / "proc/4690/task/4690/net/libLLVMProcess.so",
            self.rootfs / "run/nvidia-driver/lib/libcuda.so.1",
        ):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"virtual bind mount payload\n")
        dist = self.rootfs / "workspace/experiment/site-packages/torch-2.14.0.dist-info"
        dist.mkdir(parents=True)
        (dist / "METADATA").write_text("Name: torch\n", encoding="utf-8")

        result, report = self.run_verify()

        self.assertEqual(result.returncode, 0, report)
        self.assertTrue(report["ok"])

    def test_pip_nvidia_dist_info_fails(self) -> None:
        dist = self.rootfs / "usr/lib/python3.12/site-packages/nvidia_cudnn_cu12-9.24.0.43.dist-info"
        dist.mkdir(parents=True)
        (dist / "METADATA").write_text("Name: nvidia-cudnn-cu12\n", encoding="utf-8")
        result, report = self.run_verify()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("pip.forbidden_distribution", {issue["code"] for issue in report["issues"]})

    def test_real_driver_library_outside_stubs_fails(self) -> None:
        driver = self.rootfs / "usr/lib/x86_64-linux-gnu/libcuda.so.1"
        driver.parent.mkdir(parents=True, exist_ok=True)
        driver.write_bytes(b"driver\n")
        result, report = self.run_verify()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("driver_userspace.forbidden", {issue["code"] for issue in report["issues"]})

    def test_second_usr_lib_llvm_install_fails(self) -> None:
        duplicate = self.rootfs / "usr/lib/llvm-18/bin/clang"
        _write_executable(duplicate, "#!/usr/bin/env bash\necho 'clang version 18.1.8'\n")

        result, report = self.run_verify()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("llvm.single_install", {issue["code"] for issue in report["issues"]})

    def test_cc_symlink_to_clang_fails(self) -> None:
        cc = self.rootfs / "usr/bin/cc"
        cc.unlink()
        cc.symlink_to("../lib/llvm-23/bin/clang")

        result, report = self.run_verify()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("llvm.default_compiler", {issue["code"] for issue in report["issues"]})

    def test_clang_version_metadata_fallback_passes_when_host_execution_fails(self) -> None:
        clang = self.rootfs / "usr/lib/llvm-23/bin/clang"
        _write_executable(clang, "#!/usr/bin/env bash\nexit 127\n")
        (clang.parent.parent / ".vaso-llvm-build.json").write_text(
            json.dumps(
                {
                    "clang_version_output": (
                        "clang version 23.0.0git "
                        f"(https://github.com/llvm/llvm-project {LLVM_COMMIT})"
                    )
                }
            )
            + "\n",
            encoding="utf-8",
        )

        result, report = self.run_verify()

        self.assertEqual(result.returncode, 0, report)
        self.assertEqual(
            report["components"]["llvm"]["static_version"]["commit"],
            LLVM_COMMIT,
        )

    def test_gpu_probe_runs_directly_inside_insula_when_bwrap_is_unavailable(self) -> None:
        lock = json.loads(LOCK.read_text(encoding="utf-8"))
        components = lock["lines"]["cu129"]["components"]
        cuda_api = components["cuda_toolkit"]["verify"]["expect"]["cuda_api"].split(".")
        cublas = _version_parts(components["cuda_toolkit"]["verify"]["expect"]["components"]["libcublas"])
        probe_components = {
            "cudart": {"ok": True, "runtime_version": int(cuda_api[0]) * 1000 + int(cuda_api[1]) * 10},
            "cublas": {"ok": True, "runtime_version": cublas[0] * 10000 + cublas[1] * 100 + cublas[2]},
            "cudnn": {"ok": True, "runtime_version": components["cudnn"]["verify"]["expect"]["runtime_version"]},
            "nccl": {"ok": True, "runtime_version": components["nccl"]["verify"]["expect"]["runtime_version"]},
            "cusparselt": {
                "ok": True,
                "runtime_version": components["cusparselt"]["verify"]["expect"]["runtime_version"],
            },
            "cudss": {"ok": True, "runtime_version": components["cudss"]["verify"]["expect"]["runtime_version"]},
            "nvshmem": {"ok": True, "runtime_version": components["nvshmem"]["version"]},
            "tensorrt": {"ok": True, "runtime_version": components["tensorrt"]["verify"]["expect"]["runtime_version"]},
            "cuda_devices": {"ok": True, "count": 8},
        }
        completed = subprocess.CompletedProcess(
            ["/usr/bin/python3", "-c", verify.GPU_PROBE],
            0,
            stdout=json.dumps({"components": probe_components, "issues": []}),
            stderr="",
        )

        with (
            mock.patch.object(verify, "shutil_which", return_value=None),
            mock.patch.object(verify.subprocess, "run", return_value=completed) as run,
            mock.patch.dict(os.environ, {"VASO_IN_INSULA": "1"}, clear=False),
        ):
            actual_components, issues = verify.run_gpu_probe(Path("/"), lock["lines"]["cu129"])

        self.assertEqual(issues, [])
        self.assertEqual(actual_components["cuda_devices"]["count"], 8)
        argv = run.call_args.args[0]
        env = run.call_args.kwargs["env"]
        self.assertEqual(argv, ["/usr/bin/python3", "-c", verify.GPU_PROBE])
        self.assertTrue(env["LD_LIBRARY_PATH"].startswith("/run/nvidia-driver/lib:"))

    def test_default_compiler_package_fallback_passes_when_host_execution_fails(self) -> None:
        _write_executable(self.rootfs / "usr/bin/gcc", "#!/usr/bin/env bash\nexit 127\n")
        status = self.rootfs / "var/lib/dpkg/status"
        with status.open("a", encoding="utf-8") as fh:
            fh.write(
                "\n"
                "Package: gcc-13\n"
                "Status: install ok installed\n"
                "Version: 13.3.0-6ubuntu2~24.04.1\n"
            )

        result, report = self.run_verify()

        self.assertEqual(result.returncode, 0, report)
        self.assertTrue(report["components"]["llvm"]["default_compilers"]["cc"]["ok"])

    def test_missing_mlir_config_fails(self) -> None:
        (self.rootfs / "usr/lib/llvm-23/lib/cmake/mlir/MLIRConfig.cmake").unlink()

        result, report = self.run_verify()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("llvm.required_artifact", {issue["code"] for issue in report["issues"]})


if __name__ == "__main__":
    unittest.main()
