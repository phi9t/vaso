#!/usr/bin/env python3
"""Evidence acceptance and portable replay, without importing GPU frameworks."""
from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from workloads.nanogpt_speedrun import attest
from workloads.nanogpt_speedrun.common import case_checks, compare_cases, measured_step_stats
from workloads.nanogpt_speedrun.spec import FAMILIES, family_spec, run_spec


def write(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, allow_nan=False))


def fixture(root: Path, *, mode: str = "benchmark", backend: str = "all") -> dict:
    configuration = {"backend": backend, "arm": "all", "families": list(FAMILIES),
                     "mode": mode, "line": "cu129", "gpus": 1}
    checkout = {"git_commit": "a" * 40, "worktree_sha256": "b" * 64}
    state = {"configuration": configuration, "checkout": checkout, "state": "passed",
             "run_id": "fixture", "stages": {}}
    results = []
    run = run_spec(mode)
    for key in attest.expected_workloads(configuration):
        profile, arm = attest.WORKLOADS[key]
        state["stages"][profile] = {"state": "passed", "exit_code": 0}
        cases = []
        comparisons = []
        for family in FAMILIES:
            for candidate in (("baseline",) if arm == "baseline" else ("baseline", "triton")):
                # A valid optimization can be slower: correctness is a separate gate.
                samples = [0.01 if candidate == "baseline" else 0.011] * run.steps
                spec = family_spec(family, num_heads=run.num_heads, num_layers=run.num_layers)
                case = {"backend": profile, "arm": candidate, "family": family, "mode": mode,
                        "cuda_line": "cu129", "run_spec": run.as_dict(),
                        "family_spec": json.loads(json.dumps(spec.as_dict())),
                        "source": {"git_commit": checkout["git_commit"], "workload_sha256": "c" * 64},
                        "runtime": {"version": "built", "module_file": "/vaso-bazel-ob/prefix/pkg/__init__.py"},
                        "execution": {"device": "gpu", "optimizer": "AdamW"},
                        "parameter_count": 100, "losses": [4.0] * run.steps,
                        "timing": {**measured_step_stats(samples, run), "step_seconds": samples,
                                   "wall_time_seconds": sum(samples)}, "verdict": "passed"}
                if candidate == "triton":
                    component = {"executed": True, "max_abs_error": 0.0, "tolerance": 1e-5,
                                 "forward_max_abs_error": 0.0, "backward_max_abs_error": 0.0}
                    case["optimization"] = {"executed": True, "max_abs_error": 0.0, "tolerance": 1e-5,
                                            "activation": {**component, "compiled_text_sha256": "d" * 64,
                                                           "lowering_markers": ["pallas"]},
                                            "attention": component.copy() if "softmax" in spec.attention_pattern else
                                                {"executed": False, "reason": "no softmax"}}
                case["checks"] = case_checks(case)
                cases.append(case)
            if arm == "triton":
                comparisons.append({"family": family, **compare_cases(cases[-2], cases[-1])})
        result = {"id": key, "backend": profile, "arm": arm, "mode": mode, "line": "cu129",
                  "families": list(FAMILIES), "verdict": "passed", "state": "completed",
                  "returncode": 0, "failed_criteria": [], "criteria": {"completed": True},
                  "cases": cases, "comparisons": comparisons}
        write(root / profile / f"{key}.json", result)
        for name in ("stdout.txt", "stderr.txt"):
            path = root / profile / key / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("")
        path = root / "logs" / f"{profile}.log"
        path.parent.mkdir(exist_ok=True)
        path.write_text("fixture execution\n")
        results.append(result)
    write(root / "state.json", state)
    summary = {"run_id": "fixture", "state": "passed", "failed_workloads": [], "results": results}
    write(root / "summary.json", summary)
    return summary


class AttestTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(dir=os.environ.get("TMPDIR"))
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.attempt = self.root / "attempt"
        fixture(self.attempt)

    def edit_result(self, mutation, key="W7b") -> None:
        summary = attest.read_json(self.attempt / "summary.json")
        result = next(r for r in summary["results"] if r["id"] == key)
        mutation(result)
        write(self.attempt / attest.WORKLOADS[key][0] / f"{key}.json", result)
        write(self.attempt / "summary.json", summary)

    def assert_failed(self, text: str) -> None:
        report = attest.evaluate(self.attempt)
        self.assertEqual(report["verdict"], "failed")
        self.assertIn(text, str(report["errors"]))

    def test_full_matrix_accepts_slower_optimized_arms(self) -> None:
        report = attest.evaluate(self.attempt)
        self.assertEqual(report["verdict"], "passed", report["errors"])
        self.assertEqual(len(report["comparisons"]), 8)
        self.assertTrue(all(not row["faster"] for row in report["comparisons"]))

    def test_empty_success_summary_cannot_attest(self) -> None:
        write(self.attempt / "summary.json", {"state": "passed", "run_id": "fixture", "failed_workloads": [], "results": []})
        self.assert_failed("coverage mismatch")

    def test_missing_report_cannot_attest(self) -> None:
        (self.attempt / "jax/W7d.json").unlink()
        self.assert_failed("W7d")

    def test_duplicate_case_cannot_replace_missing_family(self) -> None:
        self.edit_result(lambda r: r["cases"].__setitem__(-1, copy.deepcopy(r["cases"][0])))
        self.assert_failed("missing or duplicate cases")

    def test_incomplete_timing_and_forged_throughput_fail(self) -> None:
        for mutation, message in (
            (lambda c: c["timing"]["step_seconds"].pop(), "incomplete or invalid timing"),
            (lambda c: c["timing"].__setitem__("tokens_per_second", 1e20), "disagrees with samples"),
            (lambda c: c["losses"].pop(), "incomplete losses"),
            (lambda c: c["source"].__setitem__("git_commit", "e" * 40), "source commit mismatch"),
            (lambda c: c["family_spec"].__setitem__("num_experts", 99), "family specification"),
        ):
            with self.subTest(message=message):
                fixture(self.attempt)
                self.edit_result(lambda r: mutation(r["cases"][0]))
                self.assert_failed(message)

    def test_skipped_activation_and_inflated_tolerance_fail(self) -> None:
        self.edit_result(lambda r: r["cases"][1]["optimization"]["activation"].__setitem__("executed", False))
        self.assert_failed("activation execution mismatch")
        fixture(self.attempt)
        self.edit_result(lambda r: r["cases"][1]["optimization"]["activation"].__setitem__("tolerance", 1.0))
        self.assert_failed("inflated activation tolerance")

    def test_forged_comparison_and_summary_disagreement_fail(self) -> None:
        self.edit_result(lambda r: r["comparisons"][0].__setitem__("speedup", 10.0))
        self.assert_failed("comparison speedup disagrees")
        fixture(self.attempt)
        path = self.attempt / "torch/W7b.json"
        result = attest.read_json(path)
        result["verdict"] = "failed"
        write(path, result)
        self.assert_failed("summary differs")

    def test_subset_needs_explicit_policy(self) -> None:
        fixture(self.attempt, backend="torch")
        self.assert_failed("full matrix required")
        report = attest.evaluate(self.attempt, require_full=False)
        self.assertEqual(report["verdict"], "passed", report["errors"])
        self.assertEqual(report["scope"], "selected-subset")

    def test_smoke_never_claims_speedup(self) -> None:
        fixture(self.attempt, mode="smoke")
        report = attest.evaluate(self.attempt)
        self.assertEqual(report["verdict"], "passed", report["errors"])
        self.assertEqual(report["performance"], "smoke-only")
        self.assertTrue(all("no speedup claim" in r["claim"] for r in report["comparisons"]))

    def test_bundle_replays_after_move_and_detects_changed_evidence(self) -> None:
        output = self.root / "bundle"
        report = attest.create_bundle(self.attempt, output)
        moved = self.root / "moved"
        output.rename(moved)
        self.assertEqual(attest.verify_bundle(moved), report)
        completed = subprocess.run(
            [sys.executable, "-B", str(moved / "verifier/workloads/nanogpt_speedrun/attest.py"),
             "--verify-bundle", str(moved)], capture_output=True, text=True,
            env={key: value for key, value in os.environ.items() if key != "PYTHONPATH"},
            cwd=self.root,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        (moved / "evidence/torch/W7b.json").write_text("{}")
        with self.assertRaisesRegex(ValueError, "digest mismatch"):
            attest.verify_bundle(moved)

    def test_rehashed_forged_report_still_fails_replay(self) -> None:
        output = self.root / "bundle"
        attest.create_bundle(self.attempt, output)
        report = attest.read_json(output / "report.json")
        report["comparisons"][0]["speedup"] = 999
        write(output / "report.json", report)
        manifest = attest.read_json(output / "manifest.json")
        manifest["sha256"]["report.json"] = attest.sha256(output / "report.json")
        write(output / "manifest.json", manifest)
        with self.assertRaisesRegex(ValueError, "disagrees with replayed"):
            attest.verify_bundle(output)

    def test_missing_inputs_leave_failed_bundle_and_refuse_overwrite(self) -> None:
        output = self.root / "failed"
        report = attest.create_bundle(self.root / "absent", output)
        self.assertEqual(report["verdict"], "failed")
        self.assertTrue((output / "manifest.json").is_file())
        with self.assertRaises(FileExistsError):
            attest.create_bundle(self.attempt, output)

    def test_cli_success_and_failed_replay_exit_status(self) -> None:
        script = Path(attest.__file__)
        output = self.root / "cli"
        completed = subprocess.run([sys.executable, "-B", str(script), "--attempt-dir", str(self.attempt),
                                    "--out", str(output)], capture_output=True, text=True)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        (output / "report.md").write_text("fake report")
        completed = subprocess.run([sys.executable, "-B", str(script), "--verify-bundle", str(output)],
                                   capture_output=True, text=True)
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("digest mismatch", completed.stderr)

    def test_json_nonfinite_and_duplicate_keys_fail(self) -> None:
        for text in ('{"state": NaN}', '{"state":"passed","state":"failed"}'):
            (self.attempt / "summary.json").write_text(text)
            self.assert_failed("JSON" if "NaN" in text else "duplicate")


    def test_missing_logs_and_partial_native_input_records_fail(self) -> None:
        (self.attempt / "torch/W7b/stdout.txt").unlink()
        self.assert_failed("missing execution log")
        fixture(self.attempt)
        write(self.attempt / "inputs/rootfs-bundle.json", {})
        self.assert_failed("invalid retained native input")

    def test_malformed_schema_leaves_failed_report(self) -> None:
        for value in ([], "invalid", None):
            with self.subTest(value=value):
                state = attest.read_json(self.attempt / "state.json")
                state["configuration"] = value
                write(self.attempt / "state.json", state)
                report = attest.evaluate(self.attempt)
                self.assertEqual(report["verdict"], "failed")
                self.assertIn("failed", attest.render(report))


if __name__ == "__main__":
    unittest.main()
