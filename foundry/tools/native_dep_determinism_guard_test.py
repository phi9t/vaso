#!/usr/bin/env python3
"""Regression guards for native dependency byte determinism fixes."""

from __future__ import annotations

import re
import unittest
from pathlib import Path


GRAPH_ROOT = Path(__file__).resolve().parents[1]
FIXED_REPO_EPOCH = "315532800"


def rule_text(relative: str) -> str:
    return (GRAPH_ROOT / relative).read_text(encoding="utf-8")


def build_script(relative: str) -> str:
    text = rule_text(relative)
    match = re.search(r'_BUILD_SH\s*=\s*"""\\?\n(?P<body>.*?)"""', text, re.S)
    if not match:
        raise AssertionError(f"{relative} must define _BUILD_SH")
    return match.group("body")


class NativeDepDeterminismGuardTest(unittest.TestCase):
    def test_perl_configure_timestamp_uses_fixed_source_date_epoch(self) -> None:
        text = rule_text("native/perl/perl.bzl")
        script = build_script("native/perl/perl.bzl")

        self.assertIn(f'"SOURCE_DATE_EPOCH": "{FIXED_REPO_EPOCH}"', text)
        self.assertIn('"TZ": "UTC"', text)
        self.assertNotIn('repository_ctx.os.environ.get("SOURCE_DATE_EPOCH"', text)
        self.assertIn("chmod u+w Configure", script)
        self.assertIn("SOURCE_DATE_EPOCH", script)
        self.assertRegex(script, r"cf_time=.*SOURCE_DATE_EPOCH|SOURCE_DATE_EPOCH.*cf_time")

    def test_openssh_install_does_not_generate_host_keys(self) -> None:
        script = build_script("native/openssh/openssh.bzl")

        self.assertIn("install-nokeys", script)
        self.assertNotRegex(script, r"(?m)^\s*make\s+(?:-[^\\n]+\s+)*install\s*$")
        self.assertIn("ssh_host_", script)

    def test_cmake_elf_deps_disable_build_id_note(self) -> None:
        for relative in (
            "native/libjpeg_turbo/libjpeg_turbo.bzl",
            "native/libpng/libpng.bzl",
            "native/protobuf/protobuf.bzl",
        ):
            with self.subTest(relative=relative):
                script = build_script(relative)
                for flag in (
                    "CMAKE_EXE_LINKER_FLAGS",
                    "CMAKE_SHARED_LINKER_FLAGS",
                    "CMAKE_MODULE_LINKER_FLAGS",
                ):
                    self.assertIn(f"-D{flag}=-Wl,--build-id=none", script)


if __name__ == "__main__":
    unittest.main()
