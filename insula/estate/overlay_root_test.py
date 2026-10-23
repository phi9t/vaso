"""Rootfs projection preserves merged-usr aliases and mountpoint isolation."""

from pathlib import Path
import tempfile
import subprocess
import sys
import unittest

sys.path.insert(0, str(Path(__file__).parent))
from overlay_root import plan_binds


class OverlayRootTest(unittest.TestCase):
    def test_merged_usr_aliases_keep_their_target_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "usr/bin").mkdir(parents=True)
            (root / "bin").symlink_to("usr/bin")
            (root / "lib").symlink_to("usr/lib")
            args = plan_binds(root, ["/vaso"])
            self.assertIn(
                ["--symlink", "usr/bin", "/bin"],
                [args[i : i + 3] for i in range(len(args))],
            )
            self.assertIn(
                ["--symlink", "usr/lib", "/lib"],
                [args[i : i + 3] for i in range(len(args))],
            )
            self.assertNotIn(str(root / "bin"), args)

    def test_invalid_mountpoints_are_rejected_before_planning(self) -> None:
        for mountpoint in (
            "relative/path",
            "/",
            "/opt/../tool",
            "/opt/./tool",
            "//opt/tool",
            "/opt//tool",
            "/opt/tool\nother",
            "/opt/tool\x00",
        ):
            with self.subTest(mountpoint=mountpoint):
                # Validation must precede even reading the base directory.
                with self.assertRaisesRegex(ValueError, "mountpoint"):
                    plan_binds(Path("/missing-base"), [mountpoint])

    def test_newline_in_base_entry_cannot_corrupt_argument_stream(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "entry\n--bind").mkdir()
            with self.assertRaisesRegex(ValueError, "newline"):
                plan_binds(root, ["/vaso"])

    def test_cli_errors_emit_no_partial_plan_or_traceback(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            (Path(temp) / "entry\n--bind").mkdir()
            for base, mountpoint in (
                (Path(temp), "relative"),
                (Path(temp) / "missing", "/vaso"),
                (Path(temp), "/vaso"),
            ):
                with self.subTest(base=base, mountpoint=mountpoint):
                    result = subprocess.run(
                        [
                            sys.executable,
                            str(Path(__file__).with_name("overlay_root.py")),
                            "--base",
                            str(base),
                            "--mountpoint",
                            mountpoint,
                        ],
                        capture_output=True,
                        text=True,
                        check=False,
                    )
                    self.assertEqual(result.returncode, 2)
                    self.assertEqual(result.stdout, "")
                    self.assertIn("rootfs overlay", result.stderr)
                    self.assertNotIn("Traceback", result.stderr)

    def test_aliases_beside_reserved_mounts_keep_targets(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "opt").mkdir()
            (root / "opt/tool").symlink_to("/usr/lib/tool")
            (root / "opt/vaso").mkdir()
            args = plan_binds(root, ["/opt/vaso"])
            self.assertIn(
                ["--symlink", "/usr/lib/tool", "/opt/tool"],
                [args[i : i + 3] for i in range(len(args))],
            )
            self.assertNotIn(str(root / "opt/vaso"), args)


if __name__ == "__main__":
    unittest.main()
