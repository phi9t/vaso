#!/usr/bin/env python3
"""Regression tests for native_source_versions.py."""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).with_name("native_source_versions.py")
SPEC = importlib.util.spec_from_file_location("native_source_versions", SCRIPT)
assert SPEC is not None
checker = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = checker
SPEC.loader.exec_module(checker)

ZSTD_BLOCK = """
zstd_native(
    name = "zstd_native",
    urls = ["https://github.com/facebook/zstd/archive/v1.5.7.tar.gz"],
    sha256 = "0000",
    strip_prefix = "zstd-1.5.7",
)
"""
SQLITE_BLOCK = """
sqlite_native(
    name = "sqlite_native",
    urls = ["https://www.sqlite.org/2026/sqlite-autoconf-3530100.tar.gz"],
    sha256 = "0000",
    strip_prefix = "sqlite-autoconf-3530100",
)
"""
FP16_COMMIT = "4dfe081cf6bcd15db339cf2680b9281b8451eeb3"
FP16_BLOCK = f"""
fp16_native(
    name = "fp16_native",
    urls = ["https://github.com/Maratyszcza/FP16/archive/{FP16_COMMIT}.tar.gz"],
    sha256 = "0000",
    strip_prefix = "FP16-{FP16_COMMIT}",
)
"""
VERSION_ATTR_BLOCK = """
thing_native(
    name = "thing_native",
    thing_version = "1.2.3",
)
"""
ROOTFS_BOUNDARY_BLOCK = """
cuda_native(
    name = "cuda_native",
    coreutils_prefix_file = "@coreutils_native//:prefix_path.txt",
)
"""
ROOTFS_LLVM_BOUNDARY_BLOCK = """
rootfs_llvm_23_native(
    name = "rootfs_llvm_23_native",
)
"""
SPELLINGS = {"sqlite": ("3.53.1", "3530100", "autoconf tarball encoding")}


