"""Triton SE3 projection kernel consumer."""

from __future__ import annotations

import torch
import triton
import triton.language as tl


@triton.jit
def _se3_pinhole_project_kernel(
    points,
    poses,
    intrinsics,
    pixels,
    depth,
    valid,
    total: tl.constexpr,
    n_points: tl.constexpr,
    block_size: tl.constexpr,
):
    offsets = tl.program_id(0) * block_size + tl.arange(0, block_size)
    mask = offsets < total
    batch = offsets // n_points
    point_index = offsets - batch * n_points

    point_base = (batch * n_points + point_index) * 3
    px = tl.load(points + point_base + 0, mask=mask, other=0.0)
    py = tl.load(points + point_base + 1, mask=mask, other=0.0)
    pz = tl.load(points + point_base + 2, mask=mask, other=0.0)

    pose_base = batch * 16
    cx = (
        tl.load(poses + pose_base + 0, mask=mask, other=0.0) * px
        + tl.load(poses + pose_base + 1, mask=mask, other=0.0) * py
        + tl.load(poses + pose_base + 2, mask=mask, other=0.0) * pz
        + tl.load(poses + pose_base + 3, mask=mask, other=0.0)
    )
    cy = (
        tl.load(poses + pose_base + 4, mask=mask, other=0.0) * px
        + tl.load(poses + pose_base + 5, mask=mask, other=0.0) * py
        + tl.load(poses + pose_base + 6, mask=mask, other=0.0) * pz
        + tl.load(poses + pose_base + 7, mask=mask, other=0.0)
    )
    cz = (
        tl.load(poses + pose_base + 8, mask=mask, other=0.0) * px
        + tl.load(poses + pose_base + 9, mask=mask, other=0.0) * py
        + tl.load(poses + pose_base + 10, mask=mask, other=0.0) * pz
        + tl.load(poses + pose_base + 11, mask=mask, other=0.0)
    )

    intrinsic_base = batch * 9
    hx = (
        tl.load(intrinsics + intrinsic_base + 0, mask=mask, other=0.0) * cx
        + tl.load(intrinsics + intrinsic_base + 1, mask=mask, other=0.0) * cy
        + tl.load(intrinsics + intrinsic_base + 2, mask=mask, other=0.0) * cz
    )
    hy = (
        tl.load(intrinsics + intrinsic_base + 3, mask=mask, other=0.0) * cx
        + tl.load(intrinsics + intrinsic_base + 4, mask=mask, other=0.0) * cy
        + tl.load(intrinsics + intrinsic_base + 5, mask=mask, other=0.0) * cz
    )
    hz = (
        tl.load(intrinsics + intrinsic_base + 6, mask=mask, other=0.0) * cx
        + tl.load(intrinsics + intrinsic_base + 7, mask=mask, other=0.0) * cy
        + tl.load(intrinsics + intrinsic_base + 8, mask=mask, other=0.0) * cz
    )

    # Validity is about camera-space depth: points behind the camera do not project.
    is_valid = cz > 0.0
    safe_hz = tl.where(is_valid, hz, 1.0)
    out_x = tl.where(is_valid, hx / safe_hz, 0.0)
    out_y = tl.where(is_valid, hy / safe_hz, 0.0)

    pixel_base = (batch * n_points + point_index) * 2
    tl.store(pixels + pixel_base + 0, out_x, mask=mask)
    tl.store(pixels + pixel_base + 1, out_y, mask=mask)
    tl.store(depth + offsets, cz, mask=mask)
    tl.store(valid + offsets, is_valid, mask=mask)


def project_se3_pinhole_triton(points, poses, intrinsics):
    if points.device.type != "cuda":
        raise ValueError("Triton verifier requires CUDA tensors")
    if points.dtype != torch.float32 or poses.dtype != torch.float32 or intrinsics.dtype != torch.float32:
        raise TypeError("Triton verifier expects float32 inputs")
    points = points.contiguous()
    poses = poses.contiguous()
    intrinsics = intrinsics.contiguous()
    batch, n_points, channels = points.shape
    if channels != 3 or poses.shape != (batch, 4, 4) or intrinsics.shape != (batch, 3, 3):
        raise ValueError("expected points=(B,N,3), poses=(B,4,4), intrinsics=(B,3,3)")

    pixels = torch.empty((batch, n_points, 2), device=points.device, dtype=torch.float32)
    depth = torch.empty((batch, n_points), device=points.device, dtype=torch.float32)
    valid = torch.empty((batch, n_points), device=points.device, dtype=torch.bool)
    total = batch * n_points
    block_size = 128
    grid = (triton.cdiv(total, block_size),)
    _se3_pinhole_project_kernel[grid](
        points,
        poses,
        intrinsics,
        pixels,
        depth,
        valid,
        total,
        n_points,
        block_size,
    )
    return pixels, depth, valid
