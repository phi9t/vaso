#!/usr/bin/env python3
"""Tests for jaxlib_deps_import_check.py."""

from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path


SCRIPT = Path(__file__).with_name("jaxlib_deps_import_check.py")
SPEC = importlib.util.spec_from_file_location("jaxlib_deps_import_check", SCRIPT)
assert SPEC is not None
assert SPEC.loader is not None
assert SCRIPT.exists(), "jaxlib-deps import checker is missing"
checker = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = checker
SPEC.loader.exec_module(checker)


class JaxlibDepsImportCheckTest(unittest.TestCase):
    def test_default_modules_exclude_jax_and_jaxlib(self) -> None:
        self.assertNotIn("jax", checker.DEFAULT_MODULES)
        self.assertNotIn("jaxlib", checker.DEFAULT_MODULES)
        self.assertIn("absl", checker.DEFAULT_MODULES)
        self.assertIn("numpy", checker.DEFAULT_MODULES)
        self.assertIn("ml_dtypes", checker.DEFAULT_MODULES)

    def test_reports_failed_import_without_stopping_other_modules(self) -> None:
        attempted: list[str] = []

        def importer(module: str) -> object:
            attempted.append(module)
            if module == "missing_dep":
                raise ModuleNotFoundError(module)
            return object()

        result = checker.check_imports(["json", "missing_dep", "math"], importer=importer)

        self.assertEqual(attempted, ["json", "missing_dep", "math"])
        self.assertEqual(result["imported"], ["json", "math"])
        self.assertEqual(result["failed"], [{"module": "missing_dep", "error": "ModuleNotFoundError: missing_dep"}])
        self.assertEqual(result["verdict"], "failed")


if __name__ == "__main__":
    unittest.main()
