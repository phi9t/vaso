#!/usr/bin/env python3
"""Fixture tests for cubin_guard.py."""

from __future__ import annotations

import json
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


SCRIPT = Path(__file__).with_name("cubin_guard.py")
SPEC = importlib.util.spec_from_file_location("cubin_guard", SCRIPT)
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
        rule_fixture("missing-cubin", "test_fails_and_names_extra_declared_arch_without_cubin"),
        rule_fixture("missing-ptx", "test_fails_and_names_declared_ptx_arch_without_ptx"),
        rule_fixture("missing-declared-architectures", "test_fails_cuda_object_without_declared_architectures"),
    )
)


def _tmp_root() -> Path:
    base = os.environ.get("TEST_TMPDIR") or os.environ.get("TMPDIR")
    return Path(tempfile.mkdtemp(dir=base))


def _runfiles_path(rel: str) -> Path:
    candidate = Path(rel)
    if candidate.is_absolute() and candidate.exists():
        return candidate
    for env_name in ("RUNFILES_DIR", "TEST_SRCDIR"):
        root = os.environ.get(env_name)
        if root:
            for prefix in ("_main", "__main__", ""):
                path = Path(root) / prefix / rel if prefix else Path(root) / rel
                if path.exists():
                    return path
    if candidate.exists():
        return candidate
    raise FileNotFoundError(rel)


def _cuda_prefix() -> Path:
    raw = os.environ.get("CUBIN_GUARD_TEST_CUDA_PREFIX_FILE")
    if not raw:
        raise unittest.SkipTest("CUBIN_GUARD_TEST_CUDA_PREFIX_FILE is not set")
    prefix_file = _runfiles_path(raw)
    prefix = Path(prefix_file.read_text(encoding="utf-8").strip())
    if not prefix.exists():
        raise unittest.SkipTest(f"CUDA prefix is unavailable: {prefix}")
    return prefix


