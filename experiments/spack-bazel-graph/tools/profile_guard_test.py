#!/usr/bin/env python3
"""Tests for profile_guard.py."""

from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path


SCRIPT = Path(__file__).with_name("profile_guard.py")
SPEC = importlib.util.spec_from_file_location("profile_guard", SCRIPT)
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
        rule_fixture("invalid-profile", "test_unknown_profile_is_rejected"),
        rule_fixture("mixed-profile-families", "test_build_invocation_text_rejects_mixed_specs"),
        rule_fixture("profile-family-membership", "test_torch_profile_rejects_jaxlib_in_graph"),
    )
)


def graph(*packages: str) -> dict[str, object]:
    return {
        "schema_version": 1,
        "root": packages[-1] if packages else "",
        "nodes": [
            {
                "package": package,
                "version": "1",
                "spack_hash": f"{package}-hash",
                "deps": [],
            }
            for package in packages
        ],
    }


class ProfileGuardTest(unittest.TestCase):
    def test_rule_fixture_manifest_matches_guard_rule_ids(self) -> None:
        self.assertEqual(set(RULE_FIXTURES), guard.RULE_IDS)
        missing = sorted(name for name in RULE_FIXTURES.values() if not hasattr(self, name))
        self.assertEqual(missing, [])

    def test_torch_profile_accepts_torch_family_graph(self) -> None:
        errors = guard.check_document(graph("python", "py-triton", "py-torch"), "torch.json", profile="torch")

        self.assertEqual(errors, [])

    def test_torch_profile_rejects_jaxlib_in_graph(self) -> None:
        errors = guard.check_document(
            graph("python", "py-torch", "py-jaxlib"),
            "mixed.json",
            profile="torch",
        )

        self.assertEqual(
            errors,
            [
                "mixed.json: profile torch cannot include JAX package py-jaxlib",
                "mixed.json: graph includes both torch-family packages py-torch and JAX packages py-jaxlib",
            ],
        )

    def test_jax_profile_rejects_torch_family_graph(self) -> None:
        errors = guard.check_document(graph("python", "py-jax", "py-triton"), "jax.json", profile="jax")

        self.assertEqual(
            errors,
            [
                "jax.json: profile jax cannot include torch-family package py-triton",
                "jax.json: graph includes both torch-family packages py-triton and JAX packages py-jax",
            ],
        )

    def test_lock_shape_is_checked_recursively(self) -> None:
        lock = {
            "concrete_specs": {
                "root": {"name": "py-jax"},
                "dep": {"name": "py-torch"},
            }
        }

        errors = guard.check_document(lock, "spack.lock", profile="jax")

        self.assertEqual(
            errors,
            [
                "spack.lock: profile jax cannot include torch-family package py-torch",
                "spack.lock: graph includes both torch-family packages py-torch and JAX packages py-jax",
            ],
        )

    def test_build_invocation_text_rejects_mixed_specs(self) -> None:
        text = "spack solve py-torch py-triton py-jaxlib"

        errors = guard.check_invocation(text, "cmdline", profile="torch")

        self.assertEqual(
            errors,
            [
                "cmdline: profile torch cannot include JAX package py-jaxlib",
                "cmdline: graph includes both torch-family packages py-torch, py-triton and JAX packages py-jaxlib",
            ],
        )

    def test_unknown_profile_is_rejected(self) -> None:
        with self.assertRaises(SystemExit):
            guard.check_document(graph("py-torch"), "torch.json", profile="unified")


if __name__ == "__main__":
    unittest.main()
