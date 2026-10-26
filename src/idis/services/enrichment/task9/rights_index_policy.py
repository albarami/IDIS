"""Closed, hash-bound policy registry for external-context indexing rights."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from types import MappingProxyType
from typing import Final

from idis.services.enrichment.models import RightsClass
from idis.services.enrichment.task9.provider_catalog import (
    PROVIDER_CATALOG,
    RightsRequirement,
)

RIGHTS_INDEX_POLICY_REGISTRY_VERSION: Final = "external-context-rights-index-policy-v1"
EXTERNAL_CONTEXT_STORE_POLICY_V1: Final = "external-context-store-v1"
EXTERNAL_CONTEXT_NO_STORE_POLICY_V1: Final = "external-context-no-store-v1"
ATTRIBUTION_NOT_REQUIRED_POLICY_V1: Final = "attribution-not-required-v1"
ATTRIBUTION_BOUND_POLICY_V1: Final = "attribution-bound-v1"


@dataclass(frozen=True, slots=True)
class ProviderRightsIndexPolicy:
    """One literal effective-rights profile for an applicable provider."""

    provider_id: str
    rights_class: RightsClass
    rights_requirement: RightsRequirement
    no_store_policy_version: str
    attribution_policy_version: str
    external_context_index_allowed: bool


def _policy_for(provider_id: str) -> ProviderRightsIndexPolicy:
    spec = PROVIDER_CATALOG[provider_id]
    no_store = (
        EXTERNAL_CONTEXT_NO_STORE_POLICY_V1
        if provider_id == "perplexity"
        else EXTERNAL_CONTEXT_STORE_POLICY_V1
    )
    attribution = (
        ATTRIBUTION_BOUND_POLICY_V1
        if spec.rights_requirement is RightsRequirement.ATTRIBUTION_PROD
        or provider_id == "perplexity"
        else ATTRIBUTION_NOT_REQUIRED_POLICY_V1
    )
    return ProviderRightsIndexPolicy(
        provider_id=provider_id,
        rights_class=spec.rights_class,
        rights_requirement=spec.rights_requirement,
        no_store_policy_version=no_store,
        attribution_policy_version=attribution,
        external_context_index_allowed=(
            spec.rag_external_context and no_store == EXTERNAL_CONTEXT_STORE_POLICY_V1
        ),
    )


PROVIDER_RIGHTS_INDEX_POLICY_REGISTRY: Final = MappingProxyType(
    {provider_id: _policy_for(provider_id) for provider_id in PROVIDER_CATALOG}
)


def rights_index_policy_registry_rows() -> tuple[tuple[object, ...], ...]:
    """Return the complete normalized registry tuple in catalog order."""

    return tuple(
        (
            ordinal,
            policy.provider_id,
            policy.rights_class.value,
            policy.rights_requirement.value,
            policy.no_store_policy_version,
            policy.attribution_policy_version,
            policy.external_context_index_allowed,
        )
        for ordinal, policy in enumerate(PROVIDER_RIGHTS_INDEX_POLICY_REGISTRY.values())
    )


def rights_index_policy_registry_sha256() -> str:
    """Hash the normalized registry without importing runtime admission code."""

    payload = [RIGHTS_INDEX_POLICY_REGISTRY_VERSION, rights_index_policy_registry_rows()]
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
