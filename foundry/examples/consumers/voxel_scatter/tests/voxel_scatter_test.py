#!/usr/bin/env python3
from __future__ import annotations

import argparse
import math
import unittest

import torch
import vaso_voxel_scatter


VOXEL_SIZE = (1.0, 1.0, 1.0)
GRID_MIN = (0.0, 0.0, 0.0)
GRID_MAX = (3.0, 2.0, 2.0)


def _grid_dims(device: torch.device) -> torch.Tensor:
    size = torch.tensor(VOXEL_SIZE, dtype=torch.float64, device=device)
    low = torch.tensor(GRID_MIN, dtype=torch.float64, device=device)
    high = torch.tensor(GRID_MAX, dtype=torch.float64, device=device)
    return torch.floor((high - low) / size).to(torch.long)


def reference_voxel_scatter(
    points: torch.Tensor,
    features: torch.Tensor,
) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
    dims = _grid_dims(points.device)
    num_voxels = int(dims.prod().item())
    size = points.new_tensor(VOXEL_SIZE)
    low = points.new_tensor(GRID_MIN)
    coords = torch.floor((points - low) / size).to(torch.long)
    in_bounds = ((coords >= 0) & (coords < dims)).all(dim=1)
    linear = coords[:, 0] + dims[0] * (coords[:, 1] + dims[1] * coords[:, 2])
    point_to_voxel = torch.where(in_bounds, linear, torch.full_like(linear, -1))

    mean = features.new_zeros((num_voxels, features.shape[1]))
    counts = features.new_zeros((num_voxels, 1))
    if bool(in_bounds.any()):
        valid_index = linear[in_bounds]
        mean.index_add_(0, valid_index, features[in_bounds])
        counts.index_add_(0, valid_index, features.new_ones((int(valid_index.numel()), 1)))
    mean = torch.where(counts > 0, mean / counts.clamp_min(1), mean)

    max_features = features.new_full((num_voxels, features.shape[1]), -math.inf)
    if bool(in_bounds.any()):
        scatter_index = linear[in_bounds].view(-1, 1).expand(-1, features.shape[1])
        max_features.scatter_reduce_(0, scatter_index, features[in_bounds], reduce="amax", include_self=True)
    max_features = torch.where(counts > 0, max_features, torch.zeros_like(max_features))
    return mean, max_features, point_to_voxel


def sample_inputs(device: torch.device) -> tuple[torch.Tensor, torch.Tensor]:
    points = torch.tensor(
        [
            [0.2, 0.1, 0.4],
            [0.8, 0.4, 0.9],
            [1.2, 0.1, 0.2],
            [2.4, 1.2, 1.1],
            [-0.1, 0.2, 0.2],
            [3.2, 0.3, 0.3],
        ],
        dtype=torch.float32,
        device=device,
    )
    features = torch.tensor(
        [
            [1.0, -2.0, 0.5],
            [3.0, -1.0, 2.5],
            [2.0, 5.0, -3.0],
            [-4.0, 0.25, 7.0],
            [9.0, 9.0, 9.0],
            [8.0, 8.0, 8.0],
        ],
        dtype=torch.float32,
        device=device,
        requires_grad=True,
    )
    return points, features


class VoxelScatterTest(unittest.TestCase):
    device: torch.device

    def test_forward_matches_reference_with_empty_and_out_of_bounds_points(self) -> None:
        points, features = sample_inputs(self.device)
        actual = vaso_voxel_scatter.voxel_scatter(points, features, VOXEL_SIZE, GRID_MIN, GRID_MAX)
        expected = reference_voxel_scatter(points, features)

        torch.testing.assert_close(actual.mean, expected[0])
        torch.testing.assert_close(actual.max, expected[1])
        torch.testing.assert_close(actual.point_to_voxel, expected[2])
        self.assertTrue((actual.point_to_voxel < 0).any())
        self.assertTrue((actual.mean == 0).all(dim=1).any())

    def test_backward_matches_reference(self) -> None:
        points, features = sample_inputs(self.device)
        ref_features = features.detach().clone().requires_grad_(True)

        actual = vaso_voxel_scatter.voxel_scatter(points, features, VOXEL_SIZE, GRID_MIN, GRID_MAX)
        expected_mean, expected_max, _ = reference_voxel_scatter(points, ref_features)

        actual_loss = (actual.mean.square().sum() * 0.3) + (actual.max.square().sum() * 0.7)
        expected_loss = (expected_mean.square().sum() * 0.3) + (expected_max.square().sum() * 0.7)
        actual_loss.backward()
        expected_loss.backward()

        torch.testing.assert_close(features.grad, ref_features.grad, rtol=1e-5, atol=1e-6)
        self.assertEqual(features.grad[4].abs().sum().item(), 0.0)
        self.assertEqual(features.grad[5].abs().sum().item(), 0.0)

    def test_dispatcher_opcheck_and_fake_registration(self) -> None:
        points, features = sample_inputs(self.device)
        op = torch.ops.vaso_graph.voxel_scatter_forward.default
        torch.library.opcheck(op, (points, features.detach(), list(VOXEL_SIZE), list(GRID_MIN), list(GRID_MAX)))

    def test_torch_compile_runs_through_python_wrapper(self) -> None:
        points, features = sample_inputs(self.device)

        def model(feature_tensor: torch.Tensor) -> torch.Tensor:
            scattered = vaso_voxel_scatter.voxel_scatter(
                points,
                feature_tensor,
                VOXEL_SIZE,
                GRID_MIN,
                GRID_MAX,
            )
            return scattered.mean.sum() + scattered.max.square().sum()

        compiled = torch.compile(model, backend="eager", fullgraph=True)
        out = compiled(features)
        out.backward()
        self.assertTrue(torch.isfinite(out))
        self.assertIsNotNone(features.grad)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--device", choices=("cpu", "cuda"), default="cpu")
    args, remaining = parser.parse_known_args()
    if args.device == "cuda" and not torch.cuda.is_available():
        raise unittest.SkipTest("CUDA device is not available")
    VoxelScatterTest.device = torch.device(args.device)
    unittest.main(argv=["voxel_scatter_test.py", *remaining])


if __name__ == "__main__":
    main()
