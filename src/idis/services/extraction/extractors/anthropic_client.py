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

import anthropic

logger = logging.getLogger(__name__)

MAX_RETRIES = 2
RETRY_BACKOFF_BASE_SECONDS = 1.0
REQUEST_TIMEOUT_SECONDS = 120
MAX_TOKENS = 4096


class AnthropicLLMClient:
    """Anthropic-backed LLM client implementing the LLMClient protocol.

    Calls Claude via the Anthropic SDK. Temperature is fixed at 0 for
    deterministic output. Retries with exponential backoff on transient errors.

    Fail-closed: raises ValueError if ANTHROPIC_API_KEY is not set.
    """

    def __init__(self, *, model: str | None = None, max_tokens: int | None = None) -> None:
        """Initialize the Anthropic client.

        Args:
            model: Model identifier override. If not provided, reads from
                IDIS_ANTHROPIC_MODEL_EXTRACT env var, falling back to
                claude-sonnet-4-20250514.
            max_tokens: Maximum output tokens per request. Defaults to
                MAX_TOKENS (4096) if not provided.

        Raises:
            ValueError: If ANTHROPIC_API_KEY is not set in the environment.
        """
        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not api_key:
            raise ValueError(
                "ANTHROPIC_API_KEY environment variable is required "
                "when using the Anthropic backend. "
                "Set IDIS_EXTRACT_BACKEND=deterministic to use the deterministic client."
            )

        self._model = model or os.environ.get(
            "IDIS_ANTHROPIC_MODEL_EXTRACT",
            "claude-sonnet-4-20250514",
        )
        self._max_tokens = max_tokens or MAX_TOKENS
        self._client: anthropic.Anthropic = anthropic.Anthropic(
            api_key=api_key,
            timeout=REQUEST_TIMEOUT_SECONDS,
        )

    def call(self, prompt: str, *, json_mode: bool = False) -> str:
        """Make an LLM call via the Anthropic API and return raw response text.

        Args:
            prompt: The full prompt text to send.
            json_mode: If True, instruct the model to return JSON.

        Returns:
            Raw response string from the LLM.

        Raises:
            RuntimeError: If all retry attempts fail.
        """
        system_parts: list[str] = []
        if json_mode:
            system_parts.append(
                "You MUST respond with valid JSON only. No markdown, no explanation, "
                "no code fences. Output raw JSON."
            )

        messages: list[anthropic.types.MessageParam] = [{"role": "user", "content": prompt}]

        last_error: Exception | None = None
        for attempt in range(MAX_RETRIES + 1):
            try:
                response = self._client.messages.create(
                    model=self._model,
                    max_tokens=self._max_tokens,
                    temperature=0,
                    system="\n".join(system_parts) if system_parts else "",
                    messages=messages,
                )
                text_block = response.content[0]
                if hasattr(text_block, "text"):
                    return str(text_block.text)
                return str(text_block)

            except anthropic.RateLimitError as exc:
                last_error = exc
                logger.warning(
                    "Anthropic rate limit (attempt %d/%d)",
                    attempt + 1,
                    MAX_RETRIES + 1,
                )
                _backoff(attempt)

            except anthropic.APIStatusError as exc:
                last_error = exc
                if exc.status_code >= 500:
                    logger.warning(
                        "Anthropic server error %d (attempt %d/%d)",
                        exc.status_code,
                        attempt + 1,
                        MAX_RETRIES + 1,
                    )
                    _backoff(attempt)
                else:
                    raise RuntimeError(
                        f"Anthropic API error (non-retryable): {exc.status_code}"
                    ) from exc

            except anthropic.APIConnectionError as exc:
                last_error = exc
                logger.warning(
                    "Anthropic connection error (attempt %d/%d)",
                    attempt + 1,
                    MAX_RETRIES + 1,
                )
                _backoff(attempt)

        raise RuntimeError(
            f"Anthropic API call failed after {MAX_RETRIES + 1} attempts"
        ) from last_error


def _backoff(attempt: int) -> None:
    """Sleep with exponential backoff.

    Args:
        attempt: Zero-based attempt number.
    """
    delay = RETRY_BACKOFF_BASE_SECONDS * (2**attempt)
    time.sleep(delay)
