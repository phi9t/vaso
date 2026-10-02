#!/usr/bin/env python3
"""Tests for compiler_provenance.py."""

from __future__ import annotations

import importlib.util
import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).with_name("compiler_provenance.py")
SPEC = importlib.util.spec_from_file_location("compiler_provenance", SCRIPT)
assert SPEC is not None
provenance = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = provenance
SPEC.loader.exec_module(provenance)


def scratch_parent() -> str | None:
    return os.environ.get("TEST_TMPDIR") or os.environ.get("TMPDIR") or os.environ.get("VASO_AGENT_IO_ROOT") or str(Path.cwd())


def policy() -> dict[str, object]:
    return {
        "schema_version": 1,
        "consumers": {
            "torch": {
                "artifact_comment": {
                    "required_substrings": ["GCC: (Ubuntu 13.3.0"],
                    "allowed_regexes": [r"GCC: \(Ubuntu 13\.3\.0"],
                    "forbidden_regexes": [r"clang version", r"GCC: \((?!Ubuntu 13\.3\.0)"],
                },
            },
            "triton": {
                "artifact_comment": {
                    "required_substrings": ["clang version 23.0.0git", "35901313"],
                    "allowed_regexes": [r"clang version 23\.0\.0git.*35901313"],
                    "forbidden_regexes": [r"GCC: ", r"clang version (?!23\.0\.0git.*35901313)"],
                },
            },
            "jaxlib": {
                "artifact_comment": {
                    "required_substrings": ["clang version 23.0.0git", "35901313"],
                    "ignored_regexes": [r"GCC: \(Ubuntu 13\.3\.0-6ubuntu2~24\.04\.1\) 13\.3\.0"],
                    "allowed_regexes": [
                        r"clang version 23\.0\.0git.*35901313",
                        r"Linker: LLD 23\.0\.0.*35901313",
                    ],
                    "forbidden_regexes": [r"clang version (?!23\.0\.0git.*35901313)"],
                },
            },
        },
        "vendored_prebuilt_allowlist": {
            "basename_prefixes": ["libcudart.so", "libcublas.so", "libcudnn.so", "libnccl.so"],
        },
    }


