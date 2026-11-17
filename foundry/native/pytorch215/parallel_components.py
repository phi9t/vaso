"""Numerical two-GPU acceptance of the parallel APIs needed by FARO.

FSDP2, tensor parallelism and PP/1F1B are exercised separately on the same two
physical GPUs. This is not an eight-GPU composed PP2×FSDP2×TP2 scaling claim.
All weights, gradients, and one AdamW update are compared to a CPU reference.
"""
from __future__ import annotations

import copy
from typing import Any

import torch
import torch.distributed as dist
from torch import nn
from torch.distributed.device_mesh import init_device_mesh
from torch.profiler import record_function


def model_and_data(seed: int) -> tuple[nn.Module, torch.Tensor, torch.Tensor]:
    torch.manual_seed(seed)
    model = nn.Sequential(nn.Linear(32, 64, bias=False), nn.GELU(), nn.Linear(64, 16, bias=False))
    x = torch.randn(8, 32)
    target = torch.randn(8, 16)
    return model, x, target


def ordinary_tensor(t: torch.Tensor) -> torch.Tensor:
    # DTensor parameters/gradients need collective gathering on every rank.
    return t.full_tensor() if hasattr(t, 'full_tensor') else t


def compare_parameters(model: nn.Module, reference: nn.Module, *, gradients: bool) -> float:
    maximum = 0.0
    pairs = list(zip(model.parameters(), reference.parameters(), strict=True))
    for actual, expected in pairs:
        a = actual.grad if gradients else actual.detach()
        b = expected.grad if gradients else expected.detach()
        if a is None or b is None:
            raise RuntimeError('Missing parameter or gradient in parallel acceptance test')
        a = ordinary_tensor(a).detach().cpu()
        b = b.detach().cpu()
        torch.testing.assert_close(a, b, rtol=2e-3, atol=3e-5)
        maximum = max(maximum, float((a - b).abs().max()))
    return maximum


def run_parallel_components(rank: int, device: torch.device, world: int) -> dict[str, Any]:
    if world != 2:
        raise ValueError('This numerical parallel acceptance fixture requires exactly two ranks')
    result: dict[str, Any] = {'scope': 'separate two-GPU component checks; not a composed 2x2x2 run'}
    mesh = init_device_mesh('cuda', (world,), mesh_dim_names=('acceptance',))

    from torch.distributed.fsdp import fully_shard
    with record_function('faro|qualification|fsdp2'):
        reference, x, target = model_and_data(1337)
        actual = copy.deepcopy(reference).to(device)
        fully_shard(actual, mesh=mesh, reshard_after_forward=True)
        cpuopt = torch.optim.AdamW(reference.parameters(), lr=1e-3, foreach=False)
        gpuopt = torch.optim.AdamW(actual.parameters(), lr=1e-3, foreach=False)
        expected_output = reference(x)
        output = actual(x.to(device))
        cpu_loss = (expected_output - target).square().mean()
        gpu_loss = (output - target.to(device)).square().mean()
        cpu_loss.backward(); gpu_loss.backward()
        torch.testing.assert_close(output.detach().cpu(), expected_output.detach(), rtol=1e-4, atol=1e-5)
        grad_error = compare_parameters(actual, reference, gradients=True)
        cpuopt.step(); gpuopt.step()
        parameter_error = compare_parameters(actual, reference, gradients=False)
        result['fsdp2'] = {'passed': True, 'gradient_max_abs_error': grad_error,
                           'updated_parameter_max_abs_error': parameter_error}
    dist.barrier(device_ids=[rank])

    from torch.distributed.tensor.parallel import ColwiseParallel, RowwiseParallel, parallelize_module
    with record_function('faro|qualification|tensor_parallel'):
        reference, x, target = model_and_data(1338)
        actual = copy.deepcopy(reference).to(device)
        parallelize_module(actual, mesh, {'0': ColwiseParallel(), '2': RowwiseParallel()})
        cpuopt = torch.optim.AdamW(reference.parameters(), lr=1e-3, foreach=False)
        gpuopt = torch.optim.AdamW(actual.parameters(), lr=1e-3, foreach=False)
        expected_output = reference(x)
        output = actual(x.to(device))
        (expected_output - target).square().mean().backward()
        (output - target.to(device)).square().mean().backward()
        torch.testing.assert_close(output.detach().cpu(), expected_output.detach(), rtol=1e-4, atol=1e-5)
        grad_error = compare_parameters(actual, reference, gradients=True)
        cpuopt.step(); gpuopt.step()
        parameter_error = compare_parameters(actual, reference, gradients=False)
        result['tensor_parallel'] = {'passed': True, 'gradient_max_abs_error': grad_error,
                                     'updated_parameter_max_abs_error': parameter_error}
    dist.barrier(device_ids=[rank])

    from torch.distributed.pipelining import PipelineStage, Schedule1F1B
    with record_function('faro|qualification|pipeline_1f1b'):
        reference, x, target = model_and_data(1339)
        if rank == 0:
            actual = nn.Sequential(copy.deepcopy(reference[0]), copy.deepcopy(reference[1])).to(device)
            local_reference = nn.Sequential(reference[0], reference[1])
            input_example = torch.empty(2, 32, device=device)
        else:
            actual = copy.deepcopy(reference[2]).to(device)
            local_reference = reference[2]
            input_example = torch.empty(2, 64, device=device)
        stage = PipelineStage(actual, rank, world, device, input_args=input_example)
        schedule = Schedule1F1B(stage, n_microbatches=4, loss_fn=nn.MSELoss(), scale_grads=True)
        cpuopt = torch.optim.AdamW(reference.parameters(), lr=1e-3, foreach=False)
        gpuopt = torch.optim.AdamW(actual.parameters(), lr=1e-3, foreach=False)
        expected_output = reference(x)
        (expected_output - target).square().mean().backward()
        if rank == 0:
            schedule.step(x.to(device))
        else:
            output = schedule.step(target=target.to(device))
            torch.testing.assert_close(output.detach().cpu(), expected_output.detach(), rtol=1e-4, atol=1e-5)
        grad_error = compare_parameters(actual, local_reference, gradients=True)
        cpuopt.step(); gpuopt.step()
        parameter_error = compare_parameters(actual, local_reference, gradients=False)
        result['pipeline_1f1b'] = {'passed': True, 'stages': world, 'microbatches': 4,
                                  'gradient_max_abs_error': grad_error,
                                  'updated_parameter_max_abs_error': parameter_error}
    dist.barrier(device_ids=[rank])
    return result
