"""Torch SE3 projection reference and compiled consumer."""

from __future__ import annotations

from .reference import example_inputs_python


def fixture_torch(device: str = "cuda"):
    import torch

    points, poses, intrinsics = example_inputs_python()
    return (
        torch.tensor(points, dtype=torch.float32, device=device),
        torch.tensor(poses, dtype=torch.float32, device=device),
        torch.tensor(intrinsics, dtype=torch.float32, device=device),
    )


def project_se3_pinhole_torch_reference(points, poses, intrinsics):
    import torch

    camera_xyz = torch.matmul(points, poses[:, :3, :3].transpose(1, 2)) + poses[:, None, :3, 3]
    depth = camera_xyz[..., 2]
    valid = depth > 0.0
    homogeneous = torch.matmul(camera_xyz, intrinsics.transpose(1, 2))
    denom = torch.where(valid, homogeneous[..., 2], torch.ones_like(homogeneous[..., 2]))
    pixels = torch.stack((homogeneous[..., 0] / denom, homogeneous[..., 1] / denom), dim=-1)
    pixels = torch.where(valid[..., None], pixels, torch.zeros_like(pixels))
    return pixels, depth, valid


def _project_se3_pinhole_torch(points, poses, intrinsics):
    return project_se3_pinhole_torch_reference(points, poses, intrinsics)


project_se3_pinhole_torch_compile = None


def compiled_projector():
    import torch

    global project_se3_pinhole_torch_compile
    if project_se3_pinhole_torch_compile is None:
        project_se3_pinhole_torch_compile = torch.compile(
            _project_se3_pinhole_torch,
            fullgraph=True,
        )
    return project_se3_pinhole_torch_compile
