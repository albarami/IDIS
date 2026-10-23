"""RED contract for the approved full-strict-v18 parent-authority closure."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[2]
_ARTIFACT = _REPO_ROOT / "contracts" / "full_strict_v18_reviewed_preimage_v1.json"
_MIGRATIONS_DIR = _REPO_ROOT / "src" / "idis" / "persistence" / "migrations" / "versions"

_BASELINE_ROW_SCHEMA = [
    "authority_ordinal",
    "table_name",
    "authority_name",
    "authority_kind",
    "column_tuple",
    "predicate",
    "installation_revision",
    "upgrade_ddl",
    "downgrade_disposition",
]
_BASELINE_ROWS = [
    [
        0,
        "claims",
        "ux_claims_task6_scope",
        "UNIQUE_INDEX",
        ["tenant_id", "deal_id", "claim_id"],
        None,
        "0037",
        "CREATE UNIQUE INDEX ux_claims_task6_scope ON claims (tenant_id, deal_id, claim_id)",
        "PRESERVE_PRE_0038",
    ],
    [
        1,
        "deals",
        "uq_task7_deals_tenant_deal",
        "UNIQUE_CONSTRAINT",
        ["tenant_id", "deal_id"],
        None,
        "0038",
        "ALTER TABLE deals ADD CONSTRAINT uq_task7_deals_tenant_deal UNIQUE (tenant_id,deal_id)",
        "DROP_WITH_0038",
    ],
    [
        2,
        "evidence_items",
        "uq_v2_evidence_item_generic_parent",
        "UNIQUE_CONSTRAINT",
        ["tenant_id", "deal_id", "evidence_id", "claim_id", "source_span_id"],
        None,
        "0038",
        "ALTER TABLE evidence_items ADD CONSTRAINT "
        "uq_v2_evidence_item_generic_parent "
        "UNIQUE (tenant_id,deal_id,evidence_id,claim_id,source_span_id)",
        "DROP_WITH_0038",
    ],
    [
        3,
        "sanads",
        "ux_sanads_task6_scope",
        "UNIQUE_INDEX",
        ["tenant_id", "deal_id", "sanad_id"],
        None,
        "0037",
        "CREATE UNIQUE INDEX ux_sanads_task6_scope ON sanads (tenant_id, deal_id, sanad_id)",
        "PRESERVE_PRE_0038",
    ],
]
_CURRENT_DDL_AUTHORITIES = {
    "debate_sessions": {"uq_v2_debate_session_scope(tenant_id,deal_id,run_id,debate_id)"},
    "evidence_trust_findings": {
        "uq_v2_evidence_trust_findings_reference_parent"
        "(tenant_id,deal_id,run_id,finding_id,run_output_object_id)"
    },
    "layer2_ic_challenges": {"uq_v2_layer2_challenge_scope(tenant_id,deal_id,run_id,challenge_id)"},
    "methodology_coverage_records": {
        "uq_v2_coverage_dashboard_parent(tenant_id,deal_id,run_id,coverage_record_id)"
    },
    "methodology_result_sanad_links": {
        "uq_v2_methodology_sanad_grading_parent(tenant_id,deal_id,run_id,sanad_link_id,sanad_id)"
    },
    "validated_evidence_packages": {
        "uq_v2_validated_evidence_package_scope(tenant_id,deal_id,run_id,package_id)",
        "uq_v2_validated_evidence_packages_reference_parent"
        "(tenant_id,deal_id,run_id,package_id,run_output_object_id)",
    },
}


def _load_artifact() -> dict[str, Any]:
    assert _ARTIFACT.is_file(), f"TASK7_CONTRACT_ARTIFACT_MISSING:{_ARTIFACT.name}"
    value = json.loads(_ARTIFACT.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _migration_path() -> Path:
    matches = sorted(_MIGRATIONS_DIR.glob("0038_*.py"))
    assert len(matches) == 1
    return matches[0]


def _load_migration() -> Any:
    path = _migration_path()
    spec = importlib.util.spec_from_file_location("slice100_task7_v5_migration", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_v5_baseline_parent_catalog_is_exact_and_hash_bound() -> None:
    section = _load_artifact()["baseline_parent_authority_registry"]

    assert section["schema_version"] == "baseline-parent-authority-registry-v1"
    assert section["row_schema"] == _BASELINE_ROW_SCHEMA
    assert section["rows"] == _BASELINE_ROWS
    assert isinstance(section["expected_sha256"], str)
    assert len(section["expected_sha256"]) == 64


def test_v5_catalogues_all_seven_current_ddl_authorities() -> None:
    artifact = _load_artifact()
    rows = {
        str(row["table_name"]): set(row["unique_tuple"])
        for row in artifact["current_ddl_registry"]["rows"]
    }

    assert sum(len(values) for values in _CURRENT_DDL_AUTHORITIES.values()) == 7
    for table, authorities in _CURRENT_DDL_AUTHORITIES.items():
        assert authorities <= rows[table]


def test_0038_installs_only_the_two_v5_baseline_authorities_owned_by_0038() -> None:
    module = _load_migration()

    expected = tuple(
        tuple(row) if not isinstance(row[4], list) else (*row[:4], tuple(row[4]), *row[5:])
        for row in _BASELINE_ROWS
    )
    assert expected == module.BASELINE_PARENT_AUTHORITIES_V1
    assert (expected[1], expected[2]) == module.BASELINE_PARENT_AUTHORITIES_0038_V1


def test_0038_materializes_all_seven_current_ddl_authorities() -> None:
    module = _load_migration()

    for table, authorities in _CURRENT_DDL_AUTHORITIES.items():
        assert authorities <= set(module._DDL_ROWS[table]["unique_tuple"])


def test_0038_downgrade_drops_v5_owned_authorities_and_preserves_0037_indexes() -> None:
    source = _migration_path().read_text(encoding="utf-8")
    downgrade = source.split("def downgrade()", 1)[1]

    assert (
        "ALTER TABLE evidence_items DROP CONSTRAINT uq_v2_evidence_item_generic_parent"
    ) in downgrade
    assert "ALTER TABLE deals DROP CONSTRAINT uq_task7_deals_tenant_deal" in downgrade
    assert "DROP INDEX ux_claims_task6_scope" not in downgrade
    assert "DROP INDEX ux_sanads_task6_scope" not in downgrade
