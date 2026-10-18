"""Small Vaso wrapper for the gsplat CUDA 3D Gaussian rasterizer."""

from __future__ import annotations

import math
from typing import Optional

import torch
from torch import Tensor

from . import _C

__all__ = [
    "covars2d_to_conics_and_radii",
    "intersect_offset",
    "intersect_tile",
    "project_ewa_simple",
    "rasterize_to_pixels",
    "reference_scene_tensors",
    "render_reference_scene",
    "render_scene",
]

_ALPHA_THRESHOLD = 1.0 / 255.0


class _ProjectEWASimple(torch.autograd.Function):
    @staticmethod
    def forward(
        ctx,
        means: Tensor,
        covars: Tensor,
        intrinsics: Tensor,
        image_width: int,
        image_height: int,
        camera_model: str,
    ) -> tuple[Tensor, Tensor]:
        means = means.contiguous()
        covars = covars.contiguous()
        intrinsics = intrinsics.contiguous()
        means2d, covars2d = _C.project_ewa_simple_forward(
            means,
            covars,
            intrinsics,
            int(image_width),
            int(image_height),
            camera_model,
        )
        ctx.save_for_backward(means, covars, intrinsics)
        ctx.image_width = int(image_width)
        ctx.image_height = int(image_height)
        ctx.camera_model = camera_model
        return means2d, covars2d

    @staticmethod
    def backward(ctx, v_means2d: Optional[Tensor], v_covars2d: Optional[Tensor]):
        means, covars, intrinsics = ctx.saved_tensors
        if v_means2d is None:
            v_means2d = means.new_zeros((*means.shape[:-1], 2))
        if v_covars2d is None:
            v_covars2d = covars.new_zeros((*covars.shape[:-2], 2, 2))
        v_means, v_covars = _C.project_ewa_simple_backward(
            means,
            covars,
            intrinsics,
            ctx.image_width,
            ctx.image_height,
            ctx.camera_model,
            v_means2d.contiguous(),
            v_covars2d.contiguous(),
        )
        return v_means, v_covars, None, None, None, None


def project_ewa_simple(
    means: Tensor,
    covars: Tensor,
    intrinsics: Tensor,
    image_width: int,
    image_height: int,
    camera_model: str = "pinhole",
) -> tuple[Tensor, Tensor]:
    return _ProjectEWASimple.apply(
        means,
        covars,
        intrinsics,
        int(image_width),
        int(image_height),
        camera_model,
    )


def covars2d_to_conics_and_radii(
    means2d: Tensor,
    covars2d: Tensor,
    opacities: Tensor,
    image_width: int,
    image_height: int,
    radius_clip: float = 0.0,
) -> tuple[Tensor, Tensor]:
    a = covars2d[..., 0, 0]
    b = 0.5 * (covars2d[..., 0, 1] + covars2d[..., 1, 0])
    c = covars2d[..., 1, 1]
    det = a * c - b * b
    valid = det > 0
    inv_det = torch.where(valid, 1.0 / det.clamp_min(torch.finfo(det.dtype).eps), torch.zeros_like(det))
    conics = torch.stack((c * inv_det, -b * inv_det, a * inv_det), dim=-1).contiguous()

    extend = torch.full_like(opacities, 3.33)
    opacity_valid = opacities >= _ALPHA_THRESHOLD
    opacity_extend = torch.sqrt(
        2.0 * torch.log(opacities.clamp_min(_ALPHA_THRESHOLD) / _ALPHA_THRESHOLD)
    )
    extend = torch.minimum(extend, opacity_extend)
    radius_x = torch.ceil(extend * torch.sqrt(a.clamp_min(0.0))).to(torch.int32)
    radius_y = torch.ceil(extend * torch.sqrt(c.clamp_min(0.0))).to(torch.int32)
    radii = torch.stack((radius_x, radius_y), dim=-1).contiguous()

    in_image = (
        (means2d[..., 0] + radius_x > 0)
        & (means2d[..., 0] - radius_x < image_width)
        & (means2d[..., 1] + radius_y > 0)
        & (means2d[..., 1] - radius_y < image_height)
    )
    keep = valid & opacity_valid & in_image & ((radius_x > radius_clip) | (radius_y > radius_clip))
    return conics, torch.where(keep[..., None], radii, torch.zeros_like(radii))


