#!/usr/bin/env python3
"""Tests for deterministic Vaso torch prefix artifact packaging."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tarfile
import tempfile
import unittest
import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = Path(__file__).with_name("torch_artifact.py")
SOURCE_MANIFEST = ROOT / "native" / "pytorch" / "pytorch-v2.14.0-2b3ec348-submodules.json"
KEYS = ROOT / "plans" / "keys.json"
SPEC = importlib.util.spec_from_file_location("torch_artifact", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
torch_artifact = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(torch_artifact)


def scratch_parent() -> Path:
    test_tmpdir = os.environ.get("TEST_TMPDIR")
    if test_tmpdir:
        return Path(test_tmpdir)
    estate = os.environ.get("VASO_ESTATE_ROOT")
    if estate:
        path = Path(estate) / "agents" / "fc-m5" / "tmp"
        path.mkdir(parents=True, exist_ok=True)
        return path
    return Path.cwd()


def write_fixture_prefix(prefix: Path) -> None:
    files = {
        "lib/python3.13/site-packages/torch/__init__.py": "__version__ = '2.14.0'\n",
        "lib/python3.13/site-packages/torch/include/torch/extension.h": "#pragma once\n",
        "lib/python3.13/site-packages/torch/include/torch/csrc/api/include/torch/torch.h": "#pragma once\n",
        "lib/python3.13/site-packages/torch/lib/libc10.so": "c10\n",
        "lib/python3.13/site-packages/torch/lib/libtorch.so": "torch\n",
        "lib/python3.13/site-packages/torch/lib/libtorch_cpu.so": "torch_cpu\n",
        "lib/python3.13/site-packages/torch/lib/libtorch_python.so": "torch_python\n",
    }
    for index, (rel, content) in enumerate(reversed(files.items())):
        path = prefix / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        os.utime(path, (1_700_000_000 + index, 1_700_000_000 + index))
    os.symlink("libtorch.so", prefix / "lib/python3.13/site-packages/torch/lib/libtorch_alias.so")


class TorchArtifactTest(unittest.TestCase):
    def test_fixture_prefix_artifact_is_deterministic_and_records_provenance(self) -> None:
        with tempfile.TemporaryDirectory(dir=scratch_parent()) as raw_tmp:
            tmp = Path(raw_tmp)
            prefix = tmp / "prefix"
            write_fixture_prefix(prefix)

            first = torch_artifact.produce_torch_artifact(
                prefix=prefix,
                line="cu130",
                out_dir=tmp / "out-a",
                repo_root=ROOT,
                source_manifest=SOURCE_MANIFEST,
                keys=KEYS,
            )
            second = torch_artifact.produce_torch_artifact(
                prefix=prefix,
                line="cu130",
                out_dir=tmp / "out-b",
                repo_root=ROOT,
                source_manifest=SOURCE_MANIFEST,
                keys=KEYS,
            )

            self.assertEqual(first["sha256"], second["sha256"])
            self.assertRegex(first["sha256"], r"^[0-9a-f]{64}$")
            self.assertEqual(
                first["plan_key"],
                json.loads(KEYS.read_text(encoding="utf-8"))["packages"]["torch/cu130/py-torch"]["merkle_key"],
            )
            self.assertEqual(
                first["source_commit"],
                json.loads(SOURCE_MANIFEST.read_text(encoding="utf-8"))["source"]["commit"],
            )
            self.assertTrue(Path(first["archive"]).is_file())
            self.assertEqual(json.loads((tmp / "out-a" / "torch-artifact.json").read_text(encoding="utf-8")), first)
            self.assertEqual(json.loads((tmp / "out-a" / "torch-artifact.provenance.json").read_text(encoding="utf-8")), first)

            with tarfile.open(first["archive"], "r") as tar:
                members = tar.getmembers()
            names = [member.name for member in members]
            self.assertEqual(names, sorted(names))
            self.assertIn("lib/python3.13/site-packages/torch/include/torch/extension.h", names)
            self.assertIn("lib/python3.13/site-packages/torch/lib/libtorch.so", names)
            for member in members:
                self.assertEqual(member.mtime, 0, member.name)
                self.assertEqual(member.uid, 0, member.name)
                self.assertEqual(member.gid, 0, member.name)
                self.assertEqual(member.uname, "", member.name)
                self.assertEqual(member.gname, "", member.name)

    def test_estate_artifact_manifest_records_store_relative_archive(self) -> None:
        with tempfile.TemporaryDirectory(dir=scratch_parent()) as raw_tmp:
            tmp = Path(raw_tmp)
            estate = tmp / "estate"
            prefix = tmp / "prefix"
            write_fixture_prefix(prefix)

            manifest = torch_artifact.produce_torch_artifact(
                prefix=prefix,
                line="cu130",
                out_dir=estate / "vaso" / "artifacts" / "torch" / "cu130",
                repo_root=ROOT,
                source_manifest=SOURCE_MANIFEST,
                keys=KEYS,
                estate_root=estate,
            )

            self.assertFalse(Path(manifest["archive"]).is_absolute())
            self.assertTrue(manifest["archive"].startswith("vaso/artifacts/torch/cu130/"))
            self.assertTrue((estate / manifest["archive"]).is_file())

    def test_runtime_repair_vendors_provider_library_with_origin_runpath(self) -> None:
        patchelf = shutil.which("patchelf")
        cc = shutil.which(os.environ.get("CC", "gcc"))
        if patchelf is None or cc is None:
            self.skipTest("patchelf and gcc are required")
        with tempfile.TemporaryDirectory(dir=scratch_parent()) as raw_tmp:
            tmp = Path(raw_tmp)
            prefix = tmp / "prefix"
            write_fixture_prefix(prefix)
            provider = tmp / "provider"
            provider_lib = provider / "lib/libfixture_dep.so"
            provider_lib.parent.mkdir(parents=True)
            dep_source = provider_lib.with_suffix(".c")
            dep_source.write_text("int fixture_dep(void) { return 1; }\n", encoding="utf-8")
            subprocess.run(
                [
                    cc,
                    "-shared",
                    "-fPIC",
                    str(dep_source),
                    "-Wl,-soname,libfixture_dep.so",
                    "-o",
                    str(provider_lib),
                ],
                check=True,
            )
            main_lib = prefix / torch_artifact.TORCH_PACKAGE_REL / "lib/libtorch_cpu.so"
            main_source = main_lib.with_suffix(".c")
            main_source.write_text("extern int fixture_dep(void); int torch_cpu(void) { return fixture_dep(); }\n", encoding="utf-8")
            subprocess.run(
                [
                    cc,
                    "-shared",
                    "-fPIC",
                    str(main_source),
                    str(provider_lib),
                    f"-Wl,-rpath,{provider / 'lib'}",
                    "-o",
                    str(main_lib),
                ],
                check=True,
            )

            manifest = torch_artifact.produce_torch_artifact(
                prefix=prefix,
                line="cu130",
                out_dir=tmp / "out",
                repo_root=ROOT,
                source_manifest=SOURCE_MANIFEST,
                keys=KEYS,
                provider_prefixes=(provider,),
            )

            with tarfile.open(manifest["archive"], "r") as tar:
                tar.extractall(tmp / "unpacked")
            repaired_main = tmp / "unpacked" / torch_artifact.TORCH_PACKAGE_REL / "lib/libtorch_cpu.so"
            needed = subprocess.run(
                [patchelf, "--print-needed", str(repaired_main)],
                check=True,
                stdout=subprocess.PIPE,
                text=True,
            ).stdout.splitlines()
            rpath = subprocess.run(
                [patchelf, "--print-rpath", str(repaired_main)],
                check=True,
                stdout=subprocess.PIPE,
                text=True,
            ).stdout.strip()

            self.assertIn("libfixture_dep.so", needed)
            self.assertTrue((tmp / "unpacked" / torch_artifact.TORCH_PACKAGE_LIBS_REL / "libfixture_dep.so").is_file())
            self.assertIn("$ORIGIN/../../torch.libs", rpath.split(":"))
            self.assertNotIn(str(provider), rpath)


if __name__ == "__main__":
    unittest.main()
