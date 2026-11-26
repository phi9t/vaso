#!/usr/bin/env python3
"""CPU acceptance/inventory for text-only placement; execute inside Insula.

--inventory-only loads ordinary parameters and packed backing without any model
forward, image execution, dense reference, or expert expansion. This mode can
check a production checkpoint before admitting a GPU job.
"""
import argparse
from collections.abc import Mapping
import inspect
import json
from pathlib import Path
import resource
import time
import traceback

import torch
from transformers import Inkling2LoopForConditionalGeneration
from packed_checkpoint import (
    clear_packed_text_placement, load_packed_checkpoint, place_packed_text_model,
)


def tensor_leaves(value):
    if isinstance(value, torch.Tensor):
        return [value]
    if isinstance(value, Mapping):
        return [tensor for child in value.values() for tensor in tensor_leaves(child)]
    if isinstance(value, (tuple, list)):
        return [tensor for child in value for tensor in tensor_leaves(child)]
    return []


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--result", type=Path, required=True)
    parser.add_argument("--cache-budget-bytes", type=int, default=256 * 1024**2)
    parser.add_argument("--inventory-only", action="store_true")
    args = parser.parse_args()
    torch.set_num_threads(2)
    began = time.monotonic()
    report = {
        "passed": False, "stage": "loading", "device": "cpu",
        "inventory_only": args.inventory_only, "gpu_placement_tested": False,
        "activation_quantization": False,
    }

    def save():
        args.result.parent.mkdir(parents=True, exist_ok=True)
        report["seconds"] = time.monotonic() - began
        report["process_peak_rss_bytes"] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024
        args.result.write_text(json.dumps(report, indent=2) + "\n")

    def reject(call):
        try:
            call()
        except ValueError as error:
            assert "Text-only placement does not accept images" in str(error), str(error)
        else:
            raise AssertionError("Visual input was accepted in text-only mode")

    save()
    try:
        print("Loading native packed checkpoint for CPU text-placement inventory", flush=True)
        model, info = load_packed_checkpoint(
            args.checkpoint, cache_budget_bytes=args.cache_budget_bytes,
        )
        report["loading_info"] = {
            key: sorted(value) if isinstance(value, set) else value
            for key, value in info.items()
        }
        store = model.packed_weights
        pointers = {
            key: (matrix.weight.data_ptr(), matrix.scales.data_ptr(),
                  None if matrix.outer_scale is None else matrix.outer_scale.data_ptr())
            for key, matrix in store.matrices.items()
        }
        assert store.stats()["cpu_reference_expert_decodes"] == 0
        assert inspect.signature(type(model).forward) == inspect.signature(
            Inkling2LoopForConditionalGeneration.forward
        )
        assert inspect.signature(type(model).get_image_features) == inspect.signature(
            Inkling2LoopForConditionalGeneration.get_image_features
        )
        report["native_public_signatures_preserved"] = True
        before = None
        features_before = None
        if not args.inventory_only:
            print("Checking CPU text generation and original vision entry point", flush=True)
            tokens = torch.tensor([[1, 42, 111, 7]], dtype=torch.long)
            generation = dict(
                max_new_tokens=2, min_new_tokens=2, do_sample=False, use_cache=True,
                return_dict_in_generate=True, output_logits=True,
            )
            pixels = torch.linspace(-1, 1, 40 * 40 * 3).reshape(1, 40, 40, 3)
            grid = torch.tensor([[1, 1, 1]], dtype=torch.long)
            with torch.inference_mode():
                before = model.generate(input_ids=tokens, **generation)
                features_before = tensor_leaves(model.get_image_features(
                    pixel_values=pixels, image_grid_thw=grid,
                ))
            assert features_before and all(torch.isfinite(t).all() for t in features_before)

        report["stage"] = "placement"
        save()
        print("Selecting CPU text-only placement and verifying module boundaries", flush=True)
        assert place_packed_text_model(model, "cpu:0") is model
        placement = model.packed_text_placement.copy()
        assert placement["text_device"] == "cpu"
        assert placement["vision_device"] == "cpu"
        assert placement["images_supported"] is False
        assert placement["text_modules"] == [
            "model.language_model", "lm_head", "logit_softcap",
        ]
        assert placement["expert_cache_cleared"]
        stats = store.stats()
        assert placement["text_parameter_bytes"] + placement["vision_parameter_bytes"] == stats["nonexpert_parameter_bytes"]
        assert stats["resident_bytes"] == stats["peak_bytes"] == 0
        assert stats["devices"] == {}
        assert stats["whole_expert_stack_dequantized"] is False
        assert stats["dense_expert_parameters_were_meta"] is True
        assert stats["removed_expert_dequantization_converters"] == 2
        assert all(p.device.type == "cpu" and p.dtype == torch.float32 for p in model.parameters())
        assert all(b.device.type == "cpu" for b in model.buffers())
        assert pointers == {
            key: (matrix.weight.data_ptr(), matrix.scales.data_ptr(),
                  None if matrix.outer_scale is None else matrix.outer_scale.data_ptr())
            for key, matrix in store.matrices.items()
        }
        report.update(
            placement=placement,
            ordinary_parameter_count=sum(1 for _ in model.parameters()),
            ordinary_buffer_count=sum(1 for _ in model.buffers()),
            physical_layers=len(model.model.language_model.layers),
            logical_layers=model.config.text_config.total_num_hidden_layers,
            raw_backing_pointers_preserved=True,
        )
        try:
            place_packed_text_model(model, "meta")
        except ValueError as error:
            assert "meta placement is not allowed" in str(error)
            report["destructive_meta_placement_rejected"] = True
        else:
            raise AssertionError("Meta placement would discard ordinary parameters")

        if not args.inventory_only:
            with torch.inference_mode():
                after = model.generate(input_ids=tokens, **generation)
                assert torch.equal(after.sequences, before.sequences)
                torch.testing.assert_close(
                    torch.stack(after.logits), torch.stack(before.logits), atol=0, rtol=0,
                )
                assert torch.isfinite(torch.stack(after.logits)).all()
                reject(lambda: model(input_ids=tokens, pixel_values=pixels, image_grid_thw=grid))
                reject(lambda: model(tokens, pixels, grid))
                reject(lambda: model(input_ids=tokens, mm_encoder_outputs={"image": pixels}))
                reject(lambda: model.get_image_features(pixel_values=pixels, image_grid_thw=grid))
                reject(lambda: model.generate(
                    input_ids=tokens, pixel_values=pixels, image_grid_thw=grid, **generation,
                ))
                assert clear_packed_text_placement(model) is model
                assert model.packed_text_placement is None
                features_after = tensor_leaves(model.get_image_features(
                    pixel_values=pixels, image_grid_thw=grid,
                ))
                assert len(features_before) == len(features_after)
                for actual, expected in zip(features_after, features_before):
                    torch.testing.assert_close(actual, expected, atol=0, rtol=0)
            place_packed_text_model(model, "cpu")
            report.update(
                text_generation_tokens_exact=True, text_generation_logits_max_abs=0,
                visual_entry_points_rejected=5, cleared_mode_vision_features_exact=True,
                generated_ids=after.sequences[0, tokens.shape[1]:].tolist(),
            )
        else:
            assert store.stats()["cpu_reference_expert_decodes"] == 0
            report.update(
                model_forward_calls=0, vision_forward_calls=0, expert_decodes=0,
                dense_reference_loaded=False,
            )
        report["packed_stats"] = store.stats()
        report.update(passed=True, stage="complete")
    except BaseException as error:
        report.update(error=repr(error), traceback=traceback.format_exc())
        save()
        raise
    save()
    print(json.dumps(report), flush=True)


if __name__ == "__main__":
    main()
