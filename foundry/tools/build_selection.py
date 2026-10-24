"""Supported unattended build selection; native recipes retain their own policy.

Package membership drives input discovery, release targets and explicit native
build tokens. JAX keeps its existing plural wheelhouse and LLVM token spelling.
This is build orchestration policy, not a source/version or toolchain pin.
"""

CUDA_LINES = ("cu129", "cu130")
PACKAGES = {
    "torch": ("pytorch", "torchvision", "torchaudio", "triton"),
    "jax": ("jaxlib",),
}
TARGETS = {
    profile: [
        f"//native/{package}:{package}_release_wheel" + ("s" if package == "jaxlib" else "")
        for package in packages
    ]
    for profile, packages in PACKAGES.items()
}
TOKENS = {
    profile: [
        f"--//native/{package}:token=build-native-" + ("llvm" if package == "jaxlib" else package)
        for package in packages
    ]
    for profile, packages in PACKAGES.items()
}
