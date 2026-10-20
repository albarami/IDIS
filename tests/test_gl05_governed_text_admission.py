"""GL-05 P5 offline admission for candidate prices and the governed text wrapper.

Arithmetic at the planning rates is not rate authority. The v1 catalog is not
consulted by the wrapper.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Mapping
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from types import MappingProxyType
from typing import Any

import pytest

from idis.models.anthropic_price_catalog_v2 import (
    ANTHROPIC_CANDIDATE_PRICE_CATALOG,
    ANTHROPIC_CANDIDATE_PRICE_CATALOG_VERSION,
    ANTHROPIC_CANDIDATE_PRICE_MODE,
    AnthropicCandidatePriceError,
    resolve_candidate_price,
)
from idis.models.methodology_pricing import (
    ANTHROPIC_STANDARD_PRICE_CATALOG_VERSION,
    ANTHROPIC_STANDARD_PRICE_MODE,
    compute_anthropic_usd,
    resolve_anthropic_standard_price,
)
from idis.providers.anthropic_capabilities import LlmRequestShapeRejected, RequestShapeReason
from idis.providers.anthropic_failure import (
    LlmProviderOutcomeUnknown,
    LlmProviderRateLimited,
    LlmProviderRequestRejected,
    LlmResponseAmbiguous,
    LlmResponseContextExceeded,
    LlmResponseEmpty,
    LlmResponseIncomplete,
    LlmResponseRefused,
    LlmResponseTruncated,
    LlmResponseUnexpectedBlock,
)
from idis.providers.anthropic_result import LlmResult, LlmUsage
from idis.providers.budget import (
    PROVIDER_BUDGET_EXCEEDED,
    ProviderBudgetExceededError,
    _validate_governed_values,
)
from idis.providers.budget_policy import (
    PROVIDER_BUDGET_CONFIG_BLOCKED,
    ProviderBudgetPolicyError,
)
from idis.providers.governed_llm_client import (
    GovernedLlmClient,
    LlmBudgetSettlementFailed,
    LlmPriceBasisNotAdmitted,
    LlmPriceRowNotAdmitted,
    LlmReservationRecordFailed,
    LlmReservedContextExceeded,
    LlmUsageUnpricedMode,
    ReservationRecord,
)
from idis.services.extraction.extractors.anthropic_client import AnthropicLLMClient

_TENANT = "11111111-1111-4111-8111-111111111111"
_POLICY = "ab" * 32
_TEXT = "gl05-p5-visible-answer"
_PAYLOAD_BYTES = 50_000
_RESERVATION = "40000000-0000-4000-8000-000000000010"
_GLOBAL_MAX_CALLS = 50
_UNADMITTED_FIELDS = frozenset(
    {
        "cache_read_usd_per_million_tokens",
        "cache_creation_usd_per_million_tokens",
        "cache_write_usd_per_million_tokens",
        "long_context_input_usd_per_million_tokens",
        "long_context_output_usd_per_million_tokens",
        "priority_multiplier",
        "residency_multiplier",
        "inference_geo",
        "service_tier",
    }
)
_ROW_KEYS = frozenset(
    {
        "input_usd_per_million_tokens",
        "output_usd_per_million_tokens",
        "context_window_limit",
        "rate_basis",
        "window_basis",
    }
)
_PLANNING_CASES = (
    ("claude-sonnet-5-5", "2.00", "10.00", 16_384, "0.26384000"),
    ("claude-sonnet-5-5", "2.00", "10.00", 64_000, "0.74000000"),
    ("claude-opus-5-5", "4.00", "20.00", 32_768, "0.85536000"),
    ("claude-opus-5-5", "4.00", "20.00", 64_000, "1.48000000"),
)
_OUTCOME_ROWS = (
    (LlmProviderRequestRejected(request_id="req_gl05_p5", status_code=400), "refund"),
    (LlmProviderRateLimited(request_id="req_gl05_p5", status_code=429), "refund"),
    (LlmProviderOutcomeUnknown(request_id="req_gl05_p5"), "leave"),
    (
        LlmResponseRefused(
            request_id="req_gl05_p5",
            category="cyber",
            recommended_model=None,
        ),
        "consume",
    ),
    (LlmResponseTruncated(request_id="req_gl05_p5"), "consume"),
    (LlmResponseContextExceeded(request_id="req_gl05_p5"), "consume"),
    (LlmResponseIncomplete(request_id="req_gl05_p5"), "consume"),
    (LlmResponseEmpty(request_id="req_gl05_p5"), "consume"),
    (LlmResponseUnexpectedBlock(request_id="req_gl05_p5"), "consume"),
    (LlmResponseAmbiguous(request_id="req_gl05_p5"), "consume"),
    (RuntimeError("provider-sdk-fault"), "leave"),
)
_SETTLEMENT_FAULTS = (False, "raise")


def test_candidate_catalog_pins_planning_rows_and_omits_unadmitted_fields() -> None:
    """The two candidate rows are planning rates on the under-200k tier."""
    catalog = ANTHROPIC_CANDIDATE_PRICE_CATALOG
    assert isinstance(catalog, MappingProxyType)
    assert catalog["version"] == ANTHROPIC_CANDIDATE_PRICE_CATALOG_VERSION
    assert catalog["version"] == "anthropic-candidate-price-catalog-2026-10-20-v2"
    assert catalog["version"] != ANTHROPIC_STANDARD_PRICE_CATALOG_VERSION
    assert catalog["mode"] == ANTHROPIC_CANDIDATE_PRICE_MODE == ANTHROPIC_STANDARD_PRICE_MODE
    models = catalog["models"]
    assert isinstance(models, MappingProxyType)
    assert set(models) == {"claude-sonnet-5-5", "claude-opus-5-5"}
    for model_id, raw_input, raw_output, _output_tokens, _usd in _PLANNING_CASES:
        if model_id == "claude-sonnet-5-5" and _output_tokens != 16_384:
            continue
        row = models[model_id]
        assert isinstance(row, MappingProxyType)
        assert set(row) == _ROW_KEYS
        assert _UNADMITTED_FIELDS.isdisjoint(row)
        assert row["input_usd_per_million_tokens"] == raw_input
        assert row["output_usd_per_million_tokens"] == raw_output
        assert row["rate_basis"] == "PLANNING"
        assert row["window_basis"] == "PRICE_TIER_BOUND"
        assert row["context_window_limit"] == 200_000
        resolved = resolve_candidate_price(model_id)
        assert resolved.rate_basis == "PLANNING"
        assert resolved.window_basis == "PRICE_TIER_BOUND"
        assert resolved.context_window_limit == 200_000
        assert resolved.mode == ANTHROPIC_STANDARD_PRICE_MODE
        assert resolved.input_usd_per_million_tokens == f"{Decimal(raw_input):.8f}"
        assert resolved.output_usd_per_million_tokens == f"{Decimal(raw_output):.8f}"
        assert resolved.row_sha256 == _expected_row_hash(resolved)
        for name in _UNADMITTED_FIELDS:
            assert not hasattr(resolved, name)


def test_unknown_and_malformed_prices_are_code_only() -> None:
    """Legacy ids, unknown ids, and a bad row fail with a code and nothing else."""
    for model_id in ("claude-sonnet-4-20250514", "claude-opus-4-20250514", "cir01-test-model"):
        with pytest.raises(AnthropicCandidatePriceError) as missing:
            resolve_candidate_price(model_id)
        assert str(missing.value) == missing.value.code == "LLM_CANDIDATE_PRICE_UNAVAILABLE"
    malformed = {
        "version": ANTHROPIC_CANDIDATE_PRICE_CATALOG_VERSION,
        "mode": ANTHROPIC_CANDIDATE_PRICE_MODE,
        "models": {
            "claude-sonnet-5-5": {
                "input_usd_per_million_tokens": "0",
                "output_usd_per_million_tokens": "10.00",
                "context_window_limit": 200_000,
                "rate_basis": "PLANNING",
                "window_basis": "PRICE_TIER_BOUND",
                "cache_read_usd_per_million_tokens": "0.20",
            }
        },
    }
    with pytest.raises(AnthropicCandidatePriceError) as bad_row:
        resolve_candidate_price("claude-sonnet-5-5", catalog=malformed)
    assert str(bad_row.value) == bad_row.value.code == "LLM_CANDIDATE_PRICE_MALFORMED"
    drifted = {**dict(ANTHROPIC_CANDIDATE_PRICE_CATALOG), "version": "other"}
    with pytest.raises(AnthropicCandidatePriceError) as bad_catalog:
        resolve_candidate_price("claude-sonnet-5-5", catalog=drifted)
    assert str(bad_catalog.value) == bad_catalog.value.code == "LLM_CANDIDATE_PRICE_CATALOG_INVALID"


@pytest.mark.parametrize(
    ("model_id", "output_tokens", "expected_usd"),
    [(case[0], case[3], case[4]) for case in _PLANNING_CASES],
)
def test_planning_rate_reservation_arithmetic(
    model_id: str, output_tokens: int, expected_usd: str
) -> None:
    """A 50,000-byte payload reserves the planning-rate ceiling. Not rate authority."""
    price = resolve_candidate_price(model_id)
    assert (
        compute_anthropic_usd(
            input_tokens=_PAYLOAD_BYTES,
            output_tokens=output_tokens,
            input_usd_per_million_tokens=price.input_usd_per_million_tokens,
            output_usd_per_million_tokens=price.output_usd_per_million_tokens,
        )
        == expected_usd
    )
    v1 = resolve_anthropic_standard_price(model_id="claude-sonnet-4-20250514")
    v1_usd = compute_anthropic_usd(
        input_tokens=_PAYLOAD_BYTES,
        output_tokens=output_tokens,
        input_usd_per_million_tokens=v1.input_usd_per_million_tokens,
        output_usd_per_million_tokens=v1.output_usd_per_million_tokens,
    )
    assert v1_usd != expected_usd
    port = _LedgerPort(max_calls=4, max_usd=Decimal("10.00"))
    inner = _ScriptedClient(payload_bytes=_PAYLOAD_BYTES, max_tokens=output_tokens)
    governed = _governed(
        port=port,
        price=price,
        inner=inner,
        admit_planning_rates=True,
        admitted_row_sha256=price.row_sha256,
    )
    assert governed.call("prompt") == _TEXT
    assert [name for name, _vector in port.operations] == ["reserve", "consume"]
    reserved, consumed = port.operations
    assert reserved[1] == consumed[1]
    vector = reserved[1]
    assert vector["calls"] == 1
    assert vector["input_tokens"] == _PAYLOAD_BYTES
    assert vector["output_tokens"] == output_tokens
    assert vector["embedding_tokens"] == 0
    assert vector["usd"] == Decimal(expected_usd)
    assert vector["component_id"] == "anthropic"
    assert vector["tenant_id"] == _TENANT
    assert vector["policy_hash"] == _POLICY
    assert vector["global_max_calls"] == _GLOBAL_MAX_CALLS
    assert vector["reservation_id"] == _RESERVATION


def test_shape_rejection_moves_no_money(monkeypatch: pytest.MonkeyPatch) -> None:
    """prepare_request rejects the shape before reserve and before call_typed."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "cir01-offline-test-key")
    inner = AnthropicLLMClient(model="claude-sonnet-4-20250514", max_tokens=173)
    inner._effort = "high"
    port = _LedgerPort(max_calls=4, max_usd=Decimal("10.00"))
    price = resolve_candidate_price("claude-sonnet-5-5")
    governed = _governed(
        port=port,
        price=price,
        inner=inner,
        admit_planning_rates=False,
        admitted_row_sha256=price.row_sha256,
    )
    with pytest.raises(LlmRequestShapeRejected) as caught:
        governed.call("Synthetic request only")
    assert caught.value.reason == RequestShapeReason.EFFORT_NOT_APPLICABLE.value
    assert port.operations == []


