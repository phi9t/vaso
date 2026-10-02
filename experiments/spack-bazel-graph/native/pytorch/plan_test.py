#!/usr/bin/env python3
"""Tests for the native PyTorch dry-run planner."""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).with_name("plan.py")
ACTION_RULE = Path(__file__).with_name("pytorch_action.bzl")
ACTION_DRIVER = Path(__file__).with_name("action_driver.py")
EXPERIMENT_ROOT = SCRIPT.parents[2]
PY_TORCH_LEAN_GRAPH = EXPERIMENT_ROOT / "py_torch_lean_nx_build_graph.json"
PY_TORCH_214_FULL_GRAPH = EXPERIMENT_ROOT / "py_torch_214_full_build_graph.json"
NATIVE_OVERRIDES = EXPERIMENT_ROOT / "native_overrides.json"
MODULE = EXPERIMENT_ROOT / "MODULE.bazel"
BUILD_FILE = SCRIPT.with_name("BUILD.bazel")
RUN_SH = EXPERIMENT_ROOT / "run.sh"
BUILD_SYSTEM_REQUIREMENTS = {
    "numpy": ("py-numpy", None),
    "packaging>=24.2": ("py-packaging", ">=24.2"),
    "pyyaml": ("py-pyyaml", None),
    "scikit-build-core>=1.0": ("py-scikit-build-core", ">=1.0"),
    "typing-extensions>=4.10.0": ("py-typing-extensions", ">=4.10.0"),
    "six": ("py-six", None),
    "pathspec>=0.12.0": ("py-pathspec", ">=0.12.0"),
}
KNOWN_BUILD_REQUIREMENT_GAPS = {
    "py-numpy": "ticket 04: graph has py-numpy@2.3.5 but no native override",
}
FIXTURE_PYTHON_MAJOR = "3"
FIXTURE_PYTHON_MINOR = "13"
FIXTURE_PYTHON_ABI = "cp" + FIXTURE_PYTHON_MAJOR + FIXTURE_PYTHON_MINOR
FIXTURE_PYTHON_VERSION = ".".join((FIXTURE_PYTHON_MAJOR, FIXTURE_PYTHON_MINOR))
FIXTURE_PYTHON_SEGMENT = "python" + FIXTURE_PYTHON_VERSION
FIXTURE_SITE_PACKAGES = Path("lib") / FIXTURE_PYTHON_SEGMENT / "site-packages"
FIXTURE_WHEEL_TAG = "-".join((FIXTURE_PYTHON_ABI, FIXTURE_PYTHON_ABI))
SOURCE_MANIFEST = SCRIPT.with_name("pytorch-v2.14.0-2b3ec348-submodules.json")
SPEC = importlib.util.spec_from_file_location("pytorch_plan", SCRIPT)
assert SPEC is not None
plan = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = plan
SPEC.loader.exec_module(plan)
ACTION_DRIVER_SPEC = importlib.util.spec_from_file_location("pytorch_action_driver", ACTION_DRIVER)
assert ACTION_DRIVER_SPEC is not None
action_driver = importlib.util.module_from_spec(ACTION_DRIVER_SPEC)
assert ACTION_DRIVER_SPEC.loader is not None
sys.modules[ACTION_DRIVER_SPEC.name] = action_driver
ACTION_DRIVER_SPEC.loader.exec_module(action_driver)


PYTHON_BUILD_PREFIXES = (
    "py-pip",
    "py-setuptools",
    "py-wheel",
    "py-scikit-build-core",
    "py-numpy",
    "py-pyyaml",
    "py-typing-extensions",
    "py-six",
    "py-packaging",
    "py-pathspec",
    "py-protobuf",
)
NATIVE_HELPER_PREFIXES = (
    "cpuinfo",
    "fp16",
    "fxdiv",
    "psimd",
    "pthreadpool",
)
PYTHON_BUILD_ATTRS = {
    key: key.replace("-", "_") + "_prefix_file"
    for key in PYTHON_BUILD_PREFIXES
}
NATIVE_HELPER_ATTRS = {
    key: key + "_prefix_file"
    for key in NATIVE_HELPER_PREFIXES
}
PYTHON_BUILD_LABELS = {
    "py-pip": "@py_pip_native//:prefix_path.txt",
    "py-setuptools": "@py_setuptools_native//:prefix_path.txt",
    "py-wheel": "@py_wheel_native//:prefix_path.txt",
    "py-scikit-build-core": "@py_scikit_build_core_native//:prefix_path.txt",
    "py-numpy": "@py_numpy_native//:prefix_path.txt",
    "py-pyyaml": "@py_pyyaml_native//:prefix_path.txt",
    "py-typing-extensions": "@py_typing_extensions_native//:prefix_path.txt",
    "py-six": "@py_six_native//:prefix_path.txt",
    "py-packaging": "@py_packaging_native//:prefix_path.txt",
    "py-pathspec": "@py_pathspec_native//:prefix_path.txt",
    "py-protobuf": "@py_protobuf_native//:prefix_path.txt",
}
NATIVE_HELPER_LABELS = {
    "cpuinfo": "@cpuinfo_native//:prefix_path.txt",
    "fp16": "@fp16_native//:prefix_path.txt",
    "fxdiv": "@fxdiv_native//:prefix_path.txt",
    "psimd": "@psimd_native//:prefix_path.txt",
    "pthreadpool": "@pthreadpool_native//:prefix_path.txt",
}


def temporary_directory():
    base = os.environ.get("TEST_TMPDIR") or os.environ.get("VASO_AGENT_IO_ROOT")
    return tempfile.TemporaryDirectory(dir=base)


def build_target_block(name: str) -> str:
    build = BUILD_FILE.read_text(encoding="utf-8")
    marker = f'    name = "{name}",'
    name_index = build.index(marker)
    call_start = build.rfind("pytorch_action_prefix(", 0, name_index)
    call_end = build.index("\n)", name_index) + 2
    return build[call_start:call_end]


