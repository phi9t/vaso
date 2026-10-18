#!/usr/bin/env python3
"""Tests for the real prefix_manifest.bzl manifest script."""

from __future__ import annotations

import ast
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


PREFIX_MANIFEST_BZL = Path(__file__).with_name("prefix_manifest.bzl")
FIXTURE_PYTHON_VERSION = ".".join(("3", "13"))
FIXTURE_SITE_PACKAGES = Path("lib") / ("python" + FIXTURE_PYTHON_VERSION) / "site-packages"
FIXTURE_PYC_TAG = "cpython-" + "".join(FIXTURE_PYTHON_VERSION.split("."))


def scratch_parent() -> Path:
    if value := os.environ.get("TEST_TMPDIR"):
        return Path(value)
    if value := os.environ.get("VASO_AGENT_IO_ROOT"):
        path = Path(value) / "tmp"
        path.mkdir(parents=True, exist_ok=True)
        return path
    if value := os.environ.get("VASO_ESTATE_ROOT"):
        path = Path(value) / "agents" / os.environ.get("VASO_AGENT", "prefix-manifest-test") / "tmp"
        path.mkdir(parents=True, exist_ok=True)
        return path
    return Path.cwd()


def load_manifest_script() -> str:
    tree = ast.parse(PREFIX_MANIFEST_BZL.read_text(encoding="utf-8"))
    for node in tree.body:
        if (
            isinstance(node, ast.Assign)
            and any(isinstance(target, ast.Name) and target.id == "_PREFIX_MANIFEST_SCRIPT" for target in node.targets)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        ):
            return node.value.value
    raise AssertionError("prefix_manifest.bzl does not define _PREFIX_MANIFEST_SCRIPT")


def write(path: Path, content: bytes | str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, bytes):
        path.write_bytes(content)
    else:
        path.write_text(content, encoding="utf-8")


class PrefixManifestScriptTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.script = load_manifest_script()

    def run_manifest(self, prefix: Path, out: Path, *dep_manifests: str, home: str = "/build/user") -> subprocess.CompletedProcess[str]:
        env = dict(os.environ, HOME=home)
        return subprocess.run(
            ["python3", "-c", self.script, str(prefix), str(out), *dep_manifests],
            check=False,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=30,
        )

    def read_manifest(self, prefix: Path, out: Path, *dep_manifests: str) -> dict[str, object]:
        result = self.run_manifest(prefix, out, *dep_manifests)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(out.read_text(encoding="utf-8"))

    def make_prefix(self, root: Path, pyc_bytes: bytes = b"pyc one") -> Path:
        prefix = root / "prefix"
        write(prefix / "bin" / "tool", "#!/vaso-bazel-ob/external/+python+python_313_native/prefix/bin/python3\n")
        write(prefix / "lib" / "pkgconfig" / "demo.pc", "prefix=/vaso-bazel-ob/external/+demo+demo_native/prefix\n")
        write(prefix / FIXTURE_SITE_PACKAGES / "demo.py", "VALUE = 1\n")
        write(prefix / FIXTURE_SITE_PACKAGES / "__pycache__" / f"demo.{FIXTURE_PYC_TAG}.pyc", pyc_bytes)
        (prefix / "lib" / "libdemo.so").symlink_to("/vaso-bazel-ob/external/+demo+demo_native/prefix/lib/libdemo.so")
        return prefix

    def test_pyc_and_location_do_not_affect_digest_but_real_content_does(self) -> None:
        with tempfile.TemporaryDirectory(dir=scratch_parent()) as tmp:
            root = Path(tmp)
            first = self.make_prefix(root / "one", b"pyc from first install path")
            second = self.make_prefix(root / "two", b"pyc from second install path")
            os.utime(second / "bin" / "tool", (1_700_000_001, 1_700_000_001))
            first_manifest = self.read_manifest(first, root / "first.json")
            second_manifest = self.read_manifest(second, root / "second.json")

            self.assertEqual(first_manifest["tree_sha256"], second_manifest["tree_sha256"])
            manifest_text = json.dumps(first_manifest, sort_keys=True)
            self.assertNotIn("__pycache__", manifest_text)
            self.assertNotIn(".pyc", manifest_text)

            write(second / FIXTURE_SITE_PACKAGES / "demo.py", "VALUE = 2\n")
            changed_manifest = self.read_manifest(second, root / "changed.json")
            self.assertNotEqual(second_manifest["tree_sha256"], changed_manifest["tree_sha256"])

    def test_dependency_manifest_digest_is_chained_into_tree_digest(self) -> None:
        with tempfile.TemporaryDirectory(dir=scratch_parent()) as tmp:
            root = Path(tmp)
            prefix = self.make_prefix(root / "python", b"ignored pyc")
            zlib_manifest = root / "zlib-prefix-manifest.json"
            write(zlib_manifest, '{"tree_sha256":"zlib-one"}\n')
            first = self.read_manifest(prefix, root / "python-one.json", f"zlib-ng={zlib_manifest}")

            write(zlib_manifest, '{"tree_sha256":"zlib-two"}\n')
            second = self.read_manifest(prefix, root / "python-two.json", f"zlib-ng={zlib_manifest}")

            self.assertNotEqual(first["tree_sha256"], second["tree_sha256"])
            self.assertIn("dependencies", second)

    def test_absolute_paths_outside_fixed_insula_mount_fail(self) -> None:
        with tempfile.TemporaryDirectory(dir=scratch_parent()) as tmp:
            root = Path(tmp)
            prefix = self.make_prefix(root / "bad", b"ignored pyc")
            write(prefix / "lib" / "pkgconfig" / "bad.pc", "prefix=/build/user/not-stable\n")

            result = self.run_manifest(prefix, root / "bad.json", home="/build/user")

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("/build/user", result.stderr)

    def test_rootfs_system_absolute_paths_are_stable_inputs(self) -> None:
        with tempfile.TemporaryDirectory(dir=scratch_parent()) as tmp:
            root = Path(tmp)
            prefix = self.make_prefix(root / "rootfs", b"ignored pyc")
            write(prefix / "bin" / "shell-tool", "#!/bin/sh\nexec /usr/bin/env python3 \"$@\"\n")
            write(prefix / "lib" / "pkgconfig" / "rootfs.pc", "sysconfdir=/etc\nlibdir=/lib64\nincludedir=/usr/include\n")
            (prefix / "lib" / "system-lib.so").symlink_to("/lib/x86_64-linux-gnu/libm.so.6")
            (prefix / "share").mkdir(parents=True)
            (prefix / "share" / "cuda").symlink_to("/opt/cuda/include")

            manifest = self.read_manifest(prefix, root / "rootfs.json")

            manifest_text = json.dumps(manifest, sort_keys=True)
            self.assertIn("/lib/x86_64-linux-gnu/libm.so.6", manifest_text)
            self.assertIn("/opt/cuda/include", manifest_text)

    def test_unstable_absolute_paths_still_fail(self) -> None:
        unstable_paths = (
            "/vaso/lines/cu130/cache/bazel/output-base/external/pkg/prefix",
            "$VASO_ESTATE_ROOT/.vaso-estate/agents/fc-n1-2b/bazel-ob/external/pkg/prefix",
            "/" + "tmp/bazel-output-base/external/pkg/prefix",
            "/home/<user>/.cache/bazel/output-base/external/pkg/prefix",
            "/vaso-bazel-ob/execroot/_main/bazel-out/k8-fastbuild/bin/native/pkg/action",
            "/vaso-bazel-ob/sandbox/processwrapper-sandbox/1/execroot/_main/pkg",
        )
        for absolute_path in unstable_paths:
            with self.subTest(absolute_path=absolute_path):
                with tempfile.TemporaryDirectory(dir=scratch_parent()) as tmp:
                    root = Path(tmp)
                    prefix = self.make_prefix(root / "bad", b"ignored pyc")
                    write(prefix / "lib" / "pkgconfig" / "bad.pc", f"prefix={absolute_path}\n")

                    result = self.run_manifest(prefix, root / "bad.json")

                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn(absolute_path, result.stderr)

    def test_free_text_slash_tokens_in_scripts_are_not_paths(self) -> None:
        # bzip2's bin/bzdiff contains tokens like "/6000"; text scans only reject known varying prefixes.
        with tempfile.TemporaryDirectory(dir=scratch_parent()) as tmp:
            root = Path(tmp)
            prefix = self.make_prefix(root / "bzip2", b"ignored pyc")
            write(prefix / "bin" / "bzdiff", "#!/bin/sh\nOPTIONS=\"-u\"\nsleep 1/6000 2>/dev/null || :\n")

            result = self.run_manifest(prefix, root / "bzip2.json")

            self.assertEqual(result.returncode, 0, result.stderr)

    def test_symlink_targets_stay_strict(self) -> None:
        with tempfile.TemporaryDirectory(dir=scratch_parent()) as tmp:
            root = Path(tmp)
            prefix = self.make_prefix(root / "link", b"ignored pyc")
            (prefix / "lib" / "odd.so").symlink_to("/random/place/libodd.so")

            result = self.run_manifest(prefix, root / "link.json")

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("/random/place/libodd.so", result.stderr)

    def test_runtime_temp_dir_references_in_scripts_are_allowed(self) -> None:
        # gzexe/libtool/xzdiff/bzdiff default to /tmp; findutils updatedb names /tmp/updatedb-trace.txt.
        with tempfile.TemporaryDirectory(dir=scratch_parent()) as tmp:
            root = Path(tmp)
            prefix = self.make_prefix(root / "tools", b"ignored pyc")
            tmp_root = "/" + "tmp"
            write(prefix / "bin" / "xzdiff", f"#!/bin/sh\nTMPDIR=${{TMPDIR:-{tmp_root}}}\n")
            write(prefix / "bin" / "updatedb", f"#!/bin/sh\ntrace={tmp_root}/updatedb-trace.txt\n")

            result = self.run_manifest(prefix, root / "tools.json")

            self.assertEqual(result.returncode, 0, result.stderr)

    def test_bazel_temp_paths_in_scripts_still_fail(self) -> None:
        with tempfile.TemporaryDirectory(dir=scratch_parent()) as tmp:
            root = Path(tmp)
            prefix = self.make_prefix(root / "bad", b"ignored pyc")
            bad = "/" + "tmp/bazel-work/execroot/_main/pkg"
            write(prefix / "bin" / "tool", f"#!/bin/sh\nexec {bad}/run\n")

            result = self.run_manifest(prefix, root / "bad.json")

            self.assertNotEqual(result.returncode, 0)
            self.assertIn(bad, result.stderr)

    def test_documentation_examples_in_scripts_are_not_paths(self) -> None:
        # perl's bin/cpanm documents "~/minicpan  # Because shell expands ~ to /home/user".
        with tempfile.TemporaryDirectory(dir=scratch_parent()) as tmp:
            root = Path(tmp)
            prefix = self.make_prefix(root / "perl", b"ignored pyc")
            write(prefix / "bin" / "cpanm", "#!/usr/bin/perl\n# cpanm --mirror ~/minicpan  # Because shell expands ~ to /home/user\n")

            result = self.run_manifest(prefix, root / "perl.json", home="/build/user")

            self.assertEqual(result.returncode, 0, result.stderr)

    def test_all_violations_are_reported_together(self) -> None:
        with tempfile.TemporaryDirectory(dir=scratch_parent()) as tmp:
            root = Path(tmp)
            prefix = self.make_prefix(root / "multi", b"ignored pyc")
            write(prefix / "lib" / "pkgconfig" / "a.pc", "prefix=/vaso/lines/cu130/cache/bazel/output-base/external/a/prefix\n")
            write(prefix / "lib" / "pkgconfig" / "b.pc", "prefix=/build/user/b\n")

            result = self.run_manifest(prefix, root / "multi.json", home="/build/user")

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("a.pc", result.stderr)
            self.assertIn("b.pc", result.stderr)


if __name__ == "__main__":
    unittest.main()
