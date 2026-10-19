#!/usr/bin/env python3
"""Tests for the release-wheel relocatability guard."""

from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
import zipfile
from pathlib import Path


SCRIPT = Path(__file__).with_name("relocatability_guard.py")
SPEC = importlib.util.spec_from_file_location("relocatability_guard", SCRIPT)
assert SPEC is not None
guard = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = guard
SPEC.loader.exec_module(guard)


def rule_fixture(rule_id: str, test_method: str) -> tuple[str, str]:
    assert rule_id in guard.RULE_IDS
    return rule_id, test_method


RULE_FIXTURES = dict(
    (
        rule_fixture("bad-needed", "test_rejects_absolute_needed_outside_wheel_and_rootfs"),
        rule_fixture("bad-rpath", "test_rejects_absolute_runpath_outside_rootfs_allowlist"),
        rule_fixture("forbidden-path-string", "test_rejects_forbidden_build_paths_in_metadata"),
    )
)


def _tmp_root() -> Path:
    base = os.environ.get("TEST_TMPDIR") or os.environ.get("TMPDIR")
    return Path(tempfile.mkdtemp(dir=base))


def _write_lock(path: Path) -> None:
    path.write_text(
        textwrap.dedent(
            """\
            {
              "lines": {
                "cu130": {
                  "runtime_driver": {
                    "bind_libs": ["/run/nvidia-driver/lib/libcuda.so.1"]
                  },
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


def _build_shared(path: Path, source: str, extra: list[str] | None = None) -> Path:
    cc = os.environ.get("CC", "/usr/bin/gcc")
    if not Path(cc).exists() and shutil.which(cc) is None:
        raise unittest.SkipTest(f"compiler not available: {cc}")
    path.with_suffix(".c").write_text(source, encoding="utf-8")
    cmd = [cc, "-shared", "-nostdlib", "-fPIC", str(path.with_suffix(".c")), "-o", str(path)]
    if extra:
        cmd.extend(extra)
    subprocess.run(cmd, check=True)
    return path


def _patchelf(*args: str) -> None:
    exe = shutil.which("patchelf")
    if exe is None:
        raise unittest.SkipTest("patchelf not available")
    subprocess.run([exe, *args], check=True)


class RelocatabilityGuardTest(unittest.TestCase):
    def test_rule_fixture_manifest_matches_guard_rule_ids(self) -> None:
        self.assertEqual(set(RULE_FIXTURES), guard.RULE_IDS)
        missing = sorted(name for name in RULE_FIXTURES.values() if not hasattr(self, name))
        self.assertEqual(missing, [])

    def test_rejects_absolute_runpath_outside_rootfs_allowlist(self) -> None:
        root = _tmp_root()
        self.addCleanup(lambda: shutil.rmtree(root, ignore_errors=True))
        lock = root / "lock.json"
        _write_lock(lock)
        lib = _build_shared(root / "libbad_rpath.so", "int value(void) { return 1; }\n")
        _patchelf("--set-rpath", "/opt/spack/lib", str(lib))

        errors = guard.check_paths([lib], lock_path=lock, line="cu130", root=Path("/"))

        self.assertEqual(
            [error.rule_id for error in errors],
            ["bad-rpath"],
        )
        self.assertIn("/opt/spack/lib", errors[0].message)

    def test_rejects_absolute_needed_outside_wheel_and_rootfs(self) -> None:
        root = _tmp_root()
        self.addCleanup(lambda: shutil.rmtree(root, ignore_errors=True))
        lock = root / "lock.json"
        _write_lock(lock)
        dep = _build_shared(
            root / "libexternal.so",
            "int dep(void) { return 7; }\n",
            ["-Wl,-soname,/opt/spack/lib/libexternal.so"],
        )
        lib = _build_shared(
            root / "libneeds_external.so",
            "extern int dep(void); int value(void) { return dep(); }\n",
            [str(dep)],
        )

        errors = guard.check_paths([lib], lock_path=lock, line="cu130", root=Path("/"))

        self.assertEqual(
            [error.rule_id for error in errors],
            ["bad-needed"],
        )
        self.assertIn("/opt/spack/lib/libexternal.so", errors[0].message)

    def test_rejects_forbidden_build_paths_in_metadata(self) -> None:
        root = _tmp_root()
        self.addCleanup(lambda: shutil.rmtree(root, ignore_errors=True))
        lock = root / "lock.json"
        _write_lock(lock)
        wheel = root / "sample-1.0-py3-none-any.whl"
        with zipfile.ZipFile(wheel, "w") as zf:
            zf.writestr("sample/__init__.py", "BUILD_PREFIX = '/vaso/lines/cu130/cache/bazel/output-base'\n")

        errors = guard.check_paths([wheel], lock_path=lock, line="cu130", root=Path("/"))

        self.assertEqual(
            [error.rule_id for error in errors],
            ["forbidden-path-string"],
        )
        self.assertIn("/vaso/", errors[0].message)

    def test_allows_forbidden_build_paths_inside_elf_payload(self) -> None:
        root = _tmp_root()
        self.addCleanup(lambda: shutil.rmtree(root, ignore_errors=True))
        lock = root / "lock.json"
        _write_lock(lock)
        lib = root / "libdebug_path.so"
        lib.write_bytes(b"\x7fELF/vaso/lines/cu130/work/pytorch/aten/native/cuda/Foo.cu\0")

        errors = guard.check_paths([lib], lock_path=lock, line="cu130", root=Path("/"))

        self.assertEqual(errors, [])

    def test_allows_output_base_identifier_without_build_path(self) -> None:
        root = _tmp_root()
        self.addCleanup(lambda: shutil.rmtree(root, ignore_errors=True))
        lock = root / "lock.json"
        _write_lock(lock)
        wheel = root / "sample-1.0-py3-none-any.whl"
        with zipfile.ZipFile(wheel, "w") as zf:
            zf.writestr(
                "sample/include/TensorIterator.h",
                "void* output_base();\n#include <kineto/output_base.h>\n",
            )

        errors = guard.check_paths([wheel], lock_path=lock, line="cu130", root=Path("/"))

        self.assertEqual(errors, [])

    def test_allows_vaso_provenance_record_name_when_estate_root_is_vaso_mount(self) -> None:
        root = _tmp_root()
        self.addCleanup(lambda: shutil.rmtree(root, ignore_errors=True))
        lock = root / "lock.json"
        _write_lock(lock)
        wheel = root / "sample-1.0-py3-none-any.whl"
        with zipfile.ZipFile(wheel, "w") as zf:
            zf.writestr("sample/__init__.py", "")
            zf.writestr(
                "sample-1.0.dist-info/RECORD",
                "sample-1.0.dist-info/vaso_provenance.json,sha256=abc,123\n",
            )
        old = os.environ.get("VASO_ESTATE_ROOT")
        os.environ["VASO_ESTATE_ROOT"] = "/vaso"
        try:
            errors = guard.check_paths([wheel], lock_path=lock, line="cu130", root=Path("/"))
        finally:
            if old is None:
                os.environ.pop("VASO_ESTATE_ROOT", None)
            else:
                os.environ["VASO_ESTATE_ROOT"] = old

        self.assertEqual(errors, [])

    def test_rootfs_soname_lookup_follows_internal_absolute_symlinks(self) -> None:
        root = _tmp_root()
        self.addCleanup(lambda: shutil.rmtree(root, ignore_errors=True))
        (root / "usr/local").mkdir(parents=True)
        (root / "etc/alternatives").mkdir(parents=True)
        (root / "usr/local/cuda-13.0/targets/x86_64-linux/lib").mkdir(parents=True)
        (root / "usr/local/cuda").symlink_to("/etc/alternatives/cuda")
        (root / "etc/alternatives/cuda").symlink_to("/usr/local/cuda-13.0")
        (root / "usr/local/cuda-13.0/lib64").symlink_to("targets/x86_64-linux/lib")
        (root / "usr/local/cuda-13.0/targets/x86_64-linux/lib/libcudart.so.13").write_text(
            "",
            encoding="utf-8",
        )

        self.assertTrue(
            guard._soname_exists_under_root("libcudart.so.13", root, ("/usr/local/cuda/lib64",))
        )

    def test_allows_explicit_runtime_driver_bind_soname(self) -> None:
        root = _tmp_root()
        self.addCleanup(lambda: shutil.rmtree(root, ignore_errors=True))
        lock = root / "lock.json"
        _write_lock(lock)
        dep = _build_shared(root / "libcuda_driver.so", "int driver(void) { return 7; }\n")
        _patchelf("--set-soname", "libcuda.so.1", str(dep))
        lib = _build_shared(
            root / "libneeds_driver.so",
            "extern int driver(void); int value(void) { return driver(); }\n",
            [str(dep)],
        )

        errors = guard.check_paths([lib], lock_path=lock, line="cu130", root=root)

        self.assertEqual(errors, [])

    def test_rejects_unlisted_driver_dir_soname(self) -> None:
        root = _tmp_root()
        self.addCleanup(lambda: shutil.rmtree(root, ignore_errors=True))
        lock = root / "lock.json"
        _write_lock(lock)
        dep = _build_shared(root / "libnot_driver.so", "int dep(void) { return 7; }\n")
        _patchelf("--set-soname", "libnotdriver.so.1", str(dep))
        lib = _build_shared(
            root / "libneeds_not_driver.so",
            "extern int dep(void); int value(void) { return dep(); }\n",
            [str(dep)],
        )

        errors = guard.check_paths([lib], lock_path=lock, line="cu130", root=root)

        self.assertEqual([error.rule_id for error in errors], ["bad-needed"])

    def test_scans_wheel_under_long_absolute_path(self) -> None:
        root = _tmp_root()
        self.addCleanup(lambda: shutil.rmtree(root, ignore_errors=True))
        lock = root / "lock.json"
        _write_lock(lock)
        wheel_dir = root / ("a" * 80) / ("b" * 80) / ("c" * 80)
        wheel_dir.mkdir(parents=True)
        wheel = wheel_dir / "sample-1.0-py3-none-any.whl"
        with zipfile.ZipFile(wheel, "w") as zf:
            zf.writestr(f"sample/{'lib' + ('x' * 120)}.so", b"\x7fELFnot-a-real-shared-object")

        errors = guard.check_paths([wheel], lock_path=lock, line="cu130", root=Path("/"))

        self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()
