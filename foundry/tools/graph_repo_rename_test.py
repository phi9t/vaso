#!/usr/bin/env python3
"""Integration fixture for graph repo naming and compatibility aliases."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from nested_bazel_fixture import local_override_block, write, write_module_override_stubs


def scratch_parent() -> str | None:
    return os.environ.get("TEST_TMPDIR") or os.environ.get("TMPDIR")


def runfile_path(path: str) -> Path:
    candidate = Path(path)
    if candidate.is_absolute() and candidate.exists():
        return candidate
    for base in (os.environ.get("RUNFILES_DIR"), Path.cwd()):
        if not base:
            continue
        resolved = Path(base) / path
        if resolved.exists():
            return resolved
    raise FileNotFoundError(path)


def bazel_binary() -> Path:
    estate = os.environ.get("VASO_ESTATE_ROOT")
    candidates = [
        str(Path(estate) / "opt-vaso" / "bin" / "bazel-real") if estate else None,
        os.environ.get("BAZEL_REAL"),
        os.environ.get("BAZEL"),
    ]
    for anchor in (Path(__file__).resolve(), Path.cwd().resolve()):
        for parent in anchor.parents:
            if estate and parent == Path(estate).resolve():
                candidates.insert(0, str(parent / "opt-vaso" / "bin" / "bazel-real"))
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return Path(candidate)
    raise FileNotFoundError("bazel-real")


def write_json(path: Path, value: object) -> None:
    write(path, json.dumps(value, indent=2, sort_keys=True) + "\n")


class GraphRepoRenameTest(unittest.TestCase):
    def test_vaso_repo_and_one_release_spack_alias_resolve_to_native_provider(self) -> None:
        parser = argparse.ArgumentParser()
        parser.add_argument("--graph-repo-bzl", required=True)
        parser.add_argument("--prefix-manifest-bzl", required=True)
        parser.add_argument("--torch-artifact-bzl", required=True)
        args, _ = parser.parse_known_args()

        with tempfile.TemporaryDirectory(dir=scratch_parent()) as td:
            root = Path(td)
            write(
                root / "MODULE.bazel",
                textwrap.dedent(
                    """\
                    module(name = "graph_repo_rename_fixture", version = "0.0.0")

                    local_repository = use_repo_rule("@bazel_tools//tools/build_defs/repo:local.bzl", "local_repository")

                    local_repository(
                        name = "zlib_ng_native",
                        path = "native/zlib_ng_native",
                    )

                    local_repository(
                        name = "zstd_native",
                        path = "native/zstd_native",
                    )

                    vaso_graph = use_extension("//tools:graph_repo.bzl", "vaso_graph")
                    vaso_graph.graph(
                        lockfile = "//locks:fixture.lock.json",
                        ensure_repos = ["vaso_zlib_ng", "vaso_zstd"],
                    )
                    use_repo(
                        vaso_graph,
                        "vaso_zlib_ng",
                        "vaso_zstd",
                        "spack_zlib_ng",
                    )
                    """
                ),
            )
            write_module_override_stubs(root)
            overrides = local_override_block(root)
            with open(root / "MODULE.bazel", "a", encoding="utf-8") as fh:
                fh.write("\n" + overrides + "\n")
            write(root / "BUILD.bazel", "package(default_visibility = [\"//visibility:public\"])\n")
            write(root / "tools" / "BUILD.bazel", "exports_files([\"graph_repo.bzl\"])\n")
            write(root / "locks" / "BUILD.bazel", "exports_files([\"fixture.lock.json\"])\n")
            write(
                root / "native" / "common" / "BUILD.bazel",
                "exports_files([\"prefix_manifest.bzl\", \"torch_artifact.bzl\"])\n",
            )
            shutil.copyfile(runfile_path(args.graph_repo_bzl), root / "tools" / "graph_repo.bzl")
            shutil.copyfile(
                runfile_path(args.prefix_manifest_bzl),
                root / "native" / "common" / "prefix_manifest.bzl",
            )
            shutil.copyfile(
                runfile_path(args.torch_artifact_bzl),
                root / "native" / "common" / "torch_artifact.bzl",
            )
            write(
                root / "native" / "zlib_ng_native" / "WORKSPACE",
                "workspace(name = \"zlib_ng_native\")\n",
            )
            write(
                root / "native" / "zlib_ng_native" / "BUILD.bazel",
                textwrap.dedent(
                    """\
                    package(default_visibility = ["//visibility:public"])

                    exports_files(["prefix_path.txt"])

                    cc_library(name = "lib")
                    """
                ),
            )
            write(root / "native" / "zlib_ng_native" / "prefix_path.txt", "/fixture/zlib-ng\n")
            write(
                root / "native" / "zstd_native" / "WORKSPACE",
                "workspace(name = \"zstd_native\")\n",
            )
            write(
                root / "native" / "zstd_native" / "BUILD.bazel",
                textwrap.dedent(
                    """\
                    package(default_visibility = ["//visibility:public"])

                    exports_files(["prefix_path.txt"])

                    cc_library(name = "lib")
                    """
                ),
            )
            write(root / "native" / "zstd_native" / "prefix_path.txt", "/fixture/zstd\n")
            write_json(
                root / "locks" / "fixture.lock.json",
                {
                    "schema_version": 1,
                    "root": "vaso_zlib_ng",
                    "packages": {
                        "vaso_zlib_ng": {
                            "build": "native",
                            "include_dirs": ["include"],
                            "link_deps": [],
                            "link_libs": [],
                            "native_prefix": "@zlib_ng_native//:lib",
                            "package": "zlib-ng",
                            "prefix": "",
                            "spack_hash": "fixturehash",
                            "version": "2.3.3",
                        },
                        "vaso_zstd": {
                            "build": "native",
                            "include_dirs": ["include"],
                            "link_deps": [],
                            "link_libs": [],
                            "native_prefix": "@zstd_native//:lib",
                            "package": "zstd",
                            "prefix": "",
                            "spack_hash": "fixturehash",
                            "version": "1.5.7",
                        }
                    },
                },
            )

            bazel = bazel_binary()
            common = [
                str(bazel),
                f"--output_base={root / 'bazel-ob'}",
                "--nohome_rc",
                "--nosystem_rc",
                "query",
                "--lockfile_mode=off",
                "--noshow_progress",
            ]

            vaso_lib = subprocess.run(
                common + ["@vaso_zlib_ng//:lib", "--output=build"],
                cwd=root,
                check=False,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            self.assertEqual(vaso_lib.returncode, 0, vaso_lib.stderr)
            self.assertIn('actual = "@zlib_ng_native//:lib"', vaso_lib.stdout)

            spack_alias = subprocess.run(
                common + ["@spack_zlib_ng//:lib", "--output=build"],
                cwd=root,
                check=False,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            self.assertEqual(spack_alias.returncode, 0, spack_alias.stderr)
            self.assertIn('actual = "@vaso_zlib_ng//:lib"', spack_alias.stdout)

            prefix = subprocess.run(
                common + ["@vaso_zlib_ng//:prefix_path.txt", "--output=build"],
                cwd=root,
                check=False,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            self.assertEqual(prefix.returncode, 0, prefix.stderr)
            self.assertIn('actual = "@zlib_ng_native//:prefix_path.txt"', prefix.stdout)

            zstd_lib = subprocess.run(
                common + ["@vaso_zstd//:lib", "--output=build"],
                cwd=root,
                check=False,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            self.assertEqual(zstd_lib.returncode, 0, zstd_lib.stderr)
            self.assertIn('actual = "@zstd_native//:lib"', zstd_lib.stdout)


if __name__ == "__main__":
    unittest.main(argv=[__file__])
