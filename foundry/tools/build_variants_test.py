"""Behavior checks for preparation, unattended failure, and build-matrix selection."""

import argparse
from contextlib import redirect_stdout
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import subprocess
import tarfile
import tempfile
import time
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
TEST_TMP_ROOT = Path(os.environ.get("TEST_TMPDIR") or os.environ.get("TMPDIR") or HERE)
sys.path.insert(0, str(HERE))
spec = importlib.util.spec_from_file_location(
    "build_variants", HERE / "build_variants.py"
)
b = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = b
spec.loader.exec_module(b)


class BuildVariantsTest(unittest.TestCase):
    def test_actual_profiles_include_fp16_resource_and_jax_wheels(self):
        assets = b.selected_assets(HERE.parents[1], ["torch", "jax"])
        hashes = {a.sha256 for a in assets}
        self.assertIn(
            "90f20492621d5ed80b442aa682ff92d7ccf333ac8fac4a10e7e02afb159f3c13", hashes
        )
        self.assertIn(
            "f6c4dab91ae9a03b3019e7cab0572743afd0e1b6e75b97fcca50259c737c924e", hashes
        )
        self.assertIn(
            "b5cd66e240201f5a06af2a75eaffdf05a6c63829edada33ff569ada0037f8086", hashes
        )
        self.assertTrue(any(a.filename and a.filename.endswith(".whl") for a in assets))
        self.assertFalse(
            any(a.owner in {"tla2tools", "lean_dist", "llvm_native"} for a in assets)
        )

    def test_patch_arrays_are_individual_pinned_resources(self):
        assets = b.repo_assets(
            "patches",
            {
                "patches": ["https://a/1", "https://a/2"],
                "patch_sha256s": ["a" * 64, "b" * 64],
            },
        )
        self.assertEqual([a.urls for a in assets], [("https://a/1",), ("https://a/2",)])
        with self.assertRaisesRegex(ValueError, "count mismatch"):
            b.repo_assets(
                "bad",
                {"patch_urls": ["https://a/1"], "patch_sha256s": ["a" * 64, "b" * 64]},
            )

    def test_dynamic_download_pin_is_rejected_without_evaluation(self):
        with self.assertRaisesRegex(ValueError, "non-literal"):
            b.repo_declarations(
                'repo(name="x", urls=["https://a/1"], sha256=calculate())'
            )

    def test_rules_python_runtime_is_read_from_the_pinned_bcr_source(self):
        with tempfile.TemporaryDirectory(dir=TEST_TMP_ROOT) as directory:
            root = Path(directory)
            (root / "foundry/examples/consumers").mkdir(parents=True)
            (root / "foundry/MODULE.bazel").write_text(
                'bazel_dep(name = "rules_python", version = "1.2.3")\n'
            )
            module = """
python = use_extension("//python/extensions:python.bzl", "python")
python.defaults(python_version = "3.11")
"""
            versions = """
DEFAULT_RELEASE_BASE_URL = "https://example.org/releases"
TOOL_VERSIONS = {"3.11.9": {"url": "20240101/cpython-{python_version}-{platform}-{build}.{ext}", "sha256": {"x86_64-unknown-linux-gnu": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}}}
MINOR_MAPPING = {"3.11": "3.11.9"}
"""
            source = root / "rules_python-1.2.3.tar.gz"
            with tarfile.open(source, "w:gz") as bundle:
                for name, text in (
                    ("rules_python-1.2.3/MODULE.bazel", module),
                    ("rules_python-1.2.3/python/versions.bzl", versions),
                ):
                    path = root / name
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(text)
                    bundle.add(path, arcname=name)
            sha = b.digest(source)
            manifest = root / "foundry/examples/consumers/bcr-manifest.tsv"
            manifest.write_text(
                "module\tversion\tkind\turl\tsha256\tsize\n"
                f"rules_python\t1.2.3\tsource-archive\thttps://example.org/rules_python.tar.gz\t{sha}\t{source.stat().st_size}\n"
            )
            archive = (
                root
                / "estate/sources/bcr/archives/sha256"
                / sha
                / "rules_python.tar.gz"
            )
            archive.parent.mkdir(parents=True)
            archive.write_bytes(source.read_bytes())

            asset = b.rules_python_asset_from_mirror(root, root / "estate")

            self.assertEqual(asset.sha256, "a" * 64)
            self.assertEqual(
                asset.urls,
                (
                    "https://example.org/releases/20240101/"
                    "cpython-3.11.9-x86_64-unknown-linux-gnu-install_only.tar.gz",
                ),
            )

    def make_runner(self, temp):
        runner = b.Runner.__new__(b.Runner)
        runner.root = HERE.parents[1]
        runner.estate = temp / "new"
        runner.state_root = runner.estate / "agents/build-variants"
        runner.output = runner.state_root / "test-run"
        runner.output.mkdir(parents=True)
        runner.args = argparse.Namespace(
            seed_estate=temp / "seed",
            fetch=False,
            prepare_only=False,
            status_interval=0.02,
            color="never",
        )
        runner.lines, runner.profiles = ["cu129", "cu130"], ["torch", "jax"]
        runner.console = b.Console(stream=io.StringIO(), color="never")
        runner.lock_fd = None
        runner.started_monotonic = time.monotonic()
        runner.event_sequence = 0
        runner.report = {
            "schema_version": 2,
            "run_id": runner.output.name,
            "status": "running",
            "phase": "starting",
            "started_at": b.utc_now(),
            "updated_at": b.utc_now(),
            "duration_seconds": 0.0,
            "summary_path": str(runner.output / "summary.json"),
            "events_path": str(runner.output / "events.jsonl"),
            "result_path": str(runner.output / "result.txt"),
            "current_stage": None,
            "stages": [],
            "variants": b.planned_variants(
                runner.estate, runner.output, runner.lines, runner.profiles
            ),
            "qualification": "not performed",
        }
        runner.check_source = lambda: None
        return runner

    def test_local_seed_is_hashed_and_canonical_markers_are_restored(self):
        with tempfile.TemporaryDirectory(dir=TEST_TMP_ROOT) as directory:
            r = self.make_runner(Path(directory))
            payload = b"native archive"
            sha = hashlib.sha256(payload).hexdigest()
            path = (
                r.args.seed_estate
                / "vaso/cache/bazel/repository-cache/content_addressable/sha256"
                / sha
                / "file"
            )
            path.parent.mkdir(parents=True)
            path.write_bytes(payload)
            asset = b.Asset("fp16", sha, ("https://a/archive",))
            r.prepare_asset(asset)
            cached = (
                r.estate
                / "vaso/cache/bazel/repository-cache/content_addressable/sha256"
                / sha
            )
            self.assertEqual((cached / "file").read_bytes(), payload)
            self.assertTrue(list(cached.glob("id-*")))
            path.write_bytes(b"corrupt")
            (cached / "file").unlink()
            with self.assertRaisesRegex(RuntimeError, "missing pinned input"):
                r.prepare_asset(asset)

    def test_nested_wheels_are_materialized_for_offline_pip(self):
        with tempfile.TemporaryDirectory(dir=TEST_TMP_ROOT) as directory:
            r = self.make_runner(Path(directory))
            payload = b"wheel"
            source = Path(directory) / "wheel"
            source.write_bytes(payload)
            sha = hashlib.sha256(payload).hexdigest()
            r.available = lambda _: source
            r.prepare_asset(b.Asset("nested", sha, ("https://a/pkg.whl",), "pkg.whl"))
            self.assertEqual(
                (r.estate / "sources/jax/distdir/pkg.whl").read_bytes(), payload
            )
            (r.estate / "sources/jax/distdir/pkg.whl").write_bytes(b"other")
            with self.assertRaisesRegex(RuntimeError, "conflicting nested"):
                r.prepare_asset(
                    b.Asset("nested", sha, ("https://a/pkg.whl",), "pkg.whl")
                )

    def test_failed_command_retains_log_and_nonzero_status(self):
        with tempfile.TemporaryDirectory(dir=TEST_TMP_ROOT) as directory:
            r = self.make_runner(Path(directory))
            with self.assertRaisesRegex(RuntimeError, "exit 7"):
                r.stage(
                    "failure",
                    [
                        sys.executable,
                        "-c",
                        'print("failure evidence"); raise SystemExit(7)',
                    ],
                    "cu129",
                    "torch",
                )
            self.assertEqual(r.report["stages"][0]["returncode"], 7)
            self.assertIn(
                "failure evidence", Path(r.report["stages"][0]["log"]).read_text()
            )
            self.assertTrue(
                all(variant["status"] == "pending" for variant in r.report["variants"])
            )

    def test_interrupted_child_is_not_retried_as_an_acquisition_failure(self):
        with tempfile.TemporaryDirectory(dir=TEST_TMP_ROOT) as directory:
            r = self.make_runner(Path(directory))
            with self.assertRaises(InterruptedError):
                r.stage(
                    "interrupted",
                    [
                        sys.executable,
                        "-c",
                        "import os,signal; os.kill(os.getpid(), signal.SIGTERM)",
                    ],
                    "cu129",
                    "torch",
                )
            self.assertEqual(r.report["stages"][0]["status"], "interrupted")
            self.assertLess(r.report["stages"][0]["returncode"], 0)

    def test_stage_heartbeat_and_transitions_are_durable(self):
        with tempfile.TemporaryDirectory(dir=TEST_TMP_ROOT) as directory:
            r = self.make_runner(Path(directory))
            r.stage(
                "heartbeat",
                [
                    sys.executable,
                    "-c",
                    'import time; print("starting", flush=True); '
                    'time.sleep(0.08); print("complete", flush=True)',
                ],
                "cu129",
                "torch",
            )

            stage = r.report["stages"][0]
            self.assertEqual(stage["status"], "passed")
            self.assertGreater(stage["duration_seconds"], 0)
            self.assertGreater(stage["log_bytes"], 0)
            self.assertEqual(stage["progress"], "complete")
            self.assertIn("started_at", stage)
            self.assertIn("finished_at", stage)
            summary = json.loads(Path(r.report["summary_path"]).read_text())
            self.assertIsNone(summary["current_stage"])
            latest = json.loads((r.state_root / "latest.json").read_text())
            self.assertEqual(latest["summary"], r.report["summary_path"])
            events = [
                json.loads(line)
                for line in Path(r.report["events_path"]).read_text().splitlines()
            ]
            self.assertEqual(
                [event["event"] for event in events],
                ["stage_started", "stage_finished"],
            )

    def test_stage_inherits_runner_lock_until_child_exits(self):
        with tempfile.TemporaryDirectory(dir=TEST_TMP_ROOT) as directory:
            root = Path(directory)
            r = self.make_runner(root)
            with (root / "runner.lock").open("w") as lock:
                b.fcntl.flock(lock, b.fcntl.LOCK_EX)
                r.lock_fd = lock.fileno()
                r.stage(
                    "lock-inheritance",
                    [
                        sys.executable,
                        "-c",
                        f'import os; os.fstat({lock.fileno()}); print("inherited")',
                    ],
                    "cu129",
                    "torch",
                )
            self.assertIn("inherited", Path(r.report["stages"][0]["log"]).read_text())

    def test_build_event_wheels_become_structured_artifacts(self):
        with tempfile.TemporaryDirectory(dir=TEST_TMP_ROOT) as directory:
            root = Path(directory)
            output_base = root / "output-base"
            relative = Path(
                "bazel-out/k8-fastbuild/bin/native/pytorch/"
                "pytorch_release_wheel_wheelhouse/torch-1.whl"
            )
            wheel = output_base / "execroot/_main" / relative
            wheel.parent.mkdir(parents=True)
            wheel.write_bytes(b"wheel")
            events = root / "events.jsonl"
            events.write_text(
                json.dumps(
                    {
                        "namedSetOfFiles": {
                            "files": [
                                {
                                    "name": (
                                        "native/pytorch/"
                                        "pytorch_release_wheel_wheelhouse/torch-1.whl"
                                    ),
                                    "pathPrefix": ["bazel-out", "k8-fastbuild", "bin"],
                                    "digest": "a" * 64,
                                    "length": "5",
                                },
                                {
                                    "name": "native/pytorch/manifest.json",
                                    "pathPrefix": ["bazel-out"],
                                },
                            ]
                        }
                    }
                )
                + "\n"
            )

            artifacts = b.build_event_artifacts(events, output_base)

            self.assertEqual(len(artifacts), 1)
            self.assertEqual(artifacts[0]["name"], "torch-1.whl")
            self.assertEqual(artifacts[0]["path"], str(wheel))
            self.assertEqual(artifacts[0]["size_bytes"], 5)
            self.assertEqual(artifacts[0]["digest"], "a" * 64)
            self.assertTrue(artifacts[0]["exists"])

    def test_tmux_console_pretty_prints_matrix_and_artifacts(self):
        stream = io.StringIO()
        console = b.Console(
            stream=stream,
            color="auto",
            environ={"TMUX": "tmux-session", "TERM": "screen-256color"},
            interactive=True,
        )
        report = {
            "run_id": "run-1",
            "status": "built",
            "phase": "complete",
            "duration_seconds": 61,
            "variants": [
                {
                    "line": "cu129",
                    "profile": "torch",
                    "status": "built",
                    "duration_seconds": 60,
                    "artifacts": [
                        {
                            "name": "torch.whl",
                            "size_bytes": 1024,
                            "digest": "abcdef1234567890",
                        }
                    ],
                }
            ],
            "summary_path": "/estate/run/summary.json",
            "events_path": "/estate/run/events.jsonl",
            "result_path": "/estate/run/result.txt",
            "qualification": "not performed",
        }

        console.final(report)

        output = stream.getvalue()
        self.assertEqual(console.mode, "tmux")
        self.assertIn("\x1b[", output)
        self.assertIn("Vaso Foundry build", output)
        self.assertIn("CUDA", output)
        self.assertIn("torch.whl", output)

    def test_banner_explains_cache_state_and_bazel_progress_terms(self):
        stream = io.StringIO()
        console = b.Console(stream=stream, color="never")
        report = {
            "run_id": "run-1",
            "lines": ["cu129"],
            "profiles": ["torch"],
            "summary_path": "/estate/run/summary.json",
            "cache_start": {"mode": "cold"},
        }

        console.banner(report, Path("/estate"))

        output = stream.getvalue()
        self.assertIn("cache    cold", output)
        self.assertIn("local=executing here", output)
        self.assertIn("disk-cache=enabled, not a cache hit", output)

    def test_cache_start_reports_only_selected_bazel_cache_paths(self):
        with tempfile.TemporaryDirectory(dir=TEST_TMP_ROOT) as directory:
            estate = Path(directory)
            cold = b.cache_start_state(estate, ["cu129"], ["torch"])
            self.assertEqual(cold["mode"], "cold")
            self.assertEqual(cold["populated_paths"], [])

            selected = estate / "vaso/lines/cu129/cache/bazel/output-base-torch"
            selected.mkdir(parents=True)
            (selected / "marker").write_text("cached", encoding="utf-8")
            unrelated = estate / "vaso/lines/cu130/cache/bazel/output-base-jax"
            unrelated.mkdir(parents=True)
            (unrelated / "marker").write_text("other", encoding="utf-8")

            warm = b.cache_start_state(estate, ["cu129"], ["torch"])
            self.assertEqual(warm["mode"], "warm")
            self.assertEqual(warm["populated_paths"], [str(selected)])
            self.assertNotIn(str(unrelated), warm["selected_paths"])

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

    def test_finish_and_status_command_preserve_preparation_result(self):
        with tempfile.TemporaryDirectory(dir=TEST_TMP_ROOT) as directory:
            r = self.make_runner(Path(directory))
            r.finish("prepared")

            self.assertTrue(
                all(variant["status"] == "skipped" for variant in r.report["variants"])
            )
            self.assertIn("PREPARED", Path(r.report["result_path"]).read_text())
            stream = io.StringIO()
            with redirect_stdout(stream):
                rc = b.main(
                    [
                        "--estate-root",
                        str(r.estate),
                        "--status",
                        "--color",
                        "never",
                    ]
                )
            self.assertEqual(rc, 0)
            self.assertIn("PREPARED", stream.getvalue())

    def test_new_owner_marks_unfinished_latest_run_abandoned(self):
        with tempfile.TemporaryDirectory(dir=TEST_TMP_ROOT) as directory:
            r = self.make_runner(Path(directory))
            log = r.output / "001-build.log"
            log.write_text("last useful line\n")
            r.report["stages"].append(
                {"name": "build", "status": "running", "log": str(log)}
            )
            r.report["variants"][0]["status"] = "running"
            r.save()

            report = b.abandon_stale_run(r.state_root)

            self.assertIsNotNone(report)
            self.assertEqual(report["status"], "abandoned")
            self.assertEqual(report["stages"][0]["status"], "abandoned")
            self.assertEqual(report["variants"][0]["status"], "abandoned")
            self.assertIn("last useful line", report["stages"][0]["tail"])
            self.assertIn("ABANDONED", Path(report["result_path"]).read_text())

    def test_source_edits_are_detected(self):
        with tempfile.TemporaryDirectory(dir=TEST_TMP_ROOT) as directory:
            root = Path(directory)
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            path = root / "source"
            path.write_text("before")
            r = b.Runner.__new__(b.Runner)
            r.root, r.identity = root, b.source_identity(root)
            r.check_source()
            path.write_text("after")
            with self.assertRaisesRegex(RuntimeError, "checkout changed"):
                r.check_source()

    def test_bad_download_cannot_become_a_cached_input(self):
        with tempfile.TemporaryDirectory(dir=TEST_TMP_ROOT) as directory:
            r = self.make_runner(Path(directory))
            r.args.fetch = True
            r.available = lambda _: None
            asset = b.Asset("archive", "a" * 64, ("https://example.org/archive",))

            def bad_download(*_):
                (
                    r.estate
                    / "vaso/cache/build-inputs"
                    / (asset.sha256 + "." + r.output.name + ".part")
                ).write_bytes(b"wrong bytes")

            r.stage = bad_download
            with self.assertRaisesRegex(RuntimeError, "cannot acquire"):
                r.prepare_asset(asset)
            self.assertFalse((r.estate / "vaso/cache/bazel/repository-cache").exists())
            self.assertEqual(len(r.report["integrity_failures"]), 1)
            self.assertFalse(
                list((r.estate / "vaso/cache/build-inputs").glob("*.part"))
            )

    def test_all_variants_are_serial_offline_and_token_enabled(self):
        with tempfile.TemporaryDirectory(dir=TEST_TMP_ROOT) as directory:
            r = self.make_runner(Path(directory))
            calls = []
            r.stage = lambda name, command, line, profile: calls.append(
                (name, command, line, profile)
            )
            r.prepare_asset = lambda _: None
            r.available = lambda _: Path(directory)
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
            self.assertEqual(len(r.report["variants"]), 4)
            self.assertEqual(r.report["status"], "built")
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
            r.stage = lambda *args: calls.append(args)
            r.available = lambda _: Path(directory)
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
            self.assertEqual(r.report["status"], "prepared")
            self.assertTrue(
                all(variant["status"] == "pending" for variant in r.report["variants"])
            )


if __name__ == "__main__":
    unittest.main()
