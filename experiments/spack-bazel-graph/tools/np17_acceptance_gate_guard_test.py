#!/usr/bin/env python3
"""Tests for the NP-17 acceptance gate guard."""

from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path


SCRIPT = Path(__file__).with_name("np17_acceptance_gate_guard.py")
SPEC = importlib.util.spec_from_file_location("np17_acceptance_gate_guard", SCRIPT)
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
        rule_fixture("acceptance-ancestor-freshness", "test_rejects_ancestor_acceptance_when_build_inputs_changed"),
        rule_fixture("acceptance-commit", "test_rejects_wrong_line_and_missing_commit"),
        rule_fixture("acceptance-complete", "test_rejects_missing_or_failed_acceptance"),
        rule_fixture("acceptance-line", "test_rejects_wrong_line_and_missing_commit"),
        rule_fixture("acceptance-profile", "test_rejects_non_torch_profile_evidence"),
        rule_fixture("acceptance-verdict", "test_rejects_missing_or_failed_acceptance"),
        rule_fixture("acceptance-waiver", "test_waivers_must_be_a_list_with_human_decisions"),
    )
)


def acceptance(line: str, commit: str, verdict: str = "passed") -> dict[str, object]:
    return {
        "schema_version": 1,
        "profile": "torch",
        "line": line,
        "commit": commit,
        "verdict": verdict,
        "sub_results": [],
    }


