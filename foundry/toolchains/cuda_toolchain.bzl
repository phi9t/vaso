CudaRootfsToolchainInfo = provider(
    doc = "Metadata for the CUDA SDK supplied by the selected insula rootfs.",
    fields = {
        "cuda_arch_list": "Torch-style CUDA architecture list for the default profile.",
        "cuda_home": "Absolute CUDA SDK path inside the selected insula rootfs.",
        "cuda_prefix_file": "Label string for the existing rootfs CUDA prefix marker.",
        "nvcc": "Absolute nvcc path inside the insula rootfs.",
    },
)

def _cuda_toolchain_info_impl(ctx):
    info = CudaRootfsToolchainInfo(
        cuda_arch_list = ctx.attr.cuda_arch_list,
        cuda_home = ctx.attr.cuda_home,
        cuda_prefix_file = ctx.attr.cuda_prefix_file,
        nvcc = ctx.attr.nvcc,
    )
    return [
        info,
        platform_common.ToolchainInfo(cuda = info),
    ]

cuda_toolchain_info = rule(
    implementation = _cuda_toolchain_info_impl,
    attrs = {
        "cuda_arch_list": attr.string(mandatory = True),
        "cuda_home": attr.string(mandatory = True),
        "cuda_prefix_file": attr.string(mandatory = True),
        "nvcc": attr.string(mandatory = True),
    },
)
