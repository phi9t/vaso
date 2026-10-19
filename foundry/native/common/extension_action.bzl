"""Public Vaso PyTorch extension rules."""

load("//native/common:extension_info.bzl", "VasoTorchExtensionInfo")
load("//native/common:native_action_inputs.bzl", "native_action_rootfs_identity_attrs", "native_action_rootfs_identity_file")
load("//native/common:release_wheel_action.bzl", "release_wheel_action")
load("//native/pytorch:pytorch_action.bzl", "PytorchNativePrefixInfo")
load("@rules_cc//cc:defs.bzl", "CcInfo")
load("@rules_cc//cc:find_cc_toolchain.bzl", "CC_TOOLCHAIN_ATTRS", "find_cpp_toolchain", "use_cc_toolchain")

_PROFILE = "torch"
_CXX_STANDARD = "c++20"
_CXX11_ABI = "1"
_FIXTURE_PYTHON_ABI = "cp" + "3" + "13"
_EXTENSION_ROOTFS_PLATFORM = str(Label("//native/common:torch_extension_rootfs_platform"))
_EXTENSION_CUDA_ROOTFS_MANIFEST = Label("//native/common:torch_extension_cuda_rootfs")
_EXTENSION_NCCL_ROOTFS_MANIFEST = Label("//native/common:torch_extension_nccl_rootfs")
_EXTENSION_NVSHMEM_ROOTFS_MANIFEST = Label("//native/common:torch_extension_nvshmem_rootfs")
_LINES = ("cu129", "cu130")
_EXTENSION_ENV = {
    "LC_ALL": "C",
    "PATH": "/usr/bin:/bin",
    "PYTHONHASHSEED": "0",
    "VASO_IN_INSULA": "1",
    "VASO_ROOTFS_BUNDLE_MANIFEST": "/run/vaso/rootfs-bundle.json",
}
_EXTENSION_CC_TOOLCHAINS = {
    "vaso-rootfs-clang-23": {
        "cc": "/usr/lib/llvm-23/bin/clang",
        "cxx": "/usr/lib/llvm-23/bin/clang++",
    },
    "vaso-rootfs-gcc-13.3": {
        "cc": "/usr/bin/gcc",
        "cxx": "/usr/bin/g++",
    },
}

def _public_torch_artifact_prefix():
    return select({
        "@vaso_graph//plans:release_line_cu129": "@vaso_torch_artifact_cu129//:prefix",
        "@vaso_graph//plans:release_line_cu130": "@vaso_torch_artifact_cu130//:prefix",
    })


def _extension_toolchains():
    return use_cc_toolchain() + [
        "//toolchains:cuda_toolchain_type",
    ]


def _extension_rootfs_platform_transition_impl(settings, attr):
    return {"//command_line_option:platforms": _EXTENSION_ROOTFS_PLATFORM}


_extension_rootfs_platform_transition = transition(
    implementation = _extension_rootfs_platform_transition_impl,
    inputs = [],
    outputs = ["//command_line_option:platforms"],
)


def _extension_rootfs_platform_forward_attrs():
    return {
        "actual": attr.label(
            mandatory = True,
            cfg = _extension_rootfs_platform_transition,
        ),
        "_allowlist_function_transition": attr.label(
            default = Label("@bazel_tools//tools/allowlists/function_transition_allowlist"),
        ),
    }


def _extension_cc_paths(toolchain_identifier):
    paths = _EXTENSION_CC_TOOLCHAINS.get(toolchain_identifier)
    if paths == None:
        fail("vaso_torch_extension C++ toolchain metadata is missing for {}".format(toolchain_identifier))
    return paths


