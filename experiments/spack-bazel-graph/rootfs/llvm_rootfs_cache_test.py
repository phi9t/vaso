#!/usr/bin/env python3
"""Behavior tests for the shared rootfs LLVM cache plan."""

from __future__ import annotations

import importlib.util
import json
import sys
import unittest
from pathlib import Path


ROOTFS_DIR = Path(__file__).resolve().parent
MODULE = ROOTFS_DIR / "llvm_rootfs_cache.py"
LOCK = ROOTFS_DIR / "cuda_ecosystem.lock.json"
LLVM_COMMIT = "35901313800ea6e6cbeb9226e51c7c4b29bfc40e"

SPEC = importlib.util.spec_from_file_location("llvm_rootfs_cache", MODULE)
assert SPEC and SPEC.loader
llvm_rootfs_cache = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = llvm_rootfs_cache
SPEC.loader.exec_module(llvm_rootfs_cache)


class LlvmRootfsCacheTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.lock = json.loads(LOCK.read_text(encoding="utf-8"))
        cls.llvm = cls.lock["components"]["llvm"]

    def test_cache_plan_is_keyed_under_estate_rootfs_lines(self) -> None:
        plan = llvm_rootfs_cache.build_plan(self.lock, Path("/estate"))

        self.assertRegex(plan["cache_key"], rf"^{LLVM_COMMIT}-[0-9a-f]{{8}}$")
        self.assertEqual(plan["cache_dir"], f"/estate/rootfs-lines/_llvm/{plan['cache_key']}")
        self.assertEqual(plan["docker_image"], f"vaso-llvm-{plan['cache_key']}:latest")
        self.assertEqual(
            plan["build_base_image"]["reference"],
            "ubuntu:24.04@sha256:496754492fb28b4d3049432f2ca787449331e23fb14f0dd3fffea86bf5a93eb4",
        )
        self.assertEqual(plan["max_jobs"], 64)

    def test_cmake_args_match_the_locked_rootfs_configuration(self) -> None:
        args = set(llvm_rootfs_cache.cmake_args(self.llvm))

        self.assertIn("-DCMAKE_INSTALL_PREFIX:PATH=/usr/lib/llvm-23", args)
        self.assertIn("-DCMAKE_C_COMPILER:FILEPATH=/usr/bin/gcc", args)
        self.assertIn("-DCMAKE_CXX_COMPILER:FILEPATH=/usr/bin/g++", args)
        self.assertIn("-DLLVM_ENABLE_PROJECTS:STRING=llvm;clang;lld;mlir", args)
        self.assertIn("-DLLVM_TARGETS_TO_BUILD:STRING=X86;NVPTX;AMDGPU", args)
        self.assertIn("-DLLVM_ENABLE_ASSERTIONS:BOOL=OFF", args)
        self.assertIn("-DLLVM_ENABLE_ZSTD:BOOL=OFF", args)
        self.assertIn("-DLLVM_APPEND_VC_REV:BOOL=ON", args)
        self.assertIn("-DLLVM_FORCE_VC_REPOSITORY:STRING=https://github.com/llvm/llvm-project", args)
        self.assertIn(f"-DLLVM_FORCE_VC_REVISION:STRING={LLVM_COMMIT}", args)
        self.assertIn("-DMLIR_ENABLE_BINDINGS_PYTHON:BOOL=OFF", args)
        self.assertIn("-DLLVM_INSTALL_UTILS:BOOL=ON", args)
        self.assertIn("-DBUILD_SHARED_LIBS:BOOL=OFF", args)

    def test_cache_key_changes_when_patch_sha_changes(self) -> None:
        mutated = json.loads(json.dumps(self.llvm))
        original = llvm_rootfs_cache.cache_key(self.llvm)
        mutated["patches"][0]["sha256"] = "0" * 64

        self.assertNotEqual(llvm_rootfs_cache.cache_key(mutated), original)


if __name__ == "__main__":
    unittest.main()
