from __future__ import annotations

import torch
import torch.distributed

from . import _C as _C


def unique_id_size() -> int:
    return int(_C.unique_id_size())


def create_unique_id() -> torch.Tensor:
    return _C.create_unique_id()


def initialize(rank: int, world_size: int, unique_id: torch.Tensor) -> None:
    _C.initialize(int(rank), int(world_size), unique_id)


def finalize() -> None:
    _C.finalize()


def allocate_symmetric(shape: tuple[int, ...] | list[int], *, dtype: torch.dtype = torch.float32) -> torch.Tensor:
    if dtype != torch.float32:
        raise TypeError("NVSHMEM EP symmetric allocation supports float32 only")
    return _C.allocate_symmetric_float32(list(shape))


def dispatch(tokens: torch.Tensor, expert_for_token: torch.Tensor, output: torch.Tensor, world_size: int) -> None:
    torch.ops.vaso_graph.nvshmem_ep_dispatch(tokens, expert_for_token, output, int(world_size))


def combine(dispatched: torch.Tensor, owner_for_row: torch.Tensor, slot_for_row: torch.Tensor, output: torch.Tensor) -> None:
    torch.ops.vaso_graph.nvshmem_ep_combine(dispatched, owner_for_row, slot_for_row, output)


__all__ = [
    "allocate_symmetric",
    "combine",
    "create_unique_id",
    "dispatch",
    "finalize",
    "initialize",
    "unique_id_size",
]