@torch.no_grad()
def intersect_tile(
    means2d: Tensor,
    radii: Tensor,
    depths: Tensor,
    image_width: int,
    image_height: int,
    tile_size: int,
    sort: bool = True,
) -> tuple[Tensor, Tensor, Tensor, int, int]:
    tile_width = math.ceil(int(image_width) / int(tile_size))
    tile_height = math.ceil(int(image_height) / int(tile_size))
    n_images = math.prod(means2d.shape[:-2])
    tiles_per_gauss, isect_ids, flatten_ids = _C.intersect_tile(
        means2d.contiguous(),
        radii.contiguous(),
        depths.contiguous(),
        None,
        None,
        int(n_images),
        int(tile_size),
        int(tile_width),
        int(tile_height),
        bool(sort),
        False,
    )
    return tiles_per_gauss, isect_ids, flatten_ids, tile_width, tile_height


@torch.no_grad()
def intersect_offset(
    isect_ids: Tensor,
    n_images: int,
    tile_width: int,
    tile_height: int,
) -> Tensor:
    return _C.intersect_offset(isect_ids.contiguous(), int(n_images), int(tile_width), int(tile_height))


class _RasterizeToPixels(torch.autograd.Function):
    @staticmethod
    def forward(
        ctx,
        means2d: Tensor,
        conics: Tensor,
        colors: Tensor,
        opacities: Tensor,
        backgrounds: Optional[Tensor],
        masks: Optional[Tensor],
        image_width: int,
        image_height: int,
        tile_size: int,
        tile_offsets: Tensor,
        flatten_ids: Tensor,
        absgrad: bool,
    ) -> tuple[Tensor, Tensor]:
        means2d = means2d.contiguous()
        conics = conics.contiguous()
        colors = colors.contiguous()
        opacities = opacities.contiguous()
        tile_offsets = tile_offsets.contiguous()
        flatten_ids = flatten_ids.contiguous()
        backgrounds_arg = backgrounds.contiguous() if backgrounds is not None else None
        masks_arg = masks.contiguous() if masks is not None else None
        render_colors, render_alphas, last_ids = _C.rasterize_to_pixels_forward(
            means2d,
            conics,
            colors,
            opacities,
            backgrounds_arg,
            masks_arg,
            int(image_width),
            int(image_height),
            int(tile_size),
            tile_offsets,
            flatten_ids,
        )
        empty = means2d.new_empty((0,))
        ctx.save_for_backward(
            means2d,
            conics,
            colors,
            opacities,
            backgrounds_arg if backgrounds_arg is not None else empty,
            masks_arg if masks_arg is not None else empty.bool(),
            tile_offsets,
            flatten_ids,
            render_alphas,
            last_ids,
        )
        ctx.has_backgrounds = backgrounds_arg is not None
        ctx.has_masks = masks_arg is not None
        ctx.image_width = int(image_width)
        ctx.image_height = int(image_height)
        ctx.tile_size = int(tile_size)
        ctx.absgrad = bool(absgrad)
        return render_colors, render_alphas

    @staticmethod
    def backward(ctx, v_render_colors: Optional[Tensor], v_render_alphas: Optional[Tensor]):
        (
            means2d,
            conics,
            colors,
            opacities,
            backgrounds,
            masks,
            tile_offsets,
            flatten_ids,
            render_alphas,
            last_ids,
        ) = ctx.saved_tensors
        if v_render_colors is None:
            v_render_colors = colors.new_zeros((*tile_offsets.shape[:-2], ctx.image_height, ctx.image_width, colors.shape[-1]))
        if v_render_alphas is None:
            v_render_alphas = colors.new_zeros((*tile_offsets.shape[:-2], ctx.image_height, ctx.image_width, 1))
        backgrounds_arg = backgrounds if ctx.has_backgrounds else None
        masks_arg = masks if ctx.has_masks else None
        v_means2d_abs, v_means2d, v_conics, v_colors, v_opacities = _C.rasterize_to_pixels_backward(
            means2d,
            conics,
            colors,
            opacities,
            backgrounds_arg,
            masks_arg,
            ctx.image_width,
            ctx.image_height,
            ctx.tile_size,
            tile_offsets,
            flatten_ids,
            render_alphas,
            last_ids,
            v_render_colors.contiguous(),
            v_render_alphas.contiguous(),
            ctx.absgrad,
        )
        if ctx.absgrad:
            means2d.absgrad = v_means2d_abs
        if ctx.has_backgrounds and ctx.needs_input_grad[4]:
            v_backgrounds = (v_render_colors * (1.0 - render_alphas).float()).sum(dim=(-3, -2))
        else:
            v_backgrounds = None
        return (
            v_means2d,
            v_conics,
            v_colors,
            v_opacities,
            v_backgrounds,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
        )


