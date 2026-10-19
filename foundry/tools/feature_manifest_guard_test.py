#!/usr/bin/env python3
"""Tests for feature_manifest_guard.py."""

from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path
from unittest import mock


SCRIPT = Path(__file__).with_name("feature_manifest_guard.py")
SPEC = importlib.util.spec_from_file_location("feature_manifest_guard", SCRIPT)
assert SPEC is not None
guard = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = guard
SPEC.loader.exec_module(guard)


def rule_fixture(rule_id: str, test_method: str) -> tuple[str, str]:
    return rule_id, test_method


RULE_FIXTURES = dict(
    (
        rule_fixture("dangling-verifier-id", "test_manifest_verifier_ids_must_exist_in_workloads"),
        rule_fixture("enabled-flag-manifest-entry", "test_enabled_flags_require_manifest_entries"),
        rule_fixture("fc-d1-off-flag", "test_fc_d1_off_flags_require_off_status_and_reason"),
        rule_fixture("jaxlib-feature-manifest-entry", "test_jaxlib_wheels_and_cuda_features_require_manifest_entries"),
        rule_fixture("manifest-schema", "test_manifest_entries_require_known_status_and_lists"),
        rule_fixture("release-selection", "test_verified_ids_must_be_selected_by_release_workloads"),
        rule_fixture("unverified-pending", "test_unverified_pending_claims_are_rejected"),
    )
)


def valid_manifest() -> dict[str, object]:
    return {
        "schema_version": 1,
        "profiles": {
            "torch": {
                "features": {
                    "cuda": {
                        "status": "verified",
                        "flags": ["pytorch:USE_CUDA"],
                        "verifier_ids": ["W1a"],
                    },
                    "magma": {
                        "status": "off",
                        "flags": ["pytorch:USE_MAGMA"],
                        "verifier_ids": [],
                        "reason": "FC-D1: MAGMA stays off.",
                    },
                    "mkldnn": {
                        "status": "off",
                        "flags": ["pytorch:USE_MKLDNN"],
                        "verifier_ids": [],
                        "reason": "FC-D1: oneDNN/MKLDNN stays off.",
                    },
                    "mkl": {
                        "status": "off",
                        "flags": ["pytorch:USE_MKL"],
                        "verifier_ids": [],
                        "reason": "FC-D1: MKL stays off.",
                    },
                    "webp": {
                        "status": "off",
                        "flags": ["torchvision:TORCHVISION_USE_WEBP"],
                        "verifier_ids": [],
                        "reason": "FC-D1: WebP stays off.",
                    },
                }
            },
            "jax": {
                "features": {
                    "jax-wheel": {
                        "status": "verified",
                        "jaxlib_features": ["jaxlib:wheel:jax"],
                        "verifier_ids": ["W3a"],
                    },
                    "jax-nvshmem": {
                        "status": "verified",
                        "jaxlib_features": ["jaxlib:nvshmem"],
                        "verifier_ids": ["W5g"],
                    },
                }
            },
        },
    }