class CompilerProvenanceTest(unittest.TestCase):
    def test_torch_accepts_gcc_133_comment(self) -> None:
        with tempfile.TemporaryDirectory(dir=scratch_parent()) as tmp:
            prefix = Path(tmp)
            so = prefix / "lib" / "libtorch_python.so"
            so.parent.mkdir()
            so.write_bytes(b"not a real elf")
            reader = lambda path: ["GCC: (Ubuntu 13.3.0-6ubuntu2~24.04) 13.3.0"]

            result = provenance.check_prefix(prefix, "torch", policy(), comment_reader=reader)

        self.assertEqual(result.errors, [])
        self.assertEqual(result.checked, 1)

    def test_torch_accepts_cuda_vendor_static_runtime_comment_with_gcc_133(self) -> None:
        with tempfile.TemporaryDirectory(dir=scratch_parent()) as tmp:
            prefix = Path(tmp)
            so = prefix / "lib" / "libtorch_nvshmem.so"
            so.parent.mkdir()
            so.write_bytes(b"not a real elf")
            test_policy = policy()
            test_policy["consumers"]["torch"]["artifact_comment"]["ignored_comments"] = [
                {
                    "path_regex": r"(^|/)libtorch_nvshmem\.so$",
                    "comment_regex": r"GCC: \(GNU\) 8\.5\.0 .* \(Red Hat 8\.5\.0-[0-9]+\)",
                }
            ]
            reader = lambda path: [
                "GCC: (Ubuntu 13.3.0-6ubuntu2~24.04.1) 13.3.0",
                "GCC: (GNU) 8.5.0 20210514 (Red Hat 8.5.0-21)",
            ]

            result = provenance.check_prefix(prefix, "torch", test_policy, comment_reader=reader)

        self.assertEqual(result.errors, [])
        self.assertEqual(result.checked, 1)

    def test_torch_rejects_cuda_vendor_comment_outside_scoped_path(self) -> None:
        with tempfile.TemporaryDirectory(dir=scratch_parent()) as tmp:
            prefix = Path(tmp)
            so = prefix / "lib" / "libtorch_cpu.so"
            so.parent.mkdir()
            so.write_bytes(b"not a real elf")
            test_policy = policy()
            test_policy["consumers"]["torch"]["artifact_comment"]["ignored_comments"] = [
                {
                    "path_regex": r"(^|/)libtorch_nvshmem\.so$",
                    "comment_regex": r"GCC: \(GNU\) 8\.5\.0 .* \(Red Hat 8\.5\.0-[0-9]+\)",
                }
            ]
            reader = lambda path: [
                "GCC: (Ubuntu 13.3.0-6ubuntu2~24.04.1) 13.3.0",
                "GCC: (GNU) 8.5.0 20210514 (Red Hat 8.5.0-21)",
            ]

            result = provenance.check_prefix(prefix, "torch", test_policy, comment_reader=reader)

        self.assertIn(
            f"{so}: forbidden .comment producer matched 'GCC: \\\\((?!Ubuntu 13\\\\.3\\\\.0)'",
            result.errors,
        )

    def test_triton_rejects_foreign_compiler_comment(self) -> None:
        with tempfile.TemporaryDirectory(dir=scratch_parent()) as tmp:
            prefix = Path(tmp)
            so = prefix / "lib" / "libtriton.so"
            so.parent.mkdir()
            so.write_bytes(b"not a real elf")
            reader = lambda path: [
                "clang version 23.0.0git (https://github.com/llvm/llvm-project.git 35901313800ea6e6cbeb9226e51c7c4b29bfc40e)",
                "GCC: (Ubuntu 13.3.0-6ubuntu2~24.04) 13.3.0",
            ]

            result = provenance.check_prefix(prefix, "triton", policy(), comment_reader=reader)

        self.assertIn(
            f"{so}: forbidden .comment producer matched 'GCC: '",
            result.errors,
        )

    def test_jaxlib_accepts_rootfs_gcc_startup_comment_when_clang_producer_is_present(self) -> None:
        with tempfile.TemporaryDirectory(dir=scratch_parent()) as tmp:
            prefix = Path(tmp)
            so = prefix / "lib" / "jaxlib" / "_jax.so"
            so.parent.mkdir(parents=True)
            so.write_bytes(b"not a real elf")
            reader = lambda path: [
                "GCC: (Ubuntu 13.3.0-6ubuntu2~24.04.1) 13.3.0",
                "clang version 23.0.0git (https://github.com/llvm/llvm-project 35901313800ea6e6cbeb9226e51c7c4b29bfc40e)",
            ]

            result = provenance.check_prefix(prefix, "jaxlib", policy(), comment_reader=reader)

        self.assertEqual(result.errors, [])

    def test_jaxlib_accepts_lld23_linker_comment_with_required_clang_producer(self) -> None:
        with tempfile.TemporaryDirectory(dir=scratch_parent()) as tmp:
            prefix = Path(tmp)
            so = prefix / "lib" / "jaxlib" / "_jax.so"
            so.parent.mkdir(parents=True)
            so.write_bytes(b"not a real elf")
            reader = lambda path: [
                "GCC: (Ubuntu 13.3.0-6ubuntu2~24.04.1) 13.3.0",
                "Linker: LLD 23.0.0 (https://github.com/llvm/llvm-project 35901313800ea6e6cbeb9226e51c7c4b29bfc40e)",
                "clang version 23.0.0git (https://github.com/llvm/llvm-project 35901313800ea6e6cbeb9226e51c7c4b29bfc40e)",
            ]

            result = provenance.check_prefix(prefix, "jaxlib", policy(), comment_reader=reader)

        self.assertEqual(result.errors, [])

    def test_jaxlib_still_rejects_gcc_only_comment(self) -> None:
        with tempfile.TemporaryDirectory(dir=scratch_parent()) as tmp:
            prefix = Path(tmp)
            so = prefix / "lib" / "jaxlib" / "_jax.so"
            so.parent.mkdir(parents=True)
            so.write_bytes(b"not a real elf")
            reader = lambda path: ["GCC: (Ubuntu 13.3.0-6ubuntu2~24.04.1) 13.3.0"]

            result = provenance.check_prefix(prefix, "jaxlib", policy(), comment_reader=reader)

        self.assertIn(
            f"{so}: missing required .comment producer substring 'clang version 23.0.0git'",
            result.errors,
        )

    def test_skips_allowlisted_nvidia_prebuilts(self) -> None:
        with tempfile.TemporaryDirectory(dir=scratch_parent()) as tmp:
            prefix = Path(tmp)
            so = prefix / "lib" / "libcudart.so.13"
            so.parent.mkdir()
            so.write_bytes(b"not a real elf")
            reader = lambda path: ["GCC: (NVIDIA CUDA) 11.2.0"]

            result = provenance.check_prefix(prefix, "triton", policy(), comment_reader=reader)

        self.assertEqual(result.errors, [])
        self.assertEqual(result.checked, 0)
        self.assertEqual(result.skipped, 1)

    def test_parse_readelf_string_dump(self) -> None:
        text = """
String dump of section '.comment':
  [     0]  GCC: (Ubuntu 13.3.0-6ubuntu2~24.04) 13.3.0
  [    2d]  clang version 23.0.0git (https://github.com/llvm/llvm-project.git 35901313800ea6e6cbeb9226e51c7c4b29bfc40e)
"""

        self.assertEqual(
            provenance.parse_readelf_comment(text),
            [
                "GCC: (Ubuntu 13.3.0-6ubuntu2~24.04) 13.3.0",
                "clang version 23.0.0git (https://github.com/llvm/llvm-project.git 35901313800ea6e6cbeb9226e51c7c4b29bfc40e)",
            ],
        )

    def test_parse_objdump_hex_dump(self) -> None:
        text = """
Contents of section .comment:
 0000 4743433a 20285562 756e7475 2031332e  GCC: (Ubuntu 13.
 0010 332e3029 2031332e 332e3000 636c616e  3.0) 13.3.0.clan
 0020 67207665 7273696f 6e203233 2e302e30  g version 23.0.0
 0030 67697420 33353930 31333133 00        git 35901313.
"""

        self.assertEqual(
            provenance.parse_objdump_comment(text),
            [
                "GCC: (Ubuntu 13.3.0) 13.3.0",
                "clang version 23.0.0git 35901313",
            ],
        )

    def test_cli_fails_on_mismatch_with_synthetic_reader_json(self) -> None:
        with tempfile.TemporaryDirectory(dir=scratch_parent()) as tmp:
            root = Path(tmp)
            policy_path = root / "compiler_pathways.json"
            prefix = root / "prefix"
            so = prefix / "lib" / "libtriton.so"
            comments = root / "comments.json"
            policy_path.write_text(json.dumps(policy()), encoding="utf-8")
            so.parent.mkdir(parents=True)
            so.write_bytes(b"not a real elf")
            comments.write_text(
                json.dumps({str(so): ["clang version 22.0.0git deadbeef"]}),
                encoding="utf-8",
            )

            with contextlib.redirect_stderr(io.StringIO()):
                rc = provenance.main(
                    [
                        str(prefix),
                        "--consumer",
                        "triton",
                        "--policy",
                        str(policy_path),
                        "--comments-json",
                        str(comments),
                    ]
                )

        self.assertEqual(rc, 1)


if __name__ == "__main__":
    unittest.main()
