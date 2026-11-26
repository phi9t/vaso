"""Offline Inkling loader retaining native packed experts and FP32 activations.

Only ordinary parameters pass through Transformers' native conversion mapping.
Expert tensors stay in CPU safetensors-backed storage outside Module parameters
and buffers. A shared LRU admits selected expert slices to each CUDA device.
The cache budget is per device, excludes activations and ordinary parameters,
and fails before allocation when a single expert cannot fit. This bounds retained
cache payload, not allocator memory or in-flight tensors on other streams.

load_packed_checkpoint(path, cache_budget_bytes=..., text_device=None) returns
(model, loading_info). Optional text_device materializes ordinary text tensors
directly on that device, then selects text-only placement. It uses the pinned
native tensor loader without an outer Accelerate device map or dispatch hooks.
model.packed_weights.stats() reports storage and cache accounting.
model.packed_weights.clear_cache(device=None) releases retained device entries.
model.to(...) moves ordinary parameters only; expert dispatch follows the actual
activation device. Clear the cache explicitly when releasing a GPU.
place_packed_text_model(model, device) leaves the vision tower on CPU and
selects explicit text-only input mode; metadata is model.packed_text_placement.

Cross-device archive refills use explicit synchronous CPU staging, never implicit
peer copies. The source archive is immutable after installation.

Optional store.prepare_archive(archive_device, compute_device=..., budget_bytes=...)
preloads fixed whole-layer native banks on a distinct CUDA device. Selection
favors physical layers visited most often by the model loop. Archived keys are
always sourced there on compute-cache misses; other keys retain CPU backing.
clear_cache() leaves this source archive intact. clear_archive() releases it;
close() synchronizes and releases both stores. Allocator pools may retain freed
memory until the caller empties each device cache or the process exits.
"""
from collections import Counter, OrderedDict
from contextlib import ExitStack
from copy import deepcopy
from functools import wraps
from dataclasses import dataclass, replace
from pathlib import Path
import re
import threading
import time

import torch
import torch.nn.functional as F
from safetensors import safe_open
from transformers import Inkling2LoopConfig, Inkling2LoopForConditionalGeneration
from transformers.core_model_loading import Inkling2LoopDequantizeExperts

DEFAULT_CACHE_BYTES = 256 * 1024 * 1024
EXPERT_KEY = re.compile(
    r"^model\.llm\.layers\.(\d+)\.mlp\.experts\.(w13|w2)_weight"
    r"(_scale|_amax|_outer_scale_denominator)?$"
)


def _bytes(tensor):
    return tensor.numel() * tensor.element_size()


@dataclass(frozen=True)
class PackedMatrix:
    layer: int
    projection: str
    format: str
    weight: torch.Tensor
    scales: torch.Tensor
    outer_scale: torch.Tensor | None
    input_features: int
    output_features: int
    source_bytes: int

    @property
    def num_experts(self):
        return self.weight.shape[0]

    @property
    def expert_bytes(self):
        return (_bytes(self.weight[0]) + _bytes(self.scales[0])
                + (4 if self.outer_scale is not None else 0))

    @property
    def raw_bytes(self):
        return (_bytes(self.weight) + _bytes(self.scales)
                + (_bytes(self.outer_scale) if self.outer_scale is not None else 0))


@dataclass(frozen=True)
class CachedExpert:
    weight: torch.Tensor
    scales: torch.Tensor
    outer_scale: torch.Tensor | None
    bytes: int


