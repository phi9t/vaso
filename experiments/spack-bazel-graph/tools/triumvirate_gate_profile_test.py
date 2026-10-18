#!/usr/bin/env python3
"""Tests for the profile-aware triumvirate gate shell wrappers."""

from __future__ import annotations

import re
import unittest
from pathlib import Path


EXPERIMENT_ROOT = Path(__file__).resolve().parents[1]
INSULA_GATE = EXPERIMENT_ROOT / "scripts" / "agents" / "triumvirate-gate-insula.sh"


class TriumvirateGateProfileTest(unittest.TestCase):
    def test_insula_gate_writes_profile_acceptance_file(self) -> None:
        text = INSULA_GATE.read_text(encoding="utf-8")

        self.assertIn('acceptance_out="${acceptance_out:-acceptance-$profile-$line.json}"', text)
        self.assertIn("--profile \"$profile\"", text)

    def test_torch_jax_and_jax_model_workload_lists_are_split_and_w4_is_absent(self) -> None:
        text = INSULA_GATE.read_text(encoding="utf-8")
        torch_match = re.search(r"torch_workloads=\((?P<body>[^)]*)\)", text, re.S)
        jax_match = re.search(r"jax_workloads=\((?P<body>[^)]*)\)", text, re.S)
        jax_model_match = re.search(r"jax_model_workloads=\((?P<body>[^)]*)\)", text, re.S)
        self.assertIsNotNone(torch_match)
        self.assertIsNotNone(jax_match)
        self.assertIsNotNone(jax_model_match)
        assert torch_match is not None
        assert jax_match is not None
        assert jax_model_match is not None
        torch_body = torch_match.group("body")
        jax_body = jax_match.group("body")
        jax_model_body = jax_model_match.group("body")

        for workload in ("W1a", "W1b", "W1c", "W1d", "W2a", "W2b", "W2c"):
            self.assertIn(workload, torch_body)
        self.assertNotIn("W3", torch_body)
        self.assertNotIn("W4", torch_body)
        self.assertIn("W3a", jax_body)
        self.assertIn("W3b", jax_body)
        self.assertIn("W3c", jax_body)
        self.assertNotIn("W5", jax_body)
        self.assertNotIn("W4", jax_body)
        for workload in ("W5a", "W5b", "W5c", "W5d", "W5e", "W5f"):
            self.assertIn(workload, jax_model_body)
        self.assertNotIn("W3", jax_model_body)
        self.assertNotIn("W4", jax_model_body)

    def test_jax_profile_runs_real_model_workloads_as_their_own_stage(self) -> None:
        text = INSULA_GATE.read_text(encoding="utf-8")

        self.assertIn('"jax_model_workloads" \\', text)
        self.assertIn('--out "$out_dir/jax-model-workloads"', text)
        self.assertIn('--only "$(join_csv "${jax_model_workloads[@]}")"', text)

    def test_torch_profile_np13_gates_receive_vision_audio_runtime_prefixes(self) -> None:
        text = INSULA_GATE.read_text(encoding="utf-8")
        runtime_match = re.search(
            r'if \[\[ "\$\{#runtime_prefixes\[@\]\}" -eq 0 \]\]; then\s+'
            r"torch_runtime_prefixes=\((?P<body>[^)]*)\)",
            text,
            re.S,
        )
        self.assertIsNotNone(runtime_match)
        assert runtime_match is not None
        runtime_body = runtime_match.group("body")

        self.assertIn("py-pillow=", runtime_body)
        self.assertIn("torchvision=$torchvision_prefix", runtime_body)
        self.assertIn("torchaudio=$torchaudio_prefix", runtime_body)

    def test_torch_profile_np13_gates_receive_triton_runtime_prefix(self) -> None:
        text = INSULA_GATE.read_text(encoding="utf-8")
        runtime_match = re.search(
            r'if \[\[ "\$\{#runtime_prefixes\[@\]\}" -eq 0 \]\]; then\s+'
            r"torch_runtime_prefixes=\((?P<body>[^)]*)\)",
            text,
            re.S,
        )
        self.assertIsNotNone(runtime_match)
        assert runtime_match is not None
        runtime_body = runtime_match.group("body")

        self.assertIn("triton=$triton_prefix", runtime_body)

    def test_jax_profile_workloads_receive_absl_runtime_prefix(self) -> None:
        text = INSULA_GATE.read_text(encoding="utf-8")
        runtime_match = re.search(
            r"(?ms)^  jaxlib_deps_prefixes=\(\n(?P<body>.*?)^  \)",
            text,
        )
        self.assertIsNotNone(runtime_match)
        assert runtime_match is not None
        runtime_body = runtime_match.group("body")

        self.assertIn("py-absl-py=", runtime_body)
        self.assertIn("+py_absl_py_native+py_absl_py_native/prefix", runtime_body)
        self.assertRegex(text, r'jax_runtime_prefixes=\("\$\{jaxlib_deps_prefixes\[@\]\}"\)')


if __name__ == "__main__":
    unittest.main()
