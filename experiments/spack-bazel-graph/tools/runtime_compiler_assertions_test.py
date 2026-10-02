#!/usr/bin/env python3
"""Tests for runtime_compiler_assertions.py."""

from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path


SCRIPT = Path(__file__).with_name("runtime_compiler_assertions.py")
SPEC = importlib.util.spec_from_file_location("runtime_compiler_assertions", SCRIPT)
assert SPEC is not None
assert SPEC.loader is not None
assert SCRIPT.exists(), "runtime compiler assertion tool is missing"
runtime_compilers = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = runtime_compilers
SPEC.loader.exec_module(runtime_compilers)


class RuntimeCompilerAssertionsTest(unittest.TestCase):
    def test_accepts_gcc_runtime_env_and_resolvers(self) -> None:
        errors = runtime_compilers.evaluate_runtime_compilers(
            {"CC": "/usr/bin/gcc", "CXX": "/usr/bin/g++"},
            triton_c_compiler="/usr/bin/gcc",
            inductor_cxx_compiler="/usr/bin/g++",
        )

        self.assertEqual(errors, [])

    def test_rejects_inherited_clang_runtime_env(self) -> None:
        errors = runtime_compilers.evaluate_runtime_compilers(
            {"CC": "/usr/lib/llvm-23/bin/clang", "CXX": "/usr/lib/llvm-23/bin/clang++"},
            triton_c_compiler="/usr/lib/llvm-23/bin/clang",
            inductor_cxx_compiler="/usr/lib/llvm-23/bin/clang++",
        )

        self.assertEqual(
            errors,
            [
                "CC=/usr/lib/llvm-23/bin/clang; expected /usr/bin/gcc",
                "CXX=/usr/lib/llvm-23/bin/clang++; expected /usr/bin/g++",
                "triton _find_compiler('c') resolved /usr/lib/llvm-23/bin/clang; expected /usr/bin/gcc",
                "inductor get_cpp_compiler() resolved /usr/lib/llvm-23/bin/clang++; expected /usr/bin/g++",
            ],
        )

    def test_accepts_versioned_rootfs_gcc_aliases(self) -> None:
        errors = runtime_compilers.evaluate_runtime_compilers(
            {"CC": "/usr/bin/gcc-13", "CXX": "/usr/bin/g++-13"},
            triton_c_compiler="/usr/bin/gcc-13",
            inductor_cxx_compiler="/usr/bin/g++-13",
        )

        self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()
