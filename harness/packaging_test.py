#!/usr/bin/env python3
"""The vaso wheel carries runtime code only: colocated tests stay out of it."""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def _can_build_wheels() -> bool:
    try:
        import setuptools  # noqa: F401
        import wheel  # noqa: F401
    except ImportError:
        return False
    return True


def build_wheel_names(source: Path, out: Path) -> list[str]:
    proc = subprocess.run(
        [sys.executable, "-m", "pip", "wheel", "--no-deps", "--no-build-isolation", "-q", "-w", str(out), str(source)],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise AssertionError(f"wheel build failed:\n{proc.stdout}\n{proc.stderr}")
    (wheel,) = out.glob("vaso-*.whl")
    return zipfile.ZipFile(wheel).namelist()


@unittest.skipUnless(_can_build_wheels(), "setuptools and wheel are needed to build the wheel offline")
class WheelContentsTest(unittest.TestCase):
    def test_wheel_excludes_colocated_tests_and_conftest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "src"
            source.mkdir()
            shutil.copy(REPO / "pyproject.toml", source / "pyproject.toml")
            shutil.copy(REPO / "setup.py", source / "setup.py")
            shutil.copytree(
                REPO / "insula" / "execution" / "vaso",
                source / "insula" / "execution" / "vaso",
                ignore=shutil.ignore_patterns("__pycache__"),
            )
            package = source / "insula" / "execution" / "vaso"
            (package / "planted_test.py").write_text("def test_x():\n    pass\n", encoding="utf-8")
            (package / "conftest.py").write_text("", encoding="utf-8")
            (package / "test_planted.py").write_text("def test_y():\n    pass\n", encoding="utf-8")

            names = build_wheel_names(source, Path(tmp) / "out")

            self.assertIn("vaso/cli.py", names)
            self.assertNotIn("vaso/planted_test.py", names)
            self.assertNotIn("vaso/conftest.py", names)
            self.assertNotIn("vaso/test_planted.py", names)
            self.assertFalse([n for n in names if n.endswith("_test.py")], names)


if __name__ == "__main__":
    unittest.main()
