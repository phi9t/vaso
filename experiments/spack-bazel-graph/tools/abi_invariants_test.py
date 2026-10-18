#!/usr/bin/env python3
"""Synthetic fixture tests for abi_invariants.py."""

from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).with_name("abi_invariants.py")
SPEC = importlib.util.spec_from_file_location("abi_invariants", SCRIPT)
assert SPEC is not None
assert SPEC.loader is not None
assert SCRIPT.exists(), "ABI invariant checker is missing"
abi = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = abi
SPEC.loader.exec_module(abi)


def elf(
    path: str,
    *,
    needed: list[str] | None = None,
    undefined: list[str] | None = None,
    defined: list[str] | None = None,
    comments: list[str] | None = None,
    string_markers: list[str] | None = None,
) -> object:
    return abi.ElfFacts(
        path=Path(path),
        needed=tuple(needed or []),
        undefined=tuple(undefined or []),
        defined=tuple(defined or []),
        comments=tuple(comments or ["GCC: (Ubuntu 13.3.0-6ubuntu2~24.04) 13.3.0"]),
        string_markers=tuple(string_markers or []),
    )


class AbiInvariantsTest(unittest.TestCase):
    def test_static_torch_profile_accepts_gcc_only_fixture(self) -> None:
        facts = [
            elf(
                "/prefix/torch/lib/libtorch_cpu.so",
                needed=["libstdc++.so.6", "libgcc_s.so.1", "libgomp.so.1"],
                undefined=["std::__cxx11::basic_string@GLIBCXX_3.4.32"],
                defined=["torch::ok", "_ZN3c1010Dispatcher9singletonEv"],
            ),
            elf(
                "/prefix/torch/lib/libtorch_python.so",
                needed=["libstdc++.so.6", "libgcc_s.so.1"],
                defined=["_ZN3c1010Dispatcher9singletonEv"],
            ),
            elf(
                "/prefix/triton/lib/libtriton.so",
                needed=["libstdc++.so.6", "libgcc_s.so.1"],
                undefined=["operator new@GLIBCXX_3.4.31"],
                defined=["PyInit_libtriton"],
            ),
        ]

        errors = abi.check_static_invariants(
            facts,
            profile="torch",
            llvm_archive_comments={
                Path("/usr/lib/llvm-23/lib/libLLVMCore.a"): [
                    "GCC: (Ubuntu 13.3.0-6ubuntu2~24.04) 13.3.0"
                ]
            },
        )

        self.assertEqual(errors, [])

    def test_static_checks_reject_cxx_runtime_old_abi_and_visibility_breaks(self) -> None:
        facts = [
            elf(
                "/prefix/torch/lib/liba.so",
                needed=["libc++.so.1", "libunwind.so.1"],
                undefined=["_ZNSs4_Rep@GLIBCXX_3.4", "__kmpc_fork_call"],
                defined=["__cxa_throw", "_ZN4absl12duplicateEv"],
                string_markers=["basic_string::_M_construct null not valid"],
                comments=["clang version 23.0.0git 35901313"],
            ),
            elf(
                "/prefix/torch/lib/libb.so",
                needed=["libstdc++.so.6"],
                undefined=["std::__cxx11::basic_string@GLIBCXX_3.4.34"],
                defined=["_Unwind_RaiseException", "_ZN4absl12duplicateEv"],
            ),
            elf(
                "/prefix/triton/lib/libtriton.so",
                needed=["libstdc++.so.6"],
                defined=["PyInit_libtriton", "_ZN4llvm6hiddenEv"],
                comments=["GCC: (Ubuntu 13.3.0-6ubuntu2~24.04) 13.3.0"],
            ),
        ]

        errors = abi.check_static_invariants(
            facts,
            profile="torch",
            llvm_archive_comments={Path("/usr/lib/llvm-23/lib/libLLVMCore.a"): ["clang version 23.0.0git 35901313"]},
        )

        self.assertIn("I1 /prefix/torch/lib/liba.so: NEEDED forbidden C++ runtime libc++.so.1", errors)
        self.assertIn("I1 /prefix/torch/lib/libb.so: imports GLIBCXX_3.4.34 above max GLIBCXX_3.4.33", errors)
        self.assertIn("I2 /prefix/torch/lib/liba.so: exports static-libstdc++ symbol __cxa_throw", errors)
        self.assertIn("I2 /prefix/torch/lib/liba.so: contains static-libstdc++ string marker", errors)
        self.assertIn("I3 /prefix/torch/lib/liba.so: imports old C++98 string ABI symbol _ZNSs4_Rep@GLIBCXX_3.4", errors)
        self.assertIn("I4 /prefix/torch/lib/liba.so: NEEDED forbidden unwinder libunwind.so.1", errors)
        self.assertIn("I4 /prefix/torch/lib/libb.so: exports unwinder symbol _Unwind_RaiseException", errors)
        self.assertIn("I6 /prefix/triton/lib/libtriton.so: libtriton exports _ZN4llvm6hiddenEv; expected only PyInit_libtriton", errors)
        self.assertIn("I6 duplicate exported symbol _ZN4absl12duplicateEv: /prefix/torch/lib/liba.so, /prefix/torch/lib/libb.so", errors)
        self.assertIn("I7 /usr/lib/llvm-23/lib/libLLVMCore.a: LLVM archive has clang .comment in torch profile", errors)
        self.assertIn("I8 /prefix/torch/lib/liba.so: torch profile ELF has clang .comment", errors)

    def test_probe_maps_accept_expected_runtime_libraries(self) -> None:
        maps = "\n".join(
            [
                "7f /usr/lib/x86_64-linux-gnu/libstdc++.so.6.0.33",
                "7f /usr/lib/x86_64-linux-gnu/libgcc_s.so.1",
                "7f /usr/lib/x86_64-linux-gnu/libgomp.so.1.0.0",
                "7f /run/nvidia-driver/lib/libcuda.so.1",
                "7f /usr/local/cuda/lib64/libcudart.so.13",
            ]
        )

        self.assertEqual(abi.check_probe_maps(maps, line="cu130"), [])

    def test_probe_maps_accept_merged_usr_runtime_aliases(self) -> None:
        maps = "\n".join(
            [
                "7f /lib/x86_64-linux-gnu/libstdc++.so.6.0.33",
                "7f /lib/x86_64-linux-gnu/libgomp.so.1.0.0",
                "7f /run/nvidia-driver/lib/libcuda.so.1",
                "7f /usr/local/cuda/lib64/libcudart.so.13",
            ]
        )

        self.assertEqual(abi.check_probe_maps(maps, line="cu130"), [])

    def test_probe_maps_reject_multiple_runtimes_and_mixed_cuda_major(self) -> None:
        maps = "\n".join(
            [
                "7f /opt/a/libstdc++.so.6.0.32",
                "7f /usr/lib/x86_64-linux-gnu/libstdc++.so.6.0.33",
                "7f /opt/llvm/lib/libomp.so",
                "7f /run/nvidia-driver/lib/libcuda.so.1",
                "7f /other/lib/libcuda.so.1",
                "7f /usr/local/cuda/lib64/libcudart.so.12",
            ]
        )

        errors = abi.check_probe_maps(maps, line="cu130")

        self.assertIn("I1 maps: expected exactly one libstdc++.so.6, found 2", errors)
        self.assertIn("I5 maps: forbidden OpenMP runtime mapped /opt/llvm/lib/libomp.so", errors)
        self.assertIn("I11 maps: expected exactly one libcuda.so.1, found 2", errors)
        self.assertIn("I11 maps: libcudart /usr/local/cuda/lib64/libcudart.so.12 does not match cu130 CUDA major 13", errors)

    def test_probe_env_sets_triton_cuda_tool_paths(self) -> None:
        with tempfile.TemporaryDirectory(dir=os.environ.get("TEST_TMPDIR")) as tmp:
            root = Path(tmp)
            env = abi.probe_env(
                {"VASO_CUDA_HOME": "/usr/local/cuda"},
                out_dir=root / "probe",
                python_prefix=root / "python",
                prefixes={},
                runtime_prefixes={},
            )

        self.assertEqual(env["TRITON_CUDACRT_PATH"], "/usr/local/cuda/include")
        self.assertEqual(env["TRITON_CUDART_PATH"], "/usr/local/cuda/include")
        self.assertEqual(env["TRITON_LIBCUDA_PATH"], "/run/nvidia-driver/lib")
        self.assertEqual(env["TRITON_LIBDEVICE_PATH"], "/usr/local/cuda/nvvm/libdevice/libdevice.10.bc")
        self.assertEqual(env["TRITON_PTXAS_PATH"], "/usr/local/cuda/bin/ptxas")
        self.assertEqual(env["MOSAIC_GPU_NVSHMEM_BC_PATH"], "/usr/local/cuda/lib64/libnvshmem_device.bc")

    def test_static_result_rejects_missing_prefix(self) -> None:
        result = abi._static_result(
            {"torch": Path("/definitely/missing/torch")},
            llvm_prefix=None,
            profile="torch",
        )

        self.assertIn("prefix torch is missing: /definitely/missing/torch", result["errors"])


if __name__ == "__main__":
    unittest.main()