class NativePytorchPlanTest(unittest.TestCase):
    def load_json(self, path: Path) -> dict:
        return json.loads(path.read_text())

    def py_torch_closure(self, graph: dict) -> dict[str, dict]:
        nodes_by_hash = {node["spack_hash"]: node for node in graph["nodes"]}
        root = next(node for node in graph["nodes"] if node["package"] == graph["root"])
        closure_hashes: set[str] = set()
        stack = [dep["hash"] for dep in root["deps"]]
        while stack:
            spack_hash = stack.pop()
            if spack_hash in closure_hashes:
                continue
            closure_hashes.add(spack_hash)
            stack.extend(dep["hash"] for dep in nodes_by_hash[spack_hash]["deps"])
        return {nodes_by_hash[spack_hash]["package"]: nodes_by_hash[spack_hash]
                for spack_hash in closure_hashes}

    def version_tuple(self, version: str) -> tuple[int, ...]:
        parts: list[int] = []
        for part in version.replace("_", ".").replace("-", ".").split("."):
            if part.isdigit():
                parts.append(int(part))
                continue
            number = ""
            for char in part:
                if char.isdigit():
                    number += char
                else:
                    break
            if number:
                parts.append(int(number))
        return tuple(parts)

    def assert_satisfies(self, version: str, specifier: str | None) -> None:
        if specifier is None:
            return
        actual = self.version_tuple(version)
        for raw_part in specifier.split(","):
            part = raw_part.strip()
            if part.startswith(">="):
                minimum = self.version_tuple(part[2:])
                self.assertGreaterEqual(actual, minimum)
            elif part.startswith("<"):
                maximum = self.version_tuple(part[1:])
                self.assertLess(actual, maximum)
            else:
                raise AssertionError(f"unsupported specifier in test: {specifier}")

    def make_prefixes(
        self,
        root: Path,
        complete: bool = True,
        scikit_build_core_version: str = "1.0.0",
    ) -> dict[str, str]:
        prefixes = {
            name: root / name
            for name in (
                "cuda",
                "cudnn",
                "nccl",
                "python",
                "cmake",
                "ninja",
                "openblas",
                "protobuf",
                "cusparselt",
                "openmpi",
                "cudss",
                "nvshmem",
                "numactl",
                *NATIVE_HELPER_PREFIXES,
                *PYTHON_BUILD_PREFIXES,
            )
        }
        for path in prefixes.values():
            path.mkdir(parents=True)
        if complete:
            (prefixes["cuda"] / "bin").mkdir()
            (prefixes["cuda"] / "bin" / "nvcc").write_text("#!/bin/sh\n")
            (prefixes["cudnn"] / "include").mkdir()
            (prefixes["cudnn"] / "include" / "cudnn.h").write_text("")
            (prefixes["cudnn"] / "include" / "cudnn_version.h").write_text("")
            (prefixes["cudnn"] / "lib64").mkdir()
            (prefixes["cudnn"] / "lib64" / "libcudnn.so").write_text("")
            (prefixes["nccl"] / "include").mkdir()
            (prefixes["nccl"] / "include" / "nccl.h").write_text("")
            (prefixes["nccl"] / "lib").mkdir()
            (prefixes["nccl"] / "lib" / "libnccl.so").write_text("")
            (prefixes["python"] / "bin").mkdir()
            python = prefixes["python"] / "bin" / "python3"
            python.write_text(
                "#!/bin/sh\n"
                'if [ "$1" = "-c" ]; then\n'
                f"  printf '%s\\n' '{FIXTURE_SITE_PACKAGES.as_posix()}'\n"
                "  exit 0\n"
                "fi\n"
                "exit 0\n",
                encoding="utf-8",
            )
            os.chmod(python, 0o755)
            (prefixes["cmake"] / "bin").mkdir()
            (prefixes["cmake"] / "bin" / "cmake").write_text("#!/bin/sh\n")
            (prefixes["ninja"] / "bin").mkdir()
            (prefixes["ninja"] / "bin" / "ninja").write_text("#!/bin/sh\n")
            (prefixes["openblas"] / "include").mkdir()
            (prefixes["openblas"] / "include" / "cblas.h").write_text("")
            (prefixes["openblas"] / "lib").mkdir()
            (prefixes["openblas"] / "lib" / "libopenblas.so").write_text("")
            (prefixes["protobuf"] / "include" / "google" / "protobuf").mkdir(parents=True)
            (prefixes["protobuf"] / "include" / "google" / "protobuf" / "message.h").write_text("")
            (prefixes["protobuf"] / "bin").mkdir()
            protoc = prefixes["protobuf"] / "bin" / "protoc"
            protoc.write_text("#!/bin/sh\necho 'libprotoc 3.21.12'\n")
            os.chmod(protoc, 0o755)
            (prefixes["protobuf"] / "lib").mkdir()
            (prefixes["protobuf"] / "lib" / "libprotobuf.so").write_text("")
            (prefixes["cusparselt"] / "include").mkdir()
            (prefixes["cusparselt"] / "include" / "cusparseLt.h").write_text("")
            (prefixes["cusparselt"] / "lib64").mkdir()
            (prefixes["cusparselt"] / "lib64" / "libcusparseLt.so").write_text("")
            (prefixes["openmpi"] / "include").mkdir()
            (prefixes["openmpi"] / "include" / "mpi.h").write_text("")
            (prefixes["openmpi"] / "lib").mkdir()
            (prefixes["openmpi"] / "lib" / "libmpi.so").write_text("")
            (prefixes["cudss"] / "include").mkdir()
            (prefixes["cudss"] / "include" / "cudss.h").write_text("")
            (prefixes["cudss"] / "lib64").mkdir()
            (prefixes["cudss"] / "lib64" / "libcudss.so").write_text("")
            (prefixes["nvshmem"] / "include").mkdir()
            (prefixes["nvshmem"] / "include" / "nvshmem.h").write_text("")
            (prefixes["nvshmem"] / "include" / "non_abi").mkdir()
            (prefixes["nvshmem"] / "include" / "non_abi" / "nvshmem_version.h").write_text("")
            (prefixes["nvshmem"] / "lib64").mkdir()
            (prefixes["nvshmem"] / "lib64" / "libnvshmem_host.so").write_text("")
            (prefixes["numactl"] / "include").mkdir()
            (prefixes["numactl"] / "include" / "numa.h").write_text("")
            (prefixes["numactl"] / "lib").mkdir()
            (prefixes["numactl"] / "lib" / "libnuma.so").write_text("")
            (prefixes["cpuinfo"] / "include").mkdir()
            (prefixes["cpuinfo"] / "include" / "cpuinfo.h").write_text("")
            (prefixes["cpuinfo"] / "lib").mkdir()
            (prefixes["cpuinfo"] / "lib" / "libcpuinfo.so").write_text("")
            (prefixes["fp16"] / "include").mkdir()
            (prefixes["fp16"] / "include" / "fp16.h").write_text("")
            (prefixes["fxdiv"] / "include").mkdir()
            (prefixes["fxdiv"] / "include" / "fxdiv.h").write_text("")
            (prefixes["psimd"] / "include").mkdir()
            (prefixes["psimd"] / "include" / "psimd.h").write_text("")
            (prefixes["pthreadpool"] / "include").mkdir()
            (prefixes["pthreadpool"] / "include" / "pthreadpool.h").write_text("")
            (prefixes["pthreadpool"] / "lib").mkdir()
            (prefixes["pthreadpool"] / "lib" / "libpthreadpool.a").write_text("")
            site_packages = FIXTURE_SITE_PACKAGES

            def python_package_prefix(name: str, package_path: str) -> None:
                package = prefixes[name] / site_packages / package_path
                if package.suffix == ".py":
                    package.parent.mkdir(parents=True, exist_ok=True)
                    package.write_text("", encoding="utf-8")
                else:
                    package.mkdir(parents=True, exist_ok=True)
                (prefixes[name] / "bin").mkdir(exist_ok=True)

            python_package_prefix("py-pip", "pip")
            (prefixes["py-pip"] / "bin" / "pip").write_text("#!/bin/sh\n")
            python_package_prefix("py-setuptools", "setuptools")
            python_package_prefix("py-wheel", "wheel")
            (prefixes["py-wheel"] / "bin" / "wheel").write_text("#!/bin/sh\n")
            python_package_prefix("py-scikit-build-core", "scikit_build_core")
            scikit_dist = (
                prefixes["py-scikit-build-core"] /
                site_packages /
                f"scikit_build_core-{scikit_build_core_version}.dist-info"
            )
            scikit_dist.mkdir(parents=True)
            (scikit_dist / "METADATA").write_text(
                f"Name: scikit-build-core\nVersion: {scikit_build_core_version}\n",
                encoding="utf-8",
            )
            python_package_prefix("py-numpy", "numpy")
            python_package_prefix("py-pyyaml", "yaml")
            python_package_prefix("py-typing-extensions", "typing_extensions.py")
            python_package_prefix("py-six", "six.py")
            python_package_prefix("py-packaging", "packaging")
            python_package_prefix("py-pathspec", "pathspec")
            python_package_prefix("py-protobuf", "google/protobuf")
        return {k: str(v) for k, v in prefixes.items()}

    def write_source_manifest(self, root: Path, missing_path: str | None = None) -> Path:
        def rows(policy: str, dependencies: dict[str, dict[str, object]]) -> list[dict[str, object]]:
            checks = []
            for index, (name, spec) in enumerate(dependencies.items(), 1):
                checks.append({
                    "name": name,
                    "policy": policy,
                    "required_env": list(spec["required_env"]),
                    "paths": [
                        {
                            "path": path,
                            "policy": policy,
                            "gitlink_commit": format(index, "040x"),
                            "status_line": f" {format(index, '040x')} {path}",
                        }
                        for path in spec["paths"]
                    ],
                })
            return checks

        manifest = {
            "schema_version": 1,
            "full_feature_dependency_cross_checks": (
                rows("vendored", plan.VENDORED_SOURCE_DEPENDENCIES) +
                rows("system-provider", plan.SYSTEM_SOURCE_DEPENDENCIES)
            ),
        }
        if missing_path is not None:
            for entry in manifest["full_feature_dependency_cross_checks"]:
                entry["paths"] = [
                    path
                    for path in entry["paths"]
                    if path["path"] != missing_path
                ]
        manifest_path = root / "pytorch-source-manifest.json"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        return manifest_path

    def run_plan(self, prefixes: dict[str, str], *extra: str) -> tuple[int, dict]:
        with temporary_directory() as tmp:
            out = Path(tmp) / "plan.json"
            argv: list[str] = [
                "--out", str(out),
                "--rootfs-cuda-bundle",
                "--build-version", "2.10.0+training.serving",
            ]
            for key, value in prefixes.items():
                argv += ["--prefix", f"{key}={value}"]
            argv.extend(extra)
            stdout = io.StringIO()
            stderr = io.StringIO()
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                rc = plan.main(argv)
            return rc, json.loads(out.read_text())

    def test_complete_dry_run_emits_exact_build_interface_without_building(self) -> None:
        with temporary_directory() as tmp:
            prefixes = self.make_prefixes(Path(tmp))
            rc, doc = self.run_plan(prefixes)

        env = doc["build_env"]
        self.assertEqual(rc, 0)
        self.assertTrue(doc["preflight_ok"])
        self.assertEqual(doc["mode"], "dry-run")
        self.assertFalse(doc["will_build"])
        self.assertEqual(
            doc["entrypoint"],
            [
                "python",
                "-m",
                "pip",
                "wheel",
                "--no-build-isolation",
                "--no-deps",
                "-w",
                "artifacts/wheels",
                "<source>",
            ],
        )
        python_tag = "python" + ".".join(("3", "13"))
        self.assertEqual(
            doc["emitted_prefix_layout"]["site_packages"],
            f"lib/{python_tag}/site-packages/torch/{{lib,include}}",
        )
        self.assertEqual(env["USE_CUDA"], "1")
        self.assertEqual(env["USE_CUDNN"], "1")
        self.assertEqual(env["USE_NCCL"], "1")
        self.assertEqual(env["USE_DISTRIBUTED"], "1")
        self.assertEqual(env["USE_SYSTEM_NCCL"], "1")
        self.assertEqual(env["USE_STATIC_NCCL"], "0")
        self.assertEqual(env["USE_GLOO"], "1")
        self.assertEqual(env["USE_MPI"], "1")
        self.assertEqual(env["USE_TENSORPIPE"], "1")
        self.assertEqual(env["USE_RPC"], "1")
        self.assertEqual(env["USE_MKLDNN"], "0")
        self.assertEqual(env["USE_MAGMA"], "0")
        self.assertEqual(env["USE_CUSPARSELT"], "1")
        self.assertEqual(env["USE_CUDSS"], "1")
        self.assertEqual(env["USE_NVSHMEM"], "1")
        self.assertEqual(env["USE_FLASH_ATTENTION"], "1")
        self.assertEqual(env["USE_MEM_EFF_ATTENTION"], "1")
        self.assertEqual(env["USE_KINETO"], "1")
        self.assertEqual(env["USE_FBGEMM"], "1")
        self.assertEqual(env["USE_QNNPACK"], "1")
        self.assertEqual(env["USE_XNNPACK"], "1")
        self.assertEqual(env["USE_NNPACK"], "1")
        self.assertEqual(env["USE_SYSTEM_CPUINFO"], "1")
        self.assertNotIn("USE_SYSTEM_FP16", env)
        self.assertEqual(env["USE_SYSTEM_FXDIV"], "1")
        self.assertEqual(env["USE_SYSTEM_PSIMD"], "1")
        self.assertEqual(env["USE_SYSTEM_PTHREADPOOL"], "1")
        self.assertEqual(
            env["CMAKE_ARGS"],
            f"-DCPUINFO_SOURCE_DIR={prefixes['cpuinfo']} "
            f"-DPYTHON_SIX_SOURCE_DIR={prefixes['py-six']}/{FIXTURE_SITE_PACKAGES.as_posix()} "
            "-DPYTHON_PEACHPY_SOURCE_DIR=${PROJECT_SOURCE_DIR}/third_party/python-peachpy "
            f"-DPTHREADPOOL_SOURCE_DIR={prefixes['pthreadpool']}",
        )
        self.assertNotIn("USE_SYSTEM_LIBS", env)
        self.assertEqual(env["USE_OPENMP"], "1")
        self.assertEqual(env["BUILD_CUSTOM_PROTOBUF"], "OFF")
        self.assertEqual(env["ONNX_BUILD_CUSTOM_PROTOBUF"], "OFF")
        self.assertEqual(env["ONNX_USE_PROTOBUF_SHARED_LIBS"], "ON")
        self.assertEqual(env["USE_MKL"], "0")
        self.assertEqual(env["USE_TENSORRT"], "0")
        self.assertEqual(env["USE_XPU"], "0")
        self.assertEqual(env["USE_ROCM"], "0")
        self.assertEqual(env["USE_MPS"], "0")
        self.assertEqual(env["_GLIBCXX_USE_CXX11_ABI"], "1")
        self.assertEqual(env["TORCH_CUDA_ARCH_LIST"], "10.0")
        self.assertEqual(env["BLAS"], "OpenBLAS")
        self.assertEqual(env["OpenBLAS_HOME"], prefixes["openblas"])
        self.assertEqual(env["CUSPARSELT_ROOT"], prefixes["cusparselt"])
        self.assertEqual(env["CUDSS_ROOT"], prefixes["cudss"])
        self.assertEqual(env["CUDSS_INCLUDE_DIR"], str(Path(prefixes["cudss"]) / "include"))
        self.assertEqual(env["CUDSS_LIBRARY"], str(Path(prefixes["cudss"]) / "lib64" / "libcudss.so"))
        self.assertEqual(env["NVSHMEM_HOME"], prefixes["nvshmem"])
        self.assertEqual(env["MPI_HOME"], prefixes["openmpi"])
        self.assertEqual(env["NUMA_ROOT"], prefixes["numactl"])
        self.assertEqual(env["PROTOBUF_PROTOC_EXECUTABLE"], str(Path(prefixes["protobuf"]) / "bin" / "protoc"))
        pythonpath = env["PYTHONPATH"].split(":")
        for provider in PYTHON_BUILD_PREFIXES:
            with self.subTest(pythonpath_provider=provider):
                self.assertIn(
                    str(Path(prefixes[provider]) / FIXTURE_SITE_PACKAGES),
                    pythonpath,
                )
        path_entries = env["PATH"].split(":")
        for provider in ("python", "cmake", "ninja", "cuda", "py-pip", "py-wheel"):
            with self.subTest(path_provider=provider):
                self.assertIn(str(Path(prefixes[provider]) / "bin"), path_entries)
        self.assertEqual(env["PYTHONNOUSERSITE"], "1")
        self.assertEqual(env["PIP_NO_INDEX"], "1")
        self.assertIn(prefixes["protobuf"], env["CMAKE_PREFIX_PATH"].split(":"))
        for forbidden in ("CFLAGS", "CXXFLAGS", "CMAKE", "CMAKE_MAKE_PROGRAM", "CC", "CXX"):
            self.assertNotIn(forbidden, env)
        self.assertEqual(
            doc["tool_inputs"],
            {
                "cmake": {
                    "kind": "build-tool",
                    "path": str(Path(prefixes["cmake"]) / "bin" / "cmake"),
                    "prefix": prefixes["cmake"],
                },
                "ninja": {
                    "kind": "build-tool",
                    "path": str(Path(prefixes["ninja"]) / "bin" / "ninja"),
                    "prefix": prefixes["ninja"],
                },
            },
        )
        self.assertNotIn("toolchain_requests", doc)
        self.assertEqual(doc["profile_optional_features"], {})
        self.assertEqual(
            doc["feature_decisions"]["magma"],
            {
                "status": "off-with-reason",
                "reason": "human decision 2026-09-30: MAGMA is dropped from native torch",
            },
        )
        self.assertEqual(
            doc["feature_decisions"]["mkldnn"],
            {
                "status": "off-with-reason",
                "reason": "human decision 2026-09-30: MKLDNN/oneDNN is dropped from native torch",
            },
        )
        checks = {item["name"]: item for item in doc["preflight"]}
        self.assertIn("source-policy:py-protobuf-recipe-provider", checks)
        self.assertTrue(checks["source-policy:py-protobuf-recipe-provider"]["ok"])
        self.assertIn("source-policy:onnx-protobuf-guardrail", checks)
        self.assertTrue(checks["source-policy:onnx-protobuf-guardrail"]["ok"])
        self.assertIn("ONNX_BUILD_CUSTOM_PROTOBUF=OFF", checks["source-policy:onnx-protobuf-guardrail"]["detail"])
        self.assertIn("source-policy:py-protobuf-native-prefix", checks)
        self.assertTrue(checks["source-policy:py-protobuf-native-prefix"]["ok"])
        self.assertEqual(doc["source_dependency_policy"]["pytorch_release"], "v2.14.0")
        self.assertEqual(doc["source_dependency_policy"]["protobuf"]["spack_key"], "protobuf@21.12")
        self.assertEqual(doc["source_dependency_policy"]["protobuf"]["protoc_version"], "libprotoc 3.21.12")
        self.assertFalse(doc["source_dependency_policy"]["grpc"]["direct_pytorch_input"])
        self.assertFalse(doc["source_dependency_policy"]["abseil"]["direct_pytorch_input"])
        self.assertFalse(doc["source_dependency_policy"]["boost"]["direct_pytorch_input"])
        self.assertEqual(
            doc["source_dependency_policy"]["wheel_frontend"]["entrypoint"],
            [
                "python",
                "-m",
                "pip",
                "wheel",
                "--no-build-isolation",
                "--no-deps",
                "-w",
                "artifacts/wheels",
                "<source>",
            ],
        )
        self.assertEqual(
            sorted(env["CMAKE_PREFIX_PATH"].split(":")),
            sorted([
                prefixes["cmake"],
                prefixes["cusparselt"],
                prefixes["cudss"],
                prefixes["nvshmem"],
                prefixes["openblas"],
                prefixes["openmpi"],
                prefixes["protobuf"],
                prefixes["python"],
                prefixes["numactl"],
                *[prefixes[provider] for provider in NATIVE_HELPER_PREFIXES],
            ]),
        )

    def test_system_provider_preflight_requires_specific_headers_and_libraries(self) -> None:
        cases = (
            ("cuda", "bin/nvcc", "provider:cuda:binary:nvcc"),
            ("cudnn", "include/cudnn.h", "provider:cudnn:header"),
            ("cudnn", "include/cudnn_version.h", "provider:cudnn:header"),
            ("cudnn", "lib64/libcudnn.so", "provider:cudnn:library"),
            ("nccl", "include/nccl.h", "provider:nccl:header"),
            ("nccl", "lib/libnccl.so", "provider:nccl:library"),
            ("openblas", "include/cblas.h", "provider:openblas:header"),
            ("openblas", "lib/libopenblas.so", "provider:openblas:library"),
            ("protobuf", "include/google/protobuf/message.h", "provider:protobuf:header"),
            ("protobuf", "bin/protoc", "provider:protobuf:binary:protoc"),
            ("protobuf", "lib/libprotobuf.so", "provider:protobuf:library"),
            ("cusparselt", "include/cusparseLt.h", "provider:cusparselt:header"),
            ("cusparselt", "lib64/libcusparseLt.so", "provider:cusparselt:library"),
            ("cudss", "include/cudss.h", "provider:cudss:header"),
            ("cudss", "lib64/libcudss.so", "provider:cudss:library"),
            ("nvshmem", "include/nvshmem.h", "provider:nvshmem:header"),
            ("nvshmem", "include/non_abi/nvshmem_version.h", "provider:nvshmem:header"),
            ("nvshmem", "lib64/libnvshmem_host.so", "provider:nvshmem:library"),
            ("openmpi", "include/mpi.h", "provider:openmpi:header"),
            ("openmpi", "lib/libmpi.so", "provider:openmpi:library"),
            ("numactl", "include/numa.h", "provider:numactl:header"),
            ("numactl", "lib/libnuma.so", "provider:numactl:library"),
            ("cpuinfo", "include/cpuinfo.h", "provider:cpuinfo:header"),
            ("cpuinfo", "lib/libcpuinfo.so", "provider:cpuinfo:library"),
            ("fp16", "include/fp16.h", "provider:fp16:header"),
            ("fxdiv", "include/fxdiv.h", "provider:fxdiv:header"),
            ("pthreadpool", "include/pthreadpool.h", "provider:pthreadpool:header"),
            ("pthreadpool", "lib/libpthreadpool.a", "provider:pthreadpool:library"),
            ("psimd", "include/psimd.h", "provider:psimd:header"),
        )
        for provider, missing_rel, check_name in cases:
            with self.subTest(provider=provider, missing_rel=missing_rel), temporary_directory() as tmp:
                prefixes = self.make_prefixes(Path(tmp))
                (Path(prefixes[provider]) / missing_rel).unlink()
                rc, doc = self.run_plan(
                    prefixes,
                    "--execute",
                    "--token",
                    plan.REQUIRED_TOKEN,
                )

            checks = {item["name"]: item for item in doc["preflight"]}
            self.assertEqual(rc, 3)
            self.assertFalse(doc["preflight_ok"])
            self.assertFalse(doc["will_build"])
            self.assertIn(check_name, checks)
            self.assertFalse(checks[check_name]["ok"])
            self.assertIn(provider, checks[check_name]["detail"])

    def test_provider_preflight_resolves_library_dir_passed_to_build(self) -> None:
        with temporary_directory() as tmp:
            prefixes = self.make_prefixes(Path(tmp))
            (Path(prefixes["cudnn"]) / "lib64" / "libcudnn.so").unlink()
            (Path(prefixes["cudnn"]) / "lib").mkdir()
            (Path(prefixes["cudnn"]) / "lib" / "libcudnn.so").write_text("")
            (Path(prefixes["nccl"]) / "lib64").mkdir()
            rc, doc = self.run_plan(prefixes)

        self.assertEqual(rc, 0)
        env = doc["build_env"]
        checks = {item["name"]: item for item in doc["preflight"]}
        self.assertEqual(env["CUDNN_LIBRARY"], str(Path(prefixes["cudnn"]) / "lib"))
        self.assertIn("lib_dir=" + env["CUDNN_LIBRARY"], checks["provider:cudnn:lib-dir"]["detail"])
        self.assertEqual(env["NCCL_LIB_DIR"], str(Path(prefixes["nccl"]) / "lib"))
        self.assertIn("lib_dir=" + env["NCCL_LIB_DIR"], checks["provider:nccl:lib-dir"]["detail"])

    def test_python_build_requirement_preflight_uses_composed_pythonpath(self) -> None:
        for provider, module_path in plan.PYTHON_BUILD_PACKAGE_CHECKS.items():
            with self.subTest(provider=provider, module_path=module_path), temporary_directory() as tmp:
                prefixes = self.make_prefixes(Path(tmp))
                missing = Path(prefixes[provider]) / FIXTURE_SITE_PACKAGES / module_path
                if missing.is_dir():
                    missing.rmdir()
                else:
                    missing.unlink()
                rc, doc = self.run_plan(
                    prefixes,
                    "--execute",
                    "--token",
                    plan.REQUIRED_TOKEN,
                )

            check_name = f"python-build-requirement:{provider}"
            checks = {item["name"]: item for item in doc["preflight"]}
            self.assertEqual(rc, 3)
            self.assertFalse(doc["preflight_ok"])
            self.assertFalse(doc["will_build"])
            self.assertIn(check_name, checks)
            self.assertFalse(checks[check_name]["ok"])
            self.assertIn(provider, checks[check_name]["detail"])
            self.assertIn("PYTHONPATH", checks[check_name]["detail"])

    def test_source_manifest_preflight_requires_enabled_vendored_subdirs(self) -> None:
        build_flags = plan.selected_build_flags()
        cases = []
        for policy_name, dependencies in (
            ("vendored", plan.VENDORED_SOURCE_DEPENDENCIES),
            ("system-provider", plan.SYSTEM_SOURCE_DEPENDENCIES),
        ):
            for dependency, spec in dependencies.items():
                required_env = tuple(spec["required_env"])
                if not plan.required_env_matches(required_env, build_flags):
                    continue
                for rel in spec["paths"]:
                    cases.append((policy_name, dependency, rel))
        self.assertGreater(len(cases), 0)

        for policy_name, dependency, missing_path in cases:
            with self.subTest(dependency=dependency, missing_path=missing_path), temporary_directory() as tmp:
                manifest = self.write_source_manifest(Path(tmp), missing_path=missing_path)
                prefixes = self.make_prefixes(Path(tmp))
                rc, doc = self.run_plan(
                    prefixes,
                    "--source-manifest",
                    str(manifest),
                    "--execute",
                    "--token",
                    plan.REQUIRED_TOKEN,
                )

            check_name = f"source-manifest:{policy_name}:{dependency}"
            checks = {item["name"]: item for item in doc["preflight"]}
            self.assertEqual(rc, 3)
            self.assertFalse(doc["preflight_ok"])
            self.assertFalse(doc["will_build"])
            self.assertIn(check_name, checks)
            self.assertFalse(checks[check_name]["ok"])
            self.assertIn(dependency, checks[check_name]["detail"])
            self.assertIn(missing_path, checks[check_name]["detail"])

    def test_source_manifest_preflight_requires_manifest_file(self) -> None:
        with temporary_directory() as tmp:
            prefixes = self.make_prefixes(Path(tmp))
            missing_manifest = Path(tmp) / "missing-source-manifest.json"
            rc, doc = self.run_plan(
                prefixes,
                "--source-manifest",
                str(missing_manifest),
                "--execute",
                "--token",
                plan.REQUIRED_TOKEN,
            )

        checks = {item["name"]: item for item in doc["preflight"]}
        self.assertEqual(rc, 3)
        self.assertFalse(doc["preflight_ok"])
        self.assertFalse(doc["will_build"])
        self.assertIn("source-manifest:file", checks)
        self.assertFalse(checks["source-manifest:file"]["ok"])
        self.assertIn("missing-source-manifest.json", checks["source-manifest:file"]["detail"])

    def test_checked_in_source_manifest_covers_enabled_vendored_subdirs(self) -> None:
        with temporary_directory() as tmp:
            prefixes = self.make_prefixes(Path(tmp))
            rc, doc = self.run_plan(prefixes, "--source-manifest", str(SOURCE_MANIFEST))

        checks = {item["name"]: item for item in doc["preflight"]}
        self.assertEqual(rc, 0)
        self.assertTrue(doc["preflight_ok"])
        self.assertIn("source-manifest:vendored:NNPACK", checks)
        self.assertTrue(checks["source-manifest:vendored:NNPACK"]["ok"])
        self.assertIn("source-manifest:vendored:qnnpack", checks)
        self.assertTrue(checks["source-manifest:vendored:qnnpack"]["ok"])
        self.assertIn("source-manifest:system-provider:pthreadpool", checks)
        self.assertTrue(checks["source-manifest:system-provider:pthreadpool"]["ok"])

    def test_scikit_build_core_below_pytorch_minimum_fails_preflight(self) -> None:
        with temporary_directory() as tmp:
            prefixes = self.make_prefixes(
                Path(tmp),
                scikit_build_core_version="0.12.2",
            )
            rc, doc = self.run_plan(prefixes)

        checks = {item["name"]: item for item in doc["preflight"]}
        self.assertEqual(rc, 1)
        self.assertFalse(doc["preflight_ok"])
        self.assertIn("python-build-requirement:py-scikit-build-core", checks)
        self.assertFalse(checks["python-build-requirement:py-scikit-build-core"]["ok"])
        self.assertIn("requires >=1.0", checks["python-build-requirement:py-scikit-build-core"]["detail"])
        self.assertIn("found 0.12.2", checks["python-build-requirement:py-scikit-build-core"]["detail"])

    def test_scikit_build_core_prerelease_fails_stable_minimum_preflight(self) -> None:
        with temporary_directory() as tmp:
            prefixes = self.make_prefixes(
                Path(tmp),
                scikit_build_core_version="1.0rc1",
            )
            rc, doc = self.run_plan(prefixes)

        checks = {item["name"]: item for item in doc["preflight"]}
        self.assertEqual(rc, 1)
        self.assertFalse(doc["preflight_ok"])
        self.assertFalse(checks["python-build-requirement:py-scikit-build-core"]["ok"])
        self.assertIn("requires >=1.0", checks["python-build-requirement:py-scikit-build-core"]["detail"])
        self.assertIn("found 1.0rc1", checks["python-build-requirement:py-scikit-build-core"]["detail"])

    def test_action_driver_layers_python_build_frontend_prefixes(self) -> None:
        with temporary_directory() as tmp:
            prefixes = self.make_prefixes(Path(tmp))
            old_tmpdir = os.environ.get("TMPDIR")
            os.environ["TMPDIR"] = tmp
            try:
                env = action_driver._minimal_build_env(
                    {
                        "build_env": {
                            "SENTINEL": "from-plan",
                        },
                        "python_abi": "derived",
                    },
                    prefixes,
                    Path(tmp) / "pytorch-v2.14.0",
                )
            finally:
                if old_tmpdir is None:
                    del os.environ["TMPDIR"]
                else:
                    os.environ["TMPDIR"] = old_tmpdir

        self.assertEqual(env["SENTINEL"], "from-plan")
        self.assertIn("PYTHONNOUSERSITE", env)
        self.assertEqual(env["PYTHONNOUSERSITE"], "1")
        self.assertIn("PYTHONPATH", env)
        pythonpath = env["PYTHONPATH"].split(os.pathsep)
        self.assertEqual(
            pythonpath[:3],
            [
                str(Path(prefixes["py-pip"]) / FIXTURE_SITE_PACKAGES),
                str(Path(prefixes["py-setuptools"]) / FIXTURE_SITE_PACKAGES),
                str(Path(prefixes["py-wheel"]) / FIXTURE_SITE_PACKAGES),
            ],
        )
        self.assertIn(
            str(Path(prefixes["py-scikit-build-core"]) / FIXTURE_SITE_PACKAGES),
            pythonpath,
        )
        path_entries = env["PATH"].split(os.pathsep)
        self.assertEqual(path_entries[0], str(Path(prefixes["python"]) / "bin"))
        self.assertIn(str(Path(prefixes["py-pip"]) / "bin"), path_entries)

    def test_action_driver_resolves_project_source_dir_placeholders_in_build_env(self) -> None:
        with temporary_directory() as tmp:
            tmp_path = Path(tmp)
            prefixes = self.make_prefixes(tmp_path)
            source_dir = tmp_path / "work" / "pytorch-v2.14.0"
            old_tmpdir = os.environ.get("TMPDIR")
            os.environ["TMPDIR"] = tmp
            try:
                env = action_driver._minimal_build_env(
                    {
                        "build_env": {
                            "CMAKE_ARGS": (
                                "-DPYTHON_PEACHPY_SOURCE_DIR="
                                "${PROJECT_SOURCE_DIR}/third_party/python-peachpy"
                            ),
                        },
                        "python_abi": "derived",
                    },
                    prefixes,
                    source_dir,
                )
            finally:
                if old_tmpdir is None:
                    del os.environ["TMPDIR"]
                else:
                    os.environ["TMPDIR"] = old_tmpdir

        self.assertNotIn("${PROJECT_SOURCE_DIR}", env["CMAKE_ARGS"])
        self.assertIn(
            f"-DPYTHON_PEACHPY_SOURCE_DIR={source_dir}/third_party/python-peachpy",
            env["CMAKE_ARGS"],
        )

    def test_action_driver_build_uses_absolute_paths_after_chdir(self) -> None:
        with temporary_directory() as tmp:
            tmp_path = Path(tmp)
            prefixes = self.make_prefixes(tmp_path)
            python = Path(prefixes["python"]) / "bin" / "python3"
            source_dir = Path("relative-work") / "pytorch-v2.14.0"
            args = type(
                "Args",
                (),
                {
                    "prefix_out": str(Path("relative-out") / "pytorch_action_prefix"),
                },
            )()
            old_cwd = Path.cwd()
            old_tmpdir = os.environ.get("TMPDIR")
            calls = []

            def fake_run(cmd, **kwargs):
                if len(cmd) >= 3 and cmd[1] == "-c":
                    return subprocess.CompletedProcess(
                        cmd,
                        0,
                        stdout=FIXTURE_SITE_PACKAGES.as_posix() + "\n",
                    )
                calls.append((cmd, kwargs))
                if "wheel" in cmd:
                    wheel_dir = Path(cmd[cmd.index("-w") + 1])
                    wheel_dir.mkdir(parents=True, exist_ok=True)
                    (wheel_dir / f"torch-2.14.0-{FIXTURE_WHEEL_TAG}-linux_x86_64.whl").write_text(
                        "",
                        encoding="utf-8",
                    )
                return subprocess.CompletedProcess(cmd, 0)

            os.environ["TMPDIR"] = tmp
            os.chdir(tmp_path)
            try:
                original_run = action_driver.subprocess.run
                action_driver.subprocess.run = fake_run
                action_driver._execute_build(
                    args,
                    prefixes,
                    {
                        "build_env": {},
                        "python_abi": "derived",
                    },
                    source_dir,
                )
            finally:
                action_driver.subprocess.run = original_run
                os.chdir(old_cwd)
                if old_tmpdir is None:
                    del os.environ["TMPDIR"]
                else:
                    os.environ["TMPDIR"] = old_tmpdir

        self.assertEqual(len(calls), 2)
        wheel_cmd, wheel_kwargs = calls[0]
        install_cmd, install_kwargs = calls[1]
        self.assertEqual(wheel_cmd[:4], [str(python), "-m", "pip", "wheel"])
        self.assertTrue(Path(wheel_cmd[wheel_cmd.index("-w") + 1]).is_absolute())
        self.assertTrue(Path(wheel_cmd[-1]).is_absolute())
        self.assertEqual(Path(wheel_cmd[-1]), (tmp_path / source_dir).resolve(strict=False))
        self.assertEqual(Path(wheel_kwargs["cwd"]), (tmp_path / args.prefix_out).resolve(strict=False))
        self.assertTrue(Path(install_cmd[-1]).is_absolute())
        self.assertEqual(Path(install_kwargs["cwd"]), (tmp_path / args.prefix_out).resolve(strict=False))

    def test_action_driver_uses_estate_work_dir_keyed_by_inputs(self) -> None:
        with temporary_directory() as tmp:
            tmp_path = Path(tmp)
            vaso_home = tmp_path / "vaso"
            source_archive = tmp_path / "pytorch.tar.zst"
            source_archive.write_bytes(b"source-v1")
            source_anchor = tmp_path / "source" / "setup.py"
            source_anchor.parent.mkdir()
            source_anchor.write_text("", encoding="utf-8")
            zstd_prefix = tmp_path / "zstd-prefix.txt"
            zstd_prefix.write_text(str(tmp_path / "zstd"), encoding="utf-8")
            source_manifest = self.write_source_manifest(tmp_path)
            plan_file = tmp_path / "plan.py"
            plan_file.write_text("plan", encoding="utf-8")
            manifest = tmp_path / "rootfs-bundle.json"
            manifest.write_text('{"line":"cu130"}\n', encoding="utf-8")
            args = type(
                "Args",
                (),
                {
                    "build_work_root": "",
                    "build_version": "2.14.0",
                    "execute": True,
                    "max_jobs": "8",
                    "plan": str(plan_file),
                    "python_abi": "derived",
                    "rootfs_cuda_bundle": True,
                    "source_anchor": str(source_anchor),
                    "source_archive": str(source_archive),
                    "source_manifest": str(source_manifest),
                    "torch_cuda_arch_list": "10.0",
                    "zstd_prefix_file": str(zstd_prefix),
                },
            )()
            prefixes = self.make_prefixes(tmp_path)
            old_env = {
                key: os.environ.get(key)
                for key in ("VASO_HOME", "VASO_CUDA_LINE", "VASO_ROOTFS_BUNDLE_MANIFEST")
            }
            os.environ["VASO_HOME"] = str(vaso_home)
            os.environ["VASO_CUDA_LINE"] = "cu130"
            os.environ["VASO_ROOTFS_BUNDLE_MANIFEST"] = str(manifest)
            try:
                work_dir = action_driver._estate_build_work_dir(args, prefixes)
                again = action_driver._estate_build_work_dir(args, prefixes)
                source_archive.write_bytes(b"source-v2")
                changed = action_driver._estate_build_work_dir(args, prefixes)
            finally:
                for key, value in old_env.items():
                    if value is None:
                        os.environ.pop(key, None)
                    else:
                        os.environ[key] = value

        self.assertEqual(work_dir, again)
        self.assertNotEqual(work_dir, changed)
        self.assertEqual(work_dir.parent, vaso_home / "lines" / "cu130" / "work" / "pytorch")
        self.assertRegex(work_dir.name, r"^[0-9a-f]{32}$")
        self.assertNotIn("bazel-out", str(work_dir))

    def test_action_driver_leaves_per_run_pytorch_max_jobs_for_plan_environment(self) -> None:
        with temporary_directory() as tmp:
            tmp_path = Path(tmp)
            plan_out = tmp_path / "plan.json"
            source_manifest = self.write_source_manifest(tmp_path)
            args = type(
                "Args",
                (),
                {
                    "build_plan_out": str(plan_out),
                    "build_version": "2.14.0",
                    "execute": True,
                    "max_jobs": "",
                    "plan": "native/pytorch/plan.py",
                    "python_abi": "derived",
                    "rootfs_cuda_bundle": True,
                    "source_manifest": str(source_manifest),
                    "token": "build-native-pytorch",
                    "torch_cuda_arch_list": "10.0",
                },
            )()
            prefixes = {key: f"/prefix/{key}" for key in action_driver.PREFIX_KEYS}
            calls = []

            def fake_run(cmd, **kwargs):
                calls.append(cmd)
                self.assertNotIn("env", kwargs)
                plan_out.write_text("{}\n", encoding="utf-8")
                return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

            old_value = os.environ.get("VASO_PYTORCH_MAX_JOBS")
            os.environ["VASO_PYTORCH_MAX_JOBS"] = "72"
            original_run = action_driver.subprocess.run
            try:
                action_driver.subprocess.run = fake_run
                rc, _ = action_driver._run_plan(args, prefixes, tmp_path / "pytorch-v2.14.0")
            finally:
                action_driver.subprocess.run = original_run
                if old_value is None:
                    os.environ.pop("VASO_PYTORCH_MAX_JOBS", None)
                else:
                    os.environ["VASO_PYTORCH_MAX_JOBS"] = old_value

        self.assertEqual(rc, 0)
        self.assertNotIn("--max-jobs", calls[0])

    def test_action_driver_pins_home_and_pip_cache_under_tmpdir(self) -> None:
        with temporary_directory() as tmp:
            tmp_path = Path(tmp)
            prefixes = self.make_prefixes(tmp_path)
            old_tmpdir = os.environ.get("TMPDIR")
            os.environ["TMPDIR"] = tmp
            try:
                env = action_driver._minimal_build_env(
                    {
                        "build_env": {},
                        "python_abi": "derived",
                    },
                    prefixes,
                    tmp_path / "pytorch-v2.14.0",
                )
            finally:
                if old_tmpdir is None:
                    del os.environ["TMPDIR"]
                else:
                    os.environ["TMPDIR"] = old_tmpdir

            self.assertTrue((tmp_path / "pytorch-home").is_dir())
            self.assertTrue((tmp_path / "pip-cache").is_dir())

        self.assertEqual(Path(env["HOME"]), tmp_path / "pytorch-home")
        self.assertEqual(Path(env["PIP_CACHE_DIR"]), tmp_path / "pip-cache")

    def test_action_driver_drops_moved_cmake_build_cache_before_resume(self) -> None:
        with temporary_directory() as tmp:
            source_dir = Path(tmp) / "work" / "pytorch-v2.14.0"
            build_dir = source_dir / "build"
            build_dir.mkdir(parents=True)
            (build_dir / "object.o").write_text("stale", encoding="utf-8")
            (build_dir / "CMakeCache.txt").write_text(
                "CMAKE_HOME_DIRECTORY:INTERNAL=/old/bazel-out/pytorch_action_work/pytorch-v2.14.0\n",
                encoding="utf-8",
            )

            cleanups = action_driver._reset_stale_cmake_build_dir(source_dir)

            self.assertEqual(cleanups, ["build:CMakeCache-home-mismatch"])
            self.assertFalse(build_dir.exists())

    def test_action_driver_drops_non_writable_moved_cmake_build_cache(self) -> None:
        with temporary_directory() as tmp:
            source_dir = Path(tmp) / "work" / "pytorch-v2.14.0"
            nested = source_dir / "build" / "c10"
            nested.mkdir(parents=True)
            (source_dir / "build" / "CMakeCache.txt").write_text(
                "CMAKE_HOME_DIRECTORY:INTERNAL=/old/bazel-out/pytorch_action_work/pytorch-v2.14.0\n",
                encoding="utf-8",
            )
            (nested / "CTestTestfile.cmake").write_text("stale", encoding="utf-8")
            for path in (nested / "CTestTestfile.cmake", nested, source_dir / "build"):
                path.chmod(0o555)

            try:
                cleanups = action_driver._reset_stale_cmake_build_dir(source_dir)
            finally:
                for path in (nested / "CTestTestfile.cmake", nested, source_dir / "build"):
                    if path.exists():
                        path.chmod(0o755)

            self.assertEqual(cleanups, ["build:CMakeCache-home-mismatch"])
            self.assertFalse((source_dir / "build").exists())

    def test_action_driver_normalizes_empty_psimd_submodule_to_native_source_shim(self) -> None:
        with temporary_directory() as tmp:
            tmp_path = Path(tmp)
            source_dir = tmp_path / "pytorch-v2.14.0"
            (source_dir / "third_party" / "psimd").mkdir(parents=True)
            psimd_prefix = tmp_path / "psimd-prefix"
            (psimd_prefix / "include").mkdir(parents=True)
            (psimd_prefix / "include" / "psimd.h").write_text("", encoding="utf-8")

            normalized = action_driver._normalize_externalized_helper_sources(
                source_dir,
                {"psimd": str(psimd_prefix)},
            )

            cmake = source_dir / "third_party" / "psimd" / "CMakeLists.txt"
            self.assertEqual(normalized, ["third_party/psimd"])
            self.assertTrue(cmake.is_file())
            cmake_text = cmake.read_text(encoding="utf-8")
            self.assertIn("add_library(psimd INTERFACE)", cmake_text)
            self.assertIn(str(psimd_prefix / "include"), cmake_text)

    def test_action_driver_adds_pthreadpool_include_to_qnnpack_imported_target(self) -> None:
        with temporary_directory() as tmp:
            tmp_path = Path(tmp)
            source_dir = tmp_path / "pytorch-v2.14.0"
            (source_dir / "third_party" / "psimd").mkdir(parents=True)
            (source_dir / "third_party" / "psimd" / "CMakeLists.txt").write_text(
                "add_library(psimd INTERFACE)\n",
                encoding="utf-8",
            )
            qnnpack_dir = (
                source_dir /
                "aten" /
                "src" /
                "ATen" /
                "native" /
                "quantized" /
                "cpu" /
                "qnnpack"
            )
            qnnpack_dir.mkdir(parents=True)
            qnnpack_cmake = qnnpack_dir / "CMakeLists.txt"
            qnnpack_cmake.write_text(
                "\n".join([
                    "elseif(NOT TARGET pthreadpool AND USE_SYSTEM_PTHREADPOOL)",
                    "  add_library(pthreadpool SHARED IMPORTED)",
                    "  find_library(PTHREADPOOL_LIBRARY pthreadpool)",
                    "  if(NOT PTHREADPOOL_LIBRARY)",
                    "    message(FATAL_ERROR \"Cannot find pthreadpool\")",
                    "  endif()",
                    "  message(\"-- Found pthreadpool: ${PTHREADPOOL_LIBRARY}\")",
                    "  set_target_properties(pthreadpool PROPERTIES",
                    "    IMPORTED_LOCATION \"${PTHREADPOOL_LIBRARY}\")",
                    "  add_library(pthreadpool_interface INTERFACE)",
                    "endif()",
                    "target_link_libraries(pytorch_qnnpack PUBLIC pthreadpool)",
                    "",
                ]),
                encoding="utf-8",
            )
            pthreadpool_prefix = tmp_path / "pthreadpool-prefix"
            (pthreadpool_prefix / "include").mkdir(parents=True)
            (pthreadpool_prefix / "include" / "pthreadpool.h").write_text("", encoding="utf-8")

            normalized = action_driver._normalize_externalized_helper_sources(
                source_dir,
                {
                    "psimd": str(tmp_path / "psimd-prefix"),
                    "pthreadpool": str(pthreadpool_prefix),
                },
            )

            cmake_text = qnnpack_cmake.read_text(encoding="utf-8")
            self.assertEqual(normalized, ["aten/src/ATen/native/quantized/cpu/qnnpack"])
            self.assertIn(
                'INTERFACE_INCLUDE_DIRECTORIES "{}"'.format(pthreadpool_prefix / "include"),
                cmake_text,
            )

    def test_action_driver_adds_psimd_include_to_qnnpack_system_target(self) -> None:
        with temporary_directory() as tmp:
            tmp_path = Path(tmp)
            source_dir = tmp_path / "pytorch-v2.14.0"
            (source_dir / "third_party" / "psimd").mkdir(parents=True)
            (source_dir / "third_party" / "psimd" / "CMakeLists.txt").write_text(
                "add_library(psimd INTERFACE)\n",
                encoding="utf-8",
            )
            qnnpack_dir = (
                source_dir /
                "aten" /
                "src" /
                "ATen" /
                "native" /
                "quantized" /
                "cpu" /
                "qnnpack"
            )
            qnnpack_dir.mkdir(parents=True)
            qnnpack_cmake = qnnpack_dir / "CMakeLists.txt"
            qnnpack_cmake.write_text(
                "\n".join([
                    "elseif(NOT TARGET psimd AND USE_SYSTEM_PSIMD)",
                    "  find_file(PSIMD_HDR psimd.h PATH_SUFFIXES include)",
                    "  if(NOT PSIMD_HDR)",
                    "    message(FATAL_ERROR \"Cannot find psimd\")",
                    "  endif()",
                    "  add_library(psimd STATIC \"${PSIMD_HDR}\")",
                    "  set_property(TARGET psimd PROPERTY LINKER_LANGUAGE C)",
                    "endif()",
                    "target_link_libraries(pytorch_qnnpack PRIVATE psimd)",
                    "elseif(NOT TARGET pthreadpool AND USE_SYSTEM_PTHREADPOOL)",
                    "  add_library(pthreadpool SHARED IMPORTED)",
                    "  find_library(PTHREADPOOL_LIBRARY pthreadpool)",
                    "  if(NOT PTHREADPOOL_LIBRARY)",
                    "    message(FATAL_ERROR \"Cannot find pthreadpool\")",
                    "  endif()",
                    "  message(\"-- Found pthreadpool: ${PTHREADPOOL_LIBRARY}\")",
                    "  set_target_properties(pthreadpool PROPERTIES",
                    "    IMPORTED_LOCATION \"${PTHREADPOOL_LIBRARY}\")",
                    "  add_library(pthreadpool_interface INTERFACE)",
                    "endif()",
                    "target_link_libraries(pytorch_qnnpack PUBLIC pthreadpool)",
                    "",
                ]),
                encoding="utf-8",
            )
            psimd_prefix = tmp_path / "psimd-prefix"
            (psimd_prefix / "include").mkdir(parents=True)
            (psimd_prefix / "include" / "psimd.h").write_text("", encoding="utf-8")
            pthreadpool_prefix = tmp_path / "pthreadpool-prefix"
            (pthreadpool_prefix / "include").mkdir(parents=True)
            (pthreadpool_prefix / "include" / "pthreadpool.h").write_text("", encoding="utf-8")

            normalized = action_driver._normalize_externalized_helper_sources(
                source_dir,
                {
                    "psimd": str(psimd_prefix),
                    "pthreadpool": str(pthreadpool_prefix),
                },
            )

            cmake_text = qnnpack_cmake.read_text(encoding="utf-8")
            self.assertEqual(normalized, ["aten/src/ATen/native/quantized/cpu/qnnpack"])
            self.assertIn(
                'set_property(TARGET psimd PROPERTY INTERFACE_INCLUDE_DIRECTORIES "{}")'.format(
                    psimd_prefix / "include",
                ),
                cmake_text,
            )

    def test_action_driver_rewrites_nnpack_peachpy_pythonpath_launcher(self) -> None:
        with temporary_directory() as tmp:
            tmp_path = Path(tmp)
            source_dir = tmp_path / "pytorch-v2.14.0"
            (source_dir / "third_party" / "psimd").mkdir(parents=True)
            (source_dir / "third_party" / "psimd" / "CMakeLists.txt").write_text(
                "add_library(psimd INTERFACE)\n",
                encoding="utf-8",
            )
            nnpack_dir = source_dir / "third_party" / "NNPACK"
            nnpack_dir.mkdir(parents=True)
            nnpack_cmake = nnpack_dir / "CMakeLists.txt"
            nnpack_cmake.write_text(
                "\n".join([
                    'SET(PEACHPY_PYTHONPATH "${PYTHON_SIX_SOURCE_DIR}:${PYTHON_PEACHPY_SOURCE_DIR}")',
                    "ADD_CUSTOM_COMMAND(",
                    "  OUTPUT ${obj}",
                    '  COMMAND "PYTHONPATH=${PEACHPY_PYTHONPATH}"',
                    "    ${PYTHON_EXECUTABLE} -m peachpy.x86_64",
                    "      -mabi=sysv -g4 -mimage-format=${PEACHPY_IMAGE_FORMAT}",
                    ")",
                    "",
                ]),
                encoding="utf-8",
            )
            psimd_prefix = tmp_path / "psimd-prefix"
            (psimd_prefix / "include").mkdir(parents=True)
            (psimd_prefix / "include" / "psimd.h").write_text("", encoding="utf-8")
            pthreadpool_prefix = tmp_path / "pthreadpool-prefix"
            (pthreadpool_prefix / "include").mkdir(parents=True)
            (pthreadpool_prefix / "include" / "pthreadpool.h").write_text("", encoding="utf-8")

            normalized = action_driver._normalize_externalized_helper_sources(
                source_dir,
                {
                    "psimd": str(psimd_prefix),
                    "pthreadpool": str(pthreadpool_prefix),
                },
            )
            cmake_text = nnpack_cmake.read_text(encoding="utf-8")

        self.assertEqual(normalized, ["third_party/NNPACK/CMakeLists.txt"])
        self.assertNotIn('COMMAND "PYTHONPATH=${PEACHPY_PYTHONPATH}"', cmake_text)
        self.assertIn(
            'COMMAND "${CMAKE_COMMAND}" -E env "PYTHONPATH=${PEACHPY_PYTHONPATH}"',
            cmake_text,
        )

    def test_action_driver_adds_pthreadpool_include_to_top_level_imported_target(self) -> None:
        with temporary_directory() as tmp:
            tmp_path = Path(tmp)
            source_dir = tmp_path / "pytorch-v2.14.0"
            (source_dir / "third_party" / "psimd").mkdir(parents=True)
            (source_dir / "third_party" / "psimd" / "CMakeLists.txt").write_text(
                "add_library(psimd INTERFACE)\n",
                encoding="utf-8",
            )
            cmake_dir = source_dir / "cmake"
            cmake_dir.mkdir(parents=True)
            dependencies_cmake = cmake_dir / "Dependencies.cmake"
            dependencies_cmake.write_text(
                "\n".join([
                    "if(USE_SYSTEM_PTHREADPOOL)",
                    "  add_library(pthreadpool SHARED IMPORTED)",
                    "  find_library(PTHREADPOOL_LIBRARY pthreadpool)",
                    "  set_property(TARGET pthreadpool PROPERTY IMPORTED_LOCATION \"${PTHREADPOOL_LIBRARY}\")",
                    "  if(NOT PTHREADPOOL_LIBRARY)",
                    "    message(FATAL_ERROR \"Cannot find pthreadpool\")",
                    "  endif()",
                    "  message(\"-- Found pthreadpool: ${PTHREADPOOL_LIBRARY}\")",
                    "endif()",
                    "",
                ]),
                encoding="utf-8",
            )
            pthreadpool_prefix = tmp_path / "pthreadpool-prefix"
            (pthreadpool_prefix / "include").mkdir(parents=True)
            (pthreadpool_prefix / "include" / "pthreadpool.h").write_text("", encoding="utf-8")

            normalized = action_driver._normalize_externalized_helper_sources(
                source_dir,
                {
                    "psimd": str(tmp_path / "psimd-prefix"),
                    "pthreadpool": str(pthreadpool_prefix),
                },
            )

            cmake_text = dependencies_cmake.read_text(encoding="utf-8")
            self.assertEqual(normalized, ["cmake/Dependencies.cmake"])
            self.assertIn(
                'set_property(TARGET pthreadpool PROPERTY INTERFACE_INCLUDE_DIRECTORIES "${PTHREADPOOL_SOURCE_DIR}/include")',
                cmake_text,
            )

    def test_action_driver_adds_cpuinfo_include_to_top_level_imported_target(self) -> None:
        with temporary_directory() as tmp:
            tmp_path = Path(tmp)
            source_dir = tmp_path / "pytorch-v2.14.0"
            (source_dir / "third_party" / "psimd").mkdir(parents=True)
            (source_dir / "third_party" / "psimd" / "CMakeLists.txt").write_text(
                "add_library(psimd INTERFACE)\n",
                encoding="utf-8",
            )
            cmake_dir = source_dir / "cmake"
            cmake_dir.mkdir(parents=True)
            dependencies_cmake = cmake_dir / "Dependencies.cmake"
            dependencies_cmake.write_text(
                "\n".join([
                    "if(NOT TARGET cpuinfo AND USE_SYSTEM_CPUINFO)",
                    "  add_library(cpuinfo SHARED IMPORTED)",
                    "  find_library(CPUINFO_LIBRARY cpuinfo)",
                    "  if(NOT CPUINFO_LIBRARY)",
                    "    message(FATAL_ERROR \"Cannot find cpuinfo\")",
                    "  endif()",
                    "  message(\"Found cpuinfo: ${CPUINFO_LIBRARY}\")",
                    "  set_target_properties(cpuinfo PROPERTIES IMPORTED_LOCATION \"${CPUINFO_LIBRARY}\")",
                    "endif()",
                    "",
                ]),
                encoding="utf-8",
            )
            cpuinfo_prefix = tmp_path / "cpuinfo-prefix"
            (cpuinfo_prefix / "include").mkdir(parents=True)
            (cpuinfo_prefix / "include" / "cpuinfo.h").write_text("", encoding="utf-8")

            normalized = action_driver._normalize_externalized_helper_sources(
                source_dir,
                {
                    "cpuinfo": str(cpuinfo_prefix),
                    "psimd": str(tmp_path / "psimd-prefix"),
                    "pthreadpool": str(tmp_path / "pthreadpool-prefix"),
                },
            )

            cmake_text = dependencies_cmake.read_text(encoding="utf-8")
            self.assertEqual(normalized, ["cmake/Dependencies.cmake"])
            self.assertIn(
                'set_property(TARGET cpuinfo PROPERTY INTERFACE_INCLUDE_DIRECTORIES "${CPUINFO_SOURCE_DIR}/include")',
                cmake_text,
            )

    def test_magma_prefix_is_not_part_of_the_torch_provider_contract(self) -> None:
        with temporary_directory() as tmp:
            prefixes = self.make_prefixes(Path(tmp))
            rc, doc = self.run_plan(prefixes)

        self.assertEqual(rc, 0)
        self.assertTrue(doc["preflight_ok"])
        self.assertNotIn("magma", doc["input_prefixes"])
        self.assertNotIn("MAGMA_HOME", doc["build_env"])

    def test_missing_native_cuda_auxiliary_providers_fail_loudly(self) -> None:
        for provider in ("cudss", "nvshmem"):
            with self.subTest(provider=provider), temporary_directory() as tmp:
                prefixes = self.make_prefixes(Path(tmp))
                del prefixes[provider]
                rc, doc = self.run_plan(prefixes)

            self.assertEqual(rc, 1)
            self.assertFalse(doc["preflight_ok"])
            self.assertEqual(doc["build_env"], {})
            self.assertIn(f"missing required --prefix {provider}=... input", doc["env_error"])

    def test_ptx_arch_requires_probe7_flag(self) -> None:
        with temporary_directory() as tmp:
            prefixes = self.make_prefixes(Path(tmp))
            rc, doc = self.run_plan(prefixes, "--torch-cuda-arch-list", "10.0+PTX")

        self.assertEqual(rc, 1)
        self.assertFalse(doc["preflight_ok"])
        self.assertIn("probe 7", doc["env_error"])

        with temporary_directory() as tmp:
            prefixes = self.make_prefixes(Path(tmp))
            rc, doc = self.run_plan(
                prefixes,
                "--torch-cuda-arch-list",
                "10.0+PTX",
                "--cuda-arch-ptx-supported",
            )

        self.assertEqual(rc, 0)
        self.assertEqual(doc["build_env"]["TORCH_CUDA_ARCH_LIST"], "10.0+PTX")

    def test_max_jobs_defaults_from_nproc_cap_and_can_be_overridden_per_run(self) -> None:
        original_detect_nproc = plan.detect_nproc
        old_max_jobs = os.environ.get("MAX_JOBS")
        old_pytorch_max_jobs = os.environ.get("VASO_PYTORCH_MAX_JOBS")
        try:
            os.environ["MAX_JOBS"] = "99"
            os.environ.pop("VASO_PYTORCH_MAX_JOBS", None)
            with temporary_directory() as tmp:
                prefixes = self.make_prefixes(Path(tmp))
                plan.detect_nproc = lambda: 216
                rc, doc = self.run_plan(prefixes)

            self.assertEqual(rc, 0)
            self.assertEqual(doc["build_env"]["MAX_JOBS"], "96")
            self.assertEqual(doc["build_env"]["CMAKE_BUILD_PARALLEL_LEVEL"], "96")
            self.assertEqual(doc["resources"], {"max_jobs": "96", "source": "nproc capped at 96"})

            with temporary_directory() as tmp:
                prefixes = self.make_prefixes(Path(tmp))
                os.environ["VASO_PYTORCH_MAX_JOBS"] = "48"
                plan.detect_nproc = lambda: 216
                rc, doc = self.run_plan(prefixes)

            self.assertEqual(rc, 0)
            self.assertEqual(doc["build_env"]["MAX_JOBS"], "48")
            self.assertEqual(doc["build_env"]["CMAKE_BUILD_PARALLEL_LEVEL"], "48")
            self.assertEqual(doc["resources"], {"max_jobs": "48", "source": "VASO_PYTORCH_MAX_JOBS"})

            with temporary_directory() as tmp:
                prefixes = self.make_prefixes(Path(tmp))
                rc, doc = self.run_plan(prefixes, "--max-jobs", "6")
        finally:
            if old_max_jobs is None:
                os.environ.pop("MAX_JOBS", None)
            else:
                os.environ["MAX_JOBS"] = old_max_jobs
            if old_pytorch_max_jobs is None:
                os.environ.pop("VASO_PYTORCH_MAX_JOBS", None)
            else:
                os.environ["VASO_PYTORCH_MAX_JOBS"] = old_pytorch_max_jobs
            plan.detect_nproc = original_detect_nproc

        self.assertEqual(rc, 0)
        self.assertEqual(doc["build_env"]["MAX_JOBS"], "6")
        self.assertEqual(doc["build_env"]["CMAKE_BUILD_PARALLEL_LEVEL"], "6")
        self.assertEqual(doc["resources"], {"max_jobs": "6", "source": "explicit --max-jobs input"})

    def test_max_jobs_rejects_values_above_scheduler_cap(self) -> None:
        with temporary_directory() as tmp:
            prefixes = self.make_prefixes(Path(tmp))
            rc, doc = self.run_plan(prefixes, "--max-jobs", "97")

        self.assertEqual(rc, 1)
        self.assertFalse(doc["preflight_ok"])
        self.assertIn("--max-jobs must be <= 96", doc["env_error"])
        self.assertEqual(doc["resources"], {"max_jobs": None, "source": None})

        old_pytorch_max_jobs = os.environ.get("VASO_PYTORCH_MAX_JOBS")
        try:
            os.environ["VASO_PYTORCH_MAX_JOBS"] = "97"
            with temporary_directory() as tmp:
                prefixes = self.make_prefixes(Path(tmp))
                rc, doc = self.run_plan(prefixes)
        finally:
            if old_pytorch_max_jobs is None:
                os.environ.pop("VASO_PYTORCH_MAX_JOBS", None)
            else:
                os.environ["VASO_PYTORCH_MAX_JOBS"] = old_pytorch_max_jobs

        self.assertEqual(rc, 1)
        self.assertFalse(doc["preflight_ok"])
        self.assertIn("VASO_PYTORCH_MAX_JOBS must be <= 96", doc["env_error"])
        self.assertEqual(doc["resources"], {"max_jobs": None, "source": None})

    def test_caller_toolchain_environment_does_not_change_plan(self) -> None:
        with temporary_directory() as tmp:
            prefixes = self.make_prefixes(Path(tmp))
            rc, baseline = self.run_plan(prefixes)
            old_cc = os.environ.get("CC")
            old_cflags = os.environ.get("CFLAGS")
            os.environ["CC"] = "clang"
            os.environ["CFLAGS"] = "-O0"
            try:
                polluted_rc, polluted = self.run_plan(prefixes)
            finally:
                if old_cc is None:
                    del os.environ["CC"]
                else:
                    os.environ["CC"] = old_cc
                if old_cflags is None:
                    del os.environ["CFLAGS"]
                else:
                    os.environ["CFLAGS"] = old_cflags

        self.assertEqual(rc, 0)
        self.assertEqual(polluted_rc, 0)
        self.assertEqual(polluted["build_env"], baseline["build_env"])
        self.assertEqual(polluted["tool_inputs"], baseline["tool_inputs"])
        self.assertEqual(polluted["feature_decisions"], baseline["feature_decisions"])

    def test_source_path_is_the_pip_wheel_source_argument(self) -> None:
        with temporary_directory() as tmp:
            prefixes = self.make_prefixes(Path(tmp))
            source = str(Path(tmp) / "pytorch-src")
            rc, doc = self.run_plan(prefixes, "--source", source)

        self.assertEqual(rc, 0)
        self.assertEqual(
            doc["entrypoint"],
            [
                "python",
                "-m",
                "pip",
                "wheel",
                "--no-build-isolation",
                "--no-deps",
                "-w",
                "artifacts/wheels",
                source,
            ],
        )

    def test_build_system_requirements_have_native_providers_or_known_gaps(self) -> None:
        graph = self.load_json(PY_TORCH_LEAN_GRAPH)
        overrides = self.load_json(NATIVE_OVERRIDES)
        closure = self.py_torch_closure(graph)
        native_keys = set(overrides["native"])
        provided_versions = overrides["provided_versions"]

        unexpected_gaps: list[str] = []
        observed_known_gaps: set[str] = set()
        for requirement, (provider_key, specifier) in BUILD_SYSTEM_REQUIREMENTS.items():
            node = closure.get(provider_key)
            problem = None
            if node is None:
                problem = "missing from py-torch graph closure"
            elif provider_key not in native_keys:
                problem = f"missing native override for graph version {node['version']}"
            else:
                try:
                    self.assert_satisfies(provided_versions[provider_key], specifier)
                except AssertionError:
                    problem = (
                        f"native version {provided_versions[provider_key]} "
                        f"does not satisfy {specifier}"
                    )

            if problem is None:
                self.assertNotIn(
                    provider_key,
                    KNOWN_BUILD_REQUIREMENT_GAPS,
                    f"{provider_key} no longer has the ticket 04 gap recorded for {requirement}",
                )
            elif provider_key in KNOWN_BUILD_REQUIREMENT_GAPS:
                observed_known_gaps.add(provider_key)
            else:
                unexpected_gaps.append(f"{requirement}: {provider_key} {problem}")

        self.assertEqual([], unexpected_gaps)
        self.assertEqual(set(KNOWN_BUILD_REQUIREMENT_GAPS), observed_known_gaps)

    def test_d1_full_feature_graph_keeps_cuda_frontier_open(self) -> None:
        graph = self.load_json(PY_TORCH_214_FULL_GRAPH)
        closure = self.py_torch_closure(graph)
        root = next(node for node in graph["nodes"] if node["package"] == graph["root"])
        nodes_by_hash = {node["spack_hash"]: node for node in graph["nodes"]}
        by_package = {node["package"]: node for node in graph["nodes"]}

        self.assertEqual(root["package"], "py-torch")
        self.assertEqual(root["version"], "2.14.0")
        root_params = root["parameters"]
        for enabled in (
            "cuda",
            "cudnn",
            "distributed",
            "nccl",
            "gloo",
            "mpi",
            "tensorpipe",
            "openmp",
            "cusparselt",
            "flash_attention",
            "kineto",
            "fbgemm",
            "qnnpack",
            "xnnpack",
        ):
            with self.subTest(enabled=enabled):
                self.assertIs(root_params[enabled], True)
        self.assertIs(root_params["mkldnn"], False)
        self.assertEqual(root_params["cuda_arch"], ["100"])

        for package in (
            "cuda",
            "cudnn",
            "nccl",
            "cudss",
            "nvshmem",
            "cusparselt",
        ):
            with self.subTest(cuda_frontier_package=package):
                self.assertIn(package, closure)

        expected_versions = {
            "protobuf": "21.12",
            "py-protobuf": "4.21.12",
            "openblas": "0.3.33",
        }
        for package, version in expected_versions.items():
            with self.subTest(package=package):
                self.assertIn(package, closure)
                self.assertEqual(closure[package]["version"], version)
        root_python_deps = [
            nodes_by_hash[dep["hash"]]
            for dep in root["deps"]
            if dep["name"] == "python" and {"link", "run"}.issubset(set(dep["deptypes"]))
        ]
        self.assertEqual([node["version"] for node in root_python_deps], ["3.13.13"])
        self.assertNotIn("grpc", by_package)
        self.assertNotIn("grpc-cpp", by_package)
        self.assertNotIn("abseil-cpp", by_package)

    def test_source_dependency_policy_records_upstream_odr_inputs(self) -> None:
        policy = plan.SOURCE_DEPENDENCY_POLICY

        self.assertEqual(policy["pytorch_release"], "v2.14.0")
        self.assertEqual(
            policy["pytorch_commit"],
            "2b3ec34829036a65cd9d1398ea72a0167dc37470",
        )
        self.assertEqual(
            policy["protobuf"]["pytorch_gitlink"],
            "f0dc78d7e6e331b8c6bb2d5283e06aa26883ca7c",
        )
        self.assertEqual(policy["protobuf"]["upstream_tag"], "v3.21.12")
        self.assertEqual(policy["protobuf"]["upstream_spack_style_tag"], "v21.12")
        self.assertEqual(policy["protobuf"]["spack_key"], "protobuf@21.12")
        self.assertEqual(policy["protobuf"]["python_spack_key"], "py-protobuf@4.21.12")
        self.assertEqual(policy["protobuf"]["selected_family"], "protobuf C++ 3.21.12 / Python 4.21.12")
        self.assertEqual(policy["protobuf"]["hermetic_spack_recipe_default"], "protobuf@3.13.0 + py-protobuf@3.13")
        self.assertEqual(policy["protobuf"]["hermetic_spack_python_provider_status"], "available-via-vaso-overlay")
        self.assertEqual(policy["protobuf"]["hermetic_spack_python_provider_namespace"], "vaso_overlay")
        self.assertEqual(
            policy["protobuf"]["hermetic_spack_python_provider_recipe"],
            "spack_overlays/vaso/spack_repo/vaso_overlay/packages/py_protobuf/package.py",
        )
        self.assertEqual(policy["protobuf"]["native_python_provider_status"], "available")
        self.assertIn("@py_protobuf_native", policy["protobuf"]["native_python_provider_detail"])
        self.assertEqual(policy["protobuf"]["source_authority"], "PyTorch v2.14.0 third_party/protobuf gitlink")
        self.assertEqual(
            policy["wheel_frontend"]["source"],
            "pyproject.toml build-backend=scikit_build_core.build",
        )
        self.assertIsNone(policy["grpc"]["native_provider"])
        self.assertEqual(policy["grpc"]["source_authority"], "PyTorch v2.14.0 .gitmodules and CMake files")
        self.assertEqual(policy["grpc"]["reason"], "not present as a PyTorch v2.14.0 submodule or CMake dependency")
        self.assertEqual(
            policy["abseil"]["onnx_gitlink"],
            "e709452ef2bbc1d113faf678c24e6d3467696e83",
        )
        self.assertEqual(policy["abseil"]["fallback_protobuf"], "29.2")
        self.assertEqual(policy["abseil"]["fallback_protobuf_cmake_version"], "5.29.2")
        self.assertEqual(policy["abseil"]["fallback_abseil"], "20240722.1")
        self.assertEqual(
            policy["abseil"]["fallback_abseil_sha1"],
            "0d6b07c6f3352981d3660978e109f2bc14594a3d",
        )
        self.assertEqual(
            policy["abseil"]["fallback_protobuf_sha1"],
            "a5639ffb17e3743d696baf16bf377fbe752b6a1f",
        )
        self.assertEqual(policy["abseil"]["system_abseil_condition"], "ONNX external protobuf >= 4.22.0")
        self.assertIsNone(policy["abseil"]["native_provider"])
        self.assertEqual(policy["abseil"]["source_authority"], "ONNX v1.18.0 CMakeLists.txt")
        self.assertEqual(policy["boost"]["native_provider"], "boost@1.90.0")
        self.assertEqual(policy["boost"]["native_provider_scope"], "spack-graph-node-only")
        self.assertEqual(policy["boost"]["source_authority"], "PyTorch v2.14.0 .gitmodules and CMake files")
        self.assertEqual(policy["boost"]["reason"], "not present as a PyTorch v2.14.0 submodule or CMake dependency")
        self.assertIn("protobuf", policy["accepted_prefix_keys"])
        self.assertIn("grpc", policy["rejected_odr_prefix_keys"])
        self.assertIn("abseil-cpp", policy["rejected_odr_prefix_keys"])
        self.assertIn("boost", policy["rejected_odr_prefix_keys"])

    def test_source_dependency_policy_records_checked_upstream_build_files(self) -> None:
        evidence = plan.SOURCE_DEPENDENCY_POLICY["upstream_source_evidence"]

        self.assertEqual(evidence["pytorch"]["release"], "v2.14.0")
        self.assertEqual(
            evidence["pytorch"]["commit"],
            "2b3ec34829036a65cd9d1398ea72a0167dc37470",
        )
        self.assertEqual(
            evidence["pytorch"]["latest_stable_release_observed"],
            "v2.14.0",
        )
        self.assertEqual(
            evidence["pytorch"]["latest_release_candidate_observed"],
            "v2.14.1-rc1",
        )
        self.assertEqual(
            evidence["pytorch"]["checked_files"],
            [
                ".gitmodules",
                "CMakeLists.txt",
                "cmake/ProtoBuf.cmake",
                "cmake/public/protobuf.cmake",
                "pyproject.toml",
            ],
        )
        self.assertEqual(
            evidence["pytorch"]["submodule_gitlinks"]["third_party/protobuf"],
            "f0dc78d7e6e331b8c6bb2d5283e06aa26883ca7c",
        )
        self.assertEqual(
            evidence["pytorch"]["submodule_gitlinks"]["third_party/onnx"],
            "e709452ef2bbc1d113faf678c24e6d3467696e83",
        )
        self.assertEqual(
            evidence["pytorch"]["absent_odr_submodules"],
            ["third_party/abseil-cpp", "third_party/boost", "third_party/grpc"],
        )
        self.assertEqual(
            evidence["pytorch"]["system_protobuf_build_path"],
            "BUILD_CUSTOM_PROTOBUF=OFF -> cmake/ProtoBuf.cmake -> cmake/public/protobuf.cmake",
        )
        self.assertEqual(evidence["protobuf"]["required_tag"], "v3.21.12")
        self.assertEqual(evidence["protobuf"]["spack_style_tag"], "v21.12")
        self.assertEqual(
            evidence["protobuf"]["spack_style_tag_peeled_commit"],
            "f0dc78d7e6e331b8c6bb2d5283e06aa26883ca7c",
        )
        self.assertEqual(
            evidence["protobuf"]["spack_style_annotated_tag"],
            "f502b8e9c831bda0bea57d9cbeefca3eb76e4254",
        )
        self.assertEqual(evidence["onnx"]["release"], "v1.18.0")
        self.assertEqual(evidence["onnx"]["checked_files"], ["CMakeLists.txt"])
        self.assertEqual(
            evidence["onnx"]["abseil_condition"],
            "external Protobuf_VERSION >= 4.22.0 or custom protobuf fallback",
        )

    def test_onnx_guardrail_keeps_vendored_onnx_on_external_protobuf_32112(self) -> None:
        guard = plan.SOURCE_DEPENDENCY_POLICY["onnx_protobuf_guardrail"]

        self.assertEqual(guard["pytorch_submodule_mode"], "vendored-onnx")
        self.assertEqual(guard["onnx_release"], "v1.18.0")
        self.assertEqual(guard["external_protobuf_version"], "3.21.12")
        self.assertEqual(guard["required_env"]["BUILD_CUSTOM_PROTOBUF"], "OFF")
        self.assertEqual(guard["required_env"]["ONNX_BUILD_CUSTOM_PROTOBUF"], "OFF")
        self.assertEqual(guard["required_env"]["ONNX_USE_PROTOBUF_SHARED_LIBS"], "ON")
        self.assertFalse(guard["requires_abseil"])
        self.assertFalse(guard["allows_custom_protobuf_fallback"])
        self.assertEqual(guard["forbidden_if_triggered"]["protobuf"], "29.2")
        self.assertEqual(guard["forbidden_if_triggered"]["abseil-cpp"], "20240722.1")
        self.assertEqual(guard["standalone_onnx_python_package"], "not-admitted")
        self.assertEqual(guard["standalone_onnx_python_requires"], "protobuf>=4.25.1")
        self.assertIn("ONNX_ABSEIL_PROTOBUF_TRIGGER", guard["failure_classes"])
        self.assertIn("ONNX_CUSTOM_PROTOBUF_FALLBACK", guard["failure_classes"])
        self.assertIn("STANDALONE_ONNX_PYTHON_PROTOBUF_MISMATCH", guard["failure_classes"])

    def test_repository_rule_forwards_every_required_provider_prefix_to_plan(self) -> None:
        rule_text = (SCRIPT.with_name("pytorch_native.bzl")).read_text()
        match = re.search(
            r"for key in \((?P<keys>.*?)\):\n\s+val = getattr",
            rule_text,
            re.DOTALL,
        )
        self.assertIsNotNone(match)
        assert match is not None
        forwarded_keys = set(re.findall(r'"([^"]+)"', match.group("keys")))

        for provider in (
            "cuda",
            "cudnn",
            "nccl",
            "python",
            "cmake",
            "ninja",
            "openblas",
            "protobuf",
            "cusparselt",
            "openmpi",
            "cudss",
            "nvshmem",
            "numactl",
            *NATIVE_HELPER_PREFIXES,
        ):
            with self.subTest(provider=provider):
                self.assertIn(provider, forwarded_keys)
                self.assertIn(f'"{provider}": attr.string', rule_text)

    def test_repository_rule_uses_current_torch_version_and_arch_defaults(self) -> None:
        rule_text = (SCRIPT.with_name("pytorch_native.bzl")).read_text()

        self.assertIn('"cuda_arch": attr.string(default = "10.0")', rule_text)
        self.assertIn('"build_version": attr.string(default = "2.14.0")', rule_text)
        self.assertNotIn('"cuda_arch": attr.string(default = "80,90,100")', rule_text)
        self.assertNotIn('"build_version": attr.string(default = "2.10.0+training.serving")', rule_text)

    def test_module_native_repo_pins_match_upstream_odr_policy(self) -> None:
        module = (EXPERIMENT_ROOT / "MODULE.bazel").read_text()

        self.assertIn("https://github.com/protocolbuffers/protobuf/archive/v3.21.12.tar.gz", module)
        self.assertIn("strip_prefix = \"protobuf-3.21.12\"", module)
        self.assertIn("protobuf-4.21.12.tar.gz", module)
        self.assertIn("strip_prefix = \"protobuf-4.21.12\"", module)
        self.assertIn("https://archives.boost.io/release/1.90.0/source/boost_1_90_0.tar.bz2", module)
        self.assertIn("strip_prefix = \"boost_1_90_0\"", module)

    def test_dependency_contract_uses_upstream_pytorch_versions(self) -> None:
        contract = plan.SOURCE_DEPENDENCY_POLICY["dependency_contract"]

        self.assertEqual(
            contract["protobuf"],
            {
                "required": True,
                "provider": "protobuf@21.12",
                "source_version": "3.21.12",
                "prefix_role": "accepted-build-input",
            },
        )
        self.assertEqual(
            contract["py-protobuf"],
            {
                "required": True,
                "provider": "py-protobuf@4.21.12",
                "source_version": "4.21.12",
                "prefix_role": "accepted-python-build-input",
            },
        )
        for name in ("grpc", "grpc-cpp", "py-grpcio", "abseil-cpp", "boost"):
            self.assertFalse(contract[name]["required"], name)
            self.assertEqual(contract[name]["prefix_role"], "rejected-odr-provider")

        self.assertEqual(contract["abseil-cpp"]["provider"], None)
        self.assertEqual(contract["boost"]["provider"], "boost@1.90.0")
        self.assertEqual(contract["boost"]["provider_scope"], "spack-graph-node-only")

    def test_repository_native_overrides_follow_upstream_odr_family_policy(self) -> None:
        policy = plan.SOURCE_DEPENDENCY_POLICY["odr_provider_families"]
        overrides = json.loads((EXPERIMENT_ROOT / "native_overrides.json").read_text())["native"]

        self.assertEqual(policy["protobuf"]["selected_cpp_provider"], "protobuf@21.12")
        self.assertEqual(policy["protobuf"]["selected_python_provider"], "py-protobuf@4.21.12")
        self.assertEqual(overrides["protobuf@21.12"], "@protobuf_native//:lib")
        self.assertEqual(overrides["py-protobuf@4.21.12"], "@py_protobuf_native//:lib")
        self.assertNotIn("protobuf", overrides)
        self.assertNotIn("py-protobuf", overrides)

        self.assertFalse(policy["grpc"]["required_by_pytorch"])
        self.assertFalse(policy["abseil-cpp"]["required_by_pytorch"])
        self.assertFalse(policy["boost"]["required_by_pytorch"])
        for rejected in ("grpc", "grpc-cpp", "py-grpcio", "abseil-cpp"):
            self.assertIsNone(policy[rejected]["selected_provider"])
            self.assertNotIn(rejected, overrides)
        self.assertEqual(policy["boost"]["selected_provider"], "boost@1.90.0")
        self.assertEqual(overrides["boost@1.90.0"], "@boost_native//:lib")
        self.assertNotIn("boost", overrides)

    def test_plan_marks_exact_python_protobuf_provider_as_available(self) -> None:
        with temporary_directory() as tmp:
            prefixes = self.make_prefixes(Path(tmp))
            rc, doc = self.run_plan(prefixes)

        self.assertEqual(rc, 0)
        self.assertTrue(doc["preflight_ok"])
        checks = {item["name"]: item for item in doc["preflight"]}
        self.assertIn("source-policy:py-protobuf-recipe-provider", checks)
        self.assertTrue(checks["source-policy:py-protobuf-recipe-provider"]["ok"])
        self.assertIn("vaso_overlay", checks["source-policy:py-protobuf-recipe-provider"]["detail"])
        self.assertIn("source-policy:py-protobuf-native-prefix", checks)
        self.assertTrue(checks["source-policy:py-protobuf-native-prefix"]["ok"])
        self.assertIn("py-protobuf@4.21.12", checks["source-policy:py-protobuf-native-prefix"]["detail"])

    def test_rejects_odr_sensitive_prefixes_that_are_not_pytorch_inputs(self) -> None:
        for rejected in ("grpc", "grpc-cpp", "py-grpcio", "abseil-cpp", "boost"):
            with self.subTest(rejected=rejected), temporary_directory() as tmp:
                prefixes = self.make_prefixes(Path(tmp))
                prefixes[rejected] = str(Path(tmp) / rejected)
                Path(prefixes[rejected]).mkdir()
                rc, doc = self.run_plan(prefixes)

            checks = {item["name"]: item for item in doc["preflight"]}
            self.assertEqual(rc, 1)
            self.assertFalse(doc["preflight_ok"])
            check_name = f"unsupported-prefix:{rejected}"
            self.assertIn(check_name, checks)
            self.assertFalse(checks[check_name]["ok"])
            self.assertIn("not a PyTorch v2.14.0 source/build input", checks[check_name]["detail"])
            self.assertIn("unsupported PyTorch native prefix input", doc["env_error"])

    def test_preflight_rejects_non_pytorch_source_protobuf_version(self) -> None:
        with temporary_directory() as tmp:
            prefixes = self.make_prefixes(Path(tmp))
            protoc = Path(prefixes["protobuf"]) / "bin" / "protoc"
            protoc.write_text("#!/bin/sh\necho 'libprotoc 3.13.0'\n")
            os.chmod(protoc, 0o755)
            rc, doc = self.run_plan(prefixes)

        checks = {item["name"]: item for item in doc["preflight"]}
        self.assertEqual(rc, 1)
        self.assertFalse(doc["preflight_ok"])
        self.assertIn("protobuf:protoc-version", checks)
        self.assertFalse(checks["protobuf:protoc-version"]["ok"])
        self.assertIn("expected libprotoc 3.21.12", checks["protobuf:protoc-version"]["detail"])

    def test_preflight_fails_when_tool_and_blas_surfaces_are_missing(self) -> None:
        with temporary_directory() as tmp:
            prefixes = self.make_prefixes(Path(tmp), complete=False)
            rc, doc = self.run_plan(prefixes)

        checks = {item["name"]: item for item in doc["preflight"]}
        self.assertEqual(rc, 1)
        self.assertFalse(doc["preflight_ok"])
        for name in (
            "cuda:nvcc",
            "python:interpreter",
            "cmake:binary",
            "ninja:binary",
            "openblas:header",
            "openblas:libdir",
            "cudss:header",
            "cudss:library",
            "nvshmem:header",
            "nvshmem:libdir",
            "protobuf:headers",
            "protobuf:protoc",
            "protobuf:libdir",
        ):
            self.assertIn(name, checks)
            self.assertFalse(checks[name]["ok"])

    def test_execute_without_token_is_refused_even_when_preflight_passes(self) -> None:
        with temporary_directory() as tmp:
            prefixes = self.make_prefixes(Path(tmp))
            rc, doc = self.run_plan(prefixes, "--execute")

        self.assertEqual(rc, 2)
        self.assertTrue(doc["preflight_ok"])
        self.assertEqual(doc["authorization"]["required_token"], plan.REQUIRED_TOKEN)
        self.assertFalse(doc["authorization"]["token_present"])
        self.assertEqual(doc["mode"], "dry-run")
        self.assertFalse(doc["will_build"])

    def test_action_rule_moves_execute_path_into_configured_target(self) -> None:
        self.assertTrue(
            ACTION_RULE.exists(),
            "ticket 12 requires a configured PyTorch action rule",
        )
        script = ACTION_RULE.read_text(encoding="utf-8")

        self.assertIn("PytorchNativePrefixInfo = provider(", script)
        self.assertIn("ctx.actions.declare_directory(ctx.label.name + \"_prefix\")", script)
        self.assertIn("ctx.actions.declare_file(ctx.label.name + \"_wheel.whl\")", script)
        self.assertIn("ctx.actions.declare_file(ctx.label.name + \"_build_plan.json\")", script)
        self.assertIn("ctx.actions.declare_file(ctx.label.name + \"_provider_metadata.json\")", script)
        self.assertIn("ctx.actions.declare_file(ctx.label.name + \"_result.txt\")", script)
        self.assertNotIn("ctx.actions.declare_directory(ctx.label.name + \"_work\")", script)
        self.assertIn("ctx.actions.run_shell(", script)
        self.assertNotIn("repository_rule(", script)
        self.assertIn("--execute", script)
        self.assertIn("--source-anchor", script)
        self.assertIn("--source-archive", script)
        self.assertIn("--source-manifest", script)
        self.assertNotIn("--build-work-out", script)
        self.assertIn("--wheel-out", script)
        self.assertIn("action_driver.py", script)
        self.assertIn("pytorch_token_flag = rule(", script)
        self.assertIn("build_setting = config.string(flag = True)", script)
        self.assertIn("ctx.build_setting_value", script)
        self.assertIn("ctx.attr._token_flag[PytorchTokenInfo].value", script)
        self.assertNotIn("\"token\": attr.string", script)
        self.assertIn("resource_set = _PYTORCH_RESOURCE_SETS[effective_max_jobs]", script)
        self.assertIn("use_default_shell_env = True", script)
        self.assertNotIn("\"VASO_IN_INSULA\":", script)
        self.assertNotIn("\"VASO_ROOTFS_BUNDLE_MANIFEST\":", script)
        self.assertNotIn("\"TMPDIR\":", script)

        for provider in plan.REQUIRED_PREFIX_KEYS:
            with self.subTest(provider=provider):
                attr_name = PYTHON_BUILD_ATTRS.get(provider, provider + "_prefix_file")
                self.assertIn(f"\"{attr_name}\": attr.label", script)
        self.assertIn('"source_manifest": attr.label', script)
        self.assertIn('default = "//native/pytorch:pytorch-v2.14.0-2b3ec348-submodules.json"', script)

    def test_action_rule_and_targets_wire_python_build_prefixes(self) -> None:
        script = ACTION_RULE.read_text(encoding="utf-8")
        build = BUILD_FILE.read_text(encoding="utf-8")

        for provider, attr_name in PYTHON_BUILD_ATTRS.items():
            with self.subTest(provider=provider):
                self.assertIn(f"(\"{provider}\", \"{attr_name}\")", script)
                self.assertIn(f"\"{attr_name}\": attr.label", script)
                self.assertIn(f"{attr_name} = \":synthetic_prefix_path.txt\"", build)
                self.assertIn(f"{attr_name} = \"{PYTHON_BUILD_LABELS[provider]}\"", build)

    def test_action_rule_and_targets_wire_native_helper_prefixes(self) -> None:
        script = ACTION_RULE.read_text(encoding="utf-8")
        build = BUILD_FILE.read_text(encoding="utf-8")

        for provider, attr_name in NATIVE_HELPER_ATTRS.items():
            with self.subTest(provider=provider):
                self.assertIn(f"(\"{provider}\", \"{attr_name}\")", script)
                self.assertIn(f"\"{attr_name}\": attr.label", script)
                self.assertIn(f"{attr_name} = \":synthetic_prefix_path.txt\"", build)
                self.assertIn(f"{attr_name} = \"{NATIVE_HELPER_LABELS[provider]}\"", build)

    def test_action_driver_uses_action_owned_source_and_derived_prefix_layout(self) -> None:
        driver = ACTION_DRIVER.read_text(encoding="utf-8")

        self.assertIn('parser.add_argument("--source-archive", required=True)', driver)
        self.assertIn('parser.add_argument("--source-manifest", required=True)', driver)
        self.assertIn('args.source_manifest', driver)
        self.assertIn('parser.add_argument("--build-work-root", default="")', driver)
        self.assertIn('parser.add_argument("--wheel-out", default="")', driver)
        self.assertNotIn('parser.add_argument("--build-work-out", required=True)', driver)
        self.assertIn('parser.add_argument("--zstd-prefix-file", required=True)', driver)
        self.assertIn("_python_version_from_abi", driver)
        self.assertIn('Path("lib") / ("python" + version) / "site-packages"', driver)
        self.assertIn('site_packages / "torch" / "lib"', driver)
        self.assertNotIn("lib\" / \"site-packages\" / \"torch\"", driver)

    def test_module_declares_offline_pinned_pytorch_source_archive(self) -> None:
        module = MODULE.read_text(encoding="utf-8")

        self.assertIn("http_file = use_repo_rule", module)
        self.assertIn('name = "pytorch_v2_14_0_source_archive"', module)
        self.assertIn('downloaded_file_path = "pytorch-v2.14.0-2b3ec348-recursive.tar.zst"', module)
        self.assertIn('name = "pytorch_v2_14_0_source"', module)
        self.assertIn(
            "pytorch-v2.14.0-2b3ec348-recursive.tar.zst",
            module,
        )
        self.assertIn(
            'sha256 = "62dedab65c9d7dda7cbb6edaad07d325ad193014496e5da4540a5c074cd8a50a"',
            module,
        )
        self.assertIn('strip_prefix = "pytorch-v2.14.0"', module)
        self.assertIn('type = "tar.zst"', module)
        self.assertIn('exports_files(["setup.py"])', module)
        pytorch_block = module.split('name = "pytorch_v2_14_0_source"', 1)[1]
        pytorch_block = pytorch_block.split(")", 1)[0]
        self.assertNotIn('glob(["**"])', pytorch_block)

    def test_build_file_exposes_token_safe_action_dry_run_targets(self) -> None:
        build = BUILD_FILE.read_text(encoding="utf-8")

        self.assertIn('load(":pytorch_action.bzl", "pytorch_action_prefix", "pytorch_max_jobs_flag", "pytorch_token_flag")', build)
        self.assertIn('name = "token"', build)
        self.assertIn('name = "max_jobs"', build)
        self.assertIn('name = "pytorch_action_dry_run"', build)
        self.assertIn('source_anchor = "@pytorch_v2_14_0_source//:setup.py"', build)
        self.assertIn('execute = False', build)
        self.assertNotIn("source_files = []", build)
        self.assertNotIn('token = "build-native-pytorch"', build)

    def test_build_file_exposes_real_token_gated_action_target(self) -> None:
        build = BUILD_FILE.read_text(encoding="utf-8")

        self.assertIn('name = "pytorch_action"', build)
        self.assertIn('execute = True', build)
        self.assertIn('source_archive = "@pytorch_v2_14_0_source_archive//file"', build)
        self.assertIn('pytorch-v2.14.0-2b3ec348-submodules.json', build)
        self.assertIn('zstd_prefix_file = "@zstd_native//:prefix_path.txt"', build)
        self.assertIn('cuda_prefix_file = "@cuda_native//:prefix_path.txt"', build)
        self.assertIn('cudnn_prefix_file = "@cudnn_native//:prefix_path.txt"', build)
        self.assertIn('nccl_prefix_file = "@nccl_native//:prefix_path.txt"', build)
        self.assertIn('numactl_prefix_file = "@numactl_native//:prefix_path.txt"', build)
        action = build_target_block("pytorch_action")
        self.assertIn('source_manifest = ":pytorch-v2.14.0-2b3ec348-submodules.json"', action)
        self.assertNotIn('max_jobs = "8"', action)

    def test_action_rule_reserves_per_run_pytorch_job_slots(self) -> None:
        script = ACTION_RULE.read_text(encoding="utf-8")
        build = BUILD_FILE.read_text(encoding="utf-8")
        run_sh = RUN_SH.read_text(encoding="utf-8")

        self.assertIn("PytorchMaxJobsInfo = provider(", script)
        self.assertIn("pytorch_max_jobs_flag = rule(", script)
        self.assertIn("build_setting = config.int(flag = True)", script)
        self.assertIn('"_max_jobs_flag": attr.label(', script)
        self.assertIn('default = Label("//native/pytorch:max_jobs")', script)
        self.assertIn("ctx.attr._max_jobs_flag[PytorchMaxJobsInfo].value", script)
        self.assertIn("resource_set = _PYTORCH_RESOURCE_SETS[effective_max_jobs]", script)
        self.assertIn("args.extend([\"--max-jobs\", str(effective_max_jobs)])", script)
        self.assertIn("def _pytorch_resource_set_48(os_name, inputs_size):", script)
        self.assertIn("return _pytorch_resource_values(48)", script)
        self.assertIn('"cpu": jobs', script)
        self.assertIn('"memory": jobs * _PYTORCH_MEMORY_MB_PER_JOB', script)
        self.assertNotIn("_PYTORCH_DEFAULT_MAX_JOBS", script)
        self.assertNotIn('"cpu": 96,', script)

        self.assertIn('load(":pytorch_action.bzl", "pytorch_action_prefix", "pytorch_max_jobs_flag", "pytorch_token_flag")', build)
        self.assertIn("pytorch_max_jobs_flag(", build)
        self.assertIn('name = "max_jobs"', build)
        self.assertIn("build_setting_default = 96", build)
        self.assertIn('"--//native/pytorch:max_jobs=$VASO_PYTORCH_MAX_JOBS_RESOLVED"', run_sh)

    def test_real_action_derives_python_abi_from_prefix(self) -> None:
        action = build_target_block("pytorch_action")

        self.assertNotIn("python_abi =", action)
        self.assertIn('python_prefix_file = "@python_313_native//:prefix_path.txt"', action)
        self.assertNotIn('python_prefix_file = "@python_native//:prefix_path.txt"', action)

    def test_run_sh_forces_offline_repository_resolution(self) -> None:
        run_sh = RUN_SH.read_text(encoding="utf-8")

        # B5 moved offline enforcement into scripts/insula/insula.sh, which
        # injects --repository_disable_download into every non-fetch Bazel
        # phase; run.sh must route through it.
        self.assertIn("--repository_disable_download", run_sh)
        self.assertIn('INSULA_SH="$REPO_ROOT/scripts/insula/insula.sh"', run_sh)


if __name__ == "__main__":
    unittest.main()
