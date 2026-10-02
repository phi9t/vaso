#!/usr/bin/env python3
"""Regression tests for estate.py placement: never seat the estate in RAM."""

from __future__ import annotations

import importlib.util
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
    def setUp(self) -> None:
        import os
        import tempfile

        base = os.environ.get("TEST_TMPDIR") or os.environ.get("VASO_AGENT_IO_ROOT")
        self._tmp = tempfile.TemporaryDirectory(dir=base)
        self.base = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _candidate(self, target: str, seat: Path, avail: int = 10**12) -> object:
        return estate.MountCandidate(
            target=Path(target), fstype="ext4", avail_bytes=avail, seat=seat, writable=True
        )

    def test_root_mount_seat_on_tmpfs_var_tmp_is_rejected(self) -> None:
        ram_seat = self.base / "var-tmp" / "vaso-u" / "estate"
        disk_seat = self.base / "data" / "u" / "vaso-estate"
        fstypes = {ram_seat: "tmpfs", disk_seat: "ext4"}
        with mock.patch.object(estate, "_list_mounts", return_value=[
            self._candidate("/", ram_seat, avail=10**13),
            self._candidate("/data", disk_seat),
        ]), mock.patch.object(estate, "seat_fstype", side_effect=lambda p: fstypes[p]):
            chosen = estate.discover_estate_root(1)
        self.assertEqual(chosen.root, disk_seat.resolve())

    def test_no_disk_seat_fails_loudly(self) -> None:
        ram_seat = self.base / "var-tmp" / "estate"
        with mock.patch.object(estate, "_list_mounts", return_value=[self._candidate("/", ram_seat)]), \
                mock.patch.object(estate, "seat_fstype", return_value="tmpfs"):
            with self.assertRaises(SystemExit) as ctx:
                estate.discover_estate_root(1)
        self.assertIn("non-tmpfs", str(ctx.exception))

    def test_explicit_root_on_ram_is_rejected(self) -> None:
        with mock.patch.object(estate, "seat_fstype", return_value="tmpfs"):
            with self.assertRaises(SystemExit) as ctx:
                estate.discover_estate_root(1, explicit_root=str(self.base / "explicit"))
        self.assertIn("tmpfs", str(ctx.exception))

    def test_explicit_root_on_disk_is_accepted(self) -> None:
        with mock.patch.object(estate, "seat_fstype", return_value="ext4"):
            chosen = estate.discover_estate_root(1, explicit_root=str(self.base / "explicit"))
        self.assertEqual(chosen.root, (self.base / "explicit").resolve())

    def test_seat_fstype_reads_real_mounts(self) -> None:
        self.assertNotEqual(estate.seat_fstype(Path("/proc/self")), "")

    def test_ram_seats_are_never_probed_or_created(self) -> None:
        ram_seat = self.base / "var-tmp" / "vaso-u" / "estate"
        findmnt = {"filesystems": [{"target": "/", "fstype": "ext4", "avail": "1000000000000"}]}
        with mock.patch.object(estate.subprocess, "run") as run, \
                mock.patch.object(estate, "_seat_for", return_value=ram_seat), \
                mock.patch.object(estate, "seat_fstype", return_value="tmpfs"):
            run.return_value.stdout = __import__("json").dumps(findmnt)
            self.assertEqual(estate._list_mounts(), [])
        self.assertFalse((self.base / "var-tmp").exists(), "probing must not create RAM-backed seats")

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
        self.assertEqual(est.cuda_rootfs_manifest("cu130"), root / "rootfs-lines/cu130/rootfs-bundle.json")
        self.assertEqual(est.line_bazel_output_base("cu129"), root / "vaso/lines/cu129/cache/bazel/output-base")
        self.assertEqual(est.line_bazel_disk_cache("cu130"), root / "vaso/lines/cu130/cache/bazel/disk-cache")
        self.assertEqual(est.line_native_state("cu129"), root / "vaso/lines/cu129/state/native")
        self.assertEqual(est.line_native_stamps("cu130"), root / "vaso/lines/cu130/state/stamps")
        self.assertEqual(est.bazel_repository_cache(), root / "vaso/cache/bazel/repository-cache")
        self.assertTrue((root / "vaso/lines/cu129/cache/bazel/output-base").is_dir())
        self.assertTrue((root / "vaso/lines/cu130/state/stamps").is_dir())

    def test_unknown_cuda_line_fails_loudly(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            estate.VasoEstate(self.base / "estate").cuda_rootfs("cu128")
        self.assertIn("unknown CUDA line", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
