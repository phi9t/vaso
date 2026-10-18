#!/usr/bin/env python3
"""Tests for release-wheel repair."""

from __future__ import annotations

import base64
import csv
import hashlib
import importlib.util
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import textwrap
import unittest
import zipfile
from pathlib import Path, PurePosixPath


SCRIPT = Path(__file__).with_name("repair_wheel.py")
SPEC = importlib.util.spec_from_file_location("repair_wheel", SCRIPT)
assert SPEC is not None
repair = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = repair
SPEC.loader.exec_module(repair)


def _tmp_root() -> Path:
    base = os.environ.get("TEST_TMPDIR") or os.environ.get("TMPDIR")
    return Path(tempfile.mkdtemp(dir=base))


def _patchelf(*args: str) -> None:
    exe = shutil.which("patchelf")
    if exe is None:
        raise unittest.SkipTest("patchelf not available")
    subprocess.run([exe, *args], check=True)


def _readelf_dynamic(path: Path) -> str:
    exe = shutil.which("readelf") or "readelf"
    return subprocess.run([exe, "-d", str(path)], check=True, stdout=subprocess.PIPE, text=True).stdout


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


def _write_lock(path: Path) -> None:
    path.write_text(
        textwrap.dedent(
            """\
            {
              "lines": {
                "cu130": {
                  "components": {
                    "cuda_toolkit": {
                      "install_layout": {"lib": "/usr/local/cuda/lib64", "prefix": "/usr/local/cuda"}
                    }
                  }
                }
              }
            }
            """
        ),
        encoding="utf-8",
    )


def _write_spack_lock(path: Path) -> None:
    path.write_text('{"roots":[{"name":"py-torch"}]}\n', encoding="utf-8")


def _write_rootfs_manifest(path: Path, digest_hex: str = "d") -> None:
    path.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "rootfs_bundle": {
                    "digest": "sha256:" + (digest_hex * 64),
                },
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


def _write_provider_metadata(path: Path, rootfs_manifest: Path, action_input_digest: str = "input-digest-1234") -> None:
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "package": "py-torch",
                "version": "1.0",
                "action_input_digest": action_input_digest,
                "rootfs_manifest": str(rootfs_manifest),
                "input_prefixes": {},
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


def _record_digest(data: bytes) -> str:
    digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).decode("ascii").rstrip("=")
    return f"sha256={digest}"


