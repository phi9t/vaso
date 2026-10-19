#!/usr/bin/env python3
"""Tests for hermetic_test_guard.py."""

from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path


SCRIPT = Path(__file__).with_name("hermetic_test_guard.py")
assert SCRIPT.exists(), "hermetic test guard is missing"
SPEC = importlib.util.spec_from_file_location("hermetic_test_guard", SCRIPT)
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
        rule_fixture("nested-bazel-hermetic", "test_bare_nested_bazel_query_is_reported"),
        rule_fixture("fetch-url-literal", "test_allowlisted_url_fetch_is_accepted_with_offline_reason"),
        rule_fixture("live-estate-state", "test_assembled_bazel_cquery_against_live_estate_output_base_is_reported"),
        rule_fixture("local-test", "test_local_true_standing_test_is_reported"),
        rule_fixture("moving-git-ref", "test_git_diff_origin_main_is_reported"),
    )
)


def temporary_directory() -> tempfile.TemporaryDirectory[str]:
    return tempfile.TemporaryDirectory(dir=os.environ.get("TMPDIR"))


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(text), encoding="utf-8")


class HermeticTestGuardTest(unittest.TestCase):
    def test_standing_labels_do_not_parse_file_registry_as_a_target(self) -> None:
        with temporary_directory() as tmp:
            script = Path(tmp) / "standing.sh"
            script.write_text('"$BAZEL" test --registry=file:///vaso/sources/bcr/registry/current //tools:all --test_tag_filters=-insula-only\n')
            labels, excluded = guard.standing_verify_labels(script)
        self.assertEqual(labels, ["//tools:all"])
        self.assertEqual(excluded, {"insula-only"})

    def test_rule_fixture_manifest_matches_guard_rule_ids(self) -> None:
        self.assertEqual(set(RULE_FIXTURES), guard.RULE_IDS)
        missing = sorted(name for name in RULE_FIXTURES.values() if not hasattr(self, name))
        self.assertEqual(missing, [])

    def test_bare_nested_bazel_query_is_reported(self) -> None:
        with temporary_directory() as tmp:
            root = Path(tmp)
            write(
                root / "bad_test.py",
                """
                import subprocess

                def test_query():
                    subprocess.run(["bazel", "query", "//:all"], check=True)
                """,
            )

            findings = guard.collect_findings(root, source_paths=[root / "bad_test.py"])

        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].rule, "nested-bazel-hermetic")
        self.assertEqual(findings[0].path, "bad_test.py")
        self.assertIn("missing --lockfile_mode=off", findings[0].detail)
        self.assertIn("no local_path_override declarations", findings[0].detail)

    def test_nested_bazel_with_lockfile_off_and_overrides_is_accepted(self) -> None:
        with temporary_directory() as tmp:
            root = Path(tmp)
            write(
                root / "good_test.py",
                '''
                import subprocess
                from pathlib import Path

                def test_query():
                    root = Path("fixture")
                    (root / "MODULE.bazel").write_text("""
                    module(name = "fixture", version = "0.0.0")
                    bazel_dep(name = "rules_cc", version = "0.2.17")
                    local_path_override(module_name = "rules_cc", path = "rules_cc")
                    """)
                    subprocess.run(
                        ["bazel", "query", "--lockfile_mode=off", "--noshow_progress", "//:all"],
                        check=True,
                    )
                ''',
            )

            findings = guard.collect_findings(root, source_paths=[root / "good_test.py"])

        self.assertEqual(findings, [])

    def test_nested_bazel_with_shared_override_helper_is_accepted(self) -> None:
        with temporary_directory() as tmp:
            root = Path(tmp)
            write(
                root / "foundry" / "tools" / "nested_bazel_fixture.py",
                '''
                MODULE_OVERRIDE_VERSIONS = {
                    "rules_cc": "0.2.17",
                    "rules_python": "1.9.0",
                }

                def local_override_block(root):
                    return ""
                ''',
            )
            source = root / "foundry" / "tools" / "good_shared_helper_test.py"
            write(
                source,
                '''
                import subprocess
                from nested_bazel_fixture import local_override_block

                def test_query():
                    module = """
                    module(name = "fixture", version = "0.0.0")
                    bazel_dep(name = "rules_cc", version = "0.2.17")
                    bazel_dep(name = "rules_python", version = "1.9.0")
                    """ + local_override_block("overrides")
                    subprocess.run(["bazel", "query", "--lockfile_mode=off", "//:all"], check=True)
                ''',
            )

            findings = guard.collect_findings(root, source_paths=[source])

        self.assertEqual(findings, [])

    def test_declared_bazel_dep_without_matching_override_is_reported(self) -> None:
        with temporary_directory() as tmp:
            root = Path(tmp)
            write(
                root / "missing_override_test.py",
                '''
                import subprocess
                from pathlib import Path

                def test_query():
                    root = Path("fixture")
                    (root / "MODULE.bazel").write_text("""
                    module(name = "fixture", version = "0.0.0")
                    bazel_dep(name = "rules_cc", version = "0.2.17")
                    """)
                    subprocess.run(["bazel", "query", "--lockfile_mode=off", "//:all"], check=True)
                ''',
            )

            findings = guard.collect_findings(root, source_paths=[root / "missing_override_test.py"])

        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].rule, "nested-bazel-hermetic")
        self.assertIn("missing local_path_override for bazel_dep: rules_cc", findings[0].detail)

    def test_assembled_bazel_cquery_against_live_estate_output_base_is_reported(self) -> None:
        with temporary_directory() as tmp:
            root = Path(tmp)
            write(
                root / "assembled_live_test.py",
                '''
                import os
                import subprocess
                from pathlib import Path

                def bazel_binary():
                    return Path("bazel-real")

                def test_query():
                    runner = bazel_binary()
                    estate = Path(os.environ["VASO_ESTATE_ROOT"])
                    output_base = estate / "vaso" / "lines" / "cu130" / "cache" / "bazel" / "output-base"
                    argv = [str(runner)]
                    argv += [f"--output_base={output_base}"]
                    argv.extend(["cquery", "//tools:target"])
                    subprocess.run(argv, check=True)
                ''',
            )

            findings = guard.collect_findings(root, source_paths=[root / "assembled_live_test.py"])

        self.assertIn("live-estate-state", [finding.rule for finding in findings])
        self.assertIn("nested-bazel-hermetic", [finding.rule for finding in findings])
        self.assertTrue(
            any("VASO_ESTATE_ROOT/vaso/lines" in finding.detail for finding in findings),
            [finding.identity for finding in findings],
        )
        self.assertTrue(
            any("missing --lockfile_mode=off" in finding.detail for finding in findings),
            [finding.identity for finding in findings],
        )

    def test_embedded_patch_text_with_workspace2_path_is_not_live_estate_state(self) -> None:
        with temporary_directory() as tmp:
            root = Path(tmp)
            write(
                root / "patch_fixture.py",
                '''
                PATCH = """
                diff --git a/workspace2.bzl b/workspace2.bzl
                --- a/workspace2.bzl
                +++ b/workspace2.bzl
                @@ -603,4 +603,5 @@ def _tf_repositories():
                     tf_http_archive(
                         urls = tf_mirror_urls("https://github.com/bazelbuild/rules_go/releases/download/v0.34.0/rules_go-v0.34.0.zip"),
                     )
                """
                ''',
            )

            findings = guard.collect_findings(root, source_paths=[root / "patch_fixture.py"])

        self.assertEqual(findings, [])

    def test_assembled_bazel_cquery_with_test_temp_output_base_is_accepted(self) -> None:
        with temporary_directory() as tmp:
            root = Path(tmp)
            write(
                root / "assembled_temp_test.py",
                '''
                import os
                import subprocess
                from pathlib import Path

                def bazel_binary():
                    return Path("bazel-real")

                def test_query():
                    runner = bazel_binary()
                    test_tmp = Path(os.environ["TEST_TMPDIR"])
                    output_base = test_tmp / "nested-bazel-ob"
                    argv = [str(runner)]
                    argv += [f"--output_base={output_base}"]
                    argv.extend(["query", "--lockfile_mode=off", "--noshow_progress", "//tools:target"])
                    subprocess.run(argv, check=True)

                def fixture_module_text(root):
                    return f"""
                    module(name = "fixture", version = "0.0.0")
                    local_path_override(module_name = "rules_cc", path = "{root / 'rules_cc'}")
                    """
                ''',
            )

            findings = guard.collect_findings(root, source_paths=[root / "assembled_temp_test.py"])

        self.assertEqual(findings, [])

    def test_repo_module_imported_by_standing_test_is_scanned(self) -> None:
        with temporary_directory() as tmp:
            root = Path(tmp)
            write(
                root / "imports_live_test.py",
                """
                import live_helper

                def test_uses_helper():
                    live_helper.include_root()
                """,
            )
            write(
                root / "live_helper.py",
                '''
                import os
                from pathlib import Path

                def include_root():
                    return Path(os.environ["VASO_ESTATE_ROOT"]) / "releases" / "torch"
                ''',
            )

            findings = guard.collect_findings(root, source_paths=[root / "imports_live_test.py"])

        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].rule, "live-estate-state")
        self.assertEqual(findings[0].path, "live_helper.py")
        self.assertIn("VASO_ESTATE_ROOT/releases", findings[0].detail)

    def test_git_diff_origin_main_is_reported(self) -> None:
        with temporary_directory() as tmp:
            root = Path(tmp)
            write(
                root / "git_test.py",
                """
                import subprocess

                def test_diff():
                    subprocess.run(["git", "diff", "origin/main", "--", "native"], check=True)
                """,
            )

            findings = guard.collect_findings(root, source_paths=[root / "git_test.py"])

        self.assertEqual(
            [finding.identity for finding in findings],
            ["moving-git-ref\tgit_test.py:5\tgit diff against moving ref origin/main"],
        )

    def test_local_true_standing_test_is_reported(self) -> None:
        with temporary_directory() as tmp:
            root = Path(tmp)
            write(
                root / "tools" / "agents" / "standing-verify.sh",
                """
                "$BAZEL" --output_base="$VASO_BAZEL_OB" test \\
                  //tools:all \\
                  --test_tag_filters=-insula-only
                """,
            )
            write(
                root / "foundry" / "tools" / "BUILD.bazel",
                """
                py_test(
                    name = "needs_local_test",
                    srcs = ["needs_local_test.py"],
                    local = True,
                    main = "needs_local_test.py",
                )
                """,
            )
            write(root / "foundry" / "tools" / "needs_local_test.py", "")

            findings = guard.collect_findings(root, standing_verify=root / "tools" / "agents" / "standing-verify.sh")

        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].rule, "local-test")
        self.assertEqual(findings[0].path, "foundry/tools/BUILD.bazel")
        self.assertEqual(findings[0].detail, "//tools:needs_local_test sets local = True")

    def test_allowlisted_url_fetch_is_accepted_with_offline_reason(self) -> None:
        with temporary_directory() as tmp:
            root = Path(tmp)
            source = root / "fetch_test.py"
            write(
                source,
                """
                import urllib.request

                def test_fetch():
                    urllib.request.urlretrieve("https://example.invalid/archive.tar.gz", "archive.tar.gz")
                """,
            )
            findings = guard.collect_findings(root, source_paths=[source])
            self.assertEqual(
                [finding.identity for finding in findings],
                ["fetch-url-literal\tfetch_test.py:5\tfetches https://example.invalid/archive.tar.gz"],
            )
            allowlist_path = root / "allowlist.txt"
            write(
                allowlist_path,
                findings[0].identity + "\toffline-safe: fixture URL is never reached; the test stubs retrieval.\\n",
            )

            allowlist = guard.read_allowlist(allowlist_path)
            failures = guard.unallowlisted_findings(findings, allowlist)

        self.assertEqual(failures, [])

    def test_network_allowlist_entries_must_name_offline_safety(self) -> None:
        with temporary_directory() as tmp:
            path = Path(tmp) / "allowlist.txt"
            write(
                path,
                "fetch-url-literal\tfetch_test.py:5\tfetches https://example.invalid/a.tar.gz\tfixture\\n",
            )

            with self.assertRaises(ValueError):
                guard.read_allowlist(path)

    def test_standing_verify_sources_are_selected_from_build_files(self) -> None:
        with temporary_directory() as tmp:
            root = Path(tmp)
            write(
                root / "tools" / "agents" / "standing-verify.sh",
                """
                "$BAZEL" --output_base="$VASO_BAZEL_OB" test \\
                  //tools:all //workloads:criteria_test \\
                  //native/common:native_action_toolchain_test \\
                  //native/pytorch:plan_test //native/pytorch:action_driver_test \\
                  //native/torchvision:plan_test //native/torchvision:action_driver_test \\
                  //native/torchaudio:plan_test //native/torchaudio:action_driver_test \\
                  //native/triton:plan_test //native/triton:action_driver_test //native/triton:action_prefix_test \\
                  //native/jaxlib:plan_test //native/jaxlib:action_driver_test \\
                  --test_tag_filters=-insula-only
                """,
            )
            write(
                root / "foundry" / "tools" / "BUILD.bazel",
                """
                py_test(name = "good_test", srcs = ["good_test.py"], main = "good_test.py")
                sh_test(name = "insula_test", srcs = ["insula_test.sh"], tags = ["insula-only"])
                """,
            )
            write(
                root / "foundry" / "workloads" / "BUILD.bazel",
                """
                py_test(name = "criteria_test", srcs = ["criteria_test.py"], main = "criteria_test.py")
                """,
            )
            write(
                root / "foundry" / "native" / "pytorch" / "BUILD.bazel",
                """
                py_test(name = "plan_test", srcs = ["plan.py", "plan_test.py"], main = "plan_test.py")
                py_test(name = "action_driver_test", srcs = ["action_driver.py", "action_driver_test.py"], main = "action_driver_test.py")
                """,
            )
            for package in ("torchvision", "torchaudio", "triton", "jaxlib"):
                write(
                    root / "foundry" / "native" / package / "BUILD.bazel",
                    """
                    py_test(name = "plan_test", srcs = ["plan.py", "plan_test.py"], main = "plan_test.py")
                    py_test(name = "action_driver_test", srcs = ["action_driver.py", "action_driver_test.py"], main = "action_driver_test.py")
                    """,
                )
            write(
                root / "foundry" / "native" / "triton" / "BUILD.bazel",
                """
                py_test(name = "plan_test", srcs = ["plan.py", "plan_test.py"], main = "plan_test.py")
                py_test(name = "action_driver_test", srcs = ["action_driver.py", "action_driver_test.py"], main = "action_driver_test.py")
                py_test(name = "action_prefix_test", srcs = ["action_prefix_test.py"], main = "action_prefix_test.py")
                """,
            )
            write(
                root / "foundry" / "native" / "common" / "BUILD.bazel",
                """
                py_test(name = "native_action_toolchain_test", srcs = ["native_action_toolchain.py", "native_action_toolchain_test.py"], main = "native_action_toolchain_test.py")
                """,
            )
            write(root / "foundry" / "tools" / "good_test.py", "")
            write(root / "foundry" / "tools" / "insula_test.sh", "")
            write(root / "foundry" / "workloads" / "criteria_test.py", "")
            write(root / "foundry" / "native" / "common" / "native_action_toolchain.py", "")
            write(
                root / "foundry" / "native" / "common" / "native_action_toolchain_test.py",
                "",
            )
            write(root / "foundry" / "native" / "pytorch" / "plan.py", "")
            write(root / "foundry" / "native" / "pytorch" / "plan_test.py", "")
            write(root / "foundry" / "native" / "pytorch" / "action_driver.py", "")
            write(root / "foundry" / "native" / "pytorch" / "action_driver_test.py", "")
            for package in ("torchvision", "torchaudio", "triton", "jaxlib"):
                write(root / "foundry" / "native" / package / "plan.py", "")
                write(root / "foundry" / "native" / package / "plan_test.py", "")
                write(root / "foundry" / "native" / package / "action_driver.py", "")
                write(root / "foundry" / "native" / package / "action_driver_test.py", "")
            write(root / "foundry" / "native" / "triton" / "action_prefix_test.py", "")

            sources = [
                path.relative_to(root).as_posix()
                for path in guard.standing_test_source_paths(root, root / "tools" / "agents" / "standing-verify.sh")
            ]

        self.assertEqual(
            sources,
            [
                "foundry/tools/good_test.py",
                "foundry/workloads/criteria_test.py",
                "foundry/native/common/native_action_toolchain.py",
                "foundry/native/common/native_action_toolchain_test.py",
                "foundry/native/pytorch/plan.py",
                "foundry/native/pytorch/plan_test.py",
                "foundry/native/pytorch/action_driver.py",
                "foundry/native/pytorch/action_driver_test.py",
                "foundry/native/torchvision/plan.py",
                "foundry/native/torchvision/plan_test.py",
                "foundry/native/torchvision/action_driver.py",
                "foundry/native/torchvision/action_driver_test.py",
                "foundry/native/torchaudio/plan.py",
                "foundry/native/torchaudio/plan_test.py",
                "foundry/native/torchaudio/action_driver.py",
                "foundry/native/torchaudio/action_driver_test.py",
                "foundry/native/triton/plan.py",
                "foundry/native/triton/plan_test.py",
                "foundry/native/triton/action_driver.py",
                "foundry/native/triton/action_driver_test.py",
                "foundry/native/triton/action_prefix_test.py",
                "foundry/native/jaxlib/plan.py",
                "foundry/native/jaxlib/plan_test.py",
                "foundry/native/jaxlib/action_driver.py",
                "foundry/native/jaxlib/action_driver_test.py",
            ],
        )


if __name__ == "__main__":
    unittest.main()