class FeatureManifestGuardTest(unittest.TestCase):
    def test_rule_fixture_manifest_matches_guard_rule_ids(self) -> None:
        self.assertEqual(set(RULE_FIXTURES), guard.RULE_IDS)
        missing = sorted(name for name in RULE_FIXTURES.values() if not hasattr(self, name))
        self.assertEqual(missing, [])

    def test_enabled_flags_require_manifest_entries(self) -> None:
        manifest = valid_manifest()

        errors = guard.validate_manifest(
            manifest,
            enabled_flags_by_profile={"torch": {"pytorch:USE_CUDA", "pytorch:USE_FBGEMM"}},
            jaxlib_features_by_profile={},
            workload_ids={"W1a", "W3a"},
            release_workload_ids_by_profile={"torch": {"W1a"}, "jax": {"W3a"}},
        )

        self.assertIn(
            "enabled-flag-manifest-entry",
            {error.rule_id for error in errors},
        )
        self.assertTrue(any("pytorch:USE_FBGEMM" in error.message for error in errors))

    def test_fc_d1_off_flags_require_off_status_and_reason(self) -> None:
        manifest = valid_manifest()
        manifest["profiles"]["torch"]["features"]["mkl"]["reason"] = "human note"

        errors = guard.validate_manifest(
            manifest,
            enabled_flags_by_profile={"torch": {"pytorch:USE_CUDA"}},
            jaxlib_features_by_profile={},
            workload_ids={"W1a", "W3a"},
            release_workload_ids_by_profile={"torch": {"W1a"}, "jax": {"W3a"}},
        )

        self.assertIn("fc-d1-off-flag", {error.rule_id for error in errors})
        self.assertTrue(any("pytorch:USE_MKL" in error.message for error in errors))

    def test_manifest_verifier_ids_must_exist_in_workloads(self) -> None:
        manifest = valid_manifest()
        manifest["profiles"]["torch"]["features"]["cuda"]["verifier_ids"] = ["W6missing"]

        errors = guard.validate_manifest(
            manifest,
            enabled_flags_by_profile={"torch": {"pytorch:USE_CUDA"}},
            jaxlib_features_by_profile={},
            workload_ids={"W1a", "W3a"},
            release_workload_ids_by_profile={"torch": {"W6missing"}, "jax": {"W3a"}},
        )

        self.assertIn("dangling-verifier-id", {error.rule_id for error in errors})
        self.assertTrue(any("W6missing" in error.message for error in errors))

    def test_verified_ids_must_be_selected_by_release_workloads(self) -> None:
        manifest = valid_manifest()

        errors = guard.validate_manifest(
            manifest,
            enabled_flags_by_profile={"torch": {"pytorch:USE_CUDA"}},
            jaxlib_features_by_profile={},
            workload_ids={"W1a", "W3a"},
            release_workload_ids_by_profile={"torch": set(), "jax": {"W3a"}},
        )

        self.assertIn("release-selection", {error.rule_id for error in errors})
        self.assertTrue(any("W1a" in error.message for error in errors))

    def test_unverified_pending_claims_are_rejected(self) -> None:
        manifest = valid_manifest()
        manifest["profiles"]["jax"]["features"]["jax-nvshmem"] = {
            "status": "unverified-pending",
            "jaxlib_features": ["jaxlib:nvshmem"],
            "verifier_ids": ["W5g"],
            "reason": "FC-V3: live workload shows this claim is not release-verified.",
        }

        errors = guard.validate_manifest(
            manifest,
            enabled_flags_by_profile={"torch": {"pytorch:USE_CUDA"}},
            jaxlib_features_by_profile={"jax": {"jaxlib:wheel:jax", "jaxlib:nvshmem"}},
            workload_ids={"W1a", "W3a", "W5g"},
            release_workload_ids_by_profile={"torch": {"W1a"}, "jax": {"W3a", "W5g"}},
        )

        self.assertIn("unverified-pending", {error.rule_id for error in errors})
        self.assertTrue(any("jax:jax-nvshmem" in error.message for error in errors))

    def test_jaxlib_wheels_and_cuda_features_require_manifest_entries(self) -> None:
        manifest = valid_manifest()

        errors = guard.validate_manifest(
            manifest,
            enabled_flags_by_profile={"torch": {"pytorch:USE_CUDA"}},
            jaxlib_features_by_profile={
                "jax": {"jaxlib:wheel:jax", "jaxlib:cuda", "jaxlib:cudnn"},
            },
            workload_ids={"W1a", "W3a"},
            release_workload_ids_by_profile={"torch": {"W1a"}, "jax": {"W3a"}},
        )

        self.assertIn("jaxlib-feature-manifest-entry", {error.rule_id for error in errors})
        self.assertTrue(any("jaxlib:cuda" in error.message for error in errors))

    def test_manifest_entries_require_known_status_and_lists(self) -> None:
        manifest = valid_manifest()
        manifest["profiles"]["torch"]["features"]["cuda"]["status"] = "maybe"
        manifest["profiles"]["torch"]["features"]["cuda"]["verifier_ids"] = "W1a"

        errors = guard.validate_manifest(
            manifest,
            enabled_flags_by_profile={"torch": {"pytorch:USE_CUDA"}},
            jaxlib_features_by_profile={},
            workload_ids={"W1a", "W3a"},
            release_workload_ids_by_profile={"torch": {"W1a"}, "jax": {"W3a"}},
        )

        self.assertIn("manifest-schema", {error.rule_id for error in errors})

    def test_valid_manifest_passes(self) -> None:
        errors = guard.validate_manifest(
            valid_manifest(),
            enabled_flags_by_profile={"torch": {"pytorch:USE_CUDA"}},
            jaxlib_features_by_profile={"jax": {"jaxlib:wheel:jax", "jaxlib:nvshmem"}},
            workload_ids={"W1a", "W3a", "W5g"},
            release_workload_ids_by_profile={"torch": {"W1a"}, "jax": {"W3a", "W5g"}},
        )

        self.assertEqual(errors, [])

    def test_snapshot_plan_supplies_native_toolchain_descriptor(self) -> None:
        sentinel = object()

        class Module:
            REQUIRED_PREFIXES = ("cuda",)

            @staticmethod
            def build_env(prefixes: dict[str, str], arch: str, version: str, max_jobs: str, abi: str, native_toolchain: object | None = None) -> dict[str, str]:
                if native_toolchain is not sentinel:
                    raise SystemExit("native action toolchain descriptor is required")
                return {"USE_CUDA": "1"}

        with mock.patch.object(guard, "native_toolchain_fixture", return_value=sentinel):
            snapshot = guard.snapshot_plan("pytorch", Module())

        self.assertEqual(snapshot.env["USE_CUDA"], "1")


if __name__ == "__main__":
    unittest.main()
