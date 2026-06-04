"""Slice83 Task 1 — characterization pinning the CURRENT strict-live-extraction truth.

RED-as-discovery: documents existing behavior so later tasks change it deliberately. No real
Anthropic/network call (constructing ``AnthropicLLMClient`` does not call the API; only
``messages.create`` would), no FULL run, no production change. Pins (per the Slice83 plan):
  1. Backend selection: unset/`deterministic` -> DeterministicLLMClient; `anthropic`+key ->
     AnthropicLLMClient; `anthropic` without key -> safe ValueError.
  2. Execution-time strict enforcement now exists via an explicit flag (Slice83 Task 3);
     strictness is NOT inferred from env (the strict FULL caller threads it).
  3. Injectable extractor factory seam now exists (Slice83 Task 2); default still None.
  4. The EXTRACT step summary now records safe extraction_provenance (Slice83 Task 4); the
     separate methodology-execution summary stays provenance-free (out of scope).
  5. Slice82 reuse symbols are importable.
  6. RunStep.result_summary is an open dict[str, Any] (D-G: additive-safe, no schema change).
  7. SNAPSHOT stays non-strict: the admission gate is FULL-only (cheap helper assertion).
"""

from __future__ import annotations

import inspect
import os
from unittest.mock import patch

import pytest

from idis.api.routes.runs import _build_extraction_llm_client, _run_snapshot_extraction
from idis.models.extraction_execution import MethodologyExtractionExecutionRunResult
from idis.models.run_step import RunStep, StepName
from idis.services.extraction.extractors.anthropic_client import AnthropicLLMClient
from idis.services.extraction.extractors.llm_client import DeterministicLLMClient
from idis.services.runs.strict_full_live import is_strict_full_live_required

# Same fake unit-test key shape used by the existing test_llm_backend_selection.py (no real key).
_FAKE_KEY = "sk-ant-test-fake-key-for-unit-test"


def _env_without(*keys: str) -> dict[str, str]:
    return {k: v for k, v in os.environ.items() if k not in keys}


# --- 1. backend selection (current truth) ---


def test_unset_backend_returns_deterministic() -> None:
    with patch.dict(os.environ, _env_without("IDIS_EXTRACT_BACKEND"), clear=True):
        client = _build_extraction_llm_client()
    assert isinstance(client, DeterministicLLMClient)


def test_explicit_deterministic_returns_deterministic() -> None:
    with patch.dict(os.environ, {"IDIS_EXTRACT_BACKEND": "deterministic"}, clear=False):
        client = _build_extraction_llm_client()
    assert isinstance(client, DeterministicLLMClient)


def test_anthropic_with_key_returns_anthropic_client() -> None:
    env = {"IDIS_EXTRACT_BACKEND": "anthropic", "ANTHROPIC_API_KEY": _FAKE_KEY}
    with patch.dict(os.environ, env, clear=False):
        client = _build_extraction_llm_client()
    assert isinstance(client, AnthropicLLMClient)


def test_anthropic_without_key_fails_closed_safely() -> None:
    env = _env_without("ANTHROPIC_API_KEY")
    env["IDIS_EXTRACT_BACKEND"] = "anthropic"
    with (
        patch.dict(os.environ, env, clear=True),
        pytest.raises(ValueError, match="ANTHROPIC_API_KEY"),
    ):
        _build_extraction_llm_client()


# --- 2. execution-time strict enforcement now exists via an explicit flag (Slice83 Task 3) ---


