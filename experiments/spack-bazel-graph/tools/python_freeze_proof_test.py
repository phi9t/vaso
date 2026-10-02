#!/usr/bin/env python3
"""Regression tests for python_freeze_proof.py."""

from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).with_name("python_freeze_proof.py")
SPEC = importlib.util.spec_from_file_location("python_freeze_proof", SCRIPT)
assert SPEC is not None
proof = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = proof
SPEC.loader.exec_module(proof)


class PythonFreezeProofTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.prefix = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _file(self, rel: str, text: str = "", mode: int = 0o644) -> Path:
        path = self.prefix / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        path.chmod(mode)
        return path

    def _kinds(self) -> set[str]:
        return {finding.kind for finding in proof.scan(self.prefix, needed=lambda path: [])}

    def test_valgrind_gdb_helpers_and_spack_metadata_are_allowed(self) -> None:
        self._file("libexec/valgrind/valgrind-monitor.py", "# This file is part of Valgrind\nimport gdb\n")
        self._file("libexec/valgrind/valgrind-monitor-def.py", "# This file is part of Valgrind\n")
        self._file(".spack/repos/spack_repo/builtin/packages/valgrind/package.py", "class Valgrind: pass\n")
        self._file("bin/valgrind", "\x7fELF", 0o755)
        self.assertEqual(self._kinds(), set())
        notes = proof.scan(self.prefix, needed=lambda path: [], include_notes=True)
        self.assertEqual(
            sorted(f.path for f in notes if f.kind == "note-plain-python-source"),
            ["libexec/valgrind/valgrind-monitor-def.py", "libexec/valgrind/valgrind-monitor.py"],
        )

    def test_abi_bound_artifacts_are_rejected(self) -> None:
        cases = {
            "lib/python3.14/site-packages/x/__init__.py": "python-abi-path",
            "lib/site-packages/x.py": "site-packages",
            "lib/python3/dist-packages/x.py": "site-packages",
            "include/python3.13/Python.h": "python-abi-path",
            "lib/foo.cpython-314-x86_64-linux-gnu.so": "extension-module",
            "lib/foo.abi3.so": "extension-module",
        }
        for rel, kind in cases.items():
            with self.subTest(rel=rel):
                self.tearDown(); self.setUp()
                self._file(rel)
                self.assertEqual(self._kinds(), {kind})

    def test_pinned_python_shebang_is_rejected_env_lookup_is_noted(self) -> None:
        self._file("bin/pinned_prefix", "#!/opt/native/python/bin/python3\nprint(1)\n", 0o755)
        self._file("bin/pinned_version", "#!/usr/bin/env python3.14\n", 0o755)
        self._file("bin/cg_annotate", "#! /usr/bin/env python3\n", 0o755)
        self._file("bin/shell", "#!/bin/sh\necho python3\n", 0o755)
        findings = proof.scan(self.prefix, needed=lambda path: [], include_notes=True)
        self.assertEqual(
            sorted(f.path for f in findings if f.kind == "python-shebang"),
            ["bin/pinned_prefix", "bin/pinned_version"],
        )
        self.assertEqual(
            [f.path for f in findings if f.kind == "note-env-python-script"],
            ["bin/cg_annotate"],
        )

    def test_libpython_needed_is_rejected(self) -> None:
        self._file("lib/libx.so", "\x7fELF")
        findings = proof.scan(
            self.prefix,
            needed=lambda path: ["libc.so.6", "libpython3.14.so.1.0"] if path.name == "libx.so" else [],
        )
        self.assertEqual([(f.kind, f.path) for f in findings], [("libpython-needed", "lib/libx.so")])

    def test_main_exit_codes(self) -> None:
        self._file("libexec/valgrind/valgrind-monitor.py", "# gdb helper\n")
        self.assertEqual(proof.main([str(self.prefix), "--no-readelf"]), 0)
        self._file("lib/python3.14/site-packages/x.py")
        self.assertEqual(proof.main([str(self.prefix), "--no-readelf"]), 1)
        self.assertEqual(proof.main([str(self.prefix / "missing")]), 2)

    def test_symlinks_are_checked_by_name_and_never_followed(self) -> None:
        (self.prefix / "bin").mkdir()
        (self.prefix / "bin" / "bzcmp").symlink_to("/nonexistent/insula-only/bin/bzdiff")
        (self.prefix / "lib").mkdir()
        (self.prefix / "lib" / "foo.cpython-314-x86_64-linux-gnu.so").symlink_to("/nonexistent/real.so")
        findings = proof.scan(self.prefix, needed=lambda path: ["libpython3.14.so"], include_notes=True)
        self.assertEqual(
            [(f.kind, f.path) for f in findings],
            [("extension-module", "lib/foo.cpython-314-x86_64-linux-gnu.so")],
        )

    def test_system_interpreter_pin_is_noted_not_blocked(self) -> None:
        self._file("libexec/git-core/git-p4", "#!/usr/bin/python\n", 0o755)
        self._file("bin/sys3", "#!/bin/python3\n", 0o755)
        findings = proof.scan(self.prefix, needed=lambda path: [], include_notes=True)
        self.assertEqual(
            sorted((f.kind, f.path) for f in findings),
            [("note-system-python-script", "bin/sys3"), ("note-system-python-script", "libexec/git-core/git-p4")],
        )


if __name__ == "__main__":
    unittest.main()
