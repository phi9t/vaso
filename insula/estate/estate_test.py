#!/usr/bin/env python3
"""Regression tests for estate.py placement: never seat the estate in RAM."""

from __future__ import annotations

import importlib.util
import contextlib
import io
import sys
import unittest
from pathlib import Path
from unittest import mock


SCRIPT = Path(__file__).with_name("estate.py")
SPEC = importlib.util.spec_from_file_location("estate", SCRIPT)
assert SPEC is not None
estate = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = estate
SPEC.loader.exec_module(estate)


class EstatePlacementTest(unittest.TestCase):
    def test_namespace_query_does_not_require_placement_or_disk_headroom(self) -> None:
        output = io.StringIO()
        with mock.patch.object(
            estate, "discover_estate_root", side_effect=SystemExit("insufficient space")
        ) as discover:
            with contextlib.redirect_stdout(output):
                result = estate.main(
                    [
                        "--print-sandbox-dirs",
                        "--root",
                        "/missing/estate",
                        "--required-gib",
                        "100000",
                    ]
                )
        self.assertEqual(result, 0)
        self.assertIn("/run/vaso", output.getvalue().splitlines())
        self.assertEqual(
            output.getvalue().splitlines(), list(estate.CANONICAL_SANDBOX_DIRS)
        )
        discover.assert_not_called()

    def setUp(self) -> None:
        import os
        import tempfile

        base = os.environ.get("TEST_TMPDIR") or os.environ.get("VASO_AGENT_IO_ROOT")
        self._tmp = tempfile.TemporaryDirectory(dir=base)
        self.base = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_missing_explicit_root_fails_closed(self) -> None:
        with mock.patch.dict("os.environ", {}, clear=True):
            with self.assertRaises(SystemExit) as ctx:
                estate.discover_estate_root(1)
        self.assertIn("VASO_ESTATE_ROOT", str(ctx.exception))

    def test_missing_explicit_root_does_not_probe_mounts(self) -> None:
        with (
            mock.patch.dict("os.environ", {}, clear=True),
            mock.patch("subprocess.run") as run,
        ):
            with self.assertRaises(SystemExit):
                estate.discover_estate_root(1)
        run.assert_not_called()

    def test_explicit_root_on_ram_is_rejected(self) -> None:
        with mock.patch.object(estate, "seat_fstype", return_value="tmpfs"):
            with self.assertRaises(SystemExit) as ctx:
                estate.discover_estate_root(
                    1, explicit_root=str(self.base / "explicit")
                )
        self.assertIn("tmpfs", str(ctx.exception))

    def test_explicit_root_on_disk_is_accepted(self) -> None:
        with mock.patch.object(estate, "seat_fstype", return_value="ext4"):
            chosen = estate.discover_estate_root(
                1, explicit_root=str(self.base / "explicit")
            )
        self.assertEqual(chosen.root, (self.base / "explicit").resolve())

    def test_seat_fstype_reads_real_mounts(self) -> None:
        self.assertNotEqual(estate.seat_fstype(Path("/proc/self")), "")

    def test_mount_path_escapes_select_the_actual_nested_filesystem(self) -> None:
        for name in (
            "mount space",
            "mount\t tab",
            "mount\nnewline",
            r"mount\backslash",
            r"mount\040",
        ):
            with self.subTest(name=name):
                mount = self.base / name
                mount.mkdir()
                encoded = (
                    str(mount)
                    .replace("\\", r"\134")
                    .replace(" ", r"\040")
                    .replace("\t", r"\011")
                    .replace("\n", r"\012")
                )
                records = f"root / ext4 rw 0 0\nram {encoded} tmpfs rw 0 0\n"
                with mock.patch.object(Path, "read_text", return_value=records):
                    self.assertEqual(estate.seat_fstype(mount / "new/estate"), "tmpfs")
                    with self.assertRaisesRegex(SystemExit, "tmpfs"):
                        estate.discover_estate_root(
                            1, explicit_root=str(mount / "new/estate")
                        )
                self.assertFalse((mount / "new").exists())

    def test_unavailable_filesystem_metadata_rejects_placement_before_creation(
        self,
    ) -> None:
        root = self.base / "unknown-filesystem"
        with mock.patch.object(
            Path, "read_text", side_effect=PermissionError("mount metadata unavailable")
        ):
            with self.assertRaisesRegex(SystemExit, "cannot determine filesystem"):
                estate.discover_estate_root(1, explicit_root=str(root))
        self.assertFalse(root.exists())

    def test_ram_seats_are_never_probed_or_created(self) -> None:
        ram_seat = self.base / "var-tmp" / "vaso-u" / "estate"
        with mock.patch.object(estate, "seat_fstype", return_value="tmpfs"):
            with self.assertRaises(SystemExit):
                estate.discover_estate_root(1, explicit_root=str(ram_seat))
        self.assertFalse(
            (self.base / "var-tmp").exists(), "probing must not create RAM-backed seats"
        )

    def test_invalid_headroom_is_rejected_before_creating_an_estate(self) -> None:
        root = self.base / "new-estate"
        for value in ("nan", "inf", "-inf", "-1", "1e308"):
            with self.subTest(value=value), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as error:
                    estate.main(["--root", str(root), "--required-gib=" + value])
                self.assertEqual(error.exception.code, 2)
                self.assertFalse(root.exists())

    def test_legacy_preference_does_not_override_explicit_root(self) -> None:
        root = self.base / "explicit"
        output = io.StringIO()
        with (
            mock.patch.object(estate, "seat_fstype", return_value="ext4"),
            contextlib.redirect_stdout(output),
        ):
            result = estate.main(
                ["--root", str(root), "--required-gib", "0", "--prefer", "/elsewhere"]
            )
        self.assertEqual(result, 0)
        self.assertEqual(output.getvalue().strip(), str(root.resolve()))

    def test_negative_headroom_fails_before_creating_an_explicit_root(self) -> None:
        root = self.base / "explicit"
        with self.assertRaisesRegex(ValueError, "nonnegative"):
            estate.discover_estate_root(-1, explicit_root=str(root))
        self.assertFalse(root.exists())

    def test_report_lists_declared_io_subtrees(self) -> None:
        root = self.base / "estate"
        est = estate.VasoEstate(root).materialize()
        (root / "vaso/cache/example.bin").write_bytes(b"abc")
        (root / "vaso/tmp/run/file").parent.mkdir(parents=True)
        (root / "vaso/tmp/run/file").write_text("x", encoding="utf-8")
        (root / "agents/trae/tmp").mkdir(parents=True)
        text = estate.format_report(est)
        for name in ["cache", "tmp", "runs", "agents"]:
            self.assertRegex(text, rf"(?m)^{name}\s+\d+\s+\d+\s+")
        self.assertIn(str(root / "vaso/cache"), text)
        self.assertIn(str(root / "agents"), text)

    def test_cuda_line_paths_are_explicit_and_partitioned(self) -> None:
        root = self.base / "estate"
        est = estate.VasoEstate(root).materialize()

        self.assertEqual(est.cuda_rootfs("cu129"), root / "rootfs-lines/cu129/rootfs")
        self.assertEqual(
            est.cuda_rootfs_manifest("cu130"),
            root / "rootfs-lines/cu130/rootfs-bundle.json",
        )
        self.assertEqual(
            est.line_bazel_output_base("cu129"),
            root / "vaso/lines/cu129/cache/bazel/output-base",
        )
        self.assertEqual(
            est.line_bazel_disk_cache("cu130"),
            root / "vaso/lines/cu130/cache/bazel/disk-cache",
        )
        self.assertEqual(
            est.line_native_state("cu129"), root / "vaso/lines/cu129/state/native"
        )
        self.assertEqual(
            est.line_native_stamps("cu130"), root / "vaso/lines/cu130/state/stamps"
        )
        self.assertEqual(
            est.bazel_repository_cache(), root / "vaso/cache/bazel/repository-cache"
        )
        self.assertTrue((root / "sources").is_dir())
        self.assertTrue((root / "agents/leases").is_dir())
        self.assertTrue((root / "vaso/lines/cu129/cache/bazel/output-base").is_dir())
        self.assertTrue((root / "vaso/lines/cu130/state/stamps").is_dir())

    def test_unknown_cuda_line_fails_loudly(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            estate.VasoEstate(self.base / "estate").cuda_rootfs("cu128")
        self.assertIn("unknown CUDA line", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