class RepairWheelTest(unittest.TestCase):
    def test_rootfs_library_dirs_include_cuda_target_lib_for_cupti(self) -> None:
        lock = {
            "lines": {
                "cu130": {
                    "components": {
                        "cuda_toolkit": {
                            "install_layout": {
                                "lib": "/usr/local/cuda/lib64",
                                "prefix": "/usr/local/cuda",
                            }
                        }
                    }
                }
            }
        }

        self.assertEqual(
            repair.rootfs_library_dirs(lock, "cu130"),
            ("/usr/local/cuda/lib64", "/usr/local/cuda/targets/x86_64-linux/lib"),
        )

    def test_writes_vaso_provenance_and_record_entry(self) -> None:
        root = _tmp_root()
        self.addCleanup(lambda: shutil.rmtree(root, ignore_errors=True))
        py_tag = "cp" + "313"
        wheel = root / f"sample-1.0-{py_tag}-{py_tag}-linux_x86_64.whl"
        lock = root / "lock.json"
        spack_lock = root / "spack_graph.lock.json"
        rootfs_manifest = root / "rootfs-bundle.json"
        provider_metadata = root / "provider_metadata.json"
        _write_lock(lock)
        _write_spack_lock(spack_lock)
        _write_rootfs_manifest(rootfs_manifest, "c")
        _write_provider_metadata(provider_metadata, rootfs_manifest)
        with zipfile.ZipFile(wheel, "w") as zf:
            zf.writestr("sample/__init__.py", "__version__ = '1.0'\n")
            zf.writestr("sample-1.0.dist-info/METADATA", "Name: sample\nVersion: 1.0\n")
            zf.writestr("sample-1.0.dist-info/WHEEL", "Wheel-Version: 1.0\n")
            zf.writestr("sample-1.0.dist-info/RECORD", "")

        repaired = repair.repair_wheel(
            wheel,
            out_dir=root / "out",
            lock_path=lock,
            spack_lock_path=spack_lock,
            profile="torch",
            line="cu130",
            git_sha="abcdef1234567890",
            provider_metadata=provider_metadata,
        )

        provenance_rel = "sample-1.0+vaso.cu130.gabcdef12.dist-info/vaso_provenance.json"
        with zipfile.ZipFile(repaired) as zf:
            provenance_data = zf.read(provenance_rel)
            self.assertEqual(
                json.loads(provenance_data),
                {
                    "schema_version": 1,
                    "action_input_digest": "input-digest-1234",
                    "declared_cuda_architectures": ["sm_100"],
                    "git_sha": "abcdef1234567890",
                    "line": "cu130",
                    "profile": "torch",
                    "provider_package": "py-torch",
                    "rootfs_bundle_digest": "sha256:" + ("c" * 64),
                    "rootfs_lock_sha256": "sha256:" + hashlib.sha256(lock.read_bytes()).hexdigest(),
                    "spack_lock_sha256": "sha256:" + hashlib.sha256(spack_lock.read_bytes()).hexdigest(),
                    "upstream_version": "1.0",
                },
            )
            rows = list(csv.reader(zf.read("sample-1.0+vaso.cu130.gabcdef12.dist-info/RECORD").decode().splitlines()))
            record = {row[0]: row[1:] for row in rows}
            self.assertEqual(record[provenance_rel], [_record_digest(provenance_data), str(len(provenance_data))])

    def test_declared_cuda_architectures_are_derived_from_provider_metadata(self) -> None:
        self.assertEqual(
            repair._declared_cuda_architectures(
                {
                    "package": "py-torch",
                    "torch_cuda_arch_list": "9.0;10.0+PTX",
                }
            ),
            ["sm_90", "sm_100+PTX"],
        )
        self.assertEqual(
            repair._declared_cuda_architectures(
                {
                    "package": "py-jaxlib",
                    "cuda": {"compute_capabilities": ["sm_100"]},
                }
            ),
            ["sm_100"],
        )
        self.assertEqual(
            repair._declared_cuda_architectures({"package": "py-torchvision"}),
            ["sm_100"],
        )
        self.assertEqual(
            repair._declared_cuda_architectures({"package": "py-torchaudio"}),
            ["sm_100"],
        )
        self.assertEqual(
            repair._declared_cuda_architectures({"package": "py-triton"}),
            [],
        )

    def test_provider_metadata_relative_prefixes_prefer_output_base_external(self) -> None:
        root = _tmp_root()
        self.addCleanup(lambda: shutil.rmtree(root, ignore_errors=True))
        output_base = root / "output-base"
        metadata = output_base / "execroot/_main/bazel-out/k8-fastbuild/bin/native/pytorch/metadata.json"
        metadata.parent.mkdir(parents=True)
        (output_base / "execroot/_main/external").mkdir(parents=True)
        provider = output_base / "external/+cpuinfo_native+cpuinfo_native/prefix"
        (provider / "lib").mkdir(parents=True)
        doc = {"input_prefixes": {"cpuinfo": "external/+cpuinfo_native+cpuinfo_native/prefix"}}

        self.assertEqual(repair._provider_prefixes_from_metadata(doc, relative_to=repair._provider_prefix_base(metadata)), [provider])

    def test_synthetic_declared_wheel_filename_uses_archive_metadata_for_output_name(self) -> None:
        root = _tmp_root()
        self.addCleanup(lambda: shutil.rmtree(root, ignore_errors=True))
        py_tag = "cp" + "313"
        wheel = root / "sample_action_wheel.whl"
        lock = root / "lock.json"
        spack_lock = root / "spack_graph.lock.json"
        rootfs_manifest = root / "rootfs-bundle.json"
        provider_metadata = root / "provider_metadata.json"
        _write_lock(lock)
        _write_spack_lock(spack_lock)
        _write_rootfs_manifest(rootfs_manifest)
        _write_provider_metadata(provider_metadata, rootfs_manifest)
        with zipfile.ZipFile(wheel, "w") as zf:
            zf.writestr("sample/__init__.py", "__version__ = '1.0'\n")
            zf.writestr("sample-1.0.dist-info/METADATA", "Name: sample\nVersion: 1.0\n")
            zf.writestr(
                "sample-1.0.dist-info/WHEEL",
                f"Wheel-Version: 1.0\nGenerator: test\nRoot-Is-Purelib: false\nTag: {py_tag}-{py_tag}-linux_x86_64\n",
            )
            zf.writestr("sample-1.0.dist-info/RECORD", "")

        repaired = repair.repair_wheel(
            wheel,
            out_dir=root / "out",
            lock_path=lock,
            spack_lock_path=spack_lock,
            profile="torch",
            line="cu130",
            git_sha="abcdef1234567890",
            provider_metadata=provider_metadata,
        )

        self.assertEqual(repaired.name, f"sample-1.0+vaso.cu130.gabcdef12-{py_tag}-{py_tag}-linux_x86_64.whl")

    def test_existing_local_version_is_replaced_by_vaso_suffix(self) -> None:
        root = _tmp_root()
        self.addCleanup(lambda: shutil.rmtree(root, ignore_errors=True))
        wheel = root / "jax_cuda13_pjrt-0.10.2.dev0+selfbuilt-py3-none-manylinux_2_28_x86_64.whl"
        lock = root / "lock.json"
        spack_lock = root / "spack_graph.lock.json"
        rootfs_manifest = root / "rootfs-bundle.json"
        provider_metadata = root / "provider_metadata.json"
        _write_lock(lock)
        _write_spack_lock(spack_lock)
        _write_rootfs_manifest(rootfs_manifest)
        _write_provider_metadata(provider_metadata, rootfs_manifest)
        with zipfile.ZipFile(wheel, "w") as zf:
            zf.writestr("jax_plugins/__init__.py", "")
            zf.writestr(
                "jax_cuda13_pjrt-0.10.2.dev0+selfbuilt.dist-info/METADATA",
                "Name: jax_cuda13_pjrt\nVersion: 0.10.2.dev0+selfbuilt\n",
            )
            zf.writestr(
                "jax_cuda13_pjrt-0.10.2.dev0+selfbuilt.dist-info/WHEEL",
                "Wheel-Version: 1.0\nTag: py3-none-manylinux_2_28_x86_64\n",
            )
            zf.writestr("jax_cuda13_pjrt-0.10.2.dev0+selfbuilt.dist-info/RECORD", "")

        repaired = repair.repair_wheel(
            wheel,
            out_dir=root / "out",
            lock_path=lock,
            spack_lock_path=spack_lock,
            profile="jax",
            line="cu130",
            git_sha="abcdef1234567890",
            provider_metadata=provider_metadata,
        )

        release_version = "0.10.2.dev0+vaso.cu130.gabcdef12"
        self.assertEqual(
            repaired.name,
            f"jax_cuda13_pjrt-{release_version}-py3-none-manylinux_2_28_x86_64.whl",
        )
        with zipfile.ZipFile(repaired) as zf:
            names = set(zf.namelist())
            self.assertIn(f"jax_cuda13_pjrt-{release_version}.dist-info/METADATA", names)
            metadata = zf.read(f"jax_cuda13_pjrt-{release_version}.dist-info/METADATA").decode()
            self.assertIn(f"Version: {release_version}\n", metadata)

    def test_rewrites_torch_aten_config_include_dir_to_installed_relative_path(self) -> None:
        root = _tmp_root()
        self.addCleanup(lambda: shutil.rmtree(root, ignore_errors=True))
        py_tag = "cp" + "313"
        wheel = root / f"torch-2.14.0-{py_tag}-{py_tag}-linux_x86_64.whl"
        lock = root / "lock.json"
        spack_lock = root / "spack_graph.lock.json"
        rootfs_manifest = root / "rootfs-bundle.json"
        provider_metadata = root / "provider_metadata.json"
        _write_lock(lock)
        _write_spack_lock(spack_lock)
        _write_rootfs_manifest(rootfs_manifest)
        _write_provider_metadata(provider_metadata, rootfs_manifest)
        with zipfile.ZipFile(wheel, "w") as zf:
            zf.writestr("torch/__init__.py", "__version__ = '2.14.0'\n")
            zf.writestr(
                "torch/share/cmake/ATen/ATenConfig.cmake",
                textwrap.dedent(
                    """\
                    set(ATEN_FOUND 1)
                    set(ATEN_INCLUDE_DIR "/vaso/tmp/action/wheel/platlib/torch/include")
                    set(ATEN_LIBRARIES "")
                    """
                ),
            )
            zf.writestr("torch-2.14.0.dist-info/METADATA", "Name: torch\nVersion: 2.14.0\n")
            zf.writestr("torch-2.14.0.dist-info/WHEEL", f"Wheel-Version: 1.0\nTag: {py_tag}-{py_tag}-linux_x86_64\n")
            zf.writestr("torch-2.14.0.dist-info/RECORD", "")

        repaired = repair.repair_wheel(
            wheel,
            out_dir=root / "out",
            lock_path=lock,
            spack_lock_path=spack_lock,
            profile="torch",
            line="cu130",
            git_sha="abcdef1234567890",
            provider_metadata=provider_metadata,
        )

        with zipfile.ZipFile(repaired) as zf:
            config = zf.read("torch/share/cmake/ATen/ATenConfig.cmake").decode()
            self.assertIn('set(ATEN_INCLUDE_DIR "${CMAKE_CURRENT_LIST_DIR}/../../../include")', config)
            self.assertNotIn("/vaso/tmp", config)

    def test_scrubs_build_root_strings_before_record_generation(self) -> None:
        root = _tmp_root()
        self.addCleanup(lambda: shutil.rmtree(root, ignore_errors=True))
        py_tag = "cp" + "313"
        wheel = root / f"sample-1.0-{py_tag}-{py_tag}-linux_x86_64.whl"
        lock = root / "lock.json"
        spack_lock = root / "spack_graph.lock.json"
        rootfs_manifest = root / "rootfs-bundle.json"
        provider_metadata = root / "provider_metadata.json"
        leaked_payload = (
            b"prefix\0"
            b"/vaso/lines/cu130/work/pytorch/aten/native/cuda/Foo.cu\0"
            b"$VASO_ESTATE_ROOT/.vaso-estate/vaso/lines/cu130/cache/bazel/output-base-pb-live/execroot/_main/foo.cc\0"
            b"tail\n"
        )
        _write_lock(lock)
        _write_spack_lock(spack_lock)
        _write_rootfs_manifest(rootfs_manifest)
        _write_provider_metadata(provider_metadata, rootfs_manifest)
        with zipfile.ZipFile(wheel, "w") as zf:
            zf.writestr("sample/debug-paths.bin", leaked_payload)
            zf.writestr("sample-1.0.dist-info/METADATA", "Name: sample\nVersion: 1.0\n")
            zf.writestr("sample-1.0.dist-info/WHEEL", f"Wheel-Version: 1.0\nTag: {py_tag}-{py_tag}-linux_x86_64\n")
            zf.writestr("sample-1.0.dist-info/RECORD", "")

        repaired = repair.repair_wheel(
            wheel,
            out_dir=root / "out",
            lock_path=lock,
            spack_lock_path=spack_lock,
            profile="torch",
            line="cu130",
            git_sha="abcdef1234567890",
            provider_metadata=provider_metadata,
        )

        with zipfile.ZipFile(repaired) as zf:
            data = zf.read("sample/debug-paths.bin")
            self.assertEqual(len(data), len(leaked_payload))
            self.assertNotIn(b"/vaso/", data)
            self.assertNotIn(b".vaso-estate", data)
            self.assertNotIn(b"output-base", data)
            rows = list(csv.reader(zf.read("sample-1.0+vaso.cu130.gabcdef12.dist-info/RECORD").decode().splitlines()))
            record = {row[0]: row[1:] for row in rows}
            self.assertEqual(record["sample/debug-paths.bin"], [_record_digest(data), str(len(data))])

    def test_scrubber_preserves_elf_payload_build_path_strings(self) -> None:
        root = _tmp_root()
        self.addCleanup(lambda: shutil.rmtree(root, ignore_errors=True))
        elf_payload = (
            b"\x7fELF"
            b"prefix\0"
            b"/vaso/lines/cu130/work/pytorch/aten/native/cuda/Foo.cu\0"
            b"$VASO_ESTATE_ROOT/.vaso-estate/vaso/lines/cu130/cache/bazel/output-base-pb-live/execroot/_main/foo.cc\0"
            b"tail\n"
        )
        text_payload = b"BUILD_PREFIX=/vaso/lines/cu130/cache/bazel/output-base\n"
        elf = root / "sample" / "_native.so"
        metadata = root / "sample" / "build-paths.txt"
        elf.parent.mkdir(parents=True)
        elf.write_bytes(elf_payload)
        metadata.write_bytes(text_payload)

        repair._scrub_build_paths(root)

        self.assertEqual(elf.read_bytes(), elf_payload)
        self.assertNotIn(b"/vaso/", metadata.read_bytes())
        self.assertNotIn(b"output-base", metadata.read_bytes())

    def test_scrubber_keeps_vaso_release_version_when_estate_root_is_vaso_mount(self) -> None:
        old = os.environ.get("VASO_ESTATE_ROOT")
        os.environ["VASO_ESTATE_ROOT"] = "/vaso"
        try:
            self.assertEqual(
                repair._scrub_build_path_bytes(b"sample-1.0.dist-info/vaso_provenance.json\n"),
                b"sample-1.0.dist-info/vaso_provenance.json\n",
            )
            self.assertNotIn(
                b"/vaso/",
                repair._scrub_build_path_bytes(b"BUILD_PREFIX=/vaso/lines/cu130/work/pytorch\n"),
            )
        finally:
            if old is None:
                os.environ.pop("VASO_ESTATE_ROOT", None)
            else:
                os.environ["VASO_ESTATE_ROOT"] = old

        root = _tmp_root()
        self.addCleanup(lambda: shutil.rmtree(root, ignore_errors=True))
        py_tag = "cp" + "313"
        wheel = root / f"sample-1.0-{py_tag}-{py_tag}-linux_x86_64.whl"
        lock = root / "lock.json"
        spack_lock = root / "spack_graph.lock.json"
        rootfs_manifest = root / "rootfs-bundle.json"
        provider_metadata = root / "provider_metadata.json"
        _write_lock(lock)
        _write_spack_lock(spack_lock)
        _write_rootfs_manifest(rootfs_manifest)
        _write_provider_metadata(provider_metadata, rootfs_manifest)
        with zipfile.ZipFile(wheel, "w") as zf:
            zf.writestr("sample/__init__.py", "__version__ = '1.0+vaso.local'\n")
            zf.writestr("sample-1.0.dist-info/METADATA", "Name: sample\nVersion: 1.0\n")
            zf.writestr("sample-1.0.dist-info/WHEEL", f"Wheel-Version: 1.0\nTag: {py_tag}-{py_tag}-linux_x86_64\n")
            zf.writestr("sample-1.0.dist-info/RECORD", "")
        old = os.environ.get("VASO_ESTATE_ROOT")
        os.environ["VASO_ESTATE_ROOT"] = "/vaso"
        try:
            repaired = repair.repair_wheel(
                wheel,
                out_dir=root / "out",
                lock_path=lock,
                spack_lock_path=spack_lock,
                profile="torch",
                line="cu130",
                git_sha="abcdef1234567890",
                provider_metadata=provider_metadata,
            )
        finally:
            if old is None:
                os.environ.pop("VASO_ESTATE_ROOT", None)
            else:
                os.environ["VASO_ESTATE_ROOT"] = old

        with zipfile.ZipFile(repaired) as zf:
            self.assertEqual(zf.read("sample/__init__.py"), b"__version__ = '1.0+vaso.local'\n")
            record = zf.read("sample-1.0+vaso.cu130.gabcdef12.dist-info/RECORD")
            self.assertIn(b"sample-1.0+vaso.cu130.gabcdef12.dist-info/vaso_provenance.json", record)

    def test_rewrites_runpath_vendors_non_rootfs_dep_and_updates_record(self) -> None:
        root = _tmp_root()
        self.addCleanup(lambda: shutil.rmtree(root, ignore_errors=True))
        provider_lib = root / "provider" / "lib"
        provider_lib.mkdir(parents=True)
        dep = _build_shared(
            provider_lib / "libvendor.so",
            "int dep(void) { return 7; }\n",
            ["-Wl,-soname,libvendor.so"],
        )
        native = _build_shared(
            root / "sample" / "_native.so",
            "extern int dep(void); int value(void) { return dep(); }\n",
            [str(dep)],
        )
        _patchelf("--set-rpath", f"$ORIGIN:/opt/spack/lib:{provider_lib}", str(native))
        py_tag = "cp" + "313"
        wheel = root / f"sample-1.0-{py_tag}-{py_tag}-linux_x86_64.whl"
        lock = root / "lock.json"
        spack_lock = root / "spack_graph.lock.json"
        rootfs_manifest = root / "rootfs-bundle.json"
        provider_metadata = root / "provider_metadata.json"
        out_dir = root / "out"
        _write_lock(lock)
        _write_spack_lock(spack_lock)
        _write_rootfs_manifest(rootfs_manifest, "b")
        _write_provider_metadata(provider_metadata, rootfs_manifest)
        with zipfile.ZipFile(wheel, "w") as zf:
            zf.write(native, "sample/_native.so")
            zf.writestr("sample/__init__.py", "__version__ = '1.0'\n")
            zf.writestr("sample-1.0.dist-info/METADATA", "Name: sample\nVersion: 1.0\n")
            zf.writestr("sample-1.0.dist-info/WHEEL", "Wheel-Version: 1.0\n")
            zf.writestr("sample-1.0.dist-info/RECORD", "")
        repaired = repair.repair_wheel(
            wheel,
            out_dir=out_dir,
            lock_path=lock,
            spack_lock_path=spack_lock,
            profile="torch",
            line="cu130",
            git_sha="abcdef1234567890",
            provider_prefixes=[root / "provider"],
            provider_metadata=provider_metadata,
        )

        self.assertEqual(repaired.name, f"sample-1.0+vaso.cu130.gabcdef12-{py_tag}-{py_tag}-linux_x86_64.whl")
        with zipfile.ZipFile(repaired) as zf:
            names = set(zf.namelist())
            self.assertIn("sample.libs/libvendor.so", names)
            self.assertIn("sample-1.0+vaso.cu130.gabcdef12.dist-info/METADATA", names)
            metadata = zf.read("sample-1.0+vaso.cu130.gabcdef12.dist-info/METADATA").decode()
            self.assertIn("Version: 1.0+vaso.cu130.gabcdef12\n", metadata)
            provenance_rel = "sample-1.0+vaso.cu130.gabcdef12.dist-info/vaso_provenance.json"
            self.assertIn(provenance_rel, names)
            provenance_data = zf.read(provenance_rel)
            provenance = json.loads(provenance_data)
            self.assertEqual(
                provenance,
                {
                    "schema_version": 1,
                    "action_input_digest": "input-digest-1234",
                    "declared_cuda_architectures": ["sm_100"],
                    "git_sha": "abcdef1234567890",
                    "line": "cu130",
                    "profile": "torch",
                    "provider_package": "py-torch",
                    "rootfs_bundle_digest": "sha256:" + ("b" * 64),
                    "rootfs_lock_sha256": "sha256:" + hashlib.sha256(lock.read_bytes()).hexdigest(),
                    "spack_lock_sha256": "sha256:" + hashlib.sha256(spack_lock.read_bytes()).hexdigest(),
                    "upstream_version": "1.0",
                },
            )
            rows = list(csv.reader(zf.read("sample-1.0+vaso.cu130.gabcdef12.dist-info/RECORD").decode().splitlines()))
            record = {row[0]: row[1:] for row in rows}
            self.assertIn("sample/_native.so", record)
            self.assertEqual(record["sample/__init__.py"][0], _record_digest(b"__version__ = '1.0'\n"))
            self.assertEqual(record[provenance_rel], [_record_digest(provenance_data), str(len(provenance_data))])
            self.assertEqual(record["sample-1.0+vaso.cu130.gabcdef12.dist-info/RECORD"], ["", ""])
            extract = root / "extract"
            zf.extractall(extract)

        dynamic = _readelf_dynamic(extract / "sample" / "_native.so")
        self.assertIn(
            "Library runpath: [$ORIGIN:$ORIGIN/../sample.libs:/usr/local/cuda/lib64:/usr/local/cuda/targets/x86_64-linux/lib]",
            dynamic,
        )
        self.assertNotIn("/opt/spack/lib", dynamic)

    def test_cuda_family_dependency_is_not_vendored(self) -> None:
        sonames = (
            "libcudart.so.13",
            "libcublasLt.so.13",
            "libcudss.so.0",
            "libnvshmem_host.so.3",
        )
        for soname in sonames:
            with self.subTest(soname=soname):
                self.assertTrue(repair._is_cuda_family(soname))
                root = _tmp_root()
                self.addCleanup(lambda root=root: shutil.rmtree(root, ignore_errors=True))
                provider_lib = root / "provider" / "lib"
                provider_lib.mkdir(parents=True)
                dep = _build_shared(
                    provider_lib / soname,
                    "int cuda_runtime(void) { return 1; }\n",
                    [f"-Wl,-soname,{soname}"],
                )
                native = _build_shared(
                    root / "sample" / "_cuda.so",
                    "extern int cuda_runtime(void); int value(void) { return cuda_runtime(); }\n",
                    [str(dep)],
                )
                _patchelf("--set-rpath", str(provider_lib), str(native))
                py_tag = "cp" + "313"
                wheel = root / f"sample-1.0-{py_tag}-{py_tag}-linux_x86_64.whl"
                lock = root / "lock.json"
                spack_lock = root / "spack_graph.lock.json"
                rootfs_manifest = root / "rootfs-bundle.json"
                provider_metadata = root / "provider_metadata.json"
                _write_lock(lock)
                _write_spack_lock(spack_lock)
                _write_rootfs_manifest(rootfs_manifest)
                _write_provider_metadata(provider_metadata, rootfs_manifest)
                with zipfile.ZipFile(wheel, "w") as zf:
                    zf.write(native, "sample/_cuda.so")
                    zf.writestr("sample-1.0.dist-info/METADATA", "Name: sample\nVersion: 1.0\n")
                    zf.writestr("sample-1.0.dist-info/WHEEL", "Wheel-Version: 1.0\n")
                    zf.writestr("sample-1.0.dist-info/RECORD", "")

                repaired = repair.repair_wheel(
                    wheel,
                    out_dir=root / "out",
                    lock_path=lock,
                    spack_lock_path=spack_lock,
                    profile="torch",
                    line="cu130",
                    git_sha="abcdef1234567890",
                    provider_prefixes=[root / "provider"],
                    provider_metadata=provider_metadata,
                )

                with zipfile.ZipFile(repaired) as zf:
                    names = set(zf.namelist())
                    self.assertNotIn(f"sample.libs/{soname}", names)
                    extract = root / f"cuda-extract-{soname}"
                    zf.extractall(extract)
                dynamic = _readelf_dynamic(extract / "sample" / "_cuda.so")
                self.assertIn("Library runpath: [/usr/local/cuda/lib64:/usr/local/cuda/targets/x86_64-linux/lib]", dynamic)

    def test_absolute_non_rootfs_needed_is_rewritten_to_vendored_basename(self) -> None:
        root = _tmp_root()
        self.addCleanup(lambda: shutil.rmtree(root, ignore_errors=True))
        provider_lib = root / "provider" / "lib"
        provider_lib.mkdir(parents=True)
        dep = _build_shared(provider_lib / "libabsolute.so", "int dep(void) { return 3; }\n")
        native = _build_shared(
            root / "sample" / "_absolute.so",
            "extern int dep(void); int value(void) { return dep(); }\n",
            [str(dep)],
        )
        _patchelf("--print-needed", str(native))
        self.assertIn(str(dep), _readelf_dynamic(native))

        py_tag = "cp" + "313"
        wheel = root / f"sample-1.0-{py_tag}-{py_tag}-linux_x86_64.whl"
        lock = root / "lock.json"
        spack_lock = root / "spack_graph.lock.json"
        rootfs_manifest = root / "rootfs-bundle.json"
        provider_metadata = root / "provider_metadata.json"
        _write_lock(lock)
        _write_spack_lock(spack_lock)
        _write_rootfs_manifest(rootfs_manifest)
        _write_provider_metadata(provider_metadata, rootfs_manifest)
        with zipfile.ZipFile(wheel, "w") as zf:
            zf.write(native, "sample/_absolute.so")
            zf.writestr("sample-1.0.dist-info/METADATA", "Name: sample\nVersion: 1.0\n")
            zf.writestr("sample-1.0.dist-info/WHEEL", "Wheel-Version: 1.0\n")
            zf.writestr("sample-1.0.dist-info/RECORD", "")

        repaired = repair.repair_wheel(
            wheel,
            out_dir=root / "out",
            lock_path=lock,
            spack_lock_path=spack_lock,
            profile="torch",
            line="cu130",
            git_sha="abcdef1234567890",
            provider_metadata=provider_metadata,
        )

        with zipfile.ZipFile(repaired) as zf:
            self.assertIn("sample.libs/libabsolute.so", set(zf.namelist()))
            extract = root / "absolute-extract"
            zf.extractall(extract)
        dynamic = _readelf_dynamic(extract / "sample" / "_absolute.so")
        self.assertIn("Shared library: [libabsolute.so]", dynamic)
        self.assertNotIn(str(dep), dynamic)

    def test_absolute_needed_replacement_is_not_queued_twice_after_vendoring(self) -> None:
        root = _tmp_root()
        source_root = _tmp_root()
        self.addCleanup(lambda: shutil.rmtree(root, ignore_errors=True))
        self.addCleanup(lambda: shutil.rmtree(source_root, ignore_errors=True))
        elf_path = root / "sample" / "_absolute.so"
        elf_path.parent.mkdir(parents=True)
        elf_path.write_bytes(b"\x7fELFfake")
        dep = source_root / "libabsolute.so"
        dep.write_bytes(b"fake dep")
        elf = repair.ElfFile(PurePosixPath("sample/_absolute.so"), elf_path)
        calls: list[tuple[str, ...]] = []

        original_iter_elfs = repair._iter_elfs
        original_dynamic_info = repair._dynamic_info
        original_run_patchelf = repair._run_patchelf
        try:
            repair._iter_elfs = lambda _root: [elf]
            repair._dynamic_info = lambda *_args, **_kwargs: repair.DynamicInfo(
                needed=(str(dep),),
                rpaths=(),
                soname=None,
            )
            repair._run_patchelf = lambda _patchelf, *args: calls.append(args)

            repair._repair_dynamic_entries(
                root,
                PurePosixPath("sample.libs"),
                ("/usr/local/cuda/lib64",),
                [],
                "patchelf",
                "readelf",
            )
        finally:
            repair._iter_elfs = original_iter_elfs
            repair._dynamic_info = original_dynamic_info
            repair._run_patchelf = original_run_patchelf

        replace_calls = [call for call in calls if call[:1] == ("--replace-needed",)]
        self.assertEqual(replace_calls, [("--replace-needed", str(dep), "libabsolute.so", str(elf_path))])

    def test_copied_vendored_library_is_user_writable_for_patchelf(self) -> None:
        root = _tmp_root()
        self.addCleanup(lambda: shutil.rmtree(root, ignore_errors=True))
        source = root / "provider" / "libreadonly.so"
        destination = root / "wheel" / "sample.libs" / "libreadonly.so"
        source.parent.mkdir(parents=True)
        source.write_bytes(b"\x7fELFreadonly")
        source.chmod(0o555)

        self.assertTrue(repair._copy_vendored_library(source, destination))

        mode = destination.stat().st_mode
        self.assertTrue(mode & stat.S_IXUSR)
        self.assertTrue(mode & stat.S_IWUSR)


if __name__ == "__main__":
    unittest.main()
