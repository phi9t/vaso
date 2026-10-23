"""Readable progress, durable report selection, and terminal rendering."""

import io
import os
from pathlib import Path
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
TEST_TMP_ROOT = Path(os.environ.get("TEST_TMPDIR") or os.environ.get("TMPDIR") or tempfile.gettempdir())
sys.path.insert(0, str(HERE))
import build_progress as b


class BuildProgressTest(unittest.TestCase):
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

    def test_formatters_keep_unknown_numeric_values_inspectable(self):
        for value in (None, "invalid", float("nan"), float("inf"), float("-inf"), 10**400):
            with self.subTest(value=value):
                self.assertEqual(b.format_duration(value), "-")
                self.assertEqual(b.human_size(value), "-")
        self.assertEqual(b.human_size(-1), "-")
        self.assertEqual(b.format_duration(3661), "1:01:01")
        self.assertEqual(b.format_duration("61.5"), "01:01")
        self.assertEqual(b.human_size(1024), "1.0 KiB")

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


if __name__ == "__main__":
    unittest.main()
