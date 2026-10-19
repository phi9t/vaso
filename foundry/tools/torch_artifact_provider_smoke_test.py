#!/usr/bin/env python3
"""Downstream smoke tests for the public torch artifact provider."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import textwrap
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from nested_bazel_fixture import local_override_block, write, write_module_override_stubs


FIXTURE_DIR = Path(__file__).resolve().parent / "testdata" / "torch_artifact_provider"


def scratch_parent() -> Path:
    test_tmpdir = os.environ.get("TEST_TMPDIR")
    if test_tmpdir:
        return Path(test_tmpdir)
    estate = os.environ.get("VASO_ESTATE_ROOT")
    if estate:
        path = Path(estate) / "agents" / os.environ.get("VASO_AGENT", "fc-m5") / "tmp"
        path.mkdir(parents=True, exist_ok=True)
        return path
    return Path.cwd()


def bazel_binary() -> Path:
    estate = os.environ.get("VASO_ESTATE_ROOT")
    candidates = [
        os.environ.get("BAZEL_BIN"),
        str(Path(estate) / "opt-vaso" / "bin" / "bazel-real") if estate else None,
        os.environ.get("BAZEL_REAL"),
        os.environ.get("BAZEL"),
        shutil.which("bazel-real"),
        shutil.which("bazel"),
    ]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return Path(candidate)
    raise FileNotFoundError("bazel-real")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def add_tar_file(tar: tarfile.TarFile, root: Path, path: Path) -> None:
    rel = path.relative_to(root).as_posix()
    info = tar.gettarinfo(str(path), arcname=rel)
    info.uid = 0
    info.gid = 0
    info.uname = ""
    info.gname = ""
    info.mtime = 0
    with path.open("rb") as handle:
        tar.addfile(info, handle)


def write_fixture_torch_archive(root: Path) -> tuple[Path, str]:
    prefix = FIXTURE_DIR / "prefix"
    archive = root / "torch-prefix.tar"
    archive.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive, "w") as tar:
        for path in sorted(prefix.rglob("*")):
            if path.is_file():
                add_tar_file(tar, prefix, path)
    return archive, sha256(archive)


def write_real_layout_torch_archive(root: Path) -> tuple[Path, str]:
    prefix = root / "real-prefix"
    torch_root = prefix / "lib" / "python3.13" / "site-packages" / "torch"
    files = {
        torch_root / "__init__.py": "__version__ = '2.14.0'\n",
        torch_root / "include" / "torch" / "extension.h": "#pragma once\n",
        torch_root / "include" / "torch" / "csrc" / "api" / "include" / "torch" / "torch.h": "#pragma once\n",
        torch_root / "lib" / "libc10.so": "c10\n",
        torch_root / "lib" / "libtorch.so": "torch\n",
        torch_root / "lib" / "libtorch_cpu.so": "torch_cpu\n",
        torch_root / "lib" / "libtorch_python.so": "torch_python\n",
    }
    for path, content in files.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    archive = root / "real-layout-torch-prefix.tar"
    with tarfile.open(archive, "w") as tar:
        for path in sorted(prefix.rglob("*")):
            if path.is_file():
                add_tar_file(tar, prefix, path)
    return archive, sha256(archive)


class TorchArtifactProviderSmokeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        parser = argparse.ArgumentParser()
        parser.add_argument("--module-root", required=True)
        args, _ = parser.parse_known_args()
        cls.module_root = Path(args.module_root).resolve().parent
        cls.bazel = bazel_binary()
        cls._tmp = tempfile.TemporaryDirectory(dir=scratch_parent())
        cls.root = Path(cls._tmp.name)
        cls.output_base = cls.root / "bazel-ob"
        cls.repository_cache = cls.root / "repository-cache"
        cls.distdir = cls.root / "distdir"
        cls.repository_cache.mkdir()
        cls.distdir.mkdir()
        cls.module_overrides = cls.root / "module-overrides"
        write_module_override_stubs(cls.module_overrides)
        cls._patch_rules_cc(cls.module_overrides / "rules_cc")
        cls.module_override_args = [
            f"--override_module={match.group(1)}={match.group(2)}"
            for match in re.finditer(
                r'module_name = "([^"]+)", path = "([^"]+)"',
                local_override_block(cls.module_overrides),
            )
        ]
        cls.prefix_repos = cls.root / "prefix-repos"

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    @classmethod
    def _patch_rules_cc(cls, root: Path) -> None:
        write(
            root / "cc" / "defs.bzl",
            textwrap.dedent(
                """\
                CcInfo = provider()

                def _cc_library_impl(ctx):
                    return [DefaultInfo(files = depset(ctx.files.srcs + ctx.files.hdrs))]

                def _cc_toolchain_impl(_ctx):
                    return [platform_common.ToolchainInfo()]

                cc_library = rule(
                    implementation = _cc_library_impl,
                    attrs = {
                        "deps": attr.label_list(),
                        "hdrs": attr.label_list(allow_files = True),
                        "includes": attr.string_list(),
                        "linkopts": attr.string_list(),
                        "srcs": attr.label_list(allow_files = True),
                    },
                )

                cc_binary = rule(
                    implementation = _cc_library_impl,
                    attrs = {
                        "deps": attr.label_list(),
                        "hdrs": attr.label_list(allow_files = True),
                        "srcs": attr.label_list(allow_files = True),
                    },
                )

                cc_toolchain = rule(
                    implementation = _cc_toolchain_impl,
                    attrs = {
                        "all_files": attr.label(),
                        "ar_files": attr.label(),
                        "compiler_files": attr.label(),
                        "dwp_files": attr.label(),
                        "linker_files": attr.label(),
                        "objcopy_files": attr.label(),
                        "strip_files": attr.label(),
                        "supports_param_files": attr.bool(),
                        "toolchain_config": attr.label(),
                        "toolchain_identifier": attr.string(),
                    },
                )
                """
            ),
        )
        write(
            root / "cc" / "find_cc_toolchain.bzl",
            textwrap.dedent(
                """\
                CC_TOOLCHAIN_ATTRS = {}

                def find_cpp_toolchain(_ctx):
                    return struct(
                        all_files = depset(),
                        toolchain_id = "vaso-rootfs-gcc-13.3",
                    )

                def use_cc_toolchain():
                    return ["@bazel_tools//tools/cpp:toolchain_type"]
                """
            ),
        )

    @classmethod
    def _write_prefix_repo(cls, root: Path, repo: str) -> None:
        write(root / "MODULE.bazel", f'module(name = "{repo}")\n')
        write(
            root / "BUILD.bazel",
            textwrap.dedent(
                """\
                load("@rules_cc//cc:defs.bzl", "cc_library")

                package(default_visibility = ["//visibility:public"])

                exports_files(["prefix_manifest.json", "prefix_path.txt"])

                filegroup(name = "prefix", srcs = glob(["prefix/**"], allow_empty = True))

                cc_library(
                    name = "lib",
                    hdrs = glob(["prefix/include/**"], allow_empty = True),
                    includes = ["prefix/include"],
                    linkopts = ["-Lprefix/lib64", "-Wl,-rpath,prefix/lib64"],
                )
                """
            ),
        )
        write(root / "prefix_path.txt", "prefix\n")
        write(
            root / "prefix_manifest.json",
            json.dumps(
                {
                    "schema_version": 1,
                    "entries": [],
                    "tree_sha256": hashlib.sha256(repo.encode()).hexdigest(),
                },
                sort_keys=True,
            )
            + "\n",
        )
        write(root / "prefix" / "include" / "cuda.h", "")
        write(root / "prefix" / "include" / "nccl.h", "")
        write(root / "prefix" / "lib64" / "libcudart.so", "INPUT(-lc)\n")
        write(root / "prefix" / "lib64" / "libnccl.so", "INPUT(-lc)\n")

    def write_downstream_module(self, *, sha: str, archive: Path | None = None) -> None:
        if archive is None:
            archive, _ = write_fixture_torch_archive(self.root / "artifact")
        shutil.copytree(FIXTURE_DIR, self.root, dirs_exist_ok=True)
        shutil.copyfile(FIXTURE_DIR / "BUILD.bazel.in", self.root / "BUILD.bazel")
        module_template = (FIXTURE_DIR / "MODULE.bazel.in").read_text(encoding="utf-8")
        write(
            self.root / "MODULE.bazel",
            module_template.format(
                archive=archive,
                module_overrides=local_override_block(self.module_overrides),
                module_root=self.module_root,
                sha256=sha,
            ),
        )

    def run_bazel(self, *args: str) -> subprocess.CompletedProcess[str]:
        startup_args = [
            str(self.bazel),
            f"--output_base={self.output_base}",
            "--nohome_rc",
            "--nosystem_rc",
        ]
        hermetic_args = [
            "--lockfile_mode=off",
            "--repository_disable_download",
            "--repo_contents_cache=",
            "--registry=file:///nonexistent",
            f"--distdir={self.distdir}",
            f"--repository_cache={self.repository_cache}",
            *self.module_override_args,
        ]
        return subprocess.run(
            [*startup_args, args[0], *hermetic_args, *args[1:]],
            cwd=self.root,
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=120,
        )

    def test_downstream_extension_analyses_against_public_torch_artifact(self) -> None:
        archive, archive_sha = write_fixture_torch_archive(self.root / "artifact")
        self.write_downstream_module(sha=archive_sha)
        result = self.run_bazel(
            "aquery",
            "--noshow_progress",
            "--output=jsonproto",
            "--include_artifacts=true",
            "--@vaso_graph//native/common:release_line=cu130",
            "deps(//:fixture_extension_raw)",
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("VasoTorchExtensionBuild", result.stdout)
        self.assertIn("VasoTorchExtensionRootfsPrefix", result.stdout)
        self.assertIn("vaso_torch_artifact_cu130", result.stdout)
        self.assertNotIn("cuda_native", result.stdout + result.stderr)
        self.assertNotIn("nccl_native", result.stdout + result.stderr)
        self.assertNotIn("py_typing_extensions_native", result.stdout + result.stderr)
        self.assertNotIn("//native/pytorch:pytorch_action", result.stdout + result.stderr)

        for target in ("@vaso_torch_artifact_cu130//:headers", "@vaso_torch_artifact_cu130//:libs"):
            with self.subTest(target=target):
                query_result = self.run_bazel(
                    "query",
                    "--noshow_progress",
                    target,
                    "--output=build",
                )
                self.assertEqual(query_result.returncode, 0, query_result.stderr)
                self.assertIn("cc_library(", query_result.stdout)

    def test_downstream_extension_accepts_real_torch_prefix_layout(self) -> None:
        archive, archive_sha = write_real_layout_torch_archive(self.root / "real-layout-artifact")
        self.write_downstream_module(sha=archive_sha, archive=archive)
        result = self.run_bazel(
            "aquery",
            "--noshow_progress",
            "--output=jsonproto",
            "--include_artifacts=true",
            "--@vaso_graph//native/common:release_line=cu130",
            "deps(//:fixture_extension_raw)",
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("VasoTorchExtensionBuild", result.stdout)
        self.assertIn("lib/python3.13/site-packages/torch/include", result.stdout)

    def test_torch_artifact_repo_rejects_sha_mismatch(self) -> None:
        self.write_downstream_module(sha="f" * 64)
        result = self.run_bazel(
            "query",
            "--noshow_progress",
            "@vaso_torch_artifact_cu130//:prefix",
        )

        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertIn("sha256 mismatch", result.stderr)


if __name__ == "__main__":
    unittest.main(argv=[__file__])