class PackedWeightStore:
    """CPU packed backing plus a bounded, device-keyed expert LRU."""

    def __init__(self, cache_budget_bytes=DEFAULT_CACHE_BYTES, *, layer_usage=None):
        if not isinstance(cache_budget_bytes, int) or cache_budget_bytes <= 0:
            raise ValueError("cache_budget_bytes must be a positive integer")
        self.cache_budget_bytes = cache_budget_bytes
        self.matrices = {}
        self.layer_usage = dict(layer_usage or {})
        self._archive_banks = {}
        self._archive_stats = self._empty_archive_stats()
        self._caches = {}
        self._devices = {}
        self._lock = threading.RLock()
        self._peak_bytes = 0
        self.nonexpert_parameter_bytes = 0
        self.nonexpert_parameter_bytes_by_prefix = {}
        self.cpu_reference_expert_decodes = 0
        self.cpu_reference_peak_dense_expert_bytes = 0
        self.dense_expert_parameters_were_meta = False
        self.removed_expert_dequantization_converters = 0

    def _empty_archive_stats(self):
        return {
            "enabled": False, "device": None, "compute_device": None,
            "budget_bytes": 0, "resident_bytes": 0, "peak_bytes": 0,
            "selected_bytes": 0, "selected_layers": [], "banks": [],
            "selected_logical_visits": 0, "total_logical_visits": 0,
            "layer_usage": {}, "policy": "whole layers: visits descending, bytes ascending, index ascending",
            "source_hits": 0, "source_hit_bytes": 0, "cache_misses_at_prepare": 0,
            "source_misses": 0, "source_miss_bytes": 0,
            "source_hits_by_format": {}, "source_misses_by_format": {},
            "initialization_seconds": 0.0, "peer_access": {}, "cuda_memory": {},
            "copy_mode": "explicit synchronous host bounce for cross-GPU copies",
            "budget_scope": "immutable packed payload only; allocator and CUDA context overhead excluded",
            "allocator_peak_scope": "maximum of observed PyTorch device peaks; caller may reset counters between observations",
        }

    def plan_archive(self, budget_bytes):
        """CPU-only whole-layer plan; bank bytes include every expert and scale.

        layer_usage has string physical-layer keys in the returned JSON-safe
        metadata. A plan too small for every complete layer is rejected before
        any CUDA query or allocation. Layers that do not fit are skipped.
        """
        if isinstance(budget_bytes, bool) or not isinstance(budget_bytes, int) or budget_bytes <= 0:
            raise ValueError("Archive budget_bytes must be a positive integer")
        layers = {}
        for key, matrix in self.matrices.items():
            layers.setdefault(matrix.layer, []).append(key)
        if not layers:
            raise ValueError("Cannot archive an empty packed store")
        usage, sizes = {}, {}
        for layer, keys in layers.items():
            if {key[1] for key in keys} != {"w13", "w2"}:
                raise ValueError(f"Archive layer {layer} requires both w13 and w2 banks")
            visits = self.layer_usage.get(layer, 1)
            if isinstance(visits, bool) or not isinstance(visits, int) or visits <= 0:
                raise ValueError(f"Invalid physical-layer usage for {layer}: {visits}")
            usage[layer] = visits
            sizes[layer] = sum(self.matrices[key].raw_bytes for key in keys)
        selected, used = [], 0
        for layer in sorted(layers, key=lambda index: (-usage[index], sizes[index], index)):
            if used + sizes[layer] <= budget_bytes:
                selected.append(layer)
                used += sizes[layer]
        if not selected:
            raise ValueError(
                f"Archive budget {budget_bytes} bytes cannot fit a complete layer; "
                f"smallest layer requires {min(sizes.values())} bytes"
            )
        banks = []
        for layer in selected:
            for key in sorted(layers[layer]):
                matrix = self.matrices[key]
                banks.append({
                    "layer": layer, "projection": matrix.projection,
                    "format": matrix.format, "bytes": matrix.raw_bytes,
                    "uses_per_forward": usage[layer],
                })
        return {
            "policy": self._empty_archive_stats()["policy"],
            "budget_bytes": budget_bytes, "selected_bytes": used,
            "selected_layers": selected, "banks": banks,
            "selected_logical_visits": sum(usage[index] for index in selected),
            "total_logical_visits": sum(usage.values()),
            "layer_usage": {str(index): usage[index] for index in sorted(usage)},
        }

    @staticmethod
    def _copy_archive_tensor(source, device):
        """Copy without using CUDA peer transport, even if peer access is enabled.

        Archive inputs are immutable and published after synchronization. Only a
        selected expert is staged at refill, bounding host memory by that slice.
        Do not use a dtype conversion: packed codes and scales must keep their
        original bytes, including NaN encodings. No direct peer mode is exposed
        until its integrity is independently qualified.
        """
        target = torch.device(device)
        if source.device.type == "cuda" and target.type == "cuda" and source.device != target:
            torch.cuda.synchronize(source.device)
            staging = source.to(device="cpu", non_blocking=False, copy=True)
            result = staging.to(device=target, non_blocking=False, copy=True)
            torch.cuda.current_stream(target).synchronize()
            return result
        # Also forbid aliases for CPU-backed or same-device copies.
        return source.to(device=target, non_blocking=False, copy=True)

    def _copy_archive_bank(self, matrix, device):
        # Clear partial allocations from traceback locals if a later copy fails.
        weight = scales = outer = None
        try:
            weight = self._copy_archive_tensor(matrix.weight, device)
            scales = self._copy_archive_tensor(matrix.scales, device)
            if matrix.outer_scale is not None:
                outer = self._copy_archive_tensor(matrix.outer_scale, device)
            torch.cuda.current_stream(device).synchronize()
            return CachedExpert(weight, scales, outer, matrix.raw_bytes)
        finally:
            weight = scales = outer = None

    def _archive_devices(self):
        return {
            key for key in (self._archive_stats["device"], self._archive_stats["compute_device"])
            if key is not None
        }

    def _capture_archive_memory(self):
        for key in sorted(self._archive_devices()):
            current = self._archive_stats["cuda_memory"].setdefault(key, {})
            allocated = int(torch.cuda.memory_allocated(key))
            reserved = int(torch.cuda.memory_reserved(key))
            current.setdefault("before_allocated_bytes", allocated)
            current.setdefault("before_reserved_bytes", reserved)
            current.update(
                allocated_bytes=allocated, reserved_bytes=reserved,
                max_memory_allocated_bytes=max(
                    current.get("max_memory_allocated_bytes", 0),
                    int(torch.cuda.max_memory_allocated(key)),
                ),
                max_memory_reserved_bytes=max(
                    current.get("max_memory_reserved_bytes", 0),
                    int(torch.cuda.max_memory_reserved(key)),
                ),
            )

    def prepare_archive(self, archive_device, *, compute_device, budget_bytes):
        """Preload immutable native banks on a distinct CUDA source device.

        Both devices must already be owned by the caller. Preparation publishes
        no source banks until every selected copy has completed. Failure drops
        all partial allocations. Changing devices requires clear_archive().
        Peer capability is recorded here, never by CPU planning or loading.
        """
        with self._lock:
            if self._archive_banks:
                raise RuntimeError("An archive is already installed; clear_archive() first")
            plan = self.plan_archive(budget_bytes)
            source = self._device_key(archive_device)
            compute = self._device_key(compute_device)
            if source == compute:
                raise ValueError("Archive and compute devices must be distinct CUDA devices")
            count = torch.cuda.device_count()
            if any(torch.device(key).index >= count for key in (source, compute)):
                raise ValueError("Archive and compute devices must both be visible")
            if self._devices.get(source, {}).get("resident_bytes", 0):
                raise ValueError("Clear compute-cache entries on the archive device first")
            self._archive_stats = self._empty_archive_stats()
            self._archive_stats.update(
                plan, device=source, compute_device=compute,
                cache_misses_at_prepare=sum(item["misses"] for item in self._devices.values()),
            )
            source_index, compute_index = torch.device(source).index, torch.device(compute).index
            self._archive_stats["peer_access"] = {
                "archive_to_compute": bool(torch.cuda.can_device_access_peer(source_index, compute_index)),
                "compute_to_archive": bool(torch.cuda.can_device_access_peer(compute_index, source_index)),
            }
            self._capture_archive_memory()
            started, pending, used = time.monotonic(), {}, 0
            try:
                for bank in plan["banks"]:
                    key = (bank["layer"], bank["projection"])
                    assert used + bank["bytes"] <= budget_bytes
                    pending[key] = self._copy_archive_bank(self.matrices[key], source)
                    used += bank["bytes"]
                    self._archive_stats["peak_bytes"] = used
                assert used == plan["selected_bytes"]
                self._archive_banks = pending
                self._archive_stats.update(enabled=True, resident_bytes=used)
            except BaseException as error:
                # No archive is exposed on failure, including mid-bank failure.
                try:
                    torch.cuda.synchronize(source)
                except BaseException as cleanup_error:
                    self._archive_stats["rollback_sync_error"] = repr(cleanup_error)
                pending.clear()
                self._archive_banks = {}
                self._archive_stats.update(enabled=False, resident_bytes=0, error=repr(error))
                raise
            finally:
                self._archive_stats["initialization_seconds"] = time.monotonic() - started
                self._capture_archive_memory()
            return deepcopy(self._archive_stats)

    def clear_archive(self):
        """Synchronize and release fixed source banks; retain history and LRU."""
        with self._lock:
            try:
                for key in sorted(self._archive_devices()):
                    torch.cuda.synchronize(key)
            finally:
                self._archive_banks.clear()
                self._archive_stats.update(enabled=False, resident_bytes=0)
                self._capture_archive_memory()

    def close(self):
        """Synchronize all used devices and release archive plus compute caches.

        This drops every retained tensor owned by the store. CUDA allocator pools
        can remain reserved; a dedicated owner may empty each cache afterward.
        """
        with self._lock:
            devices = set(self._caches) | self._archive_devices()
            try:
                for key in sorted(devices):
                    torch.cuda.synchronize(key)
            finally:
                self.clear_cache()
                self._archive_banks.clear()
                self._archive_stats.update(enabled=False, resident_bytes=0)
                self._capture_archive_memory()

    def add(self, matrix):
        if self._archive_banks:
            raise RuntimeError("Cannot change packed banks while a fixed archive is installed")
        key = (matrix.layer, matrix.projection)
        if key in self.matrices:
            raise ValueError(f"Duplicate packed matrix {key}")
        if matrix.weight.device.type != "cpu" or matrix.scales.device.type != "cpu":
            raise ValueError("Packed backing must remain on CPU")
        if matrix.expert_bytes > self.cache_budget_bytes:
            raise ValueError(
                f"Packed expert {key} requires {matrix.expert_bytes} bytes; "
                f"per-device cache budget is only {self.cache_budget_bytes} bytes"
            )
        self.matrices[key] = matrix

    def _device_key(self, device):
        device = torch.device(device)
        if device.type != "cuda":
            raise ValueError(f"Device cache requires CUDA, got {device}")
        index = device.index if device.index is not None else torch.cuda.current_device()
        return f"cuda:{index}"

    def get(self, layer, projection, expert, device):
        """Admit one expert before launch; never expands its quantized weights."""
        device_key = self._device_key(device)
        matrix_key = (int(layer), str(projection))
        matrix = self.matrices[matrix_key]
        if not 0 <= expert < matrix.num_experts:
            raise IndexError(f"Expert {expert} outside [0, {matrix.num_experts})")
        size = matrix.expert_bytes
        if size > self.cache_budget_bytes:
            raise ValueError(
                f"Packed expert {matrix_key} requires {size} bytes; "
                f"per-device cache budget is {self.cache_budget_bytes} bytes"
            )
        key = (*matrix_key, int(expert))
        with self._lock:
            if self._archive_banks and device_key != self._archive_stats["compute_device"]:
                raise ValueError("Clear the fixed archive before changing its compute device")
            cache = self._caches.setdefault(device_key, OrderedDict())
            stats = self._devices.setdefault(
                device_key, {"resident_bytes": 0, "peak_bytes": 0,
                             "hits": 0, "misses": 0, "evictions": 0}
            )
            if key in cache:
                stats["hits"] += 1
                value = cache.pop(key)
                cache[key] = value
            else:
                stats["misses"] += 1
                while cache and stats["resident_bytes"] + size > self.cache_budget_bytes:
                    _, removed = cache.popitem(last=False)
                    stats["resident_bytes"] -= removed.bytes
                    stats["evictions"] += 1
                    del removed
                # Budget admission precedes every device allocation.
                assert stats["resident_bytes"] + size <= self.cache_budget_bytes
                source = self._archive_banks.get(matrix_key, matrix)
                weight = self._copy_archive_tensor(source.weight[expert:expert + 1], device_key)
                scales = self._copy_archive_tensor(source.scales[expert:expert + 1], device_key)
                outer = (self._copy_archive_tensor(source.outer_scale[expert:expert + 1], device_key)
                         if source.outer_scale is not None else None)
                if self._archive_banks:
                    archived = matrix_key in self._archive_banks
                    if archived:
                        # Publish cache entries only after every staged field completes.
                        torch.cuda.current_stream(device_key).synchronize()
                    counter = "source_hits" if archived else "source_misses"
                    self._archive_stats[counter] += 1
                    byte_counter = "source_hit_bytes" if archived else "source_miss_bytes"
                    self._archive_stats[byte_counter] += size
                    formats = self._archive_stats[counter + "_by_format"]
                    formats[matrix.format] = formats.get(matrix.format, 0) + 1
                value = CachedExpert(weight, scales, outer, size)
                cache[key] = value
                stats["resident_bytes"] += size
                stats["peak_bytes"] = max(stats["peak_bytes"], stats["resident_bytes"])
                self._peak_bytes = max(
                    self._peak_bytes, sum(item["resident_bytes"] for item in self._devices.values())
                )
            # A later eviction must not reuse memory still consumed by another stream.
            stream = torch.cuda.current_stream(device_key)
            value.weight.record_stream(stream)
            value.scales.record_stream(stream)
            if value.outer_scale is not None:
                value.outer_scale.record_stream(stream)
            return value

    def clear_cache(self, device=None):
        """Drop all retained CUDA slices, or only those for the selected device."""
        with self._lock:
            keys = list(self._caches) if device is None else [self._device_key(device)]
            for key in keys:
                self._caches.pop(key, None)
                if key in self._devices:
                    self._devices[key]["resident_bytes"] = 0

    def stats(self):
        with self._lock:
            self._capture_archive_memory()
            devices = {key: dict(value) for key, value in self._devices.items()}
            formats = Counter(matrix.format for matrix in self.matrices.values())
            return {
                "archive": deepcopy(self._archive_stats),
                "cache_budget_bytes": self.cache_budget_bytes,
                "cache_budget_scope": "per CUDA device; packed expert payload only",
                "resident_bytes": sum(item["resident_bytes"] for item in devices.values()),
                "peak_bytes": self._peak_bytes,
                "devices": devices,
                "hits": sum(item["hits"] for item in devices.values()),
                "misses": sum(item["misses"] for item in devices.values()),
                "evictions": sum(item["evictions"] for item in devices.values()),
                "raw_storage_bytes": sum(matrix.raw_bytes for matrix in self.matrices.values()),
                "source_storage_bytes": sum(matrix.source_bytes for matrix in self.matrices.values()),
                "nonexpert_parameter_bytes": self.nonexpert_parameter_bytes,
                "nonexpert_parameter_bytes_by_prefix": dict(self.nonexpert_parameter_bytes_by_prefix),
                "avoided_dense_expert_bytes": sum(
                    matrix.num_experts * matrix.output_features * matrix.input_features * 4
                    for matrix in self.matrices.values()
                ),
                "largest_expert_bytes": max(
                    (matrix.expert_bytes for matrix in self.matrices.values()), default=0
                ),
                "format_counts": dict(formats),
                "matrix_count": len(self.matrices),
                "whole_expert_stack_dequantized": False,
                "dense_expert_parameters_were_meta": self.dense_expert_parameters_were_meta,
                "removed_expert_dequantization_converters": self.removed_expert_dequantization_converters,
                "activation_quantization": False,
                "cpu_reference_expert_decodes": self.cpu_reference_expert_decodes,
                "cpu_reference_peak_dense_expert_bytes": self.cpu_reference_peak_dense_expert_bytes,
                "cpu_reference_peak_scope": "largest single expanded matrix output; excludes temporary tensors",
                "backing": "CPU safetensors-backed tensors; payload counts are not resident-memory measurements",
            }

    def decode_one_expert(self, layer, projection, expert):
        """CPU reference only. At most one expert matrix is expanded per call."""
        matrix = self.matrices[(layer, projection)]
        codes = matrix.weight[expert]
        n, k = matrix.output_features, matrix.input_features
        if matrix.format == "nvfp4":
            lookup = torch.tensor(
                [0., .5, 1., 1.5, 2., 3., 4., 6.,
                 -0., -.5, -1., -1.5, -2., -3., -4., -6.],
                dtype=torch.float32, device="cpu",
            )
            unpacked = torch.stack((codes & 15, codes >> 4), dim=-1).reshape(n, k)
            values = lookup[unpacked.long()].reshape(n, k // 16, 16)
            scales = matrix.scales[expert].view(torch.float8_e4m3fn).float()
            values = values * scales[..., None]
            values = values * matrix.outer_scale[expert]
        else:
            values = codes.view(torch.float8_e4m3fn).float().reshape(n, k // 32, 32)
            scale_codes = matrix.scales[expert]
            scales = torch.where(scale_codes == 255, torch.full_like(scale_codes, float("nan"), dtype=torch.float32),
                                 torch.exp2(scale_codes.float() - 127))
            values = values * scales[..., None]
        self.cpu_reference_expert_decodes += 1
        self.cpu_reference_peak_dense_expert_bytes = max(
            self.cpu_reference_peak_dense_expert_bytes, n * k * 4
        )
        return values.reshape(n, k)


class PackedInklingExperts(torch.nn.Module):
    """Original checkpoint rows, with no dense expert Parameters or buffers."""

    def __init__(self, original, store, layer):
        super().__init__()
        self.num_experts = original.num_experts
        self.hidden_dim = original.hidden_dim
        self.intermediate_dim = original.intermediate_dim
        self.act_fn = original.act_fn
        self.packed_weights = store
        self.packed_layer = layer
        self.is_packed = True

    def forward(self, hidden_states, top_k_index, top_k_weights):
        if hidden_states.device.type == "cpu":
            return self._cpu_reference(hidden_states, top_k_index, top_k_weights)
        if hidden_states.device.type != "cuda":
            raise ValueError("Packed Inkling experts support CPU reference or CUDA execution")
        # The proven adapter owns activation kernels and accumulation.
        from model_triton_adapter import experts
        return experts(self, hidden_states, top_k_index, top_k_weights)

    @torch.inference_mode()
    def _cpu_reference(self, hidden_states, top_k_index, top_k_weights):
        if hidden_states.dtype != torch.float32:
            raise TypeError("Packed reference requires FP32 activations")
        result = torch.zeros_like(hidden_states, dtype=torch.float32)
        for expert in torch.unique(top_k_index, sorted=True).tolist():
            rows, slots = torch.where(top_k_index == expert)
            inputs = hidden_states.index_select(0, rows)
            weight = self.packed_weights.decode_one_expert(self.packed_layer, "w13", expert)
            raw = F.linear(inputs, weight)
            del weight
            # Original w13 rows alternate gate/up; the dense HF loader deinterleaves.
            gate, up = raw[:, 0::2], raw[:, 1::2]
            gc, uc = self.act_fn.gate_softcap.softcap, self.act_fn.up_softcap.softcap
            activated = (torch.tanh(gate / gc) * gc * torch.sigmoid(gate)
                         * (torch.tanh(up / uc) * uc))
            weight = self.packed_weights.decode_one_expert(self.packed_layer, "w2", expert)
            contribution = F.linear(activated, weight)
            del weight
            contribution *= top_k_weights[rows, slots, None]
            result.index_add_(0, rows, contribution)
        return result


def _read_matrix(tensors, layer, projection, original):
    prefix = f"model.llm.layers.{layer}.mlp.experts.{projection}_weight"
    raw = tensors[prefix]
    scales = tensors[prefix + "_scale"]
    n = 2 * original.intermediate_dim if projection == "w13" else original.hidden_dim
    k = original.hidden_dim if projection == "w13" else original.intermediate_dim
    e = original.num_experts
    relevant = {key: value for key, value in tensors.items() if key.startswith(prefix)}
    source_bytes = sum(_bytes(value) for value in relevant.values())
    if prefix + "_amax" in tensors:
        expected = {prefix, prefix + "_scale", prefix + "_amax",
                    prefix + "_outer_scale_denominator"}
        if set(relevant) != expected:
            raise ValueError(f"Incomplete NVFP4 metadata for {prefix}")
        if raw.dtype != torch.uint8 or scales.dtype != torch.float8_e4m3fn:
            raise TypeError(f"NVFP4 storage types do not match {prefix}")
        if k % 16 or tuple(raw.shape) != (e, n, k // 2) or tuple(scales.shape) != (e, n, k // 16):
            raise ValueError(f"NVFP4 shape mismatch for {prefix}: {raw.shape}, {scales.shape}")
        amax = tensors[prefix + "_amax"].float()
        denominator = tensors[prefix + "_outer_scale_denominator"].float()
        if tuple(amax.shape) != (e,) or denominator.numel() != 1:
            raise ValueError(f"NVFP4 outer scale shape mismatch for {prefix}")
        if not bool(torch.isfinite(amax).all()) or bool((amax < 0).any()):
            raise ValueError(f"Invalid expert amax for {prefix}")
        if not bool(torch.isfinite(denominator).all()) or float(denominator.item()) <= 0:
            raise ValueError(f"Invalid NVFP4 denominator for {prefix}")
        outer = (amax / denominator).contiguous()
        format_name = "nvfp4"
    else:
        if set(relevant) != {prefix, prefix + "_scale"}:
            raise ValueError(f"Unexpected MXFP8 metadata for {prefix}")
        if raw.dtype != torch.float8_e4m3fn or scales.dtype != torch.uint8:
            raise TypeError(f"MXFP8 storage types do not match {prefix}")
        if k % 32 or tuple(raw.shape) != (e, n, k) or tuple(scales.shape) != (e, n, k // 32):
            raise ValueError(f"MXFP8 requires actual per-row 1x32 scales: {prefix}")
        outer, format_name = None, "mxfp8"
    if not raw.is_contiguous() or not scales.is_contiguous():
        raise ValueError(f"Checkpoint tensors must be contiguous: {prefix}")
    return PackedMatrix(layer, projection, format_name,
                        raw.view(torch.uint8), scales.view(torch.uint8),
                        outer, k, n, source_bytes)


def _physical_layer_usage(config):
    """Derive physical-bank visits from the normalized pinned model loop."""
    count = config.num_hidden_layers
    start, end, passes = (
        config.loop_layer_index_start, config.loop_layer_index_end, config.loop_num_passes,
    )
    if not 0 <= start <= end <= count or passes < 1:
        raise ValueError("Invalid normalized Inkling loop configuration")
    usage = {index: passes if start <= index < end else 1 for index in range(count)}
    if sum(usage.values()) != config.total_num_hidden_layers:
        raise ValueError("Physical-bank usage disagrees with the model's logical layer count")
    return usage


class PackedInklingForConditionalGeneration(Inkling2LoopForConditionalGeneration):
    def __init__(self, config, cache_budget_bytes=DEFAULT_CACHE_BYTES, text_device=None):
        super().__init__(config)
        self.packed_weights = PackedWeightStore(
            cache_budget_bytes, layer_usage=_physical_layer_usage(config.get_text_config()),
        )
        self._packed_text_load_device = (
            None if text_device is None else _normalize_text_device(text_device)
        )
        self._packed_text_load_map = None
        self.packed_load_placement = None

    @classmethod
    def is_custom_code(cls):
        # This is the native architecture with a load hook. Retain its exact
        # built-in checkpoint renamings rather than dropping them for a subclass.
        return False

    @staticmethod
    def _load_pretrained_model(model, state_dict, checkpoint_files, load_config, expected_keys=None):
        if state_dict is not None or not checkpoint_files:
            raise ValueError("Packed loading requires local safetensors checkpoint files")
        if load_config.hf_quantizer is not None:
            raise ValueError("Packed loading owns expert storage; another HF quantizer is not supported")
        if any(not str(path).endswith(".safetensors") for path in checkpoint_files):
            raise ValueError("Packed loading supports safetensors only")
        store = model.packed_weights
        ordinary, packed, observed = {}, {}, set()
        with ExitStack() as stack:
            for path in checkpoint_files:
                handle = stack.enter_context(safe_open(str(path), framework="pt", device="cpu"))
                for name in handle.keys():
                    if name in observed:
                        raise ValueError(f"Duplicate checkpoint tensor {name}")
                    observed.add(name)
                    if ".mlp.experts." in name:
                        if not EXPERT_KEY.fullmatch(name):
                            raise ValueError(f"Unrecognized expert tensor {name}")
                        packed[name] = handle.get_tensor(name)
                    else:
                        ordinary[name] = handle.get_slice(name)
            layers = model.model.language_model.layers
            expected_packed = set()
            for index, layer in enumerate(layers):
                original = layer.mlp.experts
                # Native from_pretrained must still be on meta at this boundary.
                if any(not parameter.is_meta for parameter in original.parameters()):
                    raise RuntimeError("Dense expert parameters were allocated before packed replacement")
                for projection in ("w13", "w2"):
                    matrix = _read_matrix(packed, index, projection, original)
                    store.add(matrix)
                    prefix = f"model.llm.layers.{index}.mlp.experts.{projection}_weight"
                    expected_packed.update(key for key in packed if key.startswith(prefix))
                layer.mlp.experts = PackedInklingExperts(original, store, index)
            if expected_packed != set(packed):
                raise ValueError("Checkpoint contains expert layers not represented by the model")
            store.dense_expert_parameters_were_meta = True
            mappings = load_config.weight_mapping or []
            retained = [
                entry for entry in mappings
                if not any(isinstance(op, Inkling2LoopDequantizeExperts)
                           for op in getattr(entry, "operations", ()))
            ]
            store.removed_expert_dequantization_converters = len(mappings) - len(retained)
            if store.removed_expert_dequantization_converters != 2:
                raise RuntimeError("Expected the pinned model's two expert dequantization converters")
            replacements = {"weight_mapping": retained}
            if model._packed_text_load_device is not None:
                if load_config.device_map is not None:
                    raise ValueError("Direct packed text loading does not accept an outer device map")
                if any(getattr(entry, "force_cpu", False) for entry in retained):
                    raise RuntimeError("A retained converter would force direct text weights through CPU")
                device_map = _text_device_map(model, model._packed_text_load_device)
                # Only the already-filtered native tensor loader receives this
                # map. The outer from_pretrained device_map stays unset, so it
                # neither requires Accelerate nor installs dispatch/offload hooks.
                replacements["device_map"] = device_map
                model._packed_text_load_map = dict(device_map)
            load_config = replace(load_config, **replacements)
            if any(".mlp.experts." in name for name in model.state_dict()):
                raise RuntimeError("Expert tensors unexpectedly remain in Module state")
            # No expert source tensor or expert destination Parameter reaches the
            # ordinary loader, and expert converters were explicitly removed.
            return Inkling2LoopForConditionalGeneration._load_pretrained_model(
                model, ordinary, None, load_config, expected_keys
            )

    @wraps(Inkling2LoopForConditionalGeneration.forward)
    def forward(self, *args, **kwargs):
        if getattr(self, "packed_text_placement", None) and _visual_input_present(args, kwargs):
            raise ValueError("Text-only placement does not accept images or pre-encoded visual inputs")
        return super().forward(*args, **kwargs)

    @wraps(Inkling2LoopForConditionalGeneration.get_image_features)
    def get_image_features(self, *args, **kwargs):
        if getattr(self, "packed_text_placement", None):
            raise ValueError("Text-only placement does not accept images")
        return super().get_image_features(*args, **kwargs)

    def save_pretrained(self, *args, **kwargs):
        raise NotImplementedError(
            "Packed model state omits external expert backing. Retain the source "
            "checkpoint and reload with load_packed_checkpoint instead."
        )


def load_packed_checkpoint(checkpoint_dir, *, cache_budget_bytes=DEFAULT_CACHE_BYTES, text_device=None):
    """Return the FP32 model and loading_info, retaining native packed experts.

    text_device=None preserves the ordinary CPU load/reference path. An explicit
    CPU or CUDA device materializes ordinary text parameters directly there and
    leaves vision plus expert backing on CPU. The same text-only input guards as
    place_packed_text_model are then enabled.

    No Accelerate dependency, automatic placement, or offload/dispatch hooks are
    used. The pinned native loader still controls individual copy/conversion
    staging; HF_DEACTIVATE_ASYNC_LOAD=1 selects its sequential loading mode when
    concurrent CPU staging copies would pressure the workload's memory budget.
    """
    target = None if text_device is None else _normalize_text_device(text_device)
    path = Path(checkpoint_dir)
    if not path.is_dir():
        raise ValueError(f"Expected a local checkpoint directory: {path}")
    config = Inkling2LoopConfig.from_pretrained(str(path), local_files_only=True)
    # This loader, rather than ModelOpt or another quantizer, owns packed storage.
    if hasattr(config, "quantization_config"):
        delattr(config, "quantization_config")
    # The pinned framework also forwards constructor kwargs to generation
    # configuration. Keep the default call unchanged and use a serializable
    # canonical device string for the explicit option.
    placement_kwargs = {} if target is None else {"text_device": str(target)}
    model, info = PackedInklingForConditionalGeneration.from_pretrained(
        str(path), config=config, dtype=torch.float32, attn_implementation="eager",
        local_files_only=True, use_safetensors=True, output_loading_info=True,
        cache_budget_bytes=cache_budget_bytes, **placement_kwargs,
    )
    errors = {key: info.get(key) for key in
              ("missing_keys", "unexpected_keys", "mismatched_keys", "error_msgs", "conversion_errors")
              if info.get(key)}
    if errors:
        model.packed_weights.clear_cache()
        raise RuntimeError(f"Packed checkpoint did not load completely: {errors!r}")
    if any(parameter.is_meta for parameter in model.parameters()):
        raise RuntimeError("Ordinary parameters remain on meta after loading")
    if any(parameter.dtype != torch.float32 for parameter in model.parameters()):
        raise RuntimeError("Packed mode requires FP32 ordinary parameters")
    component_bytes = Counter()
    for name, parameter in model.named_parameters():
        if name.startswith("model.vision_tower."):
            prefix = "model.vision_tower"
        elif name.startswith("model.language_model.embed_tokens."):
            prefix = "model.language_model.embed_tokens"
        elif name.startswith("lm_head."):
            prefix = "lm_head"
        elif name.startswith("model.language_model."):
            prefix = "model.language_model.other"
        else:
            prefix = "other"
        component_bytes[prefix] += _bytes(parameter)
    model.packed_weights.nonexpert_parameter_bytes_by_prefix = dict(component_bytes)
    model.packed_weights.nonexpert_parameter_bytes = sum(component_bytes.values())
    model.eval()
    if target is not None:
        # Finalization may initialize non-persistent buffers on CPU, but it
        # must not move an already materialized text Parameter back to CPU.
        device_map = model._packed_text_load_map
        if device_map != _text_device_map(model, target):
            raise RuntimeError("Native text materialization map was not retained")
        if any(getattr(module, "_hf_hook", None) is not None for module in model.modules()):
            raise RuntimeError("Direct packed loading unexpectedly installed dispatch hooks")
        if getattr(model, "hf_device_map", None) is not None:
            raise RuntimeError("An outer device dispatch map unexpectedly owns this model")
        parameters = dict(model.named_parameters())
        pointers = {}
        parameter_bytes = Counter()
        persistent_names = set(model.state_dict())
        for name, parameter in parameters.items():
            expected = _ordinary_device(name, target)
            if parameter.device != expected:
                raise RuntimeError(
                    f"Native loader placed {name} on {parameter.device}, expected {expected}"
                )
            pointers[name] = parameter.data_ptr()
            parameter_bytes[str(parameter.device)] += _bytes(parameter)
        nonpersistent_bytes = Counter()
        for name, buffer in model.named_buffers():
            if name in persistent_names:
                expected = _ordinary_device(name, target)
                if buffer.device != expected:
                    raise RuntimeError(
                        f"Native loader placed buffer {name} on {buffer.device}, expected {expected}"
                    )
            else:
                nonpersistent_bytes[str(buffer.device)] += _bytes(buffer)
        place_packed_text_model(model, target)
        if any(parameter.data_ptr() != pointers[name] for name, parameter in model.named_parameters()):
            raise RuntimeError("Final buffer placement unexpectedly relocated an ordinary parameter")
        model.packed_load_placement = {
            "mode": "direct text tensor loading",
            "text_device": str(target),
            "device_map": device_map,
            "outer_public_device_map_used": False,
            "accelerate_dispatch_hooks_used": False,
            "ordinary_parameters_verified_before_final_placement": True,
            "ordinary_parameter_bytes_by_device": dict(parameter_bytes),
            "persistent_buffers_verified_before_final_placement": True,
            "nonpersistent_buffer_bytes_by_device_before_final_placement": dict(nonpersistent_bytes),
            "parameters_relocated_by_final_placement": False,
            "expert_backing_device": "cpu",
        }
        model.packed_text_placement["direct_load"] = model.packed_load_placement
    return model, info


def _normalize_text_device(device):
    target = torch.device(device)
    if target.type not in ("cpu", "cuda"):
        raise ValueError("Text placement supports CPU or CUDA; meta placement is not allowed")
    if target.type == "cpu":
        return torch.device("cpu")
    if target.index is None:
        return torch.device("cuda", torch.cuda.current_device())
    return target


def _ordinary_device(name, text_device):
    if name.startswith("model.vision_tower."):
        return torch.device("cpu")
    if name.startswith(("model.language_model.", "lm_head.", "logit_softcap.")):
        return text_device
    raise ValueError(f"Unexpected ordinary tensor outside known placement modules: {name}")


def _text_device_map(model, text_device):
    # These are converted native parameter names, not checkpoint model.llm names.
    device_map = {
        "model.language_model": str(text_device),
        "lm_head": str(text_device),
        "logit_softcap": str(text_device),
        "model.vision_tower": "cpu",
    }
    for name, _ in (*model.named_parameters(), *model.named_buffers()):
        _ordinary_device(name, text_device)
    return device_map


def _visual_input_present(args, kwargs):
    """Recognize explicit visual arguments to the native forward signature."""
    if any(value is not None for value in args[1:3]):
        return True
    if any(kwargs.get(key) is not None for key in ("pixel_values", "image_grid_thw")):
        return True
    encoded = kwargs.get("mm_encoder_outputs")
    return isinstance(encoded, dict) and any(value is not None for value in encoded.values())


def place_packed_text_model(model, device):
    """Place ordinary text modules on CPU/CUDA while retaining the vision tower on CPU.

    Returns the same model. Expert bytes remain in CPU backing and are admitted
    through the existing cache on the actual activation device. Existing device
    cache entries are released before placement. Explicit visual inputs are
    rejected while this text-only mode is active.

    Read model.packed_text_placement for selected modules, byte counts, and device
    status. clear_packed_text_placement(model) removes this input restriction
    without moving weights; callers can then choose their image-capable placement.
    """
    if not isinstance(model, PackedInklingForConditionalGeneration):
        raise TypeError("Expected a model returned by load_packed_checkpoint")
    target = _normalize_text_device(device)

    text_modules = {
        "model.language_model": model.model.language_model,
        "lm_head": model.lm_head,
        "logit_softcap": model.logit_softcap,
    }
    vision = model.model.vision_tower
    allowed = tuple(name + "." for name in (*text_modules, "model.vision_tower"))
    for name, parameter in model.named_parameters():
        if not name.startswith(allowed):
            raise ValueError(f"Unexpected ordinary parameter outside known placement modules: {name}")
        if parameter.dtype != torch.float32:
            raise TypeError(f"Text placement preserves FP32 parameters, got {name}: {parameter.dtype}")
    for name, _ in model.named_buffers():
        if not name.startswith(allowed):
            raise ValueError(f"Unexpected ordinary buffer outside known placement modules: {name}")

    model.packed_weights.clear_cache()
    vision.to("cpu")
    for module in text_modules.values():
        module.to(target)

    def verify(module, expected, label):
        for tensor in (*module.parameters(), *module.buffers()):
            if tensor.device != expected:
                raise RuntimeError(f"{label} remains on {tensor.device}, expected {expected}")

    verify(vision, torch.device("cpu"), "vision tower")
    for name, module in text_modules.items():
        verify(module, target, name)
    if any(matrix.weight.device.type != "cpu" or matrix.scales.device.type != "cpu"
           or (matrix.outer_scale is not None and matrix.outer_scale.device.type != "cpu")
           for matrix in model.packed_weights.matrices.values()):
        raise RuntimeError("Expert backing moved off CPU during ordinary text placement")

    model.packed_text_placement = {
        "mode": "text-only",
        "text_device": str(target),
        "vision_device": "cpu",
        "images_supported": False,
        "text_modules": list(text_modules),
        # named_parameters traverses the whole model once and deduplicates
        # aliases, including configurations with tied embedding/head weights.
        "text_parameter_bytes": sum(
            _bytes(parameter) for name, parameter in model.named_parameters()
            if not name.startswith("model.vision_tower.")
        ),
        "vision_parameter_bytes": sum(
            _bytes(parameter) for name, parameter in model.named_parameters()
            if name.startswith("model.vision_tower.")
        ),
        "text_buffer_bytes": sum(
            _bytes(buffer) for name, buffer in model.named_buffers()
            if not name.startswith("model.vision_tower.")
        ),
        "vision_buffer_bytes": sum(
            _bytes(buffer) for name, buffer in model.named_buffers()
            if name.startswith("model.vision_tower.")
        ),
        "expert_backing_device": "cpu",
        "expert_cache_budget_bytes": model.packed_weights.cache_budget_bytes,
        "expert_cache_cleared": model.packed_weights.stats()["resident_bytes"] == 0,
        "direct_load": getattr(model, "packed_load_placement", None),
    }
    return model


def clear_packed_text_placement(model):
    """Remove the text-only input restriction without changing parameter devices."""
    if not isinstance(model, PackedInklingForConditionalGeneration):
        raise TypeError("Expected a model returned by load_packed_checkpoint")
    model.packed_text_placement = None
    return model