def _build_cuda_so(root: Path) -> Path:
    cuda = _cuda_prefix()
    nvcc = cuda / "bin" / "nvcc"
    if not nvcc.is_file():
        raise unittest.SkipTest(f"CUDA prefix lacks nvcc: {nvcc}")
    host_cxx = Path("/usr/bin/g++")
    if not host_cxx.is_file():
        raise unittest.SkipTest(f"rootfs lacks host C++ compiler: {host_cxx}")
    source = root / "kernel.cu"
    source.write_text(
        textwrap.dedent(
            """\
            extern "C" __global__ void vaso_cubin_guard_kernel(float *out) {
              out[threadIdx.x] = 1.0f;
            }
            """
        ),
        encoding="utf-8",
    )
    so = root / "libfixture.so"
    result = subprocess.run(
        [
            str(nvcc),
            "-shared",
            f"-ccbin={host_cxx}",
            "-Xcompiler",
            "-fPIC",
            "-gencode=arch=compute_100,code=sm_100",
            str(source),
            "-o",
            str(so),
        ],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if result.returncode != 0:
        raise AssertionError(result.stdout + result.stderr)
    return so


def _write_wheel(root: Path, so: Path, arch_list: list[str], *, member_path: str = "fixture/libfixture.so") -> Path:
    wheel = root / "fixture-1.0-py3-none-linux_x86_64.whl"
    provenance = {
        "schema_version": 1,
        "declared_cuda_architectures": arch_list,
    }
    with zipfile.ZipFile(wheel, "w") as zf:
        zf.write(so, member_path)
        zf.writestr("fixture-1.0.dist-info/METADATA", "Name: fixture\nVersion: 1.0\n")
        zf.writestr("fixture-1.0.dist-info/WHEEL", "Wheel-Version: 1.0\nTag: py3-none-linux_x86_64\n")
        zf.writestr("fixture-1.0.dist-info/vaso_provenance.json", json.dumps(provenance, sort_keys=True) + "\n")
        zf.writestr("fixture-1.0.dist-info/RECORD", "")
    return wheel


def _write_fake_cuda_prefix(root: Path, *, elf_listing: str, ptx_listing: str) -> Path:
    prefix = root / "fake-cuda"
    bin_dir = prefix / "bin"
    bin_dir.mkdir(parents=True)
    cuobjdump = bin_dir / "cuobjdump"
    cuobjdump.write_text(
        textwrap.dedent(
            f"""\
            #!/usr/bin/env python3
            import sys

            listings = {{
                "--list-elf": {elf_listing!r},
                "--list-ptx": {ptx_listing!r},
            }}
            sys.stdout.write(listings.get(sys.argv[1], ""))
            """
        ),
        encoding="utf-8",
    )
    cuobjdump.chmod(0o755)
    prefix_file = root / "prefix_path.txt"
    prefix_file.write_text(str(prefix) + "\n", encoding="utf-8")
    return prefix_file


def _run_guard(wheel: Path, prefix_file: Path, *, allowlist: Path | None = None) -> subprocess.CompletedProcess[str]:
    args = [
        sys.executable,
        str(SCRIPT),
        "--cuda-prefix-file",
        str(prefix_file),
    ]
    if allowlist is not None:
        args.extend(["--missing-ptx-allowlist", str(allowlist)])
    args.append(str(wheel))
    return subprocess.run(
        args,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


class CubinGuardParserTest(unittest.TestCase):
    def test_list_ptx_sm_file_satisfies_compute_ptx_requirement(self) -> None:
        root = _tmp_root()
        self.addCleanup(lambda: shutil.rmtree(root, ignore_errors=True))
        so = root / "libtorch_cuda.so"
        so.write_bytes(b"\x7fELFfixture")
        wheel = _write_wheel(root, so, ["10.0+PTX"])
        prefix_file = _write_fake_cuda_prefix(
            root,
            elf_listing="ELF file 1: libtorch_cuda.1.sm_100.cubin\n",
            ptx_listing="PTX file 1: libtorch_cuda.1.sm_100.ptx\n",
        )

        result = _run_guard(wheel, prefix_file)

        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertIn("cubin guard passed", result.stdout)
        self.assertNotIn("missing PTX", result.stderr)

    def test_allowlisted_missing_ptx_warns_and_passes(self) -> None:
        root = _tmp_root()
        self.addCleanup(lambda: shutil.rmtree(root, ignore_errors=True))
        so = root / "libtorch_nvshmem.so"
        so.write_bytes(b"\x7fELFfixture")
        wheel = _write_wheel(root, so, ["10.0+PTX"], member_path="torch/lib/libtorch_nvshmem.so")
        prefix_file = _write_fake_cuda_prefix(
            root,
            elf_listing="ELF file 1: libtorch_nvshmem.1.sm_100.cubin\n",
            ptx_listing="",
        )

        result = _run_guard(wheel, prefix_file)

        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertIn("warning:", result.stderr)
        self.assertIn("torch/lib/libtorch_nvshmem.so", result.stderr)
        self.assertIn("missing PTX for compute_100", result.stderr)
        self.assertIn("relocatable device code plus device link", result.stderr)

    def test_allowlist_does_not_allow_missing_cubin(self) -> None:
        root = _tmp_root()
        self.addCleanup(lambda: shutil.rmtree(root, ignore_errors=True))
        so = root / "libtorch_nvshmem.so"
        so.write_bytes(b"\x7fELFfixture")
        wheel = _write_wheel(root, so, ["10.0+PTX"], member_path="torch/lib/libtorch_nvshmem.so")
        prefix_file = _write_fake_cuda_prefix(
            root,
            elf_listing="",
            ptx_listing="PTX file 1: libtorch_nvshmem.1.sm_100.ptx\n",
        )

        result = _run_guard(wheel, prefix_file)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("missing cubin for sm_100", result.stderr)


class CubinGuardTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if os.environ.get("VASO_IN_INSULA") != "1":
            raise unittest.SkipTest("cubin fixture test must run inside the insula")
        cls.root = _tmp_root()
        cls.so = _build_cuda_so(cls.root)

    @classmethod
    def tearDownClass(cls) -> None:
        if hasattr(cls, "root"):
            shutil.rmtree(cls.root, ignore_errors=True)

    def test_rule_fixture_manifest_matches_guard_rule_ids(self) -> None:
        self.assertEqual(set(RULE_FIXTURES), guard.RULE_IDS)
        missing = sorted(name for name in RULE_FIXTURES.values() if not hasattr(self, name))
        self.assertEqual(missing, [])

    def run_guard(self, wheel: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--cuda-prefix-file",
                os.environ["CUBIN_GUARD_TEST_CUDA_PREFIX_FILE"],
                str(wheel),
            ],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

    def test_passes_when_declared_sm_has_cubin(self) -> None:
        wheel = _write_wheel(self.root, self.so, ["sm_100"])

        result = self.run_guard(wheel)

        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertIn("cubin guard passed", result.stdout)
        self.assertIn("sm_100", result.stdout)

    def test_fails_and_names_extra_declared_arch_without_cubin(self) -> None:
        wheel = _write_wheel(self.root, self.so, ["sm_100", "sm_90"])

        result = self.run_guard(wheel)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("sm_90", result.stderr)
        self.assertIn("missing cubin", result.stderr)

    def test_fails_and_names_declared_ptx_arch_without_ptx(self) -> None:
        wheel = _write_wheel(self.root, self.so, ["10.0+PTX"])

        result = self.run_guard(wheel)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("compute_100", result.stderr)
        self.assertIn("missing PTX", result.stderr)

    def test_fails_cuda_object_without_declared_architectures(self) -> None:
        wheel = _write_wheel(self.root, self.so, [])

        result = self.run_guard(wheel)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("no declared_cuda_architectures", result.stderr)


if __name__ == "__main__":
    unittest.main()
