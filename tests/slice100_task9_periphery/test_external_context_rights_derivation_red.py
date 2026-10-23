"""RED contracts for governed external-context indexing rights.

The rights row, rather than a caller-provided boolean, is the sole authority for
whether one provider outcome may enter Graph/RAG external context.
"""

from __future__ import annotations

from dataclasses import fields, replace
from datetime import UTC, datetime, timedelta

import pytest

from idis.services.enrichment.models import RightsClass
from idis.services.enrichment.task9.provider_admission import (
    RightsSnapshot,
    RightsStatus,
    derive_external_context_index_allowed,
)
from idis.services.enrichment.task9.provider_catalog import (
    PROVIDER_CATALOG,
    RightsRequirement,
)
from idis.services.enrichment.task9.rights_index_policy import (
    ATTRIBUTION_BOUND_POLICY_V1,
    ATTRIBUTION_NOT_REQUIRED_POLICY_V1,
    EXTERNAL_CONTEXT_NO_STORE_POLICY_V1,
    EXTERNAL_CONTEXT_STORE_POLICY_V1,
    PROVIDER_RIGHTS_INDEX_POLICY_REGISTRY,
    rights_index_policy_registry_sha256,
)

_NOW = datetime(2026, 7, 22, tzinfo=UTC)


def _rights_row(provider_id: str) -> RightsSnapshot:
    spec = PROVIDER_CATALOG[provider_id]
    policy = PROVIDER_RIGHTS_INDEX_POLICY_REGISTRY[provider_id]
    return RightsSnapshot(
        tenant_id="tenant-001",
        rights_approval_id=f"rights-{provider_id}-001",
        provider_id=provider_id,
        environment="PROD",
        rights_class=spec.rights_class,
        rights_requirement=spec.rights_requirement,
        status=RightsStatus.ACTIVE,
        rights_version_hash="a" * 64,
        credential_version_id=(
            f"credential-{provider_id}-001"
            if spec.rights_requirement is RightsRequirement.COMMERCIAL_PROD
            else None
        ),
        expires_at=_NOW + timedelta(days=30),
        no_store_policy_version=policy.no_store_policy_version,
        attribution_policy_version=policy.attribution_policy_version,
    )


def test_all_18_provider_flags_derive_from_the_closed_rights_registry() -> None:
    expected = {provider_id: provider_id != "perplexity" for provider_id in PROVIDER_CATALOG}

    assert tuple(PROVIDER_RIGHTS_INDEX_POLICY_REGISTRY) == tuple(PROVIDER_CATALOG)
    assert len(PROVIDER_RIGHTS_INDEX_POLICY_REGISTRY) == 18
    assert {
        provider_id: derive_external_context_index_allowed(_rights_row(provider_id))
        for provider_id in PROVIDER_CATALOG
    } == expected
    assert len(rights_index_policy_registry_sha256()) == 64


def test_rights_snapshot_has_policy_versions_and_no_caller_selected_boolean() -> None:
    names = {field.name for field in fields(RightsSnapshot)}

    assert "no_store_policy_version" in names
    assert "attribution_policy_version" in names
    assert "rag_index_allowed" not in names
    assert "external_context_index_allowed" not in names


@pytest.mark.parametrize(
    ("provider_id", "no_store_policy_version", "attribution_policy_version"),
    [
        ("sec_edgar", EXTERNAL_CONTEXT_STORE_POLICY_V1, ATTRIBUTION_NOT_REQUIRED_POLICY_V1),
        ("wayback", EXTERNAL_CONTEXT_STORE_POLICY_V1, ATTRIBUTION_BOUND_POLICY_V1),
        ("perplexity", EXTERNAL_CONTEXT_NO_STORE_POLICY_V1, ATTRIBUTION_BOUND_POLICY_V1),
    ],
)
def test_registry_profiles_are_literal_and_provider_class_constrained(
    provider_id: str,
    no_store_policy_version: str,
    attribution_policy_version: str,
) -> None:
    policy = PROVIDER_RIGHTS_INDEX_POLICY_REGISTRY[provider_id]
    assert policy.no_store_policy_version == no_store_policy_version
    assert policy.attribution_policy_version == attribution_policy_version


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("no_store_policy_version", "unknown-no-store-policy"),
        ("attribution_policy_version", "unknown-attribution-policy"),
        ("rights_class", RightsClass.GREEN),
        ("rights_requirement", RightsRequirement.NONE),
    ],
)
def test_unknown_or_provider_mismatched_rights_policy_fails_closed(
    field_name: str,
    value: object,
) -> None:
    row = replace(_rights_row("perplexity"), **{field_name: value})

    with pytest.raises(Exception) as caught:
        derive_external_context_index_allowed(row)

    assert getattr(caught.value, "code", None) == "ENRICHMENT_LICENSE_BLOCKED"


def test_successor_rights_row_can_flip_later_run_without_mutating_prior_row() -> None:
    first = replace(
        _rights_row("perplexity"),
        rights_approval_id="rights-perplexity-store",
        rights_version_hash="1" * 64,
        no_store_policy_version=EXTERNAL_CONTEXT_STORE_POLICY_V1,
    )
    successor = replace(
        first,
        rights_approval_id="rights-perplexity-no-store",
        rights_version_hash="2" * 64,
        no_store_policy_version=EXTERNAL_CONTEXT_NO_STORE_POLICY_V1,
    )

    assert derive_external_context_index_allowed(first) is True
    assert derive_external_context_index_allowed(successor) is False
    assert first.rights_version_hash == "1" * 64
    assert first.no_store_policy_version == EXTERNAL_CONTEXT_STORE_POLICY_V1
