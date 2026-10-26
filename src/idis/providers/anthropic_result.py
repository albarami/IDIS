"""Typed parse of one Anthropic message.

Blocks are selected by type. Thinking text is counted and never stored. Only
end_turn and stop_sequence with exactly one text block succeed.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from idis.providers.anthropic_failure import (
    LlmResponseAmbiguous,
    LlmResponseContextExceeded,
    LlmResponseEmpty,
    LlmResponseIncomplete,
    LlmResponseRefused,
    LlmResponseTruncated,
    LlmResponseUnexpectedBlock,
)

_STOP_REFUSAL = "refusal"
_STOP_MAX_TOKENS = "max_tokens"
_STOP_CONTEXT_EXCEEDED = "model_context_window_exceeded"
_ACCEPTED_STOP_REASONS = frozenset({"end_turn", "stop_sequence"})
_BLOCK_THINKING = "thinking"
_BLOCK_REDACTED_THINKING = "redacted_thinking"
_BLOCK_TEXT = "text"


@dataclass(frozen=True, slots=True)
class LlmUsage:
    """Token usage copied from one provider response. Absent counts are zero."""

    input_tokens: int
    output_tokens: int
    cache_creation_input_tokens: int
    cache_read_input_tokens: int
    cache_creation_5m_input_tokens: int
    cache_creation_1h_input_tokens: int
    thinking_tokens: int
    service_tier: str | None
    inference_geo: str | None


@dataclass(frozen=True, slots=True)
class LlmResult:
    """One clean text completion and the usage observed for that call."""

    text: str
    stop_reason: str
    usage: LlmUsage
    request_id: str | None
    model: str | None
    thinking_block_count: int
    redacted_thinking_block_count: int


def parse_message(message: object) -> LlmResult:
    """Parse one provider message into a clean text completion.

    Refusal is decided before content, because a refusal has empty content.
    Other non-success stop reasons are next. Block checks run only for
    end_turn and stop_sequence.

    Args:
        message: A duck-typed provider message. Blocks are read by ``type``.

    Returns:
        The single text block plus stop reason, usage, request id, and model.

    Raises:
        LlmProviderError: The message is a refusal, truncation, incomplete
            stop, or not exactly one text block. Thinking text is not copied
            into the error.
    """
    request_id = _request_id(message)
    stop_reason = getattr(message, "stop_reason", None)
    if stop_reason == _STOP_REFUSAL:
        raise _refusal(message, request_id) from None
    if stop_reason == _STOP_MAX_TOKENS:
        raise LlmResponseTruncated(request_id=request_id) from None
    if stop_reason == _STOP_CONTEXT_EXCEEDED:
        raise LlmResponseContextExceeded(request_id=request_id) from None
    if not isinstance(stop_reason, str) or stop_reason not in _ACCEPTED_STOP_REASONS:
        raise LlmResponseIncomplete(request_id=request_id) from None
    return _accepted_message(message, request_id=request_id, stop_reason=stop_reason)


def _accepted_message(message: object, *, request_id: str | None, stop_reason: str) -> LlmResult:
    thinking_block_count = 0
    redacted_thinking_block_count = 0
    texts: list[str] = []
    for block in _blocks(message, request_id=request_id):
        block_type = getattr(block, "type", None)
        if block_type == _BLOCK_THINKING:
            thinking_block_count += 1
            continue
        if block_type == _BLOCK_REDACTED_THINKING:
            redacted_thinking_block_count += 1
            continue
        if block_type == _BLOCK_TEXT:
            texts.append(_text(block, request_id=request_id))
            continue
        raise LlmResponseUnexpectedBlock(request_id=request_id) from None
    if not texts:
        raise LlmResponseEmpty(request_id=request_id) from None
    if len(texts) > 1:
        raise LlmResponseAmbiguous(request_id=request_id) from None
    return LlmResult(
        text=texts[0],
        stop_reason=stop_reason,
        usage=_usage(getattr(message, "usage", None), request_id=request_id),
        request_id=request_id,
        model=_model(message),
        thinking_block_count=thinking_block_count,
        redacted_thinking_block_count=redacted_thinking_block_count,
    )


def _refusal(message: object, request_id: str | None) -> LlmResponseRefused:
    details = getattr(message, "stop_details", None)
    return LlmResponseRefused(
        request_id=request_id,
        category=_category(details),
        recommended_model=_recommended_model(details),
    )


def _blocks(message: object, *, request_id: str | None) -> list[object]:
    content = getattr(message, "content", None)
    if content is None:
        return []
    if isinstance(content, str) or not isinstance(content, Iterable):
        raise LlmResponseUnexpectedBlock(request_id=request_id) from None
    return list(content)


def _text(block: object, *, request_id: str | None) -> str:
    text = getattr(block, "text", None)
    if not isinstance(text, str):
        raise LlmResponseUnexpectedBlock(request_id=request_id) from None
    return text


def _usage(usage: object, *, request_id: str | None) -> LlmUsage:
    cache_creation = None if usage is None else getattr(usage, "cache_creation", None)
    output_details = None if usage is None else getattr(usage, "output_tokens_details", None)
    return LlmUsage(
        input_tokens=_token_count(usage, "input_tokens", request_id=request_id),
        output_tokens=_token_count(usage, "output_tokens", request_id=request_id),
        cache_creation_input_tokens=_token_count(
            usage, "cache_creation_input_tokens", request_id=request_id
        ),
        cache_read_input_tokens=_token_count(
            usage, "cache_read_input_tokens", request_id=request_id
        ),
        cache_creation_5m_input_tokens=_token_count(
            cache_creation, "ephemeral_5m_input_tokens", request_id=request_id
        ),
        cache_creation_1h_input_tokens=_token_count(
            cache_creation, "ephemeral_1h_input_tokens", request_id=request_id
        ),
        thinking_tokens=_token_count(output_details, "thinking_tokens", request_id=request_id),
        service_tier=_optional_str(usage, "service_tier", request_id=request_id),
        inference_geo=_optional_str(usage, "inference_geo", request_id=request_id),
    )


def _token_count(source: object, name: str, *, request_id: str | None) -> int:
    if source is None:
        return 0
    value = getattr(source, name, None)
    if value is None:
        return 0
    if isinstance(value, bool) or not isinstance(value, int):
        raise LlmResponseIncomplete(request_id=request_id) from None
    return value


def _optional_str(source: object, name: str, *, request_id: str | None) -> str | None:
    if source is None:
        return None
    value = getattr(source, name, None)
    if value is None:
        return None
    if isinstance(value, str):
        return value
    raise LlmResponseIncomplete(request_id=request_id) from None


def _request_id(message: object) -> str | None:
    value = getattr(message, "_request_id", None)
    if isinstance(value, str):
        return value
    return None


def _model(message: object) -> str | None:
    value = getattr(message, "model", None)
    if isinstance(value, str):
        return value
    return None


def _category(details: object) -> str | None:
    if details is None:
        return None
    value = getattr(details, "category", None)
    if isinstance(value, str):
        return value
    return None


def _recommended_model(details: object) -> str | None:
    if details is None:
        return None
    direct = getattr(details, "recommended_model", None)
    if isinstance(direct, str):
        return direct
    extra = getattr(details, "model_extra", None)
    if isinstance(extra, dict):
        value = extra.get("recommended_model")
        if isinstance(value, str):
            return value
    return None
