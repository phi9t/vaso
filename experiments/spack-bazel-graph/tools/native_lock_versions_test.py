#!/usr/bin/env python3
"""Regression tests for native_lock_versions.py."""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).with_name("native_lock_versions.py")
SPEC = importlib.util.spec_from_file_location("native_lock_versions", SCRIPT)
assert SPEC is not None
checker = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = checker
SPEC.loader.exec_module(checker)

OVERRIDES = {
    "native": {"python": "@python_native//:lib", "libxml2": "@libxml2_native//:lib"},
    "provided_versions": {"python": "3.14.5", "libxml2": "2.13.9"},
    "known_version_substitutions": [
        {"key": "python", "graph_version": "3.13.13", "provided": "3.14.5", "ticket": "07"}
    ],
}


def native_node(package: str, version: str, label: str) -> dict:
    return {"package": package, "version": version, "build": "native", "native_prefix": label}


class NativeLockVersionsTest(unittest.TestCase):
    def check(self, locks: dict[str, list[dict]], stale: dict | None = None) -> list[str]:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            overrides = root / "native_overrides.json"
            overrides.write_text(json.dumps(OVERRIDES), encoding="utf-8")
            paths = []
            for name, nodes in locks.items():
                path = root / name
                path.write_text(
                    json.dumps({"packages": {f"spack_{i}": node for i, node in enumerate(nodes)}}),
                    encoding="utf-8",
                )
                paths.append(path)
            return checker.lock_version_errors(overrides, paths, stale_evidence=stale or {})

    def test_matching_and_spack_nodes_pass(self) -> None:
        errors = self.check(
            {
                "a.lock.json": [
                    native_node("libxml2", "2.13.9", "@libxml2_native//:lib"),
                    {"package": "zstd", "version": "1.5.7", "build": "spack"},
                ]
            }
        )

        self.assertEqual(errors, [])

    def test_silent_substitution_fails(self) -> None:
        errors = self.check({"hwloc.lock.json": [native_node("libxml2", "2.15.3", "@libxml2_native//:lib")]})

        self.assertEqual(
            errors,
            ["hwloc.lock.json: libxml2@2.15.3 is flipped to @libxml2_native//:lib, which builds libxml2@2.13.9"],
        )

    def test_live_known_substitution_passes(self) -> None:
        errors = self.check({"spack_graph.lock.json": [native_node("python", "3.13.13", "@python_native//:lib")]})

        self.assertEqual(errors, [])

    def test_stale_evidence_entry_allows_and_must_stay_current(self) -> None:
        stale = {("hwloc.lock.json", "libxml2", "2.15.3"): "historical"}
        lock = {"hwloc.lock.json": [native_node("libxml2", "2.15.3", "@libxml2_native//:lib")]}
        self.assertEqual(self.check(lock, stale), [])

        errors = self.check({"hwloc.lock.json": []}, stale)
        self.assertEqual(
            errors,
            ["stale STALE_EVIDENCE_LOCKS entry: hwloc.lock.json libxml2@2.15.3 no longer matches a native flip"],
        )

    def test_unknown_native_rule_fails(self) -> None:
        errors = self.check({"a.lock.json": [native_node("zstd", "1.5.7", "@zstd_native//:lib")]})

        self.assertEqual(errors, ["a.lock.json: zstd@1.5.7 uses unknown native rule @zstd_native//:lib"])


if __name__ == "__main__":
    unittest.main()
