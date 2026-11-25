#!/usr/bin/env python3
"""Acquire exact public Inkling integration-test assets; execute inside Insula."""
import argparse
import hashlib
import json
from pathlib import Path
import time
import urllib.request

REPO = "hf-internal-testing/tiny-inkling2-mini"
REVISION = "ee242a48af34ff0729518f4ac28cfcb847e470ab"
FILES = {
    "README.md": (606, None),
    "config.json": (1953, None),
    "generation_config.json": (98, None),
    "hf_quant_config.json": (30192, None),
    "model.safetensors": (268591712, "5ad20c18c32c25961ef13b7e999a8ce36d5460f51ddb4554dff5128ddf511b32"),
    "model.safetensors.index.json": (43848, None),
    "processor_config.json": (461, None),
    "special_tokens_map.json": (529, None),
    "tokenizer.json": (26178678, "7f332727f9a3f093dd452a39cc978e6757627e5dad1b0673aa4e75174f236dd6"),
    "tokenizer_config.json": (13235, None),
    "chat_template.jinja": (6868, None),
}
def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args=parser.parse_args()
    dest=args.output
    dest.mkdir(parents=True, exist_ok=True)
    manifest_path=dest/"acquisition.json"
    prior=json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
    if prior and (prior.get("repo"),prior.get("revision"))!=(REPO,REVISION):
        raise RuntimeError("Existing acquisition belongs to different source")
    done={item["name"]:item for item in prior.get("files",[])}
    for name,(expected_size,expected_sha) in FILES.items():
        final=dest/name
        if name in done and final.is_file() and final.stat().st_size==expected_size:
            print(json.dumps({"state":"reused","name":name}),flush=True)
            continue
        url=f"https://huggingface.co/{REPO}/resolve/{REVISION}/{name}"
        part=dest/(name+".part")
        for attempt in range(3):
            try:
                h=hashlib.sha256()
                size=0
                request=urllib.request.Request(url,headers={"User-Agent":"vaso-insula-inkling-e2e"})
                with urllib.request.urlopen(request,timeout=60) as response, part.open("wb") as out:
                    while chunk:=response.read(1024*1024):
                        out.write(chunk);h.update(chunk);size+=len(chunk)
                if size!=expected_size:
                    raise RuntimeError(f"{name}: expected {expected_size} bytes, received {size}")
                digest=h.hexdigest()
                if expected_sha and digest!=expected_sha:
                    raise RuntimeError(f"{name}: SHA256 mismatch")
                part.replace(final)
                done[name]={"name":name,"bytes":size,"sha256":digest,"url":url}
                manifest={"repo":REPO,"revision":REVISION,"trained":False,"purpose":"published integration-test checkpoint; does not establish language quality","files":list(done.values()),"complete":len(done)==len(FILES)}
                tmp=dest/"acquisition.json.tmp"
                tmp.write_text(json.dumps(manifest,indent=2)+"\n")
                tmp.replace(manifest_path)
                print(json.dumps({"state":"downloaded","name":name,"bytes":size}),flush=True)
                break
            except Exception:
                if attempt==2: raise
                time.sleep(2*(attempt+1))
    print(json.dumps({"state":"complete","checkpoint":str(dest),"repo":REPO,"revision":REVISION}),flush=True)
if __name__=="__main__": main()
