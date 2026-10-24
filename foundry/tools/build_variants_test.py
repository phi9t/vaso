"""Build-matrix policy and the unattended command interface."""

import argparse
from contextlib import redirect_stdout, redirect_stderr
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
    def test_help_names_the_supported_build_command(self):
        result = subprocess.run(
            [str(HERE.parent / "build.sh"), "--help"],
            capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(result.stdout.startswith("usage: foundry/build.sh "), result.stdout)

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

    def test_fetch_uses_canonical_execution_and_preparation_has_durable_status(self):
        # Fixture pins and the command executor replace external acquisition;
        # InputStore, the build lifecycle, and status stay real.
        with tempfile.TemporaryDirectory(dir=TEST_TMP_ROOT) as directory:
            estate = Path(directory) / "estate"
            payload = b"fixture input"
            asset = b.Asset("fixture", hashlib.sha256(payload).hexdigest(),
                            ("https://example.org/source",))
            calls = []

            def execute(session, command, env, log, entry, started):
                calls.append(command)
                log.write_text("fixture command passed\n")
                if command[0] == "env":
                    self.assertEqual(command[1], "INSULA_FETCH_SHA256=" + asset.sha256)
                    self.assertEqual(Path(command[2]), HERE.parents[1] / "insula/run.sh")
                    self.assertIn("fetch", command)
                    dest = command[command.index("-o") + 1]
                    (estate / "vaso" / dest.removeprefix("/vaso/")).write_bytes(payload)
                return 0, False

            output = io.StringIO()
            with (
                patch.object(b, "selected_assets", return_value=[asset]),
                patch.object(b, "rules_python_asset_from_mirror", return_value=asset),
                patch.object(b.BuildSession, "_execute", execute),
                redirect_stdout(output), redirect_stderr(output),
            ):
                result = b.main(["--estate-root", str(estate), "--fetch", "--prepare-only", "--color", "never"])
                self.assertEqual(result, 0, output.getvalue())
                self.assertEqual(b.main(["--estate-root", str(estate), "--status", "--color", "never"]), 0)
            self.assertIn("PREPARED", output.getvalue())
            self.assertFalse(any("--bazel-build" in call for call in calls))
            cache = estate / "vaso/cache/bazel/repository-cache/content_addressable/sha256" / asset.sha256 / "file"
            self.assertEqual(cache.read_bytes(), payload)

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

    def test_invalid_latest_pointer_cannot_select_historical_success(self):
        with tempfile.TemporaryDirectory(dir=TEST_TMP_ROOT) as directory:
            estate = Path(directory) / "estate"
            state = estate / "agents/build-variants"
            historical = state / "historical/summary.json"
            historical.parent.mkdir(parents=True)
            historical.write_text('{"status": "built"}')
            pointer = state / "latest.json"
            for value in (
                {},
                {"summary": None},
                {"summary": 7},
                {"summary": []},
                {"summary": ""},
                {"summary": " "},
            ):
                original = json.dumps(value)
                pointer.write_text(original)
                for mode in (["--status"], []):
                    with self.subTest(pointer=value, mode=mode):
                        result = subprocess.run(
                            [
                                str(HERE.parent / "build.sh"),
                                "--estate-root",
                                str(estate),
                                *mode,
                            ],
                            stdin=subprocess.DEVNULL,
                            capture_output=True,
                            text=True,
                            check=False,
                        )
                        self.assertEqual(result.returncode, 2)
                        self.assertIn(str(pointer), result.stderr)
                        self.assertIn("expected a nonempty summary path", result.stderr)
                        self.assertNotIn("Traceback", result.stderr)
                        self.assertNotIn("BUILT", result.stdout)
                        self.assertEqual(pointer.read_text(), original)
                        self.assertEqual(historical.read_text(), '{"status": "built"}')
                        self.assertEqual(
                            set(state.iterdir()), {pointer, historical.parent}
                        )

    def test_malformed_summary_lists_fail_before_status_or_recovery_changes_evidence(self):
        cases = (
            ("variants[0].artifacts", {"variants": [{"artifacts": None}]}),
            ("variants[0].artifacts[0]", {"variants": [{"artifacts": [7]}]}),
            ("variants", {"variants": None}),
            ("variants[0]", {"variants": [None]}),
            ("stages", {"stages": None}),
            ("stages[0]", {"stages": ["invalid stage"]}),
        )
        for field, invalid in cases:
            for mode in (["--status"], []):
                with self.subTest(field=field, mode=mode):
                    with tempfile.TemporaryDirectory(dir=TEST_TMP_ROOT) as directory:
                        estate = Path(directory) / "estate"
                        state = estate / "agents/build-variants"
                        attempt = state / "retained"
                        attempt.mkdir(parents=True)
                        summary = attempt / "summary.json"
                        summary.write_text(
                            json.dumps(
                                {
                                    "status": "running",
                                    "stages": [],
                                    "variants": [],
                                    **invalid,
                                }
                            )
                        )
                        (state / "latest.json").write_text(
                            json.dumps({"summary": str(summary)})
                        )
                        (attempt / "events.jsonl").write_text("retained events\n")
                        (attempt / "result.txt").write_text("retained result\n")
                        before = {
                            p.relative_to(state): p.read_bytes()
                            for p in state.rglob("*")
                            if p.is_file()
                        }
                        result = subprocess.run(
                            [
                                str(HERE.parent / "build.sh"),
                                "--estate-root",
                                str(estate),
                                *mode,
                            ],
                            stdin=subprocess.DEVNULL,
                            capture_output=True,
                            text=True,
                            check=False,
                        )
                        self.assertEqual(result.returncode, 2)
                        self.assertIn(str(summary), result.stderr)
                        self.assertIn(field, result.stderr)
                        self.assertNotIn("Traceback", result.stderr)
                        self.assertEqual(
                            {
                                p.relative_to(state): p.read_bytes()
                                for p in state.rglob("*")
                                if p.is_file()
                            },
                            before,
                        )

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

    def test_incomplete_target_inventory_cannot_mark_a_profile_built(self):
        with tempfile.TemporaryDirectory(dir=TEST_TMP_ROOT) as directory:
            runner = self.make_runner(Path(directory))
            variant = runner.run.report["variants"][0]
            relative = Path("native/pytorch/pytorch_release_wheel_wheelhouse/torch.whl")
            wheel = Path(variant["output_base"]) / "execroot/_main" / relative
            wheel.parent.mkdir(parents=True)
            wheel.write_bytes(b"wheel")
            Path(variant["build_events"]).write_text(
                json.dumps({"namedSetOfFiles": {"files": [{"name": str(relative)}]}}) + "\n"
            )
            with patch.object(runner.run, "stage"):
                with self.assertRaisesRegex(
                    RuntimeError, "no release wheels for"
                ) as raised:
                    runner.build_variant("cu129", "torch")
            for target in b.TARGETS["torch"][1:]:
                self.assertIn(target, str(raised.exception))
            saved = json.loads(Path(runner.run.report["summary_path"]).read_text())
            self.assertEqual(saved["variants"][0]["status"], "failed")
            self.assertEqual(saved["variants"][0]["artifacts"][0]["path"], str(wheel))
            self.assertEqual([v["status"] for v in saved["variants"]][1:], ["pending"] * 3)

    def test_truncated_wheel_cannot_pass_a_complete_target_inventory(self):
        with tempfile.TemporaryDirectory(dir=TEST_TMP_ROOT) as directory:
            runner = self.make_runner(Path(directory))
            variant = runner.run.report["variants"][0]
            files = []
            for index, target in enumerate(b.TARGETS["torch"]):
                package, name = target.removeprefix("//").split(":")
                relative = Path(package) / (name + "_wheelhouse") / (name + ".whl")
                wheel = Path(variant["output_base"]) / "execroot/_main" / relative
                wheel.parent.mkdir(parents=True)
                wheel.write_bytes(b"bad" if index == 0 else b"wheel")
                files.append({"name": str(relative), "length": "5"})
            Path(variant["build_events"]).write_text(
                json.dumps({"namedSetOfFiles": {"files": files}}) + "\n"
            )
            with patch.object(runner.run, "stage"):
                with self.assertRaisesRegex(RuntimeError, "wheel sizes differ") as raised:
                    runner.build_variant("cu129", "torch")
            self.assertIn("expected 5, actual 3", str(raised.exception))
            saved = json.loads(Path(runner.run.report["summary_path"]).read_text())
            failed = saved["variants"][0]
            self.assertEqual(failed["status"], "failed")
            self.assertEqual(len(failed["artifacts"]), 4)
            self.assertTrue(all(a["exists"] for a in failed["artifacts"]))
            self.assertTrue(all(a["size_bytes"] == 5 for a in failed["artifacts"]))

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

    def test_failed_doctor_is_recorded_before_tooling_acquisition_or_builds(self):
        with tempfile.TemporaryDirectory(dir=TEST_TMP_ROOT) as directory:
            runner = self.make_runner(Path(directory))
            calls = []

            def execute(command, env, log, entry, started_monotonic):
                calls.append(command)
                if entry["name"] == "doctor-cu129":
                    log.write_text("FAIL: rootfs_manifest:lock_sha256: mismatch\n")
                    return 1, False
                return 0, False

            with patch.object(runner.run, "_execute", side_effect=execute):
                with self.assertRaises(b.StageFailure) as raised:
                    runner.execute()
            runner.run.finish("failed", str(raised.exception))
            saved = json.loads(Path(runner.run.report["summary_path"]).read_text())
            self.assertEqual(saved["status"], "failed")
            self.assertEqual(
                [stage["name"] for stage in saved["stages"]],
                ["materialize", "doctor-cu129"],
            )
            self.assertEqual(saved["stages"][-1]["returncode"], 1)
            self.assertEqual(saved["stages"][-1]["status"], "failed")
            self.assertIn("rootfs_manifest:lock_sha256", saved["stages"][-1]["tail"][-1])
            self.assertEqual(len(calls), 2)
            self.assertTrue(all(v["status"] == "pending" for v in saved["variants"]))

    def test_all_variants_are_serial_offline_and_token_enabled(self):
        with tempfile.TemporaryDirectory(dir=TEST_TMP_ROOT) as directory:
            root = Path(directory)
            estate, seed = root / "estate", root / "seed"
            payload = b"fixture pinned source"
            asset = b.Asset("fixture", hashlib.sha256(payload).hexdigest(),
                            ("https://example.org/source",))
            source = seed / "vaso/cache/build-inputs" / asset.sha256
            source.parent.mkdir(parents=True)
            source.write_bytes(payload)
            calls = []
            # Literal parity expectations are independent of the selection owner.
            expected = {
                "torch": (["//native/pytorch:pytorch_release_wheel",
                           "//native/torchvision:torchvision_release_wheel",
                           "//native/torchaudio:torchaudio_release_wheel",
                           "//native/triton:triton_release_wheel"],
                          ["--//native/pytorch:token=build-native-pytorch",
                           "--//native/torchvision:token=build-native-torchvision",
                           "--//native/torchaudio:token=build-native-torchaudio",
                           "--//native/triton:token=build-native-triton"]),
                "jax": (["//native/jaxlib:jaxlib_release_wheels"],
                        ["--//native/jaxlib:token=build-native-llvm"]),
            }

            def execute(session, command, env, log, entry, started):
                calls.append((entry["name"], command, env))
                log.write_text("fixture command passed\n")
                self.assertEqual(env["VASO_GPUS"], "0")
                if "--bazel-build" not in command:
                    return 0, False
                line, profile = env["VASO_CUDA_LINE"], env["VASO_PROFILE"]
                targets, tokens = expected[profile]
                self.assertEqual(command[:2], ["foundry/run.sh", "--bazel-build"])
                self.assertEqual(command[2:-2], targets + tokens)
                self.assertEqual(command[-2], "--remote_download_outputs=all")
                self.assertEqual(env["VASO_BAZEL_OB"], str(estate / f"vaso/lines/{line}/cache/bazel/output-base-{profile}"))
                self.assertEqual(env["VASO_BAZEL_DISK_CACHE"], str(estate / f"vaso/lines/{line}/cache/bazel/disk-cache"))
                variant = next(v for v in session.report["variants"]
                               if v["line"] == line and v["profile"] == profile)
                files = []
                for target in targets:
                    package, name = target.removeprefix("//").split(":")
                    relative = Path("bazel-out/k8-fastbuild/bin") / package / (name + "_wheelhouse") / (name + ".whl")
                    wheel = Path(variant["output_base"]) / "execroot/_main" / relative
                    wheel.parent.mkdir(parents=True)
                    wheel.write_bytes(b"wheel")
                    files.append({"name": str(relative), "length": "5",
                                  "digest": hashlib.sha256(b"wheel").hexdigest()})
                Path(variant["build_events"]).write_text(json.dumps({"namedSetOfFiles": {"files": files}}) + "\n")
                return 0, False

            output = io.StringIO()
            with (
                patch.object(b, "selected_assets", return_value=[asset]),
                patch.object(b, "rules_python_asset_from_mirror", return_value=asset),
                patch.object(b.BuildSession, "_execute", execute),
                redirect_stdout(output), redirect_stderr(output),
            ):
                self.assertEqual(b.main(["--estate-root", str(estate), "--seed-estate", str(seed),
                                        "--line", "all", "--profile", "all", "--color", "never"]), 0, output.getvalue())
            self.assertEqual([c[0] for c in calls[:5]],
                             ["materialize", "doctor-cu129", "tools-cu129", "doctor-cu130", "tools-cu130"])
            builds = [c for c in calls if "--bazel-build" in c[1]]
            self.assertEqual([(c[2]["VASO_CUDA_LINE"], c[2]["VASO_PROFILE"]) for c in builds],
                             [("cu129", "torch"), ("cu129", "jax"), ("cu130", "torch"), ("cu130", "jax")])
            modules = next(c[1] for c in calls if c[0] == "modules")
            self.assertIn("--offline", modules)
            pointer = json.loads((estate / "agents/build-variants/latest.json").read_text())
            saved = json.loads(Path(pointer["summary"]).read_text())
            self.assertEqual(saved["status"], "built")
            self.assertEqual([v["status"] for v in saved["variants"]], ["built"] * 4)
            self.assertEqual([len(v["artifacts"]) for v in saved["variants"]], [4, 1, 4, 1])
            for variant in saved["variants"]:
                for artifact in variant["artifacts"]:
                    self.assertTrue(artifact["exists"])
                    self.assertEqual(artifact["size_bytes"], 5)
                    self.assertEqual(artifact["digest"], hashlib.sha256(b"wheel").hexdigest())
            status = subprocess.run([str(HERE.parent / "build.sh"), "--estate-root", str(estate),
                                     "--status", "--color", "never"], cwd=directory,
                                    capture_output=True, text=True, check=True).stdout
            self.assertIn("BUILT", status)
            self.assertIn("pytorch_release_wheel.whl", status)
            self.assertIn("jaxlib_release_wheels.whl", status)

    def test_command_failures_retain_attempts_and_retry_from_verified_inputs(self):
        for failure, message, exit_code in (
            ("missing-input", "pinned inputs missing", 1),
            ("corrupt-input", "pinned inputs missing", 1),
            ("corrupt-fetch", "cannot acquire pinned input", 1),
            ("missing-output", "not materialized", 1),
            ("truncated-output", "sizes differ", 1),
            ("uncovered-output", "no release wheels for", 1),
            ("interrupted", "interrupted", 130),
        ):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory(dir=TEST_TMP_ROOT) as directory:
                estate = Path(directory) / "estate"
                payload = b"fixture source"
                asset = b.Asset("fixture", hashlib.sha256(payload).hexdigest(),
                                ("https://example.org/source",))
                completed = estate / "vaso/cache/build-inputs" / asset.sha256
                completed.parent.mkdir(parents=True)
                if failure != "missing-input":
                    completed.write_bytes(b"corrupt" if failure in {"corrupt-input", "corrupt-fetch"} else payload)
                mode = failure
                builds = []

                def execute(session, command, env, log, entry, started):
                    log.write_text("fixture " + mode + "\n")
                    if command[0] == "env":
                        destination = command[command.index("-o") + 1]
                        (estate / "vaso" / destination.removeprefix("/vaso/")).write_bytes(b"corrupt")
                    if "--bazel-build" not in command:
                        return 0, False
                    builds.append(command)
                    if mode == "interrupted":
                        return -15, True
                    variant = session.report["variants"][0]
                    targets = [arg for arg in command if arg.startswith("//")]
                    files = []
                    for index, target in enumerate(targets):
                        if mode == "uncovered-output" and index:
                            continue
                        package, name = target.removeprefix("//").split(":")
                        relative = Path("bazel-out/k8-fastbuild/bin") / package / (name + "_wheelhouse") / (name + ".whl")
                        wheel = Path(variant["output_base"]) / "execroot/_main" / relative
                        wheel.parent.mkdir(parents=True, exist_ok=True)
                        if mode != "missing-output":
                            wheel.write_bytes(b"bad" if mode == "truncated-output" else b"wheel")
                        files.append({"name": str(relative), "length": "5"})
                    Path(variant["build_events"]).write_text(json.dumps({"namedSetOfFiles": {"files": files}}) + "\n")
                    return 0, False

                args = ["--estate-root", str(estate), "--color", "never"]
                output = io.StringIO()
                with (
                    patch.object(b, "selected_assets", return_value=[asset]),
                    patch.object(b, "rules_python_asset_from_mirror", return_value=asset),
                    patch.object(b.BuildSession, "_execute", execute),
                    redirect_stdout(output), redirect_stderr(output),
                ):
                    self.assertEqual(b.main(args + (["--fetch"] if failure == "corrupt-fetch" else [])), exit_code, output.getvalue())
                    pointer = estate / "agents/build-variants/latest.json"
                    failed_path = Path(json.loads(pointer.read_text())["summary"])
                    failed_bytes = failed_path.read_bytes()
                    failed = json.loads(failed_bytes)
                    self.assertIn(message, failed["error"])
                    self.assertEqual(failed["status"], "interrupted" if exit_code == 130 else "failed")
                    self.assertEqual(b.main(args + ["--status"]), 0)
                    self.assertIn("INTERRUPTED" if exit_code == 130 else "FAILED", output.getvalue())
                    if "input" in failure or failure == "corrupt-fetch":
                        self.assertFalse(builds)
                    if failure in {"missing-input", "corrupt-input"}:
                        inventory = json.loads((failed_path.parent / "missing-inputs.json").read_text())
                        self.assertEqual(inventory[0]["sha256"], asset.sha256)
                    self.assertFalse(list(completed.parent.glob("*.part")))
                    # Repair only the fixture's source bytes, then retry offline.
                    completed.write_bytes(payload)
                    mode = "retry"
                    self.assertEqual(b.main(args), 0, output.getvalue())
                    self.assertEqual(failed_path.read_bytes(), failed_bytes)
                    latest = Path(json.loads(pointer.read_text())["summary"])
                    self.assertNotEqual(latest, failed_path)
                    self.assertEqual(json.loads(latest.read_text())["status"], "built")
                    self.assertEqual(b.main(args + ["--status"]), 0)

    def test_prepare_only_cannot_claim_a_build(self):
        with tempfile.TemporaryDirectory(dir=TEST_TMP_ROOT) as directory:
            r = self.make_runner(Path(directory))
            r.args.prepare_only = True
            r.lines, r.profiles = ["cu129"], ["torch"]
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
