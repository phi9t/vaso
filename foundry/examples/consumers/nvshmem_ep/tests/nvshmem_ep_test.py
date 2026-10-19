#!/usr/bin/env python3
from __future__ import annotations

import os
from datetime import timedelta

import torch
import torch.distributed as dist
import vaso_nvshmem_ep


TOKENS_PER_RANK = 8
CHANNELS = 4


def _distributed_backend() -> str:
    return "nccl" if torch.cuda.is_available() else "gloo"


def _broadcast_unique_id(rank: int, device: torch.device) -> torch.Tensor:
    uid = vaso_nvshmem_ep.create_unique_id() if rank == 0 else torch.empty(vaso_nvshmem_ep.unique_id_size(), dtype=torch.uint8)
    if dist.get_backend() == "nccl":
        uid_dev = uid.to(device)
        dist.broadcast(uid_dev, src=0)
        return uid_dev.cpu()
    dist.broadcast(uid, src=0)
    return uid


def _sample_tokens(rank: int, device: torch.device) -> torch.Tensor:
    base = torch.arange(TOKENS_PER_RANK * CHANNELS, dtype=torch.float32, device=device).reshape(TOKENS_PER_RANK, CHANNELS)
    return base + rank * 1000.0


def _cpu_reference_dispatch(rank: int, world_size: int) -> torch.Tensor:
    base = torch.arange(TOKENS_PER_RANK * CHANNELS, dtype=torch.float32).reshape(TOKENS_PER_RANK, CHANNELS)
    return torch.stack([base[rank] + peer * 1000.0 for peer in range(world_size)])


def _reference_dispatch(tokens: torch.Tensor, world_size: int) -> torch.Tensor:
    reference = torch.empty(world_size, CHANNELS, dtype=tokens.dtype, device=tokens.device)
    dist.all_to_all_single(reference, tokens.contiguous())
    return reference


def _layout_for_local_experts(rank: int, world_size: int, device: torch.device) -> tuple[torch.Tensor, torch.Tensor]:
    owner = torch.arange(world_size, dtype=torch.long, device=device)
    slot = torch.full((world_size,), rank, dtype=torch.long, device=device)
    assert int(owner[rank].item()) == rank
    return owner, slot


def main() -> None:
    rank = int(os.environ["RANK"])
    local_rank = int(os.environ["LOCAL_RANK"])
    world_size = int(os.environ["WORLD_SIZE"])
    if not torch.cuda.is_available():
        raise RuntimeError("NVSHMEM EP test requires CUDA")
    if world_size != 8:
        raise RuntimeError("WORLD_SIZE == 8 is required for the intranode EP proof")

    torch.cuda.set_device(local_rank)
    device = torch.device(f"cuda:{local_rank}")
    dist.init_process_group(
        backend=_distributed_backend(),
        timeout=timedelta(seconds=120),
    )
    try:
        unique_id = _broadcast_unique_id(rank, device)
        vaso_nvshmem_ep.initialize(rank, world_size, unique_id)

        tokens = _sample_tokens(rank, device)
        expert_for_token = torch.arange(world_size, dtype=torch.long, device=device)
        dispatched = vaso_nvshmem_ep.allocate_symmetric((world_size, CHANNELS), dtype=torch.float32)
        dist.barrier(device_ids=[local_rank])
        vaso_nvshmem_ep.dispatch(tokens, expert_for_token, dispatched, world_size)
        torch.cuda.synchronize()
        dist.barrier(device_ids=[local_rank])

        torch.testing.assert_close(dispatched, _reference_dispatch(tokens, world_size))
        torch.testing.assert_close(dispatched.cpu(), _cpu_reference_dispatch(rank, world_size))

        owner_for_row, slot_for_row = _layout_for_local_experts(rank, world_size, device)
        combined = vaso_nvshmem_ep.allocate_symmetric((TOKENS_PER_RANK, CHANNELS), dtype=torch.float32)
        dist.barrier(device_ids=[local_rank])
        vaso_nvshmem_ep.combine(dispatched, owner_for_row, slot_for_row, combined)
        torch.cuda.synchronize()
        dist.barrier(device_ids=[local_rank])

        torch.testing.assert_close(combined, tokens)
        torch.testing.assert_close(combined.cpu(), _sample_tokens(rank, torch.device("cpu")))
    finally:
        try:
            vaso_nvshmem_ep.finalize()
        finally:
            dist.destroy_process_group()


if __name__ == "__main__":
    main()