def _resolve_extension_toolchain(ctx, *, output_basename):
    cc_toolchain = find_cpp_toolchain(ctx)
    cuda = ctx.toolchains["//toolchains:cuda_toolchain_type"].cuda
    cc_paths = _extension_cc_paths(cc_toolchain.toolchain_id)
    toolchain_json = ctx.actions.declare_file(output_basename + "_native_toolchain.json")
    payload = {
        "schema_version": 1,
        "cc": {
            "cc": cc_paths["cc"],
            "cxx": cc_paths["cxx"],
            "toolchain_identifier": cc_toolchain.toolchain_id,
        },
        "cuda": {
            "arch_list": cuda.cuda_arch_list,
            "home": cuda.cuda_home,
            "nvcc": cuda.nvcc,
            "prefix_file": str(_EXTENSION_CUDA_ROOTFS_MANIFEST),
            "providers": [
                {
                    "label": str(_EXTENSION_CUDA_ROOTFS_MANIFEST),
                    "package": "cuda",
                },
            ],
        },
    }
    ctx.actions.write(
        output = toolchain_json,
        content = json.encode_indent(payload, indent = "  ") + "\n",
    )
    return struct(
        all_files = cc_toolchain.all_files,
        cc = cc_paths["cc"],
        cxx = cc_paths["cxx"],
        nvcc = cuda.nvcc,
        toolchain_identifier = cc_toolchain.toolchain_id,
        toolchain_json = toolchain_json,
    )


def _single_file(target, *, attr_name):
    files = target.files.to_list()
    if len(files) != 1:
        fail("{} must provide exactly one file, got {}".format(attr_name, len(files)))
    return files[0]


def _prefix_path_from_manifest(manifest):
    return manifest.dirname + "/prefix"

def _static_library_path(prefix, library_name):
    return prefix + "/lib/" + library_name

def _is_torch_artifact_prefix(torch_provider):
    return torch_provider.prefix.basename == ".vaso_torch_artifact_prefix"

def _torch_prefix_path(torch_provider):
    if _is_torch_artifact_prefix(torch_provider):
        return torch_provider.prefix.dirname
    return torch_provider.prefix.path

def _python_version_from_abi(python_abi):
    if not python_abi.startswith("cp") or len(python_abi) < 4:
        fail("cannot derive Python version from ABI {}".format(python_abi))
    return python_abi[2:3] + "." + python_abi[3:]

def _torch_include_roots(torch_provider, torch_prefix_path):
    if _is_torch_artifact_prefix(torch_provider):
        torch_package = torch_prefix_path + "/lib/python" + _python_version_from_abi(torch_provider.python_abi) + "/site-packages/torch"
        return [
            torch_prefix_path + "/include",
            torch_prefix_path + "/torch/include",
            torch_package + "/include",
        ]
    return [torch_prefix_path + "/torch/include"]

def _torch_lib_roots(torch_provider, torch_prefix_path):
    if _is_torch_artifact_prefix(torch_provider):
        torch_package = torch_prefix_path + "/lib/python" + _python_version_from_abi(torch_provider.python_abi) + "/site-packages/torch"
        return [
            torch_prefix_path + "/lib",
            torch_prefix_path + "/torch/lib",
            torch_package + "/lib",
        ]
    return [torch_prefix_path + "/torch/lib"]


def _deps_files(targets):
    files = []
    for target in targets:
        files.extend(target[DefaultInfo].files.to_list())
        if CcInfo in target:
            compilation = target[CcInfo].compilation_context
            files.extend(compilation.headers.to_list())
            linking = target[CcInfo].linking_context
            for linker_input in linking.linker_inputs.to_list():
                for library in linker_input.libraries:
                    if library.static_library:
                        files.append(library.static_library)
                    if library.pic_static_library:
                        files.append(library.pic_static_library)
                    if library.dynamic_library:
                        files.append(library.dynamic_library)
                    if library.interface_library:
                        files.append(library.interface_library)
    return files


def _deps_args(targets):
    args = []
    for target in targets:
        if CcInfo not in target:
            for file in target[DefaultInfo].files.to_list():
                args.extend(["--dep-file", file.path])
            continue
        compilation = target[CcInfo].compilation_context
        for include in compilation.includes.to_list():
            args.extend(["--include-dir", include])
        for include in compilation.quote_includes.to_list():
            args.extend(["--include-dir", include])
        for include in compilation.system_includes.to_list():
            args.extend(["--system-include-dir", include])
        for define in compilation.defines.to_list():
            args.extend(["--define", define])
        linking = target[CcInfo].linking_context
        for linker_input in linking.linker_inputs.to_list():
            for library in linker_input.libraries:
                if library.pic_static_library:
                    args.extend(["--link-file", library.pic_static_library.path])
                elif library.static_library:
                    args.extend(["--link-file", library.static_library.path])
                elif library.dynamic_library:
                    args.extend(["--link-file", library.dynamic_library.path])
                elif library.interface_library:
                    args.extend(["--link-file", library.interface_library.path])
            for user_link_flag in linker_input.user_link_flags:
                args.append("--link-flag=" + user_link_flag)
    return args


