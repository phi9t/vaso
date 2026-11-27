"""Public common build selection and migration behavior."""
import contextlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import build_contract as b
from build_report import atomic_json


class BuildContractTest(unittest.TestCase):
    def parse(self, *argv):
        return b.parser().parse_args(argv)

    def test_catalog_and_modern_dispatch(self):
        self.assertEqual(set(b.TARGETS), {
            "cu129/torch", "cu129/jax", "cu130/torch", "cu130/jax",
            "cu126-sm61/torch", "cu126-sm61/triton"})
        for name, target in b.TARGETS.items():
            if target.backend != "bazel":
                continue
            for phase in ("prepare", "build"):
                with self.subTest(name=name, phase=phase):
                    cmd = b.dispatch(self.parse("--target", name, "--phase", phase, "--estate-root", "/estate"))
                    self.assertEqual(Path(cmd[2]).name, "build_variants.py")
                    self.assertEqual(cmd[cmd.index("--line")+1], target.cuda_line)
                    self.assertEqual("--prepare-only" in cmd, phase == "prepare")

    def test_both_pascal_targets_share_builder_and_owner(self):
        for short in ("torch", "triton"):
            a = self.parse("--target", "cu126-sm61/"+short, "--phase", "prepare",
                           "--insula", "/insula", "--spec", "/spec", "--supervisor", "/owner",
                           "--output", "/out", "--jobs", "1")
            cmd = b.dispatch(a)
            self.assertEqual(b.TARGETS[a.target].backend, "bazel-capsule")
            native = cmd[cmd.index("--")+1:]
            self.assertEqual(native[:3], ["/opt/vaso/pascal/bin/build-native", "--target", short])
            self.assertIn("--jobs", native)
            self.assertIn("/out/lifecycle", cmd)
            self.assertNotIn("--gpu", cmd)

    def test_native_owner_is_explicit_and_output_is_required(self):
        base = ["--target", "cu126-sm61/torch", "--insula", "/insula", "--spec", "/spec"]
        with self.assertRaisesRegex(ValueError, "--output"):
            b.dispatch(self.parse(*base))
        with self.assertRaisesRegex(ValueError, "--state"):
            b.dispatch(self.parse(*base, "--output", "/out"))
        cmd = b.dispatch(self.parse(*base, "--output", "/out", "--state", "/state", "--run-id", "run"))
        self.assertEqual(cmd[:2], ["/insula", "run"])
        with self.assertRaisesRegex(ValueError, "mutually exclusive"):
            b.dispatch(self.parse(*base, "--output", "/out", "--state", "/state", "--run-id", "run", "--supervisor", "/owner"))

    def test_legacy_source_options_have_migration_diagnostic(self):
        for options in (("--site", "/site"), ("--source-profile", "/profile"), ("--resume",)):
            with self.subTest(options=options), self.assertRaisesRegex(ValueError, "foundry/build.sh --source"):
                b.dispatch(self.parse("--target", "cu126-sm61/torch", *options))

    def test_unrelated_options_and_oversized_jobs_fail_closed(self):
        for argv in (
            ("--target", "cu129/torch", "--estate-root", "/estate", "--jobs", "1"),
            ("--target", "cu129/torch", "--estate-root", "/estate", "--phase", "status", "--fetch"),
            ("--target", "cu126-sm61/triton", "--output", "/out", "--phase", "status", "--spec", "/spec"),
        ):
            with self.subTest(argv=argv), self.assertRaises(ValueError):
                b.dispatch(self.parse(*argv))
        with patch.object(b.capsule_build, "cpu_count", return_value=2), self.assertRaisesRegex(ValueError, "CPUs"):
            b.dispatch(self.parse("--target", "cu126-sm61/triton", "--jobs", "3"))

    def test_argument_order_is_supported_by_public_shell(self):
        result = subprocess.run(
            [str(HERE.parent/"build.sh"), "--phase", "prepare", "--target", "cu126-sm61/triton",
             "--insula", "/insula", "--spec", "/spec", "--supervisor", "/owner", "--output", "/out", "--describe"],
            capture_output=True, text=True, check=True)
        self.assertEqual(json.loads(result.stdout)["target"], "cu126-sm61/triton")
        self.assertFalse(json.loads(result.stdout)["executed"])

    def test_canonical_modern_status_selects_matching_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            estate = Path(tmp)
            root = estate/"agents/build-variants"
            selected = root/"selected/summary.json"
            other = root/"other/summary.json"
            atomic_json(selected, {"status":"built","run_id":"selected","started_at":"2026-01-01",
                                  "variants":[{"line":"cu129","profile":"torch","status":"built","artifacts":[]}]})
            atomic_json(other, {"status":"failed","run_id":"other","started_at":"2026-01-02",
                               "variants":[{"line":"cu130","profile":"jax","status":"failed","artifacts":[]}]})
            atomic_json(root/"latest.json", {"summary":str(other)})
            out = io.StringIO()
            with contextlib.redirect_stdout(out), patch.object(b.os, "execv", side_effect=AssertionError("executed")):
                self.assertEqual(b.main(["--target","cu129/torch","--phase","status","--estate-root",tmp]),0)
            result = json.loads(out.getvalue())
            self.assertEqual(result["schema"],"vaso.foundry-build-result.v1")
            self.assertEqual(result["state"],"built")
            self.assertEqual(result["request_id"],"selected")


if __name__ == "__main__":
    unittest.main()
