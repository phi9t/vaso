"""Verify torch.compile and Triton consumers against the pure Torch reference."""

from __future__ import annotations

import torch
import triton

from .release_checks import assert_shipped_wheel
from .torch_se3 import compiled_projector, fixture_torch, project_se3_pinhole_torch_reference
from .triton_se3 import project_se3_pinhole_triton


def _assert_close(name: str, actual, expected) -> None:
    if actual.dtype == torch.bool:
        if not torch.equal(actual, expected):
            raise AssertionError(f"{name} bool outputs differ: actual={actual.cpu()} expected={expected.cpu()}")
    else:
        torch.testing.assert_close(actual, expected, rtol=1e-4, atol=1e-4, msg=name)


def verify_torch_compile_matches_reference() -> None:
    points, poses, intrinsics = fixture_torch("cuda")
    expected = project_se3_pinhole_torch_reference(points, poses, intrinsics)
    actual = compiled_projector()(points, poses, intrinsics)
    for name, actual_item, expected_item in zip(("pixels", "depth", "valid"), actual, expected, strict=True):
        _assert_close(f"torch.compile {name}", actual_item, expected_item)


def verify_triton_matches_reference() -> None:
    points, poses, intrinsics = fixture_torch("cuda")
    expected = project_se3_pinhole_torch_reference(points, poses, intrinsics)
    actual = project_se3_pinhole_triton(points, poses, intrinsics)
    for name, actual_item, expected_item in zip(("pixels", "depth", "valid"), actual, expected, strict=True):
        _assert_close(f"triton {name}", actual_item, expected_item)
    invalid_pixels = actual[0][~actual[2]]
    torch.testing.assert_close(invalid_pixels, torch.zeros_like(invalid_pixels), rtol=0.0, atol=0.0)


def main() -> None:
    assert_shipped_wheel(torch, "torch")
    assert_shipped_wheel(triton, "triton")
    if not torch.cuda.is_available():
        raise SystemExit("torch CUDA is not available in the release verifier")
    verify_triton_matches_reference()
    verify_torch_compile_matches_reference()
    print("python_consumers torch verifier passed")


if __name__ == "__main__":
    main()
