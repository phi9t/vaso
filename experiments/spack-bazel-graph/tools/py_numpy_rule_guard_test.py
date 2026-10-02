#!/usr/bin/env python3
"""Regression guards for the native py-numpy repository rule."""

from __future__ import annotations

import re
import unittest
from pathlib import Path


PY_NUMPY_BZL = Path(__file__).resolve().parents[1] / "native/py_numpy/py_numpy.bzl"


def build_script() -> str:
    text = PY_NUMPY_BZL.read_text(encoding="utf-8")
    match = re.search(r'_BUILD_SH\s*=\s*"""\\?\n(?P<body>.*?)"""', text, re.S)
    if not match:
        raise AssertionError("native/py_numpy/py_numpy.bzl must define _BUILD_SH")
    return match.group("body")


class PyNumpyRuleGuardTest(unittest.TestCase):
    def test_import_smoke_runs_outside_source_tree(self) -> None:
        script = build_script()

        site_packages = script.index('site_packages="$PREFIX/lib/python${PYTHON_ABI}/site-packages"')
        source_cd = script.index('cd "$SRC"')
        smoke_pythonpath = script.index('export PYTHONPATH="$site_packages:$PYTHONPATH"')
        import_numpy = script.index("import numpy as np")

        self.assertLess(source_cd, site_packages)
        self.assertLess(site_packages, smoke_pythonpath)
        self.assertLess(smoke_pythonpath, import_numpy)
        self.assertIn('SMOKE_CWD="$PREFIX/.smoke-cwd"', script[smoke_pythonpath:import_numpy])
        self.assertIn('mkdir -p "$SMOKE_CWD"', script[smoke_pythonpath:import_numpy])
        self.assertIn('cd "$SMOKE_CWD"', script[smoke_pythonpath:import_numpy])

    def test_spack_target_flags_match_reference_architecture(self) -> None:
        script = build_script()

        self.assertIn('spack_target_flags="-march=icelake-client -mtune=icelake-client"', script)
        self.assertIn('exec /usr/bin/gcc $spack_target_flags "\\\\$@"', script)
        self.assertIn('exec /usr/bin/g++ $spack_target_flags "\\\\$@"', script)
        self.assertIn('export CC="$wrapper_dir/gcc"', script)
        self.assertIn('export CXX="$wrapper_dir/g++"', script)
        self.assertRegex(script, r'export CFLAGS="-I\$OPENBLAS_PREFIX/include -I\$python_include .*\$\{CFLAGS:-\}"')
        self.assertRegex(script, r'export CXXFLAGS="-I\$OPENBLAS_PREFIX/include -I\$python_include .*\$\{CXXFLAGS:-\}"')
        self.assertNotRegex(script, r'export CFLAGS="\$spack_target_flags')
        self.assertNotRegex(script, r'export CXXFLAGS="\$spack_target_flags')


if __name__ == "__main__":
    unittest.main()
