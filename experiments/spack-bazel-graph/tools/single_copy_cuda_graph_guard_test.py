#!/usr/bin/env python3
"""Tests for the CUDA-family single-copy graph guard."""

from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path


SCRIPT = Path(__file__).with_name("single_copy_cuda_graph_guard.py")
SPEC = importlib.util.spec_from_file_location("single_copy_cuda_graph_guard", SCRIPT)
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
        rule_fixture("duplicate-cuda-family", "test_rejects_duplicate_cuda_family_nodes"),
        rule_fixture("pip-nvidia-cuda", "test_rejects_pip_nvidia_package_nodes"),
    )
)


def node(package: str, version: str) -> dict[str, object]:
    return {"package": package, "version": version, "spack_hash": package + "-hash", "deps": []}


class SingleCopyCudaGraphGuardTest(unittest.TestCase):
    def test_rule_fixture_manifest_matches_guard_rule_ids(self) -> None:
        self.assertEqual(set(RULE_FIXTURES), guard.RULE_IDS)
        missing = sorted(name for name in RULE_FIXTURES.values() if not hasattr(self, name))
        self.assertEqual(missing, [])

    def test_accepts_one_cuda_family_node_per_package(self) -> None:
        graph = {
            "nodes": [
                node("cuda", "13.0.3"),
                node("cudnn", "9.24.0.43-13"),
                node("nccl", "2.30.7-1"),
                node("py-torch", "2.14.0"),
            ],
        }

        self.assertEqual(guard.check_graph(graph, "torch.json"), [])

    def test_rejects_duplicate_cuda_family_nodes(self) -> None:
        graph = {
            "nodes": [
                node("cuda", "13.0.3"),
                {**node("cuda", "13.3.0"), "spack_hash": "cuda-other"},
            ],
        }

        self.assertEqual(
            guard.check_graph(graph, "torch.json"),
            ["torch.json: CUDA-family package cuda appears more than once: 13.0.3, 13.3.0"],
        )

    def test_rejects_pip_nvidia_package_nodes(self) -> None:
        graph = {"nodes": [node("py-nvidia-cublas-cu13", "13.1.1.3")]}

        self.assertEqual(
            guard.check_graph(graph, "jax.json"),
            ["jax.json: forbidden pip NVIDIA CUDA package node py-nvidia-cublas-cu13@13.1.1.3"],
        )


if __name__ == "__main__":
    unittest.main()
