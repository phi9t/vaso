#!/usr/bin/env python3
"""Regression tests for the bzip2 native repository rule."""

from __future__ import annotations

import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path


BZIP2_BZL = Path(__file__).with_name("bzip2.bzl")


def _build_script() -> str:
    text = BZIP2_BZL.read_text(encoding="utf-8")
    match = re.search(r'_BUILD_SH = """\\\n(?P<script>.*?)\n"""', text, re.S)
    if match is None:
        raise AssertionError("could not find _BUILD_SH in bzip2.bzl")
    return match.group("script")


def _makefile_patch_fragment() -> str:
    script = _build_script()
    start = script.index("# Spack's patch()")
    end = script.index("\n\n# Reproduce Bzip2.install():")
    return script[start:end]


class Bzip2NativeRuleTest(unittest.TestCase):
    def test_makefile_patch_accepts_absolute_compiler_path(self) -> None:
        with tempfile.TemporaryDirectory(dir=os.environ.get("TEST_TMPDIR")) as tmp:
            root = Path(tmp)
            src = root / "src"
            src.mkdir()
            for name in ("Makefile", "Makefile-libbz2_so"):
                (src / name).write_text("CC=gcc\nall:\n\t@true\n", encoding="utf-8")

            script = root / "patch.sh"
            script.write_text(
                "\n".join(
                    [
                        "#!/usr/bin/env bash",
                        "set -euo pipefail",
                        f'SRC="{src}"',
                        'cd "$SRC"',
                        _makefile_patch_fragment(),
                        "",
                    ]
                ),
                encoding="utf-8",
            )

            proc = subprocess.run(
                ["bash", str(script)],
                env={**os.environ, "CC": "/usr/bin/gcc"},
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )

            self.assertEqual(proc.returncode, 0, proc.stderr)
            for name in ("Makefile", "Makefile-libbz2_so"):
                self.assertIn("CC=/usr/bin/gcc\n", (src / name).read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
