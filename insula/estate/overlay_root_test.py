"""Rootfs projection preserves merged-usr aliases and mountpoint isolation."""

from pathlib import Path
import tempfile
import unittest

from overlay_root import plan_binds


class OverlayRootTest(unittest.TestCase):
    def test_merged_usr_aliases_keep_their_target_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "usr/bin").mkdir(parents=True)
            (root / "bin").symlink_to("usr/bin")
            (root / "lib").symlink_to("usr/lib")
            args = plan_binds(root, ["/vaso"])
            self.assertIn(["--symlink", "usr/bin", "/bin"], [args[i:i + 3] for i in range(len(args))])
            self.assertIn(["--symlink", "usr/lib", "/lib"], [args[i:i + 3] for i in range(len(args))])
            self.assertNotIn(str(root / "bin"), args)

    def test_aliases_beside_reserved_mounts_keep_targets(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "opt").mkdir()
            (root / "opt/tool").symlink_to("/usr/lib/tool")
            (root / "opt/vaso").mkdir()
            args = plan_binds(root, ["/opt/vaso"])
            self.assertIn(["--symlink", "/usr/lib/tool", "/opt/tool"], [args[i:i + 3] for i in range(len(args))])
            self.assertNotIn(str(root / "opt/vaso"), args)


if __name__ == "__main__":
    unittest.main()
