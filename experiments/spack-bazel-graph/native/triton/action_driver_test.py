#!/usr/bin/env python3
"""Unit tests for the native Triton action driver."""

from __future__ import annotations

import importlib.util
import json
import os
import stat
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest import mock


ACTION_DRIVER = Path(__file__).with_name("action_driver.py")
PLAN = Path(__file__).with_name("plan.py")
PINS = Path(__file__).with_name("upstream_pins.json")
FIXTURE_PYTHON_MAJOR = "3"
FIXTURE_PYTHON_MINOR = "13"
FIXTURE_PYTHON_VERSION = ".".join((FIXTURE_PYTHON_MAJOR, FIXTURE_PYTHON_MINOR))
FIXTURE_PYTHON_ABI = "cp" + FIXTURE_PYTHON_MAJOR + FIXTURE_PYTHON_MINOR
FIXTURE_PYTHON_SEGMENT = "python" + FIXTURE_PYTHON_VERSION
FIXTURE_SITE_PACKAGES = Path("lib") / FIXTURE_PYTHON_SEGMENT / "site-packages"
FIXTURE_WHEEL_TAG = "-".join((FIXTURE_PYTHON_ABI, FIXTURE_PYTHON_ABI))

SPEC = importlib.util.spec_from_file_location("triton_action_driver", ACTION_DRIVER)
assert SPEC is not None
driver = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = driver
SPEC.loader.exec_module(driver)


def temporary_directory():
    base = os.environ.get("TEST_TMPDIR") or os.environ.get("VASO_AGENT_IO_ROOT")
    return tempfile.TemporaryDirectory(dir=base)


