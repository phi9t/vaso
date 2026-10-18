#!/usr/bin/env python3
"""Support helpers for the committed jaxlib build plan."""

from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import stat
import subprocess
import sys
import tarfile
from pathlib import Path, PurePosixPath


PYTHON_BUILD_PREFIXES = (
    "py-pip",
    "py-setuptools",
    "py-wheel",
    "py-numpy",
    "python-venv",
)
PATH_PREFIXES = (
    "python-venv",
    "python",
    "py-pip",
    "py-wheel",
    "bazel",
    "llvm",
    "cuda",
    "xxd-standalone",
)
JAX_LOCAL_REDIST_KEYS = ("cuda", "cudnn", "nccl", "nvshmem")
JAX_LOCAL_REDIST_DIRS = {
    "cuda": ("bin", "include", "lib", "lib64", "nvml", "nvvm", "targets", "extras"),
    "cudnn": ("include", "lib", "lib64"),
    "nccl": ("include", "lib", "lib64"),
    "nvshmem": ("include", "lib", "lib64", "bin"),
}
JAX_LOCAL_REDIST_OPTIONAL_DIRS = {
    ("cuda", "nvml"),
    ("cuda", "targets"),
    ("cuda", "extras"),
    ("cudnn", "lib64"),
    ("nccl", "lib64"),
    ("nvshmem", "lib64"),
    ("nvshmem", "bin"),
}
JAX_LOCAL_CCCL_SOURCE_DIRS = ("cub", "libcudacxx", "thrust")
JAX_LOCAL_CCCL_ARCHIVE_SHA256 = "b5cd66e240201f5a06af2a75eaffdf05a6c63829edada33ff569ada0037f8086"
JAX_LOCAL_CCCL_ARCHIVE_STRIP_PREFIX = "cccl-src-v3.2.0"
CCCL_STRING_VIEW_CLANG_CUDA_REPLACEMENTS = (
    (
        "_CCCL_HOST basic_string_view(::std::basic_string<_CharT, ::std::char_traits<_CharT>, _Alloc>)\n"
        "  -> basic_string_view<_CharT>;",
        "_CCCL_HOST_DEVICE basic_string_view(::std::basic_string<_CharT, ::std::char_traits<_CharT>, _Alloc>)\n"
        "  -> basic_string_view<_CharT>;",
    ),
    (
        "_CCCL_HOST basic_string_view(::std::basic_string<_CharT, _Traits, _Alloc>) -> "
        "basic_string_view<_CharT, _Traits>;",
        "_CCCL_HOST_DEVICE basic_string_view(::std::basic_string<_CharT, _Traits, _Alloc>) -> "
        "basic_string_view<_CharT, _Traits>;",
    ),
    (
        "_CCCL_HOST basic_string_view(::std::basic_string_view<_CharT>) -> basic_string_view<_CharT>;",
        "_CCCL_HOST_DEVICE basic_string_view(::std::basic_string_view<_CharT>) -> basic_string_view<_CharT>;",
    ),
    (
        "_CCCL_HOST basic_string_view(::std::basic_string_view<_CharT, _Traits>) -> "
        "basic_string_view<_CharT, _Traits>;",
        "_CCCL_HOST_DEVICE basic_string_view(::std::basic_string_view<_CharT, _Traits>) -> "
        "basic_string_view<_CharT, _Traits>;",
    ),
)