def test_planning_rates_are_refused_unless_explicitly_admitted() -> None:
    """The default gate refuses a PLANNING row before reserve. Nothing in src passes true."""
    port = _LedgerPort(max_calls=4, max_usd=Decimal("10.00"))
    inner = _ScriptedClient(payload_bytes=100, max_tokens=16)
    price = resolve_candidate_price("claude-sonnet-5-5")
    governed = _governed(
        port=port,
        price=price,
        inner=inner,
        admit_planning_rates=False,
        admitted_row_sha256=price.row_sha256,
    )
    with pytest.raises(LlmPriceBasisNotAdmitted) as caught:
        governed.call("prompt")
    _assert_code(caught.value, "LLM_PRICE_BASIS_NOT_ADMITTED")
    _assert_detached(caught.value)
    assert port.operations == []
    assert inner.events == [("prepare", "prompt", False)]
    root = Path("src")
    for path in root.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert "admit_planning_rates=True" not in text
        assert "admit_planning_rates = True" not in text


def test_official_rate_basis_is_admitted_without_the_planning_flag() -> None:
    """An OFFICIAL row reserves when the planning flag stays false."""
    official = replace(resolve_candidate_price("claude-opus-5-5"), rate_basis="OFFICIAL")
    official = replace(official, row_sha256=_expected_row_hash(official))
    port = _LedgerPort(max_calls=4, max_usd=Decimal("10.00"))
    inner = _ScriptedClient(payload_bytes=100, max_tokens=16)
    governed = _governed(
        port=port,
        price=official,
        inner=inner,
        admit_planning_rates=False,
        admitted_row_sha256=official.row_sha256,
    )
    assert governed.call("prompt", json_mode=True) == _TEXT
    assert inner.events == [("prepare", "prompt", True), ("call", "prompt", True)]
    assert [name for name, _vector in port.operations] == ["reserve", "consume"]


