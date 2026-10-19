#!/usr/bin/env python3
"""Tests for release runtime wheelhouse assembly."""

from __future__ import annotations

import csv
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import sysconfig
import tempfile
import unittest
import zipfile
from pathlib import Path


SCRIPT = Path(__file__).with_name("runtime_wheelhouse.py")
SPEC = importlib.util.spec_from_file_location("runtime_wheelhouse", SCRIPT)
assert SPEC is not None
runtime_wheelhouse = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(runtime_wheelhouse)


def _tmp_root() -> Path:
    base = os.environ.get("TEST_TMPDIR") or os.environ.get("TMPDIR")
    return Path(tempfile.mkdtemp(dir=base))


def _write_prefix(root: Path, *, name: str, version: str, package_file: str, tag: str = "py3-none-any") -> Path:
    prefix = root / name
    python_dir = f"python{sys.version_info.major}.{sys.version_info.minor}"
    site_packages = prefix / "lib" / python_dir / "site-packages"
    package_path = site_packages / package_file
    package_path.parent.mkdir(parents=True, exist_ok=True)
    package_path.write_text("VALUE = 1\n", encoding="utf-8")
    dist_info = site_packages / f"{name.replace('-', '_')}-{version}.dist-info"
    dist_info.mkdir(parents=True)
    (dist_info / "METADATA").write_text(f"Name: {name}\nVersion: {version}\n", encoding="utf-8")
    (dist_info / "WHEEL").write_text(
        f"Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: {tag}\n",
        encoding="utf-8",
    )
    (dist_info / "RECORD").write_text("", encoding="utf-8")
    prefix_file = root / f"{name}.prefix.txt"
    prefix_file.write_text(str(prefix) + "\n", encoding="utf-8")
    return prefix_file


def _build_shared(path: Path, source: str, extra: list[str] | None = None) -> Path:
    cc = os.environ.get("CC", "/usr/bin/gcc")
    if not Path(cc).exists() and shutil.which(cc) is None:
        raise unittest.SkipTest(f"compiler not available: {cc}")
    path.parent.mkdir(parents=True, exist_ok=True)
    source_path = path.with_suffix(".c")
    source_path.write_text(source, encoding="utf-8")
    cmd = [cc, "-shared", "-nostdlib", "-fPIC", str(source_path), "-o", str(path)]
    if extra:
        cmd.extend(extra)
    subprocess.run(cmd, check=True)
    return path


def _tool(name: str) -> str:
    for candidate in (
        shutil.which(name),
        f"/opt/tiger/ss_bin/{name}",
        f"/usr/bin/{name}",
    ):
        if candidate and Path(candidate).is_file():
            return candidate
    raise unittest.SkipTest(f"{name} not available")


def _readelf_dynamic(path: Path) -> str:
    exe = _tool("readelf")
    return subprocess.run([exe, "-d", str(path)], check=True, stdout=subprocess.PIPE, text=True).stdout


