#!/usr/bin/env python3
"""Tests for the public vaso_graph module extension profile contract."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from nested_bazel_fixture import (
    MODULE_OVERRIDE_VERSIONS, local_override_block, write_module_override,
    write_rules_cc_override as write_complete_rules_cc_override,
)


def scratch_parent() -> Path:
    test_tmpdir = os.environ.get("TEST_TMPDIR")
    if test_tmpdir:
        return Path(test_tmpdir)
    estate = os.environ.get("VASO_ESTATE_ROOT")
    if estate:
        path = Path(estate) / "agents" / "fc-m1" / "tmp"
        path.mkdir(parents=True, exist_ok=True)
        return path
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


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def write_json(path: Path, value: object) -> None:
    write(path, json.dumps(value, indent=2, sort_keys=True) + "\n")


def write_module_overrides(root: Path) -> None:
    write_complete_rules_cc_override(root / "rules_cc")
    names = sorted(set(MODULE_OVERRIDE_VERSIONS) - {"rules_cc"})
    for name in names:
        write_module_override(root / name, name)
    # `mod show_repo` evaluates built-in extension usages too. Give those
    # usages explicit empty repos; the unit fixture runs no Java/buildozer.
    for module, directory, filename, extension, repos in (
        ("rules_java", "java", "extensions.bzl", "toolchains", ("local_jdk", "remote_java_tools")),
        ("buildozer", "", "buildozer_binary.bzl", "buildozer_binary", ("buildozer_binary",)),
    ):
        path = root / module / directory
        write(path / "BUILD.bazel", "")
        calls = "\n".join(f'    _empty_repo(name = "{repo}")' for repo in repos)
        write(path / filename, f'''def _empty_impl(ctx):
    ctx.file("BUILD.bazel", "")
_empty_repo = repository_rule(implementation = _empty_impl)
def _extension_impl(ctx):
{calls}
{extension} = module_extension(implementation = _extension_impl)
''')


def lock_doc(
    *,
    root: str = "vaso_zlib_ng",
    packages: dict[str, dict[str, object]] | None = None,
) -> dict[str, object]:
    if packages is None:
        packages = {
            "vaso_python": {
                "build": "native",
                "include_dirs": ["include/python3.13"],
                "link_deps": [],
                "link_libs": ["python3.13"],
                "native_prefix": "@python_313_native//:lib",
                "package": "python",
                "prefix": "",
                "spack_hash": "pythonhash",
                "version": "3.13.13",
            },
            "vaso_zlib_ng": {
                "build": "native",
                "include_dirs": ["include"],
                "link_deps": [],
                "link_libs": ["z"],
                "native_prefix": "@zlib_ng_native//:lib",
                "package": "zlib-ng",
                "prefix": "",
                "spack_hash": "zlibhash",
                "version": "2.3.3",
            },
        }
    return {"schema_version": 1, "root": root, "packages": packages}


class ModuleExtensionUnitTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        parser = argparse.ArgumentParser()
        parser.add_argument("--module-root", required=True)
        args, _ = parser.parse_known_args()
        cls.module_root = Path(args.module_root).resolve().parent
        cls.bazel = bazel_binary()
        cls._tmp = tempfile.TemporaryDirectory(dir=scratch_parent())
        cls.fixture_root = Path(cls._tmp.name)
        write(cls.fixture_root / "BUILD.bazel", "package(default_visibility = [\"//visibility:public\"])\n")
        write(cls.fixture_root / "locks" / "BUILD.bazel", "exports_files([\"fixture.lock.json\"])\n")
        write_module_overrides(cls.fixture_root)

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    def run_fixture(
        self,
        lock: dict[str, object],
        *,
        profile_attrs: str = "",
        target: str = "@vaso_zlib_ng//:lib",
        use_repos: tuple[str, ...] = ("vaso_zlib_ng", "spack_zlib_ng"),
        inspect_repository: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        root = self.fixture_root
        use_repo_args = ", ".join(json.dumps(repo) for repo in use_repos)
        write(
            root / "MODULE.bazel",
            f"""\
module(name = "module_extension_unit_fixture", version = "0.0.0")

bazel_dep(name = "vaso_graph", version = "0.1.0")
local_path_override(module_name = "vaso_graph", path = "{self.module_root}")
{local_override_block(root)}

