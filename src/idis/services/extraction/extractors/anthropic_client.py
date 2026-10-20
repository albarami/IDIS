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
from typing import Any, NamedTuple, cast

import anthropic

from idis.providers.anthropic_capabilities import (
    LlmRequestShapeRejected,
    RequestShapeReason,
    RequestTransport,
    build_request,
    require_model_effort,
    resolve_model_capability,
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

    Legacy and unknown model ids send temperature 0 through messages.create.
    A candidate id omits sampling parameters, requires an explicit effort at
    construction, and is sent with messages.stream. Model output is not
    guaranteed deterministic. Only an initial HTTP 429 is replayed. A missing
    response, a failure after the stream starts, or any other status is not
    sent again.

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

        HTTP 429 on the initial response is replayed at most MAX_RETRIES times.
        Every other initial failure is raised after exactly one request. A
        candidate row sends the same keywords through messages.stream. A
        failure after that response has started is not replayed.

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
        transport = resolve_model_capability(self._model).transport
        for attempt in range(MAX_RETRIES + 1):
            failure: LlmProviderError | None = None
            try:
                dispatched = _dispatch(self._client, params, transport)
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
                if dispatched.failure is not None:
                    failure = dispatched.failure
                elif dispatched.message is not None:
                    return parse_message(dispatched.message)
                else:
                    failure = LlmProviderOutcomeUnknown(request_id=None)
            if failure is None:
                raise LlmProviderOutcomeUnknown(request_id=None) from None
            # Outside the handler so the SDK exception is not retained.
            raise failure from None
        raise LlmProviderRateLimited(request_id=None, status_code=REPLAYABLE_HTTP_STATUS)


def to_message_create_params(request: Mapping[str, Any]) -> dict[str, Any]:
    """Copy builder output into message keyword arguments.

    ``messages.create`` and ``messages.stream`` both accept the result.
    ``messages.stream`` adds ``stream: true``. ``thinking.type=between_tools``
    is absent from the installed SDK TypedDict. The encoder accepts that
    value, so it is cast.

    Args:
        request: Keyword arguments from ``build_request``.

    Returns:
        Arguments accepted by ``messages.create`` and ``messages.stream``.

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


class _Dispatch(NamedTuple):
    """One attempt's message, or a typed failure that must not be replayed."""

    message: object | None = None
    failure: LlmProviderError | None = None


def _dispatch(
    sdk: anthropic.Anthropic,
    params: Mapping[str, Any],
    transport: RequestTransport,
) -> _Dispatch:
    if transport != RequestTransport.STREAM:
        return _Dispatch(message=sdk.messages.create(**params))
    return _read_stream(sdk, params)


def _read_stream(sdk: anthropic.Anthropic, params: Mapping[str, Any]) -> _Dispatch:
    """Read one streamed message.

    An HTTP status error raised before the response body starts propagates so
    the caller can classify it. Any failure after the response starts becomes
    an unknown outcome and is not an HTTP admission decision.

    Args:
        sdk: SDK client with retries already disabled.
        params: Keyword arguments from ``to_message_create_params``.

    Returns:
        The final message, or a typed unknown failure with no provider text.
        A close error does not replace either value. ``KeyboardInterrupt`` and
        ``SystemExit`` from close still propagate.
    """
    try:
        manager = sdk.messages.stream(**params)
        stream = manager.__enter__()
    except anthropic.APIStatusError:
        raise
    except anthropic.APIError as exc:
        return _Dispatch(failure=LlmProviderOutcomeUnknown(request_id=_error_request_id(exc)))
    try:
        try:
            message = stream.get_final_message()
        except Exception as exc:
            # The response already started. Status 200 on an SSE error is not
            # an admission result, and a mid-stream fault is not replayed.
            logger.warning("Anthropic outcome unknown after the stream started")
            return _Dispatch(
                failure=LlmProviderOutcomeUnknown(request_id=_failure_request_id(exc, stream))
            )
        _copy_stream_request_id(message, stream)
        return _Dispatch(message=message)
    finally:
        _close_stream(manager)


def _close_stream(manager: Any) -> None:
    """Close one stream manager without replacing a pending result.

    An ``Exception`` from close is logged by class name and discarded.
    ``BaseException`` propagates, including ``KeyboardInterrupt`` and
    ``SystemExit``.

    Args:
        manager: Context manager returned by ``messages.stream``.
    """
    try:
        manager.__exit__(None, None, None)
    except Exception as exc:
        logger.warning("Anthropic stream close failed: %s", type(exc).__name__)


def _copy_stream_request_id(message: Any, stream: object) -> None:
    if isinstance(getattr(message, "_request_id", None), str):
        return
    request_id = getattr(stream, "request_id", None)
    if isinstance(request_id, str):
        message._request_id = request_id


def _failure_request_id(exc: BaseException, stream: object) -> str | None:
    if isinstance(exc, anthropic.APIError):
        found = _error_request_id(exc)
        if found is not None:
            return found
    request_id = getattr(stream, "request_id", None)
    if isinstance(request_id, str):
        return request_id
    return None


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
