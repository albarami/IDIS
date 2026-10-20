"""Request shapes for exact Anthropic model ids.

The table has two stored kinds of row. Legacy ids use ``legacy-sampling-v1``.
Candidate ids use ``candidate-5-5-v1`` and keep
``qualification_status`` ``CANDIDATE_NOT_QUALIFIED``. An unknown id is not a
row: resolution uses the legacy shape as a bound, not an admission.

``default_effort_known`` records the provider default. The builder never
substitutes it. A candidate id without an explicit effort is rejected.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Any, ClassVar

SHAPE_LEGACY_SAMPLING_V1 = "legacy-sampling-v1"
SHAPE_CANDIDATE_5_5_V1 = "candidate-5-5-v1"

LEGACY_SONNET_ID = "claude-sonnet-4-20250514"
LEGACY_OPUS_ID = "claude-opus-4-20250514"
CANDIDATE_SONNET_ID = "claude-sonnet-5-5"
CANDIDATE_OPUS_ID = "claude-opus-5-5"

SUPPORTED_EFFORTS = frozenset({"low", "medium", "high", "xhigh", "max"})
BETWEEN_TOOLS_EFFORTS = frozenset({"low", "medium", "high"})
SONNET_5_5_DEFAULT_EFFORT = "high"
OPUS_5_5_DEFAULT_EFFORT = "medium"

_THINKING_OMIT = "omit"
_THINKING_ADAPTIVE = "adaptive"
_THINKING_BETWEEN_TOOLS = "between_tools"
_THINKING_DISABLED = "disabled"
_THINKING_ENABLED = "enabled"


class ThinkingPolicy(StrEnum):
    """Default thinking field for one row when the caller does not set one."""

    OMIT = "OMIT"
    ADAPTIVE = "ADAPTIVE"


class QualificationStatus(StrEnum):
    """Admission state recorded on a capability row."""

    ADMITTED_V1 = "ADMITTED_V1"
    CANDIDATE_NOT_QUALIFIED = "CANDIDATE_NOT_QUALIFIED"
    UNKNOWN_ID_LEGACY_BOUND = "UNKNOWN_ID_LEGACY_BOUND"


class RequestShapeReason(StrEnum):
    """Closed reasons for ``LLM_REQUEST_SHAPE_REJECTED``."""

    EFFORT_REQUIRED = "effort_required"
    EFFORT_NOT_APPLICABLE = "effort_not_applicable"
    EFFORT_UNSUPPORTED = "effort_unsupported"
    THINKING_DISABLED = "thinking_disabled"
    THINKING_ENABLED = "thinking_enabled"
    THINKING_BETWEEN_TOOLS_NOT_ALLOWED = "thinking_between_tools_not_allowed"
    THINKING_UNSUPPORTED = "thinking_unsupported"
    ASSISTANT_PREFILL = "assistant_prefill"
    TOOL_CHOICE_FORBIDDEN = "tool_choice_forbidden"


class LlmRequestShapeRejected(RuntimeError):
    """The request shape was rejected before a provider call.

    The message is the stable code. ``reason`` is a closed-set string and
    does not include prompt text.
    """

    code: ClassVar[str] = "LLM_REQUEST_SHAPE_REJECTED"
    reason: str

    def __init__(self, reason: RequestShapeReason) -> None:
        self.reason = reason.value
        super().__init__(self.code)


@dataclass(frozen=True, slots=True)
class ModelCapability:
    """One exact model id and the request shape it selects.

    ``between_tools_allowed`` is the row's permission for
    ``thinking.type=between_tools``. That value is still only accepted at
    efforts low, medium, and high. ``forced_tool_choice_allowed`` records
    whether a forced tool choice is part of the shape. This builder rejects
    every ``tool_choice`` argument.
    """

    model_id: str
    shape_id: str
    supports_sampling_controls: bool
    supported_efforts: frozenset[str]
    default_effort_known: str | None
    thinking_policy: ThinkingPolicy
    between_tools_allowed: bool
    forced_tool_choice_allowed: bool
    qualification_status: QualificationStatus


def _legacy_row(model_id: str) -> ModelCapability:
    return ModelCapability(
        model_id=model_id,
        shape_id=SHAPE_LEGACY_SAMPLING_V1,
        supports_sampling_controls=True,
        supported_efforts=frozenset(),
        default_effort_known=None,
        thinking_policy=ThinkingPolicy.OMIT,
        between_tools_allowed=False,
        forced_tool_choice_allowed=False,
        qualification_status=QualificationStatus.ADMITTED_V1,
    )


def _candidate_row(
    model_id: str,
    *,
    default_effort_known: str,
    between_tools_allowed: bool,
) -> ModelCapability:
    return ModelCapability(
        model_id=model_id,
        shape_id=SHAPE_CANDIDATE_5_5_V1,
        supports_sampling_controls=False,
        supported_efforts=SUPPORTED_EFFORTS,
        default_effort_known=default_effort_known,
        thinking_policy=ThinkingPolicy.OMIT,
        between_tools_allowed=between_tools_allowed,
        forced_tool_choice_allowed=False,
        qualification_status=QualificationStatus.CANDIDATE_NOT_QUALIFIED,
    )


MODEL_CAPABILITIES: tuple[ModelCapability, ...] = (
    _legacy_row(LEGACY_SONNET_ID),
    _legacy_row(LEGACY_OPUS_ID),
    _candidate_row(
        CANDIDATE_SONNET_ID,
        default_effort_known=SONNET_5_5_DEFAULT_EFFORT,
        between_tools_allowed=True,
    ),
    _candidate_row(
        CANDIDATE_OPUS_ID,
        default_effort_known=OPUS_5_5_DEFAULT_EFFORT,
        between_tools_allowed=False,
    ),
)

_BY_ID: dict[str, ModelCapability] = {row.model_id: row for row in MODEL_CAPABILITIES}

_UNKNOWN_LEGACY_BOUND = ModelCapability(
    model_id="",
    shape_id=SHAPE_LEGACY_SAMPLING_V1,
    supports_sampling_controls=True,
    supported_efforts=frozenset(),
    default_effort_known=None,
    thinking_policy=ThinkingPolicy.OMIT,
    between_tools_allowed=False,
    forced_tool_choice_allowed=False,
    qualification_status=QualificationStatus.UNKNOWN_ID_LEGACY_BOUND,
)


def resolve_model_capability(model_id: str) -> ModelCapability:
    """Return the table row for an exact id, or the legacy-shape bound.

    An id that is not in the table keeps today's legacy request shape. That
    resolution is a bound, not an admission of the id.

    Args:
        model_id: Exact provider model id.

    Returns:
        The stored row, or a legacy-shaped row whose ``model_id`` is the
        requested id and whose status is ``UNKNOWN_ID_LEGACY_BOUND``.
    """
    row = _BY_ID.get(model_id)
    if row is not None:
        return row
    return replace(_UNKNOWN_LEGACY_BOUND, model_id=model_id)


def require_model_effort(*, model: str, effort: str | None) -> None:
    """Reject a model and effort pair that this table cannot send.

    Candidate ids require an effort in the row's supported set. Legacy ids
    and unknown ids reject any effort. The known provider default is not
    used to fill a missing effort.

    Args:
        model: Exact provider model id.
        effort: Caller effort, or None when the caller did not set one.

    Raises:
        LlmRequestShapeRejected: The pair is not sendable. The message is
            the stable code.
    """
    _require_effort(resolve_model_capability(model), effort)


def build_request(
    *,
    model: str,
    max_tokens: int,
    system: str,
    messages: Sequence[Mapping[str, Any]],
    effort: str | None = None,
    thinking: object = None,
    tool_choice: object | None = None,
) -> dict[str, Any]:
    """Build keyword arguments for ``messages.create``.

    Legacy and unknown ids send ``temperature=0`` and ``system`` even when
    it is empty. Candidate ids send ``output_config.effort``, omit sampling
    parameters, and omit an empty ``system``. Thinking is omitted unless the
    caller selects adaptive, or between_tools where the row allows it.

    Args:
        model: Exact provider model id.
        max_tokens: Output-token ceiling, including thinking tokens.
        system: System text. An empty string is kept for the legacy shape.
        messages: Conversation turns. A final assistant turn is prefill.
        effort: Explicit effort. Required for a candidate id.
        thinking: Omit, adaptive, or between_tools. Disabled and enabled
            are rejected.
        tool_choice: Rejected for every row. Pass None to leave it unset.

    Returns:
        Keyword arguments for one ``messages.create`` call.

    Raises:
        LlmRequestShapeRejected: The shape is not sendable. The message is
            the stable code and does not include prompt text.
    """
    if tool_choice is not None:
        raise LlmRequestShapeRejected(RequestShapeReason.TOOL_CHOICE_FORBIDDEN)
    _reject_trailing_assistant(messages)
    row = resolve_model_capability(model)
    accepted_effort = _require_effort(row, effort)
    thinking_value = _thinking_value(row, accepted_effort, thinking)
    if row.shape_id == SHAPE_LEGACY_SAMPLING_V1:
        return {
            "model": model,
            "max_tokens": max_tokens,
            "temperature": 0,
            "system": system,
            "messages": messages,
        }
    if accepted_effort is None:
        raise LlmRequestShapeRejected(RequestShapeReason.EFFORT_REQUIRED)
    request: dict[str, Any] = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": messages,
        "output_config": {"effort": accepted_effort},
    }
    if system != "":
        request["system"] = system
    if thinking_value is not None:
        request["thinking"] = thinking_value
    return request


def _require_effort(row: ModelCapability, effort: str | None) -> str | None:
    if row.shape_id == SHAPE_LEGACY_SAMPLING_V1:
        if effort is not None:
            raise LlmRequestShapeRejected(RequestShapeReason.EFFORT_NOT_APPLICABLE)
        return None
    if effort is None:
        raise LlmRequestShapeRejected(RequestShapeReason.EFFORT_REQUIRED)
    if effort not in row.supported_efforts:
        raise LlmRequestShapeRejected(RequestShapeReason.EFFORT_UNSUPPORTED)
    return effort


def _reject_trailing_assistant(messages: Sequence[Mapping[str, Any]]) -> None:
    if len(messages) == 0:
        return
    last = messages[-1]
    if isinstance(last, Mapping) and last.get("role") == "assistant":
        raise LlmRequestShapeRejected(RequestShapeReason.ASSISTANT_PREFILL)


def _thinking_value(
    row: ModelCapability, effort: str | None, thinking: object
) -> dict[str, str] | None:
    mode = _thinking_mode(thinking)
    if mode == _THINKING_DISABLED:
        raise LlmRequestShapeRejected(RequestShapeReason.THINKING_DISABLED)
    if mode == _THINKING_ENABLED:
        raise LlmRequestShapeRejected(RequestShapeReason.THINKING_ENABLED)
    if mode == _THINKING_OMIT:
        if row.thinking_policy is ThinkingPolicy.ADAPTIVE:
            return {"type": _THINKING_ADAPTIVE}
        return None
    if mode == _THINKING_BETWEEN_TOOLS:
        if not row.between_tools_allowed or effort not in BETWEEN_TOOLS_EFFORTS:
            raise LlmRequestShapeRejected(RequestShapeReason.THINKING_BETWEEN_TOOLS_NOT_ALLOWED)
        return {"type": _THINKING_BETWEEN_TOOLS}
    if mode == _THINKING_ADAPTIVE and row.shape_id == SHAPE_CANDIDATE_5_5_V1:
        return {"type": _THINKING_ADAPTIVE}
    raise LlmRequestShapeRejected(RequestShapeReason.THINKING_UNSUPPORTED)


def _thinking_mode(thinking: object) -> str:
    if thinking is None or thinking == _THINKING_OMIT:
        return _THINKING_OMIT
    if isinstance(thinking, str) and thinking in {
        _THINKING_DISABLED,
        _THINKING_ENABLED,
        _THINKING_BETWEEN_TOOLS,
        _THINKING_ADAPTIVE,
    }:
        return thinking
    if not isinstance(thinking, Mapping):
        return "unsupported"
    kind = thinking.get("type")
    if kind == _THINKING_DISABLED:
        return _THINKING_DISABLED
    if kind == _THINKING_ENABLED:
        return _THINKING_ENABLED
    if (
        isinstance(kind, str)
        and kind in {_THINKING_BETWEEN_TOOLS, _THINKING_ADAPTIVE}
        and set(thinking) == {"type"}
    ):
        return kind
    return "unsupported"