def _validate_profile(profile):
    if profile != _PROFILE:
        fail("profile must be torch, got {}".format(profile))


def _default_extension_rootfs_identity(profile):
    return select({
        str(Label("//plans:release_line_{}".format(line))): str(Label("//plans:{}/{}/rootfs.identity.json".format(profile, line)))
        for line in _LINES
    })


def _torch_extension_test_provider_impl(ctx):
    prefix = ctx.actions.declare_directory(ctx.label.name + "_prefix")
    wheel = ctx.actions.declare_file(ctx.label.name + "-0.1.0-" + _FIXTURE_PYTHON_ABI + "-" + _FIXTURE_PYTHON_ABI + "-linux_x86_64.whl")
    metadata = ctx.actions.declare_file(ctx.label.name + "_provider_metadata.json")
    ctx.actions.run_shell(
        inputs = [ctx.file.plan],
        outputs = [prefix, wheel, metadata],
        command = """\
set -euo pipefail
prefix="$1"
wheel="$2"
metadata="$3"
mkdir -p "$prefix/torch/include/torch/csrc/api/include" "$prefix/torch/lib"
touch "$prefix/torch/include/torch/extension.h"
for lib in libtorch.so libtorch_cpu.so libtorch_cuda.so libtorch_python.so libc10.so libc10_cuda.so; do
  printf '%s\n' 'INPUT(-lc)' > "$prefix/torch/lib/$lib"
done
touch "$wheel"
printf '%s\n' '{"schema_version":1,"package":"py-torch","version":"2.14.0"}' > "$metadata"
""",
        arguments = [prefix.path, wheel.path, metadata.path],
        env = {
            "LC_ALL": "C",
            "PATH": "/usr/bin:/bin",
            "PYTHONHASHSEED": "0",
        },
        mnemonic = "VasoTorchExtensionTestProvider",
        use_default_shell_env = False,
    )
    return [
        DefaultInfo(files = depset([prefix, wheel, ctx.file.plan, metadata])),
        PytorchNativePrefixInfo(
            prefix = prefix,
            wheel = wheel,
            build_plan = ctx.file.plan,
            provider_metadata = metadata,
            package = "py-torch",
            version = "2.14.0",
            python_abi = _FIXTURE_PYTHON_ABI,
        ),
    ]


torch_extension_test_provider = rule(
    implementation = _torch_extension_test_provider_impl,
    attrs = {
        "plan": attr.label(allow_single_file = True, mandatory = True),
    },
    doc = "Lightweight PyTorch provider fixture for extension action tests.",
)


