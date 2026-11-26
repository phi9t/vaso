#!/usr/bin/env python3
"""Compare direct CPU tensor placement with the unchanged default packed loader.

Run against the actual tiny checkpoint inside the complete Insula capsule.
This checks loading and placement only: no model forward, expert decoding, dense
expert reference, or CUDA operation is performed.
"""
import argparse
import gc
import hashlib
import json
from pathlib import Path
import resource
import time
import traceback

import torch
from transformers.utils import is_accelerate_available

from packed_checkpoint import load_packed_checkpoint, place_packed_text_model


def tensor_fingerprint(tensor):
    # memoryview avoids an additional full-tensor bytes copy. Only a
    # noncontiguous tensor needs a temporary contiguous copy.
    value = tensor.detach().contiguous()
    return {
        "shape": tuple(value.shape), "dtype": str(value.dtype),
        "sha256": hashlib.sha256(memoryview(value.numpy())).hexdigest(),
    }


def snapshot(model):
    banks = {}
    for key, matrix in model.packed_weights.matrices.items():
        banks[key] = {
            "format": matrix.format,
            "weight": tensor_fingerprint(matrix.weight),
            "scales": tensor_fingerprint(matrix.scales),
            "outer": None if matrix.outer_scale is None else tensor_fingerprint(matrix.outer_scale),
        }
    return {
        "parameters": {name: tensor_fingerprint(value) for name, value in model.named_parameters()},
        "buffers": {name: tensor_fingerprint(value) for name, value in model.named_buffers()},
        "packed_matrices": banks,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--result", type=Path, required=True)
    args = parser.parse_args()
    torch.set_num_threads(2)
    began = time.monotonic()
    report = {
        "passed": False, "stage": "default_load",
        "device": "cpu", "gpu_placement_tested": False,
        "model_forward_calls": 0, "expert_decodes": 0,
        "accelerate_available": is_accelerate_available(),
        "comparison_method": "shape, dtype, and SHA-256 of logical tensor bytes",
        "model_residency": "one model at a time",
    }

    def save():
        args.result.parent.mkdir(parents=True, exist_ok=True)
        report["seconds"] = time.monotonic() - began
        report["process_peak_rss_bytes"] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024
        args.result.write_text(json.dumps(report, indent=2) + "\n")

    def normalized(info):
        return {key: sorted(value) if isinstance(value, set) else value
                for key, value in info.items()}

    save()
    try:
        print("Loading unchanged default packed CPU reference", flush=True)
        baseline, baseline_info = load_packed_checkpoint(args.checkpoint)
        assert baseline.packed_load_placement is None
        assert not hasattr(baseline.generation_config, "text_device")
        assert getattr(baseline, "packed_text_placement", None) is None
        assert baseline.packed_weights.stats()["cpu_reference_expert_decodes"] == 0
        report["default_loading_info"] = normalized(baseline_info)
        baseline_stats = baseline.packed_weights.stats()
        print("Recording default tensor byte fingerprints and releasing the model", flush=True)
        expected = snapshot(baseline)
        del baseline
        gc.collect()
        report["stage"] = "direct_cpu_load"
        save()
        print("Loading ordinary tensors through the explicit CPU materialization map", flush=True)
        direct, direct_info = load_packed_checkpoint(args.checkpoint, text_device="cpu:0")
        report["direct_loading_info"] = normalized(direct_info)
        placement = direct.packed_load_placement
        assert placement["text_device"] == "cpu"
        assert placement["device_map"] == {
            "model.language_model": "cpu", "lm_head": "cpu",
            "logit_softcap": "cpu", "model.vision_tower": "cpu",
        }
        assert placement["outer_public_device_map_used"] is False
        assert placement["accelerate_dispatch_hooks_used"] is False
        assert placement["ordinary_parameters_verified_before_final_placement"] is True
        assert placement["persistent_buffers_verified_before_final_placement"] is True
        assert placement["parameters_relocated_by_final_placement"] is False
        assert direct.packed_text_placement["images_supported"] is False
        assert direct.packed_text_placement["direct_load"] == placement
        assert all(getattr(module, "_hf_hook", None) is None for module in direct.modules())
        assert getattr(direct, "hf_device_map", None) is None
        actual = snapshot(direct)
        for category, entries in expected.items():
            assert actual[category].keys() == entries.keys(), category
            for name, fingerprint in entries.items():
                assert actual[category][name] == fingerprint, (category, name)
        assert all(p.device.type == "cpu" and p.dtype == torch.float32 for p in direct.parameters())
        assert all(b.device.type == "cpu" for b in direct.buffers())
        assert all(matrix.weight.device.type == matrix.scales.device.type == "cpu"
                   for matrix in direct.packed_weights.matrices.values())
        direct_stats = direct.packed_weights.stats()
        for name in (
            "raw_storage_bytes", "source_storage_bytes", "nonexpert_parameter_bytes",
            "avoided_dense_expert_bytes", "largest_expert_bytes", "format_counts",
            "matrix_count", "dense_expert_parameters_were_meta",
            "removed_expert_dequantization_converters", "whole_expert_stack_dequantized",
        ):
            assert direct_stats[name] == baseline_stats[name], name
        for stats in (direct_stats, baseline_stats):
            assert stats["cpu_reference_expert_decodes"] == 0
            assert stats["resident_bytes"] == stats["peak_bytes"] == 0
            assert stats["devices"] == {}
            assert stats["whole_expert_stack_dequantized"] is False
            assert stats["dense_expert_parameters_were_meta"] is True
            assert stats["removed_expert_dequantization_converters"] == 2
        assert placement["ordinary_parameter_bytes_by_device"] == {
            "cpu": direct_stats["nonexpert_parameter_bytes"]
        }
        assert not any(".mlp.experts." in name for name in direct.state_dict())
        pointers = {name: p.data_ptr() for name, p in direct.named_parameters()}
        place_packed_text_model(direct, "cpu")
        assert direct.packed_text_placement["direct_load"] == placement
        assert all(p.data_ptr() == pointers[name] for name, p in direct.named_parameters())
        try:
            load_packed_checkpoint(args.checkpoint, text_device="meta")
        except ValueError as error:
            assert "meta placement is not allowed" in str(error)
        else:
            raise AssertionError("Meta target was not rejected")
        report.update(
            passed=True, stage="complete",
            ordinary_parameters_matched=len(actual["parameters"]),
            ordinary_buffers_matched=len(actual["buffers"]),
            packed_matrices_matched=len(actual["packed_matrices"]),
            direct_load_placement=placement,
            text_placement=direct.packed_text_placement,
            default_behavior_preserved=True,
            meta_target_rejected=True,
            packed_stats=direct_stats,
        )
    except BaseException as error:
        report.update(error=repr(error), traceback=traceback.format_exc())
        save()
        raise
    save()
    print(json.dumps(report), flush=True)


if __name__ == "__main__":
    main()