RULES_ML_TOOLCHAIN_CLANG23_PTX_PATCH_NAME = "rules_ml_toolchain-clang23-ptx.patch"
RULES_ML_TOOLCHAIN_CLANG23_PTX_PATCH = """\
diff --git a/gpu/cuda/cuda_redist_versions.bzl b/gpu/cuda/cuda_redist_versions.bzl
--- a/gpu/cuda/cuda_redist_versions.bzl
+++ b/gpu/cuda/cuda_redist_versions.bzl
@@ -716,6 +716,7 @@ PTX_VERSION_DICT = {
         "20": "8.7",
         "21": "8.8",
         "22": "9.0",
+        "23": "9.2",
     },
     # To find, look at https://docs.nvidia.com/cuda/parallel-thread-execution/index.html#release-notes
     "cuda": {
diff --git a/py/python_init_pip.bzl b/py/python_init_pip.bzl
--- a/py/python_init_pip.bzl
+++ b/py/python_init_pip.bzl
@@ -86,5 +86,8 @@
         envsubst = ["RULES_PYTHON_ENABLE_PIPSTAR"],
         requirements_lock = REQUIREMENTS_WITH_LOCAL_WHEELS,
-        extra_pip_args = extra_pip_args,
+        extra_pip_args = [
+            "--no-index",
+            "--find-links=/vaso/sources/jax/distdir",
+        ] + extra_pip_args,
         download_only = is_download_only,
     )
"""
XLA_RULES_ML_TOOLCHAIN_CLANG23_PTX_PATCH_NAME = "xla_rules_ml_toolchain-clang23-ptx.patch"
XLA_RULES_ML_TOOLCHAIN_CLANG23_PTX_PATCH = (
    "diff --git a/third_party/rules_ml_toolchain-clang23-ptx.patch b/third_party/rules_ml_toolchain-clang23-ptx.patch\n"
    "new file mode 100644\n"
    "--- /dev/null\n"
    "+++ b/third_party/rules_ml_toolchain-clang23-ptx.patch\n"
    f"@@ -0,0 +1,{len(RULES_ML_TOOLCHAIN_CLANG23_PTX_PATCH.splitlines())} @@\n"
    + "".join("+" + line + "\n" for line in RULES_ML_TOOLCHAIN_CLANG23_PTX_PATCH.splitlines())
    + """\
diff --git a/workspace3.bzl b/workspace3.bzl
--- a/workspace3.bzl
+++ b/workspace3.bzl
@@ -51,6 +51,7 @@ def repo():
         name = "rules_ml_toolchain",
         sha256 = "40963e4bc262dfa9a43146f610140af0068b023ace8f3c50f1705a7b50de0830",
         strip_prefix = "rules_ml_toolchain-cad1047facbac4fb3c1124da68bf2cb36c7eb9ac",
+        patch_file = ["//third_party:rules_ml_toolchain-clang23-ptx.patch"],
         urls = tf_mirror_urls(
             "https://github.com/google-ml-infra/rules_ml_toolchain/archive/cad1047facbac4fb3c1124da68bf2cb36c7eb9ac.tar.gz",
         ),
"""
)
XLA_RULES_GO_SDK_LINUX_AMD64_PATCH_NAME = "xla_rules_go_sdk_linux_amd64.patch"
XLA_RULES_GO_SDK_LINUX_AMD64_PATCH = """\
diff --git a/third_party/rules_go-go-sdk-linux-amd64.patch b/third_party/rules_go-go-sdk-linux-amd64.patch
new file mode 100644
--- /dev/null
+++ b/third_party/rules_go-go-sdk-linux-amd64.patch
@@ -0,0 +1,18 @@
+diff --git a/go/private/sdk.bzl b/go/private/sdk.bzl
+--- a/go/private/sdk.bzl
++++ b/go/private/sdk.bzl
+@@ -429,6 +429,14 @@ def go_register_toolchains(version = None, nogo = None, go_version = None):
+             if _version_less(pv, MIN_SUPPORTED_VERSION):
+                 print("DEPRECATED: Go versions before {} are not supported and may not work".format(_version_string(MIN_SUPPORTED_VERSION)))
+             go_download_sdk(
+                 name = "go_sdk",
++                sdks = {
++                    "1.22.5": {
++                        "linux_amd64": [
++                            "go1.22.5.linux-amd64.tar.gz",
++                            "904b924d435eaea086515bc63235b192ea441bd8c9b198c507e85009e6e4c7f0",
++                        ],
++                    },
++                }.get(version, {}),
+                 version = version,
+             )
diff --git a/workspace2.bzl b/workspace2.bzl
--- a/workspace2.bzl
+++ b/workspace2.bzl
@@ -603,4 +603,5 @@ def _tf_repositories():
     tf_http_archive(
         name = "io_bazel_rules_go",
+        patch_file = ["//third_party:rules_go-go-sdk-linux-amd64.patch"],
         sha256 = "16e9fca53ed6bd4ff4ad76facc9b7b651a89db1689a2877d6fd7b82aa824e366",
         urls = tf_mirror_urls("https://github.com/bazelbuild/rules_go/releases/download/v0.34.0/rules_go-v0.34.0.zip"),
     )
"""
XLA_NVSHMEM_GENERATED_HEADERS_PATCH_NAME = "xla_nvshmem_generated_headers.patch"
XLA_NVSHMEM_GENERATED_HEADERS_PATCH = """\
diff --git a/third_party/nvshmem/generated_headers.patch b/third_party/nvshmem/generated_headers.patch
new file mode 100644
--- /dev/null
+++ b/third_party/nvshmem/generated_headers.patch
@@ -0,0 +1,73 @@
+diff --git a/src/include/device_host_transport/nvshmem_constants.h b/src/include/device_host_transport/nvshmem_constants.h
+--- a/src/include/device_host_transport/nvshmem_constants.h
++++ b/src/include/device_host_transport/nvshmem_constants.h
+@@ -18,6 +18,6 @@
+ #include <cuda/std/climits>
+ #endif
+-#include "non_abi/nvshmem_version.h"
++#include "third_party/nvshmem/non_abi/nvshmem_version.h"
+ 
+ #define CHANNEL_BUF_SIZE (1 << CHANNEL_BUF_SIZE_LOG)
+ #define CHANNEL_BUF_SIZE_LOG 22
+diff --git a/src/include/host/nvshmem_api.h b/src/include/host/nvshmem_api.h
+--- a/src/include/host/nvshmem_api.h
++++ b/src/include/host/nvshmem_api.h
+@@ -23,7 +23,7 @@
+ #include "device_host/nvshmem_common.cuh"
+ #include "device_host_transport/nvshmem_constants.h"
+ #include "host/nvshmem_macros.h"
+-#include "non_abi/nvshmem_version.h"
++#include "third_party/nvshmem/non_abi/nvshmem_version.h"
+ 
+ int nvshmemi_init_thread(int requested_thread_support, int *provided_thread_support,
+                          unsigned int bootstrap_flags, nvshmemx_init_attr_t *bootstrap_attr,
+diff --git a/src/include/host/nvshmemx_api.h b/src/include/host/nvshmemx_api.h
+--- a/src/include/host/nvshmemx_api.h
++++ b/src/include/host/nvshmemx_api.h
+@@ -19,7 +19,7 @@
+ #include <stddef.h>
+ #include "device_host_transport/nvshmem_constants.h"
+ #include "device_host/nvshmem_common.cuh"
+-#include "non_abi/nvshmem_version.h"
++#include "third_party/nvshmem/non_abi/nvshmem_version.h"
+ #include "host/nvshmemx_coll_api.h"
+ #include "host/nvshmem_macros.h"
+ #include "non_abi/nvshmemx_error.h"
+diff --git a/src/include/internal/bootstrap_host/nvshmemi_bootstrap.h b/src/include/internal/bootstrap_host/nvshmemi_bootstrap.h
+--- a/src/include/internal/bootstrap_host/nvshmemi_bootstrap.h
++++ b/src/include/internal/bootstrap_host/nvshmemi_bootstrap.h
+@@ -7,6 +7,6 @@
+ #define NVSHMEMI_BOOTSTRAP_H
+ 
+ #include "internal/bootstrap_host_transport/nvshmemi_bootstrap_defines.h"
+-#include "non_abi/nvshmem_version.h"
++#include "third_party/nvshmem/non_abi/nvshmem_version.h"
+ /* Version = major * 10000 + minor * 100 + patch*/
+ /* ABI Introduced in NVSHMEM 2.8.0 */
+diff --git a/src/include/internal/host_transport/transport.h b/src/include/internal/host_transport/transport.h
+--- a/src/include/internal/host_transport/transport.h
++++ b/src/include/internal/host_transport/transport.h
+@@ -17,10 +17,10 @@
+  * the ABI for transport modules.
+  */
+ #include "bootstrap_host_transport/env_defs_internal.h"
+-#include "non_abi/nvshmem_version.h"
++#include "third_party/nvshmem/non_abi/nvshmem_version.h"
+ #include "device_host_transport/nvshmem_common_transport.h"
+ #include "non_abi/nvshmemx_error.h"
+-#include "non_abi/nvshmem_build_options.h"
++#include "third_party/nvshmem/non_abi/nvshmem_build_options.h"
+ #include "internal/host_transport/nvshmemi_transport_defines.h"
+ #include "internal/bootstrap_host_transport/nvshmemi_bootstrap_defines.h"
+ 
+diff --git a/src/include/nvshmem.h b/src/include/nvshmem.h
+--- a/src/include/nvshmem.h
++++ b/src/include/nvshmem.h
+@@ -13,6 +13,6 @@
+ #ifndef _NVSHMEM_H_
+ #define _NVSHMEM_H_
+ 
+-#include "non_abi/nvshmem_build_options.h"
++#include "third_party/nvshmem/non_abi/nvshmem_build_options.h"
+ /* NVRTC only compiles device code. Leave out host headers */
+ #if not defined __CUDACC_RTC__
diff --git a/third_party/nvshmem/workspace.bzl b/third_party/nvshmem/workspace.bzl
--- a/third_party/nvshmem/workspace.bzl
+++ b/third_party/nvshmem/workspace.bzl
@@ -9,6 +9,9 @@ def repo():
         sha256 = "2146ff231d9aadd2b11f324c142582f89e3804775877735dc507b4dfd70c788b",
         urls = tf_mirror_urls("https://developer.download.nvidia.com/compute/redist/nvshmem/3.1.7/source/nvshmem_src_3.1.7-1.txz"),
         build_file = "//third_party/nvshmem:nvshmem.BUILD",
-        patch_file = ["//third_party/nvshmem:archive.patch"],
+        patch_file = [
+            "//third_party/nvshmem:archive.patch",
+            "//third_party/nvshmem:generated_headers.patch",
+        ],
         type = "tar",
     )
"""
XLA_THUNK_SEQUENCE_COPY_ASSIGN_PATCH_NAME = "xla_thunk_sequence_copy_assign.patch"
XLA_THUNK_SEQUENCE_COPY_ASSIGN_PATCH = """\
diff --git a/xla/backends/gpu/runtime/thunk.h b/xla/backends/gpu/runtime/thunk.h
--- a/xla/backends/gpu/runtime/thunk.h
+++ b/xla/backends/gpu/runtime/thunk.h
@@ -506,7 +506,7 @@ class ThunkSequence : public std::vector<std::unique_ptr<Thunk>> {
       : std::vector<std::unique_ptr<Thunk>>(std::move(thunks)) {};
   ThunkSequence(const ThunkSequence&) = delete;
 
-  ThunkSequence& operator=(ThunkSequence&) = delete;
+  ThunkSequence& operator=(const ThunkSequence&) = delete;
   ThunkSequence& operator=(ThunkSequence&&) = default;
 
   explicit ThunkSequence(int64_t len)
"""
XLA_BUFFER_DEBUG_FLOAT_CHECK_CLANG_CUDA_PATCH_NAME = "xla_buffer_debug_float_check_clang_cuda.patch"
XLA_BUFFER_DEBUG_FLOAT_CHECK_CLANG_CUDA_PATCH = """\
diff --git a/xla/stream_executor/cuda/buffer_debug_float_check_kernel_cuda.cu.cc b/xla/stream_executor/cuda/buffer_debug_float_check_kernel_cuda.cu.cc
--- a/xla/stream_executor/cuda/buffer_debug_float_check_kernel_cuda.cu.cc
+++ b/xla/stream_executor/cuda/buffer_debug_float_check_kernel_cuda.cu.cc
@@ -5,9 +5,9 @@ __host__ __device__ static constexpr T kInfinity =
 template <>
 __host__ __device__ constexpr __nv_bfloat16 kInfinity<__nv_bfloat16> =
-    absl::bit_cast<__nv_bfloat16>(kInfinity<Eigen::bfloat16>);
+    __nv_bfloat16(__nv_bfloat16_raw{0x7F80U});
 // - __half lacks std::numeric_limits specialization, and Eigen::half is not a
 // literal type (non-constexpr constructors), so we construct infinity from raw
 // bits.
 template <>
 __host__ __device__ constexpr __half kInfinity<__half> =
-    absl::bit_cast<__half>(uint16_t{0x7C00});
+    __half(__half_raw{0x7C00U});
"""


