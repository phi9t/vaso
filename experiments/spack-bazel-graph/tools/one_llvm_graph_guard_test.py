#!/usr/bin/env python3
"""Regression tests for one_llvm_graph_guard.py."""

from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path


TRITON_COMMIT = "675c59878aa2280b31f722aaf42b825fcee21de8"

SCRIPT = Path(__file__).with_name("one_llvm_graph_guard.py")
SPEC = importlib.util.spec_from_file_location("one_llvm_graph_guard", SCRIPT)
assert SPEC is not None
guard = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = guard
SPEC.loader.exec_module(guard)


def rule_fixture(rule_id: str, test_method: str) -> tuple[str, str]:
    assert rule_id in guard.RULE_IDS
    return rule_id, test_method


RULE_FIXTURES = dict(
    (
        rule_fixture("jax-native-deps", "test_rejects_jax_dependency_graph_node_without_native_provider"),
        rule_fixture("jaxlib-bazel-rootfs", "test_rejects_jaxlib_graph_with_bazel_build_tool_closure"),
        rule_fixture("jaxlib-cuda-compiler", "test_rejects_jaxlib_cuda_without_allow_unsupported_compilers"),
        rule_fixture("python-singleton", "test_rejects_triumvirate_graph_with_two_python_nodes"),
        rule_fixture("rootfs-llvm", "test_rejects_triumvirate_consumer_with_legacy_llvm"),
        rule_fixture("triton-direct-deps", "test_rejects_torch_pinned_triton_without_declared_tool_edges"),
    )
)


def temporary_directory():
    base = os.environ.get("TEST_TMPDIR") or os.environ.get("TMPDIR") or str(Path.cwd())
    return tempfile.TemporaryDirectory(dir=base)


def node(
    package: str,
    version: str,
    deps: list[tuple[str, str] | tuple[str, str, list[str]]] | None = None,
    parameters: dict | None = None,
) -> dict:
    def dep_entry(dep: tuple[str, str] | tuple[str, str, list[str]]) -> dict:
        if len(dep) == 3:
            dep_name, dep_hash, deptypes = dep
        else:
            dep_name, dep_hash = dep
            deptypes = ["build", "link"]
        return {"name": dep_name, "hash": dep_hash, "deptypes": deptypes}

    return {
        "package": package,
        "version": version,
        "spack_hash": f"{package}-hash",
        "parameters": parameters or {},
        "deps": [dep_entry(dep) for dep in (deps or [])],
    }


def python313() -> dict:
    return node("python", guard.REQUIRED_PYTHON_VERSION)


def rootfs_llvm() -> dict:
    return node("llvm", guard.ROOTFS_LLVM_VERSION, parameters={"clang": True, "lld": True, "mlir": True})


