#!/usr/bin/env python3

from __future__ import annotations

import importlib.util
import contextlib
import io
import json
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).with_name("plan.py")
ACTION_RULE = Path(__file__).with_name("jaxlib_action.bzl")
ACTION_DRIVER = Path(__file__).with_name("action_driver.py")
PINS = Path(__file__).with_name("upstream_pins.json")
EXPERIMENT_ROOT = SCRIPT.parents[2]
MODULE = EXPERIMENT_ROOT / "MODULE.bazel"
NATIVE_OVERRIDES = EXPERIMENT_ROOT / "native_overrides.json"
JAX_CU130_GRAPH = EXPERIMENT_ROOT / "graphs" / "py_jax_0102_cu130_build_graph.json"
JAX_CU129_GRAPH = EXPERIMENT_ROOT / "graphs" / "py_jax_0102_cu129_build_graph.json"
TOOLS_DIR = EXPERIMENT_ROOT / "tools"
BUILD_FILE = SCRIPT.with_name("BUILD.bazel")
ONE_LLVM_COMMIT = "35901313800ea6e6cbeb9226e51c7c4b29bfc40e"
FIXTURE_PYTHON_MAJOR = "3"
FIXTURE_PYTHON_MINOR = "13"
FIXTURE_PYTHON_VERSION = FIXTURE_PYTHON_MAJOR + "." + FIXTURE_PYTHON_MINOR
FIXTURE_PYTHON_TAG = "cp" + FIXTURE_PYTHON_MAJOR + FIXTURE_PYTHON_MINOR
FIXTURE_PYTHON_SEGMENT = "python" + FIXTURE_PYTHON_VERSION
FIXTURE_SITE_PACKAGES = Path("lib") / FIXTURE_PYTHON_SEGMENT / "site-packages"
L5_REQUIRED_PREFIXES = (
    "python",
    "python-venv",
    "py-pip",
    "py-setuptools",
    "py-wheel",
    "py-numpy",
    "bazel",
    "llvm",
    "cuda",
    "cudnn",
    "nccl",
    "nvshmem",
    "xxd-standalone",
)
L5_PREFIX_ATTRS = {
    key: key.replace("-", "_") + "_prefix_file"
    for key in L5_REQUIRED_PREFIXES
}


def native_toolchain_fixture(cuda_home: str | Path = "/usr/local/cuda", **overrides: object) -> dict[str, object]:
    cuda_home_text = str(cuda_home)
    fixture: dict[str, object] = {
        "schema_version": 1,
        "cc": {
            "cc": "/usr/lib/llvm-23/bin/clang",
            "cxx": "/usr/lib/llvm-23/bin/clang++",
            "toolchain_identifier": "vaso-rootfs-clang-23",
        },
        "cuda": {
            "arch_list": "10.0+PTX",
            "home": cuda_home_text,
            "home_env": "VASO_CUDA_HOME",
            "nvcc": str(Path(cuda_home_text) / "bin" / "nvcc"),
            "prefix_file": "@cuda_native//:prefix_path.txt",
            "providers": [
                {"package": "cuda", "label": "@cuda_native//:lib"},
            ],
        },
        "determinism": {
            "env": {
                "SOURCE_DATE_EPOCH": "1700000000",
                "PYTHONHASHSEED": "0",
                "CFLAGS": "-ffile-prefix-map=/vaso-ob/execroot/_main=.",
                "CXXFLAGS": "-fdebug-prefix-map=/vaso-ob/execroot/_main=.",
                "NVCC_FLAGS": "--frandom-seed=abcdef12 --objdir-as-tempdir",
            },
            "prefix_map_flags": "-ffile-prefix-map=/vaso-ob/execroot/_main=.",
            "prefix_map_roots": "/vaso-ob/execroot/_main",
        },
        "python": {
            "prefix_file": "@python_313_native//:prefix_path.txt",
            "version": "3.13.13",
        },
    }
    fixture.update(overrides)
    return fixture


def write_native_toolchain(path: Path, *, cuda_home: str | Path = "/usr/local/cuda", **overrides: object) -> Path:
    path.write_text(json.dumps(native_toolchain_fixture(cuda_home, **overrides), sort_keys=True) + "\n", encoding="utf-8")
    return path


def temporary_directory():
    base = os.environ.get("TEST_TMPDIR") or os.environ.get("VASO_AGENT_IO_ROOT")
    return tempfile.TemporaryDirectory(dir=base)


def load_plan_module():
    if not SCRIPT.exists():
        raise AssertionError("L5 requires native/jaxlib/plan.py")
    spec = importlib.util.spec_from_file_location("jaxlib_plan", SCRIPT)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def load_spack_to_bazel_module():
    script = TOOLS_DIR / "spack_to_bazel.py"
    if not script.exists():
        raise AssertionError("native provider flip tests require tools/spack_to_bazel.py")
    spec = importlib.util.spec_from_file_location("spack_to_bazel", script)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def build_target_block(name: str) -> str:
    build = BUILD_FILE.read_text(encoding="utf-8")
    marker = f'    name = "{name}",'
    name_index = build.index(marker)
    call_start = build.rfind("jaxlib_action_prefix(", 0, name_index)
    call_end = build.index("\n)", name_index) + 2
    return build[call_start:call_end]


