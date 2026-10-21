#!/usr/bin/env python3
"""Host-side contract tests for the unattended speedrun launcher."""

from __future__ import annotations

import argparse
import os
import tempfile
import subprocess
import sys
import time
import json
import shutil
import unittest
from pathlib import Path
from unittest import mock

from workloads.nanogpt_speedrun import orchestrate


class OrchestrateTest(unittest.TestCase):
    def _args(self, estate_root: Path) -> argparse.Namespace:
        return argparse.Namespace(
            estate_root=estate_root,
            line="cu129",
            mode="smoke",
            backend="all",
            arm="all",
            families="gpt,qwen3",
            gpus=1,
            agent="test-agent",
        )

    def test_preflight_requires_both_selected_output_bases(self) -> None:
        with tempfile.TemporaryDirectory(dir=os.environ.get("TMPDIR")) as raw_root:
            root = Path(raw_root)
            (root / "rootfs-lines/cu129").mkdir(parents=True)
            (root / "rootfs-lines/cu129/rootfs-bundle.json").write_text(
                "{}\n", encoding="utf-8"
            )
            torch_base = root / "vaso/lines/cu129/cache/bazel/output-base-torch"
            jax_base = root / "vaso/lines/cu129/cache/bazel/output-base-jax"
            torch_base.mkdir(parents=True)

            with self.assertRaisesRegex(ValueError, "missing jax output base"):
                orchestrate._preflight(self._args(root), ("torch", "jax"))

            jax_base.mkdir(parents=True)
            orchestrate._preflight(self._args(root), ("torch", "jax"))

    def test_profile_environment_selects_profile_owned_output_base(self) -> None:
        with tempfile.TemporaryDirectory(dir=os.environ.get("TMPDIR")) as raw_root:
            root = Path(raw_root)
            environment = orchestrate._profile_environment(
                self._args(root), profile="jax"
            )

        self.assertEqual(environment["VASO_PROFILE"], "jax")
        self.assertEqual(
            environment["VASO_BAZEL_OB"],
            str(root / "vaso/lines/cu129/cache/bazel/output-base-jax"),
        )
        self.assertEqual(environment["VASO_NANOGPT_FAMILIES"], "gpt,qwen3")

    def test_result_loading_retains_failures(self) -> None:
        with tempfile.TemporaryDirectory(dir=os.environ.get("TMPDIR")) as raw_root:
            root = Path(raw_root)
            (root / "torch").mkdir()
            orchestrate._write_json(
                root / "torch/W7a.json", {"id": "W7a", "verdict": "passed"}
            )
            orchestrate._write_json(
                root / "torch/W7b.json", {"id": "W7b", "verdict": "failed"}
            )

            results, failures = orchestrate._load_workload_results(root, ("torch",))

        self.assertEqual([result["id"] for result in results], ["W7a", "W7b"])
        self.assertEqual(failures, ["W7b"])

    def test_optimized_profile_command_selects_only_candidate_workload(self) -> None:
        args = self._args(Path("/estate"))
        args.arm = "optimized"

        command = orchestrate._profile_command(
            args,
            profile="torch",
            sandbox_out="/vaso/out",
            git_commit="0123456789abcdef",
        )

        self.assertEqual(command[-1], "W7b")
        self.assertIn("VASO_NANOGPT_MODE=smoke", command)
        self.assertIn("VASO_NANOGPT_FAMILIES=gpt,qwen3", command)
        self.assertIn("VASO_SOURCE_GIT_COMMIT=0123456789abcdef", command)

    def test_resume_rejects_changed_result_configuration(self) -> None:
        args = self._args(Path("/estate"))
        recorded = orchestrate._run_configuration(args)
        state = {"configuration": recorded}
        args.mode = "benchmark"

        mismatches = orchestrate._resume_argument_mismatches(
            state, orchestrate._run_configuration(args)
        )

        self.assertEqual(mismatches, ["mode: recorded='smoke' requested='benchmark'"])

    def test_resume_archives_prior_summary_and_profile_output(self) -> None:
        with tempfile.TemporaryDirectory(dir=os.environ.get("TMPDIR")) as raw_root:
            root = Path(raw_root)
            orchestrate._write_json(root / "summary.json", {"state": "failed"})
            orchestrate._write_json(root / "jax/W7d.json", {"verdict": "failed"})
            state = {
                "state": "failed",
                "failure": "W7d failed",
                "finished_utc": "2026-10-20T00:00:00Z",
            }

            record = orchestrate._archive_previous_summary(root, state)
            archived_output = orchestrate._archive_profile_output(
                root, profile="jax", retry_number=1
            )

            self.assertFalse((root / "summary.json").exists())
            self.assertFalse((root / "jax").exists())
            self.assertEqual(
                record["summary"], str(root / "history/launcher/summary-01.json")
            )
            self.assertEqual(archived_output, root / "history/jax/attempt-01")
            assert archived_output is not None
            self.assertTrue((archived_output / "W7d.json").is_file())

    @mock.patch("workloads.nanogpt_speedrun.orchestrate.subprocess.run")
    def test_gpu_snapshot_is_structured_and_non_fatal(self, run: mock.Mock) -> None:
        run.return_value = mock.Mock(
            returncode=0,
            stdout=(
                "2026/10/20 09:00:00.000, 2, GPU-id, NVIDIA B200, "
                "00000000:01:00.0, 580.82.07, P0, 183359, 1024, 91, 42, 850.0\n"
            ),
            stderr="",
        )

        snapshot = orchestrate._gpu_snapshot()

        self.assertTrue(snapshot["available"])
        self.assertEqual(snapshot["gpus"][0]["index"], "2")
        self.assertEqual(snapshot["gpus"][0]["memory.used"], "1024")


    def test_missing_selected_reports_fail_and_subset_only_requires_selected_arm(self) -> None:
        with tempfile.TemporaryDirectory(dir=os.environ.get("TMPDIR")) as raw_root:
            root = Path(raw_root)
            _, failures = orchestrate._load_workload_results(root, ("torch",))
            self.assertEqual(failures, ["W7a", "W7b"])
            orchestrate._write_json(root / "torch/W7b.json", {"id": "W7b", "verdict": "passed"})
            _, failures = orchestrate._load_workload_results(root, ("torch",), "optimized")
            self.assertEqual(failures, [])
            (root / "torch/W7b.json").write_text("invalid json")
            _, failures = orchestrate._load_workload_results(root, ("torch",), "optimized")
            self.assertEqual(failures, ["W7b"])

    def test_stage_deadline_drains_partial_lines_and_returns_timeout(self) -> None:
        with tempfile.TemporaryDirectory(dir=os.environ.get("TMPDIR")) as raw_root:
            log = Path(raw_root) / "stage.log"
            started = time.monotonic()
            status = orchestrate._stream_command(
                [sys.executable, "-c", "import time; print('partial',end='',flush=True); time.sleep(60)"],
                environment=dict(os.environ), log_path=log, status_interval=0.1,
                label="test", color=False, timeout_seconds=0.3,
            )
            self.assertEqual(status, 124)
            self.assertLess(time.monotonic() - started, 4)
            self.assertIn("partial", log.read_text())
            self.assertIn("stage timeout", log.read_text())

    def test_stage_cancellation_and_child_exit_are_propagated(self) -> None:
        with tempfile.TemporaryDirectory(dir=os.environ.get("TMPDIR")) as raw_root:
            log = Path(raw_root) / "stage.log"
            for code, cancelled, expected in (("import time; time.sleep(60)", lambda: True, 130),
                                               ("raise SystemExit(23)", lambda: False, 23)):
                status = orchestrate._stream_command(
                    [sys.executable, "-c", code], environment=dict(os.environ), log_path=log,
                    status_interval=30, label="test", color=False, timeout_seconds=2,
                    cancelled=cancelled,
                )
                self.assertEqual(status, expected)

    def test_public_launcher_retains_preflight_failure_and_failed_attestation(self) -> None:
        with tempfile.TemporaryDirectory(dir=os.environ.get("TMPDIR")) as raw_root:
            root = Path(raw_root)
            # Use a real minimal checkout so this public-entrypoint test also works in Bazel runfiles.
            checkout = root / "checkout"
            destination = checkout / "foundry/workloads/nanogpt_speedrun"
            destination.mkdir(parents=True)
            for name in ("run.sh", "orchestrate.py", "attest.py", "common.py", "spec.py", "__init__.py"):
                shutil.copyfile(Path(orchestrate.__file__).with_name(name), destination / name)
            subprocess.run(["git", "init", "-q", str(checkout)], check=True, capture_output=True)
            subprocess.run(["git", "-C", str(checkout), "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
                            "-c", "commit.gpgsign=false", "commit", "--allow-empty", "-qm", "fixture"], check=True, capture_output=True)
            completed = subprocess.run(
                ["bash", str(destination / "run.sh"),
                 "--estate-root", str(root), "--agent", "test", "--run-id", "preflight", "--color", "never"],
                capture_output=True, text=True, timeout=30,
            )
            self.assertNotEqual(completed.returncode, 0, completed.stdout)
            attempt = root / "agents/test/nanogpt-speedrun/preflight"
            state = json.loads((attempt / "state.json").read_text())
            self.assertEqual(state["state"], "failed")
            self.assertIn("rootfs", state["failure"])
            report = orchestrate.attest.verify_bundle(attempt / "attestation")
            self.assertEqual(report["verdict"], "failed")

    def test_launcher_rejects_path_traversal_and_invalid_configuration(self) -> None:
        for arguments in (("--agent", "../escape"), ("--run-id", "../../escape"),
                          ("--families", "gpt,gpt"), ("--families", "unknown"),
                          ("--timeout-seconds", "nan"), ("--max-attempts", "0")):
            with self.subTest(arguments=arguments):
                self.assertEqual(orchestrate.main(["--estate-root", "/unused", *arguments]), 2)

    def test_build_input_capture_requires_unambiguous_built_provider(self) -> None:
        with tempfile.TemporaryDirectory(dir=os.environ.get("TMPDIR")) as raw_root:
            root = Path(raw_root)
            args = self._args(root)
            inputs = root / "inputs"
            with self.assertRaisesRegex(ValueError, "expected one pytorch provider"):
                orchestrate._capture_build_inputs(args, ("torch",), inputs)
            base = root / "vaso/lines/cu129/cache/bazel/output-base-torch/execroot/_main/bazel-out/k8/bin/native"
            for package in ("pytorch", "triton"):
                directory = base / package
                (directory / f"{package}_action_prefix").mkdir(parents=True)
                orchestrate._write_json(directory / f"{package}_action_provider_metadata.json",
                                        {"action_input_digest": "a" * 64})
                orchestrate._write_json(directory / f"{package}_action_build_plan.json", {"plan": "pinned"})
            orchestrate._capture_build_inputs(args, ("torch",), inputs)
            self.assertTrue((inputs / "torch/pytorch_action_provider_metadata.json").is_file())
            orchestrate._write_json(base / "pytorch/pytorch_action_build_plan.json", {"plan": "changed"})
            with self.assertRaisesRegex(ValueError, "differs from the original"):
                orchestrate._capture_build_inputs(args, ("torch",), inputs)

    @mock.patch("workloads.nanogpt_speedrun.orchestrate._checkout_identity")
    def test_resume_limit_preserves_previous_failed_attempt(self, identity: mock.Mock) -> None:
        with tempfile.TemporaryDirectory(dir=os.environ.get("TMPDIR")) as raw_root:
            root = Path(raw_root)
            arguments = ["--estate-root", str(root), "--agent", "test", "--run-id", "bounded", "--max-attempts", "1"]
            identity.return_value = {"git_commit": "a" * 40, "worktree_sha256": "b" * 64}
            self.assertEqual(orchestrate.main(arguments), 1)
            attempt = root / "agents/test/nanogpt-speedrun/bounded"
            before = (attempt / "summary.json").read_bytes()
            self.assertEqual(orchestrate.main([*arguments, "--resume"]), 2)
            self.assertEqual((attempt / "summary.json").read_bytes(), before)

    def test_resume_archives_attestation_with_summary(self) -> None:
        with tempfile.TemporaryDirectory(dir=os.environ.get("TMPDIR")) as raw_root:
            root = Path(raw_root)
            orchestrate._write_json(root / "summary.json", {"state": "failed"})
            orchestrate._write_json(root / "attestation/report.json", {"verdict": "failed"})
            record = orchestrate._archive_previous_summary(root, {"state": "failed"})
            self.assertEqual(record["attestation"], str(root / "history/launcher/attestation-01"))
            self.assertTrue((root / "history/launcher/attestation-01/report.json").is_file())


    @mock.patch("workloads.nanogpt_speedrun.orchestrate._gpu_snapshot", return_value={"available": False})
    @mock.patch("workloads.nanogpt_speedrun.orchestrate._capture_build_inputs")
    @mock.patch("workloads.nanogpt_speedrun.orchestrate._checkout_identity")
    def test_zero_exit_without_reports_fails_final_state(self, identity, capture, gpu) -> None:
        del gpu
        with tempfile.TemporaryDirectory(dir=os.environ.get("TMPDIR")) as raw_root:
            root = Path(raw_root)
            (root / "rootfs-lines/cu129").mkdir(parents=True)
            (root / "rootfs-lines/cu129/rootfs-bundle.json").write_text("{}")
            for profile in ("torch", "jax"):
                (root / f"vaso/lines/cu129/cache/bazel/output-base-{profile}").mkdir(parents=True)
            identity.return_value = {"git_commit": "a" * 40, "worktree_sha256": "b" * 64}
            with mock.patch.object(orchestrate, "_stream_command", return_value=0):
                code = orchestrate.main(["--estate-root", str(root), "--agent", "test", "--run-id", "empty"])
            self.assertEqual(code, 1)
            attempt = root / "agents/test/nanogpt-speedrun/empty"
            self.assertEqual(orchestrate._read_json(attempt / "state.json")["state"], "failed")
            self.assertEqual(orchestrate._read_json(attempt / "summary.json")["failed_workloads"],
                             ["W7a", "W7b", "W7c", "W7d"])
            with mock.patch.object(orchestrate, "_stream_command", side_effect=OSError("cannot spawn")):
                code = orchestrate.main(["--estate-root", str(root), "--agent", "test", "--run-id", "spawn-error"])
            self.assertEqual(code, 1)
            failed = root / "agents/test/nanogpt-speedrun/spawn-error"
            state = orchestrate._read_json(failed / "state.json")
            self.assertEqual(state["stages"]["torch"]["state"], "failed")
            report = orchestrate.attest.verify_bundle(failed / "attestation")
            self.assertIn("cannot spawn", str(report["errors"]))

    @mock.patch("workloads.nanogpt_speedrun.orchestrate._gpu_snapshot", return_value={"available": False})
    @mock.patch("workloads.nanogpt_speedrun.orchestrate._capture_build_inputs")
    @mock.patch("workloads.nanogpt_speedrun.orchestrate._checkout_identity")
    def test_successful_launcher_requires_replayable_case_evidence(self, identity, capture, gpu) -> None:
        from workloads.nanogpt_speedrun.attest_test import fixture
        del gpu
        with tempfile.TemporaryDirectory(dir=os.environ.get("TMPDIR")) as raw_root:
            root = Path(raw_root)
            prepared = root / "prepared"
            fixture(prepared)
            def retain_inputs(args, profiles, destination):
                del args
                for profile in profiles:
                    for package in (("pytorch", "triton") if profile == "torch" else ("jaxlib",)):
                        orchestrate._write_json(destination / profile / f"{package}_action_provider_metadata.json",
                                                {"action_input_digest": "c" * 64})
                        orchestrate._write_json(destination / profile / f"{package}_action_build_plan.json", {})
            capture.side_effect = retain_inputs
            (root / "rootfs-lines/cu129").mkdir(parents=True)
            (root / "rootfs-lines/cu129/rootfs-bundle.json").write_text("{}")
            for profile in ("torch", "jax"):
                (root / f"vaso/lines/cu129/cache/bazel/output-base-{profile}").mkdir(parents=True)
            identity.return_value = {"git_commit": "a" * 40, "worktree_sha256": "b" * 64}
            attempt = root / "agents/test/nanogpt-speedrun/valid"
            def complete_stage(command, **kwargs):
                del command
                profile = kwargs["label"]
                shutil.copytree(prepared / profile, attempt / profile)
                kwargs["log_path"].parent.mkdir(exist_ok=True)
                kwargs["log_path"].write_text("fixture stage\n")
                return 0
            with mock.patch.object(orchestrate, "_stream_command", side_effect=complete_stage):
                code = orchestrate.main(["--estate-root", str(root), "--agent", "test", "--run-id", "valid", "--mode", "benchmark"])
            self.assertEqual(code, 0)
            self.assertEqual(orchestrate._read_json(attempt / "state.json")["state"], "passed")
            self.assertEqual(orchestrate.attest.verify_bundle(attempt / "attestation")["verdict"], "passed")


if __name__ == "__main__":
    unittest.main()