vaso_graph = use_extension("@vaso_graph//:extensions.bzl", "vaso_graph")
vaso_graph.profile(
    name = "torch",
    lock = "//locks:fixture.lock.json",
    {profile_attrs}
)
use_repo(vaso_graph, {use_repo_args})
""",
        )
        write_json(root / "locks" / "fixture.lock.json", lock)
        return subprocess.run(
            [
                str(self.bazel),
                f"--output_base={root / 'bazel-ob'}",
                "--nohome_rc",
                "--nosystem_rc",
                "mod" if inspect_repository else "query",
                "--lockfile_mode=off",
                "--repository_disable_download",
                "--registry=file:///nonexistent",
                "--noshow_progress",
                *(["--extension_filter=@vaso_graph//:extensions.bzl%vaso_graph", "show_repo"] if inspect_repository else []),
                target,
                *([] if inspect_repository else ["--output=build"]),
            ],
            cwd=root,
            env={**os.environ, "VASO_ESTATE_ROOT": str(root / "estate")},
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=120,
        )

    def assert_rejected(self, lock: dict[str, object], expected: str, *, profile_attrs: str = "") -> None:
        result = self.run_fixture(lock, profile_attrs=profile_attrs)

        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn(expected, result.stderr)

    def test_valid_default_profile_exports_vaso_and_compat_repos(self) -> None:
        result = self.run_fixture(lock_doc())

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("cc_library(", result.stdout)

    def test_default_profile_exports_committed_torch_artifact(self) -> None:
        result = self.run_fixture(
            lock_doc(),
            target = "@vaso_torch_artifact_cu130",
            use_repos = ("vaso_torch_artifact_cu130",),
            inspect_repository = True,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        manifest_path = self.module_root / "plans" / "torch" / "cu130" / "torch-artifact.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        pending = manifest["sha256"] == "0" * 64 or manifest["archive"].startswith("step-b-pending://")
        if pending:
            self.assertIn("torch_artifact_stub_repository(", result.stdout)
            self.assertIn("committed torch artifact pin for CUDA line cu130 is pending", result.stdout)
        else:
            # Inspect the extension's real repository arguments. Materializing
            # a committed multi-GB artifact belongs to live qualification;
            # provider smoke tests separately extract owned fixture archives.
            self.assertIn("torch_artifact_repository(", result.stdout)
            self.assertIn(manifest["sha256"], result.stdout)
            self.assertIn(manifest["archive"], result.stdout)

    def test_rejects_non_terminal_legacy_lock(self) -> None:
        self.assert_rejected(
            lock_doc(
                root="spack_zlib_ng",
                packages={
                    "spack_zlib_ng": {
                        "build": "native",
                        "include_dirs": ["include"],
                        "link_deps": [],
                        "link_libs": ["z"],
                        "native_prefix": "@zlib_ng_native//:lib",
                        "package": "zlib-ng",
                        "prefix": "",
                        "spack_hash": "zlibhash",
                        "version": "2.3.3",
                    }
                },
            ),
            "profile lock root must be a vaso_ repo",
        )

    def test_rejects_non_native_provider_node(self) -> None:
        bad = lock_doc()
        packages = bad["packages"]
        assert isinstance(packages, dict)
        packages["vaso_zlib_ng"]["build"] = "spack"

        self.assert_rejected(bad, "checked-in profile locks require build='native'")

    def test_rejects_second_cuda_family_provider(self) -> None:
        bad = lock_doc(
            root="vaso_cuda",
            packages={
                "vaso_cuda": {
                    "build": "native",
                    "include_dirs": ["include"],
                    "link_deps": [],
                    "link_libs": ["cudart"],
                    "native_prefix": "@cuda_alt_native//:lib",
                    "package": "cuda",
                    "prefix": "",
                    "spack_hash": "cudahash",
                    "version": "13.0.3",
                }
            },
        )

        self.assert_rejected(bad, "CUDA-family package cuda must use provider @cuda_native//:lib")

    def test_rejects_compiler_outside_selected_profile(self) -> None:
        self.assert_rejected(lock_doc(), "profile torch compiler must be gcc-13.3", profile_attrs='compiler = "clang-23",')

    def test_rejects_python_outside_declared_profile_input(self) -> None:
        self.assert_rejected(lock_doc(), "profile torch python must be 3.13.13", profile_attrs='python = "3.11.9",')


if __name__ == "__main__":
    unittest.main(argv=[__file__])