def module_text_with_includes() -> str:
    module = MODULE.read_text(encoding="utf-8")
    parts = [module]
    for include_label in re.findall(r'^[ \t]*include\("([^"]+)"\)', module, re.M):
        if include_label.startswith("//:"):
            include_path = MODULE.parent / include_label.removeprefix("//:")
        elif include_label.startswith(":"):
            include_path = MODULE.parent / include_label.removeprefix(":")
        else:
            continue
        if include_path.is_file():
            parts.append(include_path.read_text(encoding="utf-8"))
    return "\n".join(parts)


class JaxlibPlanTest(unittest.TestCase):
    def setUp(self) -> None:
        self.plan = load_plan_module()

    def make_prefixes(self, root: Path, python_abi: str = FIXTURE_PYTHON_TAG) -> dict[str, str]:
        prefixes = {name: root / name for name in self.plan.REQUIRED_PREFIXES}
        for path in prefixes.values():
            path.mkdir(parents=True)

        python_version = self.plan.python_version_from_abi(python_abi)
        assert python_version is not None
        site_packages = Path("lib") / f"python{python_version}" / "site-packages"
        files = {
            "python": ("bin/python3", f"include/python{python_version}/Python.h"),
            "python-venv": ("pyvenv.cfg", f"bin/python{python_version}", str(site_packages / ".keep")),
            "py-pip": ("bin/pip", str(site_packages / "pip/__init__.py")),
            "py-setuptools": (str(site_packages / "setuptools/__init__.py"),),
            "py-wheel": ("bin/wheel", str(site_packages / "wheel/__init__.py")),
            "py-numpy": (str(site_packages / "numpy/__init__.py"),),
            "bazel": ("bin/bazel",),
            "llvm": ("bin/clang", "bin/clang++", "bin/ld.lld", "bin/llvm-config"),
            "cuda": ("bin/nvcc", "bin/ptxas", "include/cuda.h", "lib64/libcudart.so"),
            "cudnn": ("include/cudnn.h", "lib64/libcudnn.so"),
            "nccl": ("include/nccl.h", "lib/libnccl.so"),
            "nvshmem": ("include/nvshmem.h", "lib/libnvshmem_host.so"),
            "xxd-standalone": ("bin/xxd",),
        }
        for key, rels in files.items():
            for rel in rels:
                p = prefixes[key] / rel
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text("#!/bin/sh\n" if "/bin/" in rel else "", encoding="utf-8")
                if "/bin/" in rel:
                    p.chmod(0o755)
        return {key: str(value) for key, value in prefixes.items()}

    def run_plan(self, prefixes: dict[str, str], *extra: str) -> tuple[int, dict]:
        with temporary_directory() as tmp:
            out = Path(tmp) / "plan.json"
            argv = ["--out", str(out)]
            for key, value in prefixes.items():
                argv.extend(["--prefix", f"{key}={value}"])
            if "--native-toolchain" not in extra:
                native_toolchain = write_native_toolchain(Path(tmp) / "native_toolchain.json", cuda_home=prefixes["cuda"])
                argv.extend(["--native-toolchain", str(native_toolchain)])
            argv.extend(extra)
            rc = self.plan.main(argv)
            return rc, json.loads(out.read_text(encoding="utf-8"))

    def assert_plan_exits(self, prefixes: dict[str, str], *extra: str) -> str:
        with temporary_directory() as tmp:
            out = Path(tmp) / "plan.json"
            argv = ["--out", str(out)]
            for key, value in prefixes.items():
                argv.extend(["--prefix", f"{key}={value}"])
            argv.extend(extra)
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr), self.assertRaises(SystemExit) as cm:
                self.plan.main(argv)
            return stderr.getvalue() + str(cm.exception)

    def test_pins_record_jax_0102_and_one_llvm_sources(self) -> None:
        pins = self.plan.load_pins(PINS)
        self.assertEqual(pins["jax"]["version"], "0.10.2")
        self.assertEqual(pins["jax"]["sdist_sha256"], "bf77428a8c2e6904c4f46d5ab12aa5cfc6cad2179f07f7e4c0fc75ac86ef0639")
        self.assertEqual(pins["jaxlib"]["version"], "0.10.2")
        self.assertEqual(pins["jaxlib"]["source_archive_sha256"], "fa7214ab31ed1cd418b4305807e9c4f3f175c783eeea40c28e0f77c3f4c24bc7")
        self.assertEqual(pins["jaxlib"]["bazel_version"], "7.7.0")
        self.assertEqual(pins["jaxlib"]["xla_commit"], "5a9e73cbd92530cac2ac36f4736a774b2412afe2")
        self.assertEqual(pins["llvm"]["selected_source_commit"], ONE_LLVM_COMMIT)
        self.assertEqual(pins["llvm"]["selected_prefix"], "/usr/lib/llvm-23")
        self.assertEqual(pins["llvm"]["selected_version"], "23.0.0git")

    def test_plan_matches_spack_overlay_build_py_contract(self) -> None:
        with temporary_directory() as tmp:
            prefixes = self.make_prefixes(Path(tmp), python_abi=FIXTURE_PYTHON_TAG)
            rc, doc = self.run_plan(prefixes, "--python-abi", FIXTURE_PYTHON_TAG, "--cuda-line", "cu130")

        self.assertEqual(rc, 0)
        self.assertEqual(doc["package"], "py-jaxlib")
        self.assertEqual(doc["version"], "0.10.2")
        self.assertEqual(tuple(doc["required_prefixes"]), L5_REQUIRED_PREFIXES)
        self.assertTrue(doc["preflight_ok"])
        self.assertFalse(doc["will_build"])
        self.assertEqual(doc["mode"], "dry-run")
        self.assertEqual(doc["source"]["repo"], "@jax_v0_10_2_source")
        self.assertEqual(doc["source"]["tag"], "jax-v0.10.2")
        self.assertEqual(doc["llvm"]["required_source_commit"], ONE_LLVM_COMMIT)
        self.assertEqual(doc["llvm"]["rootfs_prefix"], "/usr/lib/llvm-23")
        self.assertEqual(doc["cuda"]["line"], "cu130")
        self.assertEqual(doc["cuda"]["major_version"], "13")
        self.assertEqual(doc["cuda"]["hermetic_cuda_version"], "13.0.3")
        self.assertEqual(doc["wheels"], ["jax", "jaxlib", "jax-cuda-plugin", "jax-cuda-pjrt"])
        self.assertEqual(
            doc["nested_bazel_prefetch_targets"],
            [
                "//:jax_wheel",
                "//jaxlib/tools:jaxlib_wheel",
                "//jaxlib/tools:jax_cuda13_plugin_wheel",
                "//jaxlib/tools:jax_cuda13_pjrt_wheel",
            ],
        )

        args = doc["build_py_args"]
        self.assertEqual(args[:2], ["build/build.py", "build"])
        for expected in (
            "--wheels=jax,jaxlib,jax-cuda-plugin,jax-cuda-pjrt",
            "--python_version=",
            "--bazel_path=" + str(Path(prefixes["bazel"]) / "bin" / "bazel"),
            "--clang_path=/usr/lib/llvm-23/bin/clang",
            "--build_cuda_with_clang",
            "--cuda_major_version=13",
            "--cuda_compute_capabilities=sm_100",
            "--bazel_options=--config=build_cuda_with_clang",
            "--bazel_options=--repo_env=USE_HERMETIC_CC_TOOLCHAIN=0",
            "--bazel_options=--@rules_ml_toolchain//common:enable_hermetic_cc=False",
            "--bazel_options=--repo_env=CLANG_CUDA_COMPILER_PATH=/usr/lib/llvm-23/bin/clang",
            "--bazel_options=--cxxopt=-std=gnu++17",
            "--bazel_options=--host_cxxopt=-std=gnu++17",
            "--bazel_options=--repo_env=LOCAL_CUDA_PATH=" + prefixes["cuda"],
            "--bazel_options=--repo_env=LOCAL_CUDNN_PATH=" + prefixes["cudnn"],
            "--bazel_options=--repo_env=LOCAL_NCCL_PATH=" + prefixes["nccl"],
            "--bazel_options=--repo_env=LOCAL_NVSHMEM_PATH=" + prefixes["nvshmem"],
            "--bazel_options=--repo_env=HERMETIC_CUDA_VERSION=13.0.3",
            "--bazel_options=--config=cuda_libraries_from_stubs",
            "--bazel_startup_options=--nohome_rc",
            "--bazel_startup_options=--nosystem_rc",
        ):
            self.assertIn(expected, args)
        self.assertTrue(doc["cuda"]["uses_clang_cuda"])
        self.assertFalse(doc["cuda"]["uses_nvcc"])
        self.assertFalse(any("build_cuda_with_nvcc" in arg for arg in args))
        self.assertFalse(any("TF_NVCC_CLANG" in arg for arg in args))

        env = doc["build_env"]
        self.assertEqual(env["PIP_NO_INDEX"], "1")
        self.assertEqual(env["JAX_RELEASE"], "1")
        self.assertEqual(env["CC"], "/usr/lib/llvm-23/bin/clang")
        self.assertEqual(env["CXX"], "/usr/lib/llvm-23/bin/clang++")
        self.assertEqual(env["CUDA_HOME"], prefixes["cuda"])
        self.assertEqual(env["LOCAL_CUDA_PATH"], prefixes["cuda"])
        self.assertEqual(env["LOCAL_CUDNN_PATH"], prefixes["cudnn"])
        self.assertEqual(env["LOCAL_NCCL_PATH"], prefixes["nccl"])
        self.assertEqual(env["LOCAL_NVSHMEM_PATH"], prefixes["nvshmem"])
        self.assertEqual(env["HERMETIC_CUDA_VERSION"], "13.0.3")
        self.assertIn(str(Path(prefixes["bazel"]) / "bin"), env["PATH"].split(os.pathsep))
        pythonpath = env["PYTHONPATH"].split(os.pathsep)
        self.assertIn(str(Path(prefixes["py-numpy"]) / FIXTURE_SITE_PACKAGES), pythonpath)
        self.assertNotIn("py-scipy", doc["required_prefixes"])
        self.assertNotIn("py-ml-dtypes", doc["required_prefixes"])
        self.assertFalse(any("py-scipy" in item for item in pythonpath))
        self.assertFalse(any("py-ml-dtypes" in item for item in pythonpath))

    def test_plan_uses_cuda12_wheel_line_for_cu129(self) -> None:
        with temporary_directory() as tmp:
            prefixes = self.make_prefixes(Path(tmp), python_abi=FIXTURE_PYTHON_TAG)
            rc, doc = self.run_plan(prefixes, "--python-abi", FIXTURE_PYTHON_TAG, "--cuda-line", "cu129")

        self.assertEqual(rc, 0)
        self.assertEqual(doc["cuda"]["line"], "cu129")
        self.assertEqual(doc["cuda"]["major_version"], "12")
        self.assertEqual(doc["cuda"]["hermetic_cuda_version"], "12.9.1")
        self.assertIn("--cuda_major_version=12", doc["build_py_args"])
        self.assertIn("--bazel_options=--repo_env=HERMETIC_CUDA_VERSION=12.9.1", doc["build_py_args"])
        self.assertEqual(
            doc["nested_bazel_prefetch_targets"],
            [
                "//:jax_wheel",
                "//jaxlib/tools:jaxlib_wheel",
                "//jaxlib/tools:jax_cuda12_plugin_wheel",
                "//jaxlib/tools:jax_cuda12_pjrt_wheel",
            ],
        )

    def test_plan_env_uses_native_toolchain_descriptor_for_clang_cuda_and_determinism(self) -> None:
        with temporary_directory() as tmp:
            tmp_path = Path(tmp)
            prefixes = self.make_prefixes(tmp_path, python_abi=FIXTURE_PYTHON_TAG)
            native_toolchain = write_native_toolchain(tmp_path / "native_toolchain.json", cuda_home=prefixes["cuda"])
            rc, doc = self.run_plan(
                prefixes,
                "--python-abi",
                FIXTURE_PYTHON_TAG,
                "--native-toolchain",
                str(native_toolchain),
            )

        self.assertEqual(rc, 0)
        env = doc["build_env"]
        self.assertEqual(env["CC"], "/usr/lib/llvm-23/bin/clang")
        self.assertEqual(env["CXX"], "/usr/lib/llvm-23/bin/clang++")
        self.assertEqual(env["CUDA_HOME"], prefixes["cuda"])
        self.assertEqual(env["CUDA_PATH"], prefixes["cuda"])
        self.assertNotIn("CUDA_TOOLKIT_ROOT_DIR", env)
        self.assertEqual(env["LOCAL_CUDA_PATH"], prefixes["cuda"])
        self.assertEqual(env["SOURCE_DATE_EPOCH"], "1700000000")
        self.assertEqual(env["PYTHONHASHSEED"], "0")
        self.assertEqual(env["CFLAGS"], "-ffile-prefix-map=/vaso-ob/execroot/_main=.")
        self.assertEqual(env["CXXFLAGS"], "-fdebug-prefix-map=/vaso-ob/execroot/_main=.")
        self.assertNotIn("NVCC_FLAGS", env)
        args = doc["build_py_args"]
        self.assertIn("--clang_path=/usr/lib/llvm-23/bin/clang", args)
        self.assertIn("--bazel_options=--repo_env=CLANG_CUDA_COMPILER_PATH=/usr/lib/llvm-23/bin/clang", args)
        self.assertIn("--bazel_options=--repo_env=LOCAL_CUDA_PATH=" + prefixes["cuda"], args)
        self.assertIn("--bazel_options=--action_env=SOURCE_DATE_EPOCH=1700000000", args)
        self.assertIn("--bazel_options=--action_env=PYTHONHASHSEED=0", args)
        self.assertIn(
            "--bazel_options=--action_env=CFLAGS=-ffile-prefix-map=/vaso-ob/execroot/_main=.",
            args,
        )
        self.assertIn(
            "--bazel_options=--action_env=CXXFLAGS=-fdebug-prefix-map=/vaso-ob/execroot/_main=.",
            args,
        )
        self.assertFalse(any("NVCC_FLAGS" in arg for arg in args))
        self.assertEqual(doc["native_toolchain"]["cc"]["toolchain_identifier"], "vaso-rootfs-clang-23")

    def test_jax_family_requires_native_toolchain_descriptor(self) -> None:
        with temporary_directory() as tmp:
            prefixes = self.make_prefixes(Path(tmp), python_abi=FIXTURE_PYTHON_TAG)
            error = self.assert_plan_exits(prefixes, "--python-abi", FIXTURE_PYTHON_TAG)

        self.assertIn("--native-toolchain", error)

    def test_jax_family_rejects_compiler_path_outside_selected_toolchain(self) -> None:
        with temporary_directory() as tmp:
            tmp_path = Path(tmp)
            prefixes = self.make_prefixes(tmp_path, python_abi=FIXTURE_PYTHON_TAG)
            native_toolchain = write_native_toolchain(
                tmp_path / "native_toolchain.json",
                cuda_home=prefixes["cuda"],
                cc={
                    "cc": "/opt/outside-toolchain/bin/clang",
                    "cxx": "/usr/lib/llvm-23/bin/clang++",
                    "toolchain_identifier": "vaso-rootfs-clang-23",
                },
            )
            error = self.assert_plan_exits(
                prefixes,
                "--python-abi",
                FIXTURE_PYTHON_TAG,
                "--native-toolchain",
                str(native_toolchain),
            )

        self.assertIn("cc.cc", error)
        self.assertIn("vaso-rootfs-clang-23", error)

    def test_jax_family_rejects_gcc_descriptor(self) -> None:
        with temporary_directory() as tmp:
            tmp_path = Path(tmp)
            prefixes = self.make_prefixes(tmp_path, python_abi=FIXTURE_PYTHON_TAG)
            native_toolchain = write_native_toolchain(
                tmp_path / "native_toolchain.json",
                cuda_home=prefixes["cuda"],
                cc={
                    "cc": "/usr/bin/gcc",
                    "cxx": "/usr/bin/g++",
                    "toolchain_identifier": "vaso-rootfs-gcc-13.3",
                },
            )
            error = self.assert_plan_exits(
                prefixes,
                "--python-abi",
                FIXTURE_PYTHON_TAG,
                "--native-toolchain",
                str(native_toolchain),
            )

        self.assertIn("jaxlib requires compiler family clang", error)

    def test_jax_family_rejects_cuda_home_that_differs_from_selected_prefix(self) -> None:
        with temporary_directory() as tmp:
            tmp_path = Path(tmp)
            prefixes = self.make_prefixes(tmp_path, python_abi=FIXTURE_PYTHON_TAG)
            native_toolchain = write_native_toolchain(
                tmp_path / "native_toolchain.json",
                cuda={
                    "arch_list": "10.0+PTX",
                    "home": "/descriptor/cuda",
                    "nvcc": "/descriptor/cuda/bin/nvcc",
                    "prefix_file": "@cuda_native//:prefix_path.txt",
                    "providers": [
                        {"package": "cuda", "label": "@cuda_native//:lib"},
                    ],
                },
            )
            rc, doc = self.run_plan(
                prefixes,
                "--python-abi",
                FIXTURE_PYTHON_TAG,
                "--native-toolchain",
                str(native_toolchain),
            )

        self.assertEqual(rc, 1)
        self.assertFalse(doc["preflight_ok"])
        self.assertIn("cuda.home", doc["env_error"])
        self.assertIn(prefixes["cuda"], doc["env_error"])

    def test_jax_family_rejects_missing_cuda_toolchain_info(self) -> None:
        with temporary_directory() as tmp:
            tmp_path = Path(tmp)
            prefixes = self.make_prefixes(tmp_path, python_abi=FIXTURE_PYTHON_TAG)
            native_toolchain = write_native_toolchain(tmp_path / "native_toolchain.json", cuda={})
            error = self.assert_plan_exits(
                prefixes,
                "--python-abi",
                FIXTURE_PYTHON_TAG,
                "--native-toolchain",
                str(native_toolchain),
            )

        self.assertIn("cuda.home", error)

    def test_jax_family_rejects_duplicate_cuda_family_provider(self) -> None:
        with temporary_directory() as tmp:
            tmp_path = Path(tmp)
            prefixes = self.make_prefixes(tmp_path, python_abi=FIXTURE_PYTHON_TAG)
            native_toolchain = write_native_toolchain(
                tmp_path / "native_toolchain.json",
                cuda={
                    "arch_list": "10.0+PTX",
                    "home": prefixes["cuda"],
                    "nvcc": str(Path(prefixes["cuda"]) / "bin" / "nvcc"),
                    "prefix_file": "@cuda_native//:prefix_path.txt",
                    "providers": [
                        {"package": "cuda", "label": "@cuda_native//:lib"},
                        {"package": "cuda", "label": "@cuda_13_3_native//:lib"},
                    ],
                },
            )
            error = self.assert_plan_exits(
                prefixes,
                "--python-abi",
                FIXTURE_PYTHON_TAG,
                "--native-toolchain",
                str(native_toolchain),
            )

        self.assertIn("CUDA-family package cuda appears more than once", error)

    def test_execute_without_token_is_refused(self) -> None:
        with temporary_directory() as tmp:
            prefixes = self.make_prefixes(Path(tmp))
            rc, doc = self.run_plan(prefixes, "--execute")

        self.assertEqual(rc, 2)
        self.assertEqual(doc["authorization"]["required_token"], self.plan.REQUIRED_TOKEN)
        self.assertFalse(doc["authorization"]["token_present"])
        self.assertFalse(doc["will_build"])

    def test_execute_with_token_and_complete_preflight_requests_build(self) -> None:
        with temporary_directory() as tmp:
            prefixes = self.make_prefixes(Path(tmp), python_abi=FIXTURE_PYTHON_TAG)
            rc, doc = self.run_plan(
                prefixes,
                "--execute",
                "--token",
                self.plan.REQUIRED_TOKEN,
                "--python-abi",
                FIXTURE_PYTHON_TAG,
            )

        self.assertEqual(rc, 0)
        self.assertEqual(doc["mode"], "execute")
        self.assertTrue(doc["authorization"]["token_present"])
        self.assertTrue(doc["preflight_ok"])
        self.assertTrue(doc["will_build"])

    def test_action_rule_exposes_committed_plan_contract(self) -> None:
        self.assertTrue(ACTION_RULE.exists(), "N1 requires a committed-plan jaxlib action rule")
        script = ACTION_RULE.read_text(encoding="utf-8")

        self.assertIn("JaxlibNativePrefixInfo = provider(", script)
        self.assertIn('attrs = rootfs_platform_forward_attrs("jax")', script)
        self.assertIn("ctx.actions.declare_directory(output_basename + \"_prefix\")", script)
        self.assertIn("ctx.actions.declare_directory(output_basename + \"_wheels\")", script)
        self.assertIn("ctx.actions.declare_file(output_basename + \"_wheel_manifest.json\")", script)
        self.assertIn("ctx.actions.declare_file(output_basename + \"_build_plan.json\")", script)
        self.assertIn("ctx.actions.declare_file(output_basename + \"_provider_metadata.json\")", script)
        self.assertIn("ctx.actions.declare_file(output_basename + \"_result.txt\")", script)
        self.assertNotIn("ctx.actions.declare_directory(ctx.label.name + \"_work\")", script)
        self.assertIn("ctx.actions.run(", script)
        self.assertNotIn("ctx.actions.run_shell(", script)
        self.assertNotIn("repository_rule(", script)
        self.assertIn("plan_executor.py", script)
        self.assertIn("metadata_writer.py", script)
        self.assertIn("plans:schema/v1.json", script)
        self.assertIn("plans:keys.json", script)
        self.assertIn("--plan", script)
        self.assertIn("--schema", script)
        self.assertIn("--rootfs-identity", script)
        self.assertIn("--prefix-out", script)
        self.assertIn("--wheel-out", script)
        self.assertIn("--nested-cache-manifest", script)
        self.assertIn("--nested-distdir", script)
        self.assertIn("--nested-repository-cache", script)
        self.assertIn("--nested-disk-cache", script)
        self.assertIn("--wheel-manifest-out", script)
        self.assertIn("--source-archive", script)
        self.assertNotIn("--source-anchor", script)
        self.assertNotIn("--native-toolchain", script)
        self.assertNotIn("native_toolchain.toolchain_json", script)
        self.assertNotIn("action_driver.py", script)
        self.assertIn("jaxlib_token_flag = rule(", script)
        self.assertNotIn("JaxlibNestedPrefetchInfo", script)
        self.assertNotIn("jaxlib_nested_prefetch = rule(", script)
        self.assertIn("build_setting = config.string(flag = True)", script)
        self.assertIn("ctx.build_setting_value", script)
        self.assertIn("ctx.attr._token_flag[JaxlibTokenInfo].value", script)
        self.assertNotIn("\"token\": attr.string", script)
        self.assertIn("use_default_shell_env = False", script)
        self.assertIn('"LC_ALL": "C"', script)
        self.assertIn('"PATH": "/usr/bin:/bin"', script)
        self.assertIn('"PYTHONHASHSEED": "0"', script)
        self.assertIn('"VASO_ACTION_RESERVED_CPUS": str(_JAXLIB_ACTION_CPUS)', script)
        self.assertNotIn("\"VASO_IN_INSULA\":", script)
        self.assertNotIn("\"VASO_ROOTFS_BUNDLE_MANIFEST\":", script)
        self.assertNotIn("\"TMPDIR\":", script)

        for provider, attr_name in L5_PREFIX_ATTRS.items():
            with self.subTest(provider=provider):
                self.assertIn(f"(\"{provider}\", \"{attr_name}\")", script)
                self.assertIn(f"\"{attr_name}\": attr.label", script)

    def test_action_driver_uses_native_toolchain_descriptor_contract(self) -> None:
        self.assertTrue(
            ACTION_DRIVER.exists(),
            "L5 requires a configured action driver",
        )
        driver = ACTION_DRIVER.read_text(encoding="utf-8")

        self.assertIn('parser.add_argument("--source-anchor", required=True)', driver)
        self.assertIn('parser.add_argument("--build-work-root", default="")', driver)
        self.assertIn('parser.add_argument("--wheelhouse-out")', driver)
        self.assertIn('parser.add_argument("--native-toolchain", required=True)', driver)
        self.assertIn("read_native_toolchain", driver)
        self.assertIn("native_toolchain_environment", driver)
        self.assertNotIn("DETERMINISTIC_ACTION_ENV_KEYS", driver)
        self.assertNotIn("def _deterministic_action_env(", driver)
        self.assertNotIn("def _append_deterministic_build_py_args(", driver)

    def test_build_file_exposes_token_safe_dry_run_and_real_action(self) -> None:
        build = BUILD_FILE.read_text(encoding="utf-8")

        self.assertIn(
            'load(":jaxlib_action.bzl", "jaxlib_action_prefix", "jaxlib_token_flag")',
            build,
        )
        self.assertIn('name = "token"', build)
        self.assertIn('name = "action_driver"', build)
        self.assertIn('"committed_plan_support.py"', build)
        self.assertIn('"metadata_writer.py"', build)
        self.assertIn('deps = ["//native/common:native_action_toolchain_py"]', build)
        self.assertIn('name = "jaxlib_action_dry_run"', build)
        self.assertIn('name = "jaxlib_action"', build)
        self.assertNotIn('name = "jaxlib_nested_prefetch"', build)
        self.assertIn('name = "action_smoke_test"', build)
        self.assertNotIn('token = "build-native-llvm"', build)
        self.assertNotIn("@llvm_native//:prefix_path.txt", build)

        dry_run = build_target_block("jaxlib_action_dry_run")
        self.assertIn('source_archive = ":synthetic_source_anchor.txt"', dry_run)
        self.assertIn('execute = False', dry_run)
        self.assertIn('":committed_plan_support.py": "native/jaxlib/committed_plan_support.py"', dry_run)
        self.assertIn('":nested_http_archives.json": "native/jaxlib/nested_http_archives.json"', dry_run)
        self.assertIn('":upstream_pins.json": "native/jaxlib/upstream_pins.json"', dry_run)
        self.assertIn('"//plans:release_line_cu129": "//plans:jax/cu129/jaxlib.plan.json"', dry_run)
        self.assertIn('"//plans:release_line_cu130": "//plans:jax/cu130/jaxlib.plan.json"', dry_run)
        self.assertIn('synthetic_prefixes_for_dry_run = True', dry_run)
        for provider, attr_name in L5_PREFIX_ATTRS.items():
            with self.subTest(provider=provider):
                self.assertIn(f"{attr_name} = \":synthetic_prefix_path.txt\"", dry_run)

        action = build_target_block("jaxlib_action")
        self.assertIn('source_archive = "@jax_v0_10_2_source_archive//file"', action)
        self.assertIn('execute = True', action)
        self.assertIn('":committed_plan_support.py": "native/jaxlib/committed_plan_support.py"', action)
        self.assertIn('":nested_http_archives.json": "native/jaxlib/nested_http_archives.json"', action)
        self.assertIn('":upstream_pins.json": "native/jaxlib/upstream_pins.json"', action)
        self.assertIn('"//plans:release_line_cu129": "//plans:jax/cu129/jaxlib.plan.json"', action)
        self.assertIn('"//plans:release_line_cu130": "//plans:jax/cu130/jaxlib.plan.json"', action)
        self.assertIn('python_abi = "derived"', action)
        self.assertIn('python_prefix_file = "@python_313_native//:prefix_path.txt"', action)
        self.assertIn('python_venv_prefix_file = "@python_venv_native//:prefix_path.txt"', action)
        self.assertIn('py_pip_prefix_file = "@py_pip_native//:prefix_path.txt"', action)
        self.assertIn('py_setuptools_prefix_file = "@py_setuptools_82_native//:prefix_path.txt"', action)
        self.assertIn('py_wheel_prefix_file = "@py_wheel_native//:prefix_path.txt"', action)
        self.assertIn('py_numpy_prefix_file = "@py_numpy_native//:prefix_path.txt"', action)
        self.assertIn('bazel_prefix_file = "@rootfs_bazel_native//:prefix_path.txt"', action)
        self.assertIn('llvm_prefix_file = "@rootfs_llvm_23_native//:prefix_path.txt"', action)
        self.assertIn('cuda_prefix_file = "@cuda_native//:prefix_path.txt"', action)
        self.assertIn('cudnn_prefix_file = "@cudnn_native//:prefix_path.txt"', action)
        self.assertIn('nccl_prefix_file = "@nccl_native//:prefix_path.txt"', action)
        self.assertIn('nvshmem_prefix_file = "@nvshmem_native//:prefix_path.txt"', action)
        self.assertIn('xxd_standalone_prefix_file = "@xxd_standalone_native//:prefix_path.txt"', action)
        self.assertNotIn("py_scipy_prefix_file", action)
        self.assertNotIn("py_ml_dtypes_prefix_file", action)

    def test_module_declares_jax_bazel_boundary(self) -> None:
        module = module_text_with_includes()

        self.assertIn(
            'rootfs_bazel_native = use_repo_rule("//native/rootfs_bazel:rootfs_bazel.bzl", "rootfs_bazel_native")',
            module,
        )
        self.assertIn('name = "rootfs_bazel_native"', module)
        self.assertIn('required_version = "7.7.0"', module)
        self.assertNotIn("@openjdk", module)

    def test_module_declares_offline_pinned_jax_source_archive(self) -> None:
        module = module_text_with_includes()

        self.assertIn("http_file = use_repo_rule", module)
        self.assertIn('name = "jax_v0_10_2_source_archive"', module)
        self.assertIn('downloaded_file_path = "jax-v0.10.2.tar.gz"', module)
        self.assertIn('name = "jax_v0_10_2_source"', module)
        self.assertIn('exports_files(["WORKSPACE"])', module)
        self.assertIn('sha256 = "fa7214ab31ed1cd418b4305807e9c4f3f175c783eeea40c28e0f77c3f4c24bc7"', module)
        self.assertIn('strip_prefix = "jax-jax-v0.10.2"', module)
        jax_block = module.split('name = "jax_v0_10_2_source"', 1)[1]
        jax_block = jax_block.split(")", 1)[0]
        self.assertNotIn('exports_files(["build/build.py"])', jax_block)
        self.assertNotIn('glob(["**"])', jax_block)

    def test_jax_graph_xxd_standalone_is_native_provider(self) -> None:
        spack_to_bazel = load_spack_to_bazel_module()
        graph = json.loads(JAX_CU130_GRAPH.read_text(encoding="utf-8"))
        lock = {"packages": {}}
        for node in graph["nodes"]:
            if node.get("package") == "xxd-standalone":
                lock["packages"]["spack_xxd_standalone"] = {
                    "package": "xxd-standalone",
                    "version": node["version"],
                    "build": node["status"],
                }
                break
        else:
            self.fail("JAX 0.10.2 graph must contain xxd-standalone")

        spack_to_bazel.normalize_providers(lock, NATIVE_OVERRIDES)

        node = lock["packages"]["spack_xxd_standalone"]
        self.assertEqual(node["version"], "8.2.1201")
        self.assertEqual(node["build"], "native")
        self.assertEqual(node["native_prefix"], "@xxd_standalone_native//:lib")

    def test_jax_graph_rootfs_boundaries_are_native_providers(self) -> None:
        spack_to_bazel = load_spack_to_bazel_module()
        graph = json.loads(JAX_CU130_GRAPH.read_text(encoding="utf-8"))
        wanted = {
            "cuda": ("13.0.3", "@cuda_native//:lib"),
            "cudnn": ("9.24.0.43-13", "@cudnn_native//:lib"),
            "llvm": (
                "23.0.0",
                "@rootfs_llvm_23_native//:lib",
            ),
        }
        lock = {"packages": {}}
        for node in graph["nodes"]:
            package = node.get("package")
            if package in wanted:
                lock["packages"][f"spack_{package.replace('-', '_')}"] = {
                    "package": package,
                    "version": node["version"],
                    "build": node["status"],
                }

        self.assertEqual(set(lock["packages"]), {f"spack_{key}" for key in wanted})

        spack_to_bazel.normalize_providers(lock, NATIVE_OVERRIDES)

        for package, (version, label) in wanted.items():
            with self.subTest(package=package):
                node = lock["packages"][f"spack_{package}"]
                self.assertEqual(node["version"], version)
                self.assertEqual(node["build"], "native")
                self.assertEqual(node["native_prefix"], label)

    def test_jaxlib_dependency_graph_has_native_build_frontend_providers(self) -> None:
        spack_to_bazel = load_spack_to_bazel_module()
        wanted = {
            "py-absl-py": ("1.4.0", "@py_absl_py_native//:lib"),
            "py-pyproject-hooks": ("1.2.0", "@py_pyproject_hooks_native//:lib"),
            "py-build": ("1.4.3", "@py_build_native//:lib"),
            "py-hatch-fancy-pypi-readme": ("25.1.0", "@py_hatch_fancy_pypi_readme_native//:lib"),
            "py-opt-einsum": ("3.4.0", "@py_opt_einsum_native//:lib"),
            "py-ml-dtypes": ("0.5.1", "@py_ml_dtypes_native//:lib"),
            "py-pythran": ("0.18.1", "@py_pythran_native//:lib"),
            "py-scipy": ("1.17.1", "@py_scipy_native//:lib"),
            "libxml2": ("2.15.3", "@libxml2_215_native//:lib"),
        }

        for graph_path in (JAX_CU130_GRAPH, JAX_CU129_GRAPH):
            with self.subTest(graph=graph_path.name):
                graph = json.loads(graph_path.read_text(encoding="utf-8"))
                lock = {"packages": {}}
                for node in graph["nodes"]:
                    package = node.get("package")
                    if package in wanted:
                        lock["packages"][f"spack_{package.replace('-', '_')}"] = {
                            "package": package,
                            "version": node["version"],
                            "build": node["status"],
                        }

                self.assertEqual(
                    set(lock["packages"]),
                    {f"spack_{package.replace('-', '_')}" for package in wanted},
                )

                spack_to_bazel.normalize_providers(lock, NATIVE_OVERRIDES)

                for package, (version, label) in wanted.items():
                    node = lock["packages"][f"spack_{package.replace('-', '_')}"]
                    with self.subTest(package=package):
                        self.assertEqual(node["version"], version)
                        self.assertEqual(node["build"], "native")
                        self.assertEqual(node["native_prefix"], label)

    def test_jax_graph_root_uses_native_py_jax_provider(self) -> None:
        spack_to_bazel = load_spack_to_bazel_module()

        for graph_path in (JAX_CU130_GRAPH, JAX_CU129_GRAPH):
            with self.subTest(graph=graph_path.name):
                graph = json.loads(graph_path.read_text(encoding="utf-8"))
                lock = {"packages": {}}
                for node in graph["nodes"]:
                    if node.get("package") == "py-jax":
                        lock["packages"]["spack_py_jax"] = {
                            "package": "py-jax",
                            "version": node["version"],
                            "build": node["status"],
                        }
                        break
                else:
                    self.fail("JAX 0.10.2 graph must contain py-jax")

                spack_to_bazel.normalize_providers(lock, NATIVE_OVERRIDES)

                node = lock["packages"]["spack_py_jax"]
                self.assertEqual(node["version"], "0.10.2")
                self.assertEqual(node["build"], "native")
                self.assertEqual(node["native_prefix"], "@py_jax_native//:lib")

    def test_module_declares_native_py_jax_provider(self) -> None:
        module = module_text_with_includes()
        self.assertIn(
            'py_jax_native = use_repo_rule("//native/py_jax:py_jax.bzl", "py_jax_native")',
            module,
        )
        self.assertIn('name = "py_jax_native"', module)
        self.assertIn('sha256 = "bf77428a8c2e6904c4f46d5ab12aa5cfc6cad2179f07f7e4c0fc75ac86ef0639"', module)
        self.assertIn('strip_prefix = "jax-0.10.2"', module)

    def test_module_declares_native_py_absl_py_provider(self) -> None:
        module = module_text_with_includes()
        self.assertIn(
            'py_absl_py_native = use_repo_rule("//native/py_absl_py:py_absl_py.bzl", "py_absl_py_native")',
            module,
        )
        self.assertIn('name = "py_absl_py_native"', module)
        self.assertIn('sha256 = "d2c244d01048ba476e7c080bd2c6df5e141d211de80223460d5b3b8a2a58433d"', module)
        self.assertIn('strip_prefix = "absl-py-1.4.0"', module)

    def test_module_declares_offline_pinned_jax_source(self) -> None:
        module = module_text_with_includes()
        self.assertIn('name = "jax_v0_10_2_source"', module)
        self.assertIn('exports_files(["WORKSPACE"])', module)
        self.assertIn('name = "jax_v0_10_2_source_archive"', module)
        self.assertIn('downloaded_file_path = "jax-v0.10.2.tar.gz"', module)
        self.assertIn("jax-v0.10.2.tar.gz", module)
        self.assertIn('sha256 = "fa7214ab31ed1cd418b4305807e9c4f3f175c783eeea40c28e0f77c3f4c24bc7"', module)
        self.assertIn('strip_prefix = "jax-jax-v0.10.2"', module)


if __name__ == "__main__":
    unittest.main()
