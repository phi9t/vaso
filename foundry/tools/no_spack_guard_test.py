#!/usr/bin/env python3
"""Tests for no_spack_guard.py."""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).with_name("no_spack_guard.py")
assert SCRIPT.exists(), "no-Spack guard is missing"
SPEC = importlib.util.spec_from_file_location("no_spack_guard", SCRIPT)
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
        rule_fixture("spack-executable", "test_new_spack_call_fails_beyond_baseline"),
        rule_fixture("spack-repository", "test_spack_dist_repository_reference_is_reported"),
        rule_fixture("spack-env-var", "test_spack_environment_variable_is_reported"),
        rule_fixture("build-spack-lock", "test_profile_lock_build_spack_is_reported"),
    )
)


def temporary_directory() -> tempfile.TemporaryDirectory[str]:
    return tempfile.TemporaryDirectory(dir=os.environ.get("TMPDIR"))


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def write_json(path: Path, value: object) -> None:
    write(path, json.dumps(value, indent=2) + "\n")


class NoSpackGuardTest(unittest.TestCase):
    def test_rule_fixture_manifest_matches_guard_rule_ids(self) -> None:
        self.assertEqual(set(RULE_FIXTURES), guard.RULE_IDS)
        missing = sorted(name for name in RULE_FIXTURES.values() if not hasattr(self, name))
        self.assertEqual(missing, [])

    def test_new_spack_call_fails_beyond_baseline(self) -> None:
        with temporary_directory() as tmp:
            root = Path(tmp)
            write(root / "run.sh", "#!/usr/bin/env bash\nspack spec zlib-ng\nspack install zlib-ng\n")

            findings = guard.collect_findings(root)
            unbaselined = guard.unbaselined_findings(findings, {})

        self.assertEqual(
            [finding.identity for finding in unbaselined],
            [
                "spack-executable\trun.sh:2\tspack spec zlib-ng",
                "spack-executable\trun.sh:3\tspack install zlib-ng",
            ],
        )

    def test_spack_subprocess_argv_is_reported(self) -> None:
        with temporary_directory() as tmp:
            root = Path(tmp)
            write(root / "tools" / "helper.py", "subprocess.run(['spack', 'spec', 'zlib-ng'])\n")

            findings = guard.collect_findings(root)

        self.assertEqual(
            [finding.identity for finding in findings],
            ["spack-executable\ttools/helper.py:1\tsubprocess.run(['spack', 'spec', 'zlib-ng'])"],
        )

    def test_legacy_baseline_entries_no_longer_cover_findings(self) -> None:
        with temporary_directory() as tmp:
            root = Path(tmp)
            write(root / "run.sh", "#!/usr/bin/env bash\nspack install zlib-ng\n")

            findings = guard.collect_findings(root)
            unbaselined = guard.unbaselined_findings(findings, {})

        self.assertEqual(
            [finding.identity for finding in unbaselined],
            ["spack-executable\trun.sh:2\tspack install zlib-ng"],
        )

    def test_git_worktree_scan_ignores_untracked_files(self) -> None:
        with temporary_directory() as tmp:
            root = Path(tmp)
            subprocess.run(["git", "init"], cwd=root, check=True, stdout=subprocess.DEVNULL)
            write(root / "run.sh", "#!/usr/bin/env bash\nexport SPACK_ROOT=/opt/spack\n")
            subprocess.run(["git", "add", "run.sh"], cwd=root, check=True)
            write(root / "untracked.sh", "#!/usr/bin/env bash\nspack install zlib-ng\n")
            write_json(
                root / "locks" / "torch.lock.json",
                {
                    "packages": {
                        "vaso_zlib_ng": {
                            "build": "spack",
                        }
                    }
                },
            )

            findings = guard.collect_findings(root)

        self.assertEqual(
            [finding.identity for finding in findings],
            ["spack-env-var\trun.sh:2\tSPACK_ROOT"],
        )

    def test_git_worktree_scan_preserves_default_text_scope(self) -> None:
        with temporary_directory() as tmp:
            root = Path(tmp)
            subprocess.run(["git", "init"], cwd=root, check=True, stdout=subprocess.DEVNULL)
            write(root / "synthetic" / "BUILD.bazel", "exports_files(['SPACK_ROOT'])\n")
            subprocess.run(["git", "add", "synthetic/BUILD.bazel"], cwd=root, check=True)

            findings = guard.collect_findings(root)

        self.assertEqual(findings, [])

    def test_allowlisted_docs_and_scratch_are_ignored(self) -> None:
        with temporary_directory() as tmp:
            root = Path(tmp)
            write(root / "docs" / "journal" / "note.md", "old command: spack install zlib-ng\n")
            write(root / ".scratch" / "feature" / "spec.md", "SPACK_ROOT=/legacy\n")

            findings = guard.collect_findings(
                root,
                text_paths=[
                    root / "docs" / "journal" / "note.md",
                    root / ".scratch" / "feature" / "spec.md",
                ],
            )

        self.assertEqual(findings, [])

    def test_spack_dist_repository_reference_is_reported(self) -> None:
        with temporary_directory() as tmp:
            root = Path(tmp)
            write(root / "MODULE.bazel", 'bazel_dep(name = "@spack_dist")\n')

            findings = guard.collect_findings(root)

        self.assertEqual(
            [finding.identity for finding in findings],
            ["spack-repository\tMODULE.bazel:1\t@spack_dist"],
        )

    def test_spack_environment_variable_is_reported(self) -> None:
        with temporary_directory() as tmp:
            root = Path(tmp)
            write(root / "tools" / "helper.sh", "export SPACK_ROOT=/opt/spack\n")

            findings = guard.collect_findings(root)

        self.assertEqual(
            [finding.identity for finding in findings],
            ["spack-env-var\ttools/helper.sh:1\tSPACK_ROOT"],
        )

    def test_vaso_spack_environment_variable_is_reported(self) -> None:
        with temporary_directory() as tmp:
            root = Path(tmp)
            write(root / "run.sh", "VASO_SPACK_INSTALL=1\n")

            findings = guard.collect_findings(root)

        self.assertEqual(
            [finding.identity for finding in findings],
            ["spack-env-var\trun.sh:1\tVASO_SPACK_INSTALL"],
        )

    def test_package_compat_environment_variables_are_allowed(self) -> None:
        with temporary_directory() as tmp:
            root = Path(tmp)
            write(
                root / "run.sh",
                "SPACK_ZLIB_NG_PREFIX=/opt/native\n"
                "SPACK_ZLIB_NG_BUILD=native\n"
                "SPACK_ZLIB_NG_VERSION=2.3.3\n",
            )

            findings = guard.collect_findings(root)

        self.assertEqual(findings, [])

    def test_legacy_capture_tools_are_not_build_entrypoints(self) -> None:
        with temporary_directory() as tmp:
            root = Path(tmp)
            write(root / "tools" / "spack_to_bazel.py", "spack spec zlib-ng\n")
            write(root / "tools" / "build_graph.py", "@spack_dist//:spack\n")

            findings = guard.collect_findings(root)

        self.assertEqual(findings, [])

    def test_new_shell_fixture_spack_call_is_reported(self) -> None:
        with temporary_directory() as tmp:
            root = Path(tmp)
            write(root / "tools" / "example_test.sh", "spack install zlib-ng\n")

            findings = guard.collect_findings(root)

        self.assertEqual(
            [finding.identity for finding in findings],
            ["spack-executable\ttools/example_test.sh:1\tspack install zlib-ng"],
        )

    def test_retired_spack_shell_fixtures_are_not_build_entrypoints(self) -> None:
        with temporary_directory() as tmp:
            root = Path(tmp)
            write(root / "tools" / "spack_lock_test.sh", "spack install zlib-ng\n")
            write(root / "tools" / "pytorch_recipe_provenance_test.sh", "@spack_dist//:spack\n")
            write(root / "native" / "py_llvmlite" / "recipe_provenance_test.sh", "spack spec py-llvmlite\n")

            findings = guard.collect_findings(root)

        self.assertEqual(findings, [])

    def test_profile_lock_build_spack_is_reported(self) -> None:
        with temporary_directory() as tmp:
            root = Path(tmp)
            write_json(
                root / "locks" / "torch.lock.json",
                {
                    "packages": {
                        "vaso_python": {
                            "package": "python",
                            "build": "spack",
                        },
                        "vaso_zlib_ng": {
                            "package": "zlib-ng",
                            "build": "native",
                        },
                    }
                },
            )

            findings = guard.collect_findings(root)

        self.assertEqual(
            [finding.identity for finding in findings],
            ["build-spack-lock\tlocks/torch.lock.json\tvaso_python"],
        )


if __name__ == "__main__":
    unittest.main()
