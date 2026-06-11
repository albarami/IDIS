"""Slice86 Task 2 — per-provider optional-vs-fatal strict policy (RED-first).

Implements master-plan acceptance (1): "Provider errors are fatal in strict mode unless policy
says optional." Decisions D-B/D-C locked: ``optional_in_strict: bool = False`` lives on
``ProviderDescriptor`` and is set at registration; **all 15 default providers stay mandatory**
(today's strict semantics preserved). In strict FULL, an OPTIONAL provider's error/blocked/
exception is recorded-and-continued (same handling as the non-strict path; the rich per-provider
ledger is Task 3), while a MANDATORY provider's failure stays fatal exactly as today. The strict
provider matrix surfaces the policy (additive ``optional_in_strict`` field; optional providers
report ``strict_behavior="strict_optional_continue_on_error"``). No real provider calls, no DB,
no ledger/source-grade/deliverables/conflict/hardening work (Tasks 3-6), no Slice87.
"""

from __future__ import annotations

import dataclasses
import os
from typing import Any
from unittest.mock import patch

import pytest

from idis.audit.sink import InMemoryAuditSink
from idis.persistence.repositories.enrichment_credentials import InMemoryCredentialRepository
from idis.services.enrichment.cache_policy import EnrichmentCacheStore
from idis.services.enrichment.models import EnrichmentStatus
from idis.services.enrichment.registry import EnrichmentProviderRegistry, ProviderDescriptor
from idis.services.enrichment.rights_gate import EnvironmentMode
from idis.services.enrichment.service import EnrichmentService, _build_default_registry
from tests.test_slice86_enrichment_execution_provenance_characterization import (
    TENANT_ID,
    _env_without,
    _StatusConnector,
)

_LEAK_MARKERS = ("sk-s86-opt-LEAK-1", "/var/secret/s86", "C:\\secret\\s86")


def _registry_with(
    *entries: tuple[Any, bool, bool],
) -> EnrichmentProviderRegistry:
    registry = EnrichmentProviderRegistry()
    for connector, requires_byol, optional in entries:
        registry.register(connector, requires_byol=requires_byol, optional_in_strict=optional)
    return registry


def _run_strict(registry_factory: Any) -> dict[str, Any]:
    from idis.api.routes.runs import _run_full_enrichment

    env = _env_without("IDIS_REQUIRE_FULL_LIVE", "IDIS_STRICT_DOTENV_PATH")
    with (
        patch.dict(os.environ, env, clear=True),
        patch("idis.services.enrichment.service._build_default_registry", registry_factory),
        patch("idis.api.routes.runs.is_strict_full_live_required", return_value=True),
    ):
        return _run_full_enrichment(
            run_id="run-1",
            tenant_id=TENANT_ID,
            deal_id="deal-1",
            created_claim_ids=[],
            calc_ids=[],
            db_conn=None,
        )


# --- descriptor + registration carry the policy flag (default False) ---


def test_descriptor_and_register_accept_optional_in_strict() -> None:
    field_defaults = {f.name: f.default for f in dataclasses.fields(ProviderDescriptor)}
    assert field_defaults.get("optional_in_strict") is False

    registry = _registry_with(
        (_StatusConnector("opt_provider", EnrichmentStatus.HIT), False, True),
    )
    assert registry.get("opt_provider").optional_in_strict is True

    default_registry = EnrichmentProviderRegistry()
    default_registry.register(_StatusConnector("plain", EnrichmentStatus.HIT))
    assert default_registry.get("plain").optional_in_strict is False  # default unchanged


def test_all_default_providers_remain_mandatory() -> None:
    descriptors = _build_default_registry().list_providers()
    assert len(descriptors) == 15
    assert all(d.optional_in_strict is False for d in descriptors)  # D-C: no defaults flipped


