#!/usr/bin/env python3
"""CPU-only checks for actual Inkling tokenizers, chat prefills and framed output.

No model or weight loader is imported. Supply --checkpoint once for each pinned
tokenizer to qualify. Run inside the inference capsule with CUDA devices omitted.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import time
import traceback

from inkling_chat import InklingChatTokens, parse_text_response, prepare_text_prompt


def probe_tokenizer(checkpoint: Path):
    from transformers import AutoTokenizer
    tokenizer = AutoTokenizer.from_pretrained(str(checkpoint), local_files_only=True)
    tokens = InklingChatTokens.from_tokenizer(tokenizer)
    prompt = "List the numbers from one through five, separated by commas."
    final, final_metadata = prepare_text_prompt(
        tokenizer, prompt, response_mode="final", return_tensors=None)
    native, native_metadata = prepare_text_prompt(
        tokenizer, prompt, response_mode="model", return_tensors=None)
    raw, raw_metadata = prepare_text_prompt(
        tokenizer, prompt, raw_prompt=True, return_tensors=None)
    final_ids = final["input_ids"]
    assert final_ids[-2:] == tokens.final_prefix
    assert final_metadata["final_channel_prefilled"]
    assert tokens.end_sampling not in final_ids[-2:]
    assert not native_metadata["final_channel_prefilled"]
    assert native["input_ids"][-2:] != tokens.final_prefix
    assert native == tokenizer.apply_chat_template(
        [{"role": "user", "content": prompt}], add_generation_prompt=True,
        reasoning_effort="none", tokenize=True, return_dict=True)
    assert raw == tokenizer(prompt, return_tensors=None)
    assert raw_metadata["response_mode"] == "raw"
    rendered = tokenizer.decode(final_ids, skip_special_tokens=False)
    assert "Thinking effort level: 0" in rendered
    assert rendered.endswith("<|message_model|><|content_text|>")

    text = tokenizer.encode("1, 2, 3, 4, 5", add_special_tokens=False)
    thought = tokenizer.encode("fixture", add_special_tokens=False)
    assert text and thought
    answer = tokenizer.decode(text, clean_up_tokenization_spaces=False)
    end = [tokens.end_message, tokens.end_sampling]
    thinking_prefix = [tokens.model, tokens.thinking]
    final_prefix = tokens.final_prefix
    cases = [
        ("prefilled_final_complete", "final", text + end, len(text) + 2,
         {"final_text": answer, "answer_complete": True, "analysis_present": False,
          "finish_reason": "eos", "protocol_errors": []}),
        ("prefilled_final_eos_closes_message", "final", text + [tokens.end_sampling], len(text) + 1,
         {"final_text": answer, "answer_complete": True, "end_message_seen": False}),
        ("native_analysis_then_final", "model",
         thinking_prefix + thought + [tokens.end_message] + final_prefix + text + end, 100,
         {"final_text": answer, "answer_complete": True, "analysis_present": True,
          "analysis_token_count": len(thought), "raw_text": "", "protocol_errors": []}),
        ("analysis_budget_cutoff", "model", thinking_prefix + thought, len(thought) + 2,
         {"final_text": "", "answer_complete": False, "analysis_present": True,
          "finish_reason": "length", "truncated": True, "turn_complete": False}),
        ("final_budget_cutoff", "final", text, len(text),
         {"final_text": answer, "answer_complete": False, "partial_final_answer": True,
          "finish_reason": "length", "truncated": True}),
        ("stopped_without_eos", "final", text, len(text) + 5,
         {"answer_complete": False, "finish_reason": "stopped", "turn_complete": False}),
        ("native_analysis_only_eos", "model", thinking_prefix + thought + end, 100,
         {"final_text": "", "answer_complete": False, "turn_complete": True,
          "analysis_present": True, "finish_reason": "eos"}),
        ("raw_unframed_cutoff", "raw", text, len(text),
         {"final_text": "", "raw_text": answer, "answer_complete": False,
          "analysis_present": False, "finish_reason": "length"}),
        ("raw_framed_analysis_is_not_raw_text", "raw", thinking_prefix + thought + end, 100,
         {"raw_text": "", "final_text": "", "analysis_present": True,
          "answer_complete": False}),
        ("tool_content_is_not_final", "model", [tokens.tool, tokens.text] + text + end, 100,
         {"final_text": "", "answer_complete": False, "other_content_token_count": len(text)}),
        ("unframed_native_text_is_not_final", "model", text + [tokens.end_sampling], 100,
         {"final_text": "", "answer_complete": False, "protocol_errors": ["unframed_content"]}),
        ("missing_channel_is_not_final", "model", [tokens.model] + text + end, 100,
         {"final_text": "", "answer_complete": False,
          "protocol_errors": ["unframed_content", "message_end_without_content_channel"]}),
        ("content_after_eos_rejected", "final", text + end + text, 100,
         {"final_text": answer, "answer_complete": False,
          "protocol_errors": ["non_padding_token_after_eos"]}),
        ("channel_switch_without_message_end_rejected", "final", text + [tokens.thinking] + thought + end,
         100, {"final_text": answer, "answer_complete": False, "analysis_present": True,
               "protocol_errors": ["content_channel_changed_before_message_ended"]}),
        ("role_switch_without_message_end_rejected", "final",
         text + [tokens.model, tokens.text] + text + end, 100,
         {"answer_complete": False,
          "protocol_errors": ["message_started_before_previous_message_ended"]}),
        ("trailing_user_frame_rejected", "final",
         text + [tokens.end_message, tokens.user, tokens.text] + text + end, 100,
         {"final_text": answer, "answer_complete": False,
          "protocol_errors": ["non_assistant_generated_role"]}),
        ("trailing_unfinished_header_rejected", "final",
         text + [tokens.end_message, tokens.model, tokens.end_sampling], 100,
         {"final_text": answer, "answer_complete": False,
          "protocol_errors": ["unfinished_message_header"]}),
        ("analysis_after_final_does_not_complete_final", "model",
         final_prefix + text + [tokens.end_message] + thinking_prefix + thought + end, 100,
         {"final_text": answer, "answer_complete": False, "analysis_present": True,
          "final_channel_complete": False}),
        ("replaced_unfinished_header_rejected", "model",
         [tokens.model, tokens.model, tokens.text] + text + [tokens.end_sampling], 100,
         {"answer_complete": False,
          "protocol_errors": ["message_started_before_previous_message_ended"]}),
        ("empty_final_is_not_an_answer", "final", end, 100,
         {"final_text": "", "answer_complete": False, "turn_complete": True}),
    ]
    if tokens.pad is not None and tokens.pad != tokens.end_sampling:
        cases.append(("padding_after_eos", "final", text + end + [tokens.pad, tokens.pad], 100,
                      {"answer_complete": True, "protocol_errors": []}))
    unsupported = next(token for token in tokens.special_ids if token not in {
        tokens.model, tokens.user, tokens.system, tokens.tool, tokens.text,
        tokens.thinking, tokens.end_message, tokens.end_sampling, tokens.pad,
    })
    cases.append(("unsupported_control_not_final_text", "final", text + [unsupported] + text + end, 100,
                  {"answer_complete": False, "protocol_errors": ["unsupported_control_token"]}))
    results = []
    for name, mode, ids, budget, expected in cases:
        actual = parse_text_response(tokenizer, ids, response_mode=mode, max_new_tokens=budget)
        for key, value in expected.items():
            assert actual[key] == value, f"{name}: {key}={actual[key]!r}, expected {value!r}"
        assert "analysis_text" not in actual
        results.append({"name": name, "passed": True, "response": actual})
    identity_path = checkpoint / "acquisition.json"
    identity = json.loads(identity_path.read_text()) if identity_path.is_file() else {}
    return {
        "tokenizer_source_identity": {key: identity.get(key) for key in ("repo", "revision")},
        "checkpoint": str(checkpoint), "passed": True, "cases": results,
        "final_prefix_token_ids": tokens.final_prefix,
        "sampling_eos_token_id": tokens.end_sampling,
        "end_message_token_id": tokens.end_message,
        "final_prompt": final_metadata,
        "native_prompt": native_metadata,
        "raw_prompt_unchanged": True,
        "effort_none_renders_zero": True,
        "continuation_prefill_verified": True,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkpoint", type=Path, action="append", required=True)
    parser.add_argument("--result", type=Path, required=True)
    args = parser.parse_args()
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
    started = time.monotonic()
    report = {"passed": False, "gpu_execution": False, "model_loaded": False,
              "checkpoints": []}
    try:
        for checkpoint in args.checkpoint:
            report["checkpoints"].append(probe_tokenizer(checkpoint))
            print(json.dumps({"event": "tokenizer_probe_passed", "checkpoint": str(checkpoint),
                              "cases": len(report["checkpoints"][-1]["cases"])}), flush=True)
        report["passed"] = True
    except BaseException as error:
        report.update(error=repr(error), traceback=traceback.format_exc())
        raise
    finally:
        report["elapsed_seconds"] = time.monotonic() - started
        args.result.parent.mkdir(parents=True, exist_ok=True)
        temporary = args.result.with_name(args.result.name + ".tmp")
        temporary.write_text(json.dumps(report, indent=2) + "\n")
        temporary.replace(args.result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
