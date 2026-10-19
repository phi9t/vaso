#!/usr/bin/env python3
"""Exercise runtime CPU selection independently of native action identity."""

import importlib.util
import os
from pathlib import Path
import tempfile
import unittest


spec = importlib.util.spec_from_file_location("runtime_jobs_executor", Path(__file__).with_name("runtime_jobs_executor.py"))
executor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(executor)


class RuntimeJobsExecutorTest(unittest.TestCase):
    def test_owner_budget_reaches_executor_without_changing_its_arguments(self):
        with tempfile.TemporaryDirectory(dir=os.environ.get("TEST_TMPDIR")) as directory:
            root = Path(directory)
            jobs = root / "jobs"
            output = root / "observed"
            plan = root / "plan_executor.py"
            plan.write_text("from pathlib import Path\ndef job_count(): return '4'\ndef main(args):\n Path(args[0]).write_text(job_count())\n return 23\n")
            for value in ("1", "64", "96"):
                with self.subTest(value=value):
                    jobs.write_text(value + "\n")
                    result = executor.main(["--executor", str(plan), "--runtime-jobs-file", str(jobs), "--", str(output)])
                    self.assertEqual(result, 23)
                    self.assertEqual(output.read_text(), value)

    def test_invalid_or_missing_budget_stops_before_executor_launch(self):
        with tempfile.TemporaryDirectory(dir=os.environ.get("TEST_TMPDIR")) as directory:
            jobs = Path(directory) / "jobs"
            for value in ("", "0", "-1", "97", "1.5", "two"):
                with self.subTest(value=value):
                    jobs.write_text(value)
                    with self.assertRaises(ValueError):
                        executor.main(["--executor", "/missing/executor.py", "--runtime-jobs-file", str(jobs)])
            jobs.unlink()
            with self.assertRaises(FileNotFoundError):
                executor.read_jobs(jobs)


if __name__ == "__main__":
    unittest.main()
