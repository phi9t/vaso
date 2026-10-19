#!/usr/bin/env python3
"""Tests for leak_sweep.py."""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import tempfile
import unittest
import zipfile
from pathlib import Path


SCRIPT = Path(__file__).with_name("leak_sweep.py")
GRAPH_ROOT = Path(__file__).resolve().parents[1]
NATIVE_COMMON = GRAPH_ROOT / "native" / "common"


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


leak_sweep = load_module("leak_sweep", SCRIPT)
plan_executor = load_module("plan_executor_for_leak_sweep_test", NATIVE_COMMON / "plan_executor.py")


def scratch_parent() -> str:
    return os.environ.get("TEST_TMPDIR") or os.environ.get("VASO_AGENT_IO_ROOT") or str(Path.cwd())


class LeakSweepTest(unittest.TestCase):
    def test_reports_every_hit_in_a_tree(self) -> None:
        with tempfile.TemporaryDirectory(dir=scratch_parent()) as td:
            root = Path(td)
            tree = root / "prefix"
            tree.mkdir()
            work = root / "out" / ".work"
            work.mkdir(parents=True)
            (tree / "a.txt").write_text(
                "first /vaso/lines/cu129/cache path and second /.vaso-estate/home path\n",
                encoding="utf-8",
            )
            (tree / "b.bin").write_bytes(b"binary prefix bazel-out/k8-opt/bin and exact " + str(work).encode("utf-8"))

            hits = list(leak_sweep.scan_paths([tree], leak_sweep.build_forbidden_tokens(work=work)))

            hit_pairs = {(Path(hit.path).name, hit.token) for hit in hits}
            self.assertIn(("a.txt", "/vaso/lines/"), hit_pairs)
            self.assertIn(("a.txt", "/.vaso-estate"), hit_pairs)
            self.assertIn(("b.bin", "bazel-out/"), hit_pairs)
            self.assertIn(("b.bin", str(work)), hit_pairs)
            self.assertGreaterEqual(len(hits), 4)

    def test_reports_hit_inside_wheel_member(self) -> None:
        with tempfile.TemporaryDirectory(dir=scratch_parent()) as td:
            root = Path(td)
            wheel = root / "pkg-0-py3-none-any.whl"
            with zipfile.ZipFile(wheel, "w") as archive:
                archive.writestr("pkg/module.py", b"safe bytes before /vaso/lines/cu130/work safe bytes after")

            hits = list(leak_sweep.scan_paths([wheel], leak_sweep.build_forbidden_tokens()))

            self.assertEqual(1, len(hits))
            self.assertEqual(f"{wheel}::pkg/module.py", hits[0].display_path)
            self.assertEqual("/vaso/lines/", hits[0].token)
            self.assertEqual("archive-member", hits[0].kind)
            self.assertIn("/vaso/lines/cu130/work", hits[0].context)

    def test_reports_hit_in_symlink_target(self) -> None:
        with tempfile.TemporaryDirectory(dir=scratch_parent()) as td:
            root = Path(td)
            tree = root / "prefix"
            tree.mkdir()
            link = tree / "leaky-link"
            link.symlink_to("/vaso/lines/cu129/cache/native-prefix")

            hits = list(leak_sweep.scan_paths([tree], leak_sweep.build_forbidden_tokens()))

            self.assertEqual(1, len(hits))
            self.assertEqual(str(link), hits[0].display_path)
            self.assertEqual("symlink", hits[0].kind)
            self.assertEqual("/vaso/lines/", hits[0].token)
            self.assertIn("/vaso/lines/cu129/cache/native-prefix", hits[0].context)

    def test_fixed_output_base_mount_is_allowed(self) -> None:
        with tempfile.TemporaryDirectory(dir=scratch_parent()) as td:
            root = Path(td)
            payload = root / "prefix" / "lib" / "metadata.txt"
            payload.parent.mkdir(parents=True)
            payload.write_text("/vaso-bazel-ob/external/dep/prefix/lib/libcuda.so\n", encoding="utf-8")

            hits = list(leak_sweep.scan_paths([root / "prefix"], leak_sweep.build_forbidden_tokens()))

            self.assertEqual([], hits)

    def test_token_set_matches_plan_executor_plus_executor_extras(self) -> None:
        with tempfile.TemporaryDirectory(dir=scratch_parent()) as td:
            root = Path(td)
            work = root / "out" / ".work"
            prefix = root / "prefix"
            wheel = root / "wheelhouse" / "pkg.whl"

            actual = leak_sweep.build_forbidden_tokens(work=work, prefix=prefix, wheel=wheel)
            expected = plan_executor.action_path_tokens([work, prefix, wheel])
            expected.update({str(work), str(prefix), str(wheel)})

            self.assertEqual(expected, actual)

    def test_json_cli_reports_hits_and_nonzero_exit(self) -> None:
        with tempfile.TemporaryDirectory(dir=scratch_parent()) as td:
            root = Path(td)
            payload = root / "prefix" / "bin" / "tool"
            payload.parent.mkdir(parents=True)
            payload.write_bytes(b"hello /vaso/lines/cu130/cache world")
            stdout = io.StringIO()

            with contextlib.redirect_stdout(stdout):
                rc = leak_sweep.main(["--json", str(root / "prefix")])

            report = json.loads(stdout.getvalue())
            self.assertEqual(1, rc)
            self.assertEqual(1, report["hit_count"])
            self.assertEqual(str(payload), report["hits"][0]["path"])
            self.assertEqual("/vaso/lines/", report["hits"][0]["token"])


if __name__ == "__main__":
    unittest.main()
