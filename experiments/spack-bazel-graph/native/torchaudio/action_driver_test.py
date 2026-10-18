#!/usr/bin/env python3
"""Tests for the native torchaudio action driver."""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path


SCRIPT = Path(__file__).with_name("action_driver.py")
SPEC = importlib.util.spec_from_file_location("torchaudio_action_driver", SCRIPT)
assert SPEC is not None
driver = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = driver
SPEC.loader.exec_module(driver)


def temporary_directory():
    base = os.environ.get("TEST_TMPDIR") or os.environ.get("VASO_AGENT_IO_ROOT")
    return tempfile.TemporaryDirectory(dir=base)


def write_prefix_files(root: Path) -> list[str]:
    items = []
    for key in driver.PREFIX_FILE_KEYS:
        prefix = root / key
        prefix.mkdir(parents=True)
        prefix_file = root / f"{key}.txt"
        prefix_file.write_text(str(prefix), encoding="utf-8")
        items.extend(["--prefix-file", f"{key}={prefix_file}"])
    return items


def write_fake_plan(path: Path) -> None:
    path.write_text(
        textwrap.dedent(
            """\
            #!/usr/bin/env python3
            import argparse
            import json

            parser = argparse.ArgumentParser()
            parser.add_argument("--out", required=True)
            parser.add_argument("--prefix", action="append", default=[])
            parser.add_argument("rest", nargs="*")
            args, _ = parser.parse_known_args()
            prefixes = dict(item.split("=", 1) for item in args.prefix)
            Path = __import__("pathlib").Path
            Path(args.out).write_text(json.dumps({
                "authorization": {"required_token": "build-native-torchaudio", "token_present": False},
                "dependency_decisions": {},
                "input_prefixes": prefixes,
                "mode": "dry-run",
                "preflight_ok": True,
                "required_prefixes": sorted(prefixes),
                "will_build": False,
            }))
            """
        ),
        encoding="utf-8",
    )
    path.chmod(0o755)


class TorchaudioActionDriverTest(unittest.TestCase):
    def test_relative_torch_prefix_is_absolutized_before_planning(self) -> None:
        old_cwd = Path.cwd()
        with temporary_directory() as tmp:
            root = Path(tmp)
            execroot = root / "execroot"
            execroot.mkdir()
            relative_torch = Path("bazel-out/k8-fastbuild/bin/native/pytorch/pytorch_action_prefix")
            (execroot / relative_torch).mkdir(parents=True)
            manifest = root / "rootfs-bundle.json"
            manifest.write_text("{}", encoding="utf-8")
            source_anchor = root / "source" / "setup.py"
            source_anchor.parent.mkdir()
            source_anchor.write_text("", encoding="utf-8")
            fake_plan = root / "fake_plan.py"
            write_fake_plan(fake_plan)
            plan_out = root / "plan.json"
            argv = [
                "--plan",
                str(fake_plan),
                "--pins",
                str(root / "pins.json"),
                "--prefix-out",
                str(root / "prefix"),
                "--build-plan-out",
                str(plan_out),
                "--provider-metadata-out",
                str(root / "metadata.json"),
                "--result-marker-out",
                str(root / "result.txt"),
                "--wheel-out",
                str(root / "wheel.whl"),
                "--source-anchor",
                str(source_anchor),
                "--torch-prefix",
                str(relative_torch),
                *write_prefix_files(root / "prefixes"),
            ]
            (root / "pins.json").write_text("{}", encoding="utf-8")

            try:
                os.chdir(execroot)
                old_env = os.environ.copy()
                os.environ.update(
                    {
                        "TMPDIR": str(root / "tmp"),
                        "VASO_IN_INSULA": "1",
                        "VASO_ROOTFS_BUNDLE_MANIFEST": str(manifest),
                    }
                )
                rc = driver.main(argv)
            finally:
                os.environ.clear()
                os.environ.update(old_env)
                os.chdir(old_cwd)

            plan_doc = json.loads(plan_out.read_text(encoding="utf-8"))
            torch_prefix = Path(plan_doc["input_prefixes"]["torch"])
            self.assertEqual(rc, 0)
            self.assertTrue(torch_prefix.is_absolute())
            self.assertEqual(torch_prefix, (execroot / relative_torch).resolve())


if __name__ == "__main__":
    unittest.main()
