#!/usr/bin/env python3
"""PyTorch/Python/protobuf compatibility contract checker.

This is a source-policy checker. It does not build PyTorch or query host Spack;
live build/import probes belong in the hermetic insula gate that consumes this
contract.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence


PYTORCH_VERSION = "2.14.0"
PYTORCH_PYTHON_REQUIRES = ">=3.10"
REQUIRED_PYTHON_MINOR = "3.13"
REQUIRED_CPP_PROTOBUF = "3.21.12"
REQUIRED_PY_PROTOBUF = "4.21.12"
EXPECTED_PROTOC_VERSION = "libprotoc 3.21.12"
ONNX_RELEASE = "v1.18.0"
ONNX_GITLINK = "e709452ef2bbc1d113faf678c24e6d3467696e83"
ONNX_ABSEIL_THRESHOLD = "4.22.0"
ONNX_FALLBACK_PROTOBUF = "29.2"
ONNX_FALLBACK_PROTOBUF_CMAKE_VERSION = "5.29.2"
ONNX_FALLBACK_ABSEIL = "20240722.1"
STANDALONE_ONNX_PYTHON_REQUIRES = "protobuf>=4.25.1"
SELECTED_FAMILY = "protobuf C++ 3.21.12 / Python 4.21.12"


def _version_tuple(version: str | None) -> tuple[int, ...]:
    if not version:
        return ()
    parts: list[int] = []
    for part in version.split("."):
        digits = "".join(ch for ch in part if ch.isdigit())
        if digits == "":
            break
        parts.append(int(digits))
    return tuple(parts)


def _minor(version: str) -> str:
    parts = version.split(".")
    if len(parts) < 2:
        return version
    return ".".join(parts[:2])


def _onnx_guard(onnx_mode: str, onnx_external_protobuf_version: str | None) -> dict:
    uses_fallback = onnx_mode == "custom-protobuf-fallback"
    external = onnx_external_protobuf_version or ""
    external_triggers_abseil = (
        bool(external)
        and _version_tuple(external) >= _version_tuple(ONNX_ABSEIL_THRESHOLD)
    )
    requires_abseil = uses_fallback or external_triggers_abseil

    if uses_fallback:
        policy = "rejected-custom-protobuf-fallback"
    elif external_triggers_abseil:
        policy = "rejected-external-protobuf-ge-4.22-abseil"
    elif external == REQUIRED_CPP_PROTOBUF:
        policy = "external-protobuf-3.21.12-no-abseil"
    else:
        policy = "rejected-external-protobuf-family"

    return {
        "mode": onnx_mode,
        "onnx_release": ONNX_RELEASE,
        "onnx_gitlink": ONNX_GITLINK,
        "policy": policy,
        "external_protobuf_version": external or None,
        "abseil_trigger": "external Protobuf_VERSION >= 4.22.0 or custom protobuf fallback",
        "requires_abseil": requires_abseil,
        "uses_custom_protobuf_fallback": uses_fallback,
        "fallback_protobuf": ONNX_FALLBACK_PROTOBUF if uses_fallback else None,
        "fallback_protobuf_cmake_version": (
            ONNX_FALLBACK_PROTOBUF_CMAKE_VERSION if uses_fallback else None
        ),
        "fallback_abseil": ONNX_FALLBACK_ABSEIL if uses_fallback else None,
    }


def evaluate_contract(
    *,
    python_version: str,
    cpp_protobuf_version: str,
    py_protobuf_version: str,
    pytorch_version: str,
    onnx_mode: str,
    onnx_external_protobuf_version: str | None,
    standalone_onnx_python: bool,
) -> dict:
    """Return a structured compatibility report for the selected island."""
    failures: list[str] = []
    findings: list[str] = []

    if pytorch_version != PYTORCH_VERSION:
        failures.append("PYTORCH_VERSION_UNSUPPORTED")
        findings.append(f"expected PyTorch {PYTORCH_VERSION}; got {pytorch_version}")

    python_minor = _minor(python_version)
    if _version_tuple(python_version) < _version_tuple("3.10"):
        failures.append("PYTHON_RANGE_UNSUPPORTED")
        findings.append(f"PyTorch {PYTORCH_VERSION} requires Python {PYTORCH_PYTHON_REQUIRES}")
    if python_minor != REQUIRED_PYTHON_MINOR:
        failures.append("PYTHON_COMPATIBILITY_ISLAND_MISMATCH")
        findings.append(
            "this protobuf/PyTorch island is pinned to Python 3.13.x until the "
            "py-protobuf@4.21.12 source extension is proven elsewhere"
        )
    if python_minor == "3.14" and py_protobuf_version == REQUIRED_PY_PROTOBUF:
        failures.append("PY_PROTOBUF_BUILD_INCOMPATIBLE")
        findings.append(
            "py-protobuf@4.21.12 C++ extension is known to fail on Python 3.14 "
            "with invalid use of incomplete type PyFrameObject"
        )

    if cpp_protobuf_version != REQUIRED_CPP_PROTOBUF:
        failures.append("PROTOBUF_FAMILY_MISMATCH")
        findings.append(
            f"expected C++ protobuf {REQUIRED_CPP_PROTOBUF}; got {cpp_protobuf_version}"
        )
    if py_protobuf_version != REQUIRED_PY_PROTOBUF:
        failures.append("PROTOBUF_FAMILY_MISMATCH")
        findings.append(
            f"expected Python protobuf {REQUIRED_PY_PROTOBUF}; got {py_protobuf_version}"
        )

    guard = _onnx_guard(onnx_mode, onnx_external_protobuf_version)
    if guard["requires_abseil"]:
        failures.append("ONNX_ABSEIL_PROTOBUF_TRIGGER")
        findings.append(
            "ONNX would require Abseil/utf8_range for this protobuf path; "
            "that is outside the selected PyTorch protobuf 3.21.12 island"
        )
    if guard["uses_custom_protobuf_fallback"]:
        failures.append("ONNX_CUSTOM_PROTOBUF_FALLBACK")
        findings.append(
            "ONNX custom-protobuf fallback fetches protobuf 29.2 and Abseil "
            "20240722.1, creating a second protobuf/Abseil provider family"
        )
    if guard["policy"] == "rejected-external-protobuf-family":
        failures.append("PROTOBUF_FAMILY_MISMATCH")
        findings.append(
            "vendored ONNX must consume the same external protobuf 3.21.12 "
            "provider as PyTorch"
        )

    if standalone_onnx_python:
        failures.append("STANDALONE_ONNX_PYTHON_PROTOBUF_MISMATCH")
        findings.append(
            "standalone ONNX v1.18.0 Python packaging requires protobuf>=4.25.1; "
            "it is not admitted into the PyTorch-native 4.21.12 Python protobuf island"
        )

    return {
        "schema_version": 1,
        "ok": not failures,
        "failure_classes": sorted(set(failures)),
        "findings": findings,
        "required_python": "3.13.x",
        "pytorch_version": pytorch_version,
        "pytorch_python_support": PYTORCH_PYTHON_REQUIRES,
        "cpp_protobuf_version": cpp_protobuf_version,
        "py_protobuf_version": py_protobuf_version,
        "selected_family": SELECTED_FAMILY,
        "standalone_onnx_python": standalone_onnx_python,
        "standalone_onnx_python_requires": STANDALONE_ONNX_PYTHON_REQUIRES,
        "onnx_guard": guard,
        "build_env": {
            "BUILD_CUSTOM_PROTOBUF": "OFF",
            "ONNX_BUILD_CUSTOM_PROTOBUF": "OFF",
            "ONNX_USE_PROTOBUF_SHARED_LIBS": "ON",
            "PROTOBUF_PROTOC_VERSION": EXPECTED_PROTOC_VERSION,
        },
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python-version", required=True)
    parser.add_argument("--cpp-protobuf-version", required=True)
    parser.add_argument("--py-protobuf-version", required=True)
    parser.add_argument("--pytorch-version", default=PYTORCH_VERSION)
    parser.add_argument(
        "--onnx-mode",
        default="vendored",
        choices=("vendored", "custom-protobuf-fallback"),
    )
    parser.add_argument("--onnx-external-protobuf-version", default=None)
    parser.add_argument("--standalone-onnx-python", action="store_true")
    parser.add_argument("--out", default=None)
    args = parser.parse_args(argv)

    report = evaluate_contract(
        python_version=args.python_version,
        cpp_protobuf_version=args.cpp_protobuf_version,
        py_protobuf_version=args.py_protobuf_version,
        pytorch_version=args.pytorch_version,
        onnx_mode=args.onnx_mode,
        onnx_external_protobuf_version=args.onnx_external_protobuf_version,
        standalone_onnx_python=args.standalone_onnx_python,
    )
    text = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(text)
    print(text, end="")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
