"""GL-09 slice 1: enrichment query identity comes from the deal company label."""

from __future__ import annotations

from typing import Any

import pytest

from idis.services.enrichment.models import (
    CachePolicyConfig,
    EnrichmentContext,
    EnrichmentRequest,
    EnrichmentResult,
    EnrichmentStatus,
    RightsClass,
)
from idis.services.enrichment.registry import EnrichmentProviderRegistry
from idis.services.runs.orchestrator import RunStepBlockedError

TENANT_ID = "11111111-1111-1111-1111-111111111111"
DEAL_ID = "22222222-2222-2222-2222-222222222222"
RUN_ID = "33333333-3333-3333-3333-333333333333"
LABEL = "Northwind Analytics"
BLANK_CODE = "ENRICHMENT_DEAL_LABEL_BLANK"
BLANK_MESSAGE = "enrichment requires a nonblank explicit deal company label"


class _RecordingConnector:
    """Records fetch requests and returns a miss. Never contacts a provider."""

    def __init__(self) -> None:
        self.requests: list[EnrichmentRequest] = []

    @property
    def provider_id(self) -> str:
        return "recording-provider"

    @property
    def rights_class(self) -> RightsClass:
        return RightsClass.GREEN

    @property
    def cache_policy(self) -> CachePolicyConfig:
        return CachePolicyConfig(ttl_seconds=0, no_store=True)

    def fetch(self, request: EnrichmentRequest, ctx: EnrichmentContext) -> EnrichmentResult:
        self.requests.append(request)
        return EnrichmentResult(status=EnrichmentStatus.MISS)


def _install_connector(monkeypatch: pytest.MonkeyPatch, connector: _RecordingConnector) -> None:
    def _registry() -> EnrichmentProviderRegistry:
        registry = EnrichmentProviderRegistry()
        registry.register(connector)
        return registry

    monkeypatch.setattr(
        "idis.services.enrichment.service._build_default_registry",
        _registry,
    )
    monkeypatch.delenv("IDIS_REQUIRE_FULL_LIVE", raising=False)
    monkeypatch.delenv("IDIS_STRICT_DOTENV_PATH", raising=False)


def _run(**kwargs: Any) -> dict[str, Any]:
    from idis.api.routes.runs import _run_full_enrichment

    return _run_full_enrichment(
        run_id=RUN_ID,
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        created_claim_ids=[],
        calc_ids=[],
        db_conn=None,
        **kwargs,
    )


def test_request_company_name_is_deal_label_not_deal_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The enrichment request carries the deal label, not the deal id."""
    connector = _RecordingConnector()
    _install_connector(monkeypatch, connector)

    _run(deal_metadata={"company_name": f"  {LABEL}  "})

    assert len(connector.requests) == 1
    company_name = connector.requests[0].query.company_name
    assert company_name == LABEL
    assert company_name != DEAL_ID


@pytest.mark.parametrize(
    "deal_metadata",
    [
        None,
        {},
        {"company_name": ""},
        {"company_name": "   "},
        {"sector": "Software"},
    ],
)
def test_blank_or_missing_label_fails_closed_before_provider_call(
    monkeypatch: pytest.MonkeyPatch,
    deal_metadata: dict[str, Any] | None,
) -> None:
    """A blank or missing company label blocks the step before any provider fetch."""
    connector = _RecordingConnector()
    _install_connector(monkeypatch, connector)

    with pytest.raises(RunStepBlockedError) as raised:
        _run(deal_metadata=deal_metadata)

    assert connector.requests == []
    assert raised.value.code == BLANK_CODE
    assert str(raised.value) == BLANK_MESSAGE


def test_omitted_metadata_fails_closed_before_provider_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Omitting deal metadata is a missing label and blocks before any provider fetch."""
    connector = _RecordingConnector()
    _install_connector(monkeypatch, connector)

    with pytest.raises(RunStepBlockedError) as raised:
        _run()

    assert connector.requests == []
    assert raised.value.code == BLANK_CODE
    assert str(raised.value) == BLANK_MESSAGE


def test_enrich_partial_carries_deal_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    """The FULL enrichment partial carries deal metadata the way the debate partial does."""
    from idis.audit.sink import InMemoryAuditSink
    from idis.services.runs.steps import build_run_context

    metadata = {"company_name": LABEL, "sector": "Software"}
    context_obj = build_run_context(
        db_conn=None,
        tenant_id=TENANT_ID,
        run_id=RUN_ID,
        deal_id=DEAL_ID,
        mode="FULL",
        documents=[],
        deal_metadata=metadata,
        audit_sink=InMemoryAuditSink(),
    )
    assert context_obj.enrich_fn is not None
    assert context_obj.enrich_fn.keywords.get("deal_metadata") == metadata

    connector = _RecordingConnector()
    _install_connector(monkeypatch, connector)
    context_obj.enrich_fn(
        run_id=RUN_ID,
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        created_claim_ids=[],
        calc_ids=[],
    )
    assert len(connector.requests) == 1
    assert connector.requests[0].query.company_name == LABEL
    assert connector.requests[0].query.company_name != DEAL_ID
