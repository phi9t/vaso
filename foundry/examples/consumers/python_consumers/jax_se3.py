"""JAX SE3 projection consumer and NumPy reference."""

from __future__ import annotations

from .reference import example_inputs_python


def fixture_numpy():
    import numpy as np

    points, poses, intrinsics = example_inputs_python()
    return (
        np.asarray(points, dtype=np.float32),
        np.asarray(poses, dtype=np.float32),
        np.asarray(intrinsics, dtype=np.float32),
    )


def project_se3_pinhole_numpy_reference(points, poses, intrinsics):
    import numpy as np

    camera_xyz = np.einsum("bij,bnj->bni", poses[:, :3, :3], points) + poses[:, None, :3, 3]
    depth = camera_xyz[..., 2]
    valid = depth > 0.0
    homogeneous = np.einsum("bij,bnj->bni", intrinsics, camera_xyz)
    denom = np.where(valid, homogeneous[..., 2], 1.0)
    pixels = np.stack((homogeneous[..., 0] / denom, homogeneous[..., 1] / denom), axis=-1)
    pixels = np.where(valid[..., None], pixels, np.zeros_like(pixels))
    return pixels.astype(np.float32), depth.astype(np.float32), valid


def _project_se3_pinhole_jax(points, poses, intrinsics):
    import jax.numpy as jnp

    camera_xyz = jnp.einsum("bij,bnj->bni", poses[:, :3, :3], points) + poses[:, None, :3, 3]
    depth = camera_xyz[..., 2]
    valid = depth > 0.0
    homogeneous = jnp.einsum("bij,bnj->bni", intrinsics, camera_xyz)
    denom = jnp.where(valid, homogeneous[..., 2], 1.0)
    pixels = jnp.stack((homogeneous[..., 0] / denom, homogeneous[..., 1] / denom), axis=-1)
    pixels = jnp.where(valid[..., None], pixels, jnp.zeros_like(pixels))
    return pixels, depth, valid


def project_se3_pinhole_jax(points, poses, intrinsics):
    import jax

    return jax.jit(_project_se3_pinhole_jax)(points, poses, intrinsics)
