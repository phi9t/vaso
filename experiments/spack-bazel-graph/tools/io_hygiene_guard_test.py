#!/usr/bin/env python3
"""Regression tests for io_hygiene_guard.py."""

from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).with_name("io_hygiene_guard.py")
SPEC = importlib.util.spec_from_file_location("io_hygiene_guard", SCRIPT)
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
        rule_fixture("bare-mktemp", "test_bare_mktemp_fails"),
        rule_fixture("io-allowlist-ratchet", "test_allowlist_count_must_shrink"),
        rule_fixture("tempfile-default", "test_tempfile_without_dir_fails"),
        rule_fixture("tmp-literal", "test_unbounded_tmpfs_tmp_fails"),
    )
)


class IoHygieneGuardTest(unittest.TestCase):
    def test_rule_fixture_manifest_matches_guard_rule_ids(self) -> None:
        self.assertEqual(set(RULE_FIXTURES), guard.RULE_IDS)
        missing = sorted(name for name in RULE_FIXTURES.values() if not hasattr(self, name))
        self.assertEqual(missing, [])

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _file(self, rel: str, text: str) -> Path:
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def test_unbounded_tmpfs_tmp_fails(self) -> None:
        path = self._file("run.sh", "bwrap \\\n  --tmpfs /tmp \\\n  --tmpfs /run\n")
        findings = guard.scan_file(path, self.root)
        self.assertEqual([(f.kind, f.line) for f in findings], [("tmp-literal", 2)])

    def test_bounded_tmpfs_tmp_passes(self) -> None:
        path = self._file("run.sh", "bwrap \\\n  --size 4294967296 --tmpfs /tmp \\\n  --tmpfs /run\n")
        self.assertEqual(guard.scan_file(path, self.root), [])

    def test_tmp_literal_in_refusal_case_passes(self) -> None:
        path = self._file(
            "run.sh",
            'case "$output_base" in\n'
            "  /tmp/*|/var/tmp/*)\n"
            '    echo "refusing output base on shared scratch" >&2\n'
            "    exit 2\n"
            "    ;;\n"
            "esac\n",
        )
        self.assertEqual(guard.scan_file(path, self.root), [])

    def test_python_tmp_literal_in_refusal_predicate_passes(self) -> None:
        tmp_path = "/" + "tmp"
        var_tmp_path = "/" + "var/tmp"
        path = self._file(
            "tools/x.py",
            "def is_shared_tmp(path):\n"
            "    text = path.as_posix()\n"
            f'    return text == "{tmp_path}" or text.startswith("{tmp_path}/") or text == "{var_tmp_path}"\n',
        )
        self.assertEqual(guard.scan_file(path, self.root), [])

    def test_python_tmp_literal_in_refusal_fixture_passes(self) -> None:
        tmp_path = "/" + "tmp"
        path = self._file(
            "tools/x_test.py",
            "def test_required_tmpdir_falls_back_to_estate_for_shared_tmp(self):\n"
            f'    env = {{"TMPDIR": "{tmp_path}", "VASO_HOME": str(home)}}\n'
            "    with mock.patch.dict(os.environ, env, clear=True):\n"
            "        self.assertEqual(self.driver._required_tmpdir(), home / 'tmp')\n",
        )
        self.assertEqual(guard.scan_file(path, self.root), [])

    def test_python_tmp_literal_in_estate_fallback_fixture_passes(self) -> None:
        tmp_path = "/" + "tmp"
        path = self._file(
            "tools/x_test.py",
            "def test_execute_mode_uses_estate_tmp_when_host_tmpdir_is_shared(self):\n"
            f'    env = {{"VASO_HOME": str(home), "VASO_CUDA_LINE": "cu130", "TMPDIR": "{tmp_path}"}}\n'
            "    with mock.patch.dict(os.environ, env, clear=True):\n"
            "        self.assertEqual(run(), 0)\n"
            '    action_tmp = home / "lines" / "cu130" / "tmp" / "native-actions" / "jaxlib"\n'
            '    self.assertEqual(log["env"]["PIP_CACHE_DIR"], str(action_tmp / "pip-cache"))\n',
        )
        self.assertEqual(guard.scan_file(path, self.root), [])

    def test_python_tmp_literal_in_refusal_assertion_passes(self) -> None:
        tmp_prefix = "/" + "tmp/"
        path = self._file(
            "tools/x_test.py",
            "for prefix in prefixes:\n"
            f'    self.assertFalse(prefix.startswith("{tmp_prefix}"))\n',
        )
        self.assertEqual(guard.scan_file(path, self.root), [])

    def test_tmpdir_under_vaso_is_allowed(self) -> None:
        path = self._file("run.sh", 'export TMPDIR="/vaso/tmp/$run_id"\n')
        self.assertEqual(guard.scan_file(path, self.root), [])

    def test_bare_mktemp_fails(self) -> None:
        path = self._file("native/x/x.bzl", 'BUILD="$(mktemp -d)"\n')
        findings = guard.scan_file(path, self.root)
        self.assertEqual([(f.kind, f.line) for f in findings], [("bare-mktemp", 1)])

    def test_mktemp_with_tmpdir_passes(self) -> None:
        path = self._file("native/x/x.bzl", 'BUILD="$(mktemp -d -p "$TMPDIR")"\n')
        self.assertEqual(guard.scan_file(path, self.root), [])

    def test_tempfile_without_dir_fails(self) -> None:
        path = self._file("tools/x.py", "import tempfile\nwith tempfile.TemporaryDirectory() as tmp:\n    pass\n")
        findings = guard.scan_file(path, self.root)
        self.assertEqual([(f.kind, f.line) for f in findings], [("tempfile-default", 2)])

    def test_tempfile_with_dir_passes(self) -> None:
        path = self._file("tools/x.py", "import tempfile\nwith tempfile.TemporaryDirectory(dir=base) as tmp:\n    pass\n")
        self.assertEqual(guard.scan_file(path, self.root), [])

    def test_multiline_tempfile_with_dir_passes(self) -> None:
        path = self._file(
            "tools/x.py",
            "import tempfile\n"
            "with tempfile.TemporaryDirectory(\n"
            '    prefix="vaso-",\n'
            "    dir=base,\n"
            ") as tmp:\n"
            "    pass\n",
        )
        self.assertEqual(guard.scan_file(path, self.root), [])

    def test_multiline_tempfile_without_dir_fails(self) -> None:
        wrapped_call = "with tempfile." + "TemporaryDirectory(\n"
        path = self._file(
            "tools/x.py",
            "import tempfile\n"
            f"{wrapped_call}"
            '    prefix="vaso-",\n'
            ") as tmp:\n"
            "    pass\n",
        )
        findings = guard.scan_file(path, self.root)
        self.assertEqual([(f.kind, f.line) for f in findings], [("tempfile-default", 2)])

    def test_allowlist_count_must_shrink(self) -> None:
        self._file("tools/x.py", "import tempfile\nwith tempfile.TemporaryDirectory(dir=base) as tmp:\n    pass\n")
        errors = guard.check(self.root, {("tools/x.py", "tempfile-default"): 1})
        self.assertEqual(errors, ["tools/x.py: allowlisted tempfile-default but no finding remains; remove it from the allowlist"])

    def test_allowlist_count_must_not_grow(self) -> None:
        self._file(
            "tools/x.py",
            "import tempfile\nwith tempfile.TemporaryDirectory() as a:\n    pass\nwith tempfile.TemporaryDirectory() as b:\n    pass\n",
        )
        errors = guard.check(self.root, {("tools/x.py", "tempfile-default"): 1})
        self.assertEqual(len(errors), 1)
        self.assertIn("2 tempfile-default finding(s), allowlist permits 1", errors[0])

    def test_source_root_resolves_through_symlinked_runfile(self) -> None:
        source = self.root / "src"
        (source / "tools").mkdir(parents=True)
        anchor = source / "tools" / "io_hygiene_allowlist.txt"
        anchor.write_text("", encoding="utf-8")
        runfiles = self.root / "runfiles" / "tools"
        runfiles.mkdir(parents=True)
        link = runfiles / "io_hygiene_allowlist.txt"
        link.symlink_to(anchor)
        self.assertEqual(guard.source_root_from(link), source.resolve())


if __name__ == "__main__":
    unittest.main()