def _torch_extension_rootfs_prefix_fixture_impl(ctx):
    prefix = ctx.actions.declare_directory(ctx.label.name + "/prefix")
    manifest = ctx.actions.declare_file(ctx.label.name + "/prefix_manifest.json")
    prefix_path = ctx.actions.declare_file(ctx.label.name + "/prefix_path.txt")
    ctx.actions.run_shell(
        outputs = [prefix, manifest, prefix_path],
        command = """\
set -euo pipefail
prefix="$1"
manifest="$2"
prefix_path="$3"
component="$4"
required_headers="$5"
required_libs="$6"
rm -rf "$prefix"
mkdir -p "$prefix"
printf '%s\\n' "$prefix" > "$prefix_path"
rootfs_manifest="${VASO_ROOTFS_BUNDLE_MANIFEST:-}"
cuda_home="${VASO_CUDA_HOME:-/usr/local/cuda}"
rootfs_sha="host-stub"

if [[ "${VASO_IN_INSULA:-0}" == "1" && -f "$rootfs_manifest" && -d "$cuda_home" ]]; then
  include_root="$cuda_home/include"
  if [[ ! -d "$include_root" && -d "$cuda_home/targets/x86_64-linux/include" ]]; then
    include_root="$cuda_home/targets/x86_64-linux/include"
  fi
  lib_root="$cuda_home/lib64"
  if [[ ! -d "$lib_root" && -d "$cuda_home/lib" ]]; then
    lib_root="$cuda_home/lib"
  fi
  [[ -d "$include_root" ]] || { echo "$component rootfs prefix lacks include root" >&2; exit 2; }
  [[ -d "$lib_root" ]] || { echo "$component rootfs prefix lacks library root" >&2; exit 2; }
  IFS='|' read -r -a headers <<< "$required_headers"
  for header in "${headers[@]}"; do
    [[ -z "$header" || -f "$include_root/$header" ]] || { echo "$component rootfs prefix lacks include/$header" >&2; exit 2; }
  done
  IFS='|' read -r -a libs <<< "$required_libs"
  for lib in "${libs[@]}"; do
    [[ -z "$lib" || -e "$lib_root/$lib" ]] || { echo "$component rootfs prefix lacks $lib_root/$lib" >&2; exit 2; }
  done
  ln -s "$include_root" "$prefix/include"
  ln -s "$lib_root" "$prefix/lib"
  ln -s "$lib_root" "$prefix/lib64"
  if [[ -d "$cuda_home/bin" ]]; then
    ln -s "$cuda_home/bin" "$prefix/bin"
  fi
  if [[ -d "$cuda_home/targets" ]]; then
    ln -s "$cuda_home/targets" "$prefix/targets"
  fi
  rootfs_sha="$(sha256sum "$rootfs_manifest" | awk '{print $1}')"
else
  mkdir -p "$prefix/include" "$prefix/lib" "$prefix/lib64" "$prefix/bin"
  IFS='|' read -r -a headers <<< "$required_headers"
  for header in "${headers[@]}"; do
    [[ -n "$header" ]] || continue
    mkdir -p "$prefix/include/$(dirname "$header")"
    : > "$prefix/include/$header"
  done
  IFS='|' read -r -a libs <<< "$required_libs"
  for lib in "${libs[@]}"; do
    [[ -n "$lib" ]] || continue
    printf '%s\\n' 'INPUT(-lc)' > "$prefix/lib/$lib"
    printf '%s\\n' 'INPUT(-lc)' > "$prefix/lib64/$lib"
  done
fi

python3 - "$manifest" "$component" "$rootfs_sha" <<'PY'
import json
import sys

out, component, rootfs_sha = sys.argv[1:4]
doc = {
    "schema_version": 1,
    "name": component,
    "mechanism": "vaso-torch-extension-fixture-rootfs",
    "rootfs_manifest_sha256": rootfs_sha,
    "files": [],
    "tree_sha256": rootfs_sha,
}
with open(out, "w", encoding="utf-8") as fh:
    json.dump(doc, fh, indent=2, sort_keys=True)
    fh.write("\\n")
PY
""",
        arguments = [
            prefix.path,
            manifest.path,
            prefix_path.path,
            ctx.attr.component,
            "|".join(ctx.attr.required_headers),
            "|".join(ctx.attr.required_libs),
        ],
        env = {
            "LC_ALL": "C",
            "PATH": "/usr/bin:/bin",
            "PYTHONHASHSEED": "0",
            "VASO_CUDA_HOME": "/usr/local/cuda",
            "VASO_IN_INSULA": "1",
            "VASO_ROOTFS_BUNDLE_MANIFEST": "/run/vaso/rootfs-bundle.json",
        },
        mnemonic = "VasoTorchExtensionRootfsPrefix",
        progress_message = "Writing Vaso torch extension rootfs prefix %{label}",
        execution_requirements = {
            "no-cache": "1",
            "no-sandbox": "1",
        },
        use_default_shell_env = False,
    )
    return [DefaultInfo(files = depset([manifest]))]


