#!/usr/bin/env python3
"""Contract tests for the downstream consumer conformance harness."""

from __future__ import annotations

import ast
import importlib.util
import json
import os
import re
import subprocess
import tempfile
import sys
import types
import unittest
from pathlib import Path


EXPERIMENT_ROOT = Path(__file__).resolve().parents[1]
CONSUMER_ROOT = EXPERIMENT_ROOT / "examples" / "consumers"
MODULE = CONSUMER_ROOT / "MODULE.bazel"
BAZELRC = CONSUMER_ROOT / ".bazelrc"
RUN_SH = CONSUMER_ROOT / "run.sh"
MANIFEST = CONSUMER_ROOT / "bcr-manifest.tsv"
ACQUIRE = EXPERIMENT_ROOT / "tools" / "bcr_mirror.py"
INSULA_GATE = EXPERIMENT_ROOT / "scripts" / "agents" / "triumvirate-gate-insula.sh"
TORCH_DEFS = EXPERIMENT_ROOT / "torch" / "defs.bzl"
TORCH_PROFILE_CONFIG = EXPERIMENT_ROOT / "torch" / "profile_config.h"


class _ShapeTensor:
    def __init__(self, shape: tuple[int, ...], dtype: object = None) -> None:
        self.shape = shape
        self.dtype = dtype

    def __getitem__(self, key: object) -> "_ShapeTensor":
        if not isinstance(key, tuple):
            key = (key,)

        result: list[int] = []
        source_index = 0
        for item in key:
            if item is Ellipsis:
                remaining_keys = len(key) - key.index(item) - 1
                remaining_shape = len(self.shape) - source_index - remaining_keys
                result.extend(self.shape[source_index:source_index + remaining_shape])
                source_index += remaining_shape
            elif isinstance(item, int):
                source_index += 1
            else:
                result.append(self.shape[source_index])
                source_index += 1
        result.extend(self.shape[source_index:])
        return _ShapeTensor(tuple(result), dtype=self.dtype)


class _FakeAutogradFunction:
    @classmethod
    def apply(cls, *args: object) -> object:
        class _Context:
            def save_for_backward(self, *unused: object) -> None:
                return None

        return cls.forward(_Context(), *args)


def _load_gsplat_wrapper_with_fake_torch() -> types.ModuleType:
    package_name = "_vaso_gsplat_rasterizer_shape_contract"
    package_root = CONSUMER_ROOT / "gsplat_rasterizer" / "vaso_gsplat_rasterizer"
    fake_torch = types.ModuleType("torch")
    fake_torch.Tensor = _ShapeTensor
    fake_torch.float32 = object()
    fake_torch.autograd = types.SimpleNamespace(Function=_FakeAutogradFunction)
    fake_torch.no_grad = lambda: (lambda func: func)

    previous = {
        name: sys.modules.get(name)
        for name in (
            "torch",
            package_name,
            f"{package_name}._C",
        )
    }
    try:
        sys.modules["torch"] = fake_torch
        sys.modules[f"{package_name}._C"] = types.ModuleType(f"{package_name}._C")
        spec = importlib.util.spec_from_file_location(
            package_name,
            package_root / "__init__.py",
            submodule_search_locations=[str(package_root)],
        )
        self_spec = spec
        if self_spec is None or self_spec.loader is None:
            raise AssertionError("could not load gsplat wrapper spec")
        module = importlib.util.module_from_spec(self_spec)
        sys.modules[package_name] = module
        self_spec.loader.exec_module(module)
        return module
    finally:
        for name, value in previous.items():
            if value is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = value


def _call_name(call: ast.Call) -> str | None:
    if isinstance(call.func, ast.Name):
        return call.func.id
    return None


def _string_kw(call: ast.Call, name: str) -> str | None:
    for keyword in call.keywords:
        if keyword.arg == name and isinstance(keyword.value, ast.Constant) and isinstance(keyword.value.value, str):
            return keyword.value.value
    return None


def _module_calls() -> list[ast.Call]:
    tree = ast.parse(MODULE.read_text(encoding="utf-8"), filename=str(MODULE))
    return [node for node in ast.walk(tree) if isinstance(node, ast.Call)]


