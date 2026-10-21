"""Behavior checks for the cache-empty full rebuild wrapper."""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "foundry/full_rebuild.sh"
TEST_TMP_ROOT = Path(os.environ.get("TEST_TMPDIR") or os.environ.get("TMPDIR") or ROOT)


class FullRebuildTest(unittest.TestCase):
    def make_seed(self, seed: Path) -> Path:
        for line in ("cu129", "cu130"):
            (seed / f"rootfs-lines/{line}/rootfs").mkdir(parents=True)
            (seed / f"rootfs-lines/{line}/rootfs-bundle.json").write_text(
                "{}\n", encoding="utf-8"
            )
        (seed / "sources/bcr").mkdir(parents=True)
        (seed / "sources/bcr/marker").write_text("pinned\n", encoding="utf-8")
        return seed

    def make_fake_checkout(self, root: Path) -> tuple[Path, Path]:
        foundry = root / "checkout/foundry"
        foundry.mkdir(parents=True)
        wrapper = foundry / "full_rebuild.sh"
        shutil.copy2(SCRIPT, wrapper)
        runner = foundry / "build.sh"
        runner.write_text(
            "#!/usr/bin/env bash\n"
            "set -euo pipefail\n"
            'printf \'%s\\n\' "$@" > "${CAPTURE_ARGS:?}"\n',
            encoding="utf-8",
        )
        runner.chmod(0o755)
        return wrapper, runner

    def test_creates_fresh_estate_and_executes_full_matrix(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TMP_ROOT) as directory:
            root = Path(directory)
            seed = self.make_seed(root / "seed")
            wrapper, _ = self.make_fake_checkout(root)
            estate_parent = root / "estates"
            capture = root / "args"

            result = subprocess.run(
                [
                    wrapper,
                    "--seed-estate",
                    seed,
                    "--estate-parent",
                    estate_parent,
                ],
                check=False,
                capture_output=True,
                text=True,
                env={**os.environ, "CAPTURE_ARGS": str(capture)},
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            arguments = capture.read_text(encoding="utf-8").splitlines()
            self.assertEqual(arguments[0], "--estate-root")
            fresh_estate = Path(arguments[1])
            self.assertEqual(
                arguments[2:],
                ["--seed-estate", str(seed), "--line", "all", "--profile", "all"],
            )
            self.assertTrue(fresh_estate.name.startswith(".vaso-estate-full-rebuild-"))
            self.assertEqual(fresh_estate.parent, estate_parent)
            self.assertTrue((fresh_estate / "rootfs-lines").is_symlink())
            self.assertEqual(
                (fresh_estate / "rootfs-lines").resolve(),
                (seed / "rootfs-lines").resolve(),
            )
            self.assertEqual(
                (fresh_estate / "sources/bcr/marker").read_text(encoding="utf-8"),
                "pinned\n",
            )
            self.assertFalse((fresh_estate / "vaso").exists())
            self.assertIn(f"Fresh estate: {fresh_estate}", result.stdout)

    def test_environment_seed_creates_a_sibling_estate(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TMP_ROOT) as directory:
            root = Path(directory)
            home = root / "home"
            seed = self.make_seed(home / ".vaso-estate-build-cu129")
            wrapper, _ = self.make_fake_checkout(root)
            capture = root / "args"

            result = subprocess.run(
                [wrapper],
                check=False,
                capture_output=True,
                text=True,
                env={
                    **os.environ,
                    "CAPTURE_ARGS": str(capture),
                    "VASO_FULL_REBUILD_SEED_ESTATE": str(seed),
                },
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            arguments = capture.read_text(encoding="utf-8").splitlines()
            fresh_estate = Path(arguments[1])
            self.assertEqual(fresh_estate.parent, home)
            self.assertEqual(arguments[3], str(seed))
            self.assertEqual(arguments[-4:], ["--line", "all", "--profile", "all"])

    def test_missing_seed_configuration_fails_before_creating_an_estate(self) -> None:
        env = os.environ.copy()
        env.pop("VASO_FULL_REBUILD_SEED_ESTATE", None)
        result = subprocess.run(
            [SCRIPT],
            check=False,
            capture_output=True,
            text=True,
            env=env,
        )

        self.assertEqual(result.returncode, 2)
        self.assertIn("pass --seed-estate", result.stderr)

    def test_missing_seed_fails_before_creating_an_estate(self) -> None:
        with tempfile.TemporaryDirectory(dir=TEST_TMP_ROOT) as directory:
            root = Path(directory)
            estate_parent = root / "estates"

            result = subprocess.run(
                [
                    SCRIPT,
                    "--seed-estate",
                    root / "missing",
                    "--estate-parent",
                    estate_parent,
                ],
                check=False,
                capture_output=True,
                text=True,
            )

            self.assertEqual(result.returncode, 2)
            self.assertIn("seed estate is not a directory", result.stderr)
            self.assertFalse(estate_parent.exists())


if __name__ == "__main__":
    unittest.main()
