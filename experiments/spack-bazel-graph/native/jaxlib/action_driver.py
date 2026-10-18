#!/usr/bin/env python3
"""Action driver for the token-gated native jaxlib prefix build."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import shlex
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path, PurePosixPath


REQUIRED_TOKEN = "build-native-llvm"
PREFIX_KEYS = (
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
JAX_LOCAL_REDIST_ENV = {
    "cuda": ("CUDA_HOME", "CUDA_PATH", "LOCAL_CUDA_PATH"),
    "cudnn": ("LOCAL_CUDNN_PATH",),
    "nccl": ("LOCAL_NCCL_PATH",),
    "nvshmem": ("LOCAL_NVSHMEM_PATH",),
}
JAX_LOCAL_CCCL_SOURCE_DIRS = ("cub", "libcudacxx", "thrust")
JAX_LOCAL_CCCL_REPO_ENV = "LOCAL_CCCL_PATH"
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
CCCL_STRING_VIEW_CLANG_CUDA_PATCH_SHA256 = hashlib.sha256(
    json.dumps(CCCL_STRING_VIEW_CLANG_CUDA_REPLACEMENTS, sort_keys=True).encode("utf-8")
).hexdigest()
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
"""
RULES_ML_TOOLCHAIN_CLANG23_PTX_PATCH_SHA256 = hashlib.sha256(
    RULES_ML_TOOLCHAIN_CLANG23_PTX_PATCH.encode("utf-8")
).hexdigest()
XLA_RULES_ML_TOOLCHAIN_CLANG23_PTX_PATCH_NAME = "xla_rules_ml_toolchain-clang23-ptx.patch"
XLA_RULES_ML_TOOLCHAIN_CLANG23_PTX_PATCH = """\
diff --git a/third_party/rules_ml_toolchain-clang23-ptx.patch b/third_party/rules_ml_toolchain-clang23-ptx.patch
new file mode 100644
--- /dev/null
+++ b/third_party/rules_ml_toolchain-clang23-ptx.patch
@@ -0,0 +1,11 @@
+diff --git a/gpu/cuda/cuda_redist_versions.bzl b/gpu/cuda/cuda_redist_versions.bzl
+--- a/gpu/cuda/cuda_redist_versions.bzl
++++ b/gpu/cuda/cuda_redist_versions.bzl
+@@ -716,6 +716,7 @@ PTX_VERSION_DICT = {
+         "20": "8.7",
+         "21": "8.8",
+         "22": "9.0",
++        "23": "9.2",
+     },
+     # To find, look at https://docs.nvidia.com/cuda/parallel-thread-execution/index.html#release-notes
+     "cuda": {
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
XLA_RULES_ML_TOOLCHAIN_CLANG23_PTX_PATCH_SHA256 = hashlib.sha256(
    XLA_RULES_ML_TOOLCHAIN_CLANG23_PTX_PATCH.encode("utf-8")
).hexdigest()
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
XLA_NVSHMEM_GENERATED_HEADERS_PATCH_SHA256 = hashlib.sha256(
    XLA_NVSHMEM_GENERATED_HEADERS_PATCH.encode("utf-8")
).hexdigest()
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
XLA_THUNK_SEQUENCE_COPY_ASSIGN_PATCH_SHA256 = hashlib.sha256(
    XLA_THUNK_SEQUENCE_COPY_ASSIGN_PATCH.encode("utf-8")
).hexdigest()
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
XLA_BUFFER_DEBUG_FLOAT_CHECK_CLANG_CUDA_PATCH_SHA256 = hashlib.sha256(
    XLA_BUFFER_DEBUG_FLOAT_CHECK_CLANG_CUDA_PATCH.encode("utf-8")
).hexdigest()


def _normalize_declared_output_paths(args: argparse.Namespace) -> None:
    for name in (
        "prefix_out",
        "build_plan_out",
        "provider_metadata_out",
        "result_marker_out",
        "wheelhouse_out",
        "wheel_manifest_out",
        "prefetch_log_out",
    ):
        value = getattr(args, name, None)
        if value:
            setattr(args, name, str(Path(value).resolve(strict=False)))


def _check_insula() -> None:
    if os.environ.get("VASO_IN_INSULA") != "1":
        raise SystemExit(
            "native jaxlib actions must run inside the hermetic insula "
            "(VASO_IN_INSULA=1)"
        )
    manifest = os.environ.get("VASO_ROOTFS_BUNDLE_MANIFEST", "")
    if not manifest or not Path(manifest).is_file():
        raise SystemExit(
            "native jaxlib actions require VASO_ROOTFS_BUNDLE_MANIFEST "
            "inside the hermetic CUDA rootfs"
        )


def _required_tmpdir(fallback: Path | None = None) -> Path:
    def is_shared_tmp(path: Path) -> bool:
        text = path.as_posix()
        return text == "/tmp" or text.startswith("/tmp/") or text == "/var/tmp" or text.startswith("/var/tmp/")

    candidates: list[Path] = []
    value = os.environ.get("TMPDIR", "")
    if value:
        path = Path(value)
        if not path.is_absolute():
            raise SystemExit(f"native jaxlib actions require an absolute TMPDIR, got {value!r}")
        if not is_shared_tmp(path):
            candidates.append(path)

    if fallback is not None:
        candidates.append(fallback.resolve(strict=False))

    vaso_home = os.environ.get("VASO_HOME", "")
    if vaso_home:
        home = Path(vaso_home)
        if not home.is_absolute():
            raise SystemExit(f"native jaxlib actions require an absolute VASO_HOME, got {vaso_home!r}")
        line = os.environ.get("VASO_CUDA_LINE", "unknown")
        candidates.append(home / "lines" / line / "tmp" / "native-actions" / "jaxlib")

    if not candidates:
        raise SystemExit(
            "native jaxlib actions require TMPDIR from the insula action environment "
            "or VASO_HOME for estate scratch"
        )

    errors = []
    for path in candidates:
        try:
            path.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            errors.append(f"{path}: {exc}")
            continue
        return path

    raise SystemExit("native jaxlib actions could not create scratch dir: " + "; ".join(errors))


def _read_prefix_files(items: list[str]) -> dict[str, str]:
    prefixes: dict[str, str] = {}
    for item in items:
        if "=" not in item:
            raise SystemExit(f"--prefix-file must be KEY=PATH, got {item!r}")
        key, path_text = item.split("=", 1)
        if key not in PREFIX_KEYS:
            raise SystemExit(f"unsupported jaxlib native prefix key {key!r}")
        path = Path(path_text)
        if not path.is_file():
            raise SystemExit(f"{key} prefix file is missing: {path}")
        prefixes[key] = path.read_text(encoding="utf-8").strip()
    missing = [key for key in PREFIX_KEYS if key not in prefixes]
    if missing:
        raise SystemExit("missing jaxlib native prefix file(s): " + ", ".join(missing))
    return prefixes


def _write_executable(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def _python_version_from_abi(python_abi: str) -> str | None:
    if python_abi.startswith("cp") and python_abi[2:].isdigit():
        digits = python_abi[2:]
        return digits[0] + "." + digits[1:]
    parts = python_abi.split(".", 1)
    if len(parts) == 2 and all(part.isdigit() for part in parts):
        return python_abi
    return None


def _python_site_packages_dir(python_prefix: str, python_abi: str) -> Path:
    version = _python_version_from_abi(python_abi)
    if version:
        return Path("lib") / ("python" + version) / "site-packages"

    python = Path(python_prefix) / "bin" / "python3"
    if python.is_file() and os.access(python, os.X_OK):
        probe = (
            "import sysconfig\n"
            "path = sysconfig.get_path('purelib', vars={'base': '', 'platbase': ''})\n"
            "print(path.lstrip('/'))\n"
        )
        try:
            result = subprocess.run(
                [str(python), "-c", probe],
                check=False,
                capture_output=True,
                text=True,
                timeout=30,
            )
        except (OSError, subprocess.TimeoutExpired):
            pass
        else:
            site_packages = result.stdout.strip()
            if result.returncode == 0 and site_packages:
                return Path(site_packages)

    return Path("lib") / "python" / "site-packages"


def _materialize_synthetic_prefixes(root: Path, python_abi: str) -> dict[str, str]:
    prefixes = {key: root / key for key in PREFIX_KEYS}
    for path in prefixes.values():
        path.mkdir(parents=True, exist_ok=True)

    (prefixes["python"] / "bin").mkdir()
    _write_executable(
        prefixes["python"] / "bin" / "python3",
        "#!/bin/sh\nexec " + shlex.quote(sys.executable) + ' "$@"\n',
    )

    site_packages = _python_site_packages_dir(str(prefixes["python"]), python_abi)
    if site_packages.parent.name.startswith("python"):
        header_dir = prefixes["python"] / "include" / site_packages.parent.name
        header_dir.mkdir(parents=True, exist_ok=True)
        (header_dir / "Python.h").write_text("", encoding="utf-8")
    else:
        (prefixes["python"] / "include").mkdir(parents=True, exist_ok=True)

    (prefixes["python-venv"] / "bin").mkdir()
    _write_executable(
        prefixes["python-venv"] / "bin" / "python3",
        "#!/bin/sh\nexec " + shlex.quote(sys.executable) + ' "$@"\n',
    )
    version = _python_version_from_abi(python_abi)
    if version:
        _write_executable(
            prefixes["python-venv"] / "bin" / ("python" + version),
            "#!/bin/sh\nexec " + shlex.quote(sys.executable) + ' "$@"\n',
        )
    (prefixes["python-venv"] / "pyvenv.cfg").write_text("", encoding="utf-8")
    (prefixes["python-venv"] / site_packages).mkdir(parents=True, exist_ok=True)

    def python_package_prefix(name: str, package_path: str) -> None:
        package = prefixes[name] / site_packages / package_path
        package.mkdir(parents=True, exist_ok=True)
        (package / "__init__.py").write_text("", encoding="utf-8")
        (prefixes[name] / "bin").mkdir(exist_ok=True)

    python_package_prefix("py-pip", "pip")
    _write_executable(prefixes["py-pip"] / "bin" / "pip", "#!/bin/sh\n")
    python_package_prefix("py-setuptools", "setuptools")
    python_package_prefix("py-wheel", "wheel")
    _write_executable(prefixes["py-wheel"] / "bin" / "wheel", "#!/bin/sh\n")
    python_package_prefix("py-numpy", "numpy")

    for rel in ("bin/bazel",):
        _write_executable(prefixes["bazel"] / rel, "#!/bin/sh\n")
    for rel in ("bin/clang", "bin/clang++", "bin/ld.lld", "bin/llvm-config"):
        path = prefixes["llvm"] / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        _write_executable(path, "#!/bin/sh\n")
    for rel in ("bin/nvcc", "bin/ptxas"):
        path = prefixes["cuda"] / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        _write_executable(path, "#!/bin/sh\n")
    for rel in ("include/cuda.h", "lib64/libcudart.so"):
        path = prefixes["cuda"] / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("", encoding="utf-8")
    for key, rels in {
        "cudnn": ("include/cudnn.h", "lib64/libcudnn.so"),
        "nccl": ("include/nccl.h", "lib/libnccl.so"),
        "nvshmem": ("include/nvshmem.h", "lib/libnvshmem_host.so"),
    }.items():
        for rel in rels:
            path = prefixes[key] / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("", encoding="utf-8")
    _write_executable(prefixes["xxd-standalone"] / "bin" / "xxd", "#!/bin/sh\n")

    return {key: str(path) for key, path in prefixes.items()}


def _run_plan(args: argparse.Namespace, prefixes: dict[str, str]) -> tuple[int, dict]:
    Path(args.build_plan_out).parent.mkdir(parents=True, exist_ok=True)
    plan_argv = [
        sys.executable,
        args.plan,
        "--pins",
        args.pins,
        "--out",
        args.build_plan_out,
        "--python-abi",
        args.python_abi,
        "--token",
        args.token,
        "--cuda-line",
        os.environ.get("VASO_CUDA_LINE", "cu130"),
    ]
    if args.execute:
        plan_argv.append("--execute")
    for key in PREFIX_KEYS:
        plan_argv.extend(["--prefix", f"{key}={prefixes[key]}"])

    result = subprocess.run(
        plan_argv,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if result.stdout:
        print(result.stdout, end="")
    if result.stderr:
        print(result.stderr, end="", file=sys.stderr)
    try:
        plan_doc = json.loads(Path(args.build_plan_out).read_text(encoding="utf-8"))
    except FileNotFoundError:
        plan_doc = {}
    return result.returncode, plan_doc


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _action_input_digest(args: argparse.Namespace, prefixes: dict[str, str]) -> str:
    files = {
        "plan": args.plan,
        "pins": args.pins,
        "source_anchor": args.source_anchor,
        "source_archive": args.source_archive,
    }
    rootfs_manifest = os.environ.get("VASO_ROOTFS_BUNDLE_MANIFEST", "")
    if rootfs_manifest:
        files["rootfs_manifest"] = rootfs_manifest
    payload = {
        "execute": bool(args.execute),
        "files": {},
        "local_redist_patches": {
            "cuda_cccl_string_view_clang_cuda": CCCL_STRING_VIEW_CLANG_CUDA_PATCH_SHA256,
        },
        "prefixes": {key: prefixes[key] for key in PREFIX_KEYS},
        "python_abi": args.python_abi,
        "vaso_cuda_line": os.environ.get("VASO_CUDA_LINE", ""),
    }
    for key, value in sorted(files.items()):
        path = Path(value)
        if not path.is_file():
            raise SystemExit(f"native jaxlib action digest input is missing: {key}={path}")
        payload["files"][key] = _file_sha256(path)
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:32]


def _estate_build_work_dir(args: argparse.Namespace, prefixes: dict[str, str]) -> Path:
    root_arg = getattr(args, "build_work_root", "")
    if root_arg:
        root = Path(root_arg)
    else:
        line = os.environ.get("VASO_CUDA_LINE", "")
        if not line:
            raise SystemExit("native jaxlib actions require VASO_CUDA_LINE for build work reuse")
        vaso_home = os.environ.get("VASO_HOME", "/vaso")
        root = Path(vaso_home) / "lines" / line / "work" / "jaxlib"
    if not root.is_absolute():
        raise SystemExit(f"native jaxlib build work root must be absolute: {root}")
    work_dir = root.resolve(strict=False) / _action_input_digest(args, prefixes)
    work_dir.mkdir(parents=True, exist_ok=True)
    return work_dir


def _source_dir_for_action(args: argparse.Namespace) -> Path:
    source_anchor = Path(args.source_anchor)
    if not source_anchor.is_file():
        raise SystemExit(f"jaxlib source anchor is missing: {source_anchor}")
    return source_anchor.parent.parent if source_anchor.parent.name == "build" else source_anchor.parent


def _ready_marker_payload(args: argparse.Namespace) -> dict[str, object]:
    source_anchor = Path(args.source_anchor)
    source_archive = Path(args.source_archive)
    return {
        "schema_version": 1,
        "source_patches": {
            RULES_ML_TOOLCHAIN_CLANG23_PTX_PATCH_NAME: RULES_ML_TOOLCHAIN_CLANG23_PTX_PATCH_SHA256,
            XLA_RULES_ML_TOOLCHAIN_CLANG23_PTX_PATCH_NAME: XLA_RULES_ML_TOOLCHAIN_CLANG23_PTX_PATCH_SHA256,
            XLA_NVSHMEM_GENERATED_HEADERS_PATCH_NAME: XLA_NVSHMEM_GENERATED_HEADERS_PATCH_SHA256,
            XLA_THUNK_SEQUENCE_COPY_ASSIGN_PATCH_NAME: XLA_THUNK_SEQUENCE_COPY_ASSIGN_PATCH_SHA256,
            XLA_BUFFER_DEBUG_FLOAT_CHECK_CLANG_CUDA_PATCH_NAME: (
                XLA_BUFFER_DEBUG_FLOAT_CHECK_CLANG_CUDA_PATCH_SHA256
            ),
        },
        "source_anchor_sha256": _file_sha256(source_anchor),
        "source_archive_sha256": _file_sha256(source_archive),
    }


def _normalized_relative_parts(path: PurePosixPath) -> tuple[str, ...] | None:
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


def _validate_tar_member(member: tarfile.TarInfo) -> None:
    name = PurePosixPath(member.name)
    member_parts = _normalized_relative_parts(name)
    if member_parts is None or ".." in name.parts:
        raise SystemExit(f"unsafe path in JAX source archive: {member.name}")
    if member.issym() or member.islnk():
        target = PurePosixPath(member.linkname)
        if member.issym():
            target_parts = _normalized_relative_parts(name.parent / target)
        else:
            target_parts = _normalized_relative_parts(target)
        if target_parts is None:
            raise SystemExit(f"unsafe link in JAX source archive: {member.name} -> {member.linkname}")
        top_level = member_parts[:1] if len(member_parts) > 1 else ()
        if top_level and target_parts[:1] != top_level:
            raise SystemExit(f"unsafe link in JAX source archive: {member.name} -> {member.linkname}")


def _replace_once(path: Path, old: str, new: str, *, already: str) -> None:
    text = path.read_text(encoding="utf-8")
    if already in text:
        return
    if old not in text:
        raise SystemExit(f"native jaxlib source patch could not find expected text in {path}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def _patch_jax_source_for_rootfs_toolchain(source: Path) -> None:
    patch_path = source / "third_party" / RULES_ML_TOOLCHAIN_CLANG23_PTX_PATCH_NAME
    patch_path.parent.mkdir(parents=True, exist_ok=True)
    patch_path.write_text(RULES_ML_TOOLCHAIN_CLANG23_PTX_PATCH, encoding="utf-8")
    xla_patch_path = source / "third_party" / "xla" / XLA_RULES_ML_TOOLCHAIN_CLANG23_PTX_PATCH_NAME
    xla_patch_path.parent.mkdir(parents=True, exist_ok=True)
    xla_patch_path.write_text(XLA_RULES_ML_TOOLCHAIN_CLANG23_PTX_PATCH, encoding="utf-8")
    xla_nvshmem_patch_path = source / "third_party" / "xla" / XLA_NVSHMEM_GENERATED_HEADERS_PATCH_NAME
    xla_nvshmem_patch_path.write_text(XLA_NVSHMEM_GENERATED_HEADERS_PATCH, encoding="utf-8")
    xla_thunk_patch_path = source / "third_party" / "xla" / XLA_THUNK_SEQUENCE_COPY_ASSIGN_PATCH_NAME
    xla_thunk_patch_path.write_text(XLA_THUNK_SEQUENCE_COPY_ASSIGN_PATCH, encoding="utf-8")
    xla_buffer_debug_patch_path = (
        source / "third_party" / "xla" / XLA_BUFFER_DEBUG_FLOAT_CHECK_CLANG_CUDA_PATCH_NAME
    )
    xla_buffer_debug_patch_path.write_text(XLA_BUFFER_DEBUG_FLOAT_CHECK_CLANG_CUDA_PATCH, encoding="utf-8")

    patch_label = f"//third_party:{RULES_ML_TOOLCHAIN_CLANG23_PTX_PATCH_NAME}"
    workspace = source / "WORKSPACE"
    workspace_marker = f'    patch_file = ["{patch_label}"],'
    _replace_once(
        workspace,
        '    strip_prefix = "rules_ml_toolchain-cad1047facbac4fb3c1124da68bf2cb36c7eb9ac",\n',
        (
            '    strip_prefix = "rules_ml_toolchain-cad1047facbac4fb3c1124da68bf2cb36c7eb9ac",\n'
            f"{workspace_marker}\n"
        ),
        already=workspace_marker,
    )

    module = source / "MODULE.bazel"
    module_marker = f'    patches = ["{patch_label}"],'
    _replace_once(
        module,
        '    integrity = "sha256-QJY+S8Ji36mkMUb2EBQK8AaLAjrOjzxQ8XBae1DeCDA=",\n',
        (
            '    integrity = "sha256-QJY+S8Ji36mkMUb2EBQK8AaLAjrOjzxQ8XBae1DeCDA=",\n'
            "    patch_strip = 1,\n"
            f"{module_marker}\n"
        ),
        already=module_marker,
    )
    xla_workspace = source / "third_party" / "xla" / "workspace.bzl"
    xla_patch_label = f"//third_party/xla:{XLA_RULES_ML_TOOLCHAIN_CLANG23_PTX_PATCH_NAME}"
    xla_workspace_marker = f'            "{xla_patch_label}",'
    _replace_once(
        xla_workspace,
        '            "//third_party/xla:xla_9b348c6b.patch",\n',
        (
            '            "//third_party/xla:xla_9b348c6b.patch",\n'
            f"{xla_workspace_marker}\n"
        ),
        already=xla_workspace_marker,
    )
    xla_nvshmem_patch_label = f"//third_party/xla:{XLA_NVSHMEM_GENERATED_HEADERS_PATCH_NAME}"
    xla_nvshmem_workspace_marker = f'            "{xla_nvshmem_patch_label}",'
    _replace_once(
        xla_workspace,
        xla_workspace_marker + "\n",
        xla_workspace_marker + "\n" + xla_nvshmem_workspace_marker + "\n",
        already=xla_nvshmem_workspace_marker,
    )
    xla_thunk_patch_label = f"//third_party/xla:{XLA_THUNK_SEQUENCE_COPY_ASSIGN_PATCH_NAME}"
    xla_thunk_workspace_marker = f'            "{xla_thunk_patch_label}",'
    _replace_once(
        xla_workspace,
        xla_nvshmem_workspace_marker + "\n",
        xla_nvshmem_workspace_marker + "\n" + xla_thunk_workspace_marker + "\n",
        already=xla_thunk_workspace_marker,
    )
    xla_buffer_debug_patch_label = f"//third_party/xla:{XLA_BUFFER_DEBUG_FLOAT_CHECK_CLANG_CUDA_PATCH_NAME}"
    xla_buffer_debug_workspace_marker = f'            "{xla_buffer_debug_patch_label}",'
    _replace_once(
        xla_workspace,
        xla_thunk_workspace_marker + "\n",
        xla_thunk_workspace_marker + "\n" + xla_buffer_debug_workspace_marker + "\n",
        already=xla_buffer_debug_workspace_marker,
    )


def _extract_source_archive(args: argparse.Namespace, build_work: Path) -> Path:
    source_archive = Path(args.source_archive)
    if not source_archive.is_file():
        raise SystemExit(f"JAX source archive is missing: {source_archive}")
    destination = build_work / "src"
    marker = build_work / "source_ready.json"
    desired = _ready_marker_payload(args)
    if (destination / "build" / "build.py").is_file() and marker.is_file():
        try:
            current = json.loads(marker.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            current = {}
        if current == desired:
            return destination

    if destination.exists():
        shutil.rmtree(destination)
    extracting = build_work / ("src.extracting.%d" % os.getpid())
    if extracting.exists():
        shutil.rmtree(extracting)
    extracting.mkdir(parents=True)
    with tarfile.open(source_archive, "r:gz") as archive:
        members = archive.getmembers()
        for member in members:
            _validate_tar_member(member)
        archive.extractall(extracting, members=members)

    if (extracting / "build" / "build.py").is_file():
        extracted_root = extracting
        extracting = None
    else:
        candidates = sorted(path for path in extracting.iterdir() if (path / "build" / "build.py").is_file())
        if len(candidates) != 1:
            raise SystemExit(
                "JAX source archive did not unpack to one build/build.py under "
                f"{build_work}"
            )
        extracted_root = candidates[0]

    shutil.move(str(extracted_root), str(destination))
    if extracting is not None and extracting.exists():
        shutil.rmtree(extracting)
    _patch_jax_source_for_rootfs_toolchain(destination)
    marker.write_text(json.dumps(desired, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return destination


def _python_executable(prefixes: dict[str, str], python_abi: str) -> str:
    version = _python_version_from_abi(python_abi)
    candidates = []
    if version:
        candidates.extend(
            [
                Path(prefixes["python-venv"]) / "bin" / ("python" + version),
                Path(prefixes["python"]) / "bin" / ("python" + version),
            ]
        )
    candidates.extend(
        [
            Path(prefixes["python-venv"]) / "bin" / "python3",
            Path(prefixes["python"]) / "bin" / "python3",
        ]
    )
    for candidate in candidates:
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    raise SystemExit("native jaxlib action could not find a Python executable in native prefixes")


def _minimal_build_env(plan_doc: dict) -> dict[str, str]:
    tmpdir = _required_tmpdir()
    action_home = tmpdir / "jaxlib-home"
    pip_cache = tmpdir / "pip-cache"
    action_home.mkdir(parents=True, exist_ok=True)
    pip_cache.mkdir(parents=True, exist_ok=True)
    env = {
        "HOME": str(action_home),
        "TMPDIR": str(tmpdir),
        "VASO_IN_INSULA": os.environ.get("VASO_IN_INSULA", ""),
        "VASO_ROOTFS_BUNDLE_MANIFEST": os.environ.get("VASO_ROOTFS_BUNDLE_MANIFEST", ""),
    }
    for key, value in plan_doc.get("build_env", {}).items():
        env[str(key)] = str(value)
    env["PIP_CACHE_DIR"] = str(pip_cache)
    env["PIP_DISABLE_PIP_VERSION_CHECK"] = "1"
    env["PIP_NO_INDEX"] = "1"
    env["PIP_NO_INPUT"] = "1"
    env["JAX_RELEASE"] = "1"
    return env


def _run_checked(argv: list[str], *, cwd: Path, env: dict[str, str], label: str) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        argv,
        cwd=cwd,
        env=env,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if result.stdout:
        print(result.stdout, end="")
    if result.stderr:
        print(result.stderr, end="", file=sys.stderr)
    if result.returncode != 0:
        raise SystemExit(f"{label} failed with rc={result.returncode}")
    return result


def _replace_path_segments(value: str, replacements: dict[str, str]) -> str:
    return os.pathsep.join(_replace_known_prefixes(item, replacements) for item in value.split(os.pathsep))


def _replace_known_prefixes(value: str, replacements: dict[str, str]) -> str:
    for old, new in sorted(replacements.items(), key=lambda item: len(item[0]), reverse=True):
        if old:
            value = value.replace(old, new)
    return value


def _refresh_symlink(link: Path, target: Path) -> None:
    if link.is_symlink() or link.exists():
        if link.is_dir() and not link.is_symlink():
            shutil.rmtree(link)
        else:
            link.unlink()
    link.symlink_to(target, target_is_directory=target.is_dir())


def _refresh_directory(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink()
    path.mkdir(parents=True, exist_ok=True)


def _copy_directory_once(source: Path, destination: Path) -> None:
    if destination.is_symlink() or destination.is_file():
        destination.unlink()
    if destination.exists():
        if not destination.is_dir():
            raise SystemExit(f"native jaxlib redist view path is not a directory: {destination}")
        return
    copying = destination.with_name(f"{destination.name}.copying.{os.getpid()}")
    if copying.exists():
        shutil.rmtree(copying)
    shutil.copytree(source, copying, symlinks=True)
    shutil.move(str(copying), str(destination))


def _patch_cuda_cccl_string_view_for_clang_cuda(cuda_include: Path) -> None:
    header = cuda_include / "cuda" / "std" / "string_view"
    if not header.is_file():
        return
    text = header.read_text(encoding="utf-8")
    for old, new in CCCL_STRING_VIEW_CLANG_CUDA_REPLACEMENTS:
        if old in text:
            text = text.replace(old, new, 1)
        elif new not in text:
            raise SystemExit(
                "native jaxlib CUDA redist patch could not find expected "
                f"CCCL string_view deduction guide in {header}"
            )
    header.chmod(header.stat().st_mode | stat.S_IWUSR)
    header.write_text(text, encoding="utf-8")


def _materialize_cuda_include_view(source: Path, destination: Path) -> None:
    _copy_directory_once(source, destination)
    _patch_cuda_cccl_string_view_for_clang_cuda(destination)


def _missing_cuda_cccl_source_dirs(source: Path) -> list[str]:
    return [name for name in JAX_LOCAL_CCCL_SOURCE_DIRS if not (source / name).is_dir()]


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _cuda_cccl_source_points_to_destination(source: Path, destination: Path) -> bool:
    destination_resolved = destination.resolve(strict=False)
    source_resolved = source.resolve(strict=False)
    if source_resolved == destination_resolved or _is_relative_to(source_resolved, destination_resolved):
        return True
    for name in JAX_LOCAL_CCCL_SOURCE_DIRS:
        candidate = source / name
        if not candidate.is_symlink():
            continue
        candidate_resolved = candidate.resolve(strict=False)
        if candidate_resolved == destination_resolved or _is_relative_to(candidate_resolved, destination_resolved):
            return True
    return False


def _materialize_cuda_cccl_view(
    source: Path,
    destination: Path,
    *,
    required: bool,
) -> str | None:
    missing = _missing_cuda_cccl_source_dirs(source) if source.is_dir() else []
    points_to_destination = source.is_dir() and _cuda_cccl_source_points_to_destination(source, destination)
    if not source.is_dir() or missing or points_to_destination:
        cached_source = _extract_cached_cuda_cccl_archive(destination.parent)
        if cached_source is not None:
            source = cached_source
            missing = _missing_cuda_cccl_source_dirs(source)
            points_to_destination = False
    if not source.is_dir():
        if required:
            raise SystemExit(
                "native jaxlib offline build requires a prefetched cuda_cccl repository; "
                f"missing {source}"
            )
        return None
    if points_to_destination:
        raise SystemExit(
            "native jaxlib prefetched cuda_cccl repository points at the local redist view; "
            f"missing cached CCCL archive under {_repository_cache_root()}"
        )
    if missing:
        raise SystemExit(
            "native jaxlib prefetched cuda_cccl repository is missing source dirs: "
            + ", ".join(missing)
            + f" under {source}"
        )

    marker = destination / ".vaso-cccl-view.json"
    desired = {
        "schema_version": 1,
        "layout": "xla-cccl-source-dirs-v2",
        "source_dirs": list(JAX_LOCAL_CCCL_SOURCE_DIRS),
        "source": str(source.resolve(strict=False)),
        "string_view_patch_sha256": CCCL_STRING_VIEW_CLANG_CUDA_PATCH_SHA256,
    }
    if marker.is_file():
        try:
            current = json.loads(marker.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            current = {}
        if current == desired:
            return str(destination)

    if destination.exists() or destination.is_symlink():
        if destination.is_dir() and not destination.is_symlink():
            shutil.rmtree(destination)
        else:
            destination.unlink()
    copying = destination.with_name(f"{destination.name}.copying.{os.getpid()}")
    if copying.exists():
        shutil.rmtree(copying)
    for name in JAX_LOCAL_CCCL_SOURCE_DIRS:
        shutil.copytree(source / name, copying / name, symlinks=True)
    _patch_cuda_cccl_string_view_for_clang_cuda(copying / "libcudacxx" / "include")
    (copying / marker.name).write_text(json.dumps(desired, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    shutil.move(str(copying), str(destination))
    return str(destination)


def _repository_cache_root() -> Path:
    value = os.environ.get("VASO_BAZEL_REPOSITORY_CACHE") or "/vaso/cache/bazel/repository-cache"
    return Path(value)


def _extract_cached_cuda_cccl_archive(destination_root: Path) -> Path | None:
    archive = (
        _repository_cache_root()
        / "content_addressable"
        / "sha256"
        / JAX_LOCAL_CCCL_ARCHIVE_SHA256
        / "file"
    )
    destination = destination_root / "cccl-src-v3.2.0"
    marker = destination / ".vaso-cccl-archive.json"
    desired = {
        "schema_version": 1,
        "archive_sha256": JAX_LOCAL_CCCL_ARCHIVE_SHA256,
        "strip_prefix": JAX_LOCAL_CCCL_ARCHIVE_STRIP_PREFIX,
    }
    if marker.is_file():
        try:
            current = json.loads(marker.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            current = {}
        if current == desired:
            return destination
    if not archive.is_file():
        return None
    if _file_sha256(archive) != JAX_LOCAL_CCCL_ARCHIVE_SHA256:
        raise SystemExit(f"native jaxlib cached CCCL archive sha256 mismatch: {archive}")

    if destination.exists() or destination.is_symlink():
        if destination.is_dir() and not destination.is_symlink():
            shutil.rmtree(destination)
        else:
            destination.unlink()
    extracting = destination.with_name(f"{destination.name}.extracting.{os.getpid()}")
    if extracting.exists():
        shutil.rmtree(extracting)
    extracting.mkdir(parents=True)
    with tarfile.open(archive, "r:gz") as handle:
        members = handle.getmembers()
        for member in members:
            _validate_tar_member(member)
        handle.extractall(extracting, members=members)
    extracted = extracting / JAX_LOCAL_CCCL_ARCHIVE_STRIP_PREFIX
    if not extracted.is_dir():
        raise SystemExit(f"native jaxlib cached CCCL archive missing {JAX_LOCAL_CCCL_ARCHIVE_STRIP_PREFIX}: {archive}")
    shutil.move(str(extracted), str(destination))
    shutil.rmtree(extracting)
    marker.write_text(json.dumps(desired, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return destination


def _cuda_toolkit_sibling(prefix: Path, name: str) -> Path | None:
    for anchor in ("bin", "include", "lib64", "targets"):
        candidate = prefix / anchor
        if not candidate.exists():
            continue
        sibling = candidate.resolve(strict=True).parent / name
        if sibling.is_dir():
            return sibling
    return None


def _redist_dir_candidates(prefix: Path, key: str, directory: str) -> list[Path]:
    candidates = [prefix / directory]
    if directory == "lib":
        candidates.append(prefix / "lib64")
    if key == "cuda" and directory == "nvml":
        sibling = _cuda_toolkit_sibling(prefix, "nvml")
        if sibling is not None:
            candidates.append(sibling)
    return candidates


def _materialize_jax_local_redist_views(prefixes: dict[str, str], build_work: Path) -> dict[str, str]:
    views_root = build_work / "jax-local-redists"
    views_root.mkdir(parents=True, exist_ok=True)
    views: dict[str, str] = {}
    for key in JAX_LOCAL_REDIST_KEYS:
        source = Path(prefixes[key])
        view = views_root / key
        view.mkdir(parents=True, exist_ok=True)
        for directory in JAX_LOCAL_REDIST_DIRS[key]:
            target = next((path for path in _redist_dir_candidates(source, key, directory) if path.is_dir()), None)
            link = view / directory
            if target is None:
                if (key, directory) not in JAX_LOCAL_REDIST_OPTIONAL_DIRS:
                    raise SystemExit(f"native jaxlib redist view missing {key}:{directory} under {source}")
                _refresh_directory(link)
                continue
            if key == "cuda" and directory == "include":
                _materialize_cuda_include_view(target, link)
                continue
            _refresh_symlink(link, target)
        views[key] = str(view)
    return views


def _append_or_replace_repo_env_arg(args: list[str], name: str, value: str) -> list[str]:
    prefix = f"--bazel_options=--repo_env={name}="
    replacement = prefix + value
    updated = []
    replaced = False
    for arg in args:
        if arg.startswith(prefix):
            updated.append(replacement)
            replaced = True
        else:
            updated.append(arg)
    if not replaced:
        updated.append(replacement)
    return updated


def _plan_doc_with_jax_local_redist_views(
    plan_doc: dict,
    prefixes: dict[str, str],
    build_work: Path,
    *,
    require_cccl: bool = False,
) -> dict:
    views = _materialize_jax_local_redist_views(prefixes, build_work)
    cccl_view = _materialize_cuda_cccl_view(
        _nested_bazel_output_base(build_work) / "external" / "cuda_cccl",
        build_work / "jax-local-redists" / "cccl",
        required=require_cccl,
    )
    if cccl_view is not None:
        views["cccl"] = cccl_view
    replacements = {prefixes[key]: views[key] for key in JAX_LOCAL_REDIST_KEYS}
    effective = copy.deepcopy(plan_doc)
    effective["jax_local_redist_prefixes"] = views
    build_env = dict(effective.get("build_env", {}))
    for key, env_names in JAX_LOCAL_REDIST_ENV.items():
        for env_name in env_names:
            if env_name in build_env:
                build_env[env_name] = views[key]
    if cccl_view is not None:
        build_env[JAX_LOCAL_CCCL_REPO_ENV] = cccl_view
    if "PATH" in build_env:
        build_env["PATH"] = _replace_path_segments(str(build_env["PATH"]), replacements)
    effective["build_env"] = build_env
    effective["build_py_args"] = [
        _replace_known_prefixes(str(arg), replacements)
        for arg in effective.get("build_py_args", [])
    ]
    if cccl_view is not None:
        effective["build_py_args"] = _append_or_replace_repo_env_arg(
            effective["build_py_args"],
            JAX_LOCAL_CCCL_REPO_ENV,
            cccl_view,
        )
    return effective


def _run_checked_logged(
    argv: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
    label: str,
    log_path: Path,
) -> subprocess.CompletedProcess[str]:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("w", encoding="utf-8") as log:
        log.write("$ " + shlex.join(argv) + "\n")
        log.write("cwd=" + str(cwd) + "\n")
        log.flush()
        result = subprocess.run(
            argv,
            cwd=cwd,
            env=env,
            check=False,
            text=True,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
    if result.returncode != 0:
        try:
            lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            lines = []
        tail = "\n".join(lines[-80:])
        if tail:
            print(tail, file=sys.stderr)
        raise SystemExit(f"{label} failed with rc={result.returncode}; log={log_path}")
    print(f"{label}: log={log_path}")
    return result


def _without_offline_bazel_downloads(args: list[str]) -> list[str]:
    filtered = []
    for arg in args:
        if arg == "--bazel_options=--repository_disable_download":
            continue
        if arg.startswith("--bazel_options=--repository_disable_download="):
            continue
        filtered.append(arg)
    return filtered


def _nested_bazel_output_base(build_work: Path) -> Path:
    output_base = build_work / "bazel-output-base"
    output_base.mkdir(parents=True, exist_ok=True)
    return output_base


def _nested_bazel_startup_options(plan_doc: dict, build_work: Path) -> list[str]:
    options = []
    for arg in plan_doc.get("build_py_args", []):
        if str(arg).startswith("--bazel_startup_options="):
            options.append(str(arg).split("=", 1)[1])
    output_base = "--output_base=" + str(_nested_bazel_output_base(build_work))
    if not any(option.startswith("--output_base=") for option in options):
        options.append(output_base)
    return options


def _nested_bazel_build_options(plan_doc: dict) -> list[str]:
    options = []
    for arg in _without_offline_bazel_downloads([str(item) for item in plan_doc.get("build_py_args", [])]):
        if arg.startswith("--bazel_options="):
            options.append(arg.split("=", 1)[1])
    return options


def _build_py_args_for_work(
    plan_doc: dict,
    prefixes: dict[str, str],
    build_work: Path,
    *,
    offline: bool,
) -> list[str]:
    args = [str(item) for item in plan_doc.get("build_py_args", [])]
    if not offline:
        args = _without_offline_bazel_downloads(args)
    bazel_arg = "--bazel_path=" + str(Path(prefixes["bazel"]) / "bin" / "bazel")
    if not any(arg == bazel_arg or arg.startswith("--bazel_path=") for arg in args):
        args.append(bazel_arg)
    output_base = "--bazel_startup_options=--output_base=" + str(_nested_bazel_output_base(build_work))
    if not any(arg.startswith("--bazel_startup_options=--output_base=") for arg in args):
        args.append(output_base)
    return args


def _prefetch_build_py_args(plan_doc: dict, prefixes: dict[str, str], build_work: Path) -> list[str]:
    args = _build_py_args_for_work(plan_doc, prefixes, build_work, offline=False)
    if "--configure_only" not in args:
        args.append("--configure_only")
    return args


def _default_nested_bazel_targets(plan_doc: dict) -> list[str]:
    major = str(plan_doc.get("cuda", {}).get("major_version", "13"))
    return [
        "//jaxlib/tools:jaxlib_wheel",
        f"//jaxlib/tools:jax_cuda{major}_plugin_wheel",
        f"//jaxlib/tools:jax_cuda{major}_pjrt_wheel",
    ]


def _prefetch_nested_bazel(
    args: argparse.Namespace,
    plan_doc: dict,
    prefixes: dict[str, str],
    build_work: Path,
) -> tuple[Path, dict[str, object]]:
    plan_doc = _plan_doc_with_jax_local_redist_views(plan_doc, prefixes, build_work)
    build_source = _extract_source_archive(args, build_work)
    env = _minimal_build_env(plan_doc)
    python = _python_executable(prefixes, args.python_abi)
    build_py = build_source / "build" / "build.py"
    if not build_py.is_file():
        raise SystemExit(f"JAX build.py is missing under copied source: {build_py}")

    _run_checked(
        [python] + _prefetch_build_py_args(plan_doc, prefixes, build_work),
        cwd=build_source,
        env=env,
        label="native jaxlib configure for nested Bazel prefetch",
    )

    targets = [str(item) for item in plan_doc.get("nested_bazel_prefetch_targets", [])]
    if not targets:
        targets = _default_nested_bazel_targets(plan_doc)
    bazel = Path(prefixes["bazel"]) / "bin" / "bazel"
    if not bazel.is_file() or not os.access(bazel, os.X_OK):
        raise SystemExit(f"native jaxlib prefetch requires executable Bazel prefix: {bazel}")

    resolved_file = build_work / "nested_bazel_repositories.resolved.bzl"
    prefetch_log = Path(args.prefetch_log_out) if args.prefetch_log_out else build_work / "nested_bazel_prefetch.log"
    bazel_argv = (
        [str(bazel)]
        + _nested_bazel_startup_options(plan_doc, build_work)
        + [
            "build",
            "--nobuild",
            "--verbose_failures=true",
            "--experimental_repository_resolved_file=" + str(resolved_file),
        ]
        + _nested_bazel_build_options(plan_doc)
        + targets
    )
    if any(item == "--repository_disable_download" for item in bazel_argv):
        raise SystemExit("native jaxlib nested prefetch must not disable repository downloads")

    _run_checked_logged(
        bazel_argv,
        cwd=build_source,
        env=env,
        label="native jaxlib nested Bazel prefetch",
        log_path=prefetch_log,
    )
    summary = {
        "schema_version": 1,
        "mode": "prefetch-nested-bazel",
        "targets": targets,
        "argv": bazel_argv,
        "log": str(prefetch_log),
        "resolved_file": str(resolved_file),
        "repository_cache": "/vaso/cache/bazel/repository-cache",
        "distdir": "/vaso/sources/jax/distdir",
    }
    (build_work / "nested_bazel_prefetch.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return build_source, summary


def _copy_wheels(dist: Path, wheelhouse_out: Path, wheel_manifest_out: Path) -> list[dict[str, str]]:
    wheels = sorted(dist.glob("*.whl"))
    if not wheels:
        raise SystemExit(f"native jaxlib build produced no wheels under {dist}")
    if wheelhouse_out.exists():
        shutil.rmtree(wheelhouse_out)
    wheelhouse_out.mkdir(parents=True, exist_ok=True)
    manifest_wheels = []
    for wheel in wheels:
        destination = wheelhouse_out / wheel.name
        shutil.copy2(wheel, destination)
        manifest_wheels.append(
            {
                "name": wheel.name,
                "path": str(destination),
                "sha256": _file_sha256(destination),
            }
        )
    wheel_manifest_out.parent.mkdir(parents=True, exist_ok=True)
    wheel_manifest_out.write_text(
        json.dumps({"schema_version": 1, "wheels": manifest_wheels}, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return manifest_wheels


def _build_and_install(args: argparse.Namespace, plan_doc: dict, prefixes: dict[str, str], build_work: Path) -> tuple[Path, list[dict[str, str]]]:
    plan_doc = _plan_doc_with_jax_local_redist_views(plan_doc, prefixes, build_work, require_cccl=True)
    build_source = _extract_source_archive(args, build_work)
    env = _minimal_build_env(plan_doc)
    python = _python_executable(prefixes, args.python_abi)
    build_py = build_source / "build" / "build.py"
    if not build_py.is_file():
        raise SystemExit(f"JAX build.py is missing under copied source: {build_py}")

    _run_checked(
        [python] + _build_py_args_for_work(plan_doc, prefixes, build_work, offline=True),
        cwd=build_source,
        env=env,
        label="native jaxlib build.py build",
    )
    manifest_wheels = _copy_wheels(
        build_source / "dist",
        Path(args.wheelhouse_out),
        Path(args.wheel_manifest_out),
    )

    prefix_out = Path(args.prefix_out)
    if prefix_out.exists():
        shutil.rmtree(prefix_out)
    prefix_out.parent.mkdir(parents=True, exist_ok=True)
    _run_checked(
        [
            python,
            "-m",
            "pip",
            "install",
            "--no-deps",
            "--ignore-installed",
            "--no-build-isolation",
            "--no-warn-script-location",
            "--no-index",
            "--no-cache-dir",
            "--prefix",
            str(prefix_out),
        ] + [str(Path(args.wheelhouse_out) / item["name"]) for item in manifest_wheels],
        cwd=build_work,
        env=env,
        label="native jaxlib prefix install",
    )
    return build_source, manifest_wheels


def _write_dry_run_prefix(prefix: Path, python_prefix: str, python_abi: str) -> None:
    if prefix.exists():
        shutil.rmtree(prefix)
    site_packages = _python_site_packages_dir(python_prefix, python_abi)
    (prefix / "artifacts" / "wheels").mkdir(parents=True)
    (prefix / site_packages / "jaxlib").mkdir(parents=True)
    (prefix / "DRY_RUN_DO_NOT_USE_AS_JAXLIB_PREFIX").write_text(
        "Token-safe native jaxlib dry run. No wheel was built.\n",
        encoding="utf-8",
    )


def _write_dry_run_wheel_manifest(wheelhouse_out: Path, wheel_manifest_out: Path) -> None:
    if wheelhouse_out.exists():
        shutil.rmtree(wheelhouse_out)
    wheelhouse_out.mkdir(parents=True, exist_ok=True)
    wheel_manifest_out.parent.mkdir(parents=True, exist_ok=True)
    wheel_manifest_out.write_text(
        json.dumps({"schema_version": 1, "wheels": []}, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _write_metadata(
    args: argparse.Namespace,
    plan_doc: dict,
    prefixes: dict[str, str],
    source_dir: Path,
    build_work: Path | None,
    wheels: list[dict[str, str]] | None = None,
    prefetch: dict[str, object] | None = None,
) -> None:
    metadata = {
        "schema_version": 1,
        "package": "py-jaxlib",
        "version": plan_doc.get("version", "0.10.2"),
        "jax_version": plan_doc.get("jax_version", "0.10.2"),
        "python_abi": args.python_abi,
        "mechanism": "python-wheel-action",
        "source": str(source_dir),
        "source_anchor": args.source_anchor,
        "build_work": str(build_work) if plan_doc.get("will_build") and build_work is not None else "",
        "wheelhouse": args.wheelhouse_out or "",
        "wheel_manifest": args.wheel_manifest_out or "",
        "wheels": wheels or [],
        "execute_requested": bool(args.execute),
        "token_present": args.token == REQUIRED_TOKEN,
        "will_build": bool(plan_doc.get("will_build")),
        "mode": "prefetch-nested-bazel" if prefetch else plan_doc.get("mode", "dry-run"),
        "input_prefixes": prefixes,
        "rootfs_manifest": os.environ.get("VASO_ROOTFS_BUNDLE_MANIFEST", ""),
        "cuda": plan_doc.get("cuda", {}),
        "llvm": plan_doc.get("llvm", {}),
    }
    if prefetch:
        metadata["prefetch"] = prefetch
    Path(args.provider_metadata_out).write_text(
        json.dumps(metadata, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _write_result_marker(args: argparse.Namespace, plan_doc: dict, *, prefetch: bool = False) -> None:
    if prefetch:
        text = "Nested JAX Bazel repository prefetch completed.\n"
    elif plan_doc.get("will_build"):
        text = "Native jaxlib wheel build requested and prefix installation completed.\n"
    else:
        text = "Token-safe native jaxlib dry run. No wheel was built.\n"
    Path(args.result_marker_out).write_text(text, encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", required=True)
    parser.add_argument("--pins", required=True)
    parser.add_argument("--prefix-out")
    parser.add_argument("--build-work-root", default="")
    parser.add_argument("--build-plan-out", required=True)
    parser.add_argument("--provider-metadata-out", required=True)
    parser.add_argument("--result-marker-out", required=True)
    parser.add_argument("--wheelhouse-out")
    parser.add_argument("--wheel-manifest-out")
    parser.add_argument("--prefetch-log-out")
    parser.add_argument("--source-anchor", required=True)
    parser.add_argument("--source-archive", required=True)
    parser.add_argument("--prefix-file", action="append", default=[])
    parser.add_argument("--python-abi", default="derived")
    parser.add_argument("--token", default="")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--prefetch-nested-bazel", action="store_true")
    parser.add_argument("--synthetic-prefixes-for-dry-run", action="store_true")
    args = parser.parse_args(argv)

    _check_insula()
    if args.prefetch_nested_bazel and not args.execute:
        raise SystemExit("native jaxlib nested prefetch requires --execute and the build token")
    required_outputs = ["build_plan_out", "provider_metadata_out", "result_marker_out"]
    if args.prefetch_nested_bazel:
        required_outputs.append("prefetch_log_out")
    else:
        required_outputs.extend(["prefix_out", "wheelhouse_out", "wheel_manifest_out"])
    for name in required_outputs:
        if not getattr(args, name):
            parser.error(f"--{name.replace('_', '-')} is required")
    _normalize_declared_output_paths(args)
    prefixes = _read_prefix_files(args.prefix_file)
    for output in filter(None, (
        args.prefix_out,
        args.provider_metadata_out,
        args.result_marker_out,
        args.wheel_manifest_out,
        args.prefetch_log_out,
    )):
        Path(output).parent.mkdir(parents=True, exist_ok=True)
    synthetic_root: tempfile.TemporaryDirectory[str] | None = None
    if args.synthetic_prefixes_for_dry_run:
        if args.execute or args.prefetch_nested_bazel:
            raise SystemExit("synthetic prefixes are allowed only for token-safe dry-run actions")
        output_scratch = (
            Path(args.build_plan_out).resolve(strict=False).parent
            / (Path(args.build_plan_out).name + ".scratch")
        )
        synthetic_root = tempfile.TemporaryDirectory(
            prefix="vaso-jaxlib-prefixes-",
            dir=str(_required_tmpdir(fallback=output_scratch)),
        )
        prefixes = _materialize_synthetic_prefixes(Path(synthetic_root.name), args.python_abi)

    source_dir = _source_dir_for_action(args)
    if not args.execute:
        _write_dry_run_prefix(Path(args.prefix_out), prefixes["python"], args.python_abi)
        _write_dry_run_wheel_manifest(Path(args.wheelhouse_out), Path(args.wheel_manifest_out))
    rc, plan_doc = _run_plan(args, prefixes)
    if rc != 0:
        return rc
    build_work = _estate_build_work_dir(args, prefixes) if plan_doc.get("will_build") else None
    wheels: list[dict[str, str]] = []
    if args.prefetch_nested_bazel:
        if build_work is None:
            raise SystemExit("native jaxlib nested prefetch requires a buildable plan")
        source_dir, prefetch = _prefetch_nested_bazel(args, plan_doc, prefixes, build_work)
        _write_metadata(args, plan_doc, prefixes, source_dir, build_work, wheels, prefetch=prefetch)
        _write_result_marker(args, plan_doc, prefetch=True)
    elif plan_doc.get("will_build"):
        source_dir, wheels = _build_and_install(args, plan_doc, prefixes, build_work)
        _write_metadata(args, plan_doc, prefixes, source_dir, build_work, wheels)
        _write_result_marker(args, plan_doc)
    else:
        _write_metadata(args, plan_doc, prefixes, source_dir, build_work, wheels)
        _write_result_marker(args, plan_doc)
    if synthetic_root is not None:
        synthetic_root.cleanup()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
