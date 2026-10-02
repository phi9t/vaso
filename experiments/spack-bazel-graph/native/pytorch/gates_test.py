#!/usr/bin/env python3
"""Tests for the native PyTorch D3 gate runner."""

from __future__ import annotations

import importlib.util
import json
import os
import stat
import sys
import tempfile
import types
import unittest
from pathlib import Path


SCRIPT = Path(__file__).with_name("gates.py")
EXPERIMENT_ROOT = SCRIPT.parents[2]
PY_MINOR = "3." + "13"
PYTHON_ABI_DIR = "python" + PY_MINOR


def temporary_directory():
    base = os.environ.get("TEST_TMPDIR") or os.environ.get("VASO_AGENT_IO_ROOT")
    return tempfile.TemporaryDirectory(prefix="gates-case-", dir=base)


def load_gates_module():
    if not SCRIPT.is_file():
        raise AssertionError("native PyTorch D3 gate runner is missing")
    spec = importlib.util.spec_from_file_location("pytorch_gates", SCRIPT)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class NativePytorchGatesTest(unittest.TestCase):
    def setUp(self) -> None:
        self.gates = load_gates_module()

    def make_torch_prefix(self, root: Path) -> Path:
        prefix = root / "torch-prefix"
        (prefix / "lib" / PYTHON_ABI_DIR / "site-packages" / "torch" / "lib").mkdir(parents=True)
        return prefix

    def make_python_prefix(self, root: Path, script: str = "#!/bin/sh\nexit 0\n") -> Path:
        prefix = root / "python-prefix"
        python = prefix / "bin" / "python3"
        python.parent.mkdir(parents=True)
        python.write_text(script, encoding="utf-8")
        python.chmod(python.stat().st_mode | stat.S_IXUSR)
        (prefix / "lib" / PYTHON_ABI_DIR / "site-packages").mkdir(parents=True)
        return prefix

    def make_runtime_prefix(self, root: Path, name: str) -> Path:
        prefix = root / name
        (prefix / "lib" / PYTHON_ABI_DIR / "site-packages" / name.replace("-", "_")).mkdir(parents=True)
        return prefix

    def base_args(self, torch_prefix: Path, ref_prefix: Path, python_prefix: Path, out_dir: Path) -> list[str]:
        return [
            "--torch-prefix",
            str(torch_prefix),
            "--torch-ref-prefix",
            str(ref_prefix),
            "--python-prefix",
            str(python_prefix),
            "--out-dir",
            str(out_dir),
        ]

    def test_plan_exports_every_d3_gate_with_explicit_prefix_environment(self) -> None:
        with temporary_directory() as tmp:
            root = Path(tmp)
            torch_prefix = self.make_torch_prefix(root)
            python_prefix = self.make_python_prefix(root)
            runtime_prefixes = {
                name: self.make_runtime_prefix(root, name)
                for name in (
                    "typing-extensions",
                    "sympy",
                    "filelock",
                    "jinja2",
                    "networkx",
                    "fsspec",
                    "triton",
                )
            }
            ref_prefix = root / "reference-prefix"
            ref_prefix.mkdir()
            out_dir = root / "gates"
            argv = self.base_args(torch_prefix, ref_prefix, python_prefix, out_dir)
            for name, prefix in runtime_prefixes.items():
                argv.extend(["--runtime-prefix", f"{name}={prefix}"])
            rc = self.gates.main(argv + [
                "--plan-out",
                str(root / "gate-plan.json"),
            ])

            plan_doc = json.loads((root / "gate-plan.json").read_text(encoding="utf-8"))

        self.assertEqual(rc, 0)
        self.assertFalse(plan_doc["execute_requested"])
        self.assertEqual(plan_doc["python"], str(python_prefix / "bin" / "python3"))
        self.assertEqual(plan_doc["env"]["TORCH_PREFIX"], str(torch_prefix))
        self.assertEqual(plan_doc["env"]["TORCH_REF_PREFIX"], str(ref_prefix))
        self.assertEqual(plan_doc["env"]["TRITON_CUDACRT_PATH"], "/usr/local/cuda/include")
        self.assertEqual(plan_doc["env"]["TRITON_CUDART_PATH"], "/usr/local/cuda/include")
        self.assertEqual(plan_doc["env"]["TRITON_PTXAS_PATH"], "/usr/local/cuda/bin/ptxas")
        self.assertEqual(plan_doc["env"]["TRITON_PTXAS_BLACKWELL_PATH"], "/usr/local/cuda/bin/ptxas")
        self.assertEqual(plan_doc["env"]["TRITON_CUOBJDUMP_PATH"], "/usr/local/cuda/bin/cuobjdump")
        self.assertEqual(plan_doc["env"]["TRITON_NVDISASM_PATH"], "/usr/local/cuda/bin/nvdisasm")
        self.assertEqual(
            plan_doc["env"]["TRITON_LIBDEVICE_PATH"],
            "/usr/local/cuda/nvvm/libdevice/libdevice.10.bc",
        )
        self.assertEqual(plan_doc["env"]["TRITON_LIBCUDA_PATH"], "/run/nvidia-driver/lib")
        pythonpath = plan_doc["env"]["PYTHONPATH"].split(os.pathsep)
        self.assertEqual(pythonpath[0], str(torch_prefix / "lib" / PYTHON_ABI_DIR / "site-packages"))
        self.assertEqual(
            set(pythonpath[1:]),
            {
                str(prefix / "lib" / PYTHON_ABI_DIR / "site-packages")
                for prefix in runtime_prefixes.values()
            },
        )
        self.assertTrue(
            plan_doc["env"]["LD_LIBRARY_PATH"].startswith(
                "/run/nvidia-driver/lib" + os.pathsep
                + str(torch_prefix / "lib") + os.pathsep
                + str(torch_prefix / "lib" / PYTHON_ABI_DIR / "site-packages" / "torch" / "lib")
            )
        )
        for key in (
            "TMPDIR",
            "TORCHINDUCTOR_CACHE_DIR",
            "TRITON_CACHE_DIR",
            "TORCHELASTIC_LOG_DIR",
        ):
            self.assertTrue(plan_doc["env"][key].startswith(str(out_dir)), key)
        self.assertEqual(plan_doc["out_dir"], str(out_dir))
        host_tmp = Path(os.sep) / ("t" + "mp")
        with self.assertRaises(ValueError):
            Path(plan_doc["out_dir"]).resolve(strict=False).relative_to(host_tmp)

        gates = {gate["name"]: gate for gate in plan_doc["gates"]}
        self.assertEqual(
            list(gates),
            [
                "abi_prefix_parity",
                "import_cuda",
                "feature_parity",
                "b200_matmul",
                "cudnn_conv",
                "nccl_distributed",
                "gloo_distributed",
                "torch_compile",
                "sdpa_flash",
                "torchvision_ops",
                "torchaudio_ops",
                "collect_env",
                "benchmark_smoke",
            ],
        )
        blocking = {name for name, gate in gates.items() if gate["blocking"]}
        self.assertEqual(
            blocking,
            {
                "abi_prefix_parity",
                "import_cuda",
                "feature_parity",
                "b200_matmul",
                "cudnn_conv",
                "nccl_distributed",
                "gloo_distributed",
                "sdpa_flash",
                "collect_env",
            },
        )
        self.assertEqual(gates["feature_parity"]["group"], "core")
        self.assertIn("torch.backends.cudnn.version() == 92400", " ".join(gates["feature_parity"]["command"]))
        self.assertIn("(2, 30, 7)", " ".join(gates["feature_parity"]["command"]))
        self.assertIn("USE_MAGMA", " ".join(gates["feature_parity"]["command"]))
        self.assertEqual(gates["torch_compile"]["group"], "compile")
        self.assertEqual(gates["torchvision_ops"]["group"], "vision_audio")
        self.assertEqual(gates["torchaudio_ops"]["group"], "vision_audio")
        self.assertIn("import torchvision", " ".join(gates["torchvision_ops"]["command"]))
        self.assertIn("nms(boxes, scores, 0.5)", " ".join(gates["torchvision_ops"]["command"]))
        self.assertIn("keep.is_cuda", " ".join(gates["torchvision_ops"]["command"]))
        self.assertIn("import torchaudio", " ".join(gates["torchaudio_ops"]["command"]))
        self.assertIn("torchaudio.functional.resample", " ".join(gates["torchaudio_ops"]["command"]))
        self.assertIn("tools/abi_parity.py", " ".join(gates["abi_prefix_parity"]["command"]))
        self.assertIn("--reference", gates["abi_prefix_parity"]["command"])
        for gate in gates.values():
            command = gate["command"]
            if command[0] != "/usr/bin/python3":
                self.assertEqual(command[0], str(python_prefix / "bin" / "python3"))
        nccl_command = gates["nccl_distributed"]["command"]
        self.assertIn("--log-dir", nccl_command)
        self.assertIn(str(out_dir / "torchelastic"), nccl_command)
        separator_index = nccl_command.index("--")
        self.assertEqual(nccl_command[separator_index + 1], "-c")
        self.assertEqual(gates["collect_env"]["stdout"], str(out_dir / "collect_env.txt"))
        self.assertEqual(gates["benchmark_smoke"]["stdout"], str(out_dir / "benchmark-help.txt"))

    def test_feature_parity_accepts_versioned_cuda_symlink_target(self) -> None:
        with temporary_directory() as tmp:
            root = Path(tmp)
            versioned_cuda = root / "usr" / "local" / "cuda-13.0"
            cuda_home = root / "usr" / "local" / "cuda"
            target_lib = versioned_cuda / "targets" / "x86_64-linux" / "lib"
            target_lib.mkdir(parents=True)
            (target_lib / "libcudnn.so.9.24.0").write_text("", encoding="utf-8")
            (target_lib / "libnccl.so.2.30.7").write_text("", encoding="utf-8")
            (versioned_cuda / "lib64").symlink_to(target_lib)
            (target_lib / "libcudnn.so.9").symlink_to("libcudnn.so.9.24.0")
            (target_lib / "libnccl.so.2").symlink_to("libnccl.so.2.30.7")
            cuda_home.symlink_to(versioned_cuda)
            torch_prefix = self.make_torch_prefix(root)
            torch_lib = torch_prefix / "lib" / PYTHON_ABI_DIR / "site-packages" / "torch" / "lib"
            (torch_lib / "libtorch_cuda.so").write_bytes(b"cudssCreate")
            (torch_lib / "libtorch_nvshmem.so").write_bytes(b"nvshmemx_cumodule_init")

            fake_torch = types.SimpleNamespace(
                backends=types.SimpleNamespace(
                    cudnn=types.SimpleNamespace(version=lambda: 92400),
                    cuda=types.SimpleNamespace(
                        flash_sdp_enabled=lambda: True,
                        mem_efficient_sdp_enabled=lambda: True,
                    ),
                ),
                cuda=types.SimpleNamespace(nccl=types.SimpleNamespace(version=lambda: (2, 30, 7))),
                __config__=types.SimpleNamespace(
                    show=lambda: "USE_CUSPARSELT USE_KINETO"
                ),
            )
            previous_torch = sys.modules.get("torch")
            previous_ld = os.environ.get("LD_LIBRARY_PATH")
            previous_cuda_home = os.environ.get("VASO_CUDA_HOME")
            previous_torch_prefix = os.environ.get("TORCH_PREFIX")
            sys.modules["torch"] = fake_torch
            os.environ["LD_LIBRARY_PATH"] = str(cuda_home / "lib64")
            os.environ["VASO_CUDA_HOME"] = str(cuda_home)
            os.environ["TORCH_PREFIX"] = str(torch_prefix)
            try:
                exec(self.gates.FEATURE_PARITY, {"__name__": "__main__"})
            finally:
                if previous_torch is None:
                    del sys.modules["torch"]
                else:
                    sys.modules["torch"] = previous_torch
                if previous_ld is None:
                    os.environ.pop("LD_LIBRARY_PATH", None)
                else:
                    os.environ["LD_LIBRARY_PATH"] = previous_ld
                if previous_cuda_home is None:
                    os.environ.pop("VASO_CUDA_HOME", None)
                else:
                    os.environ["VASO_CUDA_HOME"] = previous_cuda_home
                if previous_torch_prefix is None:
                    os.environ.pop("TORCH_PREFIX", None)
                else:
                    os.environ["TORCH_PREFIX"] = previous_torch_prefix

    def test_execute_records_results_and_returns_failure_for_blocking_gate(self) -> None:
        with temporary_directory() as tmp:
            root = Path(tmp)
            torch_prefix = self.make_torch_prefix(root)
            ref_prefix = root / "reference-prefix"
            ref_prefix.mkdir()
            python_prefix = self.make_python_prefix(
                root,
                "#!/bin/sh\necho \"$@\" >> \"$GATE_TRACE\"\nexit 7\n",
            )
            trace = root / "trace.txt"
            rc = self.gates.main(self.base_args(torch_prefix, ref_prefix, python_prefix, root / "gates") + [
                "--plan-out",
                str(root / "gate-plan.json"),
                "--results-out",
                str(root / "gate-results.json"),
                "--only",
                "import_cuda",
                "--execute",
            ], environ={"GATE_TRACE": str(trace)})

            results = json.loads((root / "gate-results.json").read_text(encoding="utf-8"))
            trace_text = trace.read_text(encoding="utf-8")

        self.assertEqual(rc, 1)
        self.assertEqual(results["verdict"], "failed")
        self.assertEqual(results["failed_blocking_gates"], ["import_cuda"])
        self.assertEqual(results["results"][0]["returncode"], 7)
        self.assertIn("-c", trace_text)

    def test_nonblocking_benchmark_failure_is_recorded_without_failing_run(self) -> None:
        with temporary_directory() as tmp:
            root = Path(tmp)
            torch_prefix = self.make_torch_prefix(root)
            ref_prefix = root / "reference-prefix"
            ref_prefix.mkdir()
            python_prefix = self.make_python_prefix(root, "#!/bin/sh\nexit 9\n")
            rc = self.gates.main(self.base_args(torch_prefix, ref_prefix, python_prefix, root / "gates") + [
                "--plan-out",
                str(root / "gate-plan.json"),
                "--results-out",
                str(root / "gate-results.json"),
                "--only",
                "benchmark_smoke",
                "--execute",
            ])

            results = json.loads((root / "gate-results.json").read_text(encoding="utf-8"))

        self.assertEqual(rc, 0)
        self.assertEqual(results["verdict"], "passed")
        self.assertEqual(results["failed_blocking_gates"], [])
        self.assertEqual(results["results"][0]["returncode"], 9)

    def test_exec_error_is_recorded_as_blocking_gate_failure(self) -> None:
        with temporary_directory() as tmp:
            root = Path(tmp)
            torch_prefix = self.make_torch_prefix(root)
            ref_prefix = root / "reference-prefix"
            ref_prefix.mkdir()
            python_prefix = root / "python-prefix"
            (python_prefix / "bin").mkdir(parents=True)
            rc = self.gates.main(self.base_args(torch_prefix, ref_prefix, python_prefix, root / "gates") + [
                "--results-out",
                str(root / "gate-results.json"),
                "--only",
                "import_cuda",
                "--execute",
            ])

            results = json.loads((root / "gate-results.json").read_text(encoding="utf-8"))

        self.assertEqual(rc, 1)
        self.assertEqual(results["failed_blocking_gates"], ["import_cuda"])
        self.assertEqual(results["results"][0]["returncode"], 127)
        self.assertIn("exec_error", results["results"][0])

    def test_failed_compile_gate_does_not_block_core_but_guard_can_require_it(self) -> None:
        with temporary_directory() as tmp:
            root = Path(tmp)
            torch_prefix = self.make_torch_prefix(root)
            ref_prefix = root / "reference-prefix"
            ref_prefix.mkdir()
            python_prefix = self.make_python_prefix(root, "#!/bin/sh\nexit 9\n")
            results_out = root / "gate-results.json"
            rc = self.gates.main(self.base_args(torch_prefix, ref_prefix, python_prefix, root / "gates") + [
                "--results-out",
                str(results_out),
                "--only",
                "torch_compile",
                "--execute",
            ])
            core_guard_rc = self.gates.main([
                "--guard-results",
                str(results_out),
                "--require-group",
                "core",
            ])
            compile_guard_rc = self.gates.main([
                "--guard-results",
                str(results_out),
                "--require-group",
                "compile",
            ])

            results = json.loads(results_out.read_text(encoding="utf-8"))

        self.assertEqual(rc, 0)
        self.assertEqual(results["failed_blocking_gates"], [])
        self.assertEqual(results["results"][0]["group"], "compile")
        self.assertEqual(core_guard_rc, 0)
        self.assertEqual(compile_guard_rc, 1)

    def test_vision_audio_group_can_be_promoted_to_blocking(self) -> None:
        with temporary_directory() as tmp:
            root = Path(tmp)
            torch_prefix = self.make_torch_prefix(root)
            ref_prefix = root / "reference-prefix"
            ref_prefix.mkdir()
            python_prefix = self.make_python_prefix(root, "#!/bin/sh\nexit 13\n")
            results_out = root / "gate-results.json"
            rc = self.gates.main(self.base_args(torch_prefix, ref_prefix, python_prefix, root / "gates") + [
                "--results-out",
                str(results_out),
                "--only",
                "torchvision_ops,torchaudio_ops",
                "--block-group",
                "vision_audio",
                "--execute",
            ])
            vision_guard_rc = self.gates.main([
                "--guard-results",
                str(results_out),
                "--require-group",
                "vision_audio",
            ])

            results = json.loads(results_out.read_text(encoding="utf-8"))

        self.assertEqual(rc, 1)
        self.assertEqual(results["failed_blocking_gates"], ["torchvision_ops", "torchaudio_ops"])
        self.assertEqual(vision_guard_rc, 1)

    def test_runtime_prefix_libraries_are_on_ld_library_path(self) -> None:
        with temporary_directory() as tmp:
            root = Path(tmp)
            torch_prefix = self.make_torch_prefix(root)
            ref_prefix = root / "reference-prefix"
            ref_prefix.mkdir()
            python_prefix = self.make_python_prefix(root)
            runtime = self.make_runtime_prefix(root, "torchvision")
            (runtime / "lib").mkdir(exist_ok=True)
            (runtime / "lib64").mkdir()
            rc = self.gates.main(self.base_args(torch_prefix, ref_prefix, python_prefix, root / "gates") + [
                "--runtime-prefix",
                f"torchvision={runtime}",
                "--plan-out",
                str(root / "gate-plan.json"),
            ])

            plan_doc = json.loads((root / "gate-plan.json").read_text(encoding="utf-8"))

        self.assertEqual(rc, 0)
        ld_entries = plan_doc["env"]["LD_LIBRARY_PATH"].split(os.pathsep)
        self.assertIn(str(runtime / "lib"), ld_entries)
        self.assertIn(str(runtime / "lib64"), ld_entries)

    def test_refuses_host_tmp_output_directory(self) -> None:
        with temporary_directory() as tmp:
            root = Path(tmp)
            torch_prefix = self.make_torch_prefix(root)
            ref_prefix = root / "reference-prefix"
            ref_prefix.mkdir()
            python_prefix = self.make_python_prefix(root)
            rc = self.gates.main(
                self.base_args(
                    torch_prefix,
                    ref_prefix,
                    python_prefix,
                    Path(os.sep) / "tmp" / "native-pytorch-gates",
                )
            )

        self.assertEqual(rc, 2)


if __name__ == "__main__":
    unittest.main()
