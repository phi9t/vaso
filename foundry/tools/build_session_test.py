"""Durable build stages, source identity, and estate ownership."""

import fcntl
import io
import json
import os
from pathlib import Path
import signal
import sys
import subprocess
import tempfile
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
TEST_TMP_ROOT = Path(os.environ.get("TEST_TMPDIR") or os.environ.get("TMPDIR") or tempfile.gettempdir())
sys.path.insert(0, str(HERE))
import build_session as b


class BuildSessionTest(unittest.TestCase):
    def make_session(self, temp):
        repo = temp / "repo"
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        return b.BuildSession(
            repo,
            temp / "new",
            ["cu129", "cu130"],
            ["torch", "jax"],
            {"torch": [], "jax": []},
            console=b.Console(stream=io.StringIO(), color="never"),
            status_interval=0.02,
        )

    def test_failed_command_retains_log_and_nonzero_status(self):
        with tempfile.TemporaryDirectory(dir=TEST_TMP_ROOT) as directory:
            r = self.make_session(Path(directory))
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

    def test_stage_cannot_wait_for_interactive_input(self):
        with tempfile.TemporaryDirectory(dir=TEST_TMP_ROOT) as directory:
            r = self.make_session(Path(directory))
            r.stage(
                "stdin",
                [sys.executable, "-c", "import sys; assert sys.stdin.read() == ''"],
                "cu129",
                "torch",
            )
            self.assertEqual(r.report["stages"][0]["status"], "passed")

    def test_interrupted_child_is_not_retried_as_an_acquisition_failure(self):
        with tempfile.TemporaryDirectory(dir=TEST_TMP_ROOT) as directory:
            r = self.make_session(Path(directory))
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

    def test_cancellation_stops_a_child_that_ignores_sigterm(self):
        with tempfile.TemporaryDirectory(dir=TEST_TMP_ROOT) as directory:
            r = self.make_session(Path(directory))
            ready = Path(directory) / "handlers-ready"
            save = r.save

            def record_started():
                save()
                if r.report["stages"] and "pid" in r.report["stages"][-1]:
                    ready.touch()

            r.save = record_started
            # The child signals its own parent only after installing SIG_IGN.
            # The ready file ensures the parent has installed its handlers too.
            command = [
                sys.executable,
                "-c",
                "import os,signal,time\nfrom pathlib import Path\n"
                "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
                f"while not Path({str(ready)!r}).exists(): time.sleep(0.01)\n"
                "os.kill(os.getppid(), signal.SIGTERM)\ntime.sleep(60)\n",
            ]
            with patch.object(b, "CANCEL_GRACE_SECONDS", 0.1):
                with self.assertRaises(InterruptedError):
                    r.stage("cancel", command, "cu129", "torch")
            stage = r.report["stages"][0]
            self.assertEqual(stage["status"], "interrupted")
            self.assertEqual(stage["returncode"], -signal.SIGKILL)
            with self.assertRaises(ProcessLookupError):
                os.kill(stage["pid"], 0)

    def test_reporting_error_cleans_up_the_owned_process(self):
        with tempfile.TemporaryDirectory(dir=TEST_TMP_ROOT) as directory:
            r = self.make_session(Path(directory))
            save = r.save

            def fail_after_launch():
                if r.report["stages"] and "pid" in r.report["stages"][-1]:
                    r.save = save
                    raise OSError("cannot persist progress")
                save()

            r.save = fail_after_launch
            with self.assertRaises(b.StageFailure):
                r.stage(
                    "report-error",
                    [sys.executable, "-c", "import time; time.sleep(60)"],
                    "cu129",
                    "torch",
                )
            stage = r.report["stages"][0]
            self.assertEqual(stage["status"], "failed")
            self.assertIn("cannot persist progress", stage["error"])
            with self.assertRaises(ProcessLookupError):
                os.kill(stage["pid"], 0)

    def test_stage_heartbeat_and_transitions_are_durable(self):
        with tempfile.TemporaryDirectory(dir=TEST_TMP_ROOT) as directory:
            r = self.make_session(Path(directory))
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
                ["run_started", "stage_started", "stage_finished"],
            )

    def test_stage_inherits_runner_lock_until_child_exits(self):
        with tempfile.TemporaryDirectory(dir=TEST_TMP_ROOT) as directory:
            root = Path(directory)
            r = self.make_session(root)
            with (root / "runner.lock").open("w") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX)
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

    def test_new_owner_marks_unfinished_latest_run_abandoned(self):
        with tempfile.TemporaryDirectory(dir=TEST_TMP_ROOT) as directory:
            r = self.make_session(Path(directory))
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

    def test_stale_recovery_writes_only_to_the_selected_attempt(self):
        with tempfile.TemporaryDirectory(dir=TEST_TMP_ROOT) as directory:
            root = Path(directory)
            r = self.make_session(root)
            foreign = root / "other-estate"
            foreign.mkdir()
            events = foreign / "events.jsonl"
            result = foreign / "result.txt"
            events.write_text("retained events\n")
            result.write_text("retained result\n")
            r.report.update(events_path=str(events), result_path=str(result))
            # Preserve the actual summary location; only recorded output paths
            # belong to the other estate (for example after restoring a run).
            summary = Path(r.report["summary_path"])
            b.atomic_json(summary, r.report)

            report = b.abandon_stale_run(r.state_root)

            self.assertEqual(events.read_text(), "retained events\n")
            self.assertEqual(result.read_text(), "retained result\n")
            self.assertEqual(report["events_path"], str(r.output / "events.jsonl"))
            self.assertEqual(report["result_path"], str(r.output / "result.txt"))
            self.assertIn("ABANDONED", (r.output / "result.txt").read_text())

    def test_source_edits_are_detected(self):
        with tempfile.TemporaryDirectory(dir=TEST_TMP_ROOT) as directory:
            root = Path(directory) / "repo"
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            path = root / "source"
            path.write_text("before")
            r = b.BuildSession(root, root.parent / (root.name + "-estate"), [], [], {})
            r.check_source()
            path.write_text("after")
            with self.assertRaisesRegex(RuntimeError, "checkout changed"):
                r.check_source()


if __name__ == "__main__":
    unittest.main()