def test_reserved_tokens_over_the_window_are_refused_before_reserve() -> None:
    """Input bytes plus the output ceiling cannot exceed the recorded window."""
    port = _LedgerPort(max_calls=4, max_usd=Decimal("10.00"))
    inner = _ScriptedClient(payload_bytes=150_000, max_tokens=50_001)
    price = resolve_candidate_price("claude-sonnet-5-5")
    governed = _governed(
        port=port,
        price=price,
        inner=inner,
        admit_planning_rates=True,
        admitted_row_sha256=price.row_sha256,
    )
    with pytest.raises(LlmReservedContextExceeded) as caught:
        governed.call("prompt")
    _assert_code(caught.value, "LLM_RESERVED_CONTEXT_EXCEEDED")
    _assert_detached(caught.value)
    assert port.operations == []
    assert [name for name, _prompt, _json_mode in inner.events] == ["prepare"]


def test_window_boundary_equal_to_the_limit_is_reserved() -> None:
    """A reservation that lands on the window limit is still admitted."""
    port = _LedgerPort(max_calls=4, max_usd=Decimal("100.00"))
    inner = _ScriptedClient(payload_bytes=150_000, max_tokens=50_000)
    price = resolve_candidate_price("claude-sonnet-5-5")
    governed = _governed(
        port=port,
        price=price,
        inner=inner,
        admit_planning_rates=True,
        admitted_row_sha256=price.row_sha256,
    )
    assert governed.call("prompt") == _TEXT
    vector = port.operations[0][1]
    assert vector["input_tokens"] + vector["output_tokens"] == 200_000


def test_failed_reserve_does_not_call_the_provider() -> None:
    """A false reserve becomes the existing budget-exceeded error and does not send."""
    port = _LedgerPort(max_calls=4, max_usd=Decimal("0.01"))
    inner = _ScriptedClient(payload_bytes=_PAYLOAD_BYTES, max_tokens=16_384)
    price = resolve_candidate_price("claude-sonnet-5-5")
    governed = _governed(
        port=port,
        price=price,
        inner=inner,
        admit_planning_rates=True,
        admitted_row_sha256=price.row_sha256,
    )
    with pytest.raises(ProviderBudgetExceededError) as caught:
        governed.call("prompt")
    assert caught.value.code == PROVIDER_BUDGET_EXCEEDED
    _assert_detached(caught.value)
    assert [name for name, _vector in port.operations] == ["reserve"]
    assert inner.events == [("prepare", "prompt", False)]