def fail(message: str) -> None:
    raise SystemExit(message)


def parse_name_path(items: list[str], flag: str) -> dict[str, Path]:
    values: dict[str, Path] = {}
    for item in items:
        if "=" not in item:
            fail(f"{flag} must be KEY=PATH, got {item!r}")
        key, value = item.split("=", 1)
        if not key or not value:
            fail(f"{flag} must be KEY=PATH, got {item!r}")
        values[key] = Path(value).resolve(strict=False)
    return values


def python_version_from_abi(python_abi: str) -> str | None:
    if python_abi.startswith("cp"):
        digits = python_abi[2:]
        if len(digits) >= 2 and digits.isdigit():
            return digits[0] + "." + digits[1:]
    if "." in python_abi and all(part.isdigit() for part in python_abi.split(".", 1)):
        return python_abi
    return None


def python_site_packages_from_prefixes(prefixes: dict[str, Path]) -> Path:
    for key in ("python-venv", *PYTHON_BUILD_PREFIXES, "python"):
        prefix = prefixes[key]
        lib = prefix / "lib"
        if not lib.is_dir():
            continue
        for candidate in sorted(lib.glob("python*/site-packages")):
            if candidate.is_dir():
                return candidate.relative_to(prefix)
    fail("could not derive Python site-packages path from selected jaxlib prefixes")


