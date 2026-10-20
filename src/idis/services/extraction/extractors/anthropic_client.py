"""Anthropic LLM client implementing the LLMClient protocol.

Uses the Anthropic Python SDK to call Claude models. Provider-agnostic
from the caller's perspective — only the LLMClient.call() interface is exposed.

Configuration via environment variables:
- ANTHROPIC_API_KEY: Required. Fail-closed if missing.
- IDIS_ANTHROPIC_MODEL_EXTRACT: Model for extraction (default: claude-sonnet-4-20250514).
- IDIS_ANTHROPIC_MODEL_DEBATE_DEFAULT: Model for debate roles (default: claude-sonnet-4-20250514).
- IDIS_ANTHROPIC_MODEL_DEBATE_ARBITER: Model for arbiter role (default: claude-opus-4-20250514).
"""

from __future__ import annotations

import logging
import os
import time
from collections.abc import Mapping
from typing import Any, cast

import anthropic

from idis.providers.anthropic_capabilities import (
    LlmRequestShapeRejected,
    RequestShapeReason,
    build_request,
    require_model_effort,
)
from idis.providers.anthropic_failure import (
    REPLAYABLE_HTTP_STATUS,
    HttpAdmission,
    LlmProviderError,
    LlmProviderOutcomeUnknown,
    LlmProviderRateLimited,
    LlmProviderRequestRejected,
    classify_http_status,
)
from idis.providers.anthropic_result import LlmResult, parse_message

logger = logging.getLogger(__name__)

MAX_RETRIES = 2
RETRY_BACKOFF_BASE_SECONDS = 1.0
REQUEST_TIMEOUT_SECONDS = 120
MAX_TOKENS = 4096
_THINKING_BETWEEN_TOOLS = "between_tools"
_THINKING_ADAPTIVE = "adaptive"


class AnthropicLLMClient:
    """Anthropic-backed LLM client implementing the LLMClient protocol.

    Legacy and unknown model ids send temperature 0. A candidate id omits
    sampling parameters and requires an explicit effort at construction.
    Model output is not guaranteed deterministic. Only HTTP 429 is replayed.
    A missing response, timeout, or any other status is not sent again.

    Fail-closed: raises ValueError if ANTHROPIC_API_KEY is not set.
    """

    def __init__(
        self,
        *,
        model: str | None = None,
        max_tokens: int | None = None,
        effort: str | None = None,
    ) -> None:
        """Initialize the Anthropic client.

        Args:
            model: Model identifier override. If not provided, reads from
                IDIS_ANTHROPIC_MODEL_EXTRACT env var, falling back to
                claude-sonnet-4-20250514.
            max_tokens: Maximum output tokens per request. Defaults to
                MAX_TOKENS (4096) if not provided.
            effort: Explicit effort for a candidate model id. Legacy and
                unknown ids reject any value. A candidate id with no effort
                is rejected before the SDK client is built.

        Raises:
            ValueError: If ANTHROPIC_API_KEY is not set in the environment.
            LlmRequestShapeRejected: The model and effort pair cannot be sent.
        """
        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not api_key:
            raise ValueError(
                "ANTHROPIC_API_KEY environment variable is required "
                "when using the Anthropic backend. "
                "Set IDIS_EXTRACT_BACKEND=deterministic to use the deterministic client."
            )

        resolved_model = model or os.environ.get(
            "IDIS_ANTHROPIC_MODEL_EXTRACT",
            "claude-sonnet-4-20250514",
        )
        require_model_effort(model=resolved_model, effort=effort)
        self._model = resolved_model
        self._max_tokens = max_tokens or MAX_TOKENS
        self._effort = effort
        self._client: anthropic.Anthropic = anthropic.Anthropic(
            api_key=api_key,
            timeout=REQUEST_TIMEOUT_SECONDS,
            max_retries=0,
        )

    def call(self, prompt: str, *, json_mode: bool = False) -> str:
        """Return the text of one clean completion.

        Args:
            prompt: The full prompt text to send.
            json_mode: If True, instruct the model to return JSON.

        Returns:
            The single text block from an end_turn or stop_sequence completion.

        Raises:
            RuntimeError: Typed provider or response failure. The message is the
                stable code, plus the HTTP status only when the failure has one.
        """
        return self.call_typed(prompt, json_mode=json_mode).text

    def call_typed(self, prompt: str, *, json_mode: bool = False) -> LlmResult:
        """Send one prompt and return the typed completion.

        HTTP 429 is replayed at most MAX_RETRIES times. Every other failure is
        raised after exactly one request. The request keywords come from the
        model capability table.

        Args:
            prompt: The full prompt text to send.
            json_mode: If True, instruct the model to return JSON.

        Returns:
            Parsed text, stop reason, usage, request id, and observed model.

        Raises:
            RuntimeError: Typed provider or response failure.
        """
        system_parts: list[str] = []
        if json_mode:
            system_parts.append(
                "You MUST respond with valid JSON only. No markdown, no explanation, "
                "no code fences. Output raw JSON."
            )

        built = build_request(
            model=self._model,
            max_tokens=self._max_tokens,
            system="\n".join(system_parts) if system_parts else "",
            messages=[{"role": "user", "content": prompt}],
            effort=self._effort,
        )
        params = to_message_create_params(built)
        for attempt in range(MAX_RETRIES + 1):
            failure: LlmProviderError | None = None
            try:
                response = self._client.messages.create(**params)
            except anthropic.APIStatusError as exc:
                admission = classify_http_status(exc.status_code)
                if admission is HttpAdmission.REPLAYABLE and attempt < MAX_RETRIES:
                    logger.warning(
                        "Anthropic rate limit (attempt %d/%d)",
                        attempt + 1,
                        MAX_RETRIES + 1,
                    )
                    _backoff(attempt)
                    continue
                failure = _status_error(exc, admission)
            except anthropic.APIError as exc:
                logger.warning("Anthropic outcome unknown after one request")
                failure = LlmProviderOutcomeUnknown(request_id=_error_request_id(exc))
            else:
                return parse_message(response)
            if failure is None:
                raise LlmProviderOutcomeUnknown(request_id=None) from None
            # Outside the handler so the SDK exception is not retained.
            raise failure from None
        raise LlmProviderRateLimited(request_id=None, status_code=REPLAYABLE_HTTP_STATUS)


