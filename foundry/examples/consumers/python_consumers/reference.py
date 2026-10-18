"""Pure-Python SE3 plus pinhole projection reference."""

from __future__ import annotations

from collections.abc import Sequence


PointBatch = Sequence[Sequence[Sequence[float]]]
PoseBatch = Sequence[Sequence[Sequence[float]]]
IntrinsicBatch = Sequence[Sequence[Sequence[float]]]


def example_inputs_python() -> tuple[list[list[list[float]]], list[list[list[float]]], list[list[list[float]]]]:
    return (
        [
            [
                [1.0, 2.0, 4.0],
                [-2.0, 1.0, 2.0],
                [5.0, 6.0, -2.0],
                [7.0, 8.0, 0.0],
                [0.5, -1.0, 8.0],
            ],
            [
                [2.0, 4.0, 1.0],
                [0.0, 0.0, -4.0],
                [-1.0, 3.0, 2.0],
                [4.0, -2.0, 5.0],
                [1.5, 2.5, -3.0],
            ],
        ],
        [
            [
                [1.0, 0.0, 0.0, 0.0],
                [0.0, 1.0, 0.0, 0.0],
                [0.0, 0.0, 1.0, 0.0],
                [0.0, 0.0, 0.0, 1.0],
            ],
            [
                [1.0, 0.0, 0.0, 1.0],
                [0.0, 1.0, 0.0, -2.0],
                [0.0, 0.0, 1.0, 3.0],
                [0.0, 0.0, 0.0, 1.0],
            ],
        ],
        [
            [
                [100.0, 0.0, 10.0],
                [0.0, 200.0, 20.0],
                [0.0, 0.0, 1.0],
            ],
            [
                [10.0, 0.0, 1.0],
                [0.0, 20.0, 2.0],
                [0.0, 0.0, 1.0],
            ],
        ],
    )


def project_se3_pinhole_python(
    points: PointBatch,
    poses: PoseBatch,
    intrinsics: IntrinsicBatch,
) -> tuple[list[list[list[float]]], list[list[float]], list[list[bool]]]:
    pixels: list[list[list[float]]] = []
    depths: list[list[float]] = []
    valid_mask: list[list[bool]] = []

    if len(points) != len(poses) or len(points) != len(intrinsics):
        raise ValueError("points, poses and intrinsics must have the same batch size")

    for batch_points, pose, intrinsic in zip(points, poses, intrinsics, strict=True):
        batch_pixels: list[list[float]] = []
        batch_depths: list[float] = []
        batch_valid: list[bool] = []
        for point in batch_points:
            x = (
                pose[0][0] * point[0]
                + pose[0][1] * point[1]
                + pose[0][2] * point[2]
                + pose[0][3]
            )
            y = (
                pose[1][0] * point[0]
                + pose[1][1] * point[1]
                + pose[1][2] * point[2]
                + pose[1][3]
            )
            z = (
                pose[2][0] * point[0]
                + pose[2][1] * point[1]
                + pose[2][2] * point[2]
                + pose[2][3]
            )
            is_valid = z > 0.0
            batch_depths.append(z)
            batch_valid.append(is_valid)
            if is_valid:
                h0 = intrinsic[0][0] * x + intrinsic[0][1] * y + intrinsic[0][2] * z
                h1 = intrinsic[1][0] * x + intrinsic[1][1] * y + intrinsic[1][2] * z
                h2 = intrinsic[2][0] * x + intrinsic[2][1] * y + intrinsic[2][2] * z
                batch_pixels.append([h0 / h2, h1 / h2])
            else:
                batch_pixels.append([0.0, 0.0])
        pixels.append(batch_pixels)
        depths.append(batch_depths)
        valid_mask.append(batch_valid)

    return pixels, depths, valid_mask
