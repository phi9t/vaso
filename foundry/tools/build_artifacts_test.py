"""Release artifact discovery and diagnostics from retained Bazel events."""

import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
TEST_TMP_ROOT = Path(
    os.environ.get("TEST_TMPDIR") or os.environ.get("TMPDIR") or tempfile.gettempdir()
)
sys.path.insert(0, str(HERE))
import build_artifacts as b


class BuildArtifactsTest(unittest.TestCase):
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

    def test_release_wheel_identity_does_not_depend_on_bep_field_split(self):
        with tempfile.TemporaryDirectory(dir=TEST_TMP_ROOT) as directory:
            root = Path(directory)
            events = root / "events.jsonl"
            for target in ("pytorch_release_wheel", "jaxlib_release_wheels"):
                relative = (
                    Path("bazel-out/config/bin/native")
                    / (target + "_wheelhouse")
                    / "package.whl"
                )
                wheel = root / "output-base/execroot/_main" / relative
                wheel.parent.mkdir(parents=True, exist_ok=True)
                wheel.write_bytes(b"wheel")
                expected = None
                for split in range(len(relative.parts)):
                    with self.subTest(target=target, split=split):
                        entry = {
                            "name": str(Path(*relative.parts[split:])),
                            "pathPrefix": list(relative.parts[:split]),
                        }
                        events.write_text(
                            json.dumps({"namedSetOfFiles": {"files": [entry]}}) + "\n"
                        )
                        artifacts = b.build_event_artifacts(
                            events, root / "output-base"
                        )
                        self.assertEqual(len(artifacts), 1)
                        if expected is None:
                            expected = artifacts
                        self.assertEqual(artifacts, expected)

    def test_release_word_in_filename_does_not_identify_a_release_output(self):
        with tempfile.TemporaryDirectory(dir=TEST_TMP_ROOT) as directory:
            root = Path(directory)
            events = root / "events.jsonl"
            events.write_text(
                json.dumps(
                    {
                        "namedSetOfFiles": {
                            "files": [
                                {
                                    "name": "native/dependency/pkg_release_wheel-1-py3-none-any.whl",
                                    "pathPrefix": ["bazel-out", "config", "bin"],
                                }
                            ]
                        }
                    }
                )
                + "\n"
            )
            self.assertEqual(b.build_event_artifacts(events, root / "output-base"), [])

    def test_malformed_events_report_the_file_and_line(self):
        events_to_reject = [
            "not JSON",
            [],
            {"namedSetOfFiles": []},
            {"namedSetOfFiles": {"files": "invalid"}},
            {"namedSetOfFiles": {"files": [None]}},
            {
                "namedSetOfFiles": {
                    "files": [
                        {
                            "name": "native/torch_release_wheel_wheelhouse/torch.whl",
                            "pathPrefix": "bazel-out",
                        }
                    ]
                }
            },
        ]
        wheel = {"name": "native/torch_release_wheel_wheelhouse/torch.whl"}
        for fields in (
            {"pathPrefix": [".."]},
            {"pathPrefix": ["/absolute"]},
            {"name": "native/torch_release_wheel_wheelhouse/invalid\x00.whl"},
            {"length": -1},
            {"length": "invalid"},
            {"length": True},
            {"digest": []},
        ):
            events_to_reject.append({"namedSetOfFiles": {"files": [wheel | fields]}})
        with tempfile.TemporaryDirectory(dir=TEST_TMP_ROOT) as directory:
            root = Path(directory)
            events = root / "events.jsonl"
            for event in events_to_reject:
                with self.subTest(event=event):
                    raw = event if isinstance(event, str) else json.dumps(event)
                    events.write_text("{}\n" + raw + "\n")
                    with self.assertRaises(ValueError) as raised:
                        b.build_event_artifacts(events, root / "output-base")
                    self.assertIn(f"{events}:2", str(raised.exception))

    def test_missing_wheel_keeps_bazel_size_metadata(self):
        with tempfile.TemporaryDirectory(dir=TEST_TMP_ROOT) as directory:
            root = Path(directory)
            events = root / "events.jsonl"
            events.write_text(
                json.dumps(
                    {
                        "namedSetOfFiles": {
                            "files": [
                                {
                                    "name": "native/torch_release_wheel_wheelhouse/torch.whl",
                                    "pathPrefix": ["bazel-out", "bin"],
                                    "length": "123",
                                }
                            ]
                        }
                    }
                )
                + "\n"
            )
            artifacts = b.build_event_artifacts(events, root / "output-base")
            self.assertFalse(artifacts[0]["exists"])
            self.assertEqual(artifacts[0]["size_bytes"], 123)


if __name__ == "__main__":
    unittest.main()
