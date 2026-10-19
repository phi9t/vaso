#!/usr/bin/env python3
"""Tests for committed jaxlib build-plan support helpers."""

from __future__ import annotations

import importlib.util
import tarfile
import unittest
from pathlib import Path


def load_support_module():
    module_path = Path(__file__).with_name("committed_plan_support.py")
    spec = importlib.util.spec_from_file_location("committed_plan_support", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load {module_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class CommittedPlanSupportTest(unittest.TestCase):
    def setUp(self) -> None:
        self.support = load_support_module()

    def test_tar_member_validation_allows_relative_symlink_inside_top_level(self) -> None:
        member = tarfile.TarInfo(
            f"{self.support.JAX_LOCAL_CCCL_ARCHIVE_STRIP_PREFIX}/.github/copilot-instructions.md"
        )
        member.type = tarfile.SYMTYPE
        member.linkname = "../AGENTS.md"

        self.support.validate_tar_member(member)

    def test_tar_member_validation_rejects_symlink_that_escapes_top_level(self) -> None:
        member = tarfile.TarInfo(
            f"{self.support.JAX_LOCAL_CCCL_ARCHIVE_STRIP_PREFIX}/.github/copilot-instructions.md"
        )
        member.type = tarfile.SYMTYPE
        member.linkname = "../../outside"

        with self.assertRaises(SystemExit) as raised:
            self.support.validate_tar_member(member)

        self.assertIn("archive link escapes extraction root", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
