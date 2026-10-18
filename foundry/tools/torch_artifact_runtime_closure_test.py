#!/usr/bin/env python3
"""Tests for consumer torch artifact runtime-closure validation."""

from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = Path(__file__).with_name("torch_artifact_runtime_closure.py")
ROOTFS_LOCK = ROOT / "rootfs" / "cuda_ecosystem.lock.json"
SPEC = importlib.util.spec_from_file_location("torch_artifact_runtime_closure", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
closure = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = closure
SPEC.loader.exec_module(closure)


def scratch_parent() -> Path:
    value = os.environ.get("TEST_TMPDIR") or os.environ.get("TMPDIR")
    if value:
        return Path(value)
    return Path.cwd()


def require_tool(name: str) -> str:
    path = shutil.which(name)
    if path is None:
        raise unittest.SkipTest(f"{name} not available")
    return path


def build_shared(path: Path, *, soname: str | None = None, needed: Path | None = None) -> None:
    cc = os.environ.get("CC") or require_tool("gcc")
    source = path.with_suffix(".c")
    source.write_text("int vaso_artifact_fixture(void) { return 7; }\n", encoding="utf-8")
    cmd = [cc, "-shared", "-fPIC", str(source), "-o", str(path)]
    if soname:
        cmd.append(f"-Wl,-soname,{soname}")
    if needed is not None:
        cmd.extend([str(needed), f"-Wl,-rpath,{needed.parent}"])
    subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


class TorchArtifactRuntimeClosureTest(unittest.TestCase):
    def test_rejects_absolute_runpath_and_missing_needed_library(self) -> None:
        require_tool("readelf")
        patchelf = require_tool("patchelf")
        with tempfile.TemporaryDirectory(dir=scratch_parent()) as raw_tmp:
            tmp = Path(raw_tmp)
            artifact = tmp / "artifact"
            lib_dir = artifact / "lib/python3.13/site-packages/torch/lib"
            lib_dir.mkdir(parents=True)
            bad = lib_dir / "libbad.so"
            build_shared(bad)
            subprocess.run(
                [
                    patchelf,
                    "--set-rpath",
                    "/vaso-bazel-ob/external/+openmpi_native+openmpi_native/prefix/lib",
                    "--add-needed",
                    "libmpi.so.40",
                    str(bad),
                ],
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )

            findings = closure.check_artifact_runtime_closure(
                artifact,
                lock_path=ROOTFS_LOCK,
                line="cu130",
            )

        self.assertTrue(any(finding.rule_id == "bad-rpath" for finding in findings), findings)
        self.assertTrue(any(finding.rule_id == "bad-needed" for finding in findings), findings)

    def test_accepts_origin_relative_runpath_and_vendored_needed_library(self) -> None:
        require_tool("readelf")
        patchelf = require_tool("patchelf")
        with tempfile.TemporaryDirectory(dir=scratch_parent()) as raw_tmp:
            tmp = Path(raw_tmp)
            artifact = tmp / "artifact"
            lib_dir = artifact / "lib/python3.13/site-packages/torch/lib"
            libs_dir = artifact / "torch.libs"
            lib_dir.mkdir(parents=True)
            libs_dir.mkdir()
            dep = libs_dir / "libvendored_dep.so"
            main = lib_dir / "libmain.so"
            build_shared(dep, soname="libvendored_dep.so")
            build_shared(main, needed=dep)
            subprocess.run(
                [
                    patchelf,
                    "--set-rpath",
                    "$ORIGIN/../../../../../torch.libs",
                    str(main),
                ],
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )

            findings = closure.check_artifact_runtime_closure(
                artifact,
                lock_path=ROOTFS_LOCK,
                line="cu130",
            )

        self.assertEqual(findings, [])


if __name__ == "__main__":
    unittest.main()
