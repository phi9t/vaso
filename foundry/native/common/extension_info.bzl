"""Providers shared by torch extension build and release repair rules."""

VasoTorchExtensionInfo = provider(
    doc = "Raw wheel produced by a Vaso PyTorch CUDA extension build.",
    fields = {
        "build_plan": "Committed PyTorch plan used to derive ABI and CUDA flags.",
        "package": "Python distribution name for the extension wheel.",
        "provider_metadata": "Native provider metadata JSON consumed by release repair.",
        "python_abi": "Python ABI tag matched to the selected torch profile.",
        "version": "Upstream extension package version before release repair.",
        "wheel": "Raw extension wheel artifact before release repair.",
    },
)
