#!/usr/bin/env python3
"""Contract tests for PyTorch-specific Spack overlay deltas."""

from __future__ import annotations

import re
import unittest
from pathlib import Path


EXPERIMENT_ROOT = Path(__file__).parents[1]
OVERLAY_PACKAGES = (
    EXPERIMENT_ROOT
    / "spack_overlays"
    / "vaso"
    / "spack_repo"
    / "vaso_overlay"
    / "packages"
)
SCIKIT_BUILD_CORE_100_SHA256 = "b82a8b41dd66926b96096a61e8fc8df22214bbec437d251c0fda1bfb9d7df558"


def read_overlay(package: str) -> str:
    return (OVERLAY_PACKAGES / package / "package.py").read_text(encoding="utf-8")


class PytorchSpackOverlayTest(unittest.TestCase):
    def test_torch_214_requires_scikit_build_core_one_or_newer(self) -> None:
        text = read_overlay("py_torch")

        self.assertRegex(
            text,
            r'depends_on\(\s*"py-scikit-build-core@1:",\s*when="@2\.14\.0",\s*type="build"',
        )

    def test_scikit_build_core_overlay_declares_pytorch_minimum(self) -> None:
        text = read_overlay("py_scikit_build_core")

        self.assertIn(
            f'version("1.0.0", sha256="{SCIKIT_BUILD_CORE_100_SHA256}")',
            text,
        )

    def test_pybind11_3_uses_scikit_build_core_one_or_newer(self) -> None:
        text = read_overlay("py_pybind11")

        self.assertRegex(
            text,
            r'depends_on\(\s*"py-scikit-build-core@1:",\s*when="@3:",\s*type="build"',
        )


if __name__ == "__main__":
    unittest.main()
