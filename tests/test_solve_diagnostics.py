#!/usr/bin/env python3
"""Tests for Spack concretization diagnostics in structured run results."""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RESULT_PY = ROOT / "scripts" / "insula" / "result.py"


def _load_result_module():
    spec = importlib.util.spec_from_file_location("insula_result", RESULT_PY)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _scratch_root() -> Path:
    raw = os.environ.get("TEST_TMPDIR")
    root = Path(raw) / "solve-diagnostics-test" if raw else ROOT / ".scratch" / "solve-diagnostics-test"
    root.mkdir(parents=True, exist_ok=True)
    return root


ONLY_EXTERNAL_SAMPLE = """\
spack spec failed (1):
==> Error: failed to concretize `py-jax@0.9.0 ^py-jaxlib@0.9.0+cuda+nccl cuda_arch=100 ^python@3.13.13+bz2+ctypes+dbm~debug+libxml2+lzma~optimizations+pic+pyexpat+pythoncmd+readline+shared+sqlite3+ssl~static~tkinter+uuid+zlib` for the following reasons:
     1. Only external, or concrete, compilers are allowed for the cxx language
     2. Only external, or concrete, compilers are allowed for the c language
"""


CONFLICTS_WITH_SAMPLE = """\
spack spec failed (1):
==> Error: failed to concretize `py-torch@2.14.0+cuda+cudnn+cusparselt+distributed+fbgemm+flash_attention+gloo+kineto+magma~mkldnn+mpi+nccl+openmp+qnnpack+tensorpipe+xnnpack cuda_arch=100 ^font-util fonts:=encodings ^openblas~fortran` for the following reasons:
     1. magma: '^cuda@13:' conflicts with '@:2.9.0'
     2. magma: '^cuda@12.6:' conflicts with '@:2.8.0'
        required because conflict is triggered when ^cuda@13:
"""


NO_VERSION_SAMPLE = """\
spack spec failed (1):
==> Error: No version exists that satisfies these input specs:
    py-jax@0.10.2 ^py-jaxlib@0.10.2+cuda+nccl cuda_arch=100 ^python@3.13.13+bz2+ctypes+dbm~debug+libxml2+lzma~optimizations+pic+pyexpat+pythoncmd+readline+shared+sqlite3+ssl~static~tkinter+uuid+zlib, py-jaxlib@0.10.2+cuda+nccl cuda_arch=100
"""


GITVERSION_SAMPLE = """\
==> Found no new compilers
==> Error: 'GitVersion' object has no attribute 'dotted_numeric_string'
"""


def test_parse_only_external_compiler_diagnostic() -> None:
    result = _load_result_module()
    diagnostic = result.parse_spack_solve_diagnostic(ONLY_EXTERNAL_SAMPLE)

    assert diagnostic is not None
    assert diagnostic["spec"].startswith("py-jax@0.9.0")
    assert diagnostic["conflicting_constraints"] == [
        "Only external, or concrete, compilers are allowed for the cxx language",
        "Only external, or concrete, compilers are allowed for the c language",
    ]
    assert "compiler externals" in diagnostic["hint"]


def test_parse_conflicts_with_diagnostic() -> None:
    result = _load_result_module()
    diagnostic = result.parse_spack_solve_diagnostic(CONFLICTS_WITH_SAMPLE)

    assert diagnostic is not None
    assert diagnostic["spec"].startswith("py-torch@2.14.0+cuda")
    assert "magma: '^cuda@13:' conflicts with '@:2.9.0'" in diagnostic["conflicting_constraints"]
    assert "compatible CUDA/package version" in diagnostic["hint"]


def test_parse_no_version_diagnostic() -> None:
    result = _load_result_module()
    diagnostic = result.parse_spack_solve_diagnostic(NO_VERSION_SAMPLE)

    assert diagnostic is not None
    assert diagnostic["spec"].startswith("py-jax@0.10.2")
    assert diagnostic["conflicting_constraints"][0] == "No version exists that satisfies these input specs"
    assert "py-jaxlib@0.10.2+cuda+nccl" in diagnostic["conflicting_constraints"][1]
    assert "requested version pins" in diagnostic["hint"]


def test_parse_gitversion_diagnostic_uses_fallback_spec() -> None:
    result = _load_result_module()
    diagnostic = result.parse_spack_solve_diagnostic(GITVERSION_SAMPLE, fallback_spec="py-numpy")

    assert diagnostic is not None
    assert diagnostic["spec"] == "py-numpy"
    assert diagnostic["conflicting_constraints"] == [
        "'GitVersion' object has no attribute 'dotted_numeric_string'",
    ]
    assert "Spack version parser" in diagnostic["hint"]


def test_finalize_records_solve_diagnostics_from_failed_stage_log() -> None:
    with tempfile.TemporaryDirectory(dir=_scratch_root()) as root:
        work = Path(root)
        log = work / "spack-lock.log"
        stages = work / "stages.jsonl"
        out = work / "result.json"
        log.write_text(CONFLICTS_WITH_SAMPLE, encoding="utf-8")

        subprocess.run(
            [
                sys.executable,
                str(RESULT_PY),
                "stage",
                "--stages-jsonl",
                str(stages),
                "--name",
                "bazel-run",
                "--exit-code",
                "17",
                "--duration-seconds",
                "1.25",
                "--log-path",
                str(log),
            ],
            check=True,
        )
        subprocess.run(
            [
                sys.executable,
                str(RESULT_PY),
                "finalize",
                "--out",
                str(out),
                "--stages-jsonl",
                str(stages),
                "--mode",
                "gate",
                "--line",
                "cu130",
                "--agent",
                "worker-a",
                "--args-json",
                json.dumps(["gate"]),
                "--log-path",
                str(work),
                "--commit",
                "abc123",
                "--exit-code",
                "17",
                "--started-utc",
                "2026-10-02T00:00:00Z",
                "--solve-spec",
                "py-torch@2.14.0+cuda",
            ],
            check=True,
        )

        doc = json.loads(out.read_text(encoding="utf-8"))
        assert doc["solve_diagnostics"] == [
            {
                "stage": "bazel-run",
                "log_path": str(log),
                "spec": (
                    "py-torch@2.14.0+cuda+cudnn+cusparselt+distributed+fbgemm+"
                    "flash_attention+gloo+kineto+magma~mkldnn+mpi+nccl+openmp+"
                    "qnnpack+tensorpipe+xnnpack cuda_arch=100 ^font-util "
                    "fonts:=encodings ^openblas~fortran"
                ),
                "conflicting_constraints": [
                    "magma: '^cuda@13:' conflicts with '@:2.9.0'",
                    "magma: '^cuda@12.6:' conflicts with '@:2.8.0'",
                ],
                "hint": (
                    "The requested spec conflicts with package constraints; try a "
                    "compatible CUDA/package version or remove the conflicting pin."
                ),
            }
        ]
