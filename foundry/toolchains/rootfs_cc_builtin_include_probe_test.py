#!/usr/bin/env python3
"""Compare checked-in rootfs C++ include directories with the live insula."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path


def _load_checked_in_data(path: Path) -> dict[str, dict[str, list[str]]]:
    namespace: dict[str, object] = {}
    exec(compile(path.read_text(encoding="utf-8"), str(path), "exec"), {"__builtins__": {}}, namespace)
    return namespace["ROOTFS_CC_BUILTIN_INCLUDE_DIRECTORIES_BY_LINE"]  # type: ignore[return-value]


class RootfsCcBuiltinIncludeProbeTest(unittest.TestCase):
    def test_live_rootfs_builtin_include_directories_match_checked_in_data(self) -> None:
        self.assertEqual(
            os.environ.get("VASO_IN_INSULA"),
            "1",
            "rootfs include probing is only valid inside the selected insula",
        )
        line = os.environ.get("VASO_CUDA_LINE")
        self.assertIn(line, ("cu129", "cu130"))

        package = Path(__file__).resolve().parent
        expected = _load_checked_in_data(package / "rootfs_cc_builtin_includes.bzl")[line]
        result = subprocess.run(
            [sys.executable, str(package / "rootfs_cc_builtin_include_probe.py")],
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=120,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), expected)


if __name__ == "__main__":
    unittest.main()
