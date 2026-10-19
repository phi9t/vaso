from __future__ import annotations

import math
from collections import namedtuple
from collections.abc import Sequence

import torch

from . import _C as _C  # noqa: F401


VoxelScatter = namedtuple("VoxelScatter", ("mean", "max", "point_to_voxel"))


def _triplet(name: str, value: Sequence[float]) -> list[float]:
    values = [float(item) for item in value]
    if len(values) != 3:
        raise ValueError(f"{name} must contain exactly three values")
    return values


def _num_voxels(voxel_size: Sequence[float], grid_min: Sequence[float], grid_max: Sequence[float]) -> int:
    dims = []
    for size, low, high in zip(voxel_size, grid_min, grid_max, strict=True):
        if size <= 0:
            raise ValueError("voxel_size values must be positive")
        dim = math.floor((high - low) / size)
        if dim <= 0:
            raise ValueError("grid bounds must contain at least one voxel on every axis")
        dims.append(dim)
    return int(dims[0] * dims[1] * dims[2])


class _VoxelScatterFunction(torch.autograd.Function):
    @staticmethod
    def forward(
        ctx: torch.autograd.function.FunctionCtx,
        points: torch.Tensor,
        features: torch.Tensor,
        voxel_size: Sequence[float],
        grid_min: Sequence[float],
        grid_max: Sequence[float],
    ) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
        voxel_size = _triplet("voxel_size", voxel_size)
        grid_min = _triplet("grid_min", grid_min)
        grid_max = _triplet("grid_max", grid_max)
        mean, max_features, point_to_voxel, counts, max_indices = torch.ops.vaso_graph.voxel_scatter_forward(
            points,
            features,
            voxel_size,
            grid_min,
            grid_max,
        )
        ctx.save_for_backward(point_to_voxel, counts, max_indices)
        ctx.mark_non_differentiable(point_to_voxel)
        return mean, max_features, point_to_voxel

    @staticmethod
    def backward(
        ctx: torch.autograd.function.FunctionCtx,
        grad_mean: torch.Tensor,
        grad_max: torch.Tensor,
        grad_point_to_voxel: torch.Tensor | None,
    ) -> tuple[None, torch.Tensor, None, None, None]:
        del grad_point_to_voxel
        point_to_voxel, counts, max_indices = ctx.saved_tensors
        grad_features = torch.ops.vaso_graph.voxel_scatter_backward(
            grad_mean.contiguous(),
            grad_max.contiguous(),
            point_to_voxel,
            counts,
            max_indices,
        )
        return None, grad_features, None, None, None


def voxel_scatter(
    points: torch.Tensor,
    features: torch.Tensor,
    voxel_size: Sequence[float],
    grid_min: Sequence[float],
    grid_max: Sequence[float],
) -> VoxelScatter:
    mean, max_features, point_to_voxel = _VoxelScatterFunction.apply(
        points,
        features,
        voxel_size,
        grid_min,
        grid_max,
    )
    return VoxelScatter(mean=mean, max=max_features, point_to_voxel=point_to_voxel)


def _register_fake(op_name: str):
    def decorate(fn):
        try:
            return torch.library.register_fake(op_name)(fn)
        except RuntimeError as exc:
            if "already has" in str(exc):
                return fn
            raise

    return decorate


@_register_fake("vaso_graph::voxel_scatter_forward")
def _fake_voxel_scatter_forward(
    points: torch.Tensor,
    features: torch.Tensor,
    voxel_size: Sequence[float],
    grid_min: Sequence[float],
    grid_max: Sequence[float],
) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor, torch.Tensor, torch.Tensor]:
    if points.dim() != 2 or points.shape[1] != 3:
        raise ValueError("points must have shape (N, 3)")
    if features.dim() != 2 or features.shape[0] != points.shape[0]:
        raise ValueError("features must have shape (N, C)")
    voxel_count = _num_voxels(
        _triplet("voxel_size", voxel_size),
        _triplet("grid_min", grid_min),
        _triplet("grid_max", grid_max),
    )
    channels = features.shape[1]
    return (
        features.new_empty((voxel_count, channels)),
        features.new_empty((voxel_count, channels)),
        points.new_empty((points.shape[0],), dtype=torch.long),
        points.new_empty((voxel_count,), dtype=torch.long),
        points.new_empty((voxel_count, channels), dtype=torch.long),
    )


@_register_fake("vaso_graph::voxel_scatter_backward")
def _fake_voxel_scatter_backward(
    grad_mean: torch.Tensor,
    grad_max: torch.Tensor,
    point_to_voxel: torch.Tensor,
    counts: torch.Tensor,
    max_indices: torch.Tensor,
) -> torch.Tensor:
    del grad_max, counts, max_indices
    return grad_mean.new_empty((point_to_voxel.shape[0], grad_mean.shape[1]))


__all__ = ["VoxelScatter", "voxel_scatter"]