def test_budget_exceeded_from_the_port_is_not_followed_by_a_provider_call() -> None:
    """A budget denial raised by the port is the same error and does not send."""
    denial = ProviderBudgetExceededError(
        provider="anthropic",
        tenant_id=_TENANT,
        limit=1,
        used=1,
    )
    port = _RaisingPort(denial)
    inner = _ScriptedClient(payload_bytes=10, max_tokens=10)
    price = resolve_candidate_price("claude-sonnet-5-5")
    governed = _governed(
        port=port,
        price=price,
        inner=inner,
        admit_planning_rates=True,
        admitted_row_sha256=price.row_sha256,
    )
    with pytest.raises(ProviderBudgetExceededError) as caught:
        governed.call("prompt")
    assert caught.value is denial
    _assert_detached(caught.value)
    assert inner.events == [("prepare", "prompt", False)]


@pytest.mark.parametrize(("failure", "settlement"), _OUTCOME_ROWS)
def test_outcome_settles_the_reserved_vector(failure: Exception, settlement: str) -> None:
    """Each typed outcome consumes, refunds, or leaves the same reserved vector."""
    port = _LedgerPort(max_calls=4, max_usd=Decimal("10.00"))
    inner = _ScriptedClient(payload_bytes=100, max_tokens=16, error=failure)
    price = resolve_candidate_price("claude-sonnet-5-5")
    governed = _governed(
        port=port,
        price=price,
        inner=inner,
        admit_planning_rates=True,
        admitted_row_sha256=price.row_sha256,
    )
    with pytest.raises(type(failure)) as caught:
        governed.call("prompt")
    assert caught.value is failure
    _assert_detached(caught.value)
    names = [name for name, _vector in port.operations]
    if settlement == "refund":
        assert names == ["reserve", "refund"]
    elif settlement == "consume":
        assert names == ["reserve", "consume"]
    else:
        assert names == ["reserve"]
    if len(port.operations) == 2:
        assert port.operations[0][1] == port.operations[1][1]


@pytest.mark.parametrize(
    "usage",
    [
        {"cache_creation_input_tokens": 1},
        {"cache_read_input_tokens": 2},
        {"cache_creation_5m_input_tokens": 3},
        {"cache_creation_1h_input_tokens": 4},
        {"service_tier": "priority"},
        {"service_tier": None},
    ],
)
def test_unpriced_usage_is_consumed_and_the_text_is_not_released(usage: dict[str, Any]) -> None:
    """Cache tokens or a non-standard tier are consumed, then refused without the text."""
    port = _LedgerPort(max_calls=4, max_usd=Decimal("10.00"))
    inner = _ScriptedClient(payload_bytes=100, max_tokens=16, usage=usage)
    price = resolve_candidate_price("claude-sonnet-5-5")
    governed = _governed(
        port=port,
        price=price,
        inner=inner,
        admit_planning_rates=True,
        admitted_row_sha256=price.row_sha256,
    )
    with pytest.raises(LlmUsageUnpricedMode) as caught:
        governed.call("prompt")
    _assert_code(caught.value, "LLM_USAGE_UNPRICED_MODE")
    _assert_detached(caught.value)
    assert _TEXT not in str(caught.value)
    assert _TEXT not in repr(caught.value)
    assert [name for name, _vector in port.operations] == ["reserve", "consume"]
    assert port.operations[0][1] == port.operations[1][1]


def test_standard_usage_with_thinking_tokens_is_returned() -> None:
    """Thinking tokens and inference geo are not cache fields and do not refuse the text."""
    port = _LedgerPort(max_calls=4, max_usd=Decimal("10.00"))
    inner = _ScriptedClient(
        payload_bytes=100,
        max_tokens=16,
        usage={"thinking_tokens": 9, "inference_geo": "us", "service_tier": "standard"},
    )
    price = resolve_candidate_price("claude-sonnet-5-5")
    governed = _governed(
        port=port,
        price=price,
        inner=inner,
        admit_planning_rates=True,
        admitted_row_sha256=price.row_sha256,
    )
    assert governed.call("prompt") == _TEXT


def test_ledger_port_enforces_exact_vector_one_terminal_and_caps() -> None:
    """The in-memory port rejects a different vector, a second terminal, and cap breaches."""
    port = _LedgerPort(max_calls=1, max_usd=Decimal("1.00"))
    first = _sample_vector(usd=Decimal("0.20000000"))
    assert port.reserve(first) is True
    assert port.consume(first) is True
    assert port.consume(dict(first)) is True
    with pytest.raises(ProviderBudgetPolicyError) as wrong_terminal:
        port.refund(first)
    assert wrong_terminal.value.code == PROVIDER_BUDGET_CONFIG_BLOCKED
    second = _sample_vector(usd=Decimal("0.20000000"), reservation_id=_RESERVATION[:-1] + "1")
    assert port.reserve(second) is False
    room = _LedgerPort(max_calls=4, max_usd=Decimal("0.10"))
    assert room.reserve(_sample_vector(usd=Decimal("0.20000000"))) is False
    exact = _LedgerPort(max_calls=4, max_usd=Decimal("10.00"))
    kept = _sample_vector(usd=Decimal("0.20000000"), reservation_id=_RESERVATION[:-1] + "2")
    assert exact.reserve(kept) is True
    changed = {**kept, "output_tokens": int(kept["output_tokens"]) + 1}
    with pytest.raises(ProviderBudgetPolicyError) as mismatched:
        exact.consume(changed)
    assert mismatched.value.code == PROVIDER_BUDGET_CONFIG_BLOCKED
    assert exact.state_of(str(kept["reservation_id"])) == "RESERVED"