def python_site_packages_dir(prefixes: dict[str, Path], python_abi: str) -> Path:
    version = python_version_from_abi(python_abi)
    if version:
        return Path("lib") / f"python{version}" / "site-packages"
    python = prefixes["python"] / "bin" / "python3"
    probe = (
        "import sysconfig\n"
        "path = sysconfig.get_path('purelib', vars={'base': '', 'platbase': ''})\n"
        "print(path.lstrip('/'))\n"
    )
    try:
        result = subprocess.run([str(python), "-c", probe], check=False, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except OSError:
        result = None
    if result is not None and result.returncode == 0 and result.stdout.strip():
        return Path(result.stdout.strip())
    return python_site_packages_from_prefixes(prefixes)


def normalized_relative_parts(path: PurePosixPath) -> tuple[str, ...] | None:
    if path.is_absolute():
        return None
    parts: list[str] = []
    for part in path.parts:
        if part in ("", "."):
            continue
        if part == "..":
            if not parts:
                return None
            parts.pop()
            continue
        parts.append(part)
    return tuple(parts)


def validate_tar_member(member: tarfile.TarInfo) -> None:
    name = PurePosixPath(member.name)
    member_parts = normalized_relative_parts(name)
    if member_parts is None or ".." in name.parts:
        fail(f"archive member escapes extraction root: {member.name}")
    if member.issym() or member.islnk():
        link = PurePosixPath(member.linkname)
        if member.issym():
            link_parts = normalized_relative_parts(name.parent / link)
        else:
            link_parts = normalized_relative_parts(link)
        if link_parts is None:
            fail(f"archive link escapes extraction root: {member.name}")
        top_level = member_parts[:1] if len(member_parts) > 1 else ()
        if top_level and link_parts[:1] != top_level:
            fail(f"archive link escapes extraction root: {member.name}")


def replace_once(path: Path, old: str, new: str, *, already: str) -> None:
    text = path.read_text(encoding="utf-8")
    if already in text:
        return
    if old not in text:
        fail(f"could not find expected text in {path}: {old!r}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def patch_jax_source(source: Path) -> None:
    patch_path = source / "third_party" / RULES_ML_TOOLCHAIN_CLANG23_PTX_PATCH_NAME
    patch_path.parent.mkdir(parents=True, exist_ok=True)
    patch_path.write_text(RULES_ML_TOOLCHAIN_CLANG23_PTX_PATCH, encoding="utf-8")
    xla_root = source / "third_party" / "xla"
    xla_root.mkdir(parents=True, exist_ok=True)
    (xla_root / XLA_RULES_ML_TOOLCHAIN_CLANG23_PTX_PATCH_NAME).write_text(
        XLA_RULES_ML_TOOLCHAIN_CLANG23_PTX_PATCH,
        encoding="utf-8",
    )
    (xla_root / XLA_RULES_GO_SDK_LINUX_AMD64_PATCH_NAME).write_text(
        XLA_RULES_GO_SDK_LINUX_AMD64_PATCH,
        encoding="utf-8",
    )
    (xla_root / XLA_NVSHMEM_GENERATED_HEADERS_PATCH_NAME).write_text(
        XLA_NVSHMEM_GENERATED_HEADERS_PATCH,
        encoding="utf-8",
    )
    (xla_root / XLA_THUNK_SEQUENCE_COPY_ASSIGN_PATCH_NAME).write_text(
        XLA_THUNK_SEQUENCE_COPY_ASSIGN_PATCH,
        encoding="utf-8",
    )
    (xla_root / XLA_BUFFER_DEBUG_FLOAT_CHECK_CLANG_CUDA_PATCH_NAME).write_text(
        XLA_BUFFER_DEBUG_FLOAT_CHECK_CLANG_CUDA_PATCH,
        encoding="utf-8",
    )

    patch_label = f"//third_party:{RULES_ML_TOOLCHAIN_CLANG23_PTX_PATCH_NAME}"
    workspace_marker = f'    patch_file = ["{patch_label}"],'
    replace_once(
        source / "WORKSPACE",
        '    strip_prefix = "rules_ml_toolchain-cad1047facbac4fb3c1124da68bf2cb36c7eb9ac",\n',
        (
            '    strip_prefix = "rules_ml_toolchain-cad1047facbac4fb3c1124da68bf2cb36c7eb9ac",\n'
            f"{workspace_marker}\n"
        ),
        already=workspace_marker,
    )
    module_marker = f'    patches = ["{patch_label}"],'
    replace_once(
        source / "MODULE.bazel",
        '    integrity = "sha256-QJY+S8Ji36mkMUb2EBQK8AaLAjrOjzxQ8XBae1DeCDA=",\n',
        (
            '    integrity = "sha256-QJY+S8Ji36mkMUb2EBQK8AaLAjrOjzxQ8XBae1DeCDA=",\n'
            "    patch_strip = 1,\n"
            f"{module_marker}\n"
        ),
        already=module_marker,
    )
    xla_workspace = xla_root / "workspace.bzl"
    xla_patch_label = f"//third_party/xla:{XLA_RULES_ML_TOOLCHAIN_CLANG23_PTX_PATCH_NAME}"
    xla_workspace_marker = f'            "{xla_patch_label}",'
    replace_once(
        xla_workspace,
        '            "//third_party/xla:xla_9b348c6b.patch",\n',
        '            "//third_party/xla:xla_9b348c6b.patch",\n' + xla_workspace_marker + "\n",
        already=xla_workspace_marker,
    )
    markers = [
        f'            "//third_party/xla:{XLA_NVSHMEM_GENERATED_HEADERS_PATCH_NAME}",',
        f'            "//third_party/xla:{XLA_THUNK_SEQUENCE_COPY_ASSIGN_PATCH_NAME}",',
        f'            "//third_party/xla:{XLA_BUFFER_DEBUG_FLOAT_CHECK_CLANG_CUDA_PATCH_NAME}",',
        f'            "//third_party/xla:{XLA_RULES_GO_SDK_LINUX_AMD64_PATCH_NAME}",',
    ]
    previous = xla_workspace_marker
    for marker in markers:
        replace_once(
            xla_workspace,
            previous + "\n",
            previous + "\n" + marker + "\n",
            already=marker,
        )
        previous = marker


def patch_cuda_string_view(cuda_include: Path) -> None:
    header = cuda_include / "cuda" / "std" / "string_view"
    if not header.is_file():
        return
    text = header.read_text(encoding="utf-8")
    for old, new in CCCL_STRING_VIEW_CLANG_CUDA_REPLACEMENTS:
        if old in text:
            text = text.replace(old, new, 1)
        elif new not in text:
            fail(f"could not patch CCCL string_view deduction guide in {header}")
    header.chmod(header.stat().st_mode | stat.S_IWUSR)
    header.write_text(text, encoding="utf-8")


def refresh_symlink(link: Path, target: Path) -> None:
    if link.is_symlink() or link.exists():
        if link.is_dir() and not link.is_symlink():
            shutil.rmtree(link)
        else:
            link.unlink()
    link.symlink_to(target, target_is_directory=target.is_dir())


def refresh_directory(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink()
    path.mkdir(parents=True, exist_ok=True)


def copy_directory_once(source: Path, destination: Path) -> None:
    if destination.is_symlink() or destination.is_file():
        destination.unlink()
    if destination.exists():
        if not destination.is_dir():
            fail(f"local redist view path is not a directory: {destination}")
        return
    copying = destination.with_name(destination.name + ".copying")
    if copying.exists():
        shutil.rmtree(copying)
    shutil.copytree(source, copying, symlinks=True)
    shutil.move(str(copying), str(destination))


def cuda_toolkit_sibling(prefix: Path, name: str) -> Path | None:
    for anchor in ("bin", "include", "lib64", "targets"):
        candidate = prefix / anchor
        if candidate.exists():
            sibling = candidate.resolve(strict=True).parent / name
            if sibling.is_dir():
                return sibling
    return None


def redist_dir_candidates(prefix: Path, key: str, directory: str) -> list[Path]:
    candidates = [prefix / directory]
    if directory == "lib":
        candidates.append(prefix / "lib64")
    if key == "cuda" and directory == "nvml":
        sibling = cuda_toolkit_sibling(prefix, "nvml")
        if sibling is not None:
            candidates.append(sibling)
    return candidates


def extract_cached_cccl(repository_cache: Path, destination_root: Path) -> Path:
    archive = repository_cache / "content_addressable" / "sha256" / JAX_LOCAL_CCCL_ARCHIVE_SHA256 / "file"
    if not archive.is_file():
        fail(f"required jaxlib nested repository-cache CCCL archive is missing: {archive}")
    destination = destination_root / JAX_LOCAL_CCCL_ARCHIVE_STRIP_PREFIX
    if destination.is_dir():
        return destination
    extracting = destination.with_name(destination.name + ".extracting")
    if extracting.exists():
        shutil.rmtree(extracting)
    extracting.mkdir(parents=True)
    with tarfile.open(archive, "r:*") as handle:
        members = handle.getmembers()
        for member in members:
            validate_tar_member(member)
        handle.extractall(extracting, members=members)
    extracted = extracting / JAX_LOCAL_CCCL_ARCHIVE_STRIP_PREFIX
    if not extracted.is_dir():
        fail(f"cached CCCL archive missing {JAX_LOCAL_CCCL_ARCHIVE_STRIP_PREFIX}: {archive}")
    shutil.move(str(extracted), str(destination))
    shutil.rmtree(extracting)
    return destination


def materialize_local_redists(prefixes: dict[str, Path], work: Path, repository_cache: Path) -> dict[str, Path]:
    views_root = work / "jax-local-redists"
    views_root.mkdir(parents=True, exist_ok=True)
    views: dict[str, Path] = {}
    for key in JAX_LOCAL_REDIST_KEYS:
        source = prefixes[key]
        view = views_root / key
        view.mkdir(parents=True, exist_ok=True)
        for directory in JAX_LOCAL_REDIST_DIRS[key]:
            target = next((path for path in redist_dir_candidates(source, key, directory) if path.is_dir()), None)
            link = view / directory
            if target is None:
                if (key, directory) not in JAX_LOCAL_REDIST_OPTIONAL_DIRS:
                    fail(f"local redist view missing {key}:{directory} under {source}")
                refresh_directory(link)
                continue
            if key == "cuda" and directory == "include":
                copy_directory_once(target, link)
                patch_cuda_string_view(link)
                continue
            refresh_symlink(link, target)
        views[key] = view

    cccl_source = extract_cached_cccl(repository_cache, views_root)
    cccl_view = views_root / "cccl"
    if cccl_view.exists() or cccl_view.is_symlink():
        if cccl_view.is_dir() and not cccl_view.is_symlink():
            shutil.rmtree(cccl_view)
        else:
            cccl_view.unlink()
    cccl_view.mkdir()
    for name in JAX_LOCAL_CCCL_SOURCE_DIRS:
        source = cccl_source / name
        if not source.is_dir():
            fail(f"cached CCCL archive is missing source dir {name}: {cccl_source}")
        shutil.copytree(source, cccl_view / name, symlinks=True)
    patch_cuda_string_view(cccl_view / "libcudacxx" / "include")
    views["cccl"] = cccl_view
    return views


def q(value: object) -> str:
    return shlex.quote(str(value))


def write_env_file(
    env_file: Path,
    *,
    prefixes: dict[str, Path],
    views: dict[str, Path],
    work: Path,
    python_abi: str,
    cuda_version: str,
) -> None:
    site_packages = python_site_packages_dir(prefixes, python_abi)
    pythonpath = [prefixes[key] / site_packages for key in PYTHON_BUILD_PREFIXES]
    path_parts = [prefixes[key] / "bin" for key in PATH_PREFIXES] + [Path("/usr/bin"), Path("/bin")]
    library_parts = [
        views["cuda"] / "lib64",
        views["cuda"] / "lib",
        views["cudnn"] / "lib64",
        views["cudnn"] / "lib",
        views["nccl"] / "lib64",
        views["nccl"] / "lib",
        views["nvshmem"] / "lib64",
        views["nvshmem"] / "lib",
    ]
    values = {
        "BAZELISK_SKIP_WRAPPER": "1",
        "CC": os.environ.get("CC", "/usr/lib/llvm-23/bin/clang"),
        "CXX": os.environ.get("CXX", "/usr/lib/llvm-23/bin/clang++"),
        "CUDA_HOME": views["cuda"],
        "CUDA_PATH": views["cuda"],
        "HERMETIC_CUDA_VERSION": cuda_version,
        "HOME": work / "home",
        "JAX_RELEASE": "1",
        "LOCAL_CCCL_PATH": views["cccl"],
        "LOCAL_CUDA_PATH": views["cuda"],
        "LOCAL_CUDNN_PATH": views["cudnn"],
        "LOCAL_NCCL_PATH": views["nccl"],
        "LOCAL_NVSHMEM_PATH": views["nvshmem"],
        "PATH": os.pathsep.join(str(path) for path in path_parts),
        "PIP_CACHE_DIR": work / "pip-cache",
        "PIP_DISABLE_PIP_VERSION_CHECK": "1",
        "PIP_NO_INDEX": "1",
        "PIP_NO_INPUT": "1",
        "PYTHONHASHSEED": os.environ.get("PYTHONHASHSEED", "0"),
        "PYTHONHOME": "",
        "PYTHONNOUSERSITE": "1",
        "PYTHONPATH": os.pathsep.join(str(path) for path in pythonpath),
        "SOURCE_DATE_EPOCH": os.environ.get("SOURCE_DATE_EPOCH", ""),
        "XLA_PYTHON_CLIENT_PREALLOCATE": "false",
    }
    for key in ("CFLAGS", "CXXFLAGS", "CUDAFLAGS", "NVCC_FLAGS"):
        if os.environ.get(key):
            values[key] = os.environ[key]
    (work / "home").mkdir(parents=True, exist_ok=True)
    (work / "pip-cache").mkdir(parents=True, exist_ok=True)
    env_file.parent.mkdir(parents=True, exist_ok=True)
    lines = ["set -euo pipefail"]
    for key, value in sorted(values.items()):
        lines.append(f"export {key}={q(value)}")
    lines.append("export LD_LIBRARY_PATH=" + q(os.pathsep.join(str(path) for path in library_parts)))
    env_file.write_text("\n".join(lines) + "\n", encoding="utf-8")


def prepare(args: argparse.Namespace) -> None:
    prefixes = parse_name_path(args.dep_prefix, "--dep-prefix")
    missing = sorted(set(PATH_PREFIXES + PYTHON_BUILD_PREFIXES + JAX_LOCAL_REDIST_KEYS) - set(prefixes))
    if missing:
        fail("missing jaxlib dependency prefix(es): " + ", ".join(missing))
    work = args.work.resolve(strict=False)
    work.mkdir(parents=True, exist_ok=True)
    patch_jax_source(args.source)
    views = materialize_local_redists(prefixes, work, args.nested_repository_cache)
    write_env_file(
        args.env_file,
        prefixes=prefixes,
        views=views,
        work=work,
        python_abi=args.python_abi,
        cuda_version=args.cuda_version,
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    prepare_parser = subparsers.add_parser("prepare")
    prepare_parser.add_argument("--source", type=Path, required=True)
    prepare_parser.add_argument("--work", type=Path, required=True)
    prepare_parser.add_argument("--env-file", type=Path, required=True)
    prepare_parser.add_argument("--python-abi", required=True)
    prepare_parser.add_argument("--cuda-version", required=True)
    prepare_parser.add_argument("--nested-repository-cache", type=Path, required=True)
    prepare_parser.add_argument("--dep-prefix", action="append", default=[])
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.command == "prepare":
        prepare(args)
        return 0
    fail(f"unsupported command {args.command!r}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
