#!/usr/bin/env python3
"""Regression tests for migration_ledger_check.py."""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).with_name("migration_ledger_check.py")
SPEC = importlib.util.spec_from_file_location("migration_ledger_check", SCRIPT)
assert SPEC is not None
checker = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = checker
SPEC.loader.exec_module(checker)


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


class MigrationLedgerCheckTest(unittest.TestCase):
    def test_native_entries_must_have_artifacts_and_overrides(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "docs" / "recipes").mkdir(parents=True)
            (root / "docs" / "recipes" / "zlib-ng.md").write_text("# zlib-ng\n", encoding="utf-8")
            (root / "synthetic").mkdir()
            (root / "synthetic" / "BUILD.bazel").write_text(
                'sh_test(name = "zlib_ng_abi_parity")\n',
                encoding="utf-8",
            )
            write_json(
                root / "native_overrides.json",
                {"native": {"zlib-ng": "@zlib_ng_native//:lib"}, "provided_versions": {"zlib-ng": "2.3.3"}},
            )
            write_json(
                root / "migration_ledger.json",
                {
                    "front": "next frontier: llvm",
                    "packages": {
                        "zlib-ng": {
                            "topo_index": 1,
                            "status": "native",
                            "recipe": "docs/recipes/zlib-ng.md",
                            "native_rule": "@zlib_ng_native//:lib",
                            "abi_gate": "//synthetic:zlib_ng_abi_parity",
                        },
                        "llvm": {
                            "topo_index": 2,
                            "status": "recipe-captured",
                            "recipe": "docs/recipes/llvm.md",
                        },
                    },
                },
            )
            (root / "docs" / "recipes" / "llvm.md").write_text("# llvm\n", encoding="utf-8")

            self.assertEqual(checker.check_repo(root), [])

    def test_front_must_not_name_completed_native_package_as_active(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "docs" / "recipes").mkdir(parents=True)
            (root / "docs" / "recipes" / "meson.md").write_text("# meson\n", encoding="utf-8")
            (root / "synthetic").mkdir()
            (root / "synthetic" / "BUILD.bazel").write_text(
                'sh_test(name = "meson_prefix_parity")\n',
                encoding="utf-8",
            )
            write_json(
                root / "native_overrides.json",
                {"native": {"meson": "@meson_native//:lib"}, "provided_versions": {"meson": "1.11.1"}},
            )
            write_json(
                root / "migration_ledger.json",
                {
                    "front": "meson is the active focused frontier",
                    "packages": {
                        "meson": {
                            "topo_index": 1,
                            "status": "native",
                            "recipe": "docs/recipes/meson.md",
                            "native_rule": "@meson_native//:lib",
                            "abi_gate": "//synthetic:meson_prefix_parity",
                        }
                    },
                },
            )

            errors = checker.check_repo(root)

            self.assertEqual(
                errors,
                ["front names native package as active frontier: meson"],
            )

    def test_remaining_migration_entries_need_topological_indices(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "docs" / "recipes").mkdir(parents=True)
            (root / "docs" / "recipes" / "llvm.md").write_text("# llvm\n", encoding="utf-8")
            write_json(root / "native_overrides.json", {"native": {}})
            write_json(
                root / "migration_ledger.json",
                {
                    "front": "next frontier: llvm",
                    "packages": {
                        "llvm": {
                            "status": "recipe-captured",
                            "recipe": "docs/recipes/llvm.md",
                        }
                    },
                },
            )

            errors = checker.check_repo(root)

            self.assertEqual(
                errors,
                ["llvm: one of topo_index, focused_topo_index, lean_py_torch_topo_index must be an integer"],
            )

    def test_front_lean_pytorch_topo_index_must_match_package_entry(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "docs" / "recipes").mkdir(parents=True)
            (root / "docs" / "recipes" / "llvm.md").write_text("# llvm\n", encoding="utf-8")
            write_json(root / "native_overrides.json", {"native": {}})
            write_json(
                root / "migration_ledger.json",
                {
                    "front": "llvm is the active token-gated frontier at lean py-torch topo index 136",
                    "packages": {
                        "llvm": {
                            "lean_py_torch_topo_index": 135,
                            "status": "recipe-captured",
                            "recipe": "docs/recipes/llvm.md",
                        }
                    },
                },
            )

            errors = checker.check_repo(root)

            self.assertEqual(
                errors,
                ["front lean py-torch topo index 136 does not match llvm.lean_py_torch_topo_index 135"],
            )

    def test_lean_pytorch_topo_indices_must_match_frontier_graph(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "docs" / "recipes").mkdir(parents=True)
            (root / "docs" / "recipes" / "llvm.md").write_text("# llvm\n", encoding="utf-8")
            write_json(root / "native_overrides.json", {"native": {}})
            write_json(
                root / "py_torch_lean_fonts_build_graph.json",
                {"topological_order": ["cmake", "ninja", "llvm"]},
            )
            write_json(
                root / "migration_ledger.json",
                {
                    "front": "llvm is the active token-gated frontier at lean py-torch topo index 2",
                    "frontier_graphs": {
                        "py-torch": {
                            "graph": "py_torch_lean_fonts_build_graph.json",
                        },
                    },
                    "packages": {
                        "llvm": {
                            "lean_py_torch_topo_index": 1,
                            "status": "recipe-captured",
                            "recipe": "docs/recipes/llvm.md",
                        }
                    },
                },
            )

            errors = checker.check_repo(root)

            self.assertEqual(
                errors,
                [
                    "front lean py-torch topo index 2 does not match llvm.lean_py_torch_topo_index 1",
                    "llvm: lean_py_torch_topo_index 1 does not match py_torch_lean_fonts_build_graph.json index 2",
                ],
            )

    def test_pruned_frontier_entries_can_be_noted_without_active_index(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "docs" / "recipes").mkdir(parents=True)
            (root / "docs" / "recipes" / "llvm.md").write_text("# llvm\n", encoding="utf-8")
            write_json(root / "native_overrides.json", {"native": {}})
            write_json(
                root / "py_torch_lean_nx_build_graph.json",
                {"topological_order": ["py-networkx", "py-torch"], "nodes": []},
            )
            write_json(
                root / "migration_ledger.json",
                {
                    "front": "py-networkx is the active frontier at lean py-torch topo index 0",
                    "frontier_graphs": {
                        "py-torch": {
                            "graph": "py_torch_lean_nx_build_graph.json",
                        },
                    },
                    "packages": {
                        "py-networkx": {
                            "lean_py_torch_topo_index": 0,
                            "status": "spack",
                        },
                        "llvm": {
                            "status": "recipe-captured",
                            "recipe": "docs/recipes/llvm.md",
                            "note": "not in the pruned py-torch closure (ticket 14).",
                        },
                    },
                },
            )

            self.assertEqual(checker.check_repo(root), [])

    def test_py_focused_graph_rejects_foreign_python_version(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "docs" / "recipes").mkdir(parents=True)
            (root / "docs" / "recipes" / "py-example.md").write_text("# py-example\n", encoding="utf-8")
            write_json(root / "native_overrides.json", {"native": {}})
            write_json(
                root / "py_example_build_graph.json",
                {
                    "nodes": [
                        {"package": "python", "version": "3.13.13"},
                        {"package": "python", "version": "3.14.5"},
                        {"package": "py-example", "version": "1.0"},
                    ],
                    "topological_order": ["python", "py-example"],
                },
            )
            write_json(
                root / "migration_ledger.json",
                {
                    "front": "py-example is the active frontier at lean py-torch topo index 10",
                    "packages": {
                        "py-example": {
                            "lean_py_torch_topo_index": 10,
                            "status": "recipe-captured",
                            "recipe": "docs/recipes/py-example.md",
                            "focused_graph": "py_example_build_graph.json",
                        }
                    },
                },
            )

            errors = checker.check_repo(root)

            self.assertEqual(
                errors,
                [
                    "py-example: focused_graph py_example_build_graph.json contains Python versions "
                    "3.13.13, 3.14.5; expected only 3.13.13"
                ],
            )


