#!/usr/bin/env python3
"""Committed native build plan drift tests."""

from __future__ import annotations

import filecmp
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


GRAPH_ROOT = Path(__file__).resolve().parents[1]


def scratch_parent() -> Path:
    return Path(os.environ.get("TEST_TMPDIR") or os.environ.get("VASO_AGENT_IO_ROOT") or Path.cwd())


class GenPlansTest(unittest.TestCase):
    def run_generator(self, repo_root: Path, out_dir: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                sys.executable,
                str(repo_root / "tools" / "gen_plans.py"),
                "--repo-root",
                str(repo_root),
                "--out-dir",
                str(out_dir),
            ],
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

    def test_generated_committed_plans_match_committed_files(self) -> None:
        with tempfile.TemporaryDirectory(dir=scratch_parent()) as tmp:
            generated = Path(tmp) / "plans"
            result = self.run_generator(GRAPH_ROOT, generated)

            self.assertEqual(result.returncode, 0, result.stderr)
            for relative in (
                "jax/cu129/rootfs.identity.json",
                "jax/cu130/rootfs.identity.json",
                "schema/v1.json",
                "torch/cu129/rootfs.identity.json",
                "torch/cu129/pytorch.plan.json",
                "torch/cu129/torchaudio.plan.json",
                "torch/cu129/torchvision.plan.json",
                "torch/cu129/triton.plan.json",
                "torch/cu129/bzip2.plan.json",
                "torch/cu129/xz.plan.json",
                "torch/cu129/zstd.plan.json",
                "torch/cu129/zlib_ng.plan.json",
                "torch/cu130/rootfs.identity.json",
                "torch/cu130/pytorch.plan.json",
                "torch/cu130/torchaudio.plan.json",
                "torch/cu130/torchvision.plan.json",
                "torch/cu130/triton.plan.json",
                "torch/cu130/bzip2.plan.json",
                "torch/cu130/xz.plan.json",
                "torch/cu130/zstd.plan.json",
                "torch/cu130/zlib_ng.plan.json",
            ):
                self.assertTrue(
                    filecmp.cmp(generated / relative, GRAPH_ROOT / "plans" / relative, shallow=False),
                    f"plan drift for plans/{relative}; run tools/gen_plans.py and commit the result",
                )

    def test_committed_plans_validate_against_schema(self) -> None:
        result = subprocess.run(
            [
                sys.executable,
                str(GRAPH_ROOT / "tools" / "gen_plans.py"),
                "--repo-root",
                str(GRAPH_ROOT),
                "--check",
            ],
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        self.assertEqual(result.returncode, 0, result.stderr)

    def test_generated_triton_plan_is_a_wheel_build_from_committed_data(self) -> None:
        with tempfile.TemporaryDirectory(dir=scratch_parent()) as tmp:
            generated = Path(tmp) / "plans"
            result = self.run_generator(GRAPH_ROOT, generated)

            self.assertEqual(result.returncode, 0, result.stderr)
            plan = json.loads((generated / "torch" / "cu130" / "triton.plan.json").read_text(encoding="utf-8"))

        self.assertEqual(plan["package"], {"name": "py-triton", "version": "3.8.0"})
        self.assertEqual(plan["sources"][0]["sha256"], "61a11952362a0d54e67fb11fd29f25747da7339fe4ed39b2cff730c21ebe8825")
        self.assertIn({"kind": "wheel", "path": "{wheel}"}, plan["outputs"])
        self.assertIn({"kind": "prefix", "path": "{out}"}, plan["outputs"])
        self.assertNotIn("native/triton/plan.py", json.dumps(plan, sort_keys=True))
        self.assertNotIn("action_driver.py", json.dumps(plan, sort_keys=True))
        self.assertIn("llvm", plan["deps"])
        self.assertIn("py-pip", plan["deps"])
        self.assertEqual(plan["determinism"]["source_date_epoch"], 1772203844)
        configure_command = plan["configure"]["argv"][2]
        self.assertIn("compiler_prefix_map_flags", configure_command)
        self.assertIn("-ffile-prefix-map=$map_root=.", configure_command)
        self.assertIn("-Xcompiler=-ffile-prefix-map=$map_root=.", configure_command)
        self.assertIn("TRITON_APPEND_CMAKE_ARGS", configure_command)
        self.assertIn("-DCMAKE_CXX_FLAGS=", configure_command)
        self.assertIn("$CXXFLAGS $compiler_prefix_map_flags", configure_command)
        build_command = plan["build"]["argv"][2]
        self.assertIn("zipfile", build_command)
        self.assertIn('endswith(".ll")', build_command)
        self.assertIn("{src}", build_command)
        install_command = plan["install"]["argv"][2]
        self.assertIn("--no-compile", install_command)
        self.assertIn("__pycache__", install_command)
        self.assertIn("*.pyc", install_command)
        self.assertIn("direct_url.json", install_command)
        self.assertIn("RECORD", install_command)
        self.assertIn("/ \"bin\"", install_command)
        self.assertIn("#!/usr/bin/env python3", install_command)
        self.assertIn("PurePosixPath", install_command)

    def test_generated_torchvision_plan_is_a_wheel_build_from_committed_data(self) -> None:
        with tempfile.TemporaryDirectory(dir=scratch_parent()) as tmp:
            generated = Path(tmp) / "plans"
            result = self.run_generator(GRAPH_ROOT, generated)

            self.assertEqual(result.returncode, 0, result.stderr)
            plan = json.loads((generated / "torch" / "cu130" / "torchvision.plan.json").read_text(encoding="utf-8"))

        self.assertEqual(plan["package"], {"name": "py-torchvision", "version": "0.29.0"})
        self.assertEqual(plan["sources"][0]["sha256"], "24be57d922927d8a2ac2e8f076f07c3447ddf8f1d25ddbb7b65578f36c9ab8e3")
        self.assertIn({"kind": "wheel", "path": "{wheel}"}, plan["outputs"])
        self.assertIn({"kind": "prefix", "path": "{out}"}, plan["outputs"])
        self.assertIn("torch", plan["deps"])
        self.assertIn("py-pillow", plan["deps"])
        self.assertIn("libjpeg-turbo", plan["deps"])
        self.assertIn("zlib-ng", plan["deps"])
        self.assertNotIn("native/torchvision/plan.py", json.dumps(plan, sort_keys=True))
        self.assertNotIn("action_driver.py", json.dumps(plan, sort_keys=True))
        self.assertEqual(plan["determinism"]["source_date_epoch"], 1793577600)
        plan_text = json.dumps(plan, sort_keys=True)
        self.assertIn("{jobs}", plan_text)
        self.assertIn("export MAX_JOBS={jobs}", plan_text)
        self.assertNotIn("VASO_TORCHVISION_MAX_JOBS", plan_text)
        configure_command = plan["configure"]["argv"][2]
        self.assertIn("TORCHVISION_USE_PNG=1", configure_command)
        self.assertIn("TORCHVISION_USE_JPEG=1", configure_command)
        self.assertIn("TORCHVISION_USE_NVJPEG=1", configure_command)
        self.assertIn("CUDA_HOME=\"$cuda\"", configure_command)
        build_command = plan["build"]["argv"][2]
        self.assertIn("pip wheel", build_command)
        self.assertIn("--no-build-isolation", build_command)
        self.assertIn("-w \"{work}/wheelhouse\"", build_command)
        install_command = plan["install"]["argv"][2]
        self.assertIn("--no-compile", install_command)
        self.assertIn("direct_url.json", install_command)
        self.assertIn("__pycache__", install_command)
        self.assertIn("torchvision-*.whl", install_command)

    def test_generated_torchaudio_plan_is_a_wheel_build_from_committed_data(self) -> None:
        with tempfile.TemporaryDirectory(dir=scratch_parent()) as tmp:
            generated = Path(tmp) / "plans"
            result = self.run_generator(GRAPH_ROOT, generated)

            self.assertEqual(result.returncode, 0, result.stderr)
            plan = json.loads((generated / "torch" / "cu130" / "torchaudio.plan.json").read_text(encoding="utf-8"))

        self.assertEqual(plan["package"], {"name": "py-torchaudio", "version": "2.11.0"})
        self.assertEqual(plan["sources"][0]["sha256"], "599ec24e7e1eef476ef21f0178e33da00e2434f930ba42e9cc20bf4002220486")
        self.assertIn({"kind": "wheel", "path": "{wheel}"}, plan["outputs"])
        self.assertIn({"kind": "prefix", "path": "{out}"}, plan["outputs"])
        self.assertIn("torch", plan["deps"])
        self.assertIn("py-filelock", plan["deps"])
        self.assertIn("ninja", plan["deps"])
        self.assertNotIn("native/torchaudio/plan.py", json.dumps(plan, sort_keys=True))
        self.assertNotIn("action_driver.py", json.dumps(plan, sort_keys=True))
        self.assertEqual(plan["determinism"]["source_date_epoch"], 1793577600)
        plan_text = json.dumps(plan, sort_keys=True)
        self.assertIn("{jobs}", plan_text)
        self.assertIn("export MAX_JOBS={jobs}", plan_text)
        self.assertNotIn("VASO_TORCHAUDIO_MAX_JOBS", plan_text)
        configure_command = plan["configure"]["argv"][2]
        self.assertIn("USE_CUDA=1", configure_command)
        self.assertIn("BUILD_RNNT=1", configure_command)
        self.assertIn("BUILD_CUDA_CTC_DECODER=1", configure_command)
        self.assertIn("CUDA_HOME=\"$cuda\"", configure_command)
        build_command = plan["build"]["argv"][2]
        self.assertIn("pip wheel", build_command)
        self.assertIn("--no-build-isolation", build_command)
        self.assertIn("-w \"{work}/wheelhouse\"", build_command)
        install_command = plan["install"]["argv"][2]
        self.assertIn("--no-compile", install_command)
        self.assertIn("direct_url.json", install_command)
        self.assertIn("__pycache__", install_command)
        self.assertIn("torchaudio-*.whl", install_command)

    def test_generated_jaxlib_plan_is_nested_bazel_build_from_committed_data(self) -> None:
        with tempfile.TemporaryDirectory(dir=scratch_parent()) as tmp:
            generated = Path(tmp) / "plans"
            result = self.run_generator(GRAPH_ROOT, generated)

            self.assertEqual(result.returncode, 0, result.stderr)
            plan = json.loads((generated / "jax" / "cu130" / "jaxlib.plan.json").read_text(encoding="utf-8"))

        self.assertEqual(plan["package"], {"name": "py-jaxlib", "version": "0.10.2"})
        self.assertEqual(plan["sources"][0]["sha256"], "fa7214ab31ed1cd418b4305807e9c4f3f175c783eeea40c28e0f77c3f4c24bc7")
        self.assertIn({"kind": "wheel", "path": "{wheel}"}, plan["outputs"])
        self.assertIn({"kind": "prefix", "path": "{out}"}, plan["outputs"])
        self.assertIn("native/jaxlib/upstream_pins.json", json.dumps(plan["inputs"], sort_keys=True))
        self.assertIn("native/jaxlib/nested_http_archives.json", json.dumps(plan["inputs"], sort_keys=True))
        self.assertIn("nested_bazel", plan)
        nested = plan["nested_bazel"]
        self.assertEqual(len(nested["archives"]), 155)
        self.assertTrue(any(item["name"] == "pypi_lock_ml_dtypes" for item in nested["archives"]))
        self.assertEqual(nested["distdir_manifest"]["label"], "@jaxlib_nested_cache_manifest//:manifest.json")
        self.assertEqual(nested["repository_cache_manifest"]["label"], "@jaxlib_nested_cache_manifest//:manifest.json")
        build_args = nested["build_py_args"]
        for expected in (
            "--wheels=jax,jaxlib,jax-cuda-plugin,jax-cuda-pjrt",
            "--python_version=",
            "--bazel_path={prefix:bazel}/bin/bazel",
            "--clang_path=/usr/lib/llvm-23/bin/clang",
            "--build_cuda_with_clang",
            "--bazel_options=--config=build_cuda_with_clang",
            "--bazel_options=--repo_env=USE_HERMETIC_CC_TOOLCHAIN=0",
            "--bazel_options=--@rules_ml_toolchain//common:enable_hermetic_cc=False",
            "--bazel_options=--repo_env=CLANG_CUDA_COMPILER_PATH=/usr/lib/llvm-23/bin/clang",
            "--bazel_options=--cxxopt=-std=gnu++17",
            "--bazel_options=--host_cxxopt=-std=gnu++17",
            "--cuda_major_version=13",
            "--cuda_compute_capabilities=sm_100",
            "--bazel_options=--repo_env=LOCAL_CUDA_PATH={work}/jax-local-redists/cuda",
            "--bazel_options=--repo_env=LOCAL_CUDNN_PATH={work}/jax-local-redists/cudnn",
            "--bazel_options=--repo_env=LOCAL_NCCL_PATH={work}/jax-local-redists/nccl",
            "--bazel_options=--repo_env=LOCAL_NVSHMEM_PATH={work}/jax-local-redists/nvshmem",
            "--bazel_options=--repo_env=LOCAL_CCCL_PATH={work}/jax-local-redists/cccl",
            "--bazel_options=--repo_env=HERMETIC_CUDA_VERSION=13.0.3",
            "--bazel_options=--config=cuda_libraries_from_stubs",
            "--bazel_options=--repository_cache={nested_repository_cache}",
            "--bazel_options=--distdir={nested_distdir}",
            "--bazel_options=--disk_cache={nested_disk_cache}",
            "--bazel_options=--jobs={jobs}",
            "--bazel_options=--local_resources=cpu={jobs}",
            "--bazel_options=--repository_disable_download",
            "--bazel_startup_options=--nohome_rc",
            "--bazel_startup_options=--nosystem_rc",
        ):
            self.assertIn(expected, build_args)
        self.assertFalse(any("--output_base" in arg for arg in build_args))
        self.assertNotIn("VASO_JAXLIB_MAX_JOBS", json.dumps(plan, sort_keys=True))
        self.assertNotIn("MAKE_JOBS", json.dumps(plan, sort_keys=True))
        self.assertEqual(plan["gpu_archs"], ["sm_100"])
        self.assertEqual(plan["determinism"]["source_date_epoch"], 1793577600)

    def test_schema_v1_is_general_native_plan_contract(self) -> None:
        schema = json.loads((GRAPH_ROOT / "plans" / "schema" / "v1.json").read_text(encoding="utf-8"))
        properties = schema["properties"]

        self.assertEqual(properties["profile"]["enum"], ["jax", "torch"])
        self.assertEqual(properties["line"]["enum"], ["cu129", "cu130"])
        self.assertIn("sources", properties)
        self.assertIn("inputs", properties)
        self.assertIn("outputs", properties)
        self.assertIn("nested_bazel", properties)
        self.assertNotIn("source", properties)

    def test_rootfs_identity_excludes_host_driver_version(self) -> None:
        for relative in (
            "jax/cu129/rootfs.identity.json",
            "jax/cu130/rootfs.identity.json",
            "torch/cu129/rootfs.identity.json",
            "torch/cu130/rootfs.identity.json",
        ):
            identity = json.loads((GRAPH_ROOT / "plans" / relative).read_text(encoding="utf-8"))
            self.assertNotIn("host_version", identity.get("runtime_driver", {}), relative)
            self.assertIn("live_checks", identity, relative)

    def test_zlib_constants_live_in_package_generator_data(self) -> None:
        import importlib.util

        spec = importlib.util.spec_from_file_location("gen_plans_under_test", GRAPH_ROOT / "tools" / "gen_plans.py")
        self.assertIsNotNone(spec)
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)

        zlib_data = module.PACKAGE_GENERATORS["zlib-ng"]
        self.assertEqual(zlib_data["version"], "2.3.3")
        self.assertEqual(zlib_data["source"]["strip_prefix"], "zlib-ng-2.3.3")
        self.assertFalse(hasattr(module, "ZLIB_NG_VERSION"))

    def test_zlib_phases_live_in_package_generator_data(self) -> None:
        import importlib.util

        spec = importlib.util.spec_from_file_location("gen_plans_under_test", GRAPH_ROOT / "tools" / "gen_plans.py")
        self.assertIsNotNone(spec)
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)

        zlib_data = module.PACKAGE_GENERATORS["zlib-ng"]
        self.assertEqual(zlib_data["configure"]["argv"], ["{src}/configure", "--prefix=/", "--zlib-compat"])
        self.assertEqual(zlib_data["build"]["argv"], ["make", "-j{jobs}"])
        self.assertEqual(zlib_data["install"]["argv"], ["make", "DESTDIR={out}", "install"])
        mutated = json.loads(json.dumps(zlib_data))
        mutated["build"]["argv"] = ["make", "-j{jobs}", "check"]
        module.PACKAGE_GENERATORS = {"zlib-ng": mutated}
        generated = module.generated_files(GRAPH_ROOT)
        plan = json.loads(generated[Path("torch/cu130/zlib_ng.plan.json")])
        self.assertEqual(plan["build"]["argv"], ["make", "-j{jobs}", "check"])

    def test_generated_leaf_dep_plans_are_prefix_builds_from_committed_data(self) -> None:
        with tempfile.TemporaryDirectory(dir=scratch_parent()) as tmp:
            generated = Path(tmp) / "plans"
            result = self.run_generator(GRAPH_ROOT, generated)

            self.assertEqual(result.returncode, 0, result.stderr)
            plans = {
                name: json.loads((generated / "torch" / "cu130" / f"{name}.plan.json").read_text(encoding="utf-8"))
                for name in ("bzip2", "xz", "zstd")
            }

        expectations = {
            "bzip2": {
                "package": {"name": "bzip2", "version": "1.0.8"},
                "sha256": "ab5a03176ee106d3f0fa90e381da478ddae405918153cca248e682cd0c4a2269",
                "strip_prefix": "bzip2-1.0.8",
                "configure": "Makefile-libbz2_so",
                "build": "make -j{jobs}",
                "install": "bzip2.pc",
            },
            "xz": {
                "package": {"name": "xz", "version": "5.8.3"},
                "sha256": "3d3a1b973af218114f4f889bbaa2f4c037deaae0c8e815eec381c3d546b974a0",
                "strip_prefix": "xz-5.8.3",
                "configure": "--disable-nls",
                "build": "-j{jobs}",
                "install": "--strip-debug",
            },
            "zstd": {
                "package": {"name": "zstd", "version": "1.5.7"},
                "sha256": "37d7284556b20954e56e1ca85b80226768902e2edabd3b649e9e72c0c9012ee3",
                "strip_prefix": "zstd-1.5.7",
                "configure": "install-pc",
                "build": "{src}/programs",
                "install": "HAVE_ZLIB=0",
            },
        }
        for name, expected in expectations.items():
            with self.subTest(name=name):
                plan = plans[name]
                self.assertEqual(plan["package"], expected["package"])
                self.assertEqual(plan["sources"][0]["sha256"], expected["sha256"])
                self.assertEqual(plan["sources"][0]["strip_prefix"], expected["strip_prefix"])
                self.assertEqual(plan["deps"], {})
                self.assertEqual(plan["outputs"], [{"kind": "prefix", "path": "{out}"}])
                plan_text = json.dumps(plan, sort_keys=True)
                self.assertIn(expected["configure"], plan_text)
                self.assertIn(expected["build"], plan_text)
                self.assertIn(expected["install"], plan_text)
                self.assertNotIn("prefix_path.txt", plan_text)
                self.assertNotIn("MAKE_JOBS", plan_text)
                if name == "bzip2":
                    self.assertIn("ln -sfn bzgrep bzegrep", plan_text)
                    self.assertNotIn("ln -s -f {out}/bin", plan_text)
                    self.assertIn('CFLAGS=\\"-fpic -fPIC -Wall -Winline -O2 -D_FILE_OFFSET_BITS=64\\"', plan_text)
                    self.assertIn('CFLAGS=\\"-Wall -Winline -O2 -D_FILE_OFFSET_BITS=64\\"', plan_text)
                    self.assertNotIn(" -g ", plan_text)
                if name == "xz":
                    self.assertIn("file -b --", plan_text)
                    self.assertIn("patchelf --set-rpath", plan_text)
                    self.assertIn("$ORIGIN/../lib", plan_text)
                    self.assertIn("exec_prefix=$\\\\173prefix\\\\175", plan_text)
                    self.assertIn("libdir=$\\\\173prefix\\\\175/lib", plan_text)
                    self.assertIn("includedir=$\\\\173prefix\\\\175/include", plan_text)
                    self.assertIn("strip --strip-all", plan_text)
                    self.assertIn("strip --strip-debug", plan_text)
                    self.assertNotIn("-exec strip", plan_text)

    def test_gcc_live_check_version_is_derived_from_rootfs_identity(self) -> None:
        import importlib.util

        spec = importlib.util.spec_from_file_location("gen_plans_under_test", GRAPH_ROOT / "tools" / "gen_plans.py")
        self.assertIsNotNone(spec)
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)

        checks = module.live_rootfs_checks(
            {
                "apt": {"packages": {"gcc-13": "99.3.1-1ubuntu1", "cuda-cudart-13-0": "13.0.96-1"}},
                "components": {"cuda_toolkit": {"version": "13.0.3"}},
            }
        )
        gcc_check = next(command for command in checks["commands"] if command["name"] == "gcc-dumpfullversion")
        self.assertEqual(gcc_check["expect_stdout"], "99.3.1")

    def test_generated_plan_carries_registered_toolchain_environment(self) -> None:
        with tempfile.TemporaryDirectory(dir=scratch_parent()) as tmp:
            generated = Path(tmp) / "plans"
            result = self.run_generator(GRAPH_ROOT, generated)

            self.assertEqual(result.returncode, 0, result.stderr)
            plan = json.loads((generated / "torch" / "cu130" / "zlib_ng.plan.json").read_text(encoding="utf-8"))
            self.assertEqual(
                plan["toolchain"]["env"],
                {
                    "CC": "/usr/bin/gcc",
                    "CXX": "/usr/bin/g++",
                    "CFLAGS": "",
                    "CXXFLAGS": "",
                    "CUDAFLAGS": "",
                    "NVCC_FLAGS": "-forward-unknown-to-host-compiler",
                },
            )

    def test_generator_code_change_that_preserves_output_keeps_merkle_key(self) -> None:
        with tempfile.TemporaryDirectory(dir=scratch_parent()) as tmp:
            fake_root = Path(tmp) / "repo"
            shutil.copytree(GRAPH_ROOT / "plans", fake_root / "plans")
            shutil.copytree(GRAPH_ROOT / "toolchains", fake_root / "toolchains")
            (fake_root / "rootfs").mkdir(parents=True)
            shutil.copy2(
                GRAPH_ROOT / "rootfs" / "cuda_ecosystem.lock.json",
                fake_root / "rootfs" / "cuda_ecosystem.lock.json",
            )
            (fake_root / "native" / "common").mkdir(parents=True)
            for filename in ("plan_executor.py", "plan_schema.py", "prefix_manifest.py"):
                source = GRAPH_ROOT / "native" / "common" / filename
                if source.exists():
                    shutil.copy2(source, fake_root / "native" / "common" / filename)
            for relative in (
                "native/bzip2/bzip2_action.bzl",
                "native/common/committed_plan_prefix.bzl",
                "native/pytorch/pytorch_action.bzl",
                "native/pytorch/pytorch-v2.14.0-2b3ec348-submodules.json",
                "native/pytorch/runtime_jobs_executor.py",
                "native/triton/triton_action.bzl",
                "native/triton/upstream_pins.json",
                "native/triton/patches/0001-llvm-35901313.patch",
                "native/torchvision/torchvision_action.bzl",
                "native/torchvision/upstream_pins.json",
                "native/torchaudio/torchaudio_action.bzl",
                "native/torchaudio/upstream_pins.json",
                "native/jaxlib/jaxlib_action.bzl",
                "native/jaxlib/runtime_jobs_executor.py",
                "native/jaxlib/committed_plan_support.py",
                "native/jaxlib/upstream_pins.json",
                "native/jaxlib/nested_http_archives.json",
                "native/xz/xz_action.bzl",
                "native/zlib_ng/zlib_ng_action.bzl",
                "native/zstd/zstd_action.bzl",
            ):
                source = GRAPH_ROOT / relative
                target = fake_root / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
            (fake_root / "tools").mkdir()
            for filename in ("gen_plans.py", "plan_key_snapshot.py"):
                shutil.copy2(GRAPH_ROOT / "tools" / filename, fake_root / "tools" / filename)

            first_gen = self.run_generator(fake_root, fake_root / "plans")
            self.assertEqual(first_gen.returncode, 0, first_gen.stderr)
            first = fake_root / "first.keys.json"
            first_snapshot = subprocess.run(
                [
                    sys.executable,
                    str(fake_root / "tools" / "plan_key_snapshot.py"),
                    "--repo-root",
                    str(fake_root),
                    "--out",
                    str(first),
                ],
                check=False,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            self.assertEqual(first_snapshot.returncode, 0, first_snapshot.stderr)

            generator = fake_root / "tools" / "gen_plans.py"
            generator.write_text(generator.read_text(encoding="utf-8") + "\n# harmless generator refactor\n", encoding="utf-8")
            second_gen = self.run_generator(fake_root, fake_root / "plans")
            self.assertEqual(second_gen.returncode, 0, second_gen.stderr)
            second = fake_root / "second.keys.json"
            second_snapshot = subprocess.run(
                [
                    sys.executable,
                    str(fake_root / "tools" / "plan_key_snapshot.py"),
                    "--repo-root",
                    str(fake_root),
                    "--out",
                    str(second),
                ],
                check=False,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            self.assertEqual(second_snapshot.returncode, 0, second_snapshot.stderr)
            self.assertEqual(
                json.loads(first.read_text(encoding="utf-8"))["packages"],
                json.loads(second.read_text(encoding="utf-8"))["packages"],
            )

    def test_pytorch_configure_strips_prefix_maps_from_macros_header(self) -> None:
        for line in ("cu129", "cu130"):
            plan = json.loads((GRAPH_ROOT / "plans" / "torch" / line / "pytorch.plan.json").read_text(encoding="utf-8"))
            script = plan["configure"]["argv"][2]
            self.assertIn("normalize_macros_build_flags() (", script)
            self.assertIn("\nnormalize_macros_build_flags\n", script)
            self.assertIn("\nnormalize_aten_config_include_dir\n", script)

    def test_jaxlib_nested_build_maps_generated_header_paths(self) -> None:
        # f2c7cd6a jax/cu130: _mosaic_gpu_ext.so carried bazel-out/k8-opt/bin/external/... __FILE__ strings.
        for line in ("cu129", "cu130"):
            plan = json.loads((GRAPH_ROOT / "plans" / "jax" / line / "jaxlib.plan.json").read_text(encoding="utf-8"))
            self.assertIn(
                "--bazel_options=--copt=-ffile-prefix-map=bazel-out/k8-opt/bin/external/=external/",
                plan["nested_bazel"]["build_py_args"],
            )

    def test_cuda_wheels_compile_from_fixed_line_build_root(self) -> None:
        # nvcc writes device __FILE__ strings with no prefix-map; the source path must be constant.
        for package in ("pytorch", "torchvision", "torchaudio"):
            for line in ("cu129", "cu130"):
                with self.subTest(package=package, line=line):
                    path = GRAPH_ROOT / "plans" / "torch" / line / f"{package}.plan.json"
                    plan = json.loads(path.read_text(encoding="utf-8"))
                    root = f"/vaso/build/{package}-{line}"
                    configure = plan["configure"]["argv"][2]
                    self.assertIn(f'fixed_root="{root}"', configure)
                    self.assertIn('cp -a "{src}/." "$fixed_root/src/"', configure)
                    self.assertEqual(configure.count("{src}"), 1)
                    for phase in ("build", "install"):
                        script = plan[phase]["argv"][2]
                        self.assertNotIn("{src}", script)
                        self.assertIn('"$fixed_root/owner"', script)
                    self.assertIn(f"{root}/src", plan["build"]["argv"][2])
                    self.assertTrue(plan["install"]["argv"][2].endswith(f'rm -rf "{root}"\n'))

    def test_generated_pytorch_plan_is_a_wheel_build_from_committed_data(self) -> None:
        with tempfile.TemporaryDirectory(dir=scratch_parent()) as tmp:
            generated = Path(tmp) / "plans"
            result = self.run_generator(GRAPH_ROOT, generated)

            self.assertEqual(result.returncode, 0, result.stderr)
            plan = json.loads((generated / "torch" / "cu130" / "pytorch.plan.json").read_text(encoding="utf-8"))

        self.assertEqual(plan["package"], {"name": "py-torch", "version": "2.14.0"})
        self.assertEqual(plan["sources"][0]["sha256"], "62dedab65c9d7dda7cbb6edaad07d325ad193014496e5da4540a5c074cd8a50a")
        self.assertEqual(plan["sources"][0]["strip_prefix"], "pytorch-v2.14.0")
        self.assertIn({"kind": "wheel", "path": "{wheel}"}, plan["outputs"])
        self.assertIn({"kind": "prefix", "path": "{out}"}, plan["outputs"])
        self.assertIn("cuda", plan["deps"])
        self.assertIn("cudss", plan["deps"])
        self.assertIn("nvshmem", plan["deps"])
        self.assertIn("py-scikit-build-core", plan["deps"])
        self.assertEqual(plan["deps"]["zstd"]["label"], "//native/zstd:zstd_action_prefix")
        self.assertEqual(plan["deps"]["zstd"]["manifest"], "action://native/zstd:zstd_action_prefix_manifest")
        self.assertNotIn("@zstd_native//:prefix_manifest.json", json.dumps(plan["deps"], sort_keys=True))
        self.assertNotIn("native/pytorch/plan.py", json.dumps(plan, sort_keys=True))
        self.assertNotIn("action_driver.py", json.dumps(plan, sort_keys=True))
        self.assertEqual(plan["determinism"]["source_date_epoch"], 0)
        plan_text = json.dumps(plan, sort_keys=True)
        self.assertIn("{jobs}", plan_text)
        self.assertIn("export MAX_JOBS={jobs}", plan_text)
        self.assertIn("export TORCH_CUDA_ARCH_LIST=10.0+PTX", plan_text)
        self.assertIn("export USE_CUDA=1", plan_text)
        self.assertIn("export USE_DISTRIBUTED=1", plan_text)
        self.assertIn("export USE_CUDSS=1", plan_text)
        self.assertIn("export USE_NVSHMEM=1", plan_text)
        self.assertIn("-DCPUINFO_SOURCE_DIR=", plan_text)
        self.assertIn("-DPYTHON_PEACHPY_SOURCE_DIR=/vaso/build/pytorch-cu130/src/third_party/python-peachpy", plan_text)
        self.assertIn("-DPTHREADPOOL_SOURCE_DIR=", plan_text)
        self.assertNotIn("VASO_PYTORCH_MAX_JOBS", plan_text)
        self.assertNotIn("PYTORCH_MAX_JOBS", plan_text)
        self.assertNotIn("VASO_BAZEL_OB", plan_text)
        self.assertNotIn("TMPDIR", plan_text)
        configure_command = plan["configure"]["argv"][2]
        self.assertIn("SparseCsrTensorMath.cu", configure_command)
        self.assertIn("third_party/psimd", configure_command)
        self.assertIn("cmake/Dependencies.cmake", configure_command)
        build_command = plan["build"]["argv"][2]
        self.assertIn("pip wheel", build_command)
        self.assertIn("--no-build-isolation", build_command)
        self.assertNotIn("--no-cache-dir", build_command)
        self.assertIn("-w \"{work}/wheelhouse\"", build_command)
        install_command = plan["install"]["argv"][2]
        self.assertIn("--no-compile", install_command)
        self.assertIn("direct_url.json", install_command)
        self.assertIn("__pycache__", install_command)
        self.assertIn("torch-*.whl", install_command)


if __name__ == "__main__":
    unittest.main()
