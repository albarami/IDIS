"""Deterministic UUID aliases for Slice 11 Muhasabah validation."""

from __future__ import annotations

import json
from collections.abc import Iterable
from enum import StrEnum
from uuid import UUID, uuid5

from pydantic import BaseModel, ConfigDict, Field

EVIDENCE_TRUST_ALIAS_NAMESPACE = UUID("06670d05-522c-5fed-9af0-f66b818e2e11")


class EvidenceTrustIdType(StrEnum):
    """Supported deterministic alias ID domains."""

    CLAIM = "claim"
    CALC = "calc"


class EvidenceTrustAliasMaps(BaseModel):
    """Deterministic internal UUID alias maps for Muhasabah validation."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    tenant_id: str
    deal_id: str
    run_id: str
    claim_aliases: dict[str, str] = Field(default_factory=dict)
    calc_aliases: dict[str, str] = Field(default_factory=dict)

    def resolve(self, alias: str) -> tuple[EvidenceTrustIdType, str] | None:
        """Map a UUID alias back to its safe run-scoped ID."""
        claim_reverse = {value: key for key, value in self.claim_aliases.items()}
        if alias in claim_reverse:
            return EvidenceTrustIdType.CLAIM, claim_reverse[alias]
        calc_reverse = {value: key for key, value in self.calc_aliases.items()}
        if alias in calc_reverse:
            return EvidenceTrustIdType.CALC, calc_reverse[alias]
        return None


def build_evidence_trust_alias_maps(
    *,
    tenant_id: str,
    deal_id: str,
    run_id: str,
    claim_ids: Iterable[str],
    calc_ids: Iterable[str],
) -> EvidenceTrustAliasMaps:
    """Build deterministic UUID aliases for run-scoped claim and calc IDs."""
    claim_aliases = {
        claim_id: _alias_uuid(
            tenant_id=tenant_id,
            deal_id=deal_id,
            run_id=run_id,
            id_type=EvidenceTrustIdType.CLAIM,
            run_scoped_id=claim_id,
        )
        for claim_id in sorted({item for item in claim_ids if item.strip()})
    }
    calc_aliases = {
        calc_id: _alias_uuid(
            tenant_id=tenant_id,
            deal_id=deal_id,
            run_id=run_id,
            id_type=EvidenceTrustIdType.CALC,
            run_scoped_id=calc_id,
        )
        for calc_id in sorted({item for item in calc_ids if item.strip()})
    }
    return EvidenceTrustAliasMaps(
        tenant_id=tenant_id,
        deal_id=deal_id,
        run_id=run_id,
        claim_aliases=claim_aliases,
        calc_aliases=calc_aliases,
    )


def _alias_uuid(
    *,
    tenant_id: str,
    deal_id: str,
    run_id: str,
    id_type: EvidenceTrustIdType,
    run_scoped_id: str,
) -> str:
    return str(
        uuid5(
            EVIDENCE_TRUST_ALIAS_NAMESPACE,
            json.dumps(
                {
                    "tenant_id": tenant_id,
                    "deal_id": deal_id,
                    "run_id": run_id,
                    "id_type": id_type.value,
                    "run_scoped_id": run_scoped_id,
                },
                sort_keys=True,
                separators=(",", ":"),
            ),
        )
    )