def to_message_create_params(request: Mapping[str, Any]) -> dict[str, Any]:
    """Copy builder output into ``messages.create`` keyword arguments.

    ``thinking.type=between_tools`` is absent from the installed SDK
    TypedDict. The encoder accepts that value, so it is cast.

    Args:
        request: Keyword arguments from ``build_request``.

    Returns:
        Arguments accepted by ``messages.create``.

    Raises:
        LlmRequestShapeRejected: ``thinking`` is present and is not adaptive
            or between_tools.
    """
    params: dict[str, Any] = {
        "model": request["model"],
        "max_tokens": request["max_tokens"],
        "messages": request["messages"],
    }
    if "temperature" in request:
        params["temperature"] = request["temperature"]
    if "system" in request:
        params["system"] = request["system"]
    if "output_config" in request:
        params["output_config"] = request["output_config"]
    if "thinking" in request:
        params["thinking"] = _sdk_thinking(request["thinking"])
    return params


def _sdk_thinking(value: object) -> anthropic.types.ThinkingConfigParam:
    if (
        isinstance(value, Mapping)
        and value.get("type") == _THINKING_BETWEEN_TOOLS
        and set(value) == {"type"}
    ):
        return cast(anthropic.types.ThinkingConfigParam, {"type": _THINKING_BETWEEN_TOOLS})
    if (
        isinstance(value, Mapping)
        and value.get("type") == _THINKING_ADAPTIVE
        and set(value) == {"type"}
    ):
        adaptive: anthropic.types.ThinkingConfigAdaptiveParam = {"type": "adaptive"}
        return adaptive
    raise LlmRequestShapeRejected(RequestShapeReason.THINKING_UNSUPPORTED)


def _status_error(exc: anthropic.APIStatusError, admission: HttpAdmission) -> LlmProviderError:
    request_id = exc.request_id if isinstance(exc.request_id, str) else None
    if admission is HttpAdmission.REPLAYABLE:
        return LlmProviderRateLimited(request_id=request_id, status_code=exc.status_code)
    if admission is HttpAdmission.CONFIRMED_NOT_ACCEPTED:
        return LlmProviderRequestRejected(request_id=request_id, status_code=exc.status_code)
    return LlmProviderOutcomeUnknown(request_id=request_id, status_code=exc.status_code)


def _error_request_id(exc: anthropic.APIError) -> str | None:
    value = getattr(exc, "request_id", None)
    if isinstance(value, str):
        return value
    return None


def _backoff(attempt: int) -> None:
    """Sleep with exponential backoff.

    Args:
        attempt: Zero-based attempt number.
    """
    delay = RETRY_BACKOFF_BASE_SECONDS * (2**attempt)
    time.sleep(delay)
