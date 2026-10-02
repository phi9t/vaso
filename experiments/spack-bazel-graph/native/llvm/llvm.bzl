"""Native LLVM build-action skeleton.

This rule records and verifies the exact CMake interface for the hermetic Spack
llvm@20.1.8 node. It does not flip the graph or run the full LLVM build unless
the trusted request provides VASO_NATIVE_LLVM_TOKEN=build-native-llvm.
"""

_PLAN_BUILD = """\
package(default_visibility = ["//visibility:public"])

exports_files(["build_plan.json", "build.sh"])
"""

_NATIVE_BUILD = """\
load("@rules_cc//cc:defs.bzl", "cc_library")

package(default_visibility = ["//visibility:public"])

filegroup(
    name = "prefix",
    srcs = glob(["prefix/**"], allow_empty = False),
)

exports_files(["prefix_path.txt"])

cc_library(
    name = "lib",
    hdrs = glob(["prefix/include/**"], allow_empty = True),
    includes = ["prefix/include"],
    linkopts = {linkopts},
)
"""

_BUILD_SH = """\
#!/usr/bin/env bash
set -euo pipefail
# spack-build-system: cmake
SRC="$1"
PREFIX="$2"

if [[ "${VASO_IN_INSULA:-0}" != "1" ]]; then
  echo "llvm native build must run inside the hermetic insula (VASO_IN_INSULA=1)" >&2
  exit 2
fi
for var in CMAKE_PREFIX NINJA_PREFIX PYTHON_PREFIX PKGCONF_PREFIX PERL_DATA_DUMPER_PREFIX HWLOC_PREFIX LIBEDIT_PREFIX LIBFFI_PREFIX LIBXML2_PREFIX LUA_PREFIX NCURSES_PREFIX SWIG_PREFIX XZ_PREFIX ZLIB_NG_PREFIX; do
  if [[ -z "${!var:-}" || ! -d "${!var}" ]]; then
    echo "$var must name a Bazel-built prefix" >&2
    exit 2
  fi
done
if [[ ! -x "$CMAKE_PREFIX/bin/cmake" ]]; then echo "missing CMake binary" >&2; exit 2; fi
if [[ ! -x "$NINJA_PREFIX/bin/ninja-build" ]]; then echo "missing Ninja binary" >&2; exit 2; fi
if [[ ! -x "$PYTHON_PREFIX/bin/python3.14" ]]; then echo "missing Python 3.14 binary" >&2; exit 2; fi
if [[ ! -x "$PKGCONF_PREFIX/bin/pkgconf" ]]; then echo "missing pkgconf binary" >&2; exit 2; fi
if [[ ! -x "$SWIG_PREFIX/bin/swig" ]]; then echo "missing SWIG binary" >&2; exit 2; fi

CMAKE="$CMAKE_PREFIX/bin/cmake"
NINJA="$NINJA_PREFIX/bin/ninja-build"
BUILD="$(mktemp -d)"
cd "$BUILD"

spack_target_flags="-march=icelake-client -mtune=icelake-client"
prefix_path="${CMAKE_PREFIX};${NINJA_PREFIX};${PYTHON_PREFIX};${PKGCONF_PREFIX};${PERL_DATA_DUMPER_PREFIX};${HWLOC_PREFIX};${LIBEDIT_PREFIX};${LIBFFI_PREFIX};${LIBXML2_PREFIX};${LUA_PREFIX};${NCURSES_PREFIX};${SWIG_PREFIX};${XZ_PREFIX};${ZLIB_NG_PREFIX}"
export PATH="${CMAKE_PREFIX}/bin:${NINJA_PREFIX}/bin:${PYTHON_PREFIX}/bin:${PKGCONF_PREFIX}/bin:${LUA_PREFIX}/bin:${SWIG_PREFIX}/bin:${PATH}"
export CMAKE_PREFIX_PATH="$prefix_path"
export PKG_CONFIG="$PKGCONF_PREFIX/bin/pkgconf"
export PKG_CONFIG_PATH="${HWLOC_PREFIX}/lib/pkgconfig:${LIBEDIT_PREFIX}/lib/pkgconfig:${LIBFFI_PREFIX}/lib/pkgconfig:${LIBXML2_PREFIX}/lib/pkgconfig:${LUA_PREFIX}/lib/pkgconfig:${NCURSES_PREFIX}/lib/pkgconfig:${XZ_PREFIX}/lib/pkgconfig:${ZLIB_NG_PREFIX}/lib/pkgconfig"

"$CMAKE" -G Ninja "$SRC/llvm" \
  -DCMAKE_MAKE_PROGRAM:FILEPATH="$NINJA" \
  -DCMAKE_INSTALL_PREFIX:STRING="$PREFIX" \
  -DCMAKE_INSTALL_RPATH_USE_LINK_PATH:BOOL=ON \
  "-DCMAKE_INSTALL_RPATH:STRING=${PREFIX}/lib;${PREFIX}/lib64" \
  "-DCMAKE_PREFIX_PATH:STRING=${prefix_path}" \
  -DCMAKE_FIND_PACKAGE_PREFER_CONFIG:BOOL=ON \
  -DCMAKE_FIND_USE_PACKAGE_ROOT_PATH:BOOL=OFF \
  -DCMAKE_FIND_USE_SYSTEM_PACKAGE_REGISTRY:BOOL=OFF \
  -DCMAKE_FIND_USE_PACKAGE_REGISTRY:BOOL=OFF \
  -DCMAKE_FIND_USE_SYSTEM_PATH:BOOL=OFF \
  -DCMAKE_C_COMPILER:FILEPATH=/usr/bin/gcc \
  -DCMAKE_CXX_COMPILER:FILEPATH=/usr/bin/g++ \
  "-DCMAKE_C_FLAGS:STRING=${spack_target_flags}" \
  "-DCMAKE_CXX_FLAGS:STRING=${spack_target_flags}" \
  -DCMAKE_BUILD_TYPE:STRING=Release \
  -DCMAKE_INTERPROCEDURAL_OPTIMIZATION:BOOL=OFF \
  -DCMAKE_POLICY_DEFAULT_CMP0090:STRING=NEW \
  -DCMAKE_EXPORT_COMPILE_COMMANDS:BOOL=ON \
  -DBUILD_SHARED_LIBS:BOOL=OFF \
  -DCLANG_DEFAULT_OPENMP_RUNTIME:STRING=libomp \
  -DCLANG_OPENMP_NVPTX_DEFAULT_ARCH:STRING=sm_100 \
  -DCUDA_NVCC_EXECUTABLE:STRING=IGNORE \
  -DCUDA_SDK_ROOT_DIR:STRING=IGNORE \
  -DCUDA_TOOLKIT_ROOT_DIR:STRING=IGNORE \
  -DLIBOMPTARGET_DEP_CUDA_DRIVER_LIBRARIES:STRING=IGNORE \
  -DLIBOMPTARGET_BUILD_AMDGPU_PLUGIN:BOOL=OFF \
  -DLIBOMPTARGET_ENABLE_DEBUG:BOOL=OFF \
  -DLLDB_ENABLE_LIBEDIT:BOOL=ON \
  -DLLDB_ENABLE_CURSES:BOOL=ON \
  -DLLDB_ENABLE_LIBXML2:BOOL=ON \
  -DLLDB_ENABLE_LUA:BOOL=ON \
  -DLLDB_ENABLE_LZMA:BOOL=ON \
  -DLLDB_ENABLE_PYTHON:BOOL=OFF \
  -DLLDB_CURSES_LIBS:STRING=-lncursesw \
  -DLLVM_BUILD_LLVM_DYLIB:BOOL=ON \
  -DLLVM_ENABLE_EH:BOOL=ON \
  -DLLVM_ENABLE_LIBXML2:BOOL=OFF \
  "-DLLVM_ENABLE_PROJECTS:STRING=lldb;clang;clang-tools-extra;lld;polly" \
  -DLLVM_ENABLE_RTTI:BOOL=ON \
  "-DLLVM_ENABLE_RUNTIMES:STRING=openmp;offload;compiler-rt;libcxx;libcxxabi;libunwind" \
  -DLLVM_ENABLE_TERMINFO:BOOL=ON \
  -DLLVM_ENABLE_Z3_SOLVER:BOOL=OFF \
  -DLLVM_ENABLE_ZSTD:BOOL=OFF \
  -DLLVM_LINK_LLVM_DYLIB:BOOL=OFF \
  -DLLVM_REQUIRES_RTTI:BOOL=ON \
  "-DLLVM_TARGETS_TO_BUILD:STRING=AArch64;AMDGPU;NVPTX;X86" \
  -DLLVM_USE_SPLIT_DWARF:BOOL=OFF \
  -DOPENMP_ENABLE_LIBOMPTARGET:BOOL=ON \
  -DRUNTIMES_CMAKE_ARGS:STRING=-DCMAKE_INSTALL_RPATH_USE_LINK_PATH=ON \
  "-DLIBOMP_HWLOC_INSTALL_DIR:PATH=${HWLOC_PREFIX}" \
  -DLIBOMP_USE_HWLOC:BOOL=ON \
  -DLIBOMP_TSAN_SUPPORT:BOOL=OFF \
  -DLIBCXX_ENABLE_STATIC_ABI_LIBRARY:BOOL=ON \
  -DLIBCXXABI_USE_LLVM_UNWINDER:BOOL=ON \
  "-Dpython_executable:FILEPATH=${PYTHON_PREFIX}/bin/python3.14"

"$NINJA" -v
"$NINJA" install
"""

