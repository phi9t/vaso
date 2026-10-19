#!/usr/bin/env python3
"""Tests for module_boundary_dev_repo_guard."""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import module_boundary_dev_repo_guard as guard


def rule_fixture(rule_id: str, test_method: str) -> tuple[str, str]:
    assert rule_id in guard.RULE_IDS
    return rule_id, test_method


RULE_FIXTURES = dict(
    (
        rule_fixture(
            "non-dev-toolchain-dev-repo",
            "test_non_dev_registered_toolchain_cannot_reach_dev_only_repo_transitively",
        ),
    )
)


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(text), encoding="utf-8")


def scratch_parent() -> Path:
    return Path(os.environ.get("TEST_TMPDIR", Path.cwd()))


class ModuleBoundaryDevRepoGuardUnitTest(unittest.TestCase):
    def test_rule_fixture_manifest_matches_guard_rule_ids(self) -> None:
        self.assertEqual(set(RULE_FIXTURES), guard.RULE_IDS)
        missing = sorted(name for name in RULE_FIXTURES.values() if not hasattr(self, name))
        self.assertEqual(missing, [])

    def test_non_dev_registered_toolchain_cannot_reach_dev_only_repo_transitively(self) -> None:
        with tempfile.TemporaryDirectory(dir=scratch_parent()) as raw_tmp:
            root = Path(raw_tmp)
            write(
                root / "MODULE.bazel",
                """\
                module(name = "fixture", version = "0.0.0")

                register_toolchains("//toolchains:leaky_toolchain")
                """,
            )
            write(
                root / "dev.MODULE.bazel",
                """\
                local_repository = use_repo_rule("@bazel_tools//tools/build_defs/repo:local.bzl", "local_repository")

                local_repository(
                    name = "dev_only_python",
                    path = "dev-only-python",
                    dev_dependency = True,
                )
                """,
            )
            write(
                root / "toolchains" / "BUILD.bazel",
                """\
                toolchain(
                    name = "leaky_toolchain",
                    toolchain = ":runtime_pair",
                )

                py_runtime_pair(
                    name = "runtime_pair",
                    py3_runtime = ":runtime",
                )

                py_runtime(
                    name = "runtime",
                    files = ["@dev_only_python//:prefix"],
                )
                """,
            )

            violations = guard.find_violations(
                root / "MODULE.bazel",
                root / "dev.MODULE.bazel",
                root / "toolchains" / "BUILD.bazel",
            )

        self.assertEqual(len(violations), 1)
        self.assertEqual(violations[0].registered_label, "//toolchains:leaky_toolchain")
        self.assertEqual(violations[0].repo, "dev_only_python")
        self.assertIn("//toolchains:runtime", violations[0].path)

    def test_dev_only_registration_may_reach_dev_only_repo(self) -> None:
        with tempfile.TemporaryDirectory(dir=scratch_parent()) as raw_tmp:
            root = Path(raw_tmp)
            write(
                root / "MODULE.bazel",
                """\
                module(name = "fixture", version = "0.0.0")

                register_toolchains("//toolchains:dev_toolchain", dev_dependency = True)
                """,
            )
            write(
                root / "dev.MODULE.bazel",
                """\
                local_repository = use_repo_rule("@bazel_tools//tools/build_defs/repo:local.bzl", "local_repository")

                local_repository(
                    name = "dev_only_python",
                    path = "dev-only-python",
                    dev_dependency = True,
                )
                """,
            )
            write(
                root / "toolchains" / "BUILD.bazel",
                """\
                toolchain(
                    name = "dev_toolchain",
                    toolchain = ":runtime",
                )

                py_runtime(
                    name = "runtime",
                    files = ["@dev_only_python//:prefix"],
                )
                """,
            )

            violations = guard.find_violations(
                root / "MODULE.bazel",
                root / "dev.MODULE.bazel",
                root / "toolchains" / "BUILD.bazel",
            )

        self.assertEqual(violations, [])


class ModuleBoundaryDevRepoGuardLiveTest(unittest.TestCase):
    def test_live_module_non_dev_toolchain_registrations_do_not_use_dev_only_repos(self) -> None:
        parser = argparse.ArgumentParser()
        parser.add_argument("--module", required=True)
        parser.add_argument("--dev-module", required=True)
        parser.add_argument("--toolchains-build", required=True)
        args, _ = parser.parse_known_args()

        violations = guard.find_violations(
            Path(args.module),
            Path(args.dev_module),
            Path(args.toolchains_build),
        )

        self.assertEqual([violation.format() for violation in violations], [])


if __name__ == "__main__":
    unittest.main(argv=[__file__])
