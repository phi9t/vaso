#!/usr/bin/env python3
"""Verifier for the shipped gsplat rasterizer wheel."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import torch

import vaso_gsplat_rasterizer as rasterizer

DEFAULT_REFERENCE_IMAGE = Path(__file__).with_name("reference_image.pt")


def _load_reference(path: Path, device: torch.device) -> torch.Tensor:
    doc = json.loads(path.read_text(encoding="utf-8"))
    return torch.tensor(doc["image"], device=device, dtype=torch.float32)


def compare_reference_image(device: torch.device, reference_path: Path) -> None:
    actual = rasterizer.render_reference_scene(device=device)
    expected = _load_reference(reference_path, device=device)
    torch.testing.assert_close(actual, expected, rtol=2e-4, atol=2e-5)


def finite_difference_gradcheck(device: torch.device) -> None:
    base_colors = torch.tensor([[[[0.25, 0.5, 0.9]]]], device=device, dtype=torch.float32)
    colors = base_colors.detach().clone().requires_grad_(True)
    loss = rasterizer.render_reference_scene(device=device, colors=colors)[..., 0].sum()
    loss.backward()
    analytic = colors.grad[0, 0, 0, 0].detach().item()

    eps = 2e-3

    def red_loss(candidate: torch.Tensor) -> float:
        return rasterizer.render_reference_scene(device=device, colors=candidate)[..., 0].sum().detach().item()

    plus = base_colors.clone()
    minus = base_colors.clone()
    plus[0, 0, 0, 0] += eps
    minus[0, 0, 0, 0] -= eps
    finite_difference = (red_loss(plus) - red_loss(minus)) / (2.0 * eps)
    if abs(analytic - finite_difference) > 5e-2:
        raise AssertionError(
            f"color gradient mismatch: analytic={analytic:.6f} finite_difference={finite_difference:.6f}"
        )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--device", default="cuda", choices=("cuda",))
    parser.add_argument("--reference", type=Path, default=DEFAULT_REFERENCE_IMAGE)
    args = parser.parse_args()

    if not torch.cuda.is_available():
        raise SystemExit("CUDA is required for the gsplat rasterizer verifier")
    device = torch.device(args.device)
    compare_reference_image(device, args.reference)
    finite_difference_gradcheck(device)


if __name__ == "__main__":
    main()