def test_service_list_providers_exposes_optional_flag() -> None:
    registry = _registry_with(
        (_StatusConnector("opt_provider", EnrichmentStatus.HIT), False, True),
        (_StatusConnector("mand_provider", EnrichmentStatus.HIT), False, False),
    )
    service = EnrichmentService(
        registry=registry,
        audit_sink=InMemoryAuditSink(),
        credential_repo=InMemoryCredentialRepository(),
        cache_store=EnrichmentCacheStore(),
        environment=EnvironmentMode.DEV,
    )
    flags = {p["provider_id"]: p["optional_in_strict"] for p in service.list_providers()}
    assert flags == {"opt_provider": True, "mand_provider": False}


# --- strict differential: optional failures continue; mandatory failures stay fatal ---


@pytest.mark.parametrize(
    ("connector", "requires_byol", "expected_blocked"),
    [
        (_StatusConnector("opt_error", EnrichmentStatus.ERROR), False, 0),
        (_StatusConnector("opt_byol", EnrichmentStatus.HIT), True, 1),  # blocked: missing BYOL
        (_StatusConnector("opt_raise", EnrichmentStatus.HIT, raises=True), False, 0),
    ],
)
def test_strict_optional_failures_are_not_fatal(
    connector: Any, requires_byol: bool, expected_blocked: int
) -> None:
    summary = _run_strict(lambda: _registry_with((connector, requires_byol, True)))
    assert summary["provider_count"] == 1
    assert summary["result_count"] == 0
    assert summary["blocked_count"] == expected_blocked  # non-strict handling, recorded-continued


def test_strict_mixed_mandatory_failure_still_fatal() -> None:
    def registry() -> EnrichmentProviderRegistry:
        return _registry_with(
            (_StatusConnector("opt_error", EnrichmentStatus.ERROR), False, True),
            (_StatusConnector("mand_error", EnrichmentStatus.ERROR), False, False),
        )

    with pytest.raises(RuntimeError) as exc_info:
        _run_strict(registry)
    assert "mand_error" in str(exc_info.value)


def test_strict_mandatory_fatality_regression_with_optional_sibling() -> None:
    # A healthy optional sibling must not soften the mandatory provider's fatality.
    def registry() -> EnrichmentProviderRegistry:
        return _registry_with(
            (_StatusConnector("opt_hit", EnrichmentStatus.HIT), False, True),
            (_StatusConnector("mand_raise", EnrichmentStatus.HIT, raises=True), False, False),
        )

    with pytest.raises(RuntimeError) as exc_info:
        _run_strict(registry)
    assert "mand_raise" in str(exc_info.value)


# --- leak safety on the optional failure path ---


def test_optional_failure_path_is_leak_free() -> None:
    confidential = " ".join(_LEAK_MARKERS)

    class _LeakyRaise(_StatusConnector):
        def fetch(self, request: Any, ctx: Any) -> Any:
            raise RuntimeError(confidential)

    def registry() -> EnrichmentProviderRegistry:
        return _registry_with(
            (_LeakyRaise("opt_leaky", EnrichmentStatus.HIT), False, True),
        )

    summary = _run_strict(registry)  # must NOT raise
    blob = repr(summary)
    for marker in _LEAK_MARKERS:
        assert marker not in blob


# --- strict matrix surfaces the policy ---


def test_matrix_surfaces_optional_policy() -> None:
    from idis.services.enrichment.byol_credentials import build_enrichment_provider_matrix

    optional_registry = _registry_with(
        (_StatusConnector("opt_provider", EnrichmentStatus.HIT), False, True),
        (_StatusConnector("mand_provider", EnrichmentStatus.HIT), False, False),
    )
    matrix = build_enrichment_provider_matrix(
        byol_providers=[],
        provider_descriptors=optional_registry.list_providers(),
    )
    by_id = {entry.provider_id: entry for entry in matrix}
    assert by_id["opt_provider"].optional_in_strict is True
    assert by_id["opt_provider"].strict_behavior == "strict_optional_continue_on_error"
    assert by_id["mand_provider"].optional_in_strict is False
    assert by_id["mand_provider"].strict_behavior == "strict_fail_closed_on_error"