_REQUIRED_TOKEN = "build-native-llvm"


def _read_prefix(repository_ctx, label, dep_name):
    value = repository_ctx.read(label).strip()
    if not value:
        fail("llvm native build requires a non-empty {} prefix path".format(dep_name))
    return value


def _llvm_native_impl(repository_ctx):
    attr = repository_ctx.attr
    if repository_ctx.os.environ.get("VASO_IN_INSULA") != "1":
        fail("llvm native planning/build must run inside the hermetic insula (VASO_IN_INSULA=1)")

    prefix_inputs = {
        "cmake": _read_prefix(repository_ctx, attr.cmake_prefix_file, "cmake"),
        "ninja": _read_prefix(repository_ctx, attr.ninja_prefix_file, "ninja"),
        "python": _read_prefix(repository_ctx, attr.python_prefix_file, "python"),
        "pkgconf": _read_prefix(repository_ctx, attr.pkgconf_prefix_file, "pkgconf"),
        "perl_data_dumper": _read_prefix(repository_ctx, attr.perl_data_dumper_prefix_file, "perl-data-dumper"),
        "hwloc": _read_prefix(repository_ctx, attr.hwloc_prefix_file, "hwloc"),
        "libedit": _read_prefix(repository_ctx, attr.libedit_prefix_file, "libedit"),
        "libffi": _read_prefix(repository_ctx, attr.libffi_prefix_file, "libffi"),
        "libxml2": _read_prefix(repository_ctx, attr.libxml2_prefix_file, "libxml2"),
        "lua": _read_prefix(repository_ctx, attr.lua_prefix_file, "lua"),
        "ncurses": _read_prefix(repository_ctx, attr.ncurses_prefix_file, "ncurses"),
        "swig": _read_prefix(repository_ctx, attr.swig_prefix_file, "swig"),
        "xz": _read_prefix(repository_ctx, attr.xz_prefix_file, "xz"),
        "zlib_ng": _read_prefix(repository_ctx, attr.zlib_ng_prefix_file, "zlib-ng"),
    }
    env = {
        "CMAKE_PREFIX": prefix_inputs["cmake"],
        "NINJA_PREFIX": prefix_inputs["ninja"],
        "PYTHON_PREFIX": prefix_inputs["python"],
        "PKGCONF_PREFIX": prefix_inputs["pkgconf"],
        "PERL_DATA_DUMPER_PREFIX": prefix_inputs["perl_data_dumper"],
        "HWLOC_PREFIX": prefix_inputs["hwloc"],
        "LIBEDIT_PREFIX": prefix_inputs["libedit"],
        "LIBFFI_PREFIX": prefix_inputs["libffi"],
        "LIBXML2_PREFIX": prefix_inputs["libxml2"],
        "LUA_PREFIX": prefix_inputs["lua"],
        "NCURSES_PREFIX": prefix_inputs["ncurses"],
        "SWIG_PREFIX": prefix_inputs["swig"],
        "XZ_PREFIX": prefix_inputs["xz"],
        "ZLIB_NG_PREFIX": prefix_inputs["zlib_ng"],
        "VASO_IN_INSULA": "1",
    }
    plan_args = [
        prefix_inputs["python"] + "/bin/python3.14",
        str(repository_ctx.path(attr._plan)),
        "--out",
        "build_plan.json",
    ]
    for key, value in sorted(prefix_inputs.items()):
        plan_args.extend(["--prefix", "{}={}".format(key, value)])
    plan_res = repository_ctx.execute(plan_args, quiet = False, environment = env)
    repository_ctx.file("build.sh", _BUILD_SH, executable = True)

    token = repository_ctx.os.environ.get("VASO_NATIVE_LLVM_TOKEN", "")
    authorized = token == _REQUIRED_TOKEN
    if not authorized:
        repository_ctx.file("BUILD.bazel", _PLAN_BUILD)
        return
    if plan_res.return_code != 0:
        fail("llvm native preflight failed:\n{}\n{}".format(plan_res.stdout, plan_res.stderr))

    fail(
        "llvm_native full build is gated and intentionally not implemented in this " +
        "checkpoint. Wire the build.sh execution after the plan and prefix/ABI " +
        "gates are reviewed, then issue VASO_NATIVE_LLVM_TOKEN=build-native-llvm.",
    )


