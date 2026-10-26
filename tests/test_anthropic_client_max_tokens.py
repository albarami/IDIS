"""Tests for AnthropicLLMClient max_tokens configurability.

Verifies that:
1. Default max_tokens is 4096 (backward compat).
2. Custom max_tokens is stored and propagated to API calls.
3. max_tokens=None falls back to default 4096.
4. Each builder in runs.py constructs with the correct max_tokens.
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch


def _parsable_message(text: str) -> MagicMock:
    """SDK-shaped completion the typed parser can accept."""
    block = MagicMock()
    block.type = "text"
    block.text = text
    usage = MagicMock()
    usage.input_tokens = 1
    usage.output_tokens = 1
    usage.cache_creation_input_tokens = 0
    usage.cache_read_input_tokens = 0
    usage.cache_creation = None
    usage.output_tokens_details = None
    usage.service_tier = None
    usage.inference_geo = None
    message = MagicMock()
    message.content = [block]
    message.stop_reason = "end_turn"
    message.usage = usage
    message.model = "test-model"
    message._request_id = None
    return message


class TestAnthropicClientMaxTokens:
    """Unit tests for AnthropicLLMClient max_tokens parameter."""

    def _make_client(self, *, max_tokens: int | None = None, model: str = "test") -> MagicMock:
        """Create an AnthropicLLMClient with mocked Anthropic SDK."""
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}):
            from idis.services.extraction.extractors.anthropic_client import (
                AnthropicLLMClient,
            )

            client = AnthropicLLMClient(model=model, max_tokens=max_tokens)
        return client

    def test_default_max_tokens_is_4096(self) -> None:
        """Default max_tokens should be 4096 when not specified."""
        from idis.services.extraction.extractors.anthropic_client import MAX_TOKENS

        client = self._make_client()
        assert client._max_tokens == MAX_TOKENS
        assert client._max_tokens == 4096

    def test_custom_max_tokens_stored(self) -> None:
        """Custom max_tokens=16384 should be stored on the instance."""
        client = self._make_client(max_tokens=16384)
        assert client._max_tokens == 16384

    def test_none_max_tokens_falls_back_to_default(self) -> None:
        """max_tokens=None should fall back to default 4096."""
        client = self._make_client(max_tokens=None)
        assert client._max_tokens == 4096

    def test_custom_max_tokens_used_in_api_call(self) -> None:
        """Custom max_tokens should be passed to messages.create()."""
        client = self._make_client(max_tokens=16384)

        mock_response = _parsable_message("{}")
        client._client.messages.create = MagicMock(return_value=mock_response)

        client.call("test prompt", json_mode=True)

        call_kwargs = client._client.messages.create.call_args
        assert call_kwargs.kwargs["max_tokens"] == 16384

    def test_default_max_tokens_used_in_api_call(self) -> None:
        """Default max_tokens=4096 should be passed to messages.create()."""
        client = self._make_client()

        mock_response = _parsable_message("{}")
        client._client.messages.create = MagicMock(return_value=mock_response)

        client.call("test prompt", json_mode=False)

        call_kwargs = client._client.messages.create.call_args
        assert call_kwargs.kwargs["max_tokens"] == 4096


class TestBuilderMaxTokensWiring:
    """Verify each builder function passes the correct max_tokens."""

    @patch.dict(
        os.environ,
        {
            "IDIS_EXTRACT_BACKEND": "anthropic",
            "ANTHROPIC_API_KEY": "test-key",
        },
    )
    def test_extraction_builder_uses_4096(self) -> None:
        """_build_extraction_llm_client should use max_tokens=4096."""
        from idis.services.runs.step_runtime.roles import _build_extraction_llm_client

        client = _build_extraction_llm_client()
        assert client._max_tokens == 4096

    @patch.dict(
        os.environ,
        {
            "IDIS_DEBATE_BACKEND": "anthropic",
            "ANTHROPIC_API_KEY": "test-key",
        },
    )
    def test_analysis_builder_uses_8192(self) -> None:
        """_build_analysis_llm_client should use max_tokens=8192."""
        from idis.services.runs.step_runtime.roles import _build_analysis_llm_client

        client = _build_analysis_llm_client()
        assert client._max_tokens == 8192

    @patch.dict(
        os.environ,
        {
            "IDIS_DEBATE_BACKEND": "anthropic",
            "ANTHROPIC_API_KEY": "test-key",
        },
    )
    def test_scoring_builder_uses_16384(self) -> None:
        """_build_scoring_llm_client should use max_tokens=16384."""
        from idis.services.runs.step_runtime.roles import _build_scoring_llm_client

        client = _build_scoring_llm_client()
        assert client._max_tokens == 16384

    @patch.dict(
        os.environ,
        {
            "IDIS_DEBATE_BACKEND": "anthropic",
            "ANTHROPIC_API_KEY": "test-key",
        },
    )
    def test_debate_builder_uses_8192(self) -> None:
        """_build_debate_role_runners default+arbiter clients use max_tokens=8192."""
        from idis.services.runs.step_runtime.roles import _build_debate_role_runners

        runners = _build_debate_role_runners()
        assert runners.advocate._llm_client._max_tokens == 8192
        assert runners.arbiter._llm_client._max_tokens == 8192

    @patch.dict(
        os.environ,
        {
            "IDIS_EXTRACT_BACKEND": "deterministic",
            "IDIS_DEBATE_BACKEND": "deterministic",
        },
    )
    def test_deterministic_builders_unchanged(self) -> None:
        """Deterministic builders should not have _max_tokens attribute."""
        from idis.services.runs.step_runtime.roles import (
            _build_analysis_llm_client,
            _build_extraction_llm_client,
            _build_scoring_llm_client,
        )

        assert not hasattr(_build_extraction_llm_client(), "_max_tokens")
        assert not hasattr(_build_analysis_llm_client(), "_max_tokens")
        assert not hasattr(_build_scoring_llm_client(), "_max_tokens")
