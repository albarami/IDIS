"""Synthetic source-freeze/admission mechanics, not product ingestion/FULL evidence.

All writes use the application role. Existing source/freeze/admission algorithms
are composed unchanged; only a repository-local contract copy is made ACTIVE.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from types import MappingProxyType
from typing import Any

from sqlalchemy import Connection, Engine, text

from idis.persistence.db import set_tenant_local
from idis.persistence.repositories.strict_preflight_proofs import (
    PostgresStrictPreflightProofsRepository,
    StrictPreflightBinding,
)
from idis.persistence.repositories.task7_source_freeze import freeze_source_package
from idis.pipeline import contract_registry
from tests.slice100_task7.test_task7_source_freeze_postgres_red import _seed_open_source

VERSION = "full-strict-v21"


@dataclass(frozen=True)
class SyntheticFrozenSource:
    tenant_id: str
    deal_id: str
    package_id: str
    corpus_hash: str


@dataclass(frozen=True)
class SyntheticAdmissionProof:
    source: SyntheticFrozenSource
    proof_id: str
    binding: StrictPreflightBinding


def seed_synthetic_frozen_source(engine: Engine, *, label: str) -> SyntheticFrozenSource:
    """Reuse the reviewed staging fixture and real freeze, under app credentials."""
    with engine.begin() as conn:
        posture = conn.execute(
            text("SELECT rolsuper,rolbypassrls FROM pg_roles WHERE rolname=current_user")
        ).one()
        assert tuple(posture) == (False, False)
        values = _seed_open_source(conn, label=f"cir04c2-synthetic-{label}-{uuid.uuid4()}")
    freeze_source_package(
        engine=engine,
        tenant_id=values["tenant_id"],
        deal_id=values["deal_id"],
        package_id=values["package_id"],
    )
    with engine.begin() as conn:
        set_tenant_local(conn, values["tenant_id"])
        corpus_hash = conn.execute(
            text(
                "SELECT corpus_receipt_sha256 FROM source_lineage_objects "
                "WHERE tenant_id=CAST(:tenant_id AS uuid) AND deal_id=CAST(:deal_id AS uuid) "
                "AND corpus_receipt_id=CAST(:package_id AS uuid) "
                "AND object_kind='PACKAGE' AND write_origin='RUNTIME'"
            ),
            values,
        ).scalar_one()
    return SyntheticFrozenSource(
        values["tenant_id"], values["deal_id"], values["package_id"], str(corpus_hash)
    )


def insert_synthetic_proof(
    engine: Engine,
    source: SyntheticFrozenSource,
    *,
    version: str = VERSION,
    expires_at: datetime | None = None,
) -> SyntheticAdmissionProof:
    """Real corpus/contract binding; other hashes explicitly synthetic mechanics."""
    proof_id = str(uuid.uuid4())
    synthetic_hash = hashlib.sha256(f"synthetic-mechanics:{proof_id}".encode()).hexdigest()
    contract = contract_registry.PIPELINE_CONTRACTS.get(version)
    binding = StrictPreflightBinding(
        tenant_id=source.tenant_id,
        deal_id=source.deal_id,
        corpus_hash=source.corpus_hash,
        strict_profile_generation="bootstrap-v1",
        strict_profile_hmac=synthetic_hash,
        pipeline_contract_version=version,
        pipeline_contract_hash=contract.contract_hash
        if contract and contract.contract_hash
        else synthetic_hash,
        expected_consumer_edge_rule_hash=synthetic_hash,
        static_decision_set_hash=synthetic_hash,
        methodology_version_hashes=(synthetic_hash,),
        promoted_pointer_hash=synthetic_hash,
        provider_receipt_set_hash=synthetic_hash,
        dependency_receipt_set_hash=synthetic_hash,
    )
    with engine.begin() as conn:
        set_tenant_local(conn, source.tenant_id)
        conn.execute(
            text(
                "INSERT INTO strict_preflight_proofs ("
                "proof_id,tenant_id,deal_id,corpus_hash,strict_profile_generation,"
                "strict_profile_hmac,pipeline_contract_version,pipeline_contract_hash,"
                "expected_consumer_edge_rule_hash,static_decision_set_hash,"
                "methodology_version_hashes,promoted_pointer_hash,provider_receipt_set_hash,"
                "dependency_receipt_set_hash,binding_hash,created_at,expires_at) VALUES ("
                "CAST(:proof_id AS uuid),CAST(:tenant_id AS uuid),CAST(:deal_id AS uuid),"
                ":corpus_hash,:strict_profile_generation,:strict_profile_hmac,"
                ":pipeline_contract_version,:pipeline_contract_hash,"
                ":expected_consumer_edge_rule_hash,:static_decision_set_hash,"
                "CAST(:methodology_version_hashes AS jsonb),:promoted_pointer_hash,"
                ":provider_receipt_set_hash,:dependency_receipt_set_hash,:binding_hash,:created_at,:expires_at)"
            ),
            {
                **binding.__dict__,
                "proof_id": proof_id,
                "methodology_version_hashes": json.dumps(binding.methodology_version_hashes),
                "binding_hash": binding.digest(),
                "created_at": (expires_at or datetime.now(UTC)) - timedelta(minutes=1),
                "expires_at": expires_at or datetime.now(UTC) + timedelta(hours=1),
            },
        )
    return SyntheticAdmissionProof(source, proof_id, binding)


def admission_repository(
    conn: Connection, tenant_id: str
) -> PostgresStrictPreflightProofsRepository:
    """Per-instance hermetic admission only; leave the global registry untouched."""
    contract = contract_registry.PIPELINE_CONTRACTS[VERSION]
    registry = dict(contract_registry.PIPELINE_CONTRACTS)
    registry[VERSION] = replace(contract, state=contract_registry.PipelineContractState.ACTIVE)
    return PostgresStrictPreflightProofsRepository(
        conn, tenant_id, pipeline_contract_registry=MappingProxyType(registry)
    )


def admit_synthetic_proof(
    repository: PostgresStrictPreflightProofsRepository,
    proof: SyntheticAdmissionProof,
    run_id: str,
    *,
    actor_id: str = "cir04c2-synthetic-mechanics",
    actor_type: str = "SERVICE",
) -> dict[str, Any]:
    return repository.consume_and_admit_contract_run(
        proof_id=proof.proof_id,
        expected_binding=proof.binding,
        run_id=run_id,
        deal_id=proof.source.deal_id,
        mode="FULL",
        idempotency_key=f"cir04c2-mechanics-{run_id}",
        source=None,
        created_by_actor_id=actor_id,
        created_by_actor_type=actor_type,
        selected_source_package_ids=(proof.source.package_id,),
    )
