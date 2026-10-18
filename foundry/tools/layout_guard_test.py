#!/usr/bin/env python3
"""Tests for layout_guard.py."""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).with_name("layout_guard.py")
assert SCRIPT.exists(), "layout guard is missing"
SPEC = importlib.util.spec_from_file_location("layout_guard", SCRIPT)
assert SPEC is not None
assert SPEC.loader is not None
guard = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = guard
SPEC.loader.exec_module(guard)


def rule_fixture(rule_id: str, test_method: str) -> tuple[str, str]:
    assert rule_id in guard.RULE_IDS
    return rule_id, test_method


RULE_FIXTURES = dict(
    (
        rule_fixture("bulk-gate-evidence", "test_bulk_gate_evidence_directory_is_reported"),
        rule_fixture("generated-graph-root", "test_generated_graph_file_in_graph_root_is_reported"),
        rule_fixture("old-graph-module-path", "test_old_module_path_is_reported"),
        rule_fixture("old-harness-path", "test_old_harness_paths_are_reported"),
        rule_fixture("old-vendored-path", "test_old_vendored_paths_are_reported"),
        rule_fixture("top-level-tests-dir", "test_top_level_tests_directory_is_reported"),
    )
)


def write(path: Path, text: str = "fixture\n") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def git(root: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(root), *args],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


class LayoutGuardTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(dir=os.environ.get("TEST_TMPDIR") or os.environ.get("TMPDIR"))
        self.root = Path(self._tmp.name)
        git(self.root, "init", "-q")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def track(self, *paths: str) -> None:
        for rel in paths:
            write(self.root / rel)
            git(self.root, "add", rel)

    def leave_untracked(self, *paths: str) -> None:
        for rel in paths:
            write(self.root / rel)

    def test_rule_fixture_manifest_matches_guard_rule_ids(self) -> None:
        self.assertEqual(set(RULE_FIXTURES), guard.RULE_IDS)
        missing = sorted(name for name in RULE_FIXTURES.values() if not hasattr(self, name))
        self.assertEqual(missing, [])

    def test_generated_graph_file_in_graph_root_is_reported(self) -> None:
        self.track("foundry/pytorch_build_graph.json")

        findings = guard.collect_findings(self.root)

        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].rule, "generated-graph-root")
        self.assertEqual(findings[0].path, "foundry/pytorch_build_graph.json")
        self.assertIn("foundry/graphs/pytorch_build_graph.json", findings[0].message)

    def test_spack_graph_lock_in_graph_root_is_reported(self) -> None:
        self.track("foundry/pytorch_spack_graph.lock.json")

        findings = guard.collect_findings(self.root)

        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].rule, "generated-graph-root")
        self.assertEqual(findings[0].path, "foundry/pytorch_spack_graph.lock.json")
        self.assertIn("foundry/graphs/pytorch_spack_graph.lock.json", findings[0].message)

    def test_generated_graph_file_under_graphs_directory_passes(self) -> None:
        self.track(
            "foundry/graphs/pytorch_build_graph.json",
            "foundry/graphs/pytorch_spack_graph.lock.json",
        )

        self.assertEqual(guard.collect_findings(self.root), [])

    def test_untracked_generated_graph_file_in_graph_root_is_ignored(self) -> None:
        self.leave_untracked("foundry/pytorch_build_graph.json")

        self.assertEqual(guard.collect_findings(self.root), [])

    def test_bulk_gate_evidence_directory_is_reported(self) -> None:
        self.track(".scratch/production-build/evidence/d5-gate.log")

        findings = guard.collect_findings(self.root)

        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].rule, "bulk-gate-evidence")
        self.assertEqual(findings[0].path, ".scratch/production-build/evidence/d5-gate.log")
        self.assertIn("estate", findings[0].message)
        self.assertIn(".scratch/production-build/evidence-archive.md", findings[0].message)

    def test_tracker_pointer_tickets_specs_designs_and_small_evidence_pass(self) -> None:
        self.track(
            ".scratch/production-build/evidence-archive.md",
            ".scratch/production-build/issues/D5-release-gate.md",
            ".scratch/production-build/spec.md",
            ".scratch/production-build/design/gate-design.md",
            ".scratch/production-build/evidence-note.md",
        )

        self.assertEqual(guard.collect_findings(self.root), [])

    def test_old_module_path_is_reported(self) -> None:
        self.track("experiments/spack-bazel-graph/tools/build_graph.py")

        findings = guard.collect_findings(self.root)

        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].rule, "old-graph-module-path")
        self.assertEqual(findings[0].path, "experiments/spack-bazel-graph/tools/build_graph.py")
        self.assertIn("foundry/tools/build_graph.py", findings[0].message)

    def test_previous_graph_module_path_is_reported(self) -> None:
        self.track("graph/tools/build_graph.py")

        findings = guard.collect_findings(self.root)

        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].rule, "old-graph-module-path")
        self.assertEqual(findings[0].path, "graph/tools/build_graph.py")
        self.assertIn("foundry/tools/build_graph.py", findings[0].message)

    def test_old_harness_paths_are_reported(self) -> None:
        expected = {
            "src/vaso/cli.py": "harness/vaso/cli.py",
            "experiments/bazel-lab/run_experiments.sh": "harness/bazel-lab/run_experiments.sh",
            "fixtures/vaso-fixtures/MODULE.bazel": "harness/smoke/MODULE.bazel",
        }
        self.track(*expected)

        findings = guard.collect_findings(self.root)

        self.assertEqual({finding.path for finding in findings}, set(expected))
        for finding in findings:
            self.assertEqual(finding.rule, "old-harness-path")
            self.assertIn(expected[finding.path], finding.message)

    def test_old_vendored_paths_are_reported(self) -> None:
        expected = {
            "foundry/examples/consumers/third_party/gsplat_cuda/MODULE.bazel": "third_party/gsplat_cuda/MODULE.bazel",
            "foundry/examples/consumers/rust_pyo3/crates/vendor/serde-1.0.217/Cargo.toml": "third_party/rust_pyo3_cargo/vendor/serde-1.0.217/Cargo.toml",
        }
        self.track(*expected)

        findings = guard.collect_findings(self.root)

        self.assertEqual({finding.path for finding in findings}, set(expected))
        for finding in findings:
            self.assertEqual(finding.rule, "old-vendored-path")
            self.assertIn(expected[finding.path], finding.message)

    def test_top_level_tests_directory_is_reported(self) -> None:
        self.track("tests/test_cli.py")

        findings = guard.collect_findings(self.root)

        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].rule, "top-level-tests-dir")
        self.assertEqual(findings[0].path, "tests/test_cli.py")
        self.assertIn("beside", findings[0].message)

    def test_tests_directories_below_the_top_level_pass(self) -> None:
        self.track("foundry/examples/consumers/cutlass_gemm/tests/cutlass_gemm_test.py", "harness/vaso/cli_test.py")

        self.assertEqual(guard.collect_findings(self.root), [])

    def test_third_party_paths_pass(self) -> None:
        self.track("third_party/gsplat_cuda/MODULE.bazel", "third_party/rust_pyo3_cargo/vendor/serde-1.0.217/Cargo.toml")

        self.assertEqual(guard.collect_findings(self.root), [])

    def test_harness_paths_pass(self) -> None:
        self.track("harness/vaso/cli.py", "harness/bazel-lab/MODULE.bazel", "harness/smoke/MODULE.bazel")

        self.assertEqual(guard.collect_findings(self.root), [])

    def test_untracked_old_module_path_is_ignored(self) -> None:
        self.leave_untracked("experiments/spack-bazel-graph/README.md")

        self.assertEqual(guard.collect_findings(self.root), [])


if __name__ == "__main__":
    unittest.main()