llvm_native = repository_rule(
    implementation = _llvm_native_impl,
    attrs = {
        "urls": attr.string_list(mandatory = True),
        "sha256": attr.string(mandatory = True),
        "strip_prefix": attr.string(mandatory = True),
        "cmake_prefix_file": attr.label(mandatory = True, allow_single_file = True),
        "ninja_prefix_file": attr.label(mandatory = True, allow_single_file = True),
        "python_prefix_file": attr.label(mandatory = True, allow_single_file = True),
        "pkgconf_prefix_file": attr.label(mandatory = True, allow_single_file = True),
        "perl_data_dumper_prefix_file": attr.label(mandatory = True, allow_single_file = True),
        "hwloc_prefix_file": attr.label(mandatory = True, allow_single_file = True),
        "libedit_prefix_file": attr.label(mandatory = True, allow_single_file = True),
        "libffi_prefix_file": attr.label(mandatory = True, allow_single_file = True),
        "libxml2_prefix_file": attr.label(mandatory = True, allow_single_file = True),
        "lua_prefix_file": attr.label(mandatory = True, allow_single_file = True),
        "ncurses_prefix_file": attr.label(mandatory = True, allow_single_file = True),
        "swig_prefix_file": attr.label(mandatory = True, allow_single_file = True),
        "xz_prefix_file": attr.label(mandatory = True, allow_single_file = True),
        "zlib_ng_prefix_file": attr.label(mandatory = True, allow_single_file = True),
        "_plan": attr.label(default = "//native/llvm:plan.py", allow_single_file = True),
    },
    environ = ["VASO_NATIVE_LLVM_TOKEN", "VASO_IN_INSULA"],
    doc = "Plan and eventually build LLVM 20.1.8 into a Spack-compatible prefix.",
)
