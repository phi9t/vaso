#!/usr/bin/env python3
"""Host tests for the JAX group added to native/pytorch/gates.py."""

from __future__ import annotations

import importlib.util
import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "native" / "pytorch" / "gates.py"
PYTHON_ABI_DIR = "python3.13"


def load_gates_module():
    spec = importlib.util.spec_from_file_location("pytorch_gates", SCRIPT)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def temporary_directory():
    base = os.environ.get("TEST_TMPDIR") or os.environ.get("VASO_AGENT_IO_ROOT")
    return tempfile.TemporaryDirectory(prefix="pytorch-gates-jax-", dir=base)


def make_prefix(root: Path, name: str, package: str | None = None) -> Path:
    prefix = root / name
    site_packages = prefix / "lib" / PYTHON_ABI_DIR / "site-packages"
    if package:
        (site_packages / package).mkdir(parents=True)
    else:
        site_packages.mkdir(parents=True)
    (prefix / "bin").mkdir(parents=True, exist_ok=True)
    return prefix


def make_python_prefix(root: Path) -> Path:
    prefix = make_prefix(root, "python-prefix")
    python = prefix / "bin" / "python3"
    python.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    python.chmod(python.stat().st_mode | stat.S_IXUSR)
    return prefix


class PytorchGatesJaxGroupTest(unittest.TestCase):
    def test_jax_plan_does_not_expose_torch_prefixes(self) -> None:
        gates = load_gates_module()
        with temporary_directory() as tmp:
            root = Path(tmp)
            python_prefix = make_python_prefix(root)
            jax_prefix = make_prefix(root, "jax-prefix", "jax")
            jaxlib_prefix = make_prefix(root, "jaxlib-prefix", "jaxlib")
            torch_prefix = make_prefix(root, "torch-prefix", "torch")
            ml_dtypes_prefix = make_prefix(root, "py-ml-dtypes", "ml_dtypes")
            opt_einsum_prefix = make_prefix(root, "py-opt-einsum", "opt_einsum")
            scipy_prefix = make_prefix(root, "py-scipy", "scipy")
            plan_out = root / "gate-plan.json"
            rc = gates.main(
                [
                    "--python-prefix",
                    str(python_prefix),
                    "--jax-prefix",
                    str(jax_prefix),
                    "--jaxlib-prefix",
                    str(jaxlib_prefix),
                    "--runtime-prefix",
                    f"py-ml-dtypes={ml_dtypes_prefix}",
                    "--runtime-prefix",
                    f"py-opt-einsum={opt_einsum_prefix}",
                    "--runtime-prefix",
                    f"py-scipy={scipy_prefix}",
                    "--out-dir",
                    str(root / "out"),
                    "--block-group",
                    "core",
                    "--block-group",
                    "jax",
                    "--plan-out",
                    str(plan_out),
                ]
            )
            plan_doc = json.loads(plan_out.read_text(encoding="utf-8"))

        self.assertEqual(rc, 0)
        self.assertEqual(plan_doc["env"]["JAX_PREFIX"], str(jax_prefix))
        self.assertEqual(plan_doc["env"]["JAXLIB_PREFIX"], str(jaxlib_prefix))
        self.assertNotIn("TORCH_PREFIX", plan_doc["env"])
        self.assertNotIn("TORCH_REF_PREFIX", plan_doc["env"])
        pythonpath = plan_doc["env"]["PYTHONPATH"].split(os.pathsep)
        self.assertIn(str(jax_prefix / "lib" / PYTHON_ABI_DIR / "site-packages"), pythonpath)
        self.assertIn(str(jaxlib_prefix / "lib" / PYTHON_ABI_DIR / "site-packages"), pythonpath)
        self.assertNotIn(str(torch_prefix / "lib" / PYTHON_ABI_DIR / "site-packages"), pythonpath)
        ld_library_path = plan_doc["env"]["LD_LIBRARY_PATH"].split(os.pathsep)
        self.assertNotIn(str(torch_prefix / "lib"), ld_library_path)
        gate_doc = {gate["name"]: gate for gate in plan_doc["gates"]}
        self.assertEqual(set(gate_doc), {"jax_import", "jax_matmul", "jax_psum_2gpu"})
        self.assertEqual(gate_doc["jax_import"]["group"], "jax")
        self.assertEqual(gate_doc["jax_matmul"]["group"], "jax")
        self.assertEqual(gate_doc["jax_psum_2gpu"]["group"], "jax")
        self.assertTrue(gate_doc["jax_import"]["blocking"])
        self.assertTrue(gate_doc["jax_matmul"]["blocking"])
        self.assertTrue(gate_doc["jax_psum_2gpu"]["blocking"])

    def test_rejects_only_one_jax_prefix(self) -> None:
        gates = load_gates_module()
        with temporary_directory() as tmp:
            root = Path(tmp)
            torch_prefix = make_prefix(root, "torch-prefix", "torch")
            (torch_prefix / "lib" / PYTHON_ABI_DIR / "site-packages" / "torch" / "lib").mkdir(parents=True)
            ref_prefix = root / "ref-prefix"
            ref_prefix.mkdir()
            rc = gates.main(
                [
                    "--torch-prefix",
                    str(torch_prefix),
                    "--torch-ref-prefix",
                    str(ref_prefix),
                    "--python-prefix",
                    str(make_python_prefix(root)),
                    "--jax-prefix",
                    str(make_prefix(root, "jax-prefix", "jax")),
                    "--out-dir",
                    str(root / "out"),
                ]
            )

        self.assertEqual(rc, 2)

    def test_rejects_mixed_torch_and_jax_prefixes(self) -> None:
        gates = load_gates_module()
        with temporary_directory() as tmp:
            root = Path(tmp)
            torch_prefix = make_prefix(root, "torch-prefix", "torch")
            ref_prefix = root / "ref-prefix"
            ref_prefix.mkdir()
            rc = gates.main(
                [
                    "--torch-prefix",
                    str(torch_prefix),
                    "--torch-ref-prefix",
                    str(ref_prefix),
                    "--python-prefix",
                    str(make_python_prefix(root)),
                    "--jax-prefix",
                    str(make_prefix(root, "jax-prefix", "jax")),
                    "--jaxlib-prefix",
                    str(make_prefix(root, "jaxlib-prefix", "jaxlib")),
                    "--out-dir",
                    str(root / "out"),
                ]
            )

        self.assertEqual(rc, 2)


if __name__ == "__main__":
    unittest.main()
