"""Verify the JAX consumer against the pure NumPy reference."""

from __future__ import annotations

import jax
import jax.numpy as jnp
import jaxlib
import numpy as np

from .jax_se3 import fixture_numpy, project_se3_pinhole_jax, project_se3_pinhole_numpy_reference
from .release_checks import assert_shipped_wheel


def _has_gpu_device() -> bool:
    return any(device.platform in {"cuda", "gpu"} for device in jax.devices())


def main() -> None:
    assert_shipped_wheel(jax, "jax")
    assert_shipped_wheel(jaxlib, "jaxlib")
    if not _has_gpu_device():
        raise SystemExit(f"JAX GPU device is not available: {jax.devices()}")

    points, poses, intrinsics = fixture_numpy()
    expected_pixels, expected_depth, expected_valid = project_se3_pinhole_numpy_reference(points, poses, intrinsics)
    actual_pixels, actual_depth, actual_valid = project_se3_pinhole_jax(
        jnp.asarray(points),
        jnp.asarray(poses),
        jnp.asarray(intrinsics),
    )
    actual_pixels.block_until_ready()
    np.testing.assert_allclose(np.asarray(actual_pixels), expected_pixels, rtol=1e-4, atol=1e-4)
    np.testing.assert_allclose(np.asarray(actual_depth), expected_depth, rtol=1e-4, atol=1e-4)
    np.testing.assert_array_equal(np.asarray(actual_valid), expected_valid)
    np.testing.assert_allclose(np.asarray(actual_pixels)[~np.asarray(actual_valid)], 0.0, rtol=0.0, atol=0.0)
    print("python_consumers jax verifier passed")


if __name__ == "__main__":
    main()