class Np17AcceptanceGateGuardTest(unittest.TestCase):
    def test_rule_fixture_manifest_matches_guard_rule_ids(self) -> None:
        self.assertEqual(set(RULE_FIXTURES), guard.RULE_IDS)
        missing = sorted(name for name in RULE_FIXTURES.values() if not hasattr(self, name))
        self.assertEqual(missing, [])

    def test_build_input_path_filter_matches_experiment_tree_minus_docs_and_scratch(self) -> None:
        self.assertTrue(
            guard.is_build_input_path("experiments/spack-bazel-graph/native/pytorch/BUILD.bazel")
        )
        self.assertTrue(
            guard.is_build_input_path("experiments/spack-bazel-graph/workloads/w3_jax.py")
        )
        self.assertTrue(
            guard.is_build_input_path("experiments/spack-bazel-graph/workloads/w5_jax_model.py")
        )
        self.assertFalse(
            guard.is_build_input_path("experiments/spack-bazel-graph/docs/native-migration.md")
        )
        self.assertFalse(
            guard.is_build_input_path("experiments/spack-bazel-graph/.scratch/live-workloads/spec.md")
        )
        self.assertFalse(
            guard.is_build_input_path("experiments/spack-bazel-graph/acceptance-torch-cu130.json")
        )
        self.assertFalse(
            guard.is_build_input_path("experiments/spack-bazel-graph/acceptance-waivers.json")
        )
        self.assertFalse(guard.is_build_input_path("scripts/agents/triumvirate-gate.sh"))

    def test_requires_both_lines_to_pass_for_the_flip_commit(self) -> None:
        errors = guard.check_acceptance_pair(
            {
                "cu129": acceptance("cu129", "flip"),
                "cu130": acceptance("cu130", "flip"),
            },
            flip_commit="flip",
            is_ancestor=lambda ancestor, descendant: False,
            changed_paths=lambda ancestor, descendant: [],
        )

        self.assertEqual(errors, [])

    def test_rejects_missing_or_failed_acceptance(self) -> None:
        errors = guard.check_acceptance_pair(
            {"cu129": acceptance("cu129", "flip", verdict="failed")},
            flip_commit="flip",
            is_ancestor=lambda ancestor, descendant: False,
            changed_paths=lambda ancestor, descendant: [],
        )

        self.assertIn("acceptance-torch-cu130.json is missing", errors)
        self.assertIn("acceptance-torch-cu129.json verdict must be passed, got failed", errors)

    def test_rejects_wrong_line_and_missing_commit(self) -> None:
        wrong_line = acceptance("cu130", "")
        errors = guard.check_acceptance_pair(
            {
                "cu129": wrong_line,
                "cu130": acceptance("cu130", "flip"),
            },
            flip_commit="flip",
            is_ancestor=lambda ancestor, descendant: False,
            changed_paths=lambda ancestor, descendant: [],
        )

        self.assertEqual(
            errors,
            [
                "acceptance-torch-cu129.json line must be cu129, got cu130",
                "acceptance-torch-cu129.json must record commit",
            ],
        )

    def test_rejects_non_torch_profile_evidence(self) -> None:
        bad = acceptance("cu129", "flip")
        bad["profile"] = "jax"
        errors = guard.check_acceptance_pair(
            {
                "cu129": bad,
                "cu130": acceptance("cu130", "flip"),
            },
            flip_commit="flip",
            is_ancestor=lambda ancestor, descendant: False,
            changed_paths=lambda ancestor, descendant: [],
        )

        self.assertEqual(errors, ["acceptance-torch-cu129.json profile must be torch, got jax"])

    def test_accepts_ancestor_acceptance_when_no_build_inputs_changed(self) -> None:
        errors = guard.check_acceptance_pair(
            {
                "cu129": acceptance("cu129", "base"),
                "cu130": acceptance("cu130", "base"),
            },
            flip_commit="flip",
            is_ancestor=lambda ancestor, descendant: ancestor == "base" and descendant == "flip",
            changed_paths=lambda ancestor, descendant: [
                "experiments/spack-bazel-graph/docs/native-migration.md",
                "scripts/agents/triumvirate-gate.sh",
            ],
        )

        self.assertEqual(errors, [])

    def test_accepts_ancestor_acceptance_with_committed_evidence_and_guard_changes(self) -> None:
        errors = guard.check_acceptance_pair(
            {
                "cu129": acceptance("cu129", "base"),
                "cu130": acceptance("cu130", "base"),
            },
            flip_commit="flip",
            is_ancestor=lambda ancestor, descendant: ancestor == "base" and descendant == "flip",
            changed_paths=lambda ancestor, descendant: [
                "experiments/spack-bazel-graph/acceptance-torch-cu129.json",
                "experiments/spack-bazel-graph/acceptance-torch-cu130.json",
                "experiments/spack-bazel-graph/tools/np17_acceptance_gate_guard.py",
                "experiments/spack-bazel-graph/tools/np17_acceptance_gate_guard_test.py",
            ],
        )

        self.assertEqual(errors, [])

    def test_rejects_ancestor_acceptance_when_build_inputs_changed(self) -> None:
        errors = guard.check_acceptance_pair(
            {
                "cu129": acceptance("cu129", "base"),
                "cu130": acceptance("cu130", "base"),
            },
            flip_commit="flip",
            is_ancestor=lambda ancestor, descendant: True,
            changed_paths=lambda ancestor, descendant: [
                "experiments/spack-bazel-graph/native_overrides.json",
            ],
        )

        self.assertEqual(
            errors,
            [
                "acceptance-torch-cu129.json is for ancestor base but build inputs changed: "
                "experiments/spack-bazel-graph/native_overrides.json",
                "acceptance-torch-cu130.json is for ancestor base but build inputs changed: "
                "experiments/spack-bazel-graph/native_overrides.json",
            ],
        )

    def test_waivers_must_be_a_list_with_human_decisions(self) -> None:
        self.assertEqual(guard.validate_waivers([]), [])
        self.assertEqual(
            guard.validate_waivers([{"id": "known-runtime-gap"}]),
            ["waiver 0 must include human_decision"],
        )
        self.assertEqual(
            guard.validate_waivers({"waivers": []}),
            ["acceptance-waivers.json must be a JSON list"],
        )


if __name__ == "__main__":
    unittest.main()
