"""Build-matrix policy and the unattended command interface."""

import argparse
from contextlib import redirect_stdout
import fcntl
import io
import json
import hashlib
import os
from pathlib import Path
import sys
import subprocess
import tempfile
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
TEST_TMP_ROOT = Path(os.environ.get("TEST_TMPDIR") or os.environ.get("TMPDIR") or tempfile.gettempdir())
sys.path.insert(0, str(HERE))
import build_variants as b


class BuildVariantsTest(unittest.TestCase):
    def make_runner(self, temp):
        args = argparse.Namespace(
            estate_root=temp / "new",
            seed_estate=temp / "seed",
            fetch=False,
            prepare_only=False,
            line="all",
            profile="all",
            status_interval=0.02,
            color="never",
        )
        runner = b.Runner(args, console=b.Console(stream=io.StringIO(), color="never"))
        return runner

    def test_help_explains_cache_boundaries_and_progress_legend(self):
        stream = io.StringIO()
        with redirect_stdout(stream), self.assertRaises(SystemExit) as raised:
            b.main(["--help"])

        self.assertEqual(raised.exception.code, 0)
        output = stream.getvalue()
        self.assertIn(
            "--seed-estate imports only SHA-256-verified source blobs", output
        )
        self.assertIn('"disk-cache" means the disk-cache backend is enabled', output)
        self.assertIn("package-sized actions", output)

    def test_invalid_status_intervals_fail_before_creating_an_estate(self):
        with tempfile.TemporaryDirectory(dir=TEST_TMP_ROOT) as directory:
            estate = Path(directory) / "new"
            for interval in ("nan", "inf", "-inf", "0", "-1"):
                with self.subTest(interval=interval):
                    result = subprocess.run(
                        [
                            str(HERE.parent / "build.sh"),
                            "--estate-root",
                            str(estate),
                            "--status-interval=" + interval,
                        ],
                        capture_output=True,
                        text=True,
                        check=False,
                    )
                    self.assertEqual(result.returncode, 2)
                    self.assertIn("finite and positive", result.stderr)
                    self.assertNotIn("Traceback", result.stderr)
                    self.assertFalse(estate.exists())

    def test_status_and_setup_errors_have_actionable_diagnostics(self):
        with tempfile.TemporaryDirectory(dir=TEST_TMP_ROOT) as directory:
            root = Path(directory)
            estate = root / "estate"
            state = estate / "agents/build-variants"
            state.mkdir(parents=True)
            pointer = state / "latest.json"
            pointer.write_text("invalid JSON")
            for mode in (["--status"], []):
                with self.subTest(mode=mode):
                    result = subprocess.run(
                        [
                            str(HERE.parent / "build.sh"),
                            "--estate-root",
                            str(estate),
                            *mode,
                        ],
                        capture_output=True,
                        text=True,
                        check=False,
                    )
                    self.assertEqual(result.returncode, 2)
                    self.assertIn(str(pointer), result.stderr)
                    self.assertNotIn("Traceback", result.stderr)
                    self.assertEqual(pointer.read_text(), "invalid JSON")
                    self.assertEqual(list(state.iterdir()), [pointer])

    def test_status_preserves_failed_report_with_nonfinite_metrics(self):
        with tempfile.TemporaryDirectory(dir=TEST_TMP_ROOT) as directory:
            estate = Path(directory) / "estate"
            summary = estate / "agents/build-variants/retained/summary.json"
            summary.parent.mkdir(parents=True)
            original = json.dumps(
                {
                    "run_id": "retained",
                    "status": "failed",
                    "error": "retained failure cause",
                    "duration_seconds": float("inf"),
                    "variants": [
                        {
                            "line": "cu129",
                            "profile": "torch",
                            "status": "failed",
                            "duration_seconds": float("inf"),
                            "artifacts": [
                                {"name": "retained.whl", "size_bytes": float("-inf")}
                            ],
                        }
                    ],
                }
            )
            summary.write_text(original)
            result = subprocess.run(
                [
                    str(HERE.parent / "build.sh"),
                    "--estate-root",
                    str(estate),
                    "--status",
                    "--color",
                    "never",
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0)
            self.assertIn("retained failure cause", result.stdout)
            self.assertIn("retained.whl", result.stdout)
            self.assertNotIn("Traceback", result.stderr)
            self.assertEqual(summary.read_text(), original)

    def test_finish_and_status_command_preserve_preparation_result(self):
        with tempfile.TemporaryDirectory(dir=TEST_TMP_ROOT) as directory:
            r = self.make_runner(Path(directory))
            r.run.finish("prepared")

            self.assertTrue(
                all(
                    variant["status"] == "skipped"
                    for variant in r.run.report["variants"]
                )
            )
            self.assertIn("PREPARED", Path(r.run.report["result_path"]).read_text())
            # Editing a historical report must not change the public status view.
            historical = r.run.state_root / "historical" / "summary.json"
            historical.parent.mkdir()
            historical.write_text('{"status": "failed"}')
            selected_mtime = Path(r.run.report["summary_path"]).stat().st_mtime
            os.utime(historical, (selected_mtime + 60, selected_mtime + 60))
            result = subprocess.run(
                [
                    str(HERE.parent / "build.sh"),
                    "--estate-root",
                    str(r.estate),
                    "--status",
                    "--color",
                    "never",
                ],
                cwd=directory,
                capture_output=True,
                text=True,
                check=True,
            )
            self.assertIn("PREPARED", result.stdout)

    def test_second_runner_refuses_an_owned_estate(self):
        with tempfile.TemporaryDirectory(dir=TEST_TMP_ROOT) as directory:
            estate = Path(directory)
            (estate / "agents").mkdir()
            with (estate / "agents/build-variants.lock").open("w") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX)
                result = subprocess.run(
                    [str(HERE.parent / "build.sh"), "--estate-root", str(estate)],
                    capture_output=True,
                    text=True,
                    check=False,
                )
            self.assertEqual(result.returncode, 2)
            self.assertIn("another variant runner owns this estate", result.stderr)
            self.assertFalse((estate / "agents/build-variants").exists())

    def test_failed_variant_stops_the_matrix_and_preserves_later_work(self):
        with tempfile.TemporaryDirectory(dir=TEST_TMP_ROOT) as directory:
            r = self.make_runner(Path(directory))
            with (
                patch.object(r, "preflight"),
                patch.object(r, "prepare_inputs"),
                patch.object(
                    r.run, "stage", side_effect=b.StageFailure("build failed")
                ) as stage,
            ):
                with self.assertRaises(b.StageFailure):
                    r.execute()
            self.assertEqual(stage.call_count, 1)
            self.assertEqual(
                [v["status"] for v in r.run.report["variants"]],
                ["failed", "pending", "pending", "pending"],
            )
            self.assertNotEqual(r.run.report["status"], "built")

    def test_failed_materialization_retains_inventory_in_summary_and_status(self):
        with tempfile.TemporaryDirectory(dir=TEST_TMP_ROOT) as directory:
            runner = self.make_runner(Path(directory))
            variant = runner.run.report["variants"][0]
            relative = Path("native/pytorch/pytorch_release_wheel_wheelhouse")
            present = (
                Path(variant["output_base"]) / "execroot/_main" / relative / "present.whl"
            )
            present.parent.mkdir(parents=True)
            present.write_bytes(b"wheel")
            events = Path(variant["build_events"])
            events.write_text(
                json.dumps(
                    {
                        "namedSetOfFiles": {
                            "files": [
                                {"name": str(relative / "present.whl"), "length": "5"},
                                {
                                    "name": str(relative / "missing.whl"),
                                    "length": "123",
                                    "digest": "a" * 64,
                                },
                            ]
                        }
                    }
                )
                + "\n"
            )
            with (
                patch.object(runner, "preflight"),
                patch.object(runner, "prepare_inputs"),
                patch.object(runner.run, "stage"),
            ):
                with self.assertRaisesRegex(RuntimeError, "not materialized") as raised:
                    runner.execute()
            runner.run.finish("failed", str(raised.exception))
            saved = json.loads(Path(runner.run.report["summary_path"]).read_text())
            failed = saved["variants"][0]
            self.assertEqual(failed["status"], "failed")
            self.assertEqual([v["status"] for v in saved["variants"]][1:], ["pending"] * 3)
            artifacts = {item["name"]: item for item in failed["artifacts"]}
            self.assertEqual(set(artifacts), {"present.whl", "missing.whl"})
            self.assertTrue(artifacts["present.whl"]["exists"])
            self.assertFalse(artifacts["missing.whl"]["exists"])
            self.assertEqual(artifacts["missing.whl"]["size_bytes"], 123)
            self.assertEqual(artifacts["missing.whl"]["digest"], "a" * 64)
            status = subprocess.run(
                [
                    str(HERE.parent / "build.sh"),
                    "--estate-root",
                    str(runner.estate),
                    "--status",
                    "--color",
                    "never",
                ],
                capture_output=True,
                text=True,
                check=True,
            ).stdout
            self.assertTrue(
                any(
                    "missing.whl" in line and "MISSING" in line
                    for line in status.splitlines()
                )
            )
            self.assertTrue(
                any(
                    "present.whl" in line and "PRESENT" in line
                    for line in status.splitlines()
                )
            )

    def test_bad_download_cannot_become_a_cached_input(self):
        with tempfile.TemporaryDirectory(dir=TEST_TMP_ROOT) as directory:
            r = self.make_runner(Path(directory))
            r.args.fetch = True
            r.inputs.find = lambda _: None
            asset = b.Asset("archive", "a" * 64, ("https://example.org/archive",))

            def bad_download(*_):
                (
                    r.estate
                    / "vaso/cache/build-inputs"
                    / (asset.sha256 + "." + r.run.output.name + ".part")
                ).write_bytes(b"wrong bytes")

            r.run.stage = bad_download
            with self.assertRaisesRegex(RuntimeError, "cannot acquire"):
                r.prepare_asset(asset)
            self.assertFalse((r.estate / "vaso/cache/bazel/repository-cache").exists())
            self.assertEqual(len(r.run.report["integrity_failures"]), 1)
            self.assertFalse(
                list((r.estate / "vaso/cache/build-inputs").glob("*.part"))
            )

    def test_install_failure_can_retry_offline_from_completed_download(self):
        with tempfile.TemporaryDirectory(dir=TEST_TMP_ROOT) as directory:
            runner = self.make_runner(Path(directory))
            runner.args.fetch = True
            payload = b"retained source archive"
            asset = b.Asset(
                "fixture", hashlib.sha256(payload).hexdigest(),
                ("https://example.org/archive",),
            )

            def download(*_):
                partial = (
                    runner.estate / "vaso/cache/build-inputs"
                    / (asset.sha256 + "." + runner.run.output.name + ".part")
                )
                partial.write_bytes(payload)

            with patch.object(runner.run, "stage", side_effect=download) as stage:
                with patch.object(
                    runner.inputs, "install", side_effect=OSError("cache unavailable")
                ):
                    with self.assertRaisesRegex(OSError, "cache unavailable"):
                        runner.prepare_asset(asset)
                self.assertEqual(stage.call_count, 1)
            self.assertEqual(runner.run.report.get("inputs_prepared", 0), 0)
            runner.args.fetch = False
            with patch.object(
                runner.run, "stage", side_effect=AssertionError("unexpected fetch")
            ):
                runner.prepare_asset(asset)
            self.assertEqual(runner.run.report["inputs_prepared"], 1)
            cached = runner.inputs.find(asset)
            self.assertEqual(cached.read_bytes(), payload)
            self.assertIn("repository-cache", cached.parts)

    def test_all_variants_are_serial_offline_and_token_enabled(self):
        with tempfile.TemporaryDirectory(dir=TEST_TMP_ROOT) as directory:
            r = self.make_runner(Path(directory))
            calls = []
            r.run.stage = lambda name, command, line, profile: calls.append(
                (name, command, line, profile)
            )
            r.prepare_asset = lambda _: None
            r.inputs.find = lambda _: Path(directory)
            expected = b.digest(r.root / "foundry/rootfs/cuda_ecosystem.lock.json")
            for line in r.lines:
                path = r.estate / f"rootfs-lines/{line}/rootfs-bundle.json"
                path.parent.mkdir(parents=True)
                path.write_text('{"lock":{"sha256":"' + expected + '"}}')
            with (
                patch.object(
                    b,
                    "selected_assets",
                    return_value=[b.Asset("x", "a" * 64, ("https://a/1",))],
                ),
                patch.object(
                    b,
                    "rules_python_asset_from_mirror",
                    return_value=b.Asset("python", "b" * 64, ("https://a/2",)),
                ),
                patch.object(
                    b,
                    "build_event_artifacts",
                    return_value=[{"name": "release.whl", "exists": True}],
                ),
            ):
                r.execute()
            builds = [call for call in calls if call[0].startswith("build-")]
            self.assertEqual(
                [(c[2], c[3]) for c in builds],
                [
                    ("cu129", "torch"),
                    ("cu129", "jax"),
                    ("cu130", "torch"),
                    ("cu130", "jax"),
                ],
            )
            self.assertTrue(all(c[1][1] == "--bazel-build" for c in builds))
            self.assertTrue(
                all("--remote_download_outputs=all" in c[1] for c in builds)
            )
            self.assertEqual(len(r.run.report["variants"]), 4)
            self.assertEqual(r.run.report["status"], "built")
            self.assertIn(
                "--//native/torchvision:token=build-native-torchvision", builds[0][1]
            )

    def test_prepare_only_cannot_claim_a_build(self):
        with tempfile.TemporaryDirectory(dir=TEST_TMP_ROOT) as directory:
            r = self.make_runner(Path(directory))
            r.args.prepare_only = True
            r.lines, r.profiles = ["cu129"], ["torch"]
            expected = b.digest(r.root / "foundry/rootfs/cuda_ecosystem.lock.json")
            manifest = r.estate / "rootfs-lines/cu129/rootfs-bundle.json"
            manifest.parent.mkdir(parents=True)
            manifest.write_text('{"lock":{"sha256":"' + expected + '"}}')
            calls = []
            r.run.stage = lambda *args: calls.append(args)
            r.inputs.find = lambda _: Path(directory)
            r.prepare_asset = lambda _: None
            with (
                patch.object(
                    b,
                    "selected_assets",
                    return_value=[b.Asset("x", "a" * 64, ("https://a/1",))],
                ),
                patch.object(
                    b,
                    "rules_python_asset_from_mirror",
                    return_value=b.Asset("python", "b" * 64, ("https://a/2",)),
                ),
            ):
                r.execute()
            self.assertFalse(any(call[0].startswith("build-") for call in calls))
            self.assertEqual(r.run.report["status"], "prepared")
            self.assertTrue(
                all(
                    variant["status"] == "pending"
                    for variant in r.run.report["variants"]
                )
            )


if __name__ == "__main__":
    unittest.main()
