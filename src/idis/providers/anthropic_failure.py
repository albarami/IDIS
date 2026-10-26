"""Typed Anthropic failures and the unknown-outcome retry classifier.

Every error message is the stable code. A status-bearing failure appends the
numeric HTTP status and nothing from the provider body, the prompt, or a refusal
explanation.
"""

from __future__ import annotations

from enum import StrEnum
from typing import ClassVar

REPLAYABLE_HTTP_STATUS = 429
CONFIRMED_NOT_ACCEPTED_HTTP_STATUSES = frozenset({400, 401, 402, 403, 404, 409, 413, 422})
REFUSAL_CATEGORIES = frozenset(
    {"cyber", "bio", "frontier_llm", "reasoning_extraction", "general_harms"}
)


class HttpAdmission(StrEnum):
    """Whether one HTTP result may be sent again."""

    REPLAYABLE = "REPLAYABLE"
    CONFIRMED_NOT_ACCEPTED = "CONFIRMED_NOT_ACCEPTED"
    UNKNOWN = "UNKNOWN"


def classify_http_status(status_code: int | None) -> HttpAdmission:
    """Classify an HTTP status, or no response, for the retry policy.

    Args:
        status_code: The provider HTTP status, or None when no response arrived.

    Returns:
        REPLAYABLE for HTTP 429 only. The confirmed non-acceptance statuses are
        not replayed. Every other status, including no response, is UNKNOWN.
    """
    if status_code is None:
        return HttpAdmission.UNKNOWN
    if status_code == REPLAYABLE_HTTP_STATUS:
        return HttpAdmission.REPLAYABLE
    if status_code in CONFIRMED_NOT_ACCEPTED_HTTP_STATUSES:
        return HttpAdmission.CONFIRMED_NOT_ACCEPTED
    return HttpAdmission.UNKNOWN


class LlmProviderError(RuntimeError):
    """Safe provider failure whose message never carries provider text."""

    code: ClassVar[str]
    request_id: str | None
    status_code: int | None

    def __init__(self, *, request_id: str | None = None, status_code: int | None = None) -> None:
        self.request_id = request_id
        self.status_code = status_code
        if status_code is None:
            super().__init__(self.code)
            return
        super().__init__(f"{self.code} {status_code}")


class LlmProviderOutcomeUnknown(LlmProviderError):
    """The provider may have accepted the request. It is not replayed."""

    code: ClassVar[str] = "LLM_PROVIDER_OUTCOME_UNKNOWN"


class LlmProviderRequestRejected(LlmProviderError):
    """The provider confirmed that it did not accept the request."""

    code: ClassVar[str] = "LLM_PROVIDER_REQUEST_REJECTED"


class LlmProviderRateLimited(LlmProviderError):
    """HTTP 429 persisted for every allowed attempt."""

    code: ClassVar[str] = "LLM_PROVIDER_RATE_LIMITED"


class LlmResponseRefused(LlmProviderError):
    """HTTP 200 refusal. The category is closed and the model hint is not followed."""

    code: ClassVar[str] = "LLM_RESPONSE_REFUSED"
    category: str | None
    recommended_model: str | None

    def __init__(
        self,
        *,
        request_id: str | None,
        category: str | None,
        recommended_model: str | None,
    ) -> None:
        self.category = category if category in REFUSAL_CATEGORIES else None
        self.recommended_model = recommended_model if isinstance(recommended_model, str) else None
        super().__init__(request_id=request_id)


class LlmResponseTruncated(LlmProviderError):
    """The completion stopped for max_tokens. Partial text is not a success."""

    code: ClassVar[str] = "LLM_RESPONSE_TRUNCATED"


class LlmResponseContextExceeded(LlmProviderError):
    """The completion stopped because the model context window was exceeded."""

    code: ClassVar[str] = "LLM_RESPONSE_CONTEXT_EXCEEDED"


class LlmResponseIncomplete(LlmProviderError):
    """The stop reason was pause_turn, tool_use, missing, or otherwise unfinished."""

    code: ClassVar[str] = "LLM_RESPONSE_INCOMPLETE"


class LlmResponseEmpty(LlmProviderError):
    """An accepted stop reason produced no text block."""

    code: ClassVar[str] = "LLM_RESPONSE_EMPTY"


class LlmResponseUnexpectedBlock(LlmProviderError):
    """A content block other than text or thinking was present."""

    code: ClassVar[str] = "LLM_RESPONSE_UNEXPECTED_BLOCK"


class LlmResponseAmbiguous(LlmProviderError):
    """More than one text block was present."""

    code: ClassVar[str] = "LLM_RESPONSE_AMBIGUOUS"
