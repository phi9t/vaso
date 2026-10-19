#!/usr/bin/env python3
from __future__ import annotations

import argparse
import unittest

import torch
import vaso_cutlass_gemm


def reference_cutlass_gemm(a: torch.Tensor, b: torch.Tensor) -> torch.Tensor:
    return torch.matmul(a, b)


def sample_inputs(device: torch.device) -> tuple[torch.Tensor, torch.Tensor]:
    a = torch.tensor(
        [
            [1.0, -2.0, 0.5, 3.0, 0.25, -1.5, 2.0],
            [0.0, 4.0, -3.0, 1.5, 2.25, 0.5, -0.75],
            [2.5, 0.5, 1.0, -1.0, 3.5, -2.0, 0.125],
            [-4.0, 1.0, 2.0, 0.25, -0.5, 1.75, 3.0],
            [0.75, -0.25, 1.5, 2.5, -3.0, 4.0, -1.0],
        ],
        dtype=torch.float32,
        device=device,
    )
    b = torch.tensor(
        [
            [0.5, -1.0, 2.0],
            [1.5, 0.25, -0.5],
            [-2.0, 3.0, 1.0],
            [0.125, -0.75, 2.5],
            [4.0, 1.0, -1.5],
            [-0.25, 2.25, 0.75],
            [3.0, -2.0, 0.5],
        ],
        dtype=torch.float32,
        device=device,
    )
    return a, b


class CutlassGemmTest(unittest.TestCase):
    device: torch.device

    def test_forward_matches_torch_matmul_for_ragged_tile(self) -> None:
        a, b = sample_inputs(self.device)

        actual = vaso_cutlass_gemm.cutlass_gemm(a, b)
        expected = reference_cutlass_gemm(a, b)

        torch.testing.assert_close(actual, expected, rtol=1e-5, atol=1e-5)

    def test_forward_accepts_non_contiguous_inputs(self) -> None:
        a, b = sample_inputs(self.device)
        a = a.t().contiguous().t()
        b = b.t().contiguous().t()

        actual = vaso_cutlass_gemm.cutlass_gemm(a, b)
        expected = reference_cutlass_gemm(a, b)

        self.assertFalse(a.is_contiguous())
        self.assertFalse(b.is_contiguous())
        torch.testing.assert_close(actual, expected, rtol=1e-5, atol=1e-5)

    def test_empty_output_matches_torch_matmul(self) -> None:
        a = torch.empty((0, 7), dtype=torch.float32, device=self.device)
        b = torch.empty((7, 3), dtype=torch.float32, device=self.device)

        actual = vaso_cutlass_gemm.cutlass_gemm(a, b)
        expected = reference_cutlass_gemm(a, b)

        self.assertEqual(tuple(actual.shape), (0, 3))
        torch.testing.assert_close(actual, expected)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--device", choices=("cpu", "cuda"), default="cpu")
    args, remaining = parser.parse_known_args()
    if args.device == "cuda" and not torch.cuda.is_available():
        raise unittest.SkipTest("CUDA device is not available")
    CutlassGemmTest.device = torch.device(args.device)
    unittest.main(argv=["cutlass_gemm_test.py", *remaining])


if __name__ == "__main__":
    main()
