"""Slice92 Task 2 — migration 0021 + safe-shape rows + Layer-1 evidence twin repositories.

DEC-A/DEC-C (locked): persist the SAFE shapes of the Layer-1 court/VEP output — IDs,
dispositions, grades, finding types, reason codes, and aggregates; structured Muḥāsabah
fields (agent/output ids, confidence, subjectivity, supported ids, uncertainty/impact/
mitigation triples) — never claim text, transcripts, falsifiability narrative, failure-mode
prose, or recommendations. Deterministic UUIDs make every upsert idempotent (retry/resume
safe). Postgres repo follows the canonical conventions (set_tenant_local, NULLIF RLS,
ON CONFLICT upsert); the InMemory twin keeps non-DB runs hermetic (DEC-G).

Scope boundary: NO run-step wiring, NO Layer-2 threading, NO export — Tasks 3-5.

The Postgres roundtrip test is env-gated (skips without IDIS_DATABASE_URL /
IDIS_DATABASE_ADMIN_URL; runs in the postgres-integration CI job where `alembic upgrade
head` applies migration 0021).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from idis.models.evidence_trust_court_materialization import (
    EvidenceTrustFindingType,
    RunScopedEvidenceTrustCourtFinding,
)
from idis.models.muhasabah_record import (
    FalsifiabilityTest,
    MuhasabahRecordCanonical,
    Uncertainty,
)
from idis.models.validated_evidence_package_materialization import (
    MethodologyValidatedEvidencePackageStatus,
    RunScopedValidatedEvidencePackageRecord,
)

pytest_plugins = ("tests.test_ingestion_persists_documents_postgres",)

TENANT_ID = "11111111-1111-1111-1111-111111111111"
OTHER_TENANT_ID = "99999999-9999-9999-9999-999999999999"
DEAL_ID = "33333333-3333-3333-3333-333333333333"
RUN_ID = "22222222-2222-2222-2222-222222222222"
# Court/package/dashboard/sanad ids are bare UUID5 in production (verified generators).
COURT_ID = "44444444-4444-4444-4444-444444444444"
PACKAGE_ID = "55555555-5555-5555-5555-555555555555"
DASHBOARD_ID = "66666666-6666-6666-6666-666666666666"
SANAD_ID = "77777777-7777-7777-7777-777777777777"
# Claim/finding ids are NOT UUID-shaped in production: claims are
# ``claim_mth_<24 hex>`` (claim_materialization.py) and court findings are
# ``finding-<12 hex>`` (debate/roles/base.py deterministic_id). The schema must
# accept these actual shapes, so the fixtures use them verbatim.
CLAIM_ID = "claim_mth_0123456789abcdef01234567"
FINDING_ID = "finding-c41828c98cf5"

_MIGRATION = Path("src/idis/persistence/migrations/versions/0021_layer1_evidence_durability.py")

_VEP_SAFE_SUMMARY_KEYS = {
    "claim_ids_by_disposition",
    "evidence_ids",
    "source_span_ids",
    "sanad_ids",
    "defect_ids",
    "calc_ids",
    "finding_ids",
    "finding_types",
    "role_names",
    "reason_codes",
    "by_disposition",
    "by_grade",
    "by_dashboard_verdict",
    "by_finding_type",
    "by_reason",
}


def _vep_record(**overrides: Any) -> RunScopedValidatedEvidencePackageRecord:
    kwargs: dict[str, Any] = {
        "tenant_id": TENANT_ID,
        "deal_id": DEAL_ID,
        "run_id": RUN_ID,
        "package_id": PACKAGE_ID,
        "court_id": COURT_ID,
        "dashboard_id": DASHBOARD_ID,
        "claim_ids_by_disposition": {"TRUSTED": [CLAIM_ID]},
        "evidence_ids": ["evi-1"],
        "source_span_ids": ["span-1"],
        "sanad_ids": [SANAD_ID],
        "defect_ids": [],
        "calc_ids": ["calc-1"],
        "finding_ids": [FINDING_ID],
        "finding_types": ["contradiction"],
        "role_names": ["advocate"],
        "reason_codes": ["GRADE_B"],
        "by_disposition": {"TRUSTED": 1},
        "by_grade": {"B": 1},
        "by_dashboard_verdict": {"SUPPORTED": 1},
        "by_finding_type": {"contradiction": 1},
        "by_reason": {"GRADE_B": 1},
        "status": MethodologyValidatedEvidencePackageStatus.COMPLETED,
    }
    kwargs.update(overrides)
    return RunScopedValidatedEvidencePackageRecord(**kwargs)


def _court_finding() -> RunScopedEvidenceTrustCourtFinding:
    return RunScopedEvidenceTrustCourtFinding(
        finding_id=FINDING_ID,
        finding_type=EvidenceTrustFindingType.CONTRADICTION,
        claim_id=CLAIM_ID,
        evidence_ids=["evi-1"],
        sanad_id=SANAD_ID,
        calc_ids=["calc-1"],
        defect_ids=[],
        reason_codes=["NUMERIC_INCONSISTENCY"],
    )


def _muhasabah_record() -> MuhasabahRecordCanonical:
    return MuhasabahRecordCanonical(
        agent_id="layer1-advocate-01",
        output_id="out-000000000001",
        supported_claim_ids=[CLAIM_ID],
        supported_calc_ids=["calc-1"],
        falsifiability_tests=[
            FalsifiabilityTest(
                test_description="FREE TEXT MUST NOT PERSIST",
                required_evidence="FREE TEXT MUST NOT PERSIST",
                pass_fail_rule="FREE TEXT MUST NOT PERSIST",
            )
        ],
        uncertainties=[
            Uncertainty(
                uncertainty="Revenue recognition timing unclear",
                impact="HIGH",
                mitigation="Request audited statements",
            )
        ],
        confidence=0.9,
        failure_modes=["FREE TEXT MUST NOT PERSIST"],
        is_subjective=False,
        timestamp="2026-01-01T00:00:00+00:00",
    )


@pytest.fixture(autouse=True)
def _clear_layer1_store() -> None:
    from idis.persistence.repositories.layer1_evidence import (
        clear_in_memory_layer1_evidence_store,
    )

    clear_in_memory_layer1_evidence_store()


# --- Migration 0021: three tables, RLS NULLIF pattern, idempotent keys ---


def test_migration_0021_layer1_tables_with_rls_and_keys() -> None:
    assert _MIGRATION.exists()
    source = _MIGRATION.read_text(encoding="utf-8")
    assert 'revision = "0021"' in source
    assert 'down_revision = "0020"' in source
    for table in (
        "validated_evidence_packages",
        "evidence_trust_findings",
        "muhasabah_records",
    ):
        assert f"CREATE TABLE IF NOT EXISTS {table}" in source
        assert f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY" in source
        assert f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY" in source
        assert f"CREATE POLICY tenant_isolation_{table} ON {table}" in source
        assert f"DROP TABLE IF EXISTS {table} CASCADE" in source
    # Canonical fail-closed RLS predicate (NULLIF empty-tenant pattern).
    assert "NULLIF(current_setting('idis.tenant_id', true), '') IS NOT NULL" in source
    # Production-shaped string IDs must be stored as text, not UUID: claim ids are
    # claim_mth_<hex> and finding ids are finding-<hex>. Sanad ids are verified
    # bare UUID5 (deterministic_sanad_id) and stay UUID.
    assert "finding_id VARCHAR(255) NOT NULL" in source
    assert "claim_id VARCHAR(255) NOT NULL" in source
    assert "sanad_id UUID" in source
    # Finding ids are only 48 bits of entropy (finding-<uuid5_hex[:12]>), so the
    # findings PK MUST be tenant/run scoped: a bare global finding_id PK would let a
    # cross-tenant collision permanently block a run (RLS-invisible conflict target)
    # and a same-tenant cross-run collision silently absorb another run's finding.
    assert "PRIMARY KEY (tenant_id, run_id, finding_id)" in source
    repo_source = Path("src/idis/persistence/repositories/layer1_evidence.py").read_text(
        encoding="utf-8"
    )
    assert "ON CONFLICT (tenant_id, run_id, finding_id)" in repo_source


# --- Safe-shape rows: whitelist conversions from the existing in-memory records ---


def test_vep_row_from_record_whitelists_safe_fields() -> None:
    from idis.models.layer1_durability import ValidatedEvidencePackageRow

    row = ValidatedEvidencePackageRow.from_record(_vep_record())
    assert row.tenant_id == TENANT_ID
    assert row.deal_id == DEAL_ID
    assert row.run_id == RUN_ID
    assert row.package_id == PACKAGE_ID
    assert row.court_id == COURT_ID
    assert row.dashboard_id == DASHBOARD_ID
    assert row.status == "completed"  # enum .value convention is lowercase
    assert set(row.safe_summary) == _VEP_SAFE_SUMMARY_KEYS
    assert row.safe_summary["claim_ids_by_disposition"] == {"TRUSTED": [CLAIM_ID]}
    assert row.safe_summary["by_grade"] == {"B": 1}


def test_finding_row_from_court_finding_keeps_ids_and_codes_only() -> None:
    from idis.models.layer1_durability import EvidenceTrustFindingRow

    row = EvidenceTrustFindingRow.from_finding(
        _court_finding(),
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        court_id=COURT_ID,
    )
    assert row.finding_id == FINDING_ID
    assert row.finding_type == "contradiction"  # enum .value convention is lowercase
    assert row.claim_id == CLAIM_ID
    assert row.evidence_ids == ["evi-1"]
    assert row.sanad_id == SANAD_ID
    assert row.calc_ids == ["calc-1"]
    assert row.defect_ids == []
    assert row.reason_codes == ["NUMERIC_INCONSISTENCY"]


def test_muhasabah_row_from_canonical_keeps_safe_structured_fields_only() -> None:
    from idis.models.layer1_durability import MuhasabahRecordRow

    record = _muhasabah_record()
    row = MuhasabahRecordRow.from_canonical(
        record,
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        source_step="METHODOLOGY_EVIDENCE_TRUST_COURT",
    )
    assert row.agent_id == "layer1-advocate-01"
    assert row.output_id == "out-000000000001"
    assert row.confidence == 0.9
    assert row.is_subjective is False
    assert row.supported_claim_ids == [CLAIM_ID]
    assert row.supported_calc_ids == ["calc-1"]
    assert row.uncertainties == [
        {
            "uncertainty": "Revenue recognition timing unclear",
            "impact": "HIGH",
            "mitigation": "Request audited statements",
        }
    ]
    assert row.record_timestamp == "2026-01-01T00:00:00+00:00"
    # Falsifiability narrative and failure-mode prose never persist (DEC-C safe set).
    dumped = row.model_dump(mode="json")
    assert "FREE TEXT MUST NOT PERSIST" not in str(dumped)
    assert "falsifiability" not in str(sorted(dumped))
    assert "failure_modes" not in dumped

    # Deterministic record id: stable for identical identity, distinct otherwise.
    again = MuhasabahRecordRow.from_canonical(
        record,
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        source_step="METHODOLOGY_EVIDENCE_TRUST_COURT",
    )
    assert again.record_id == row.record_id
    other = MuhasabahRecordRow.from_canonical(
        MuhasabahRecordCanonical(**{**record.model_dump(), "output_id": "out-000000000002"}),
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        source_step="METHODOLOGY_EVIDENCE_TRUST_COURT",
    )
    assert other.record_id != row.record_id


# --- InMemory twin: idempotent upserts, tenant scoping, deterministic ordering ---


def test_in_memory_repo_upsert_idempotent_tenant_scoped_sorted() -> None:
    from idis.models.layer1_durability import (
        EvidenceTrustFindingRow,
        MuhasabahRecordRow,
        ValidatedEvidencePackageRow,
    )
    from idis.persistence.repositories.layer1_evidence import (
        InMemoryLayer1EvidenceRepository,
    )

    repo = InMemoryLayer1EvidenceRepository(TENANT_ID)
    vep_row = ValidatedEvidencePackageRow.from_record(_vep_record())
    finding_row = EvidenceTrustFindingRow.from_finding(
        _court_finding(),
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        court_id=COURT_ID,
    )
    muhasabah_row = MuhasabahRecordRow.from_canonical(
        _muhasabah_record(),
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        source_step="METHODOLOGY_EVIDENCE_TRUST_COURT",
    )

    # Upsert twice each: deterministic ids make the writes idempotent.
    for _ in range(2):
        repo.upsert_validated_evidence_package(vep_row)
        repo.upsert_evidence_trust_finding(finding_row)
        repo.upsert_muhasabah_record(muhasabah_row)

    packages = repo.list_validated_evidence_packages(run_id=RUN_ID)
    findings = repo.list_evidence_trust_findings(run_id=RUN_ID)
    records = repo.list_muhasabah_records(run_id=RUN_ID)
    assert [item["package_id"] for item in packages] == [PACKAGE_ID]
    assert [item["finding_id"] for item in findings] == [FINDING_ID]
    assert len(records) == 1
    assert records[0]["record_id"] == muhasabah_row.record_id

    # Another tenant sees nothing; another run sees nothing.
    other = InMemoryLayer1EvidenceRepository(OTHER_TENANT_ID)
    assert other.list_validated_evidence_packages(run_id=RUN_ID) == []
    assert repo.list_validated_evidence_packages(run_id=DASHBOARD_ID) == []


def test_same_finding_id_across_runs_survives_under_each_run() -> None:
    # 48-bit finding ids can collide across runs; the composite tenant/run/finding
    # key must keep both rows, each listed under its own run — never absorb one
    # run's finding into another's row.
    from idis.models.layer1_durability import EvidenceTrustFindingRow
    from idis.persistence.repositories.layer1_evidence import (
        InMemoryLayer1EvidenceRepository,
    )

    other_run_id = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    repo = InMemoryLayer1EvidenceRepository(TENANT_ID)
    for run in (RUN_ID, other_run_id):
        repo.upsert_evidence_trust_finding(
            EvidenceTrustFindingRow.from_finding(
                _court_finding(),
                tenant_id=TENANT_ID,
                deal_id=DEAL_ID,
                run_id=run,
                court_id=COURT_ID,
            )
        )

    first = repo.list_evidence_trust_findings(run_id=RUN_ID)
    second = repo.list_evidence_trust_findings(run_id=other_run_id)
    assert [item["finding_id"] for item in first] == [FINDING_ID]
    assert [item["finding_id"] for item in second] == [FINDING_ID]
    assert first[0]["run_id"] == RUN_ID
    assert second[0]["run_id"] == other_run_id


def test_in_memory_repo_rejects_tenant_mismatch() -> None:
    from idis.models.layer1_durability import ValidatedEvidencePackageRow
    from idis.persistence.repositories.layer1_evidence import (
        InMemoryLayer1EvidenceRepository,
    )

    repo = InMemoryLayer1EvidenceRepository(OTHER_TENANT_ID)
    with pytest.raises(ValueError):
        repo.upsert_validated_evidence_package(
            ValidatedEvidencePackageRow.from_record(_vep_record())
        )


def test_get_layer1_evidence_repository_falls_back_in_memory() -> None:
    from idis.persistence.repositories.layer1_evidence import (
        InMemoryLayer1EvidenceRepository,
        get_layer1_evidence_repository,
    )

    repo = get_layer1_evidence_repository(None, TENANT_ID)
    assert isinstance(repo, InMemoryLayer1EvidenceRepository)


# --- Postgres roundtrip (env-gated; migrations applied by the shared fixture) ---


def test_postgres_layer1_repo_roundtrip(app_engine: Any, migrated_db: Any) -> None:
    # Covers all three tables with PRODUCTION-SHAPED ids — especially the
    # non-UUID claim_mth_/finding- identifiers the findings table must accept.
    from idis.models.layer1_durability import (
        EvidenceTrustFindingRow,
        MuhasabahRecordRow,
        ValidatedEvidencePackageRow,
    )
    from idis.persistence.repositories.layer1_evidence import (
        PostgresLayer1EvidenceRepository,
    )

    vep_row = ValidatedEvidencePackageRow.from_record(_vep_record())
    finding_row = EvidenceTrustFindingRow.from_finding(
        _court_finding(),
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        court_id=COURT_ID,
    )
    muhasabah_row = MuhasabahRecordRow.from_canonical(
        _muhasabah_record(),
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        source_step="METHODOLOGY_EVIDENCE_TRUST_COURT",
    )

    with app_engine.begin() as conn:
        repo = PostgresLayer1EvidenceRepository(conn, TENANT_ID)
        # Idempotent upserts across all three tables.
        for _ in range(2):
            assert repo.upsert_validated_evidence_package(vep_row)["package_id"] == PACKAGE_ID
            assert repo.upsert_evidence_trust_finding(finding_row)["finding_id"] == FINDING_ID
            assert (
                repo.upsert_muhasabah_record(muhasabah_row)["record_id"] == muhasabah_row.record_id
            )

        packages = repo.list_validated_evidence_packages(run_id=RUN_ID)
        assert [item["package_id"] for item in packages] == [PACKAGE_ID]
        assert packages[0]["safe_summary"]["by_grade"] == {"B": 1}

        findings = repo.list_evidence_trust_findings(run_id=RUN_ID)
        assert [item["finding_id"] for item in findings] == [FINDING_ID]
        assert findings[0]["claim_id"] == CLAIM_ID
        assert findings[0]["sanad_id"] == SANAD_ID
        assert findings[0]["finding_type"] == "contradiction"

        records = repo.list_muhasabah_records(run_id=RUN_ID)
        assert [item["record_id"] for item in records] == [muhasabah_row.record_id]
        assert records[0]["supported_claim_ids"] == [CLAIM_ID]
        assert records[0]["uncertainties"][0]["impact"] == "HIGH"

    # RLS: a different tenant context sees none of the three tables' rows.
    with app_engine.begin() as conn:
        other = PostgresLayer1EvidenceRepository(conn, OTHER_TENANT_ID)
        assert other.list_validated_evidence_packages(run_id=RUN_ID) == []
        assert other.list_evidence_trust_findings(run_id=RUN_ID) == []
        assert other.list_muhasabah_records(run_id=RUN_ID) == []