@pytest.mark.parametrize(("failure", "settlement"), _OUTCOME_ROWS)
@pytest.mark.parametrize("fault", _SETTLEMENT_FAULTS)
def test_failed_settlement_is_not_treated_as_settled(
    failure: Exception, settlement: str, fault: bool | str
) -> None:
    """A refused consume or refund replaces the outcome. A leave row never settles."""
    override = _settlement_override(fault)
    port = _LedgerPort(
        max_calls=4,
        max_usd=Decimal("10.00"),
        consume_override=override if settlement != "refund" else None,
        refund_override=override if settlement != "consume" else None,
    )
    inner = _ScriptedClient(payload_bytes=100, max_tokens=16, error=failure)
    price = resolve_candidate_price("claude-sonnet-5-5")
    governed = _governed(
        port=port,
        price=price,
        inner=inner,
        admit_planning_rates=True,
        admitted_row_sha256=price.row_sha256,
    )
    prompt = "gl05-p5b-settlement-prompt"
    if settlement == "leave":
        with pytest.raises(type(failure)) as left:
            governed.call(prompt)
        assert left.value is failure
        _assert_detached(left.value)
        assert [name for name, _vector in port.operations] == ["reserve"]
        assert port.state_of(_RESERVATION) == "RESERVED"
        assert [name for name, _text, _json_mode in inner.events] == ["prepare", "call"]
        return
    with pytest.raises(LlmBudgetSettlementFailed) as caught:
        governed.call(prompt)
    _assert_settlement_failure(
        caught.value,
        intended=settlement,
        provider_code=getattr(failure, "code", None),
        prompt=prompt,
    )
    assert [name for name, _vector in port.operations] == ["reserve", settlement]
    assert port.operations[0][1] == port.operations[1][1]
    assert port.state_of(_RESERVATION) == "RESERVED"
    assert [name for name, _text, _json_mode in inner.events] == ["prepare", "call"]


@pytest.mark.parametrize("fault", _SETTLEMENT_FAULTS)
def test_failed_consume_of_a_clean_result_withholds_the_text(fault: bool | str) -> None:
    """A clean completion whose consume is refused does not release the text."""
    port = _LedgerPort(
        max_calls=4,
        max_usd=Decimal("10.00"),
        consume_override=_settlement_override(fault),
    )
    inner = _ScriptedClient(payload_bytes=100, max_tokens=16)
    price = resolve_candidate_price("claude-sonnet-5-5")
    governed = _governed(
        port=port,
        price=price,
        inner=inner,
        admit_planning_rates=True,
        admitted_row_sha256=price.row_sha256,
    )
    prompt = "gl05-p5b-clean-settlement-prompt"
    with pytest.raises(LlmBudgetSettlementFailed) as caught:
        governed.call(prompt)
    _assert_settlement_failure(caught.value, intended="consume", provider_code=None, prompt=prompt)
    assert _TEXT not in str(caught.value)
    assert [name for name, _vector in port.operations] == ["reserve", "consume"]
    assert port.state_of(_RESERVATION) == "RESERVED"
    assert [name for name, _text, _json_mode in inner.events] == ["prepare", "call"]


def test_lookalike_official_row_is_refused_before_reserve() -> None:
    """An object that merely claims OFFICIAL is not the pinned resolver row."""

    class _OfficialLookAlike:
        rate_basis = "OFFICIAL"
        context_window_limit = 200_000
        input_usd_per_million_tokens = "2.00000000"
        output_usd_per_million_tokens = "10.00000000"
        catalog_version = ANTHROPIC_CANDIDATE_PRICE_CATALOG_VERSION
        mode = ANTHROPIC_CANDIDATE_PRICE_MODE
        model_id = "claude-sonnet-5-5"
        window_basis = "PRICE_TIER_BOUND"
        row_sha256 = "ab" * 32

    price = _OfficialLookAlike()
    port = _LedgerPort(max_calls=4, max_usd=Decimal("10.00"))
    inner = _ScriptedClient(payload_bytes=100, max_tokens=16)
    governed = _governed(
        port=port,
        price=price,
        inner=inner,
        admit_planning_rates=False,
        admitted_row_sha256=price.row_sha256,
    )
    with pytest.raises(LlmPriceRowNotAdmitted) as caught:
        governed.call("prompt")
    _assert_code(caught.value, "LLM_PRICE_ROW_NOT_ADMITTED")
    _assert_detached(caught.value)
    assert port.operations == []
    assert [name for name, _text, _json_mode in inner.events] == ["prepare"]


def test_tampered_resolver_row_is_refused_before_reserve() -> None:
    """A resolver row whose identity changed without a new hash is refused."""
    price = resolve_candidate_price("claude-sonnet-5-5")
    tampered = replace(price, input_usd_per_million_tokens="9.00000000")
    port = _LedgerPort(max_calls=4, max_usd=Decimal("10.00"))
    inner = _ScriptedClient(payload_bytes=100, max_tokens=16)
    governed = _governed(
        port=port,
        price=tampered,
        inner=inner,
        admit_planning_rates=True,
        admitted_row_sha256=tampered.row_sha256,
    )
    with pytest.raises(LlmPriceRowNotAdmitted) as caught:
        governed.call("prompt")
    _assert_code(caught.value, "LLM_PRICE_ROW_NOT_ADMITTED")
    _assert_detached(caught.value)
    assert port.operations == []
    assert [name for name, _text, _json_mode in inner.events] == ["prepare"]


