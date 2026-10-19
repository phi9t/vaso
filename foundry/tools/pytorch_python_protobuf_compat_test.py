#!/usr/bin/env python3
"""Tests for the PyTorch/Python/protobuf compatibility contract."""

from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path


SCRIPT = Path(__file__).with_name("pytorch_python_protobuf_compat.py")
SPEC = importlib.util.spec_from_file_location("pytorch_python_protobuf_compat", SCRIPT)
assert SPEC is not None
compat = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = compat
SPEC.loader.exec_module(compat)


class PytorchPythonProtobufCompatTest(unittest.TestCase):
    def test_python_313_protobuf_32112_contract_is_coherent_for_vendored_onnx(self) -> None:
        report = compat.evaluate_contract(
            python_version="3.13.7",
            cpp_protobuf_version="3.21.12",
            py_protobuf_version="4.21.12",
            pytorch_version="2.14.0",
            onnx_mode="vendored",
            onnx_external_protobuf_version="3.21.12",
            standalone_onnx_python=False,
        )

        self.assertTrue(report["ok"], report)
        self.assertEqual(report["selected_family"], "protobuf C++ 3.21.12 / Python 4.21.12")
        self.assertEqual(report["required_python"], "3.13.x")
        self.assertEqual(report["pytorch_python_support"], ">=3.10")
        self.assertEqual(report["onnx_guard"]["mode"], "vendored")
        self.assertEqual(report["onnx_guard"]["policy"], "external-protobuf-3.21.12-no-abseil")
        self.assertFalse(report["onnx_guard"]["requires_abseil"])
        self.assertFalse(report["onnx_guard"]["uses_custom_protobuf_fallback"])
        self.assertEqual(report["build_env"]["BUILD_CUSTOM_PROTOBUF"], "OFF")
        self.assertEqual(report["build_env"]["ONNX_BUILD_CUSTOM_PROTOBUF"], "OFF")
        self.assertEqual(report["build_env"]["ONNX_USE_PROTOBUF_SHARED_LIBS"], "ON")
        self.assertEqual(report["build_env"]["PROTOBUF_PROTOC_VERSION"], "libprotoc 3.21.12")
        self.assertEqual(report["failure_classes"], [])

    def test_python_314_blocks_py_protobuf_42112_native_extension(self) -> None:
        report = compat.evaluate_contract(
            python_version="3.14.0",
            cpp_protobuf_version="3.21.12",
            py_protobuf_version="4.21.12",
            pytorch_version="2.14.0",
            onnx_mode="vendored",
            onnx_external_protobuf_version="3.21.12",
            standalone_onnx_python=False,
        )

        self.assertFalse(report["ok"], report)
        self.assertIn("PY_PROTOBUF_BUILD_INCOMPATIBLE", report["failure_classes"])
        self.assertIn("PyFrameObject", "\n".join(report["findings"]))

    def test_onnx_external_protobuf_422_or_newer_triggers_abseil_guardrail(self) -> None:
        report = compat.evaluate_contract(
            python_version="3.13.7",
            cpp_protobuf_version="4.25.1",
            py_protobuf_version="4.25.1",
            pytorch_version="2.14.0",
            onnx_mode="vendored",
            onnx_external_protobuf_version="4.25.1",
            standalone_onnx_python=False,
        )

        self.assertFalse(report["ok"], report)
        self.assertTrue(report["onnx_guard"]["requires_abseil"])
        self.assertIn("ONNX_ABSEIL_PROTOBUF_TRIGGER", report["failure_classes"])
        self.assertIn("PROTOBUF_FAMILY_MISMATCH", report["failure_classes"])

    def test_onnx_custom_protobuf_fallback_is_rejected(self) -> None:
        report = compat.evaluate_contract(
            python_version="3.13.7",
            cpp_protobuf_version="3.21.12",
            py_protobuf_version="4.21.12",
            pytorch_version="2.14.0",
            onnx_mode="custom-protobuf-fallback",
            onnx_external_protobuf_version=None,
            standalone_onnx_python=False,
        )

        self.assertFalse(report["ok"], report)
        self.assertTrue(report["onnx_guard"]["requires_abseil"])
        self.assertTrue(report["onnx_guard"]["uses_custom_protobuf_fallback"])
        self.assertEqual(report["onnx_guard"]["fallback_protobuf"], "29.2")
        self.assertEqual(report["onnx_guard"]["fallback_abseil"], "20240722.1")
        self.assertIn("ONNX_CUSTOM_PROTOBUF_FALLBACK", report["failure_classes"])

    def test_standalone_onnx_python_package_is_not_part_of_this_island(self) -> None:
        report = compat.evaluate_contract(
            python_version="3.13.7",
            cpp_protobuf_version="3.21.12",
            py_protobuf_version="4.21.12",
            pytorch_version="2.14.0",
            onnx_mode="vendored",
            onnx_external_protobuf_version="3.21.12",
            standalone_onnx_python=True,
        )

        self.assertFalse(report["ok"], report)
        self.assertEqual(report["standalone_onnx_python_requires"], "protobuf>=4.25.1")
        self.assertIn("STANDALONE_ONNX_PYTHON_PROTOBUF_MISMATCH", report["failure_classes"])


if __name__ == "__main__":
    unittest.main()