class RuntimeWheelhouseTest(unittest.TestCase):
    def test_packages_runtime_prefixes_as_hash_manifested_wheels(self) -> None:
        root = _tmp_root()
        self.addCleanup(lambda: shutil.rmtree(root, ignore_errors=True))
        typing_prefix = _write_prefix(
            root,
            name="typing-extensions",
            version="4.15.0",
            package_file="typing_extensions.py",
        )
        filelock_prefix = _write_prefix(
            root,
            name="filelock",
            version="3.29.1",
            package_file="filelock/__init__.py",
        )

        manifest = root / "manifest.json"
        wheels = runtime_wheelhouse.build_wheelhouse(
            [typing_prefix, filelock_prefix],
            out_dir=root / "wheelhouse",
            manifest_out=manifest,
        )

        wheel_names = [path.name for path in wheels]
        self.assertEqual(
            wheel_names,
            [
                "filelock-3.29.1-py3-none-any.whl",
                "typing_extensions-4.15.0-py3-none-any.whl",
            ],
        )
        manifest_doc = json.loads(manifest.read_text(encoding="utf-8"))
        self.assertEqual(manifest_doc["schema_version"], 1)
        self.assertEqual([item["name"] for item in manifest_doc["wheels"]], wheel_names)
        self.assertTrue(all(item["sha256"] for item in manifest_doc["wheels"]))

        with zipfile.ZipFile(root / "wheelhouse" / "typing_extensions-4.15.0-py3-none-any.whl") as zf:
            names = set(zf.namelist())
            self.assertIn("typing_extensions.py", names)
            self.assertIn("typing_extensions-4.15.0.dist-info/METADATA", names)
            rows = list(
                csv.reader(
                    zf.read("typing_extensions-4.15.0.dist-info/RECORD").decode("utf-8").splitlines()
                )
            )
            record_names = {row[0] for row in rows}
            self.assertIn("typing_extensions.py", record_names)
            self.assertNotIn(str(root), zf.read("typing_extensions-4.15.0.dist-info/RECORD").decode("utf-8"))

    def test_vendors_provider_libraries_needed_by_native_extensions(self) -> None:
        root = _tmp_root()
        self.addCleanup(lambda: shutil.rmtree(root, ignore_errors=True))
        patchelf = _tool("patchelf")
        provider_prefix = root / "provider"
        provider_lib = provider_prefix / "lib"
        provider_lib.mkdir(parents=True)
        dep = _build_shared(
            provider_lib / "libdep.so.0",
            "int dep(void) { return 7; }\n",
            ["-Wl,-soname,libdep.so.0"],
        )
        prefix_file = _write_prefix(
            root,
            name="sample",
            version="1.0",
            package_file="sample/__init__.py",
            tag=f"cp{sys.version_info.major}{sys.version_info.minor}-cp{sys.version_info.major}{sys.version_info.minor}-linux_x86_64",
        )
        prefix = Path(prefix_file.read_text(encoding="utf-8").strip())
        site_packages = runtime_wheelhouse._site_packages(prefix)
        ext_suffix = sysconfig.get_config_var("EXT_SUFFIX") or ".so"
        native = _build_shared(
            site_packages / "sample" / ("_native" + ext_suffix),
            "extern int dep(void); int value(void) { return dep(); }\n",
            [str(dep), f"-Wl,-rpath,{provider_lib}"],
        )
        provider_prefix_file = root / "provider.prefix.txt"
        provider_prefix_file.write_text(str(provider_prefix) + "\n", encoding="utf-8")

        wheels = runtime_wheelhouse.build_wheelhouse(
            [prefix_file],
            out_dir=root / "wheelhouse",
            manifest_out=root / "manifest.json",
            provider_prefix_files=[provider_prefix_file],
            patchelf=patchelf,
        )

        py_tag = f"cp{sys.version_info.major}{sys.version_info.minor}"
        self.assertEqual([wheel.name for wheel in wheels], [f"sample-1.0-{py_tag}-{py_tag}-linux_x86_64.whl"])
        with zipfile.ZipFile(wheels[0]) as zf:
            names = set(zf.namelist())
            self.assertIn("sample.libs/libdep.so.0", names)
            extract = root / "extract"
            zf.extractall(extract)
            rows = list(csv.reader(zf.read("sample-1.0.dist-info/RECORD").decode("utf-8").splitlines()))
            record_names = {row[0] for row in rows}
            self.assertIn("sample.libs/libdep.so.0", record_names)

        dynamic = _readelf_dynamic(extract / "sample" / native.name)
        self.assertIn("Shared library: [libdep.so.0]", dynamic)
        self.assertIn("Library runpath: [$ORIGIN/../sample.libs]", dynamic)
        self.assertNotIn(str(provider_lib), dynamic)


if __name__ == "__main__":
    unittest.main()