class ProviderVersionParityTest(unittest.TestCase):
    GRAPH = "py_torch_lean_fonts_build_graph.json"
    PYTHON_SUBSTITUTION = {
        "key": "python",
        "graph_version": "3.13.13",
        "provided": "3.14.5",
        "ticket": ".scratch/pytorch-frontier-convergence/issues/07-native-python-313-provider.md",
    }

    def check(self, overrides: dict, nodes: list[dict]) -> list[str]:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            write_json(root / "native_overrides.json", overrides)
            write_json(root / self.GRAPH, {"nodes": nodes, "topological_order": []})
            write_json(
                root / "migration_ledger.json",
                {"frontier_graphs": {"py-torch": {"graph": self.GRAPH}}, "packages": {}},
            )
            return checker.check_repo(root)

    def test_exact_key_with_matching_provided_version_passes(self) -> None:
        errors = self.check(
            {
                "native": {"python@3.13.13": "@python_313_native//:lib"},
                "provided_versions": {"python@3.13.13": "3.13.13"},
            },
            [{"package": "python", "version": "3.13.13"}],
        )

        self.assertEqual(errors, [])

    def test_unversioned_key_serving_another_version_fails(self) -> None:
        errors = self.check(
            {
                "native": {"python": "@python_native//:lib"},
                "provided_versions": {"python": "3.14.5"},
            },
            [{"package": "python", "version": "3.13.13"}],
        )

        self.assertEqual(len(errors), 1)
        self.assertIn("native override 'python' builds python@3.14.5 but serves graph node python@3.13.13", errors[0])

    def test_duplicate_nodes_report_one_substitution(self) -> None:
        errors = self.check(
            {"native": {"zstd": "@zstd_native//:lib"}, "provided_versions": {"zstd": "1.5.6"}},
            [{"package": "zstd", "version": "1.5.7"}, {"package": "zstd", "version": "1.5.7"}],
        )

        self.assertEqual(len(errors), 1)

    def test_allowlisted_substitution_passes(self) -> None:
        errors = self.check(
            {
                "native": {"python": "@python_native//:lib"},
                "provided_versions": {"python": "3.14.5"},
                "known_version_substitutions": [self.PYTHON_SUBSTITUTION],
            },
            [{"package": "python", "version": "3.13.13"}],
        )

        self.assertEqual(errors, [])

    def test_stale_allowlist_entry_fails(self) -> None:
        errors = self.check(
            {
                "native": {"python@3.13.13": "@python_313_native//:lib"},
                "provided_versions": {"python@3.13.13": "3.13.13"},
                "known_version_substitutions": [self.PYTHON_SUBSTITUTION],
            },
            [{"package": "python", "version": "3.13.13"}],
        )

        self.assertEqual(len(errors), 1)
        self.assertIn("stale known_version_substitutions entry: 'python'", errors[0])

    def test_allowlist_entry_requires_ticket(self) -> None:
        entry = dict(self.PYTHON_SUBSTITUTION)
        del entry["ticket"]
        errors = self.check(
            {
                "native": {"python": "@python_native//:lib"},
                "provided_versions": {"python": "3.14.5"},
                "known_version_substitutions": [entry],
            },
            [{"package": "python", "version": "3.13.13"}],
        )

        self.assertTrue(any("need non-empty key, graph_version, provided, and ticket" in e for e in errors))
        self.assertTrue(any("builds python@3.14.5 but serves graph node python@3.13.13" in e for e in errors))

    def test_provided_versions_must_cover_native_keys_both_ways(self) -> None:
        errors = self.check(
            {
                "native": {"zstd": "@zstd_native//:lib"},
                "provided_versions": {"xz": "5.8.1"},
            },
            [{"package": "zstd", "version": "1.5.7"}],
        )

        self.assertEqual(
            errors,
            [
                "native_overrides.json: native override 'zstd' has no provided_versions entry",
                "native_overrides.json: provided_versions entry 'xz' has no native override",
            ],
        )

    def test_canonical_overrides_must_declare_provided_versions(self) -> None:
        errors = self.check(
            {"native": {"zstd": "@zstd_native//:lib"}},
            [{"package": "zstd", "version": "1.5.7"}],
        )

        self.assertIn(
            "native_overrides.json: provided_versions must record every native key's built version",
            errors,
        )

    def test_missing_frontier_graph_is_an_error_and_static_checks_still_run(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            write_json(
                root / "native_overrides.json",
                {"native": {"zstd": "@zstd_native//:lib"}, "provided_versions": {}},
            )
            write_json(
                root / "migration_ledger.json",
                {"frontier_graphs": {"py-torch": {"graph": self.GRAPH}}, "packages": {}},
            )

            errors = checker.check_repo(root)

        self.assertEqual(
            errors,
            [
                "native_overrides.json: native override 'zstd' has no provided_versions entry",
                f"py-torch frontier graph does not exist: {self.GRAPH}; native provider version parity cannot be checked",
            ],
        )

    def test_exact_key_provided_version_is_checked_statically(self) -> None:
        errors = self.check(
            {
                "native": {"python@3.13.13": "@python_313_native//:lib"},
                "provided_versions": {"python@3.13.13": "3.14.5"},
            },
            [],
        )

        self.assertEqual(
            errors,
            [
                "native_overrides.json: exact native override 'python@3.13.13' must provide "
                "3.13.13, but provided_versions says 3.14.5"
            ],
        )

    def test_exact_key_for_other_version_does_not_serve_node(self) -> None:
        errors = self.check(
            {
                "native": {"protobuf@21.12": "@protobuf_native//:lib"},
                "provided_versions": {"protobuf@21.12": "21.12"},
            },
            [{"package": "protobuf", "version": "3.13.0"}],
        )

        self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()