def rasterize_to_pixels(
    means2d: Tensor,
    conics: Tensor,
    colors: Tensor,
    opacities: Tensor,
    image_width: int,
    image_height: int,
    tile_size: int,
    tile_offsets: Tensor,
    flatten_ids: Tensor,
    backgrounds: Optional[Tensor] = None,
    masks: Optional[Tensor] = None,
    absgrad: bool = False,
) -> tuple[Tensor, Tensor]:
    return _RasterizeToPixels.apply(
        means2d,
        conics,
        colors,
        opacities,
        backgrounds,
        masks,
        int(image_width),
        int(image_height),
        int(tile_size),
        tile_offsets,
        flatten_ids,
        bool(absgrad),
    )


def reference_scene_tensors(
    device: torch.device | str = "cuda",
    dtype: torch.dtype = torch.float32,
    colors: Optional[Tensor] = None,
) -> tuple[Tensor, Tensor, Tensor, Tensor, Tensor]:
    if dtype is not torch.float32:
        raise TypeError("gsplat rasterize kernels are float32-only")
    means = torch.tensor([[[[0.0, 0.0, 4.0]]]], device=device, dtype=dtype)
    covars = torch.zeros((1, 1, 1, 3, 3), device=device, dtype=dtype)
    covars[..., 0, 0] = 1.0
    covars[..., 1, 1] = 1.0
    covars[..., 2, 2] = 1.0
    intrinsics = torch.tensor(
        [[[[4.0, 0.0, 4.0], [0.0, 4.0, 4.0], [0.0, 0.0, 1.0]]]],
        device=device,
        dtype=dtype,
    )
    if colors is None:
        colors = torch.tensor([[[[0.25, 0.5, 0.9]]]], device=device, dtype=dtype)
    opacities = torch.tensor([[[0.8]]], device=device, dtype=dtype)
    return means, covars, intrinsics, colors, opacities


def render_scene(
    means: Tensor,
    covars: Tensor,
    intrinsics: Tensor,
    colors: Tensor,
    opacities: Tensor,
    image_width: int,
    image_height: int,
    tile_size: int = 8,
) -> tuple[Tensor, Tensor]:
    means2d, covars2d = project_ewa_simple(means, covars, intrinsics, image_width, image_height)
    conics, radii = covars2d_to_conics_and_radii(means2d, covars2d, opacities, image_width, image_height)
    depths = means[..., 2].contiguous()
    _, isect_ids, flatten_ids, tile_width, tile_height = intersect_tile(
        means2d,
        radii,
        depths,
        image_width,
        image_height,
        tile_size,
    )
    tile_offsets = intersect_offset(isect_ids, math.prod(means2d.shape[:-2]), tile_width, tile_height)
    return rasterize_to_pixels(
        means2d,
        conics,
        colors,
        opacities,
        image_width,
        image_height,
        tile_size,
        tile_offsets,
        flatten_ids,
    )


def render_reference_scene(
    device: torch.device | str = "cuda",
    colors: Optional[Tensor] = None,
) -> Tensor:
    dtype = colors.dtype if colors is not None else torch.float32
    means, covars, intrinsics, colors, opacities = reference_scene_tensors(device=device, dtype=dtype, colors=colors)
    render_colors, _ = render_scene(
        means,
        covars,
        intrinsics,
        colors,
        opacities,
        image_width=8,
        image_height=8,
        tile_size=8,
    )
    image = render_colors[0]
    expected_shape = (8, 8, render_colors.shape[-1])
    if tuple(image.shape) != expected_shape:
        raise RuntimeError(f"reference scene rendered shape {tuple(image.shape)}, expected {expected_shape}")
    return image