def test_strict_enforcement_is_explicit_not_env_inferred() -> None:
    from idis.api.routes.runs import StrictLiveExtractionError

    # Task 3: the explicit strict flag forbids deterministic extraction (fail closed)...
    with (
        patch.dict(os.environ, _env_without("IDIS_EXTRACT_BACKEND"), clear=True),
        pytest.raises(StrictLiveExtractionError),
    ):
        _build_extraction_llm_client(strict_live_extraction_required=True)

    # ...but strictness is NOT inferred from env: IDIS_REQUIRE_FULL_LIVE=1 with no explicit
    # flag still returns deterministic (the strict FULL caller is responsible for threading it).
    env = _env_without("IDIS_EXTRACT_BACKEND")
    env["IDIS_REQUIRE_FULL_LIVE"] = "1"
    with patch.dict(os.environ, env, clear=True):
        assert isinstance(_build_extraction_llm_client(), DeterministicLLMClient)


# --- 3. injectable extractor factory seam now exists (Slice83 Task 2) ---


def test_build_extraction_llm_client_has_extractor_client_factory_param() -> None:
    params = inspect.signature(_build_extraction_llm_client).parameters
    # Task 2 added the injectable seam; default is None so behavior is unchanged without it.
    assert "extractor_client_factory" in params
    assert params["extractor_client_factory"].default is None


# --- 4. EXTRACT step summary records provenance; methodology summary stays clean (Task 4) ---


def test_extract_step_summary_records_provenance_methodology_summary_does_not() -> None:
    # The EXTRACT step summary (from _run_snapshot_extraction) now carries provenance...
    with patch.dict(os.environ, _env_without("IDIS_EXTRACT_BACKEND"), clear=True):
        extract_summary = _run_snapshot_extraction(
            run_id="run-1", tenant_id="tenant-1", deal_id="deal-1", documents=[], db_conn=None
        )
    assert "extraction_provenance" in extract_summary
    for key in ("status", "created_claim_ids", "chunk_count"):
        assert key in extract_summary  # existing fields remain

    # ...but the separate methodology-execution summary remains provenance-free (out of scope).
    methodology = MethodologyExtractionExecutionRunResult.from_task_results(
        tenant_id="tenant-1", deal_id="deal-1", run_id="run-1", task_results=[]
    ).to_run_step_summary()
    assert "extraction_provenance" not in methodology
    assert set(methodology.keys()) == {"status", "task_results", "summary"}


# --- 5. Slice82 reuse symbols are importable ---


def test_slice82_reuse_symbols_are_importable() -> None:
    from idis.services.llm_model_health import (
        LlmModelHealthCheck,
        LlmModelRole,
        _sanitize_request_id,
        summarize_prompt_registry_model_linkage,
    )
    from idis.services.prompts.registry import PromptArtifact, PromptRegistry

    assert LlmModelRole.EXTRACTION.value == "extraction"
    assert "provider_request_id" in LlmModelHealthCheck.model_fields
    assert callable(summarize_prompt_registry_model_linkage)
    assert callable(_sanitize_request_id)
    assert hasattr(PromptRegistry, "get_version")
    assert "version" in PromptArtifact.model_fields


# --- 6. result_summary is an open, additive-safe dict (D-G) ---


def test_run_step_result_summary_is_open_additive_dict() -> None:
    step = RunStep(
        step_id="step-1",
        run_id="run-1",
        tenant_id="tenant-1",
        step_name=next(iter(StepName)),
        step_order=0,
        result_summary={"extraction_provenance": {"provider": "anthropic"}},
    )
    assert step.result_summary["extraction_provenance"]["provider"] == "anthropic"
    dumped = step.model_dump(mode="json")
    assert dumped["result_summary"]["extraction_provenance"]["provider"] == "anthropic"


# --- 7. SNAPSHOT stays non-strict: the admission gate is FULL-only (cheap, no FULL run) ---


def test_snapshot_bypasses_full_only_strict_gate() -> None:
    # routes/runs.py:227 gates on `mode == "FULL" and is_strict_full_live_required(...)`.
    # The strict-profile flag is env-only / mode-agnostic, so a SNAPSHOT run never reaches it.
    assert is_strict_full_live_required(env={"IDIS_REQUIRE_FULL_LIVE": "1"}) is True
    assert is_strict_full_live_required(env={}) is False