def test_wrong_pinned_hash_is_refused_before_reserve() -> None:
    """A genuine row whose pin is not the admitted hash is refused."""
    price = resolve_candidate_price("claude-sonnet-5-5")
    port = _LedgerPort(max_calls=4, max_usd=Decimal("10.00"))
    inner = _ScriptedClient(payload_bytes=100, max_tokens=16)
    governed = _governed(
        port=port,
        price=price,
        inner=inner,
        admit_planning_rates=True,
        admitted_row_sha256="cd" * 32,
    )
    with pytest.raises(LlmPriceRowNotAdmitted) as caught:
        governed.call("prompt")
    _assert_code(caught.value, "LLM_PRICE_ROW_NOT_ADMITTED")
    _assert_detached(caught.value)
    assert port.operations == []
    assert [name for name, _text, _json_mode in inner.events] == ["prepare"]


def test_genuine_planning_row_passes_the_hash_then_the_basis_gate() -> None:
    """The real planning hash is admitted, and the basis gate still applies."""
    price = resolve_candidate_price("claude-sonnet-5-5")
    closed = _LedgerPort(max_calls=4, max_usd=Decimal("10.00"))
    closed_inner = _ScriptedClient(payload_bytes=100, max_tokens=16)
    refused = _governed(
        port=closed,
        price=price,
        inner=closed_inner,
        admit_planning_rates=False,
        admitted_row_sha256=price.row_sha256,
    )
    with pytest.raises(LlmPriceBasisNotAdmitted) as caught:
        refused.call("prompt")
    _assert_code(caught.value, "LLM_PRICE_BASIS_NOT_ADMITTED")
    _assert_detached(caught.value)
    assert closed.operations == []
    opened = _LedgerPort(max_calls=4, max_usd=Decimal("10.00"))
    opened_inner = _ScriptedClient(payload_bytes=100, max_tokens=16)
    admitted = _governed(
        port=opened,
        price=price,
        inner=opened_inner,
        admit_planning_rates=True,
        admitted_row_sha256=price.row_sha256,
    )
    assert admitted.call("prompt") == _TEXT
    assert [name for name, _vector in opened.operations] == ["reserve", "consume"]


def test_on_reserved_runs_once_after_reserve_and_before_the_provider() -> None:
    """The recording hook sees the reservation before the provider is called."""
    timeline: list[str] = []
    port = _LedgerPort(max_calls=4, max_usd=Decimal("10.00"), timeline=timeline)
    inner = _ScriptedClient(
        payload_bytes=100,
        max_tokens=16,
        model="claude-opus-5-5",
        timeline=timeline,
    )
    seen: list[ReservationRecord] = []

    def on_reserved(record: ReservationRecord) -> None:
        timeline.append("hook")
        seen.append(record)

    price = resolve_candidate_price("claude-sonnet-5-5")
    governed = _governed(
        port=port,
        price=price,
        inner=inner,
        admit_planning_rates=True,
        admitted_row_sha256=price.row_sha256,
        on_reserved=on_reserved,
    )
    assert governed.call("prompt") == _TEXT
    assert timeline == ["prepare", "reserve", "hook", "call", "consume"]
    assert seen == [
        ReservationRecord(
            reservation_id=_RESERVATION,
            tenant_id=_TENANT,
            component_id="anthropic",
            policy_hash=_POLICY,
            model="claude-opus-5-5",
            max_tokens=16,
            payload_bytes=100,
            vector_sha256=_expected_vector_sha256(port.operations[0][1]),
        )
    ]


