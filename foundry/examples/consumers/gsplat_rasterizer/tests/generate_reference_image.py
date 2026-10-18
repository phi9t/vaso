#!/usr/bin/env python3
"""Generate the independent CPU reference image for the gsplat verifier."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np


def reference_image() -> np.ndarray:
    image_height = 8
    image_width = 8
    center_x = 4.0
    center_y = 4.0
    opacity = 0.8
    alpha_threshold = 1.0 / 255.0
    color = np.array([0.25, 0.5, 0.9], dtype=np.float64)

    y, x = np.mgrid[0:image_height, 0:image_width]
    dx = (x.astype(np.float64) + 0.5) - center_x
    dy = (y.astype(np.float64) + 0.5) - center_y
    alpha = opacity * np.exp(-0.5 * (dx * dx + dy * dy))
    alpha = np.where(alpha >= alpha_threshold, alpha, 0.0)
    return alpha[..., None] * color


def reference_doc() -> dict[str, object]:
    image = reference_image()
    return {
        "shape": list(image.shape),
        "image": np.round(image, 8).tolist(),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", type=Path)
    args = parser.parse_args()

    doc = reference_doc()
    if args.check is None:
        print(json.dumps(doc, indent=2))
        return

    actual_doc = json.loads(args.check.read_text(encoding="utf-8"))
    actual = np.array(actual_doc["image"], dtype=np.float64)
    expected = np.array(doc["image"], dtype=np.float64)
    if list(actual.shape) != doc["shape"]:
        raise SystemExit(f"reference image shape {list(actual.shape)}, expected {doc['shape']}")
    max_abs = float(np.max(np.abs(actual - expected)))
    if max_abs > 5e-9:
        raise SystemExit(f"reference image differs from CPU NumPy render: max_abs={max_abs:.3e}")
    print(f"reference image matches CPU NumPy render: shape={list(actual.shape)} max_abs={max_abs:.3e}")


if __name__ == "__main__":
    main()
