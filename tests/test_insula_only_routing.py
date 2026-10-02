"""Insula-only tests fail fast with a rerun hint on the host."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INSULA_ONLY_SHELL_TESTS = [
    ROOT / "experiments" / "spack-bazel-graph" / "formal" / "lean" / "lean_bazel_test.sh",
    ROOT / "experiments" / "spack-bazel-graph" / "native" / "py_llvmlite" / "mechanism_guard_test.sh",
    ROOT / "experiments" / "spack-bazel-graph" / "native" / "py_llvmlite" / "recipe_provenance_test.sh",
    ROOT / "experiments" / "spack-bazel-graph" / "synthetic" / "nccl_two_rank_native.sh",
    ROOT / "experiments" / "spack-bazel-graph" / "tools" / "pytorch_recipe_provenance_test.sh",
]


def test_insula_only_shell_tests_print_rerun_hint_on_host(tmp_path: Path) -> None:
    env = os.environ.copy()
    env.pop("VASO_IN_INSULA", None)
    env["TMPDIR"] = str(tmp_path)

    for script in INSULA_ONLY_SHELL_TESTS:
        result = subprocess.run(
            ["bash", str(script), "unused-tool", "unused-spack"],
            cwd=ROOT / "experiments" / "spack-bazel-graph",
            capture_output=True,
            text=True,
            env=env,
        )

        assert result.returncode == 2, script
        assert "run me inside the insula: run.sh --insula-cmd" in result.stderr, script
