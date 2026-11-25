#!/usr/bin/env python3
"""Load the published tiny checkpoint and optionally generate on CPU, offline."""
import argparse
import json
import os
from pathlib import Path
import time

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--checkpoint",type=Path,required=True)
    parser.add_argument("--result",type=Path,required=True)
    parser.add_argument("--new-tokens",type=int,default=0)
    args=parser.parse_args()
    os.environ["HF_HUB_OFFLINE"]="1"
    os.environ["TRANSFORMERS_OFFLINE"]="1"
    import torch
    import transformers
    from transformers import AutoTokenizer, Inkling2LoopForConditionalGeneration
    torch.set_num_threads(2)
    started=time.monotonic()
    receipt=json.loads((args.checkpoint/"acquisition.json").read_text())
    assert receipt["complete"] and receipt["revision"]=="ee242a48af34ff0729518f4ac28cfcb847e470ab"
    tokenizer=AutoTokenizer.from_pretrained(str(args.checkpoint),local_files_only=True)
    model,loading=Inkling2LoopForConditionalGeneration.from_pretrained(
        str(args.checkpoint),dtype=torch.float32,attn_implementation="eager",
        local_files_only=True,output_loading_info=True,
    )
    model.eval()
    # Complete original-format checkpoint mapping should not silently skip weights.
    loading = {key: sorted(value) if isinstance(value, set) else value for key, value in loading.items()}
    for field in ("missing_keys","unexpected_keys","mismatched_keys","error_msgs"):
        assert not loading.get(field), (field,loading.get(field))
    inputs=tokenizer.apply_chat_template(
        [{"role":"user","content":"Explain in one sentence why the sky is usually blue."}],
        add_generation_prompt=True,tokenize=True,return_dict=True,return_tensors="pt",
        reasoning_effort="none",
    )
    params=sum(p.numel() for p in model.parameters())
    result={
        "state":"loaded","checkpoint":receipt["repo"],"revision":receipt["revision"],
        "trained":False,"torch":torch.__version__,"transformers":transformers.__version__,
        "dtype":str(model.dtype),"parameter_count":params,
        "parameter_bytes":sum(p.numel()*p.element_size() for p in model.parameters()),
        "physical_layers":model.config.text_config.num_hidden_layers,
        "logical_layers":model.config.text_config.total_num_hidden_layers,
        "prompt_tokens":inputs["input_ids"][0].tolist(),
        "loading_info":loading,
        "load_seconds":time.monotonic()-started,
    }
    if args.new_tokens:
        with torch.inference_mode():
            start=time.monotonic()
            output=model.generate(**inputs,max_new_tokens=args.new_tokens,do_sample=False,
                use_cache=True,return_dict_in_generate=True,output_logits=True)
        result.update(
            state="generated",
            generated_tokens=output.sequences[0,inputs["input_ids"].shape[1]:].tolist(),
            generated_text=tokenizer.decode(output.sequences[0,inputs["input_ids"].shape[1]:],skip_special_tokens=False),
            first_logits=output.logits[0][0,:5].float().tolist(),
            all_logits_finite=all(bool(torch.isfinite(x).all()) for x in output.logits),
            generation_seconds=time.monotonic()-start,
        )
        assert result["all_logits_finite"]
    args.result.parent.mkdir(parents=True,exist_ok=True)
    args.result.write_text(json.dumps(result,indent=2)+"\n")
    print(json.dumps(result),flush=True)
if __name__=="__main__": main()