class ConsumerHarnessTest(unittest.TestCase):
    def test_consumer_module_depends_on_vaso_graph_and_common_bcr_modules_only(self) -> None:
        calls = _module_calls()
        deps = {
            _string_kw(call, "name"): _string_kw(call, "version")
            for call in calls
            if _call_name(call) == "bazel_dep"
        }
        self.assertEqual(deps.get("vaso_graph"), "0.1.0")
        for module in (
            "abseil-cpp",
            "cutlass",
            "googletest",
            "pybind11_bazel",
            "fmt",
            "protobuf",
            "eigen",
            "glm",
            "gsplat_cuda",
            "rules_cc",
            "rules_rust",
            "rules_shell",
        ):
            self.assertIn(module, deps)
        self.assertEqual(deps.get("cutlass"), "4.2.1")
        self.assertEqual(deps.get("glm"), "1.0.1")
        self.assertEqual(deps.get("gsplat_cuda"), "1.5.3")
        self.assertEqual(
            sum(1 for call in calls if _call_name(call) == "bazel_dep" and _string_kw(call, "name") == "cutlass"),
            1,
        )
        self.assertEqual(
            sum(1 for call in calls if _call_name(call) == "bazel_dep" and _string_kw(call, "name") == "glm"),
            1,
        )
        self.assertEqual(
            sum(1 for call in calls if _call_name(call) == "bazel_dep" and _string_kw(call, "name") == "gsplat_cuda"),
            1,
        )
        self.assertNotIn('"vaso_zstd"', MODULE.read_text(encoding="utf-8"))
        self.assertIn("--inject_repository=vaso_zstd=$zstd_profile_repo", RUN_SH.read_text(encoding="utf-8"))

        overrides = [
            call
            for call in calls
            if _call_name(call) == "local_path_override"
        ]
        self.assertEqual(
            [(_string_kw(call, "module_name"), _string_kw(call, "path")) for call in overrides],
            [("vaso_graph", "../.."), ("rust_pyo3_cargo", "../../../third_party/rust_pyo3_cargo")],
        )
        text = MODULE.read_text(encoding="utf-8")
        self.assertNotIn("@spack_", text)
        self.assertNotRegex(text, r"\b(?:archive_override|git_override|use_repo_rule|http_archive)\s*\(")
        self.assertIn("vaso_graph.profile(", text)
        self.assertIn('name = "torch"', text)
        self.assertIn('lock = "@vaso_graph//locks:torch.lock.json"', text)
        self.assertIn('line = "cu129"', text)
        self.assertIn('/vaso/artifacts/torch/cu129/torch-prefix-cu129-b050106d6e5baaf18af428962408c8f13ecc527cb9ebbe4c2eded63b67f3cc4f.tar', text)
        self.assertIn('sha256 = "b050106d6e5baaf18af428962408c8f13ecc527cb9ebbe4c2eded63b67f3cc4f"', text)
        self.assertIn('line = "cu130"', text)
        self.assertIn('/vaso/artifacts/torch/cu130/torch-prefix-cu130-1e79907c097c6e2f1084074aa2fcc91967dad1de5ab442f8a88d8c2ba7d84c9a.tar', text)
        self.assertIn('sha256 = "1e79907c097c6e2f1084074aa2fcc91967dad1de5ab442f8a88d8c2ba7d84c9a"', text)
        self.assertNotIn("vaso_graph.graph(", text)

    def test_rust_pyo3_package_declares_pinned_toolchain_crates_and_wheel_verifier(self) -> None:
        rust_root = CONSUMER_ROOT / "rust_pyo3"
        build = (rust_root / "BUILD.bazel").read_text(encoding="utf-8")
        cargo = (rust_root / "Cargo.toml").read_text(encoding="utf-8")
        lock = (rust_root / "Cargo.lock").read_text(encoding="utf-8")
        rust_rule = (rust_root / "rust_pyo3.bzl").read_text(encoding="utf-8")
        runner = (rust_root / "run_verifier.sh").read_text(encoding="utf-8")
        verifier = (rust_root / "rust_pyo3_verifier.py").read_text(encoding="utf-8")
        toolchain_manifest = (rust_root / "rust-toolchain-manifest.json").read_text(encoding="utf-8")

        for name in (
            "rust_pyo3_wheel",
            "rust_pyo3_wheel_verifier",
            "rust_pyo3_host_toolchain_poison_test",
        ):
            self.assertIn(f'name = "{name}"', build)
        self.assertIn("cargo_vendor", build)
        self.assertIn("rustc_sha256", build)
        self.assertIn("cargo_sha256", build)
        self.assertIn("rust_toolchain_manifest", build)
        self.assertIn("mmap pointers are inside", verifier)
        self.assertIn("round-trip", verifier)
        self.assertIn("vaso_provenance.json", verifier)
        self.assertIn("pyo3", cargo)
        self.assertIn("regex", cargo)
        self.assertIn("memmap2", cargo)
        self.assertNotIn("VASO_OPENMPI_PREFIX", build)
        self.assertNotIn("VASO_PMIX_PREFIX", build)
        self.assertNotIn("VASO_HWLOC_PREFIX", build)
        self.assertNotIn("VASO_LIBEVENT_PREFIX", build)
        self.assertNotIn("VASO_LIBPCIACCESS_PREFIX", build)
        self.assertNotIn("VASO_LIBXML2_PREFIX", build)
        self.assertNotIn("VASO_LIBICONV_PREFIX", build)
        self.assertIn("checksum =", lock)
        self.assertIn("rustc", toolchain_manifest)
        self.assertIn("cargo", toolchain_manifest)
        self.assertNotIn("~/.cargo", rust_rule)
        self.assertNotIn("use_default_shell_env", rust_rule)
        self.assertIn("python_external", runner)
        self.assertIn("dependency_sites", runner)
        self.assertIn("PYTHONPATH", runner)

    def test_python_consumers_declare_torch_triton_compile_and_jax_release_verifiers(self) -> None:
        python_root = CONSUMER_ROOT / "python_consumers"
        build = (python_root / "BUILD.bazel").read_text(encoding="utf-8")
        reference = (python_root / "reference.py").read_text(encoding="utf-8")
        torch_impl = (python_root / "torch_se3.py").read_text(encoding="utf-8")
        triton_impl = (python_root / "triton_se3.py").read_text(encoding="utf-8")
        jax_impl = (python_root / "jax_se3.py").read_text(encoding="utf-8")
        torch_verifier = (python_root / "verify_torch.py").read_text(encoding="utf-8")
        jax_verifier = (python_root / "verify_jax.py").read_text(encoding="utf-8")

        for name in (
            "python_consumers",
            "reference_math_test",
            "torch_release_verifier",
            "jax_release_verifier",
        ):
            self.assertIn(f'name = "{name}"', build)
        self.assertIn("py_library", build)
        self.assertIn("project_se3_pinhole_python", reference)
        self.assertIn("project_se3_pinhole_torch_reference", torch_impl)
        self.assertIn("torch.compile", torch_impl)
        self.assertIn("@triton.jit", triton_impl)
        self.assertIn("tl.program_id", triton_impl)
        self.assertIn("behind the camera", triton_impl)
        self.assertIn("project_se3_pinhole_jax", jax_impl)
        self.assertIn("project_se3_pinhole_numpy_reference", jax_impl)
        self.assertIn("assert_shipped_wheel", torch_verifier)
        self.assertIn("verify_triton_matches_reference", torch_verifier)
        self.assertIn("verify_torch_compile_matches_reference", torch_verifier)
        self.assertIn("assert_shipped_wheel", jax_verifier)
        self.assertNotIn("pip install", torch_verifier)
        self.assertNotIn("pip install", jax_verifier)

    def test_voxel_scatter_declares_cuda_torch_extension_and_conformance_tests(self) -> None:
        voxel_root = CONSUMER_ROOT / "voxel_scatter"
        build = (voxel_root / "BUILD.bazel").read_text(encoding="utf-8")
        source = (voxel_root / "vaso_voxel_scatter" / "voxel_scatter.cpp").read_text(encoding="utf-8")
        wrapper = (voxel_root / "vaso_voxel_scatter" / "__init__.py").read_text(encoding="utf-8")
        test = (voxel_root / "tests" / "voxel_scatter_test.py").read_text(encoding="utf-8")
        runner = (voxel_root / "run_wheel_test.sh").read_text(encoding="utf-8")

        for name in (
            "voxel_scatter",
            "voxel_scatter_cpu_test",
            "voxel_scatter_cuda_test",
        ):
            self.assertIn(f'name = "{name}"', build)
        self.assertIn("vaso_torch_extension", build)
        self.assertIn('cuda_srcs = ["vaso_voxel_scatter/voxel_scatter_kernel.cu"]', build)
        self.assertIn('py_srcs = ["vaso_voxel_scatter/__init__.py"]', build)
        self.assertIn('"requires-gpu"', build)
        self.assertIn("torch.autograd.Function", wrapper)
        self.assertIn("torch.library.register_fake", wrapper)
        self.assertIn("torch.ops.vaso_graph.voxel_scatter_forward", wrapper)
        self.assertLess(
            source.index("voxel_scatter_forward_cuda"),
            source.index("namespace {"),
        )
        self.assertIn("reference_voxel_scatter", test)
        self.assertIn("torch.library.opcheck", test)
        self.assertIn("torch.compile", test)
        self.assertIn("LD_LIBRARY_PATH", runner)
        self.assertIn("find -L \"$wheelhouse\"", runner)
        self.assertIn("python_external", runner)
        self.assertIn("dependency_sites", runner)
        self.assertNotIn("@vaso_graph//native/pytorch:pytorch_action", build)
        self.assertNotIn("@cuda_native", build)
        self.assertNotIn("@nccl_native", build)

    def test_nvshmem_ep_declares_rdc_torch_extension_and_eight_gpu_conformance(self) -> None:
        nvshmem_root = CONSUMER_ROOT / "nvshmem_ep"
        build = (nvshmem_root / "BUILD.bazel").read_text(encoding="utf-8")
        wrapper = (nvshmem_root / "vaso_nvshmem_ep" / "__init__.py").read_text(encoding="utf-8")
        source = (nvshmem_root / "vaso_nvshmem_ep" / "nvshmem_ep.cpp").read_text(encoding="utf-8")
        kernel = (nvshmem_root / "vaso_nvshmem_ep" / "nvshmem_ep_kernel.cu").read_text(encoding="utf-8")
        test = (nvshmem_root / "tests" / "nvshmem_ep_test.py").read_text(encoding="utf-8")
        runner = (nvshmem_root / "run_wheel_test.sh").read_text(encoding="utf-8")
        provenance = (nvshmem_root / "PROVENANCE.md").read_text(encoding="utf-8")

        for name in (
            "nvshmem_ep",
            "nvshmem_ep_cuda_test",
        ):
            self.assertIn(f'name = "{name}"', build)
        self.assertIn("vaso_torch_extension", build)
        self.assertIn("rdc = True", build)
        self.assertIn("uses_nvshmem = True", build)
        self.assertIn('cuda_srcs = ["vaso_nvshmem_ep/nvshmem_ep_kernel.cu"]', build)
        self.assertIn('"requires-gpu"', build)
        for api in (
            "#include <nvshmem",
            "nvshmemx_get_uniqueid",
            "nvshmemx_set_attr_uniqueid_args",
            "NVSHMEMX_INIT_WITH_UNIQUEID",
            "nvshmem_malloc(",
            "nvshmem_free(",
            "nvshmem_finalize(",
            "nvshmem_n_pes(",
            "nvshmem_my_pe(",
        ):
            self.assertNotIn(api, source)
        for wrapper_name in (
            "nvshmem_ep_initialize_cuda",
            "nvshmem_ep_n_pes_cuda",
            "nvshmem_ep_malloc_cuda",
            "nvshmem_ep_free_cuda",
        ):
            self.assertIn(wrapper_name, source)
        self.assertIn("TORCH_LIBRARY_IMPL(vaso_graph, CUDA, m)", source)
        self.assertIn("nvshmemx_get_uniqueid", kernel)
        self.assertIn("nvshmemx_set_attr_uniqueid_args", kernel)
        self.assertIn("NVSHMEMX_INIT_WITH_UNIQUEID", kernel)
        self.assertIn("nvshmem_finalize", kernel)
        self.assertIn("nvshmem_malloc", kernel)
        self.assertIn("nvshmem_free", kernel)
        self.assertIn("nvshmem_putmem", kernel)
        self.assertIn("nvshmem_quiet", kernel)
        self.assertIn("namespace vaso_nvshmem_ep_kernel", kernel)
        self.assertIn('extern "C" __global__ void nvshmem_ep_dispatch_kernel', kernel)
        self.assertIn('extern "C" __global__ void nvshmem_ep_combine_kernel', kernel)
        self.assertIn("nvshmemx_collective_launch", kernel)
        self.assertIn("check_collective_launch", kernel)
        self.assertIn("clear_last_kernel_error();", kernel)
        self.assertIn("torch.distributed", test)
        self.assertIn("all_to_all_single", test)
        self.assertIn("WORLD_SIZE == 8", test)
        self.assertIn("torch.distributed.run", runner)
        self.assertIn("--nproc-per-node=8", runner)
        self.assertIn("find -L \"$wheelhouse\"", runner)
        self.assertIn("python_external", runner)
        self.assertIn("dependency_sites", runner)
        self.assertIn("dtype != torch.float32", wrapper)
        self.assertIn("allocate_symmetric_float32", wrapper)
        self.assertIn("#include <c10/cuda/CUDAGuard.h>", source)
        self.assertIn("#include <pybind11/stl.h>", source)
        self.assertIn("std::vector<int64_t> sizes", source)
        self.assertIn("c10::cuda::CUDAGuard guard(tokens.device())", source)
        self.assertIn("c10::cuda::CUDAGuard guard(dispatched.device())", source)
        self.assertIn('m.def("allocate_symmetric_float32"', source)
        self.assertNotIn('m.def("allocate_symmetric", allocate_symmetric)', source)
        self.assertIn("MIT", provenance)
        self.assertNotIn("@vaso_graph//native/pytorch:pytorch_action", build)
        self.assertNotIn("@cuda_native", build)
        self.assertNotIn("@nccl_native", build)
        self.assertNotIn("@nvshmem_native", build)

    def test_nvshmem_ep_runner_uses_profile_python_for_torch_distributed_run(self) -> None:
        runner = (CONSUMER_ROOT / "nvshmem_ep" / "run_wheel_test.sh").read_text(encoding="utf-8")

        self.assertIn('cd "$site"', runner)
        self.assertIn('"$python" -m torch.distributed.run', runner)
        self.assertNotIn('torchrun="$torch_prefix/bin/torchrun"', runner)
        self.assertNotRegex(runner, r'\bexec\s+"\$torchrun"')
        self.assertLess(
            runner.index('cd "$site"'),
            runner.index('exec "$python" -m torch.distributed.run'),
        )

    def test_cutlass_gemm_declares_cutlass_extension_and_conformance_tests(self) -> None:
        cutlass_root = CONSUMER_ROOT / "cutlass_gemm"
        build = (cutlass_root / "BUILD.bazel").read_text(encoding="utf-8")
        wrapper = (cutlass_root / "vaso_cutlass_gemm" / "__init__.py").read_text(encoding="utf-8")
        test = (cutlass_root / "tests" / "cutlass_gemm_test.py").read_text(encoding="utf-8")
        runner = (cutlass_root / "run_wheel_test.sh").read_text(encoding="utf-8")
        readme = (cutlass_root / "README.md").read_text(encoding="utf-8")

        for name in (
            "cutlass_gemm",
            "cutlass_gemm_cpu_test",
            "cutlass_gemm_cuda_test",
        ):
            self.assertIn(f'name = "{name}"', build)
        self.assertIn("vaso_torch_extension", build)
        self.assertIn('cuda_srcs = ["vaso_cutlass_gemm/cutlass_gemm_kernel.cu"]', build)
        self.assertIn('py_srcs = ["vaso_cutlass_gemm/__init__.py"]', build)
        self.assertIn('deps = ["@cutlass//:cutlass"]', build)
        self.assertIn('"requires-gpu"', build)
        self.assertIn("torch.ops.vaso_graph.cutlass_gemm", wrapper)
        self.assertIn("reference_cutlass_gemm", test)
        self.assertIn("torch.matmul", test)
        self.assertIn("LD_LIBRARY_PATH", runner)
        self.assertIn("BSD-3-Clause", readme)
        self.assertNotIn("@vaso_graph//native/pytorch:pytorch_action", build)
        self.assertNotIn("@cuda_native", build)
        self.assertNotIn("@nccl_native", build)

    def test_gsplat_rasterizer_declares_gsplat_extension_and_gpu_verifier(self) -> None:
        gsplat_root = CONSUMER_ROOT / "gsplat_rasterizer"
        build = (gsplat_root / "BUILD.bazel").read_text(encoding="utf-8")
        wrapper = (gsplat_root / "vaso_gsplat_rasterizer" / "__init__.py").read_text(encoding="utf-8")
        binding = (gsplat_root / "vaso_gsplat_rasterizer" / "bindings.cpp").read_text(encoding="utf-8")
        test = (gsplat_root / "tests" / "gsplat_rasterizer_test.py").read_text(encoding="utf-8")
        runner = (gsplat_root / "run_wheel_test.sh").read_text(encoding="utf-8")
        provenance = (gsplat_root / "PROVENANCE.md").read_text(encoding="utf-8")
        root_build = (CONSUMER_ROOT / "BUILD.bazel").read_text(encoding="utf-8")
        run_sh = RUN_SH.read_text(encoding="utf-8")

        for name in (
            "gsplat_rasterizer",
            "gsplat_rasterizer_cuda_test",
        ):
            self.assertIn(f'name = "{name}"', build)
        self.assertIn("vaso_torch_extension", build)
        self.assertIn("vaso_gsplat_rasterizer/bindings.cpp", build)
        self.assertIn("@gsplat_cuda//:cpp_srcs", build)
        self.assertIn("@gsplat_cuda//:cuda_srcs", build)
        self.assertIn("@gsplat_cuda//:headers", build)
        self.assertIn("--expt-relaxed-constexpr", build)
        self.assertIn("-diag-suppress=20012,20013,20015", build)
        self.assertIn('py_srcs = ["vaso_gsplat_rasterizer/__init__.py"]', build)
        self.assertIn('"requires-gpu"', build)
        self.assertIn('"insula-only"', build)
        self.assertIn("PYBIND11_MODULE(_C, m)", binding)
        self.assertIn("projection_ewa_simple_fwd", binding)
        self.assertIn("rasterize_to_pixels_3dgs_fwd", binding)
        self.assertIn("torch.autograd.Function", wrapper)
        self.assertIn("_C.project_ewa_simple_forward", wrapper)
        self.assertIn("_C.rasterize_to_pixels_forward", wrapper)
        self.assertIn("render_reference_scene", wrapper)
        self.assertIn("render_colors[0]", wrapper)
        self.assertIn("tests/generate_reference_image.py", build)
        self.assertIn("reference_image.pt", test)
        self.assertIn("finite_difference_gradcheck", test)
        self.assertIn("render_reference_scene", test)
        self.assertTrue((gsplat_root / "tests" / "reference_image.pt").exists())
        generator = (gsplat_root / "tests" / "generate_reference_image.py").read_text(encoding="utf-8")
        self.assertIn("import numpy as np", generator)
        self.assertIn("alpha_threshold = 1.0 / 255.0", generator)
        self.assertIn("--check", generator)
        self.assertIn("LD_LIBRARY_PATH", runner)
        self.assertIn("python_external", runner)
        self.assertIn("dependency_sites", runner)
        self.assertIn("Apache-2.0", provenance)
        self.assertIn("gsplat", provenance)
        self.assertIn("diff-gaussian-rasterization", provenance)
        self.assertIn("non-commercial", provenance)
        self.assertIn("generate_reference_image.py", provenance)
        self.assertIn("CPU with NumPy", provenance)
        self.assertIn("//examples/consumers/gsplat_rasterizer:consumer_gsplat_rasterizer_files", root_build)
        self.assertIn("//gsplat_rasterizer:gsplat_rasterizer_cuda_test", run_sh)
        self.assertNotIn("@vaso_graph//native/pytorch:pytorch_action", build)
        self.assertNotIn("@cuda_native", build)
        self.assertNotIn("@nccl_native", build)

    def test_gsplat_reference_scene_returns_single_rgb_image(self) -> None:
        wrapper = _load_gsplat_wrapper_with_fake_torch()
        wrapper.reference_scene_tensors = lambda **unused: (object(), object(), object(), object(), object())
        wrapper.render_scene = lambda *unused, **unused_kw: (
            _ShapeTensor((1, 8, 8, 3)),
            _ShapeTensor((1, 8, 8, 1)),
        )

        image = wrapper.render_reference_scene(device="cuda")

        self.assertEqual(image.shape, (8, 8, 3))

    def test_consumer_rootfs_config_carries_offline_registry_and_insula_env(self) -> None:
        text = BAZELRC.read_text(encoding="utf-8")

        for config in ("vaso_rootfs", "vaso_torch_rootfs"):
            self.assertIn(f"build:{config} --platforms=@vaso_graph//toolchains:torch_rootfs_platform", text)
            self.assertIn(f"build:{config} --extra_toolchains=@vaso_graph//toolchains:gcc_13_3_toolchain", text)
            self.assertIn(f"build:{config} --extra_toolchains=@vaso_graph//toolchains:cuda_rootfs_toolchain", text)
        self.assertIn("build:vaso_jax_rootfs --platforms=@vaso_graph//toolchains:jax_rootfs_platform", text)
        self.assertIn("build:vaso_jax_rootfs --extra_toolchains=@vaso_graph//toolchains:clang_23_toolchain", text)
        self.assertIn("common:vaso_rootfs --registry=file:///vaso/sources/bcr/registry/current", text)
        self.assertIn("common:vaso_rootfs --lockfile_mode=off", text)
        self.assertIn("common:vaso_rootfs --repository_disable_download", text)
        self.assertIn("common:vaso_rootfs --action_env=VASO_IN_INSULA=1", text)
        self.assertIn("common:vaso_rootfs --action_env=VASO_ROOTFS_BUNDLE_MANIFEST=/run/vaso/rootfs-bundle.json", text)
        self.assertIn("common:vaso_rootfs --repo_env=VASO_IN_INSULA=1", text)
        self.assertIn("common:vaso_rootfs --repo_env=VASO_ROOTFS_BUNDLE_MANIFEST=/run/vaso/rootfs-bundle.json", text)

    def test_hello_cc_is_insula_only_and_uses_abseil_and_googletest(self) -> None:
        build = (CONSUMER_ROOT / "hello_cc" / "BUILD.bazel").read_text(encoding="utf-8")

        self.assertIn('name = "hello_cc"', build)
        self.assertIn('name = "hello_cc_test"', build)
        self.assertIn("@abseil-cpp//absl/strings", build)
        self.assertIn("@googletest//:gtest_main", build)
        self.assertIn('"insula-only"', build)
        self.assertNotIn("@spack_", build)

    def test_consumer_build_files_do_not_hand_set_profile_cxx_flags(self) -> None:
        offenders: list[str] = []
        for build_file in sorted(CONSUMER_ROOT.rglob("BUILD.bazel")):
            text = build_file.read_text(encoding="utf-8")
            for needle in ("-std=c++", "_GLIBCXX_USE_CXX11_ABI"):
                if needle in text:
                    offenders.append(f"{build_file.relative_to(CONSUMER_ROOT)} contains {needle}")

        self.assertEqual(offenders, [])

    def test_geom_core_declares_geometry_libtorch_and_protobuf_targets(self) -> None:
        build = (CONSUMER_ROOT / "geom_core" / "BUILD.bazel").read_text(encoding="utf-8")

        for name in (
            "geom_core",
            "geometry_test",
            "camera_test",
            "libtorch_cli",
            "libtorch_cli_cpu_test",
            "libtorch_cli_cuda_test",
            "protobuf_coexistence_test",
        ):
            self.assertIn(f'name = "{name}"', build)
        self.assertIn("@eigen//:eigen", build)
        self.assertIn("@vaso_zstd//:lib", build)
        self.assertIn("vaso_libtorch_cc_binary", build)
        self.assertIn("vaso_libtorch_cc_test", build)
        self.assertNotIn("@spack_", build)

    def test_torch_profile_wrapper_owns_libtorch_cxx_contract(self) -> None:
        defs = TORCH_DEFS.read_text(encoding="utf-8")
        config = TORCH_PROFILE_CONFIG.read_text(encoding="utf-8")
        run_sh = RUN_SH.read_text(encoding="utf-8")

        self.assertIn("$(VASO_TORCH_PREFIX)", defs)
        self.assertIn("$(VASO_TORCH_CXX_STANDARD)", defs)
        self.assertIn("$(VASO_TORCH_CXX11_ABI)", defs)
        self.assertIn("@vaso_libtorch_profile//:headers", defs)
        self.assertIn("-ltorch_cpu", defs)
        self.assertIn("-ltorch_cuda", defs)
        self.assertIn("-Wl,-rpath-link,", defs)
        self.assertIn("def _libtorch_test_env", defs)
        self.assertIn("LD_LIBRARY_PATH", defs)
        self.assertIn("/torch.libs", defs)
        self.assertNotIn("$(VASO_OPENMPI_PREFIX)/lib", defs)
        self.assertNotIn("$(VASO_PMIX_PREFIX)/lib", defs)
        self.assertNotIn("$(VASO_HWLOC_PREFIX)/lib", defs)
        self.assertNotIn("$(VASO_LIBEVENT_PREFIX)/lib", defs)
        self.assertNotIn("$(VASO_LIBPCIACCESS_PREFIX)/lib", defs)
        self.assertNotIn("$(VASO_LIBXML2_PREFIX)/lib", defs)
        self.assertNotIn("$(VASO_LIBICONV_PREFIX)/lib", defs)
        self.assertIn("VASO_TORCH_EXPECTED_CXX11_ABI", config)
        self.assertIn("_GLIBCXX_USE_CXX11_ABI != VASO_TORCH_EXPECTED_CXX11_ABI", config)
        self.assertIn("torch_cxx_standard", run_sh)
        self.assertIn("_GLIBCXX_USE_CXX11_ABI", run_sh)
        self.assertIn("@vaso_torch_artifact_$line//:prefix_path.txt", run_sh)
        self.assertIn("$consumer_ob/$torch_artifact_prefix_relative", run_sh)
        self.assertIn("info", run_sh)
        self.assertIn("execution_root", run_sh)
        self.assertIn("--define=VASO_TORCH_PREFIX=$torch_prefix", run_sh)
        self.assertIn("--define=VASO_TORCH_CXX_STANDARD=$torch_cxx_standard", run_sh)
        self.assertIn("--define=VASO_TORCH_CXX11_ABI=$torch_cxx11_abi", run_sh)
        self.assertIn("--@vaso_graph//native/common:release_line=$line", run_sh)
        self.assertNotIn("pytorch_action_prefix", run_sh)
        self.assertNotIn("VASO_OPENMPI_PREFIX", run_sh)
        self.assertNotIn("VASO_PMIX_PREFIX", run_sh)
        self.assertNotIn("VASO_HWLOC_PREFIX", run_sh)
        self.assertNotIn("VASO_LIBEVENT_PREFIX", run_sh)
        self.assertNotIn("VASO_LIBPCIACCESS_PREFIX", run_sh)
        self.assertNotIn("VASO_LIBXML2_PREFIX", run_sh)
        self.assertNotIn("VASO_LIBICONV_PREFIX", run_sh)
        self.assertIn("--inject_repository=vaso_libtorch_profile=$torch_profile_repo", run_sh)

    def test_runner_executes_all_consumer_packages_inside_insula_cpu_only(self) -> None:
        text = RUN_SH.read_text(encoding="utf-8")

        self.assertIn("--line", text)
        self.assertIn("foundry/insula.sh", text)
        self.assertIn("VASO_GPUS=0", text)
        self.assertIn("cd /workspace/experiment/examples/consumers", text)
        self.assertIn('torch) config="vaso_rootfs"', text)
        self.assertIn('jax) config="vaso_jax_rootfs"', text)
        self.assertIn('bazel_config_arg="--config=$config"', text)
        self.assertIn("//hello_cc:hello_cc", text)
        self.assertIn("//hello_cc:hello_cc_test", text)
        self.assertIn("//geom_core:geometry_test", text)
        self.assertIn("//geom_core:camera_test", text)
        self.assertIn("//geom_core:libtorch_cli", text)
        self.assertIn("//geom_core:libtorch_cli_cpu_test", text)
        self.assertIn("//geom_core:libtorch_cli_cuda_test", text)
        self.assertIn("//geom_core:protobuf_coexistence_test", text)
        self.assertIn("//rust_pyo3:rust_pyo3_wheel", text)
        self.assertIn("//rust_pyo3:rust_pyo3_wheel_verifier", text)
        self.assertIn("//rust_pyo3:rust_pyo3_host_toolchain_poison_test", text)
        self.assertIn("//nvshmem_ep:nvshmem_ep_cuda_test", text)
        self.assertIn("//voxel_scatter:voxel_scatter_cpu_test", text)
        self.assertIn("//voxel_scatter:voxel_scatter_cuda_test", text)
        self.assertIn("//cutlass_gemm:cutlass_gemm_cpu_test", text)
        self.assertIn("//cutlass_gemm:cutlass_gemm_cuda_test", text)
        self.assertIn("//gsplat_rasterizer:gsplat_rasterizer", text)
        self.assertIn("//gsplat_rasterizer:gsplat_rasterizer_cuda_test", text)
        self.assertIn("--release-dir", text)
        self.assertIn("INSULA_RELEASE_PYTHON_PREFIX_HOST", text)
        self.assertIn("VASO_ESTATE_ROOT", text)
        self.assertIn("/vaso/lines/$line/cache/bazel/output-base", text)
        self.assertIn("run_release_python_consumers", text)
        self.assertIn("python_consumers.verify_torch", text)
        self.assertIn("python_consumers.verify_jax", text)
        self.assertIn("consumer_default_test_toolchain", MODULE.read_text(encoding="utf-8"))
        self.assertIn("--test_tag_filters=-requires-gpu", text)
        self.assertIn("--keep_going", text)
        self.assertIn("--repository_cache=/vaso/cache/bazel/repository-cache", text)
        self.assertNotIn("http://", text)
        self.assertNotIn("https://", text)

    def test_runner_prints_profile_specific_release_verifier_commands_for_fixture_release_dir(self) -> None:
        test_tmp = Path(os.environ["TEST_TMPDIR"])
        with tempfile.TemporaryDirectory(dir=test_tmp) as raw_tmp:
            tmp = Path(raw_tmp)
            release_dir = tmp / "release-dir"
            release_dir.mkdir()
            (release_dir / "SHA256SUMS").write_text("", encoding="utf-8")

            base_command = [
                "bash",
                str(RUN_SH),
                "--line",
                "cu129",
                "--release-dir",
                str(release_dir),
                "--print-release-commands-for-test",
            ]
            torch_result = subprocess.run(
                [*base_command, "--profile", "torch"],
                cwd=CONSUMER_ROOT,
                capture_output=True,
                text=True,
                check=True,
            )
            jax_result = subprocess.run(
                [*base_command, "--profile", "jax"],
                cwd=CONSUMER_ROOT,
                capture_output=True,
                text=True,
                check=True,
            )

        torch_commands = json.loads(torch_result.stdout)
        jax_commands = json.loads(jax_result.stdout)
        self.assertEqual(len(torch_commands), 1)
        self.assertEqual(len(jax_commands), 1)
        self.assertIn("python_consumers.verify_torch", torch_commands[0])
        self.assertIn("python_consumers.verify_jax", jax_commands[0])
        self.assertIn(str(release_dir), torch_commands[0])
        self.assertIn(str(release_dir), jax_commands[0])
        self.assertIn("--gpus 1", torch_commands[0])
        self.assertIn("--gpus 1", jax_commands[0])
        expected_estate_root = os.environ.get("VASO_ESTATE_ROOT", "/vaso")
        self.assertIn(f"VASO_ESTATE_ROOT={expected_estate_root}", torch_commands[0])
        self.assertIn(f"VASO_ESTATE_ROOT={expected_estate_root}", jax_commands[0])
        # The release runner starts the sandbox with --clearenv, so the module path must be
        # set by the command that runs inside it (after "--"), not on the host runner.
        for command in (torch_commands[0], jax_commands[0]):
            _, sandbox_command = command.split(" -- ", 1)
            self.assertIn("PYTHONPATH=/workspace/experiment/examples/consumers", sandbox_command)
        self.assertIn("INSULA_RELEASE_PYTHON_PREFIX_HOST=", torch_commands[0])

    def test_bcr_manifest_is_sha_pinned_for_consumer_module_set(self) -> None:
        rows: list[dict[str, str]] = []
        for line in MANIFEST.read_text(encoding="utf-8").splitlines():
            if not line or line.startswith("#"):
                continue
            module, version, kind, url, sha256, size = line.split("\t")
            rows.append(
                {
                    "module": module,
                    "version": version,
                    "kind": kind,
                    "url": url,
                    "sha256": sha256,
                    "size": size,
                }
            )

        self.assertTrue(rows)
        for row in rows:
            self.assertRegex(row["sha256"], r"^[0-9a-f]{64}$")
            self.assertRegex(row["size"], r"^[0-9]+$")
            self.assertNotIn("@spack_", row["url"])

        by_module_kind = {(row["module"], row["kind"]) for row in rows}
        for module in (
            "abseil-cpp",
            "cutlass",
            "googletest",
            "pybind11_bazel",
            "fmt",
            "protobuf",
            "eigen",
            "glm",
            "gsplat_cuda",
            "rules_cc",
            "rules_python",
            "platforms",
            "rules_rust",
            "rules_shell",
        ):
            self.assertIn((module, "module"), by_module_kind)
            self.assertIn((module, "source-archive"), by_module_kind)
        self.assertIn(("registry", "metadata"), by_module_kind)

    def test_acquisition_tool_populates_estate_registry_archive_store_and_repository_cache(self) -> None:
        text = ACQUIRE.read_text(encoding="utf-8")

        self.assertIn("sources/bcr/registry", text)
        self.assertIn("sources/bcr/archives/sha256", text)
        self.assertIn("repository-cache/content_addressable/sha256", text)
        self.assertIn("sha256", text)
        self.assertIn("file:///vaso/sources/bcr/archives/sha256", text)
        self.assertRegex(text, r"def\s+acquire\(")
        self.assertRegex(text, r"def\s+rewrite_source_json\(")
        self.assertRegex(text, r"def\s+seed_repository_cache\(")

    def test_release_gate_runs_consumer_conformance_only_in_release_mode(self) -> None:
        text = INSULA_GATE.read_text(encoding="utf-8")
        release_block = text.split('if [[ "$release_mode" == "1" ]]; then', 1)[1]

        self.assertIn('"consumer_conformance"', release_block)
        self.assertIn("examples/consumers/run.sh", release_block)
        self.assertIn("--release-dir \"$release_dir\"", release_block)
        self.assertIn("VASO_PROFILE=\"$profile\"", release_block)
        self.assertIn("INSULA_RELEASE_PYTHON_PREFIX_HOST=$release_python_prefix", release_block)
        self.assertRegex(release_block, r'--line "\$line"')
        self.assertLess(
            release_block.index('"release_gates"'),
            release_block.index('"consumer_conformance"'),
        )


if __name__ == "__main__":
    unittest.main()
