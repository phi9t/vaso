#!/usr/bin/env python3
"""Schema and consistency checks for cuda_ecosystem.lock.json."""

from __future__ import annotations

import json
import re
import unittest
from pathlib import Path


EXPECTED_LINES = {"cu129", "cu130"}
EXPECTED_COMPONENTS = {
    "cuda_toolkit",
    "cudnn",
    "cusparselt",
    "cudss",
    "nvshmem",
    "nccl",
    "tensorrt",
}
SHARED_VERSION_COMPONENTS = {
    "cudnn",
    "cusparselt",
    "cudss",
    "nvshmem",
    "nccl",
    "tensorrt",
}
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
LLVM_COMMIT = "35901313800ea6e6cbeb9226e51c7c4b29bfc40e"
LLVM_TARBALL_SHA256 = "c3f06997045e0f9aa43628616df3217f229aa43d478bd78bd445e2448637d4ef"
LLVM_PATCH_SHA256S = {
    "generated.patch": "3072a498ccea4daab771fb9ebf9ffde12ff5718f2761d26cfa69f5031f778de9",
    "b514740398.patch": "ec5f91ebffd137c946cc342e0f7e96fbdf293ab7a5220761ddef990fa2c24396",
}
JAX_COMPILER_CONTRACT = {
    "applies_to": ["py-jax@0.10.2", "py-jaxlib@0.10.2"],
    "host_compiler": "/usr/lib/llvm-23/bin/clang",
    "host_cxx_compiler": "/usr/lib/llvm-23/bin/clang++",
    "cuda_compiler": "nvcc",
    "llvm": "llvm@23.0.0 +clang +lld +mlir",
}
TORCH_COMPILER_CONTRACT = {
    "host_compiler": "/usr/bin/gcc",
    "host_cxx_compiler": "/usr/bin/g++",
    "cuda_compiler": "nvcc",
}


def _load_lock() -> dict[str, object]:
    here = Path(__file__).resolve()
    candidates = [
        here.with_name("cuda_ecosystem.lock.json"),
        here.parent / "rootfs" / "cuda_ecosystem.lock.json",
    ]
    for parent in here.parents:
        candidates.append(parent / "rootfs" / "cuda_ecosystem.lock.json")
    for candidate in candidates:
        if candidate.exists():
            with candidate.open(encoding="utf-8") as fh:
                return json.load(fh)
    raise FileNotFoundError("could not find cuda_ecosystem.lock.json")


class CudaEcosystemLockTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.lock = _load_lock()

    def test_top_level_schema(self) -> None:
        self.assertEqual(self.lock["schema_version"], 1)
        self.assertEqual(set(self.lock["lines"]), EXPECTED_LINES)
        self.assertIn("consumers", self.lock)
        self.assertIn("conflicts", self.lock)
        self.assertIn("components", self.lock)
        for consumer in ["torch", "triton", "jax"]:
            self.assertIn(consumer, self.lock["consumers"])

    def test_llvm_component_is_line_independent_and_pinned(self) -> None:
        llvm = self.lock["components"]["llvm"]

        self.assertEqual(llvm["version"], "23.0.0git")
        self.assertEqual(llvm["commit"], LLVM_COMMIT)
        self.assertEqual(llvm["install_layout"]["prefix"], "/usr/lib/llvm-23")
        self.assertEqual(llvm["source"]["kind"], "tarball")
        self.assertEqual(
            llvm["source"]["url"],
            f"https://storage.googleapis.com/mirror.tensorflow.org/github.com/llvm/llvm-project/archive/{LLVM_COMMIT}.tar.gz",
        )
        self.assertIn(f"https://github.com/llvm/llvm-project/archive/{LLVM_COMMIT}.tar.gz", llvm["source"]["urls"])
        self.assertEqual(llvm["source"]["sha256"], LLVM_TARBALL_SHA256)
        self._assert_source_is_pinned(llvm["source"])

        patches = {patch["name"]: patch for patch in llvm["patches"]}
        self.assertEqual(set(patches), set(LLVM_PATCH_SHA256S))
        for name, sha256 in LLVM_PATCH_SHA256S.items():
            self.assertEqual(patches[name]["sha256"], sha256)
            self.assertRegex(patches[name]["url"], rf"/third_party/llvm/{re.escape(name)}$")
            self.assertEqual(patches[name]["strip"], "-p1")

        cmake = llvm["build"]["cmake"]
        build_base = llvm["build"]["base_image"]
        self.assertEqual(
            build_base["reference"],
            f'{build_base["repository"]}:{build_base["tag"]}@{build_base["digest"]}',
        )
        self.assertRegex(build_base["digest"], r"^sha256:[0-9a-f]{64}$")
        self.assertEqual(build_base["ubuntu_version"], "24.04")
        self.assertEqual(cmake["LLVM_ENABLE_PROJECTS"], "llvm;clang;lld;mlir")
        self.assertEqual(cmake["LLVM_TARGETS_TO_BUILD"], "X86;NVPTX;AMDGPU")
        self.assertEqual(cmake["CMAKE_BUILD_TYPE"], "Release")
        self.assertEqual(cmake["LLVM_ENABLE_ASSERTIONS"], "OFF")
        self.assertEqual(cmake["LLVM_ENABLE_ZSTD"], "OFF")
        self.assertEqual(cmake["LLVM_APPEND_VC_REV"], "ON")
        self.assertEqual(cmake["LLVM_FORCE_VC_REPOSITORY"], "https://github.com/llvm/llvm-project")
        self.assertEqual(cmake["LLVM_FORCE_VC_REVISION"], LLVM_COMMIT)
        self.assertEqual(cmake["MLIR_ENABLE_BINDINGS_PYTHON"], "OFF")
        self.assertEqual(cmake["LLVM_INSTALL_UTILS"], "ON")
        self.assertEqual(cmake["BUILD_SHARED_LIBS"], "OFF")
        self.assertEqual(cmake["LLVM_BUILD_LLVM_DYLIB"], "OFF")
        self.assertEqual(cmake["LLVM_LINK_LLVM_DYLIB"], "OFF")
        self.assertEqual(llvm["build"]["compiler"]["cc"], "/usr/bin/gcc")
        self.assertEqual(llvm["build"]["compiler"]["cxx"], "/usr/bin/g++")
        self.assertEqual(llvm["build"]["max_jobs"], 64)
        self.assertIn("lib/cmake/llvm/LLVMConfig.cmake", llvm["build"]["install_artifacts"])
        self.assertIn("lib/cmake/mlir/MLIRConfig.cmake", llvm["build"]["install_artifacts"])
        self.assertEqual(
            llvm["verify"]["expect"],
            {
                "clang_version": "23.0.0git",
                "commit": LLVM_COMMIT,
                "prefix": "/usr/lib/llvm-23",
            },
        )

    def test_base_images_are_digest_pinned(self) -> None:
        for line_name, line in self.lock["lines"].items():
            base = line["base_image"]
            self.assertEqual(base["repository"], "nvidia/cuda", line_name)
            self.assertTrue(base["tag"].endswith("-devel-ubuntu24.04"), line_name)
            self.assertRegex(base["digest"], r"^sha256:[0-9a-f]{64}$", line_name)
            self.assertEqual(base["sha256"], base["digest"].split(":", 1)[1], line_name)
            self.assertEqual(
                base["reference"],
                f'{base["repository"]}:{base["tag"]}@{base["digest"]}',
                line_name,
            )

    def test_component_schema_and_pins(self) -> None:
        for line_name, line in self.lock["lines"].items():
            components = line["components"]
            self.assertEqual(set(components), EXPECTED_COMPONENTS, line_name)
            for name, component in components.items():
                with self.subTest(line=line_name, component=name):
                    self.assertIsInstance(component.get("version"), str)
                    self.assertTrue(component["version"])
                    self.assertIsInstance(component.get("install_layout"), dict)
                    self.assertIsInstance(component.get("verify"), dict)
                    self.assertIn("how", component["verify"])
                    self.assertIn("expect", component["verify"])
                    self._assert_source_is_pinned(component["source"])

    def test_lines_are_consistent(self) -> None:
        cu129 = self.lock["lines"]["cu129"]
        cu130 = self.lock["lines"]["cu130"]
        self.assertEqual(set(cu129["components"]), set(cu130["components"]))
        self.assertEqual(cu129["base_image"]["ubuntu_version"], cu130["base_image"]["ubuntu_version"])
        for component in SHARED_VERSION_COMPONENTS:
            self.assertEqual(
                cu129["components"][component]["version"],
                cu130["components"][component]["version"],
                component,
            )
            self.assertEqual(
                cu129["components"][component]["install_layout"]["style"],
                cu130["components"][component]["install_layout"]["style"],
                component,
            )

    def test_consumer_versions_and_compiler_contracts(self) -> None:
        consumers = self.lock["consumers"]

        self.assertEqual(consumers["jax"]["version"], "0.10.2")
        self.assertEqual(consumers["jax"]["compiler_contract"], JAX_COMPILER_CONTRACT)
        self.assertEqual(consumers["torch"]["compiler_contract"], TORCH_COMPILER_CONTRACT)

    def test_conflicts_are_unique_and_mapped(self) -> None:
        conflicts = self.lock["conflicts"]
        ids = [entry["id"] for entry in conflicts]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(ids, [f"C{i}" for i in range(1, 12)])
        for entry in conflicts:
            self.assertTrue(set(entry["lines"]).issubset(EXPECTED_LINES), entry["id"])
            self.assertIn("resolution", entry, entry["id"])

    def _assert_source_is_pinned(self, source: dict[str, object]) -> None:
        kind = source.get("kind")
        self.assertIsInstance(kind, str)
        if kind == "git":
            self.assertIn("tag", source)
            self.assertRegex(str(source.get("commit", "")), r"^[0-9a-f]{40}$")
            return
        self.assertRegex(str(source.get("sha256", "")), SHA256_RE)
        if kind == "apt":
            packages = source.get("packages")
            self.assertIsInstance(packages, list)
            self.assertGreater(len(packages), 0)
            for package in packages:
                self.assertIn("package", package)
                self.assertIn("url", package)
                self.assertRegex(str(package.get("sha256", "")), SHA256_RE)


if __name__ == "__main__":
    unittest.main()