def write_executable(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


class TritonActionDriverTest(unittest.TestCase):
    def make_prefixes(self, root: Path) -> tuple[dict[str, Path], list[str]]:
        prefixes = {key: root / "prefixes" / key for key in driver.PREFIX_KEYS}
        for path in prefixes.values():
            path.mkdir(parents=True, exist_ok=True)

        log = root / "fake-python.log"
        fake_python = textwrap.dedent(
            f"""\
            #!/usr/bin/python3
            import json
            import os
            import sys
            from pathlib import Path

            log = Path({str(log)!r})
            log.parent.mkdir(parents=True, exist_ok=True)
            argv = sys.argv[1:]
            with log.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps({{
                    "argv": argv,
                    "cwd": os.getcwd(),
                    "env": {{
                        key: os.environ.get(key, "")
                        for key in (
                            "HOME",
                            "PIP_CACHE_DIR",
                            "PIP_NO_INDEX",
                            "CC",
                            "CXX",
                            "CMAKE_C_COMPILER",
                            "CMAKE_CXX_COMPILER",
                            "TRITON_BUILD_WITH_CLANG_LLD",
                            "TRITON_OFFLINE_BUILD",
                            "LLVM_SYSPATH",
                            "JSON_SYSPATH",
                            "PYBIND11_SYSPATH",
                            "LIBRARY_PATH",
                            "LDFLAGS",
                            "TRITON_PTXAS_PATH",
                        )
                    }},
                }}, sort_keys=True) + "\\n")
            if argv[:3] == ["-m", "pip", "wheel"]:
                out_dir = Path(argv[argv.index("-w") + 1])
                out_dir.mkdir(parents=True, exist_ok=True)
                (out_dir / f"triton-3.8.0-{FIXTURE_WHEEL_TAG}-linux_x86_64.whl").write_text(
                    "fake triton wheel\\n",
                    encoding="utf-8",
                )
                raise SystemExit(0)
            if argv[:3] == ["-m", "pip", "install"]:
                prefix_arg = argv[argv.index("--prefix") + 1]
                site_packages = Path(prefix_arg) / {str(FIXTURE_SITE_PACKAGES)!r}
                (site_packages / "triton").mkdir(parents=True, exist_ok=True)
                (site_packages / "triton" / "__init__.py").write_text(
                    "__version__ = '3.8.0'\\n",
                    encoding="utf-8",
                )
                raise SystemExit(0)
            print("unexpected fake python argv: " + repr(argv), file=sys.stderr)
            raise SystemExit(97)
            """
        )
        write_executable(prefixes["python"] / "bin" / "python3", fake_python)
        write_executable(prefixes["python-venv"] / "bin" / f"python{FIXTURE_PYTHON_VERSION}", fake_python)
        (prefixes["python"] / "include" / f"python{FIXTURE_PYTHON_VERSION}").mkdir(parents=True)
        (prefixes["python"] / "include" / f"python{FIXTURE_PYTHON_VERSION}" / "Python.h").write_text(
            "",
            encoding="utf-8",
        )
        (prefixes["python-venv"] / "pyvenv.cfg").write_text("", encoding="utf-8")
        (prefixes["python-venv"] / FIXTURE_SITE_PACKAGES).mkdir(parents=True)

        def package_prefix(name: str, package: str) -> None:
            root_path = prefixes[name] / FIXTURE_SITE_PACKAGES / package
            root_path.mkdir(parents=True, exist_ok=True)
            (root_path / "__init__.py").write_text("", encoding="utf-8")
            (prefixes[name] / "bin").mkdir(exist_ok=True)

        package_prefix("py-pip", "pip")
        write_executable(prefixes["py-pip"] / "bin" / "pip", "#!/bin/sh\n")
        package_prefix("py-setuptools", "setuptools")
        package_prefix("py-wheel", "wheel")
        write_executable(prefixes["py-wheel"] / "bin" / "wheel", "#!/bin/sh\n")
        package_prefix("py-filelock", "filelock")
        package_prefix("py-lit", "lit")
        write_executable(prefixes["py-lit"] / "bin" / "lit", "#!/bin/sh\n")
        package_prefix("py-pybind11", "pybind11")

        for rel in ("bin/cmake", "bin/ninja"):
            key = "cmake" if "cmake" in rel else "ninja"
            write_executable(prefixes[key] / rel, "#!/bin/sh\n")
        for rel in (
            "bin/llvm-config",
            "bin/clang",
            "bin/ld.lld",
            "bin/FileCheck",
            "include/llvm/Config/llvm-config.h",
            "lib/cmake/llvm/LLVMConfig.cmake",
            "lib/cmake/mlir/MLIRConfig.cmake",
            "lib/cmake/lld/LLDConfig.cmake",
        ):
            path = prefixes["llvm"] / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            if "/bin/" in rel:
                write_executable(path, "#!/bin/sh\n")
            else:
                path.write_text("", encoding="utf-8")
        (prefixes["nlohmann_json"] / "include" / "nlohmann").mkdir(parents=True)
        (prefixes["nlohmann_json"] / "include" / "nlohmann" / "json.hpp").write_text("", encoding="utf-8")
        for rel in (
            "bin/ptxas",
            "bin/nvdisasm",
            "bin/cuobjdump",
            "include/cuda.h",
            "lib64/libcupti.so",
            "nvvm/libdevice/libdevice.10.bc",
        ):
            path = prefixes["cuda"] / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            if "/bin/" in rel:
                write_executable(path, "#!/bin/sh\n")
            else:
                path.write_text("", encoding="utf-8")
        (prefixes["zlib_ng"] / "include").mkdir(parents=True)
        (prefixes["zlib_ng"] / "include" / "zlib.h").write_text("", encoding="utf-8")
        (prefixes["zlib_ng"] / "lib").mkdir()
        (prefixes["zlib_ng"] / "lib" / "libz.so").write_text("", encoding="utf-8")

        prefix_args = []
        prefix_file_dir = root / "prefix-files"
        prefix_file_dir.mkdir()
        for key, path in prefixes.items():
            prefix_file = prefix_file_dir / f"{key}.txt"
            prefix_file.write_text(str(path) + "\n", encoding="utf-8")
            prefix_args.extend(["--prefix-file", f"{key}={prefix_file}"])
        return prefixes, prefix_args

    def test_execute_mode_builds_wheel_installs_prefix_and_records_metadata(self) -> None:
        with temporary_directory() as tmp_text:
            tmp = Path(tmp_text)
            prefixes, prefix_args = self.make_prefixes(tmp)
            source = tmp / "triton-src"
            source.joinpath("python", "triton").mkdir(parents=True)
            source.joinpath("setup.py").write_text(
                "print('fake triton setup')\n",
                encoding="utf-8",
            )
            source.joinpath("python", "triton", "__init__.py").write_text(
                "__version__ = '3.8.0'\n",
                encoding="utf-8",
            )
            source.joinpath("README.md").write_text("before\n", encoding="utf-8")
            patch = tmp / "0001-test.patch"
            patch.write_text(
                "\n".join(
                    [
                        "--- a/README.md",
                        "+++ b/README.md",
                        "@@ -1 +1 @@",
                        "-before",
                        "+after",
                        "",
                    ]
                ),
                encoding="utf-8",
            )
            patch_sha = driver._file_sha256(patch)
            rootfs_manifest = tmp / "rootfs-bundle.json"
            rootfs_manifest.write_text('{"schema_version": 2}\n', encoding="utf-8")
            prefix_out = tmp / "out-prefix"
            wheel_out = tmp / "out" / "triton.whl"
            build_plan_out = tmp / "out" / "plan.json"
            metadata_out = tmp / "out" / "metadata.json"
            marker_out = tmp / "out" / "result.txt"
            source_anchor = source / "setup.py"
            patch_arg = patch

            env = {
                "VASO_IN_INSULA": "1",
                "VASO_ROOTFS_BUNDLE_MANIFEST": str(rootfs_manifest),
                "VASO_CUDA_LINE": "cu130",
                "VASO_HOME": str(tmp / "vaso"),
                "TMPDIR": str(tmp / "tmp"),
            }
            argv = [
                "--plan",
                str(PLAN),
                "--pins",
                str(PINS),
                "--prefix-out",
                str(prefix_out),
                "--build-plan-out",
                str(build_plan_out),
                "--provider-metadata-out",
                str(metadata_out),
                "--result-marker-out",
                str(marker_out),
                "--wheel-out",
                str(wheel_out),
                "--source-anchor",
                str(source_anchor),
                "--patch-file",
                str(patch_arg),
                "--patch-sha256",
                patch_sha,
                "--python-abi",
                FIXTURE_PYTHON_ABI,
                "--token",
                driver.REQUIRED_TOKEN,
                "--execute",
            ]
            argv.extend(prefix_args)
            with mock.patch.dict(os.environ, env, clear=True):
                self.assertEqual(driver.main(argv), 0)

            self.assertTrue(wheel_out.is_file())
            self.assertEqual(wheel_out.read_text(encoding="utf-8"), "fake triton wheel\n")
            self.assertEqual((prefix_out / FIXTURE_SITE_PACKAGES / "triton" / "__init__.py").read_text(), "__version__ = '3.8.0'\n")
            self.assertEqual(source.joinpath("README.md").read_text(encoding="utf-8"), "before\n")
            build_work_roots = list((tmp / "vaso" / "lines" / "cu130" / "work" / "triton").iterdir())
            self.assertEqual(len(build_work_roots), 1)
            self.assertEqual(build_work_roots[0].joinpath("src", "README.md").read_text(encoding="utf-8"), "after\n")

            metadata = json.loads(metadata_out.read_text(encoding="utf-8"))
            self.assertEqual(metadata["mode"], "execute")
            self.assertTrue(metadata["will_build"])
            self.assertTrue(metadata["token_present"])
            self.assertEqual(metadata["build_work"], str(build_work_roots[0]))
            self.assertEqual(metadata["source"], str(build_work_roots[0] / "src"))
            self.assertIn("0001-test.patch", metadata["applied_patches"])
            self.assertIn("prefix installation completed", marker_out.read_text(encoding="utf-8"))

            fake_log = [json.loads(line) for line in (tmp / "fake-python.log").read_text(encoding="utf-8").splitlines()]
            self.assertEqual([entry["argv"][:3] for entry in fake_log], [["-m", "pip", "wheel"], ["-m", "pip", "install"]])
            self.assertEqual(fake_log[0]["cwd"], str(build_work_roots[0] / "src"))
            wheel_env = fake_log[0]["env"]
            self.assertEqual(wheel_env["PIP_NO_INDEX"], "1")
            self.assertEqual(wheel_env["TRITON_OFFLINE_BUILD"], "1")
            self.assertEqual(wheel_env["CC"], "/usr/bin/gcc")
            self.assertEqual(wheel_env["CXX"], "/usr/bin/g++")
            self.assertEqual(wheel_env["CMAKE_C_COMPILER"], "/usr/bin/gcc")
            self.assertEqual(wheel_env["CMAKE_CXX_COMPILER"], "/usr/bin/g++")
            self.assertEqual(wheel_env["TRITON_BUILD_WITH_CLANG_LLD"], "")
            self.assertEqual(wheel_env["LLVM_SYSPATH"], str(prefixes["llvm"]))
            self.assertEqual(wheel_env["JSON_SYSPATH"], str(prefixes["nlohmann_json"]))
            self.assertEqual(wheel_env["PYBIND11_SYSPATH"], str(prefixes["py-pybind11"]))
            self.assertEqual(wheel_env["LIBRARY_PATH"], str(prefixes["zlib_ng"] / "lib"))
            self.assertEqual(wheel_env["LDFLAGS"], "-L" + str(prefixes["zlib_ng"] / "lib"))
            self.assertEqual(wheel_env["TRITON_PTXAS_PATH"], str(prefixes["cuda"] / "bin" / "ptxas"))
            self.assertTrue(wheel_env["HOME"].startswith(str(tmp / "tmp")))
            self.assertTrue(wheel_env["PIP_CACHE_DIR"].startswith(str(tmp / "tmp")))

    def test_relative_patch_path_survives_build_source_chdir(self) -> None:
        with temporary_directory() as tmp_text:
            tmp = Path(tmp_text)
            prefixes, prefix_args = self.make_prefixes(tmp)
            source = tmp / "triton-src"
            source.joinpath("python", "triton").mkdir(parents=True)
            source.joinpath("setup.py").write_text(
                "print('fake triton setup')\n",
                encoding="utf-8",
            )
            source.joinpath("README.md").write_text("before\n", encoding="utf-8")
            patch_dir = tmp / "native" / "triton" / "patches"
            patch = patch_dir / "0001-test.patch"
            patch_dir.mkdir(parents=True)
            patch.write_text(
                "\n".join(
                    [
                        "--- a/README.md",
                        "+++ b/README.md",
                        "@@ -1 +1 @@",
                        "-before",
                        "+after",
                        "",
                    ]
                ),
                encoding="utf-8",
            )
            patch_sha = driver._file_sha256(patch)
            rootfs_manifest = tmp / "rootfs-bundle.json"
            rootfs_manifest.write_text('{"schema_version": 2}\n', encoding="utf-8")
            prefix_out = tmp / "out-prefix"
            wheel_out = tmp / "out" / "triton.whl"
            build_plan_out = tmp / "out" / "plan.json"
            metadata_out = tmp / "out" / "metadata.json"
            marker_out = tmp / "out" / "result.txt"

            env = {
                "VASO_IN_INSULA": "1",
                "VASO_ROOTFS_BUNDLE_MANIFEST": str(rootfs_manifest),
                "VASO_CUDA_LINE": "cu130",
                "VASO_HOME": str(tmp / "vaso"),
                "TMPDIR": str(tmp / "tmp"),
            }
            argv = [
                "--plan",
                str(PLAN),
                "--pins",
                str(PINS),
                "--prefix-out",
                str(prefix_out),
                "--build-plan-out",
                str(build_plan_out),
                "--provider-metadata-out",
                str(metadata_out),
                "--result-marker-out",
                str(marker_out),
                "--wheel-out",
                str(wheel_out),
                "--source-anchor",
                "triton-src/setup.py",
                "--patch-file",
                "native/triton/patches/0001-test.patch",
                "--patch-sha256",
                patch_sha,
                "--python-abi",
                FIXTURE_PYTHON_ABI,
                "--token",
                driver.REQUIRED_TOKEN,
                "--execute",
            ]
            argv.extend(prefix_args)
            old_cwd = Path.cwd()
            try:
                os.chdir(tmp)
                with mock.patch.dict(os.environ, env, clear=True):
                    self.assertEqual(driver.main(argv), 0)
            finally:
                os.chdir(old_cwd)

            build_work_roots = list((tmp / "vaso" / "lines" / "cu130" / "work" / "triton").iterdir())
            self.assertEqual(build_work_roots[0].joinpath("src", "README.md").read_text(encoding="utf-8"), "after\n")

    def test_relative_declared_outputs_install_under_execroot_not_build_work(self) -> None:
        with temporary_directory() as tmp_text:
            tmp = Path(tmp_text)
            prefixes, prefix_args = self.make_prefixes(tmp)
            execroot = tmp / "execroot"
            execroot.mkdir()
            source = execroot / "triton-src"
            source.joinpath("python", "triton").mkdir(parents=True)
            source.joinpath("setup.py").write_text(
                "print('fake triton setup')\n",
                encoding="utf-8",
            )
            rootfs_manifest = tmp / "rootfs-bundle.json"
            rootfs_manifest.write_text('{"schema_version": 2}\n', encoding="utf-8")
            output_dir = Path("bazel-out") / "k8-fastbuild" / "bin" / "native" / "triton"
            prefix_out = output_dir / "triton_action_prefix"
            wheel_out = output_dir / "triton_action_wheel.whl"
            build_plan_out = output_dir / "triton_action_build_plan.json"
            metadata_out = output_dir / "triton_action_provider_metadata.json"
            marker_out = output_dir / "triton_action_result.txt"

            env = {
                "VASO_IN_INSULA": "1",
                "VASO_ROOTFS_BUNDLE_MANIFEST": str(rootfs_manifest),
                "VASO_CUDA_LINE": "cu130",
                "VASO_HOME": str(tmp / "vaso"),
                "TMPDIR": str(tmp / "tmp"),
            }
            argv = [
                "--plan",
                str(PLAN.resolve()),
                "--pins",
                str(PINS.resolve()),
                "--prefix-out",
                str(prefix_out),
                "--build-plan-out",
                str(build_plan_out),
                "--provider-metadata-out",
                str(metadata_out),
                "--result-marker-out",
                str(marker_out),
                "--wheel-out",
                str(wheel_out),
                "--source-anchor",
                "triton-src/setup.py",
                "--python-abi",
                FIXTURE_PYTHON_ABI,
                "--token",
                driver.REQUIRED_TOKEN,
                "--execute",
            ]
            argv.extend(prefix_args)
            old_cwd = Path.cwd()
            try:
                os.chdir(execroot)
                with mock.patch.dict(os.environ, env, clear=True):
                    self.assertEqual(driver.main(argv), 0)
            finally:
                os.chdir(old_cwd)

            build_work_roots = list((tmp / "vaso" / "lines" / "cu130" / "work" / "triton").iterdir())
            self.assertEqual(len(build_work_roots), 1)
            expected_module = execroot / prefix_out / FIXTURE_SITE_PACKAGES / "triton" / "__init__.py"
            misplaced_module = build_work_roots[0] / prefix_out / FIXTURE_SITE_PACKAGES / "triton" / "__init__.py"
            self.assertEqual(expected_module.read_text(encoding="utf-8"), "__version__ = '3.8.0'\n")
            self.assertFalse(misplaced_module.exists(), str(misplaced_module))


if __name__ == "__main__":
    unittest.main()