torch_extension_rootfs_prefix_fixture = rule(
    implementation = _torch_extension_rootfs_prefix_fixture_impl,
    attrs = {
        "component": attr.string(mandatory = True),
        "required_headers": attr.string_list(default = []),
        "required_libs": attr.string_list(default = []),
    },
    doc = "Rootfs CUDA-family prefix manifest for vaso_torch_extension.",
)


def _extension_action_impl(ctx):
    output_basename = ctx.attr.output_basename or ctx.label.name
    torch_provider = ctx.attr.torch_prefix[PytorchNativePrefixInfo]
    torch_cxx_standard = ctx.var.get("VASO_TORCH_CXX_STANDARD", _CXX_STANDARD)
    python_prefix = ctx.var.get("VASO_PYTHON_PREFIX", "")
    torch_prefix_path = _torch_prefix_path(torch_provider)
    torch_include_roots = _torch_include_roots(torch_provider, torch_prefix_path)
    torch_lib_roots = _torch_lib_roots(torch_provider, torch_prefix_path)
    raw_wheel = ctx.actions.declare_file(output_basename + "_raw.whl")
    build_plan = ctx.actions.declare_file(output_basename + "_build_plan.json")
    provider_metadata = ctx.actions.declare_file(output_basename + "_provider_metadata.json")
    result_marker = ctx.actions.declare_file(output_basename + "_result.txt")
    torch_prefix_manifest = ctx.actions.declare_file(output_basename + "_torch_prefix_manifest.json")

    cuda_manifest = _single_file(ctx.attr.cuda_prefix_manifest, attr_name = "cuda_prefix_manifest")
    nccl_manifest = _single_file(ctx.attr.nccl_prefix_manifest, attr_name = "nccl_prefix_manifest")
    nvshmem_manifest = None
    cuda_prefix = _prefix_path_from_manifest(cuda_manifest)
    nccl_prefix = _prefix_path_from_manifest(nccl_manifest)
    nvshmem_prefix = ""
    if ctx.attr.uses_nvshmem:
        if not ctx.attr.rdc:
            fail("uses_nvshmem requires rdc = True so CUDA sources are device-linked")
        nvshmem_manifest = _single_file(ctx.attr.nvshmem_prefix_manifest, attr_name = "nvshmem_prefix_manifest")
        nvshmem_prefix = _prefix_path_from_manifest(nvshmem_manifest)
    rootfs_identity = native_action_rootfs_identity_file(ctx, profile = _PROFILE, consumer = "vaso_torch_extension")
    toolchain = _resolve_extension_toolchain(
        ctx,
        output_basename = output_basename,
    )

    ctx.actions.run(
        inputs = depset(
            [ctx.file._prefix_manifest_writer],
            transitive = [ctx.attr.torch_prefix[DefaultInfo].files],
        ),
        outputs = [torch_prefix_manifest],
        executable = "python3",
        arguments = [
            ctx.file._prefix_manifest_writer.path,
            "--prefix",
            torch_prefix_path,
            "--out",
            torch_prefix_manifest.path,
            "--name",
            "torch",
        ],
        env = {
            "LC_ALL": "C",
            "PATH": "/usr/bin:/bin",
            "PYTHONHASHSEED": "0",
        },
        mnemonic = "VasoTorchPrefixManifest",
        progress_message = "Writing PyTorch prefix content manifest %{label}",
        use_default_shell_env = False,
    )

    args = [
        "--wheel-out",
        raw_wheel.path,
        "--build-plan-out",
        build_plan.path,
        "--provider-metadata-out",
        provider_metadata.path,
        "--result-marker-out",
        result_marker.path,
        "--package",
        ctx.attr.package,
        "--version",
        ctx.attr.version,
        "--python-abi",
        torch_provider.python_abi,
        "--torch-prefix",
        torch_prefix_path,
        "--torch-prefix-manifest",
        torch_prefix_manifest.path,
        "--torch-build-plan",
        torch_provider.build_plan.path,
        "--torch-provider-metadata",
        torch_provider.provider_metadata.path,
        "--cuda-prefix",
        cuda_prefix,
        "--cuda-prefix-manifest",
        cuda_manifest.path,
        "--nccl-prefix",
        nccl_prefix,
        "--nccl-prefix-manifest",
        nccl_manifest.path,
        "--rootfs-identity",
        rootfs_identity.path,
        "--toolchain",
        toolchain.toolchain_json.path,
        "--cxx",
        toolchain.cxx,
        "--nvcc",
        toolchain.nvcc,
        "--cxx11-abi",
        _CXX11_ABI,
        "--cxx-standard",
        torch_cxx_standard,
        "--cuda-arch-list",
        "10.0+PTX",
        "--extra-cxx-flag=-D_GLIBCXX_USE_CXX11_ABI=1",
        "--link-flag=-L" + cuda_prefix + "/lib64",
        "--link-flag=-L" + cuda_prefix + "/lib",
        "--link-flag=-L" + nccl_prefix + "/lib64",
        "--link-flag=-L" + nccl_prefix + "/lib",
        "--link-flag=-ltorch",
        "--link-flag=-ltorch_cpu",
        "--link-flag=-ltorch_cuda",
        "--link-flag=-ltorch_python",
        "--link-flag=-lc10",
        "--link-flag=-lc10_cuda",
        "--link-flag=-lcudart",
        "--link-flag=-lnccl",
    ]
    if ctx.attr.rdc:
        args.extend([
            "--rdc",
            "--extra-cuda-flag=-rdc=true",
            "--link-flag=-lcudadevrt",
        ])
    if ctx.attr.uses_nvshmem:
        args.extend([
            "--nvshmem-prefix",
            nvshmem_prefix,
            "--nvshmem-prefix-manifest",
            nvshmem_manifest.path,
            "--device-link-file",
            _static_library_path(nvshmem_prefix, "libnvshmem_device.a"),
            "--link-flag=-L" + nvshmem_prefix + "/lib64",
            "--link-flag=-L" + nvshmem_prefix + "/lib",
            "--link-flag=-lnvshmem_host",
        ])
    if python_prefix:
        args.extend([
            "--python-prefix",
            python_prefix,
        ])
    for flag in ctx.attr.extra_cxx_flags:
        args.append("--extra-cxx-flag=" + flag)
    for flag in ctx.attr.extra_cuda_flags:
        args.append("--extra-cuda-flag=" + flag)
    for torch_include_root in torch_include_roots:
        args.append("--extra-cxx-flag=-I" + torch_include_root)
        args.append("--extra-cxx-flag=-I" + torch_include_root + "/torch/csrc/api/include")
    for torch_lib_root in torch_lib_roots:
        args.append("--link-flag=-L" + torch_lib_root)
    for src in ctx.files.srcs:
        args.extend(["--src", src.path])
    for src in ctx.files.cuda_srcs:
        args.extend(["--cuda-src", src.path])
    for src in ctx.files.py_srcs:
        args.extend(["--py-src", src.path])
    args.extend(_deps_args(ctx.attr.deps))

    action_inputs = [
        ctx.file._builder,
        torch_prefix_manifest,
        torch_provider.build_plan,
        torch_provider.provider_metadata,
        cuda_manifest,
        nccl_manifest,
        rootfs_identity,
        toolchain.toolchain_json,
    ]
    if nvshmem_manifest != None:
        action_inputs.append(nvshmem_manifest)

    ctx.actions.run(
        inputs = depset(
            action_inputs +
            ctx.files.srcs +
            ctx.files.cuda_srcs +
            ctx.files.py_srcs +
            _deps_files(ctx.attr.deps),
            transitive = [
                toolchain.all_files,
                ctx.attr.torch_prefix[DefaultInfo].files,
            ],
        ),
        tools = [ctx.file._builder],
        outputs = [raw_wheel, build_plan, provider_metadata, result_marker],
        executable = "python3",
        arguments = [ctx.file._builder.path] + args,
        env = _EXTENSION_ENV,
        mnemonic = "VasoTorchExtensionBuild",
        progress_message = "Building Vaso PyTorch extension %{label}",
        execution_requirements = {"no-sandbox": "1"},
        use_default_shell_env = False,
    )

    return [
        DefaultInfo(files = depset([raw_wheel, build_plan, provider_metadata, result_marker])),
        VasoTorchExtensionInfo(
            wheel = raw_wheel,
            build_plan = build_plan,
            provider_metadata = provider_metadata,
            package = ctx.attr.package,
            version = ctx.attr.version,
            python_abi = torch_provider.python_abi,
        ),
    ]


