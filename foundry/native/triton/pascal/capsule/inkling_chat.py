"""Inkling chat prefills and channel-aware completion parsing.

The checkpoint's template supports reasoning_effort but does not append an
assistant prefix for add_generation_prompt. Continue an empty assistant content
message to request final text. Parse special-token framing before decoding so
analysis is never mistaken for a final answer.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class InklingChatTokens:
    model: int
    user: int
    system: int
    tool: int
    text: int
    thinking: int
    end_message: int
    end_sampling: int
    pad: int | None
    special_ids: frozenset[int]

    @classmethod
    def from_tokenizer(cls, tokenizer: Any) -> "InklingChatTokens":
        names = {
            "model": "<|message_model|>",
            "user": "<|message_user|>",
            "system": "<|message_system|>",
            "tool": "<|message_tool|>",
            "text": "<|content_text|>",
            "thinking": "<|content_thinking|>",
            "end_message": "<|end_message|>",
            "end_sampling": "<|content_model_end_sampling|>",
        }
        special = frozenset(int(value) for value in tokenizer.all_special_ids)
        values = {}
        for name, spelling in names.items():
            value = tokenizer.convert_tokens_to_ids(spelling)
            if (not isinstance(value, int) or value not in special
                    or tokenizer.encode(spelling, add_special_tokens=False) != [value]):
                raise ValueError(f"Tokenizer lacks the atomic Inkling token {spelling}")
            values[name] = value
        if len(set(values.values())) != len(values):
            raise ValueError("Inkling framing tokens must have distinct IDs")
        if tokenizer.eos_token_id != values["end_sampling"]:
            raise ValueError("Inkling sampling terminator must be the tokenizer EOS")
        return cls(**values, pad=tokenizer.pad_token_id, special_ids=special)

    @property
    def final_prefix(self) -> list[int]:
        return [self.model, self.text]


def _single_input_ids(inputs: Any) -> list[int]:
    value = inputs["input_ids"]
    if hasattr(value, "tolist"):
        value = value.tolist()
    if value and isinstance(value[0], list):
        if len(value) != 1:
            raise ValueError("The text prompt helper expects one prompt")
        value = value[0]
    return [int(token) for token in value]


def prepare_text_prompt(tokenizer: Any, prompt: str, *, response_mode: str = "final",
                        raw_prompt: bool = False, return_tensors: str | None = "pt"):
    """Return tokenizer inputs and the exact initial response-channel contract."""
    if response_mode not in ("final", "model"):
        raise ValueError("response_mode must be final or model")
    framing = InklingChatTokens.from_tokenizer(tokenizer)
    if raw_prompt:
        inputs = tokenizer(prompt, return_tensors=return_tensors)
    else:
        messages = [{"role": "user", "content": prompt}]
        if response_mode == "final":
            messages.append({"role": "assistant", "content": ""})
        inputs = tokenizer.apply_chat_template(
            messages, continue_final_message=response_mode == "final",
            add_generation_prompt=response_mode == "model", reasoning_effort="none",
            tokenize=True, return_dict=True, return_tensors=return_tensors,
        )
    ids = _single_input_ids(inputs)
    prefilled = not raw_prompt and response_mode == "final"
    if prefilled and ids[-2:] != framing.final_prefix:
        raise ValueError("Chat template did not produce an open assistant final-text prefix")
    return inputs, {
        "response_mode": "raw" if raw_prompt else response_mode,
        "final_channel_prefilled": prefilled,
        "prompt_tokens": len(ids),
        "prompt_suffix_token_ids": ids[-2:],
        "reasoning_effort": None if raw_prompt else "none",
    }


def parse_text_response(tokenizer: Any, generated_ids: list[int], *,
                        response_mode: str, max_new_tokens: int) -> dict[str, Any]:
    """Report final content and termination without returning analysis text.

    In final mode the assistant/text header is already in the prompt. Model mode
    requires generated framing. Raw mode preserves unframed text separately and
    does not label it a final-channel answer. EOS, rather than end_message, closes
    an assistant turn. A token-budget cutoff is always incomplete.
    """
    if response_mode not in ("final", "model", "raw"):
        raise ValueError("response_mode must be final, model or raw")
    if max_new_tokens <= 0:
        raise ValueError("max_new_tokens must be positive")
    tokens = InklingChatTokens.from_tokenizer(tokenizer)
    role = "assistant" if response_mode == "final" else None
    channel = "final" if response_mode == "final" else "raw" if response_mode == "raw" else None
    roles = {tokens.model: "assistant", tokens.user: "user",
             tokens.system: "system", tokens.tool: "tool"}
    final_segments: list[list[int]] = []
    raw_segments: list[list[int]] = []
    segment: list[int] = []
    analysis_present = False
    analysis_tokens = 0
    other_tokens = 0
    end_message_seen = False
    eos_seen = False
    eos_closes_final = False
    last_completed_channel = None
    errors: list[str] = []

    def fail(reason: str) -> None:
        if reason not in errors:
            errors.append(reason)

    def flush() -> None:
        nonlocal segment
        if segment and role == "assistant" and channel == "final":
            final_segments.append(segment)
        elif segment and channel == "raw":
            raw_segments.append(segment)
        segment = []

    for raw_token in generated_ids:
        token = int(raw_token)
        if eos_seen:
            if tokens.pad is None or token != tokens.pad:
                fail("non_padding_token_after_eos")
            continue
        if token == tokens.end_sampling:
            if role is not None and channel is None:
                fail("unfinished_message_header")
            ending_channel = channel if role == "assistant" else (
                last_completed_channel if role is None and channel is None else None)
            eos_closes_final = ending_channel == "final"
            flush()
            eos_seen = True
            continue
        if token == tokens.end_message:
            if channel is None:
                fail("message_end_without_content_channel")
            last_completed_channel = channel if role == "assistant" else None
            flush()
            end_message_seen = True
            role, channel = None, None
            continue
        if token in roles:
            if role is not None or channel not in (None, "raw"):
                fail("message_started_before_previous_message_ended")
            flush()
            role, channel = roles[token], None
            if role != "assistant":
                fail("non_assistant_generated_role")
            continue
        if token in (tokens.text, tokens.thinking):
            if channel is not None:
                fail("content_channel_changed_before_message_ended")
            flush()
            if role is None:
                fail("content_channel_without_role")
                channel = "other"
            elif token == tokens.thinking:
                channel = "analysis" if role == "assistant" else "other"
                analysis_present |= role == "assistant"
            else:
                channel = "final" if role == "assistant" else "other"
            continue
        if token in tokens.special_ids:
            flush()
            channel = "other"
            fail("unsupported_control_token")
            continue
        if channel == "analysis":
            analysis_tokens += 1
        elif (role == "assistant" and channel == "final") or channel == "raw":
            segment.append(token)
        else:
            other_tokens += 1
            if role is None or channel is None:
                fail("unframed_content")
    flush()
    decode = lambda ids: tokenizer.decode(ids, skip_special_tokens=False,
                                         clean_up_tokenization_spaces=False)
    final_messages = [decode(ids) for ids in final_segments]
    final_text = "".join(final_messages)
    present = bool(final_text.strip())
    turn_complete = eos_seen and not errors
    finish_reason = ("eos" if eos_seen else
                     "length" if len(generated_ids) >= max_new_tokens else "stopped")
    return {
        "final_text": final_text,
        "final_messages": final_messages,
        "final_answer_present": present,
        "answer_complete": present and turn_complete and eos_closes_final,
        "final_channel_complete": turn_complete and eos_closes_final,
        "partial_final_answer": present and not (turn_complete and eos_closes_final),
        "analysis_present": analysis_present,
        "analysis_token_count": analysis_tokens,
        "other_content_token_count": other_tokens,
        "raw_text": "".join(decode(ids) for ids in raw_segments),
        "response_mode": response_mode,
        "finish_reason": finish_reason,
        "turn_complete": turn_complete,
        "eos_seen": eos_seen,
        "end_message_seen": end_message_seen,
        "truncated": not eos_seen and len(generated_ids) >= max_new_tokens,
        "generated_token_count": len(generated_ids),
        "protocol_errors": errors,
    }