def test_default_reservation_log_has_id_and_vector_hash_only(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """With no hook, one INFO line carries the reservation id and the vector hash."""
    caplog.set_level(logging.INFO, logger="idis.providers.governed_llm_client")
    port = _LedgerPort(max_calls=4, max_usd=Decimal("10.00"))
    inner = _ScriptedClient(payload_bytes=100, max_tokens=16)
    price = resolve_candidate_price("claude-sonnet-5-5")
    governed = _governed(
        port=port,
        price=price,
        inner=inner,
        admit_planning_rates=True,
        admitted_row_sha256=price.row_sha256,
    )
    prompt = "gl05-p5b-prompt-must-not-be-logged"
    assert governed.call(prompt) == _TEXT
    records = [
        record for record in caplog.records if record.name == "idis.providers.governed_llm_client"
    ]
    assert len(records) == 1
    assert records[0].levelno == logging.INFO
    digest = _expected_vector_sha256(port.operations[0][1])
    assert records[0].getMessage() == f"{_RESERVATION} {digest}"
    assert prompt not in caplog.text
    assert _TEXT not in caplog.text


def test_failed_reservation_record_refunds_and_skips_the_provider() -> None:
    """A hook that cannot record refunds the reservation and does not send."""
    port = _LedgerPort(max_calls=4, max_usd=Decimal("10.00"))
    inner = _ScriptedClient(payload_bytes=100, max_tokens=16)
    price = resolve_candidate_price("claude-sonnet-5-5")

    def on_reserved(_record: ReservationRecord) -> None:
        raise RuntimeError("record-store-unavailable")

    governed = _governed(
        port=port,
        price=price,
        inner=inner,
        admit_planning_rates=True,
        admitted_row_sha256=price.row_sha256,
        on_reserved=on_reserved,
    )
    prompt = "gl05-p5b-record-prompt"
    with pytest.raises(LlmReservationRecordFailed) as caught:
        governed.call(prompt)
    _assert_code(caught.value, "LLM_RESERVATION_RECORD_FAILED")
    _assert_detached(caught.value)
    assert prompt not in str(caught.value)
    assert [name for name, _vector in port.operations] == ["reserve", "refund"]
    assert port.operations[0][1] == port.operations[1][1]
    assert port.state_of(_RESERVATION) == "REFUNDED"
    assert [name for name, _text, _json_mode in inner.events] == ["prepare"]


@pytest.mark.parametrize("fault", _SETTLEMENT_FAULTS)
def test_failed_record_with_failed_refund_is_a_settlement_error(fault: bool | str) -> None:
    """A hook failure whose refund is refused surfaces the settlement error."""
    port = _LedgerPort(
        max_calls=4,
        max_usd=Decimal("10.00"),
        refund_override=_settlement_override(fault),
    )
    inner = _ScriptedClient(payload_bytes=100, max_tokens=16)
    price = resolve_candidate_price("claude-sonnet-5-5")

    def on_reserved(_record: ReservationRecord) -> None:
        raise RuntimeError("record-store-unavailable")

    governed = _governed(
        port=port,
        price=price,
        inner=inner,
        admit_planning_rates=True,
        admitted_row_sha256=price.row_sha256,
        on_reserved=on_reserved,
    )
    with pytest.raises(LlmBudgetSettlementFailed) as caught:
        governed.call("gl05-p5b-record-prompt")
    _assert_settlement_failure(
        caught.value,
        intended="refund",
        provider_code=None,
        prompt="gl05-p5b-record-prompt",
    )
    assert [name for name, _vector in port.operations] == ["reserve", "refund"]
    assert port.state_of(_RESERVATION) == "RESERVED"
    assert [name for name, _text, _json_mode in inner.events] == ["prepare"]


def _expected_row_hash(resolved: Any) -> str:
    identity = {
        "catalog_version": resolved.catalog_version,
        "mode": resolved.mode,
        "model_id": resolved.model_id,
        "input_usd_per_million_tokens": resolved.input_usd_per_million_tokens,
        "output_usd_per_million_tokens": resolved.output_usd_per_million_tokens,
        "context_window_limit": resolved.context_window_limit,
        "rate_basis": resolved.rate_basis,
        "window_basis": resolved.window_basis,
    }
    return hashlib.sha256(
        json.dumps(identity, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _governed(
    *,
    port: Any,
    price: Any,
    inner: Any,
    admit_planning_rates: bool,
    admitted_row_sha256: str,
    on_reserved: Any = None,
) -> GovernedLlmClient:
    return GovernedLlmClient(
        inner=inner,
        port=port,
        price=price,
        tenant_id=_TENANT,
        policy_hash_provider=lambda: _POLICY,
        global_max_calls=_GLOBAL_MAX_CALLS,
        reservation_id_factory=lambda: _RESERVATION,
        admitted_row_sha256=admitted_row_sha256,
        admit_planning_rates=admit_planning_rates,
        on_reserved=on_reserved,
    )


def _sample_vector(
    *,
    usd: Decimal,
    reservation_id: str = _RESERVATION,
) -> dict[str, object]:
    return {
        "tenant_id": _TENANT,
        "policy_hash": _POLICY,
        "component_id": "anthropic",
        "calls": 1,
        "usd": usd,
        "input_tokens": 10,
        "output_tokens": 5,
        "embedding_tokens": 0,
        "global_max_calls": _GLOBAL_MAX_CALLS,
        "reservation_id": reservation_id,
    }


def _usage(**overrides: Any) -> LlmUsage:
    values: dict[str, Any] = {
        "input_tokens": 1,
        "output_tokens": 1,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 0,
        "cache_creation_5m_input_tokens": 0,
        "cache_creation_1h_input_tokens": 0,
        "thinking_tokens": 0,
        "service_tier": "standard",
        "inference_geo": None,
    }
    values.update(overrides)
    return LlmUsage(**values)


def _assert_code(exc: BaseException, code: str) -> None:
    assert getattr(exc, "code", None) == code
    assert str(exc) == code


def _assert_detached(exc: BaseException) -> None:
    assert exc.__cause__ is None
    assert exc.__context__ is None


def _settlement_override(fault: bool | str) -> bool | BaseException:
    if fault is False:
        return False
    return RuntimeError("ledger-rejected-settlement")


def _assert_settlement_failure(
    exc: BaseException,
    *,
    intended: str,
    provider_code: str | None,
    prompt: str,
) -> None:
    assert isinstance(exc, LlmBudgetSettlementFailed)
    _assert_code(exc, "LLM_BUDGET_SETTLEMENT_FAILED")
    _assert_detached(exc)
    assert exc.reservation_id == _RESERVATION
    assert exc.intended == intended
    assert exc.provider_code == provider_code
    assert prompt not in str(exc)
    assert prompt not in repr(exc)
    assert _TEXT not in str(exc)


def _expected_vector_sha256(vector: Mapping[str, object]) -> str:
    rendered = {
        key: format(Decimal(str(value)), "f") if key == "usd" else value
        for key, value in vector.items()
    }
    encoded = json.dumps(rendered, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class _ScriptedClient:
    """Inner client with a fixed prepared size and a scripted typed result."""

    def __init__(
        self,
        *,
        payload_bytes: int,
        max_tokens: int,
        error: BaseException | None = None,
        usage: dict[str, Any] | None = None,
        model: str = "claude-sonnet-5-5",
        timeline: list[str] | None = None,
    ) -> None:
        self.payload_bytes = payload_bytes
        self.max_tokens = max_tokens
        self.error = error
        self.usage = usage or {}
        self.model = model
        self._timeline = timeline
        self.events: list[tuple[str, str, bool]] = []

    def prepare_request(self, prompt: str, *, json_mode: bool = False) -> Any:
        self.events.append(("prepare", prompt, json_mode))
        self._mark("prepare")
        return type(
            "Prepared",
            (),
            {
                "payload_bytes": self.payload_bytes,
                "max_tokens": self.max_tokens,
                "model": self.model,
            },
        )()

    def call_typed(self, prompt: str, *, json_mode: bool = False) -> LlmResult:
        self.events.append(("call", prompt, json_mode))
        self._mark("call")
        if self.error is not None:
            raise self.error
        return LlmResult(
            text=_TEXT,
            stop_reason="end_turn",
            usage=_usage(**self.usage),
            request_id="req_gl05_p5",
            model="claude-sonnet-5-5",
            thinking_block_count=0,
            redacted_thinking_block_count=0,
        )

    def _mark(self, name: str) -> None:
        if self._timeline is not None:
            self._timeline.append(name)


class _RaisingPort:
    """Reserve raises a budget denial. Consume and refund are unused."""

    def __init__(self, error: BaseException) -> None:
        self._error = error

    def reserve(self, vector: Mapping[str, object]) -> bool:
        del vector
        raise self._error

    def consume(self, vector: Mapping[str, object]) -> bool:
        del vector
        raise AssertionError("consume")

    def refund(self, vector: Mapping[str, object]) -> bool:
        del vector
        raise AssertionError("refund")


class _LedgerPort:
    """In-memory governed port with validation, exact vectors, one terminal, and caps."""

    def __init__(
        self,
        *,
        max_calls: int,
        max_usd: Decimal,
        consume_override: bool | BaseException | None = None,
        refund_override: bool | BaseException | None = None,
        timeline: list[str] | None = None,
    ) -> None:
        self._max_calls = max_calls
        self._max_usd = max_usd
        self._consume_override = consume_override
        self._refund_override = refund_override
        self._timeline = timeline
        self.operations: list[tuple[str, dict[str, object]]] = []
        self._rows: dict[str, tuple[str, dict[str, object]]] = {}
        self._reserved_calls = 0
        self._reserved_usd = Decimal("0")
        self._consumed_calls = 0
        self._consumed_usd = Decimal("0")

    def reserve(self, vector: Mapping[str, object]) -> bool:
        canonical = _canonical(vector)
        self.operations.append(("reserve", canonical))
        self._mark("reserve")
        reservation_id = str(canonical["reservation_id"])
        existing = self._rows.get(reservation_id)
        if existing is not None:
            state, stored = existing
            if not _same_vector(stored, canonical) or state != "RESERVED":
                raise ProviderBudgetPolicyError(PROVIDER_BUDGET_CONFIG_BLOCKED)
            return True
        calls = int(canonical["calls"])
        usd = Decimal(str(canonical["usd"]))
        if (
            self._reserved_calls + self._consumed_calls + calls > self._max_calls
            or self._reserved_usd + self._consumed_usd + usd > self._max_usd
        ):
            return False
        self._rows[reservation_id] = ("RESERVED", canonical)
        self._reserved_calls += calls
        self._reserved_usd += usd
        return True

    def consume(self, vector: Mapping[str, object]) -> bool:
        if self._consume_override is not None:
            return self._overridden("consume", vector, self._consume_override)
        return self._terminal("consume", "CONSUMED", vector)

    def refund(self, vector: Mapping[str, object]) -> bool:
        if self._refund_override is not None:
            return self._overridden("refund", vector, self._refund_override)
        return self._terminal("refund", "REFUNDED", vector)

    def state_of(self, reservation_id: str) -> str:
        return self._rows[reservation_id][0]

    def _overridden(
        self,
        name: str,
        vector: Mapping[str, object],
        override: bool | BaseException,
    ) -> bool:
        canonical = _canonical(vector)
        self.operations.append((name, canonical))
        self._mark(name)
        if isinstance(override, BaseException):
            raise override
        return override

    def _mark(self, name: str) -> None:
        if self._timeline is not None:
            self._timeline.append(name)

    def _terminal(self, name: str, target: str, vector: Mapping[str, object]) -> bool:
        canonical = _canonical(vector)
        self.operations.append((name, canonical))
        self._mark(name)
        reservation_id = str(canonical["reservation_id"])
        existing = self._rows.get(reservation_id)
        if existing is None:
            raise ProviderBudgetPolicyError(PROVIDER_BUDGET_CONFIG_BLOCKED)
        state, stored = existing
        if not _same_vector(stored, canonical):
            raise ProviderBudgetPolicyError(PROVIDER_BUDGET_CONFIG_BLOCKED)
        if state == target:
            return True
        if state != "RESERVED":
            raise ProviderBudgetPolicyError(PROVIDER_BUDGET_CONFIG_BLOCKED)
        calls = int(stored["calls"])
        usd = Decimal(str(stored["usd"]))
        self._reserved_calls -= calls
        self._reserved_usd -= usd
        if target == "CONSUMED":
            self._consumed_calls += calls
            self._consumed_usd += usd
        self._rows[reservation_id] = (target, stored)
        return True


def _canonical(vector: Mapping[str, object]) -> dict[str, object]:
    return _validate_governed_values(vector)


def _same_vector(left: Mapping[str, object], right: Mapping[str, object]) -> bool:
    return _ledger_key(left) == _ledger_key(right)


def _ledger_key(governed: Mapping[str, object]) -> tuple[tuple[str, str], ...]:
    rendered = {
        key: format(Decimal(str(governed["usd"])), "f") if key == "usd" else str(value)
        for key, value in governed.items()
    }
    return tuple(sorted(rendered.items()))