_vaso_torch_extension_raw = rule(
    implementation = _extension_action_impl,
    attrs = {
        "srcs": attr.label_list(allow_files = [".cc", ".cpp", ".cxx", ".c"]),
        "cuda_srcs": attr.label_list(allow_files = [".cu"]),
        "py_srcs": attr.label_list(allow_files = [".py"]),
        "deps": attr.label_list(allow_files = True),
        "rdc": attr.bool(
            default = False,
            doc = "Compile CUDA sources with relocatable device code and run a device-link step.",
        ),
        "uses_nvshmem": attr.bool(
            default = False,
            doc = "Link against the rootfs NVSHMEM host library and device archive.",
        ),
        "torch_prefix": attr.label(
            providers = [PytorchNativePrefixInfo],
            mandatory = True,
            doc = "Native PyTorch provider for the selected torch profile.",
        ),
        "cuda_prefix_manifest": attr.label(
            allow_files = True,
            mandatory = True,
            doc = "CUDA rootfs prefix content manifest.",
        ),
        "nccl_prefix_manifest": attr.label(
            allow_files = True,
            mandatory = True,
            doc = "NCCL rootfs prefix content manifest.",
        ),
        "nvshmem_prefix_manifest": attr.label(
            allow_files = True,
            doc = "NVSHMEM rootfs prefix content manifest, required when uses_nvshmem is true.",
        ),
        "package": attr.string(default = ""),
        "version": attr.string(default = "0.0.0"),
        "output_basename": attr.string(),
        "extra_cxx_flags": attr.string_list(
            default = [],
            doc = "Additional C++ compiler flags for extension sources.",
        ),
        "extra_cuda_flags": attr.string_list(
            default = [],
            doc = "Additional nvcc flags for extension CUDA sources.",
        ),
        "_builder": attr.label(
            default = Label("//native/common:extension_builder.py"),
            allow_single_file = True,
        ),
        "_prefix_manifest_writer": attr.label(
            default = Label("//native/common:prefix_manifest.py"),
            allow_single_file = True,
        ),
    } | CC_TOOLCHAIN_ATTRS | native_action_rootfs_identity_attrs(),
    toolchains = _extension_toolchains(),
    doc = "Build a raw PyTorch CUDA extension wheel against the selected Vaso torch profile.",
)


