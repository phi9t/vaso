from __future__ import annotations

from pathlib import Path

import torch

torch.ops.load_library(str(Path(__file__).with_name("_C.so")))


def cutlass_gemm(a: torch.Tensor, b: torch.Tensor) -> torch.Tensor:
    return torch.ops.vaso_graph.cutlass_gemm(a, b)


def _register_fake(op_name: str):
    def decorate(fn):
        try:
            return torch.library.register_fake(op_name)(fn)
        except RuntimeError as exc:
            if "already has" in str(exc):
                return fn
            raise

    return decorate


@_register_fake("vaso_graph::cutlass_gemm")
def _fake_cutlass_gemm(a: torch.Tensor, b: torch.Tensor) -> torch.Tensor:
    if a.dim() != 2 or b.dim() != 2:
        raise ValueError("cutlass_gemm inputs must be two-dimensional")
    if a.shape[1] != b.shape[0]:
        raise ValueError("cutlass_gemm inputs must have compatible K dimensions")
    return a.new_empty((a.shape[0], b.shape[1]))


__all__ = ["cutlass_gemm"]
