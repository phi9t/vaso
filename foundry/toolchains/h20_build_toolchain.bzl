"""Explicit opt-in H20 build tools; does not change the canonical rootfs line."""

H20BuildToolchainInfo = provider(fields = [
    "contract", "files", "python", "cuda_prefix_file", "python_prefix_file",
    "cuda_version", "compiler_identity", "python_version", "nccl_prefix_file", "cudnn_prefix_file",
])

def _h20_build_toolchain_impl(ctx):
    if ctx.attr.python_version not in ["3.13.13", "3.13.18"]:
        fail("declare the actual supported cp313 interpreter version, never relabel it")
    if ctx.attr.cuda_version != "13.3":
        fail("H20 Cuspy toolchain requires CUDA 13.3")
    codegen_names = ["cupti_header", "cupti_version_header", "libclang", "clang_cindex", "clang_init"]
    codegen_files = [getattr(ctx.file, name) for name in codegen_names if getattr(ctx.file, name)]
    if ctx.attr.enable_cuspy_codegen:
        if not ctx.attr.runtime_files:
            fail("Cuspy codegen requires declared runtime_files for the full clang bindings and libclang runtime closure")
        if len(codegen_files) != len(codegen_names) or not ctx.attr.clang_source_identity:
            fail("Cuspy codegen requires all declared headers, matching clang bindings/library and source identity")
    elif codegen_files or ctx.attr.clang_source_identity:
        fail("Cuspy codegen inputs require enable_cuspy_codegen=True")
    codegen = None
    if ctx.attr.enable_cuspy_codegen:
        codegen = {name: getattr(ctx.file, name).path for name in codegen_names}
        codegen["clang_source_identity"] = ctx.attr.clang_source_identity
    contract = ctx.actions.declare_file(ctx.label.name + ".toolchain.json")
    tools = {
        "cc": ctx.file.cc.path,
        "cxx": ctx.file.cxx.path,
        "nvcc": ctx.file.nvcc.path,
        "python": ctx.file.python.path,
    }
    ctx.actions.write(contract, json.encode({
        "schema": 1,
        "cuspy_codegen": codegen,
        "host_os": "linux",
        "host_arch": "x86_64",
        "cuda_version": ctx.attr.cuda_version,
        "cuda_arch_list": "9.0",
        "gpu_archs": ["sm_90"],
        "python_abi": "cp313",
        "python_version": ctx.attr.python_version,
        "compiler_identity": ctx.attr.compiler_identity,
        "tools": tools,
        "cuda_prefix_file": ctx.file.cuda_prefix_file.path,
        "python_prefix_file": ctx.file.python_prefix_file.path,
        "python_requirements": ctx.file.python_requirements.path,
        "python_site_packages_file": ctx.file.python_site_packages_file.path,
        "library_prefix_files": {"nccl": ctx.file.nccl_prefix_file.path, "cudnn": ctx.file.cudnn_prefix_file.path},
    }) + "\n")
    files = depset(
        [contract, ctx.file.cc, ctx.file.cxx, ctx.file.nvcc, ctx.file.python,
         ctx.file.cuda_prefix_file, ctx.file.python_prefix_file, ctx.file.python_requirements, ctx.file.python_site_packages_file, ctx.file.nccl_prefix_file, ctx.file.cudnn_prefix_file] + codegen_files,
        transitive = [target[DefaultInfo].files for target in ctx.attr.runtime_files],
    )
    info = H20BuildToolchainInfo(
        contract = contract, files = files, python = ctx.file.python,
        cuda_prefix_file = ctx.file.cuda_prefix_file,
        python_prefix_file = ctx.file.python_prefix_file,
        nccl_prefix_file = ctx.file.nccl_prefix_file,
        cudnn_prefix_file = ctx.file.cudnn_prefix_file,
        cuda_version = ctx.attr.cuda_version,
        compiler_identity = ctx.attr.compiler_identity,
        python_version = ctx.attr.python_version,
    )
    return [DefaultInfo(files = files), info, platform_common.ToolchainInfo(h20 = info)]

h20_build_toolchain = rule(
    implementation = _h20_build_toolchain_impl,
    attrs = {
        "enable_cuspy_codegen": attr.bool(default = False),
        "cupti_header": attr.label(allow_single_file = True),
        "cupti_version_header": attr.label(allow_single_file = True),
        "libclang": attr.label(allow_single_file = True),
        "clang_cindex": attr.label(allow_single_file = True),
        "clang_init": attr.label(allow_single_file = True),
        "clang_source_identity": attr.string(doc = "Producer-declared common source identity for bindings and libclang; not verified by hashing."),
        "cc": attr.label(allow_single_file = True, mandatory = True, cfg = "exec"),
        "cxx": attr.label(allow_single_file = True, mandatory = True, cfg = "exec"),
        "nvcc": attr.label(allow_single_file = True, mandatory = True, cfg = "exec"),
        "python": attr.label(allow_single_file = True, mandatory = True, cfg = "exec"),
        "cuda_prefix_file": attr.label(allow_single_file = True, mandatory = True),
        "python_prefix_file": attr.label(allow_single_file = True, mandatory = True),
        "runtime_files": attr.label_list(mandatory = True, doc = "Complete declared package/runtime closures, including clang.enumerations and libclang shared-library dependencies when codegen is enabled; selected anchor files alone are insufficient."),
        "nccl_prefix_file": attr.label(allow_single_file = True, mandatory = True),
        "cudnn_prefix_file": attr.label(allow_single_file = True, mandatory = True),
        "python_site_packages_file": attr.label(allow_single_file = True, mandatory = True),
        "python_requirements": attr.label(allow_single_file = True, mandatory = True),
        "compiler_identity": attr.string(mandatory = True),
        "python_version": attr.string(mandatory = True),
        "cuda_version": attr.string(default = "13.3"),
    },
)
