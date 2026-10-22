"""Readable progress and artifact extraction from build events."""

import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
TEST_TMP_ROOT = Path(os.environ.get("TEST_TMPDIR") or os.environ.get("TMPDIR") or HERE)
sys.path.insert(0, str(HERE))
import build_progress as b


class BuildProgressTest(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
