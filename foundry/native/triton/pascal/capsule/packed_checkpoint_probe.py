#!/usr/bin/env python3
"""Actual-checkpoint CPU acceptance for packed loading; run inside Insula."""
import argparse
import json
from pathlib import Path
import time
import traceback

import torch
from transformers import Inkling2LoopForConditionalGeneration
from packed_checkpoint import PackedMatrix, PackedWeightStore, load_packed_checkpoint


def main():
    p=argparse.ArgumentParser()
    p.add_argument("--checkpoint",type=Path,required=True)
    p.add_argument("--result",type=Path,required=True)
    args=p.parse_args()
    torch.set_num_threads(2)
    began=time.monotonic()
    report={"passed":False,"stage":"load_packed"}
    def save():
        args.result.parent.mkdir(parents=True,exist_ok=True)
        args.result.write_text(json.dumps(report,indent=2)+"\n")
    save()
    try:
        reserved = PackedWeightStore(1024)
        reserved.add(PackedMatrix(0,"w13","mxfp8",torch.full((1,1,32),0x38,dtype=torch.uint8),
                                  torch.full((1,1,1),255,dtype=torch.uint8),None,32,1,33))
        assert torch.isnan(reserved.decode_one_expert(0,"w13",0)).all()
        report["reserved_mxfp8_scale_is_nan"]=True
        print("Loading packed model on CPU",flush=True)
        packed,info=load_packed_checkpoint(args.checkpoint,cache_budget_bytes=64*1024**2)
        stats=packed.packed_weights.stats()
        assert stats["whole_expert_stack_dequantized"] is False
        assert stats["dense_expert_parameters_were_meta"] is True
        assert stats["removed_expert_dequantization_converters"]==2
        assert stats["resident_bytes"]==0 and stats["peak_bytes"]==0 and stats["devices"]=={}
        assert stats["format_counts"]["nvfp4"]>0 and stats["format_counts"]["mxfp8"]>0
        assert stats["raw_storage_bytes"]<stats["avoided_dense_expert_bytes"]
        assert not any(".mlp.experts." in name for name in packed.state_dict())
        pointers={key:matrix.weight.data_ptr() for key,matrix in packed.packed_weights.matrices.items()}
        packed.to("cpu")
        assert all(matrix.weight.data_ptr()==pointers[key] and matrix.weight.device.type=="cpu"
                   for key,matrix in packed.packed_weights.matrices.items())
        report["packed_initial"]=stats
        report["stage"]="load_dense_reference";save()
        print("Loading independent dense FP32 reference on CPU",flush=True)
        reference=Inkling2LoopForConditionalGeneration.from_pretrained(
            str(args.checkpoint),dtype=torch.float32,attn_implementation="eager",
            local_files_only=True).eval()
        ordinary=0
        for name,value in packed.named_parameters():
            torch.testing.assert_close(value,reference.get_parameter(name),atol=0,rtol=0)
            ordinary+=1
        checked=0
        for layer,projection in packed.packed_weights.matrices:
            matrix=packed.packed_weights.matrices[(layer,projection)]
            original=reference.model.language_model.layers[layer].mlp.experts
            source=original.gate_up_proj if projection=="w13" else original.down_proj
            for expert in (0,matrix.num_experts-1):
                value=packed.packed_weights.decode_one_expert(layer,projection,expert)
                if projection=="w13":
                    value=torch.cat((value[0::2],value[1::2]),dim=0)
                torch.testing.assert_close(value,source[expert],atol=0,rtol=0)
                checked+=1
                del value
        report.update(ordinary_parameters_exact=ordinary,expert_matrices_exact=checked)
        report["stage"]="cpu_inference";save()
        print("Comparing actual-model CPU prefill and cached decoding",flush=True)
        tokens=torch.tensor([[1,42,111,7,42,8]],dtype=torch.long)
        with torch.inference_mode():
            expected=reference(tokens,use_cache=False).logits
            actual=packed(tokens,use_cache=False).logits
            torch.testing.assert_close(actual,expected,atol=3e-4,rtol=1e-4)
            report["full_logits_max_abs"]=float((actual-expected).abs().max())
            pre=packed(tokens[:,:3],use_cache=True)
            chunks=[pre.logits]
            cache=pre.cache
            for index in range(3,tokens.shape[1]):
                step=packed(tokens[:,index:index+1],cache=cache,use_cache=True)
                cache=step.cache
                chunks.append(step.logits)
            increment=torch.cat(chunks,dim=1)
            torch.testing.assert_close(increment,expected,atol=5e-4,rtol=1e-4)
            report["cached_logits_max_abs"]=float((increment-expected).abs().max())
            report["cache_layers"]=len(cache.layers)
        largest=max(packed.packed_weights.matrices.values(),key=lambda m:m.expert_bytes)
        undersized=PackedWeightStore(largest.expert_bytes-1)
        try:
            undersized.add(largest)
        except ValueError as error:
            assert "requires" in str(error) and "budget" in str(error)
            report["oversized_expert_rejected"]=True
        else:raise AssertionError("Oversized expert was accepted")
        assert undersized.stats()["resident_bytes"]==0
        packed.packed_weights.clear_cache()
        report["packed_final"]=packed.packed_weights.stats()
        assert report["packed_final"]["resident_bytes"]==0
        assert report["packed_final"]["devices"]=={}
        report.update(passed=True,stage="complete",seconds=time.monotonic()-began)
    except BaseException as error:
        report.update(error=repr(error),traceback=traceback.format_exc(),seconds=time.monotonic()-began)
        save()
        raise
    save()
    print(json.dumps(report),flush=True)

if __name__=="__main__":main()
