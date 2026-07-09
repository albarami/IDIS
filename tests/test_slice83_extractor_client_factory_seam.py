"""Slice83 Task 2 — injectable extractor-client factory seam (no strict enforcement yet).

TDD RED-first. Adds an optional ``extractor_client_factory`` to ``_build_extraction_llm_client``
(and threads it through ``_run_snapshot_extraction``) so the extraction client can be injected
for tests / future strict wiring — WITHOUT changing default behavior, without a real provider
call, and without any network. The factory receives a safe ``ExtractorClientSelection``
(backend / model / max_tokens) — never the API key. No strict enforcement and no provenance
here (Tasks 3 and 4).
"""

from __future__ import annotations

import dataclasses
import inspect
import os
from typing import Any
from unittest.mock import patch

import pytest

from idis.api.routes.runs import (
    ExtractorClientSelection,
    _build_extraction_llm_client,
    _run_snapshot_extraction,
)
from idis.services.extraction.extractors.anthropic_client import AnthropicLLMClient
from idis.services.extraction.extractors.llm_client import DeterministicLLMClient

_FAKE_KEY = "sk-ant-test-fake-key-for-unit-test"


def _env_without(*keys: str) -> dict[str, str]:
    return {k: v for k, v in os.environ.items() if k not in keys}


class _FakeLiveClient:
    """Stand-in for a live extraction client — never calls the network."""

    def call(self, prompt: str, *, json_mode: bool = False) -> str:
        return "[]"


class _StopForTest(Exception):
    """Sentinel to halt _run_snapshot_extraction right after the builder call."""


# --- default behavior unchanged (no factory supplied) ---


def test_default_unset_returns_deterministic() -> None:
    with patch.dict(os.environ, _env_without("IDIS_EXTRACT_BACKEND"), clear=True):
        assert isinstance(_build_extraction_llm_client(), DeterministicLLMClient)


def test_default_anthropic_with_key_returns_anthropic_client() -> None:
    env = {"IDIS_EXTRACT_BACKEND": "anthropic", "ANTHROPIC_API_KEY": _FAKE_KEY}
    with patch.dict(os.environ, env, clear=False):
        assert isinstance(_build_extraction_llm_client(), AnthropicLLMClient)


def test_default_anthropic_without_key_still_fails_closed() -> None:
    env = _env_without("ANTHROPIC_API_KEY")
    env["IDIS_EXTRACT_BACKEND"] = "anthropic"
    with (
        patch.dict(os.environ, env, clear=True),
        pytest.raises(ValueError, match="ANTHROPIC_API_KEY"),
    ):
        _build_extraction_llm_client()


# --- injected factory is used when supplied ---


def test_injected_factory_used_for_anthropic_without_real_construction() -> None:
    # No ANTHROPIC_API_KEY: the real AnthropicLLMClient would raise ValueError. With a factory
    # injected, the factory is used instead -> fake returned, no ValueError, no network.
    captured: dict[str, Any] = {}

    def fake_factory(selection: ExtractorClientSelection) -> _FakeLiveClient:
        captured["selection"] = selection
        return _FakeLiveClient()

    env = _env_without("ANTHROPIC_API_KEY")
    env["IDIS_EXTRACT_BACKEND"] = "anthropic"
    with patch.dict(os.environ, env, clear=True):
        client = _build_extraction_llm_client(extractor_client_factory=fake_factory)

    assert isinstance(client, _FakeLiveClient)
    selection = captured["selection"]
    assert selection.backend == "anthropic"
    assert selection.model == "claude-sonnet-4-20250514"
    assert selection.max_tokens == 4096


def test_injected_factory_used_for_deterministic_backend() -> None:
    def fake_factory(selection: ExtractorClientSelection) -> _FakeLiveClient:
        return _FakeLiveClient()

    with patch.dict(os.environ, {"IDIS_EXTRACT_BACKEND": "deterministic"}, clear=False):
        client = _build_extraction_llm_client(extractor_client_factory=fake_factory)
    assert isinstance(client, _FakeLiveClient)


def test_factory_receives_safe_selection_with_no_api_key() -> None:
    field_names = {f.name for f in dataclasses.fields(ExtractorClientSelection)}
    assert field_names == {"backend", "model", "max_tokens"}
    assert "api_key" not in field_names
    assert "anthropic_api_key" not in field_names

    captured: dict[str, Any] = {}

    def fake_factory(selection: ExtractorClientSelection) -> _FakeLiveClient:
        captured["selection"] = selection
        return _FakeLiveClient()

    env = {"IDIS_EXTRACT_BACKEND": "anthropic", "ANTHROPIC_API_KEY": _FAKE_KEY}
    with patch.dict(os.environ, env, clear=False):
        _build_extraction_llm_client(extractor_client_factory=fake_factory)
    # the selection object itself never carries the API key value
    assert _FAKE_KEY not in repr(captured["selection"])


# --- signature + threading ---


def test_builder_accepts_extractor_client_factory_param() -> None:
    params = inspect.signature(_build_extraction_llm_client).parameters
    assert "extractor_client_factory" in params
    assert params["extractor_client_factory"].default is None


def test_run_snapshot_extraction_accepts_factory_param() -> None:
    params = inspect.signature(_run_snapshot_extraction).parameters
    assert "extractor_client_factory" in params
    assert params["extractor_client_factory"].default is None


def test_run_snapshot_extraction_forwards_factory_to_builder() -> None:
    def sentinel_factory(selection: ExtractorClientSelection) -> _FakeLiveClient:
        return _FakeLiveClient()

    captured: dict[str, Any] = {}

    def fake_build(
        *,
        extractor_client_factory: Any = None,
        strict_live_extraction_required: bool = False,
        tenant_id: Any = None,
    ) -> Any:
        # Task 3 forwards the strict flag; Slice96 DEC-C also forwards tenant_id (provider budget).
        captured["factory"] = extractor_client_factory
        captured["tenant_id"] = tenant_id
        raise _StopForTest

    with (
        patch("idis.api.routes.runs._build_extraction_llm_client", fake_build),
        pytest.raises(_StopForTest),
    ):
        _run_snapshot_extraction(
            run_id="run-1",
            tenant_id="tenant-1",
            deal_id="deal-1",
            documents=[],
            db_conn=None,
            extractor_client_factory=sentinel_factory,
        )
    assert captured["factory"] is sentinel_factory
    assert captured["tenant_id"] == "tenant-1"  # tenant flows to the budget-wrapping helper
