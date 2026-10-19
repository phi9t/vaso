#!/usr/bin/env python3
"""Downstream smoke test for consuming vaso_graph as a Bazel module."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from nested_bazel_fixture import local_override_block, write, write_module_override_stubs


def scratch_parent() -> Path:
    estate = os.environ.get("VASO_ESTATE_ROOT")
    if estate:
        path = Path(estate) / "agents" / "fc-m1" / "tmp"
        path.mkdir(parents=True, exist_ok=True)
        return path
    test_tmpdir = os.environ.get("TEST_TMPDIR")
    if test_tmpdir:
        return Path(test_tmpdir)
    return Path.cwd()


def bazel_binary() -> Path:
    estate = os.environ.get("VASO_ESTATE_ROOT")
    candidates = [
        str(Path(estate) / "opt-vaso" / "bin" / "bazel-real") if estate else None,
        os.environ.get("BAZEL_REAL"),
        os.environ.get("BAZEL"),
    ]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return Path(candidate)
    raise FileNotFoundError("bazel-real")


class ModuleExtensionSmokeTest(unittest.TestCase):
    def test_downstream_module_queries_vaso_provider_without_native_repo_declarations(self) -> None:
        parser = argparse.ArgumentParser()
        parser.add_argument("--module-root", required=True)
        args, _ = parser.parse_known_args()
        module_root = Path(args.module_root).resolve().parent

        with tempfile.TemporaryDirectory(dir=scratch_parent()) as td:
            root = Path(td)
            write(
                root / "MODULE.bazel",
                f"""\
module(name = "vaso_graph_downstream_smoke", version = "0.0.0")

bazel_dep(name = "vaso_graph", version = "0.1.0")
local_path_override(module_name = "vaso_graph", path = "{module_root}")
{local_override_block(root)}

vaso_graph = use_extension("@vaso_graph//:extensions.bzl", "vaso_graph")
vaso_graph.profile(
    name = "torch",
    lock = "@vaso_graph//locks:torch.lock.json",
)
use_repo(
    vaso_graph,
    "vaso_zlib_ng",
    "spack_zlib_ng",
)
""",
            )
            write(root / "BUILD.bazel", "package(default_visibility = [\"//visibility:public\"])\n")
            write_module_override_stubs(root)

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
            for target, expected in (
                ("@vaso_zlib_ng//:lib", "cc_library("),
                ("@vaso_zlib_ng//:prefix_path.txt", ""),
                ("@spack_zlib_ng//:lib", "@vaso_zlib_ng//:lib"),
            ):
                result = subprocess.run(
                    common + [target, "--output=build"],
                    cwd=root,
                    check=False,
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    timeout=120,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                if expected:
                    self.assertIn(expected, result.stdout)


if __name__ == "__main__":
    unittest.main(argv=[__file__])
