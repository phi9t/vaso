#!/usr/bin/env python3
"""Regression tests for py_torch_networkx_prune_check.py."""

from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path


SCRIPT = Path(__file__).with_name("py_torch_networkx_prune_check.py")
SPEC = importlib.util.spec_from_file_location("py_torch_networkx_prune_check", SCRIPT)
assert SPEC is not None
checker = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = checker
SPEC.loader.exec_module(checker)


def node(package: str, spack_hash: str, *, deps: list[str] | None = None, params: dict | None = None) -> dict:
    return {
        "package": package,
        "version": "1.0",
        "spack_hash": spack_hash,
        "parameters": params or {},
        "deps": [{"name": dep, "hash": dep + "-hash", "deptypes": ["build"]} for dep in (deps or [])],
    }


def graph(nodes: list[dict]) -> dict:
    return {"n_nodes": len(nodes), "nodes": nodes, "topological_order": [n["package"] for n in nodes]}


class PyTorchNetworkxPruneCheckTest(unittest.TestCase):
    def test_accepts_documented_pruned_graph(self) -> None:
        old = graph(
            [
                node("py-networkx", "old-nx", deps=["py-scipy"], params={"default": True, "extra": False}),
                node("py-torch", "old-torch", deps=["py-networkx"]),
                node("py-numpy", "same"),
                node("py-scipy", "dropped"),
            ]
        )
        new = graph(
            [
                node(
                    "py-networkx",
                    "new-nx",
                    deps=["py-pip", "py-setuptools", "py-wheel", "python", "python-venv"],
                    params={"default": False, "extra": False},
                ),
                node("py-torch", "new-torch", deps=["py-networkx"]),
                node("py-numpy", "same"),
            ]
        )

        errors, report = checker.check_graphs(
            old,
            new,
            must_drop={"py-scipy"},
            expected_hash_changes={
                "py-networkx": "default extras disabled",
                "py-torch": "depends on py-networkx",
            },
            expected_n_nodes=3,
            expected_dropped_count=1,
        )

        self.assertEqual(errors, [])
        self.assertEqual(report["dropped"], ["py-scipy"])

    def test_rejects_forbidden_package(self) -> None:
        old = graph([node("py-networkx", "old", params={"default": True, "extra": False})])
        new = graph(
            [
                node(
                    "py-networkx",
                    "new",
                    deps=["py-pip", "py-setuptools", "py-wheel", "python", "python-venv"],
                    params={"default": False, "extra": False},
                ),
                node("llvm", "still-here"),
            ]
        )

        errors, _ = checker.check_graphs(old, new, must_drop={"llvm"}, expected_hash_changes={"py-networkx": "ok"})

        self.assertIn("pruned graph still contains forbidden package(s): llvm", errors)

    def test_rejects_networkx_extra_dependency(self) -> None:
        old = graph([node("py-networkx", "old", params={"default": True, "extra": False})])
        new = graph(
            [
                node(
                    "py-networkx",
                    "new",
                    deps=["py-pip", "py-scipy"],
                    params={"default": False, "extra": False},
                )
            ]
        )

        errors, _ = checker.check_graphs(old, new, expected_hash_changes={"py-networkx": "ok"})

        self.assertTrue(any("py-networkx deps are" in error for error in errors))

    def test_hash_changes_need_exact_reasons(self) -> None:
        old = graph([node("py-networkx", "same", params={"default": False, "extra": False}), node("py-torch", "old")])
        new = graph(
            [
                node(
                    "py-networkx",
                    "same",
                    deps=["py-pip", "py-setuptools", "py-wheel", "python", "python-venv"],
                    params={"default": False, "extra": False},
                ),
                node("py-torch", "new"),
            ]
        )

        errors, _ = checker.check_graphs(old, new, expected_hash_changes={"py-networkx": "stale"})

        self.assertIn("unexplained hash change(s): py-torch", errors)
        self.assertIn("stale expected hash-change reason(s): py-networkx", errors)


if __name__ == "__main__":
    unittest.main()
