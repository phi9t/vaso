#!/usr/bin/env python3
"""Unit tests for the native jaxlib action driver."""

from __future__ import annotations

import importlib.util
import json
import os
import stat
import subprocess
import sys
import tarfile
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


def temporary_directory():
    base = os.environ.get("TEST_TMPDIR") or os.environ.get("VASO_AGENT_IO_ROOT")
    return tempfile.TemporaryDirectory(dir=base)


def non_shared_temporary_directory():
    base = os.environ.get("TEST_TMPDIR") or os.environ.get("VASO_AGENT_IO_ROOT") or str(Path.cwd())
    return tempfile.TemporaryDirectory(dir=base)


def write_executable(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.lstrip(), encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def load_driver_module():
    if not ACTION_DRIVER.exists():
        raise AssertionError("L5 requires native/jaxlib/action_driver.py")
    spec = importlib.util.spec_from_file_location("jaxlib_action_driver", ACTION_DRIVER)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class JaxlibActionDriverTest(unittest.TestCase):
    def setUp(self) -> None:
        self.driver = load_driver_module()

    def write_rules_ml_toolchain_stanzas(self, source: Path) -> None:
        source.joinpath("third_party").mkdir(parents=True, exist_ok=True)
        source.joinpath("third_party", "xla").mkdir(parents=True, exist_ok=True)
        source.joinpath("WORKSPACE").write_text(
            textwrap.dedent(
                """\
                tf_http_archive(
                    name = "rules_ml_toolchain",
                    sha256 = "40963e4bc262dfa9a43146f610140af0068b023ace8f3c50f1705a7b50de0830",
                    strip_prefix = "rules_ml_toolchain-cad1047facbac4fb3c1124da68bf2cb36c7eb9ac",
                    urls = tf_mirror_urls(
                        "https://github.com/google-ml-infra/rules_ml_toolchain/archive/cad1047facbac4fb3c1124da68bf2cb36c7eb9ac.tar.gz",
                    ),
                )
                """
            ),
            encoding="utf-8",
        )
        source.joinpath("third_party", "xla", "BUILD.bazel").write_text("# xla patch package\n", encoding="utf-8")
        source.joinpath("third_party", "xla", "workspace.bzl").write_text(
            textwrap.dedent(
                """\
                def repo():
                    tf_http_archive(
                        name = "xla",
                        sha256 = XLA_SHA256,
                        strip_prefix = "xla-{commit}".format(commit = XLA_COMMIT),
                        urls = tf_mirror_urls("https://github.com/openxla/xla/archive/{commit}.tar.gz".format(commit = XLA_COMMIT)),
                        patch_file = [
                            # Add any patch files here.
                            # "//third_party/xla:temporary.patch
                            "//third_party/xla:xla_1f3fbb74.patch",
                            "//third_party/xla:xla_4c9ae741.patch",
                            "//third_party/xla:xla_llvm_pr_203230.patch",
                            "//third_party/xla:xla_9b348c6b.patch",
                        ],
                    )
                """
            ),
            encoding="utf-8",
        )
        source.joinpath("MODULE.bazel").write_text(
            textwrap.dedent(
                """\
                archive_override(
                    module_name = "rules_ml_toolchain",
                    integrity = "sha256-QJY+S8Ji36mkMUb2EBQK8AaLAjrOjzxQ8XBae1DeCDA=",
                    strip_prefix = "rules_ml_toolchain-cad1047facbac4fb3c1124da68bf2cb36c7eb9ac",
                    urls = ["https://github.com/google-ml-infra/rules_ml_toolchain/archive/cad1047facbac4fb3c1124da68bf2cb36c7eb9ac.tar.gz"],
                )
                """
            ),
            encoding="utf-8",
        )

    def assert_patch_applies(self, patch: Path, target: Path) -> None:
        check = subprocess.run(
            ["git", "apply", "--check", str(patch)],
            cwd=target,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(check.returncode, 0, check.stderr)
        apply = subprocess.run(
            ["git", "apply", str(patch)],
            cwd=target,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(apply.returncode, 0, apply.stderr)

    def write_prefetched_cuda_cccl(self, prefetched_cccl: Path) -> Path:
        for directory in (
            prefetched_cccl / "thrust" / "thrust",
            prefetched_cccl / "cub" / "cub",
            prefetched_cccl / "libcudacxx" / "include" / "nv",
        ):
            directory.mkdir(parents=True, exist_ok=True)
            (directory / "placeholder.h").write_text("", encoding="utf-8")
        source_header = prefetched_cccl / "libcudacxx" / "include" / "cuda" / "std" / "string_view"
        source_header.parent.mkdir(parents=True, exist_ok=True)
        source_header.write_text(
            textwrap.dedent(
                """\
                _CCCL_TEMPLATE(class _It, class _End)
                _CCCL_HOST_DEVICE basic_string_view(_It, _End) -> basic_string_view<iter_value_t<_It>>;

                #if !_CCCL_COMPILER(NVRTC)
                template <class _CharT, class _Alloc>
                _CCCL_HOST basic_string_view(::std::basic_string<_CharT, ::std::char_traits<_CharT>, _Alloc>)
                  -> basic_string_view<_CharT>;

                template <class _CharT, class _Traits, class _Alloc>
                _CCCL_HOST basic_string_view(::std::basic_string<_CharT, _Traits, _Alloc>) -> basic_string_view<_CharT, _Traits>;
                #endif // !_CCCL_COMPILER(NVRTC)

                #if __cpp_lib_string_view >= 201606L
                template <class _CharT>
                _CCCL_HOST basic_string_view(::std::basic_string_view<_CharT>) -> basic_string_view<_CharT>;

                template <class _CharT, class _Traits>
                _CCCL_HOST basic_string_view(::std::basic_string_view<_CharT, _Traits>) -> basic_string_view<_CharT, _Traits>;
                #endif // __cpp_lib_string_view >= 201606L
                """
            ),
            encoding="utf-8",
        )
        return source_header

    def write_rules_ml_toolchain_source(self, source: Path) -> None:
        source.joinpath("gpu", "cuda").mkdir(parents=True, exist_ok=True)
        source.joinpath("gpu", "cuda", "cuda_redist_versions.bzl").write_text(
            textwrap.dedent(
                """\
                PTX_VERSION_DICT = {
                    "clang": {
                        "20": "8.7",
                        "21": "8.8",
                        "22": "9.0",
                    },
                    # To find, look at https://docs.nvidia.com/cuda/parallel-thread-execution/index.html#release-notes
                    "cuda": {
                    },
                }
                """
            ),
            encoding="utf-8",
        )

    def write_nvshmem_include_sources(self, source: Path) -> Path:
        include = source / "src" / "include"
        include.joinpath("device_host_transport").mkdir(parents=True, exist_ok=True)
        include.joinpath("host").mkdir(parents=True, exist_ok=True)
        include.joinpath("internal", "bootstrap_host").mkdir(parents=True, exist_ok=True)
        include.joinpath("internal", "host_transport").mkdir(parents=True, exist_ok=True)
        include.joinpath("nvshmem.h").write_text(
            textwrap.dedent(
                """\
                #ifndef _NVSHMEM_H_
                #define _NVSHMEM_H_

                #include "non_abi/nvshmem_build_options.h"
                /* NVRTC only compiles device code. Leave out host headers */
                #if not defined __CUDACC_RTC__
                """
            ),
            encoding="utf-8",
        )
        include.joinpath("device_host_transport", "nvshmem_constants.h").write_text(
            textwrap.dedent(
                """\
                #if not defined __CUDACC_RTC__
                #include <limits.h>
                #else
                #include <cuda/std/climits>
                #endif
                #include "non_abi/nvshmem_version.h"

                #define CHANNEL_BUF_SIZE (1 << CHANNEL_BUF_SIZE_LOG)
                #define CHANNEL_BUF_SIZE_LOG 22
                """
            ),
            encoding="utf-8",
        )
        include.joinpath("host", "nvshmem_api.h").write_text(
            textwrap.dedent(
                """\
                #include "device_host/nvshmem_common.cuh"
                #include "device_host_transport/nvshmem_constants.h"
                #include "host/nvshmem_macros.h"
                #include "non_abi/nvshmem_version.h"

                int nvshmemi_init_thread(int requested_thread_support, int *provided_thread_support,
                                         unsigned int bootstrap_flags, nvshmemx_init_attr_t *bootstrap_attr,
                """
            ),
            encoding="utf-8",
        )
        include.joinpath("host", "nvshmemx_api.h").write_text(
            textwrap.dedent(
                """\
                #include <stddef.h>
                #include "device_host_transport/nvshmem_constants.h"
                #include "device_host/nvshmem_common.cuh"
                #include "non_abi/nvshmem_version.h"
                #include "host/nvshmemx_coll_api.h"
                #include "host/nvshmem_macros.h"
                #include "non_abi/nvshmemx_error.h"
                """
            ),
            encoding="utf-8",
        )
        include.joinpath("internal", "bootstrap_host", "nvshmemi_bootstrap.h").write_text(
            textwrap.dedent(
                """\
                #define NVSHMEMI_BOOTSTRAP_H

                #include "internal/bootstrap_host_transport/nvshmemi_bootstrap_defines.h"
                #include "non_abi/nvshmem_version.h"
                /* Version = major * 10000 + minor * 100 + patch*/
                /* ABI Introduced in NVSHMEM 2.8.0 */
                """
            ),
            encoding="utf-8",
        )
        include.joinpath("internal", "host_transport", "transport.h").write_text(
            textwrap.dedent(
                """\
                /* This header, along with the six below, comprise
                 * the ABI for transport modules.
                 */
                #include "bootstrap_host_transport/env_defs_internal.h"
                #include "non_abi/nvshmem_version.h"
                #include "device_host_transport/nvshmem_common_transport.h"
                #include "non_abi/nvshmemx_error.h"
                #include "non_abi/nvshmem_build_options.h"
                #include "internal/host_transport/nvshmemi_transport_defines.h"
                #include "internal/bootstrap_host_transport/nvshmemi_bootstrap_defines.h"

                """
            ),
            encoding="utf-8",
        )
        return source

    def write_xla_thunk_sequence_source(self, source: Path) -> Path:
        header = source / "xla" / "backends" / "gpu" / "runtime" / "thunk.h"
        header.parent.mkdir(parents=True, exist_ok=True)
        header.write_text(
            textwrap.dedent(
                """\
                class ThunkSequence : public std::vector<std::unique_ptr<Thunk>> {
                 public:
                  ThunkSequence() = default;
                  ThunkSequence(ThunkSequence&&) = default;
                  explicit ThunkSequence(std::vector<std::unique_ptr<Thunk>>&& thunks)
                      : std::vector<std::unique_ptr<Thunk>>(std::move(thunks)) {};
                  ThunkSequence(const ThunkSequence&) = delete;

                  ThunkSequence& operator=(ThunkSequence&) = delete;
                  ThunkSequence& operator=(ThunkSequence&&) = default;

                  explicit ThunkSequence(int64_t len)
                """
            ),
            encoding="utf-8",
        )
        return source

    def write_xla_buffer_debug_float_check_source(self, source: Path) -> Path:
        kernel = source / "xla" / "stream_executor" / "cuda" / "buffer_debug_float_check_kernel_cuda.cu.cc"
        kernel.parent.mkdir(parents=True, exist_ok=True)
        kernel.write_text(
            textwrap.dedent(
                """\
                template <typename T>
                __host__ __device__ static constexpr T kInfinity =
                    std::numeric_limits<T>::infinity();

                template <>
                __host__ __device__ constexpr __nv_bfloat16 kInfinity<__nv_bfloat16> =
                    absl::bit_cast<__nv_bfloat16>(kInfinity<Eigen::bfloat16>);
                // - __half lacks std::numeric_limits specialization, and Eigen::half is not a
                // literal type (non-constexpr constructors), so we construct infinity from raw
                // bits.
                template <>
                __host__ __device__ constexpr __half kInfinity<__half> =
                    absl::bit_cast<__half>(uint16_t{0x7C00});
                """
            ),
            encoding="utf-8",
        )
        return kernel

    def test_required_tmpdir_preserves_safe_action_tmpdir(self) -> None:
        with non_shared_temporary_directory() as tmp_text:
            tmp = Path(tmp_text)
            safe_tmp = tmp / "action-tmp"
            with mock.patch.dict(os.environ, {"TMPDIR": str(safe_tmp)}, clear=True):
                self.assertEqual(self.driver._required_tmpdir(), safe_tmp)
            self.assertTrue(safe_tmp.is_dir())

    def test_required_tmpdir_falls_back_to_estate_for_shared_tmp(self) -> None:
        with temporary_directory() as tmp_text:
            tmp = Path(tmp_text)
            vaso_home = tmp / "vaso"
            env = {
                "TMPDIR": "/tmp",
                "VASO_HOME": str(vaso_home),
                "VASO_CUDA_LINE": "cu129",
            }
            with mock.patch.dict(os.environ, env, clear=True):
                self.assertEqual(
                    self.driver._required_tmpdir(),
                    vaso_home / "lines" / "cu129" / "tmp" / "native-actions" / "jaxlib",
                )
            self.assertTrue((vaso_home / "lines" / "cu129" / "tmp" / "native-actions" / "jaxlib").is_dir())

    def test_patches_extracted_jax_source_for_clang_23_ptx_mapping(self) -> None:
        with temporary_directory() as tmp_text:
            tmp = Path(tmp_text)
            source = tmp / "jax-src"
            self.write_rules_ml_toolchain_stanzas(source)

            self.driver._patch_jax_source_for_rootfs_toolchain(source)
            workspace_once = source.joinpath("WORKSPACE").read_text(encoding="utf-8")
            module_once = source.joinpath("MODULE.bazel").read_text(encoding="utf-8")
            xla_workspace_once = source.joinpath("third_party", "xla", "workspace.bzl").read_text(
                encoding="utf-8"
            )
            patch = source / "third_party" / "rules_ml_toolchain-clang23-ptx.patch"
            xla_patch = source / "third_party" / "xla" / "xla_rules_ml_toolchain-clang23-ptx.patch"
            xla_nvshmem_patch = source / "third_party" / "xla" / "xla_nvshmem_generated_headers.patch"
            xla_thunk_patch = source / "third_party" / "xla" / "xla_thunk_sequence_copy_assign.patch"
            xla_buffer_debug_patch = (
                source / "third_party" / "xla" / "xla_buffer_debug_float_check_clang_cuda.patch"
            )

            self.assertTrue(patch.is_file())
            self.assertIn('+        "23": "9.2",', patch.read_text(encoding="utf-8"))
            self.assertTrue(xla_patch.is_file())
            self.assertFalse((source / "third_party" / "xla" / "xla_nvcc_clang23_features.patch").exists())
            self.assertTrue(xla_nvshmem_patch.is_file())
            self.assertTrue(xla_thunk_patch.is_file())
            self.assertTrue(xla_buffer_debug_patch.is_file())
            self.assertIn(
                'patch_file = ["//third_party:rules_ml_toolchain-clang23-ptx.patch"],',
                workspace_once,
            )
            self.assertIn(
                'patches = ["//third_party:rules_ml_toolchain-clang23-ptx.patch"],',
                module_once,
            )
            self.assertIn("patch_strip = 1,", module_once)
            self.assertIn(
                '"//third_party/xla:xla_rules_ml_toolchain-clang23-ptx.patch",',
                xla_workspace_once,
            )
            self.assertNotIn("nvcc_clang23_features.patch", xla_workspace_once)
            self.assertIn(
                '"//third_party/xla:xla_nvshmem_generated_headers.patch",',
                xla_workspace_once,
            )
            self.assertIn(
                '"//third_party/xla:xla_thunk_sequence_copy_assign.patch",',
                xla_workspace_once,
            )
            self.assertIn(
                '"//third_party/xla:xla_buffer_debug_float_check_clang_cuda.patch",',
                xla_workspace_once,
            )

            xla_source = tmp / "xla-source"
            xla_source.joinpath("third_party").mkdir(parents=True)
            xla_source.joinpath("third_party", "absl").mkdir(parents=True)
            xla_source.joinpath("third_party", "nvshmem").mkdir(parents=True)
            xla_source.joinpath("third_party", "py").mkdir(parents=True)
            xla_source.joinpath("third_party", "protobuf").mkdir(parents=True)
            self.write_xla_thunk_sequence_source(xla_source)
            buffer_debug_source = self.write_xla_buffer_debug_float_check_source(xla_source)
            xla_source.joinpath("third_party", "absl", "workspace.bzl").write_text(
                textwrap.dedent(
                    """\
                    def repo():
                        tf_http_archive(
                            name = "com_google_absl",
                            sha256 = ABSL_SHA256,
                            strip_prefix = "abseil-cpp-{commit}".format(commit = ABSL_COMMIT),
                            urls = tf_mirror_urls("https://github.com/abseil/abseil-cpp/archive/{commit}.tar.gz".format(commit = ABSL_COMMIT)),
                            patch_file = [
                                "//third_party/absl:btree.patch",
                                "//third_party/absl:build_dll.patch",
                                "//third_party/absl:endian.patch",
                                "//third_party/absl:append_and_overwrite.patch",
                            ],
                            repo_mapping = {
                                "@google_benchmark": "@com_google_benchmark",
                            },
                        )
                    """
                ),
                encoding="utf-8",
            )
            xla_source.joinpath("third_party", "py", "python_init_rules.bzl").write_text(
                textwrap.dedent(
                    """\
                    def python_init_rules(extra_patches = []):
                        tf_http_archive(
                            name = "com_google_protobuf",
                            patch_file = [
                                "@xla//third_party/protobuf:protobuf.patch",
                                "@xla//third_party/protobuf:protobuf_arena.patch",
                            ],
                            sha256 = "6e09bbc950ba60c3a7b30280210cd285af8d7d8ed5e0a6ed101c72aff22e8d88",
                            strip_prefix = "protobuf-6.31.1",
                            urls = tf_mirror_urls("https://github.com/protocolbuffers/protobuf/archive/refs/tags/v6.31.1.zip"),
                        )
                    """
                ),
                encoding="utf-8",
            )
            xla_source.joinpath("third_party", "nvshmem", "workspace.bzl").write_text(
                textwrap.dedent(
                    """\
                    def repo():
                        tf_http_archive(
                            name = "nvshmem",
                            strip_prefix = "nvshmem_src",
                            sha256 = "2146ff231d9aadd2b11f324c142582f89e3804775877735dc507b4dfd70c788b",
                            urls = tf_mirror_urls("https://developer.download.nvidia.com/compute/redist/nvshmem/3.1.7/source/nvshmem_src_3.1.7-1.txz"),
                            build_file = "//third_party/nvshmem:nvshmem.BUILD",
                            patch_file = ["//third_party/nvshmem:archive.patch"],
                            type = "tar",
                        )
                    """
                ),
                encoding="utf-8",
            )
            xla_source.joinpath("workspace2.bzl").write_text(
                textwrap.dedent(
                    """\
                    def _tf_repositories():
                        maybe(
                            tf_http_archive,
                            name = "com_google_protobuf",
                            patch_file = [
                                "//third_party/protobuf:protobuf.patch",
                                "//third_party/protobuf:protobuf_arena.patch",
                            ],
                            sha256 = "61e5e5b7f29c4a719d9691b97c2b8937b8bd5ab1b6b7586f3f55934011806280",
                            strip_prefix = "protobuf-34.1",
                            urls = tf_mirror_urls("https://github.com/protocolbuffers/protobuf/releases/download/v34.1/protobuf-34.1.zip"),
                        )
                    """
                ),
                encoding="utf-8",
            )
            xla_source.joinpath("workspace3.bzl").write_text(
                textwrap.dedent(
                    """\
                    def repo():
                        tf_http_archive(
                            name = "rules_ml_toolchain",
                            sha256 = "40963e4bc262dfa9a43146f610140af0068b023ace8f3c50f1705a7b50de0830",
                            strip_prefix = "rules_ml_toolchain-cad1047facbac4fb3c1124da68bf2cb36c7eb9ac",
                            urls = tf_mirror_urls(
                                "https://github.com/google-ml-infra/rules_ml_toolchain/archive/cad1047facbac4fb3c1124da68bf2cb36c7eb9ac.tar.gz",
                            ),
                        )
                    """
                ),
                encoding="utf-8",
            )
            xla_source.joinpath("MODULE.bazel").write_text(
                textwrap.dedent(
                    """\
                    single_version_override(
                        module_name = "abseil-cpp",
                        patch_strip = 1,
                        patches = [
                            "//third_party/absl:btree.patch",
                            "//third_party/absl:build_dll.patch",
                            "//third_party/absl:endian.patch",
                            "//third_party/absl:append_and_overwrite.patch",
                        ],
                    )

                    single_version_override(
                        module_name = "protobuf",
                        patch_strip = 1,
                        patches = [
                            "//third_party/protobuf:protobuf_arena.patch",
                            "//third_party/protobuf:fix_message_lite_incomplete_type.patch",
                            # The following patch is needed to fix package loading for `bazel query "deps(//xla/...)"`.
                            "//third_party/protobuf:fix_python_dist_package.patch",
                        ],
                        version = "32.1",
                    )
                    """
                ),
                encoding="utf-8",
            )
            self.assert_patch_applies(xla_patch, xla_source)
            self.assert_patch_applies(xla_nvshmem_patch, xla_source)
            self.assert_patch_applies(xla_thunk_patch, xla_source)
            self.assert_patch_applies(xla_buffer_debug_patch, xla_source)
            self.assertIn(
                'patch_file = ["//third_party:rules_ml_toolchain-clang23-ptx.patch"],',
                xla_source.joinpath("workspace3.bzl").read_text(encoding="utf-8"),
            )
            nested_patch = xla_source / "third_party" / "rules_ml_toolchain-clang23-ptx.patch"
            self.assertTrue(nested_patch.is_file())
            nested_patch_text = nested_patch.read_text(encoding="utf-8")
            self.assertIn("+++ b/gpu/cuda/cuda_redist_versions.bzl", nested_patch_text)
            self.assertNotIn("++++ b/gpu/cuda/cuda_redist_versions.bzl", nested_patch_text)

            rules_source = tmp / "rules-ml-toolchain-source"
            self.write_rules_ml_toolchain_source(rules_source)
            self.assert_patch_applies(nested_patch, rules_source)
            self.assertIn(
                '        "23": "9.2",',
                rules_source.joinpath("gpu", "cuda", "cuda_redist_versions.bzl").read_text(encoding="utf-8"),
            )
            nvshmem_headers_patch = xla_source / "third_party" / "nvshmem" / "generated_headers.patch"
            self.assertTrue(nvshmem_headers_patch.is_file())
            self.assertFalse((xla_source / "third_party" / "absl" / "nvcc_clang23_features.patch").exists())
            self.assertFalse((xla_source / "third_party" / "protobuf" / "nvcc_clang23_features.patch").exists())
            self.assertNotIn(
                "nvcc_clang23_features.patch",
                xla_source.joinpath("third_party", "absl", "workspace.bzl").read_text(encoding="utf-8"),
            )
            self.assertNotIn("nvcc_clang23_features.patch", xla_source.joinpath("workspace2.bzl").read_text(encoding="utf-8"))
            self.assertNotIn("nvcc_clang23_features.patch", xla_source.joinpath("MODULE.bazel").read_text(encoding="utf-8"))
            self.assertIn(
                '"//third_party/nvshmem:generated_headers.patch",',
                xla_source.joinpath("third_party", "nvshmem", "workspace.bzl").read_text(encoding="utf-8"),
            )
            nvshmem_source = self.write_nvshmem_include_sources(tmp / "nvshmem-source")
            self.assert_patch_applies(nvshmem_headers_patch, nvshmem_source)
            thunk_source = self.write_xla_thunk_sequence_source(tmp / "xla-thunk-source")
            self.assert_patch_applies(xla_thunk_patch, thunk_source)
            patched_buffer_debug = buffer_debug_source.read_text(encoding="utf-8")
            self.assertNotIn("absl::bit_cast<__nv_bfloat16>", patched_buffer_debug)
            self.assertNotIn("absl::bit_cast<__half>", patched_buffer_debug)
            self.assertIn("__nv_bfloat16_raw{0x7F80U}", patched_buffer_debug)
            self.assertIn("__half_raw{0x7C00U}", patched_buffer_debug)
            for header in (
                "device_host_transport/nvshmem_constants.h",
                "host/nvshmem_api.h",
                "host/nvshmemx_api.h",
                "internal/bootstrap_host/nvshmemi_bootstrap.h",
            ):
                self.assertIn(
                    '#include "third_party/nvshmem/non_abi/nvshmem_version.h"',
                    nvshmem_source.joinpath("src", "include", header).read_text(encoding="utf-8"),
                )
            self.assertIn(
                '#include "third_party/nvshmem/non_abi/nvshmem_build_options.h"',
                nvshmem_source.joinpath("src", "include", "nvshmem.h").read_text(encoding="utf-8"),
            )
            self.assertIn(
                '#include "third_party/nvshmem/non_abi/nvshmem_build_options.h"',
                nvshmem_source.joinpath("src", "include", "internal", "host_transport", "transport.h").read_text(
                    encoding="utf-8"
                ),
            )
            thunk_text = thunk_source.joinpath(
                "xla", "backends", "gpu", "runtime", "thunk.h"
            ).read_text(encoding="utf-8")
            self.assertIn("ThunkSequence& operator=(const ThunkSequence&) = delete;", thunk_text)
            self.assertNotIn("ThunkSequence& operator=(ThunkSequence&) = delete;", thunk_text)

            self.driver._patch_jax_source_for_rootfs_toolchain(source)
            self.assertEqual(workspace_once, source.joinpath("WORKSPACE").read_text(encoding="utf-8"))
            self.assertEqual(module_once, source.joinpath("MODULE.bazel").read_text(encoding="utf-8"))
            self.assertEqual(
                xla_workspace_once,
                source.joinpath("third_party", "xla", "workspace.bzl").read_text(encoding="utf-8"),
            )

    def make_prefixes(self, root: Path) -> tuple[dict[str, Path], list[str]]:
        prefixes = {key: root / "prefixes" / key for key in self.driver.PREFIX_KEYS}
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
                            "JAX_RELEASE",
                            "CC",
                            "CXX",
                            "CUDA_HOME",
                            "LOCAL_CUDA_PATH",
                            "LOCAL_CCCL_PATH",
                            "LOCAL_CUDNN_PATH",
                            "LOCAL_NCCL_PATH",
                            "LOCAL_NVSHMEM_PATH",
                            "HERMETIC_CUDA_VERSION",
                        )
                    }},
                }}, sort_keys=True) + "\\n")
            if argv[:2] == ["build/build.py", "build"]:
                if "--configure_only" in argv:
                    (Path.cwd() / ".jax_configure.bazelrc").write_text(
                        "# fake configure output\\n",
                        encoding="utf-8",
                    )
                    raise SystemExit(0)
                dist = Path.cwd() / "dist"
                dist.mkdir(parents=True, exist_ok=True)
                for wheel in (
                    "jaxlib-0.10.2-{FIXTURE_PYTHON_ABI}-{FIXTURE_PYTHON_ABI}-linux_x86_64.whl",
                    "jax_cuda13_plugin-0.10.2-py3-none-manylinux_2_28_x86_64.whl",
                    "jax_cuda13_pjrt-0.10.2-py3-none-manylinux_2_28_x86_64.whl",
                ):
                    (dist / wheel).write_text("fake " + wheel + "\\n", encoding="utf-8")
                raise SystemExit(0)
            if argv[:3] == ["-m", "pip", "install"]:
                prefix_arg = argv[argv.index("--prefix") + 1]
                site_packages = Path(prefix_arg) / {str(FIXTURE_SITE_PACKAGES)!r}
                (site_packages / "jaxlib").mkdir(parents=True, exist_ok=True)
                (site_packages / "jaxlib" / "__init__.py").write_text(
                    "__version__ = '0.10.2'\\n",
                    encoding="utf-8",
                )
                raise SystemExit(0)
            print("unexpected fake python argv: " + repr(argv), file=sys.stderr)
            raise SystemExit(97)
            """
        ).lstrip()
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
        package_prefix("py-numpy", "numpy")

        bazel_log = root / "fake-bazel.log"
        fake_bazel = textwrap.dedent(
            f"""\
            #!/usr/bin/python3
            import json
            import os
            import sys
            from pathlib import Path

            argv = sys.argv[1:]
            log = Path({str(bazel_log)!r})
            log.parent.mkdir(parents=True, exist_ok=True)
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
                            "JAX_RELEASE",
                            "CC",
                            "CXX",
                            "LOCAL_CUDA_PATH",
                            "LOCAL_CUDNN_PATH",
                            "LOCAL_NCCL_PATH",
                            "LOCAL_NVSHMEM_PATH",
                            "HERMETIC_CUDA_VERSION",
                        )
                    }},
                }}, sort_keys=True) + "\\n")
            for arg in argv:
                if arg.startswith("--experimental_repository_resolved_file="):
                    Path(arg.split("=", 1)[1]).write_text(
                        "# fake resolved repositories\\n",
                        encoding="utf-8",
                    )
            raise SystemExit(0)
            """
        ).lstrip()
        write_executable(prefixes["bazel"] / "bin" / "bazel", fake_bazel)
        for rel in ("bin/clang", "bin/clang++", "bin/ld.lld", "bin/llvm-config"):
            write_executable(prefixes["llvm"] / rel, "#!/bin/sh\n")
        for key, rels in {
            "cuda": ("bin/nvcc", "bin/ptxas", "include/cuda.h", "lib64/libcudart.so", "nvvm/libdevice/libdevice.10.bc"),
            "cudnn": ("include/cudnn.h", "lib64/libcudnn.so"),
            "nccl": ("include/nccl.h", "lib/libnccl.so"),
            "nvshmem": ("include/nvshmem.h", "lib/libnvshmem_host.so"),
            "xxd-standalone": ("bin/xxd",),
        }.items():
            for rel in rels:
                path = prefixes[key] / rel
                path.parent.mkdir(parents=True, exist_ok=True)
                if "/bin/" in rel:
                    write_executable(path, "#!/bin/sh\n")
                else:
                    path.write_text("", encoding="utf-8")

        prefix_args = []
        prefix_file_dir = root / "prefix-files"
        prefix_file_dir.mkdir()
        for key, path in prefixes.items():
            prefix_file = prefix_file_dir / f"{key}.txt"
            prefix_file.write_text(str(path) + "\n", encoding="utf-8")
            prefix_args.extend(["--prefix-file", f"{key}={prefix_file}"])
        return prefixes, prefix_args

    def test_cuda_redist_view_copies_and_patches_cccl_string_view_for_clang_cuda(self) -> None:
        with temporary_directory() as tmp_text:
            tmp = Path(tmp_text)
            prefixes, _prefix_args = self.make_prefixes(tmp)
            source_header = prefixes["cuda"] / "include" / "cuda" / "std" / "string_view"
            source_header.parent.mkdir(parents=True, exist_ok=True)
            source_header.write_text(
                textwrap.dedent(
                    """\
                    _CCCL_TEMPLATE(class _It, class _End)
                    _CCCL_HOST_DEVICE basic_string_view(_It, _End) -> basic_string_view<iter_value_t<_It>>;

                    #if !_CCCL_COMPILER(NVRTC)
                    template <class _CharT, class _Alloc>
                    _CCCL_HOST basic_string_view(::std::basic_string<_CharT, ::std::char_traits<_CharT>, _Alloc>)
                      -> basic_string_view<_CharT>;

                    template <class _CharT, class _Traits, class _Alloc>
                    _CCCL_HOST basic_string_view(::std::basic_string<_CharT, _Traits, _Alloc>) -> basic_string_view<_CharT, _Traits>;
                    #endif // !_CCCL_COMPILER(NVRTC)

                    #if __cpp_lib_string_view >= 201606L
                    template <class _CharT>
                    _CCCL_HOST basic_string_view(::std::basic_string_view<_CharT>) -> basic_string_view<_CharT>;

                    template <class _CharT, class _Traits>
                    _CCCL_HOST basic_string_view(::std::basic_string_view<_CharT, _Traits>) -> basic_string_view<_CharT, _Traits>;
                    #endif // __cpp_lib_string_view >= 201606L

                    template <class _CharT>
                    _CCCL_HOST_API ::std::basic_ostream<_CharT>& operator<<(::std::basic_ostream<_CharT>&, basic_string_view<_CharT>);
                    """
                ),
                encoding="utf-8",
            )

            views = self.driver._materialize_jax_local_redist_views(
                {key: str(path) for key, path in prefixes.items()},
                tmp / "work",
            )

            include_view = Path(views["cuda"]) / "include"
            self.assertTrue(include_view.is_dir())
            self.assertFalse(include_view.is_symlink())
            self.assertTrue((Path(views["cuda"]) / "lib").is_symlink())
            self.assertEqual(
                source_header.read_text(encoding="utf-8").count("_CCCL_HOST basic_string_view"),
                4,
            )
            patched = (include_view / "cuda" / "std" / "string_view").read_text(encoding="utf-8")
            self.assertNotIn("_CCCL_HOST basic_string_view(::std::basic_string", patched)
            self.assertNotIn("_CCCL_HOST basic_string_view(::std::basic_string_view", patched)
            self.assertEqual(patched.count("_CCCL_HOST_DEVICE basic_string_view"), 5)
            self.assertIn("_CCCL_HOST_API ::std::basic_ostream", patched)

            views_again = self.driver._materialize_jax_local_redist_views(
                {key: str(path) for key, path in prefixes.items()},
                tmp / "work",
            )
            self.assertEqual(views_again, views)
            patched_again = (include_view / "cuda" / "std" / "string_view").read_text(encoding="utf-8")
            self.assertEqual(patched_again, patched)

    def test_jax_local_redist_uses_patched_prefetched_cuda_cccl_for_clang_cuda(self) -> None:
        with temporary_directory() as tmp_text:
            tmp = Path(tmp_text)
            prefixes, _prefix_args = self.make_prefixes(tmp)
            build_work = tmp / "work"
            prefetched_cccl = build_work / "bazel-output-base" / "external" / "cuda_cccl"
            source_header = self.write_prefetched_cuda_cccl(prefetched_cccl)
            plan_doc = {
                "build_env": {
                    "CUDA_HOME": str(prefixes["cuda"]),
                    "LOCAL_CUDA_PATH": str(prefixes["cuda"]),
                },
                "build_py_args": [
                    "--bazel_options=--repo_env=LOCAL_CUDA_PATH=" + str(prefixes["cuda"]),
                    "//jaxlib/tools:jax_cuda13_pjrt_wheel",
                ],
            }

            effective = self.driver._plan_doc_with_jax_local_redist_views(
                plan_doc,
                {key: str(path) for key, path in prefixes.items()},
                build_work,
                require_cccl=True,
            )

            cccl_view = Path(effective["jax_local_redist_prefixes"]["cccl"])
            self.assertEqual(effective["build_env"]["LOCAL_CCCL_PATH"], str(cccl_view))
            self.assertIn(
                "--bazel_options=--repo_env=LOCAL_CCCL_PATH=" + str(cccl_view),
                effective["build_py_args"],
            )
            self.assertFalse((cccl_view / "libcudacxx").is_symlink())
            self.assertTrue((cccl_view / "thrust").is_dir())
            self.assertTrue((cccl_view / "cub").is_dir())
            self.assertEqual(
                source_header.read_text(encoding="utf-8").count("_CCCL_HOST basic_string_view"),
                4,
            )
            patched = (cccl_view / "libcudacxx" / "include" / "cuda" / "std" / "string_view").read_text(
                encoding="utf-8"
            )
            self.assertNotIn("_CCCL_HOST basic_string_view(::std::basic_string", patched)
            self.assertNotIn("_CCCL_HOST basic_string_view(::std::basic_string_view", patched)
            self.assertEqual(patched.count("_CCCL_HOST_DEVICE basic_string_view"), 5)

    def test_jax_local_redist_can_materialize_cuda_cccl_from_repository_cache(self) -> None:
        with temporary_directory() as tmp_text:
            tmp = Path(tmp_text)
            archive_source = tmp / "archive-source" / self.driver.JAX_LOCAL_CCCL_ARCHIVE_STRIP_PREFIX
            source_header = self.write_prefetched_cuda_cccl(archive_source)
            (archive_source / "AGENTS.md").write_text("cccl agent guidance\n", encoding="utf-8")
            (archive_source / ".github").mkdir()
            (archive_source / ".github" / "copilot-instructions.md").symlink_to("../AGENTS.md")
            archive = tmp / "cache" / "content_addressable" / "sha256" / "placeholder" / "file"
            archive.parent.mkdir(parents=True)
            with tarfile.open(archive, "w:gz") as handle:
                handle.add(archive_source, arcname=self.driver.JAX_LOCAL_CCCL_ARCHIVE_STRIP_PREFIX)
            digest = self.driver._file_sha256(archive)
            cache_file = tmp / "cache" / "content_addressable" / "sha256" / digest / "file"
            cache_file.parent.mkdir(parents=True)
            archive.replace(cache_file)
            build_work = tmp / "work"

            with mock.patch.object(self.driver, "JAX_LOCAL_CCCL_ARCHIVE_SHA256", digest):
                with mock.patch.dict(os.environ, {"VASO_BAZEL_REPOSITORY_CACHE": str(tmp / "cache")}):
                    view = self.driver._materialize_cuda_cccl_view(
                        build_work / "bazel-output-base" / "external" / "cuda_cccl",
                        build_work / "jax-local-redists" / "cccl",
                        required=True,
                    )

            self.assertIsNotNone(view)
            cccl_view = Path(view)
            self.assertTrue((cccl_view / "thrust").is_dir())
            self.assertTrue((cccl_view / "cub").is_dir())
            self.assertEqual(
                source_header.read_text(encoding="utf-8").count("_CCCL_HOST basic_string_view"),
                4,
            )
            patched = (cccl_view / "libcudacxx" / "include" / "cuda" / "std" / "string_view").read_text(
                encoding="utf-8"
            )
            self.assertNotIn("_CCCL_HOST basic_string_view(::std::basic_string", patched)
            self.assertEqual(patched.count("_CCCL_HOST_DEVICE basic_string_view"), 5)

    def test_jax_local_redist_uses_repository_cache_when_prefetched_cuda_cccl_is_partial(self) -> None:
        with temporary_directory() as tmp_text:
            tmp = Path(tmp_text)
            archive_source = tmp / "archive-source" / self.driver.JAX_LOCAL_CCCL_ARCHIVE_STRIP_PREFIX
            self.write_prefetched_cuda_cccl(archive_source)
            archive = tmp / "cache" / "content_addressable" / "sha256" / "placeholder" / "file"
            archive.parent.mkdir(parents=True)
            with tarfile.open(archive, "w:gz") as handle:
                handle.add(archive_source, arcname=self.driver.JAX_LOCAL_CCCL_ARCHIVE_STRIP_PREFIX)
            digest = self.driver._file_sha256(archive)
            cache_file = tmp / "cache" / "content_addressable" / "sha256" / digest / "file"
            cache_file.parent.mkdir(parents=True)
            archive.replace(cache_file)
            build_work = tmp / "work"
            partial_external = build_work / "bazel-output-base" / "external" / "cuda_cccl"
            (partial_external / "libcudacxx").mkdir(parents=True)

            with mock.patch.object(self.driver, "JAX_LOCAL_CCCL_ARCHIVE_SHA256", digest):
                with mock.patch.dict(os.environ, {"VASO_BAZEL_REPOSITORY_CACHE": str(tmp / "cache")}):
                    view = self.driver._materialize_cuda_cccl_view(
                        partial_external,
                        build_work / "jax-local-redists" / "cccl",
                        required=True,
                    )

            cccl_view = Path(view)
            self.assertTrue((cccl_view / "thrust").is_dir())
            self.assertTrue((cccl_view / "cub").is_dir())
            self.assertTrue((cccl_view / "libcudacxx").is_dir())

    def test_jax_local_redist_uses_repository_cache_when_prefetched_cuda_cccl_points_to_view(self) -> None:
        with temporary_directory() as tmp_text:
            tmp = Path(tmp_text)
            archive_source = tmp / "archive-source" / self.driver.JAX_LOCAL_CCCL_ARCHIVE_STRIP_PREFIX
            self.write_prefetched_cuda_cccl(archive_source)
            archive = tmp / "cache" / "content_addressable" / "sha256" / "placeholder" / "file"
            archive.parent.mkdir(parents=True)
            with tarfile.open(archive, "w:gz") as handle:
                handle.add(archive_source, arcname=self.driver.JAX_LOCAL_CCCL_ARCHIVE_STRIP_PREFIX)
            digest = self.driver._file_sha256(archive)
            cache_file = tmp / "cache" / "content_addressable" / "sha256" / digest / "file"
            cache_file.parent.mkdir(parents=True)
            archive.replace(cache_file)

            build_work = tmp / "work"
            destination = build_work / "jax-local-redists" / "cccl"
            self.write_prefetched_cuda_cccl(destination)
            prefetched_cccl = build_work / "bazel-output-base" / "external" / "cuda_cccl"
            prefetched_cccl.mkdir(parents=True)
            for name in self.driver.JAX_LOCAL_CCCL_SOURCE_DIRS:
                (prefetched_cccl / name).symlink_to(destination / name, target_is_directory=True)

            with mock.patch.object(self.driver, "JAX_LOCAL_CCCL_ARCHIVE_SHA256", digest):
                with mock.patch.dict(os.environ, {"VASO_BAZEL_REPOSITORY_CACHE": str(tmp / "cache")}):
                    view = self.driver._materialize_cuda_cccl_view(
                        prefetched_cccl,
                        destination,
                        required=True,
                    )

            cccl_view = Path(view)
            for name in self.driver.JAX_LOCAL_CCCL_SOURCE_DIRS:
                self.assertTrue((cccl_view / name).is_dir())
                self.assertFalse((cccl_view / name).is_symlink())

    def test_tar_member_validation_rejects_symlink_that_escapes_top_level(self) -> None:
        member = tarfile.TarInfo(
            f"{self.driver.JAX_LOCAL_CCCL_ARCHIVE_STRIP_PREFIX}/.github/copilot-instructions.md"
        )
        member.type = tarfile.SYMTYPE
        member.linkname = "../../outside"

        with self.assertRaises(SystemExit) as raised:
            self.driver._validate_tar_member(member)

        self.assertIn("unsafe link in JAX source archive", str(raised.exception))

    def test_execute_mode_runs_build_py_installs_wheels_and_records_metadata(self) -> None:
        with temporary_directory() as tmp_text:
            tmp = Path(tmp_text)
            prefixes, prefix_args = self.make_prefixes(tmp)
            source = tmp / "jax-src"
            source.joinpath("build").mkdir(parents=True)
            source.joinpath("build", "build.py").write_text("# fake jax build.py\n", encoding="utf-8")
            source.joinpath("jaxlib").mkdir()
            source.joinpath("jaxlib", "__init__.py").write_text("", encoding="utf-8")
            self.write_rules_ml_toolchain_stanzas(source)
            source_archive = tmp / "jax-v0.10.2.tar.gz"
            with tarfile.open(source_archive, "w:gz") as archive:
                archive.add(source, arcname="jax-jax-v0.10.2")
            rootfs_manifest = tmp / "rootfs-bundle.json"
            rootfs_manifest.write_text('{"schema_version": 2}\n', encoding="utf-8")
            prefix_out = tmp / "out-prefix"
            wheelhouse_out = tmp / "out" / "wheels"
            wheel_manifest_out = tmp / "out" / "wheel_manifest.json"
            build_plan_out = tmp / "out" / "plan.json"
            metadata_out = tmp / "out" / "metadata.json"
            marker_out = tmp / "out" / "result.txt"

            env = {
                "VASO_IN_INSULA": "1",
                "VASO_ROOTFS_BUNDLE_MANIFEST": str(rootfs_manifest),
                "VASO_CUDA_LINE": "cu130",
                "VASO_HOME": str(tmp / "vaso"),
                "TMPDIR": "/tmp",
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
                "--wheelhouse-out",
                str(wheelhouse_out),
                "--wheel-manifest-out",
                str(wheel_manifest_out),
                "--source-anchor",
                str(source / "build" / "build.py"),
                "--source-archive",
                str(source_archive),
                "--python-abi",
                FIXTURE_PYTHON_ABI,
                "--token",
                self.driver.REQUIRED_TOKEN,
                "--execute",
            ]
            argv.extend(prefix_args)
            real_estate_build_work_dir = self.driver._estate_build_work_dir

            def prepare_build_work(args, prefixes_by_name):
                build_work = real_estate_build_work_dir(args, prefixes_by_name)
                self.write_prefetched_cuda_cccl(build_work / "bazel-output-base" / "external" / "cuda_cccl")
                return build_work

            with mock.patch.dict(os.environ, env, clear=True):
                with mock.patch.object(self.driver, "_estate_build_work_dir", side_effect=prepare_build_work):
                    self.assertEqual(self.driver.main(argv), 0)

            build_work_roots = list((tmp / "vaso" / "lines" / "cu130" / "work" / "jaxlib").iterdir())
            self.assertEqual(len(build_work_roots), 1)
            build_source = build_work_roots[0] / "src"
            self.assertTrue((build_source / "build" / "build.py").is_file())
            self.assertEqual(
                (prefix_out / FIXTURE_SITE_PACKAGES / "jaxlib" / "__init__.py").read_text(encoding="utf-8"),
                "__version__ = '0.10.2'\n",
            )

            wheels = json.loads(wheel_manifest_out.read_text(encoding="utf-8"))["wheels"]
            self.assertEqual(
                [item["name"] for item in wheels],
                [
                    "jax_cuda13_pjrt-0.10.2-py3-none-manylinux_2_28_x86_64.whl",
                    "jax_cuda13_plugin-0.10.2-py3-none-manylinux_2_28_x86_64.whl",
                    f"jaxlib-0.10.2-{FIXTURE_PYTHON_ABI}-{FIXTURE_PYTHON_ABI}-linux_x86_64.whl",
                ],
            )
            for item in wheels:
                self.assertTrue((wheelhouse_out / item["name"]).is_file())

            metadata = json.loads(metadata_out.read_text(encoding="utf-8"))
            self.assertEqual(metadata["mode"], "execute")
            self.assertTrue(metadata["will_build"])
            self.assertTrue(metadata["token_present"])
            self.assertEqual(metadata["build_work"], str(build_work_roots[0]))
            self.assertEqual(metadata["source"], str(build_source))
            self.assertEqual(metadata["rootfs_manifest"], str(rootfs_manifest))
            self.assertNotIn("py-scipy", metadata["input_prefixes"])
            self.assertNotIn("py-ml-dtypes", metadata["input_prefixes"])
            self.assertIn("prefix installation completed", marker_out.read_text(encoding="utf-8"))

            fake_log = [json.loads(line) for line in (tmp / "fake-python.log").read_text(encoding="utf-8").splitlines()]
            self.assertEqual(fake_log[0]["argv"][:2], ["build/build.py", "build"])
            self.assertIn("--bazel_options=--config=cuda_libraries_from_stubs", fake_log[0]["argv"])
            self.assertIn("--bazel_startup_options=--nohome_rc", fake_log[0]["argv"])
            self.assertEqual(fake_log[0]["cwd"], str(build_source))
            self.assertEqual(fake_log[0]["env"]["PIP_NO_INDEX"], "1")
            self.assertEqual(fake_log[0]["env"]["JAX_RELEASE"], "1")
            self.assertEqual(fake_log[0]["env"]["CC"], str(prefixes["llvm"] / "bin" / "clang"))
            self.assertEqual(fake_log[0]["env"]["CXX"], str(prefixes["llvm"] / "bin" / "clang++"))
            self.assertTrue(fake_log[0]["env"]["LOCAL_CUDA_PATH"].endswith("/jax-local-redists/cuda"))
            self.assertEqual(
                (Path(fake_log[0]["env"]["LOCAL_CUDA_PATH"]) / "lib").resolve(),
                (prefixes["cuda"] / "lib64").resolve(),
            )
            self.assertEqual(fake_log[0]["env"]["CUDA_HOME"], fake_log[0]["env"]["LOCAL_CUDA_PATH"])
            self.assertTrue(fake_log[0]["env"]["LOCAL_CCCL_PATH"].endswith("/jax-local-redists/cccl"))
            self.assertIn(
                "--bazel_options=--repo_env=LOCAL_CCCL_PATH=" + fake_log[0]["env"]["LOCAL_CCCL_PATH"],
                fake_log[0]["argv"],
            )
            self.assertTrue(fake_log[0]["env"]["LOCAL_CUDNN_PATH"].endswith("/jax-local-redists/cudnn"))
            self.assertTrue(fake_log[0]["env"]["LOCAL_NCCL_PATH"].endswith("/jax-local-redists/nccl"))
            self.assertTrue(fake_log[0]["env"]["LOCAL_NVSHMEM_PATH"].endswith("/jax-local-redists/nvshmem"))
            self.assertEqual(fake_log[0]["env"]["HERMETIC_CUDA_VERSION"], "13.0.3")
            action_tmp = tmp / "vaso" / "lines" / "cu130" / "tmp" / "native-actions" / "jaxlib"
            self.assertEqual(fake_log[0]["env"]["HOME"], str(action_tmp / "jaxlib-home"))
            self.assertEqual(fake_log[0]["env"]["PIP_CACHE_DIR"], str(action_tmp / "pip-cache"))

    def test_prefetch_mode_runs_configure_then_nested_bazel_without_offline_flag(self) -> None:
        with temporary_directory() as tmp_text:
            tmp = Path(tmp_text)
            prefixes, prefix_args = self.make_prefixes(tmp)
            source = tmp / "jax-src"
            source.joinpath("build").mkdir(parents=True)
            source.joinpath("build", "build.py").write_text("# fake jax build.py\n", encoding="utf-8")
            source.joinpath("jaxlib").mkdir()
            source.joinpath("jaxlib", "__init__.py").write_text("", encoding="utf-8")
            self.write_rules_ml_toolchain_stanzas(source)
            source_archive = tmp / "jax-v0.10.2.tar.gz"
            with tarfile.open(source_archive, "w:gz") as archive:
                archive.add(source, arcname="jax-jax-v0.10.2")
            rootfs_manifest = tmp / "rootfs-bundle.json"
            rootfs_manifest.write_text('{"schema_version": 2}\n', encoding="utf-8")
            build_plan_out = tmp / "out" / "plan.json"
            metadata_out = tmp / "out" / "metadata.json"
            marker_out = tmp / "out" / "result.txt"
            prefetch_log_out = tmp / "out" / "nested-prefetch.log"

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
                "--build-plan-out",
                str(build_plan_out),
                "--provider-metadata-out",
                str(metadata_out),
                "--result-marker-out",
                str(marker_out),
                "--prefetch-log-out",
                str(prefetch_log_out),
                "--source-anchor",
                str(source / "build" / "build.py"),
                "--source-archive",
                str(source_archive),
                "--python-abi",
                FIXTURE_PYTHON_ABI,
                "--token",
                self.driver.REQUIRED_TOKEN,
                "--execute",
                "--prefetch-nested-bazel",
            ]
            argv.extend(prefix_args)
            with mock.patch.dict(os.environ, env, clear=True):
                self.assertEqual(self.driver.main(argv), 0)

            build_work_roots = list((tmp / "vaso" / "lines" / "cu130" / "work" / "jaxlib").iterdir())
            self.assertEqual(len(build_work_roots), 1)
            build_source = build_work_roots[0] / "src"
            self.assertTrue((build_source / "build" / "build.py").is_file())
            self.assertTrue((build_source / ".jax_configure.bazelrc").is_file())
            self.assertTrue(prefetch_log_out.is_file())

            fake_python = [
                json.loads(line)
                for line in (tmp / "fake-python.log").read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual(fake_python[0]["argv"][:2], ["build/build.py", "build"])
            self.assertIn("--configure_only", fake_python[0]["argv"])
            self.assertIn("--bazel_path=" + str(prefixes["bazel"] / "bin" / "bazel"), fake_python[0]["argv"])
            self.assertNotIn("--bazel_options=--repository_disable_download", fake_python[0]["argv"])
            self.assertEqual(fake_python[0]["cwd"], str(build_source))
            self.assertEqual(fake_python[0]["env"]["PIP_NO_INDEX"], "1")
            self.assertTrue(fake_python[0]["env"]["LOCAL_CUDA_PATH"].endswith("/jax-local-redists/cuda"))
            self.assertEqual(
                (Path(fake_python[0]["env"]["LOCAL_CUDA_PATH"]) / "lib").resolve(),
                (prefixes["cuda"] / "lib64").resolve(),
            )
            self.assertEqual(fake_python[0]["env"]["CUDA_HOME"], fake_python[0]["env"]["LOCAL_CUDA_PATH"])

            fake_bazel = [
                json.loads(line)
                for line in (tmp / "fake-bazel.log").read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual(fake_bazel[0]["argv"][:2], ["--nohome_rc", "--nosystem_rc"])
            self.assertEqual(fake_bazel[0]["argv"][3], "build")
            self.assertTrue(fake_bazel[0]["argv"][2].startswith("--output_base="))
            self.assertTrue(fake_bazel[0]["argv"][2].endswith("/bazel-output-base"))
            self.assertIn("--nobuild", fake_bazel[0]["argv"])
            self.assertIn("--config=cuda_libraries_from_stubs", fake_bazel[0]["argv"])
            self.assertNotIn("--repository_disable_download", fake_bazel[0]["argv"])
            local_cuda_arg = next(
                arg for arg in fake_bazel[0]["argv"] if arg.startswith("--repo_env=LOCAL_CUDA_PATH=")
            )
            self.assertTrue(local_cuda_arg.endswith("/jax-local-redists/cuda"))
            self.assertEqual(
                (Path(local_cuda_arg.split("LOCAL_CUDA_PATH=", 1)[1]) / "lib").resolve(),
                (prefixes["cuda"] / "lib64").resolve(),
            )
            self.assertTrue(
                any(
                    arg.startswith("--repo_env=LOCAL_CUDNN_PATH=")
                    and arg.endswith("/jax-local-redists/cudnn")
                    for arg in fake_bazel[0]["argv"]
                )
            )
            self.assertTrue(
                any(
                    arg.startswith("--repo_env=LOCAL_NCCL_PATH=")
                    and arg.endswith("/jax-local-redists/nccl")
                    for arg in fake_bazel[0]["argv"]
                )
            )
            self.assertTrue(
                any(
                    arg.startswith("--repo_env=LOCAL_NVSHMEM_PATH=")
                    and arg.endswith("/jax-local-redists/nvshmem")
                    for arg in fake_bazel[0]["argv"]
                )
            )
            self.assertIn("--repository_cache=/vaso/cache/bazel/repository-cache", fake_bazel[0]["argv"])
            self.assertIn("--distdir=/vaso/sources/jax/distdir", fake_bazel[0]["argv"])
            self.assertEqual(
                fake_bazel[0]["argv"][-3:],
                [
                    "//jaxlib/tools:jaxlib_wheel",
                    "//jaxlib/tools:jax_cuda13_plugin_wheel",
                    "//jaxlib/tools:jax_cuda13_pjrt_wheel",
                ],
            )
            self.assertTrue(
                any(arg.startswith("--experimental_repository_resolved_file=") for arg in fake_bazel[0]["argv"])
            )

            metadata = json.loads(metadata_out.read_text(encoding="utf-8"))
            self.assertEqual(metadata["mode"], "prefetch-nested-bazel")
            self.assertEqual(metadata["source"], str(build_source))
            self.assertEqual(metadata["prefetch"]["log"], str(prefetch_log_out))
            self.assertEqual(metadata["prefetch"]["targets"], fake_bazel[0]["argv"][-3:])
            self.assertIn("Nested JAX Bazel repository prefetch completed", marker_out.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