class OneLlvmGraphGuardTest(unittest.TestCase):
    def test_rule_fixture_manifest_matches_guard_rule_ids(self) -> None:
        self.assertEqual(set(RULE_FIXTURES), guard.RULE_IDS)
        missing = sorted(name for name in RULE_FIXTURES.values() if not hasattr(self, name))
        self.assertEqual(missing, [])

    def test_rejects_triumvirate_graph_with_two_python_nodes(self) -> None:
        graph = {
            "root": "py-torch",
            "nodes": [
                node("python", "3.13.13"),
                node("python", "3.14.5"),
                node("py-torch", "2.14.0"),
            ],
        }

        errors = guard.check_graph(graph, "torch.json")

        self.assertEqual(
            errors,
            [
                "torch.json: triumvirate graph must contain exactly one python node "
                "at version 3.13.13, found python@3.13.13, python@3.14.5"
            ],
        )

    def test_rejects_triumvirate_graph_with_wrong_python_version(self) -> None:
        graph = {
            "root": "py-jax",
            "nodes": [
                node("python", "3.14.5"),
                node("py-jax", "0.10.2"),
            ],
        }

        errors = guard.check_graph(graph, "jax.json")

        self.assertEqual(
            errors,
            [
                "jax.json: triumvirate graph must contain exactly one python node "
                "at version 3.13.13, found python@3.14.5"
            ],
        )

    def test_rejects_triumvirate_consumer_with_legacy_llvm(self) -> None:
        graph = {
            "root": "py-triton",
            "nodes": [
                python313(),
                node("llvm", "20.1.8"),
                node("py-triton", "675c598", [("llvm", "llvm-hash")]),
            ],
        }

        errors = guard.check_graph(graph, "triton.json")

        self.assertEqual(
            errors,
            [
                "triton.json: py-triton depends on non-rootfs LLVM 20.1.8; "
                "expected rootfs llvm@23.0.0 from llvm-project "
                "35901313800ea6e6cbeb9226e51c7c4b29bfc40e"
            ],
        )

    def test_accepts_triumvirate_consumer_with_rootfs_llvm(self) -> None:
        graph = {
            "root": "py-jaxlib",
            "nodes": [
                python313(),
                rootfs_llvm(),
                node("py-jaxlib", "0.10.2", [("llvm", "llvm-hash")]),
            ],
        }

        self.assertEqual(guard.check_graph(graph, "jaxlib.json"), [])

    def test_rejects_git_version_llvm_even_when_it_names_the_rootfs_commit(self) -> None:
        graph = {
            "root": "py-jaxlib",
            "nodes": [
                python313(),
                node(
                    "llvm",
                    f"git.{guard.ROOTFS_LLVM_COMMIT}=23.0.0",
                    parameters={"clang": True, "lld": True, "mlir": True},
                ),
                node("py-jaxlib", "0.10.2", [("llvm", "llvm-hash")]),
            ],
        }

        self.assertEqual(
            guard.check_graph(graph, "jaxlib.json"),
            [
                "jaxlib.json: py-jaxlib depends on non-rootfs LLVM "
                f"git.{guard.ROOTFS_LLVM_COMMIT}=23.0.0; expected rootfs llvm@23.0.0 "
                f"from llvm-project {guard.ROOTFS_LLVM_COMMIT}"
            ],
        )

    def test_accepts_triumvirate_consumer_with_no_llvm_edge(self) -> None:
        graph = {
            "root": "py-torch",
            "nodes": [
                python313(),
                node("py-torch", "2.14.0"),
            ],
        }

        self.assertEqual(guard.check_graph(graph, "torch.json"), [])

    def test_allows_legacy_llvm_only_for_pruned_llvmlite_path(self) -> None:
        graph = {
            "root": "py-llvmlite",
            "nodes": [
                node("llvm", "20.1.8"),
                node("py-llvmlite", "0.47.0", [("llvm", "llvm-hash")]),
            ],
        }

        self.assertEqual(guard.check_graph(graph, "llvmlite.json"), [])

    def test_rejects_torch_pinned_triton_without_declared_tool_edges(self) -> None:
        graph = {
            "root": "py-triton",
            "nodes": [
                python313(),
                rootfs_llvm(),
                node(
                    "py-triton",
                    "3.8.0",
                    [("llvm", "llvm-hash")],
                    parameters={"commit": TRITON_COMMIT},
                ),
            ],
        }

        errors = guard.check_graph(graph, "triton.json")

        self.assertEqual(
            errors,
            [
                "triton.json: py-triton@3.8.0 is missing direct dependency "
                "cuda with deptypes build, link, run",
                "triton.json: py-triton@3.8.0 is missing direct dependency "
                "nlohmann-json with deptypes build",
            ],
        )

    def test_accepts_torch_pinned_triton_with_declared_tool_edges(self) -> None:
        graph = {
            "root": "py-triton",
            "nodes": [
                python313(),
                node("cuda", "13.0.3"),
                node("nlohmann-json", "3.11.3"),
                rootfs_llvm(),
                node(
                    "py-triton",
                    "3.8.0",
                    [
                        ("cuda", "cuda-hash", ["build", "link", "run"]),
                        ("nlohmann-json", "nlohmann-json-hash", ["build"]),
                        ("llvm", "llvm-hash", ["build", "link"]),
                    ],
                    parameters={"commit": TRITON_COMMIT},
                ),
            ],
        }

        self.assertEqual(guard.check_graph(graph, "triton.json"), [])

    def test_does_not_apply_torch_pin_tool_edges_to_plain_triton_380(self) -> None:
        graph = {
            "root": "py-triton",
            "nodes": [
                python313(),
                rootfs_llvm(),
                node("py-triton", "3.8.0", [("llvm", "llvm-hash")]),
            ],
        }

        self.assertEqual(guard.check_graph(graph, "triton.json"), [])

    def test_rejects_jaxlib_cuda_without_allow_unsupported_compilers(self) -> None:
        graph = {
            "root": "py-jax",
            "nodes": [
                python313(),
                node(
                    "cuda",
                    "13.0.3",
                    parameters={"allow-unsupported-compilers": False},
                ),
                node(
                    "py-jaxlib",
                    "0.10.2",
                    [("cuda", "cuda-hash", ["build", "link", "run"])],
                    parameters={"cuda": True},
                ),
                node("py-jax", "0.10.2", [("py-jaxlib", "py-jaxlib-hash")]),
            ],
        }

        errors = guard.check_graph(graph, "jax.json")

        self.assertEqual(
            errors,
            [
                "jax.json: py-jaxlib@0.10.2 uses CUDA without "
                "cuda+allow-unsupported-compilers; required for rootfs LLVM clang 23"
            ],
        )

    def test_accepts_jaxlib_cuda_allow_unsupported_compilers_external(self) -> None:
        graph = {
            "root": "py-jax",
            "nodes": [
                python313(),
                node(
                    "cuda",
                    "13.0.3",
                    parameters={"allow-unsupported-compilers": True},
                ),
                node(
                    "py-jaxlib",
                    "0.10.2",
                    [("cuda", "cuda-hash", ["build", "link", "run"])],
                    parameters={"cuda": True},
                ),
                node("py-jax", "0.10.2", [("py-jaxlib", "py-jaxlib-hash")]),
            ],
        }

        self.assertEqual(guard.check_graph(graph, "jax.json"), [])

    def test_rejects_jaxlib_graph_with_bazel_build_tool_closure(self) -> None:
        graph = {
            "root": "py-jax",
            "nodes": [
                python313(),
                node("bash", "5.3"),
                node("zip", "3.0"),
                node("openjdk", "21.0.10_7"),
                node(
                    "bazel",
                    "7.7.0",
                    [
                        ("bash", "bash-hash", ["build"]),
                        ("zip", "zip-hash", ["build", "run"]),
                        ("openjdk", "openjdk-hash", ["build", "run"]),
                    ],
                ),
                node("py-jaxlib", "0.10.2", [("bazel", "bazel-hash", ["build"])]),
                node("py-jax", "0.10.2", [("py-jaxlib", "py-jaxlib-hash")]),
            ],
        }

        self.assertEqual(
            guard.check_graph(graph, "jax.json"),
            [
                "jax.json: py-jaxlib@0.10.2 still pulls Bazel build-tool dependency "
                "bash@5.3; make bazel a non-buildable rootfs external",
                "jax.json: py-jaxlib@0.10.2 still pulls Bazel build-tool dependency "
                "openjdk@21.0.10_7; make bazel a non-buildable rootfs external",
                "jax.json: py-jaxlib@0.10.2 still pulls Bazel build-tool dependency "
                "zip@3.0; make bazel a non-buildable rootfs external",
            ],
        )

    def test_rejects_jax_dependency_graph_node_without_native_provider(self) -> None:
        graph = {
            "root": "py-jax",
            "nodes": [
                {**python313(), "status": "spack"},
                node("bazel", "7.7.0"),
                {**node("compiler-wrapper", "1.1.0"), "status": "spack"},
                {**node("py-numpy", "2.4.6"), "status": "spack"},
                node("py-ml-dtypes", "0.5.1", [("py-numpy", "py-numpy-hash")]),
                node(
                    "py-jaxlib",
                    "0.10.2",
                    [
                        ("bazel", "bazel-hash", ["build"]),
                        ("compiler-wrapper", "compiler-wrapper-hash", ["build"]),
                        ("py-ml-dtypes", "py-ml-dtypes-hash", ["build", "run"]),
                    ],
                ),
                node("py-jax", "0.10.2", [("py-jaxlib", "py-jaxlib-hash")]),
            ],
        }

        self.assertEqual(
            guard.check_graph(graph, "jax.json"),
            [
                "jax.json: py-jax dependency py-numpy@2.4.6 is not native; "
                "B7 requires native providers for the JAX dependency graph except "
                "py-jax/py-jaxlib and rootfs tool boundaries"
            ],
        )

    def test_cli_reports_graph_errors(self) -> None:
        with temporary_directory() as td:
            path = Path(td) / "bad.json"
            path.write_text(
                '{"root":"py-jax","nodes":['
                '{"package":"python","version":"3.13.13","spack_hash":"python-hash","deps":[]},'
                '{"package":"llvm","version":"20.1.8","spack_hash":"llvm-hash","deps":[]},'
                '{"package":"py-jax","version":"0.10.2","spack_hash":"jax-hash",'
                '"deps":[{"name":"llvm","hash":"llvm-hash"}]}'
                ']}',
                encoding="utf-8",
            )

            rc = guard.main([str(path)])

        self.assertEqual(rc, 1)


if __name__ == "__main__":
    unittest.main()