def _extension_forward_impl(ctx):
    actual = ctx.attr.actual
    if type(actual) == "list":
        if len(actual) != 1:
            fail("expected one transitioned Vaso torch extension target, got {}".format(len(actual)))
        actual = actual[0]
    return [
        actual[DefaultInfo],
        actual[VasoTorchExtensionInfo],
    ]


_extension_forward = rule(
    implementation = _extension_forward_impl,
    attrs = _extension_rootfs_platform_forward_attrs(),
)


def _raw_extension_target(name, srcs, cuda_srcs, py_srcs, deps, profile, visibility = None, **kwargs):
    _validate_profile(profile)
    package = kwargs.pop("package", name.replace("_", "-"))
    version = kwargs.pop("version", "0.0.0")
    raw_name = name + "_raw"
    impl_name = name + "_rootfs_impl"
    if "torch_prefix" not in kwargs:
        kwargs["torch_prefix"] = Label("//native/pytorch:pytorch_action")
    if "cuda_prefix_manifest" not in kwargs:
        kwargs["cuda_prefix_manifest"] = _EXTENSION_CUDA_ROOTFS_MANIFEST
    if "nccl_prefix_manifest" not in kwargs:
        kwargs["nccl_prefix_manifest"] = _EXTENSION_NCCL_ROOTFS_MANIFEST
    if kwargs.get("uses_nvshmem", False) and "nvshmem_prefix_manifest" not in kwargs:
        kwargs["nvshmem_prefix_manifest"] = _EXTENSION_NVSHMEM_ROOTFS_MANIFEST
    if "rootfs_identity" not in kwargs:
        kwargs["rootfs_identity"] = _default_extension_rootfs_identity(_PROFILE)
    _vaso_torch_extension_raw(
        name = impl_name,
        srcs = srcs,
        cuda_srcs = cuda_srcs,
        py_srcs = py_srcs,
        deps = deps,
        output_basename = name,
        package = package,
        version = version,
        visibility = ["//visibility:private"],
        **kwargs
    )
    _extension_forward(
        name = raw_name,
        actual = ":" + impl_name,
        visibility = ["//visibility:private"],
    )
    return raw_name


