#!/usr/bin/env python3
"""Regression tests for python_abi_literal_guard.py."""

from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).with_name("python_abi_literal_guard.py")
SPEC = importlib.util.spec_from_file_location("python_abi_literal_guard", SCRIPT)
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
        rule_fixture("python-abi-allowlist-ratchet", "test_stale_allowlist_entry_fails"),
        rule_fixture("python-abi-coverage", "test_undeclared_source_file_fails_coverage"),
        rule_fixture("python-abi-literal", "test_new_literal_outside_allowlist_fails"),
    )
)


class PythonAbiLiteralGuardTest(unittest.TestCase):
    def test_rule_fixture_manifest_matches_guard_rule_ids(self) -> None:
        self.assertEqual(set(RULE_FIXTURES), guard.RULE_IDS)
        missing = sorted(name for name in RULE_FIXTURES.values() if not hasattr(self, name))
        self.assertEqual(missing, [])

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _rule(self, rel: str, text: str) -> Path:
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def test_new_literal_outside_allowlist_fails(self) -> None:
        rule = self._rule("native/py_new/py_new.bzl", 'x = "$PREFIX/lib/python3.14/site-packages"\n')
        errors = guard.check([rule], {})
        self.assertEqual(len(errors), 1)
        self.assertIn("native/py_new/py_new.bzl:1", errors[0])

    def test_allowlisted_literal_passes(self) -> None:
        rule = self._rule("native/py_old/py_old.bzl", 'x = "python3.14"\n')
        self.assertEqual(guard.check([rule], {"native/py_old/py_old.bzl": 1}), [])

    def test_stale_allowlist_entry_fails(self) -> None:
        rule = self._rule("native/py_fixed/py_fixed.bzl", 'x = "python{abi}".format(abi = abi)\n')
        errors = guard.check([rule], {"native/py_fixed/py_fixed.bzl": 2})
        self.assertEqual(len(errors), 1)
        self.assertIn("remove it from the allowlist", errors[0])

    def test_unscanned_allowlist_entry_fails(self) -> None:
        rule = self._rule("native/zlib/zlib.bzl", "x = 1\n")
        errors = guard.check([rule], {"native/gone/gone.bzl": 1})
        self.assertEqual(errors, ["native/gone/gone.bzl: allowlisted but not scanned; remove it from the allowlist"])

    def test_python_provider_is_exempt(self) -> None:
        rule = self._rule("native/python/python.bzl", '"-lpython3.14"\n')
        self.assertEqual(guard.check([rule], {}), [])

    def test_other_versions_and_minor_forms_are_caught(self) -> None:
        for literal in (
            "python3.13",
            "include/python3.9",
            "bin/python3.14t",
            "_yaml.cpython-314-x86_64-linux-gnu.so",
            "bin/pip3.14",
            "numpy-2.3.5-cp313-cp313-linux_x86_64.whl",
        ):
            with self.subTest(literal=literal):
                rule = self._rule("native/py_x/py_x.bzl", f'x = "{literal}"\n')
                self.assertEqual(len(guard.check([rule], {})), 1)

    def test_python3_without_minor_is_allowed(self) -> None:
        rule = self._rule("native/py_ok/py_ok.bzl", '"$VENV/bin/python3" -m pip3 install; cp3 = 1\n')
        self.assertEqual(guard.check([rule], {}), [])

    def test_allowlist_comments_and_blanks_are_ignored(self) -> None:
        allow = self.root / "allow.txt"
        allow.write_text("# header\n\nnative/a/a.bzl 3  # reason\n", encoding="utf-8")
        self.assertEqual(guard.read_allowlist(allow), {"native/a/a.bzl": 3})

    def test_allowlist_entry_without_count_is_rejected(self) -> None:
        allow = self.root / "allow.txt"
        allow.write_text("native/a/a.bzl\n", encoding="utf-8")
        with self.assertRaises(ValueError):
            guard.read_allowlist(allow)

    def test_allowlisted_file_gaining_literals_fails(self) -> None:
        rule = self._rule("native/py_old/py_old.bzl", 'a = "python3.14"\nb = "cpython-314"\n')
        errors = guard.check([rule], {"native/py_old/py_old.bzl": 1})
        self.assertEqual(len(errors), 1)
        self.assertIn("2 literal line(s), allowlist permits 1", errors[0])

    def test_allowlisted_file_losing_literals_must_lower_count(self) -> None:
        rule = self._rule("native/py_old/py_old.bzl", 'a = "python3.14"\n')
        errors = guard.check([rule], {"native/py_old/py_old.bzl": 3})
        self.assertEqual(len(errors), 1)
        self.assertIn("lower its allowlist count to 1", errors[0])

    def test_python_sources_are_scanned(self) -> None:
        plan = self._rule("native/llvm/plan.py", 'PY = "bin/python3.14"\n')
        self.assertEqual(len(guard.check([plan], {})), 1)

    def test_non_utf8_file_does_not_crash(self) -> None:
        path = self.root / "native/x/x.bzl"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"\xff\xfe python3.14\n")
        self.assertEqual(len(guard.check([path], {})), 1)

    def test_undeclared_source_file_fails_coverage(self) -> None:
        declared = self._rule("native/zlib/zlib.bzl", "x = 1\n")
        self._rule("native/py_new/py_new.bzl", 'x = "python3.14"\n')
        self._rule("native/py_new/plan.py", "x = 1\n")
        self._rule("native/py_new/BUILD.bazel", "x = 1\n")
        errors = guard.coverage_errors([declared], self.root)
        self.assertEqual(
            errors,
            [
                "native/py_new/plan.py: not declared in hermetic_native_deps_guard_test data; the guards never scan it",
                "native/py_new/py_new.bzl: not declared in hermetic_native_deps_guard_test data; the guards never scan it",
            ],
        )

    def test_fully_declared_tree_passes_coverage(self) -> None:
        files = [self._rule("native/a/a.bzl", "x\n"), self._rule("native/a/plan.py", "x\n")]
        self.assertEqual(guard.coverage_errors(files, self.root), [])

    def test_source_root_resolves_through_symlinked_runfile(self) -> None:
        source = self.root / "src"
        (source / "tools").mkdir(parents=True)
        (source / "native").mkdir()
        anchor = source / "tools" / "python_abi_literal_allowlist.txt"
        anchor.write_text("", encoding="utf-8")
        runfiles = self.root / "runfiles" / "tools"
        runfiles.mkdir(parents=True)
        link = runfiles / "python_abi_literal_allowlist.txt"
        link.symlink_to(anchor)
        self.assertEqual(guard.source_root_from(link), source.resolve())


if __name__ == "__main__":
    unittest.main()