class NativeSourceVersionsTest(unittest.TestCase):
    def check(
        self,
        native: dict[str, str],
        provided: dict[str, str],
        module: str,
        docs: dict[str, str] | None = None,
        native_files: dict[str, str] | None = None,
        rootfs_boundary_packages: set[str] | None = None,
    ) -> list[str]:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "native_overrides.json").write_text(
                json.dumps({"native": native, "provided_versions": provided}), encoding="utf-8"
            )
            (root / "MODULE.bazel").write_text(module, encoding="utf-8")
            (root / "docs" / "recipes").mkdir(parents=True)
            for package, text in (docs or {}).items():
                (root / "docs" / "recipes" / f"{package}.md").write_text(text, encoding="utf-8")
            for package, text in (native_files or {}).items():
                path = root / "native" / package / f"{package}.bzl"
                path.parent.mkdir(parents=True)
                path.write_text(text, encoding="utf-8")
            return checker.source_version_errors(
                root,
                spellings={key: value for key, value in SPELLINGS.items() if key in native},
                commit_pinned={"fp16"} & set(native),
                doc_only={"python-venv": "generic package"} if "python-venv" in native else {},
                rootfs_boundaries=rootfs_boundary_packages if rootfs_boundary_packages is not None else set(native_files or {}),
            )

    def test_strip_prefix_and_url_witness_provided_version(self) -> None:
        self.assertEqual(self.check({"zstd": "@zstd_native//:lib"}, {"zstd": "1.5.7"}, ZSTD_BLOCK), [])

    def test_module_source_bump_without_provided_version_fails(self) -> None:
        bumped = ZSTD_BLOCK.replace("1.5.7", "1.5.8")

        errors = self.check({"zstd": "@zstd_native//:lib"}, {"zstd": "1.5.7"}, bumped)

        self.assertEqual(errors, ["zstd: provided_versions says 1.5.7 but MODULE.bazel zstd_native sources name 1.5.8"])

    def test_version_attribute_witnesses_provided_version(self) -> None:
        native = {"thing": "@thing_native//:lib"}
        self.assertEqual(self.check(native, {"thing": "1.2.3"}, VERSION_ATTR_BLOCK), [])
        self.assertEqual(len(self.check(native, {"thing": "1.2.4"}, VERSION_ATTR_BLOCK)), 1)

    def test_rootfs_boundary_provider_does_not_need_source_version(self) -> None:
        native = {"cuda": "@cuda_native//:lib"}
        rule_text = '"""CUDA rootfs sdk-boundary."""\n'
        self.assertEqual(
            self.check(native, {"cuda": "12.9.1"}, ROOTFS_BOUNDARY_BLOCK, native_files={"cuda": rule_text}),
            [],
        )
        self.assertEqual(
            self.check(native, {"cuda": "13.0.3"}, ROOTFS_BOUNDARY_BLOCK, native_files={"cuda": rule_text}),
            [],
        )

    def test_rootfs_boundary_provider_may_have_repo_name_different_from_package(self) -> None:
        native = {
            "llvm@23.0.0": "@rootfs_llvm_23_native//:lib",
        }
        provided = {
            "llvm@23.0.0": "23.0.0",
        }
        rule_text = '"""LLVM rootfs sdk-boundary."""\n'

        self.assertEqual(
            self.check(
                native,
                provided,
                ROOTFS_LLVM_BOUNDARY_BLOCK,
                native_files={"rootfs_llvm_23": rule_text},
                rootfs_boundary_packages={"llvm"},
            ),
            [],
        )

    def test_rootfs_boundary_provider_must_declare_boundary(self) -> None:
        errors = self.check(
            {"cuda": "@cuda_native//:lib"},
            {"cuda": "12.9.1"},
            ROOTFS_BOUNDARY_BLOCK,
            native_files={"cuda": "cuda_native = repository_rule()\n"},
        )

        self.assertEqual(
            errors,
            [
                "cuda: native/cuda/cuda.bzl must declare an sdk-boundary "
                "instead of a source/archive provider"
            ],
        )

    def test_spelling_map_requires_upstream_spelling_in_block(self) -> None:
        native = {"sqlite": "@sqlite_native//:lib"}
        self.assertEqual(self.check(native, {"sqlite": "3.53.1"}, SQLITE_BLOCK), [])

        errors = self.check(native, {"sqlite": "3.53.1"}, SQLITE_BLOCK.replace("3530100", "3540000"))

        self.assertEqual(len(errors), 1)
        self.assertIn("no longer fetches '3530100'", errors[0])

    def test_spelling_map_must_move_with_provided_version(self) -> None:
        errors = self.check({"sqlite": "@sqlite_native//:lib"}, {"sqlite": "3.53.2"}, SQLITE_BLOCK)

        self.assertEqual(len(errors), 1)
        self.assertIn("SOURCE_SPELLINGS maps 3.53.1", errors[0])

    def test_commit_pinned_package_needs_doc_to_name_commit_and_version(self) -> None:
        native = {"fp16": "@fp16_native//:lib"}
        doc = f"`fp16@2020-05-14` source commit {FP16_COMMIT}\n"
        self.assertEqual(self.check(native, {"fp16": "2020-05-14"}, FP16_BLOCK, {"fp16": doc}), [])

        moved = FP16_BLOCK.replace(FP16_COMMIT, "f" * 40)
        errors = self.check(native, {"fp16": "2020-05-14"}, moved, {"fp16": doc})
        self.assertEqual(len(errors), 1)
        self.assertIn("does not name the commit", errors[0])

        errors = self.check(native, {"fp16": "2020-06-01"}, FP16_BLOCK, {"fp16": doc})
        self.assertEqual(errors, [f"fp16: docs/recipes/fp16.md names pinned commit {FP16_COMMIT} but not fp16@2020-06-01"])

    def test_doc_only_package_needs_recipe_doc_version(self) -> None:
        native = {"python-venv": "@python_venv_native//:lib"}
        self.assertEqual(self.check(native, {"python-venv": "1.0"}, "", {"python-venv": "`python-venv@1.0`\n"}), [])
        self.assertEqual(len(self.check(native, {"python-venv": "1.1"}, "", {"python-venv": "`python-venv@1.0`\n"})), 1)

    def test_missing_repository_block_fails(self) -> None:
        errors = self.check({"zstd": "@zstd_native//:lib"}, {"zstd": "1.5.7"}, SQLITE_BLOCK)

        self.assertEqual(errors, ["zstd: MODULE.bazel has no repository block named 'zstd_native' for @zstd_native//:lib"])

    def test_exception_table_entries_need_native_overrides(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "native_overrides.json").write_text(json.dumps({"native": {}, "provided_versions": {}}))
            (root / "MODULE.bazel").write_text("")

            errors = checker.source_version_errors(root, spellings=SPELLINGS, commit_pinned={"fp16"}, doc_only={})

        self.assertEqual(
            errors,
            [
                "SOURCE_SPELLINGS entry 'sqlite' has no native override",
                "source-version exception 'fp16' has no native override",
            ],
        )


if __name__ == "__main__":
    unittest.main()