def _vaso_torch_extension(name, srcs, cuda_srcs = [], py_srcs = [], deps = [], profile = _PROFILE, visibility = None, tags = None, **kwargs):
    """Build a PyTorch CUDA extension and expose a repaired release wheel.

    The raw native build is available as ``:<name>_raw`` for low-level tests.
    The public ``:<name>`` target runs the common release repair action.
    """
    raw_name = _raw_extension_target(
        name,
        srcs = srcs,
        cuda_srcs = cuda_srcs,
        py_srcs = py_srcs,
        deps = deps,
        profile = profile,
        **kwargs
    )
    release_wheel_action(
        name = name,
        input = ":" + raw_name,
        profile = profile,
        tags = tags,
        visibility = visibility,
    )


def vaso_torch_extension(
        name,
        srcs,
        cuda_srcs = [],
        py_srcs = [],
        deps = [],
        profile = _PROFILE,
        rdc = False,
        uses_nvshmem = False,
        torch_prefix = None,
        cuda_prefix_manifest = None,
        nccl_prefix_manifest = None,
        nvshmem_prefix_manifest = None,
        rootfs_identity = None,
        **kwargs):
    """Build a PyTorch CUDA extension and expose a repaired release wheel."""
    if torch_prefix == None:
        kwargs["torch_prefix"] = _public_torch_artifact_prefix()
    else:
        kwargs["torch_prefix"] = torch_prefix
    if cuda_prefix_manifest != None:
        kwargs["cuda_prefix_manifest"] = cuda_prefix_manifest
    if nccl_prefix_manifest != None:
        kwargs["nccl_prefix_manifest"] = nccl_prefix_manifest
    if nvshmem_prefix_manifest != None:
        kwargs["nvshmem_prefix_manifest"] = nvshmem_prefix_manifest
    kwargs["rdc"] = rdc
    kwargs["uses_nvshmem"] = uses_nvshmem
    if rootfs_identity != None:
        kwargs["rootfs_identity"] = rootfs_identity
    _vaso_torch_extension(
        name,
        srcs = srcs,
        cuda_srcs = cuda_srcs,
        py_srcs = py_srcs,
        deps = deps,
        profile = profile,
        **kwargs
    )


def vaso_torch_extension_for_test(name, srcs, cuda_srcs = [], py_srcs = [], deps = [], profile = _PROFILE, visibility = None, tags = None, **kwargs):
    """Test-only extension macro with provider injection hooks."""
    _vaso_torch_extension(
        name,
        srcs = srcs,
        cuda_srcs = cuda_srcs,
        py_srcs = py_srcs,
        deps = deps,
        profile = profile,
        visibility = visibility,
        tags = tags,
        **kwargs
    )


def vaso_torch_extension_raw_for_test(name, srcs, cuda_srcs = [], py_srcs = [], deps = [], profile = _PROFILE, visibility = None, **kwargs):
    """Test-only escape hatch exposing the raw provider target directly."""
    raw_name = _raw_extension_target(
        name,
        srcs = srcs,
        cuda_srcs = cuda_srcs,
        py_srcs = py_srcs,
        deps = deps,
        profile = profile,
        visibility = visibility,
        **kwargs
    )
    native.alias(
        name = name,
        actual = ":" + raw_name,
        visibility = visibility,
    )


def vaso_torch_wheel(name, profile = _PROFILE, visibility = None, tags = None):
    """Re-export the repaired PyTorch wheel for a Vaso torch profile."""
    _validate_profile(profile)
    release_wheel_action(
        name = name,
        input = "//native/pytorch:pytorch_action",
        profile = profile,
        tags = tags,
        visibility = visibility,
    )
