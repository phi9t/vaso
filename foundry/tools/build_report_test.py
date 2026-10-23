"""Durable report selection and placement-independent attempt paths."""

import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
TEST_TMP_ROOT = Path(
    os.environ.get("TEST_TMPDIR") or os.environ.get("TMPDIR") or tempfile.gettempdir()
)
sys.path.insert(0, str(HERE))
import build_report as b


class BuildReportTest(unittest.TestCase):
    def test_latest_pointer_wins_over_modified_historical_summary(self):
        with tempfile.TemporaryDirectory(dir=TEST_TMP_ROOT) as directory:
            root = Path(directory)
            selected = root / "active" / "summary.json"
            historical = root / "historical" / "summary.json"
            b.atomic_json(selected, {"status": "running"})
            b.atomic_json(historical, {"status": "built"})
            os.utime(selected, (1, 1))
            os.utime(historical, (2, 2))
            b.atomic_json(root / "latest.json", {"summary": str(selected)})

            report, path = b.latest_report(root)

            self.assertEqual(path, selected)
            self.assertEqual(report["status"], "running")
            self.assertEqual(report["run_id"], "active")

    def test_selected_summary_does_not_require_access_to_legacy_history(self):
        with tempfile.TemporaryDirectory(dir=TEST_TMP_ROOT) as directory:
            root = Path(directory)
            selected = root / "active" / "summary.json"
            b.atomic_json(selected, {"status": "running"})
            b.atomic_json(root / "latest.json", {"summary": str(selected)})

            with patch.object(
                Path, "glob", side_effect=PermissionError("history is unavailable")
            ):
                report, path = b.latest_report(root)

            self.assertEqual(path, selected)
            self.assertEqual(report["status"], "running")

    def test_legacy_scan_used_when_pointer_or_selected_summary_is_missing(self):
        with tempfile.TemporaryDirectory(dir=TEST_TMP_ROOT) as directory:
            root = Path(directory)
            older = root / "older" / "summary.json"
            newer = root / "newer" / "summary.json"
            b.atomic_json(older, {"status": "failed"})
            b.atomic_json(newer, {"status": "built"})
            os.utime(older, (1, 1))
            os.utime(newer, (2, 2))
            for pointer in (None, {"summary": str(root / "missing/summary.json")}):
                with self.subTest(pointer=pointer):
                    if pointer is not None:
                        b.atomic_json(root / "latest.json", pointer)
                    report, path = b.latest_report(root)
                    self.assertEqual(path, newer)
                    self.assertEqual(report["status"], "built")

    def test_invalid_selected_summary_does_not_fall_back_to_success(self):
        with tempfile.TemporaryDirectory(dir=TEST_TMP_ROOT) as directory:
            root = Path(directory)
            selected = root / "active" / "summary.json"
            b.atomic_json(selected, {"status": "running"})
            selected.write_text("[]")
            b.atomic_json(root / "historical/summary.json", {"status": "built"})
            b.atomic_json(root / "latest.json", {"summary": str(selected)})

            with self.assertRaisesRegex(ValueError, "expected a JSON object"):
                b.latest_report(root)

    def test_failed_publication_preserves_previous_report_and_cleans_partial_files(
        self,
    ):
        with tempfile.TemporaryDirectory(dir=TEST_TMP_ROOT) as directory:
            root = Path(directory)
            for writer, value in (
                (b.atomic_json, {"status": "passed"}),
                (b.atomic_text, "PASSED"),
            ):
                with self.subTest(writer=writer.__name__):
                    path = root / "report"
                    path.write_bytes(b"previous report")
                    with patch.object(
                        b.os, "replace", side_effect=OSError("cannot publish")
                    ):
                        with self.assertRaisesRegex(OSError, "cannot publish"):
                            writer(path, value)
                    self.assertEqual(path.read_bytes(), b"previous report")
                    self.assertEqual(list(root.iterdir()), [path])
                    writer(path, value)
                    self.assertNotEqual(path.read_bytes(), b"previous report")

    def test_selected_directory_owns_evidence_paths(self):
        with tempfile.TemporaryDirectory(dir=TEST_TMP_ROOT) as directory:
            root = Path(directory)
            output = root / "selected"
            b.atomic_json(
                output / "summary.json",
                {
                    "status": "running",
                    "summary_path": "/other/summary.json",
                    "events_path": "/other/events.jsonl",
                    "result_path": "/other/result.txt",
                },
            )
            report, summary = b.latest_report(root)
            self.assertEqual(summary, output / "summary.json")
            for field, path in b.report_paths(output).items():
                self.assertEqual(report[field], path)


if __name__ == "__main__":
    unittest.main()
