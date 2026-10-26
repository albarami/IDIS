"""RED-first admission for full-strict-v9 native schemas and digest authority.

V8 was never append-frozen.  These tests deliberately fall back to that void
candidate until the v9 artifact exists so the first run diagnoses the semantic
absences instead of failing only on a missing pathname.
"""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from jsonschema import Draft202012Validator
from tests.slice100_task7._void_contract_hash_evidence import (
    VOID_V8_ARTIFACT_SHA256,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_V8_ARTIFACT = _REPO_ROOT / "contracts" / "full_strict_v8_reviewed_preimage_v1.json"
_V9_ARTIFACT = _REPO_ROOT / "contracts" / "full_strict_v9_reviewed_preimage_v1.json"
_V9_MATERIALIZER = _REPO_ROOT / "scripts" / "materialize_full_strict_v9_preimage.py"
_V9_GENERATOR = _REPO_ROOT / "scripts" / "generate_task7_v9_contract_fixture.py"
_V9_FIXTURE = Path(__file__).with_name("fixtures") / "full_strict_v9_contract_hash_v1.json"

_V8_AUTHORITY_SOURCE_SHA512 = (
    "e4a3f91cf753a62a548e7d1c1463f8bd6534c1a42a41656eb451857ec12aa367"
    "1dc87210813400cfc53b788c196aed79d94f0154708c64aa69a89e8f71aa6d2a"
)

_SCHEMA_VERSIONS = {
    "validated-evidence-package-native": ("task7-validated-evidence-package-native-v1"),
    "muhasabah-native": "task7-muhasabah-native-v1",
    "layer2-challenge-native": "task7-layer2-challenge-native-v1",
    "debate-session-native": "task7-debate-session-native-v1",
}

_MANIFEST_REFERENCE_DOMAIN = "deliverable-manifest-reference-digest-v1"
_MANIFEST_REFERENCE_FIELDS = (
    "tenant_id",
    "deal_id",
    "run_id",
    "pipeline_contract_version",
    "pipeline_contract_hash",
    "manifest_deliverable_id",
    "member_kind",
    "catalog_ordinal",
    "member_step_output_id",
    "member_output_object_id",
    "member_identity_sha256",
    "member_value_sha256",
    "member_content_sha256",
)
_MANIFEST_REFERENCE_ROOT_FIELDS = (
    "manifest_reference_id",
    "manifest_output_object_id",
    "run_output_object_id",
)


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _candidate() -> dict[str, Any]:
    """Use v8 only as a RED witness, never as admitted v9 authority."""

    return _load(_V9_ARTIFACT if _V9_ARTIFACT.is_file() else _V8_ARTIFACT)


def _load_module(path: Path, name: str) -> ModuleType:
    assert path.is_file(), f"required v9 module is absent: {path}"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _schema_row(schema_id: str) -> dict[str, Any]:
    matches = [
        row for row in _candidate()["schema_registry"]["rows"] if row["schema_id"] == schema_id
    ]
    assert len(matches) == 1, f"v9 closed native schema is absent: {schema_id}"
    return matches[0]


def _schema(schema_id: str) -> dict[str, Any]:
    row = _schema_row(schema_id)
    snapshot = row["normalized_schema_snapshot"]
    assert isinstance(snapshot, dict)
    Draft202012Validator.check_schema(snapshot)
    return snapshot


def _canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _assert_no_open_object_schema(value: Any, *, path: str = "$") -> None:
    if isinstance(value, dict):
        if value.get("type") == "object":
            additional = value.get("additionalProperties")
            # Governed dynamic-key maps stay maps; only untyped object values are forbidden.
            if isinstance(additional, dict):
                assert value.get("propertyNames", {}).get("pattern"), (
                    f"typed map lacks a closed key domain at {path}"
                )
                assert additional not in ({}, {"type": "object"})
            else:
                assert additional is False, f"open object schema at {path}"
        for key, nested in value.items():
            _assert_no_open_object_schema(nested, path=f"{path}.{key}")
    elif isinstance(value, list):
        for index, nested in enumerate(value):
            _assert_no_open_object_schema(
                nested,
                path=f"{path}[{index}]",
            )


def _validate(schema_id: str, payload: dict[str, Any]) -> None:
    errors = sorted(
        Draft202012Validator(_schema(schema_id)).iter_errors(payload),
        key=lambda error: tuple(str(part) for part in error.absolute_path),
    )
    assert errors == [], "\n".join(
        f"{list(error.absolute_path)}: {error.message}" for error in errors
    )


def _vep_payload() -> dict[str, Any]:
    from tests.test_run_methodology_validated_evidence_package_service import (
        _court_record,
    )

    from idis.models.layer1_durability import ValidatedEvidencePackageRow
    from idis.services.runs.methodology_validated_evidence_package import (
        InMemoryRunMethodologyValidatedEvidencePackageService,
    )

    tenant_id = "11111111-1111-1111-1111-111111111111"
    deal_id = "22222222-2222-2222-2222-222222222222"
    run_id = "33333333-3333-3333-3333-333333333333"
    court = _court_record(tenant_id=tenant_id).model_copy(
        update={
            "deal_id": deal_id,
            "run_id": run_id,
            "court_id": "55555555-5555-5555-5555-555555555555",
            "dashboard_id": "66666666-6666-6666-6666-666666666666",
        }
    )
    result, packages = InMemoryRunMethodologyValidatedEvidencePackageService().run(
        tenant_id=tenant_id,
        deal_id=deal_id,
        run_id=run_id,
        evidence_trust_courts=[court],
    )
    assert result.status.value == "completed"
    assert len(packages) == 1
    return ValidatedEvidencePackageRow.from_record(packages[0]).model_dump(
        mode="json"
    )


def _muhasabah_payload() -> dict[str, Any]:
    from idis.models.debate import MuhasabahRecord
    from idis.models.layer1_durability import MuhasabahRecordRow
    from idis.validators.muhasabah import validate_muhasabah

    record = MuhasabahRecord(
        record_id="55555555-5555-5555-5555-555555555555",
        agent_id="layer1-advocate-01",
        output_id="66666666-6666-6666-6666-666666666666",
        supported_claim_ids=["77777777-7777-7777-7777-777777777777"],
        supported_calc_ids=["88888888-8888-8888-8888-888888888888"],
        falsifiability_tests=[
            {
                "test_description": "Request the signed schedule",
                "required_evidence": "Signed schedule",
                "pass_fail_rule": "The signed total agrees",
            }
        ],
        uncertainties=[
            {
                "uncertainty": "Timing remains unverified",
                "impact": "HIGH",
                "mitigation": "Obtain the signed schedule",
            }
        ],
        confidence=0.9,
        failure_modes=[],
        is_subjective=False,
        timestamp=datetime(2026, 7, 23, tzinfo=UTC),
    )
    assert validate_muhasabah(record.model_dump(mode="json")).passed
    return MuhasabahRecordRow.from_debate_record(
        record,
        tenant_id="11111111-1111-1111-1111-111111111111",
        deal_id="22222222-2222-2222-2222-222222222222",
        run_id="33333333-3333-3333-3333-333333333333",
        source_step="METHODOLOGY_EVIDENCE_TRUST_COURT",
    ).model_dump(mode="json")


def _layer2_payload() -> dict[str, Any]:
    from idis.models.layer2_durability import Layer2ChallengeRow
    from idis.services.runs.layer2_ic_challenge import (
        RunLayer2ICChallengeService,
    )

    tenant_id = "11111111-1111-1111-1111-111111111111"
    deal_id = "22222222-2222-2222-2222-222222222222"
    run_id = "33333333-3333-3333-3333-333333333333"
    debate_id = "44444444-4444-4444-4444-444444444444"
    _summary, record = RunLayer2ICChallengeService(
        strict_full_live=False
    ).run_with_record(
        tenant_id=tenant_id,
        deal_id=deal_id,
        run_id=run_id,
        debate_summary={"debate_id": debate_id, "muhasabah_passed": True},
        created_claim_ids=["claim_mth_0123456789abcdef01234567"],
        calc_ids=["calc-1"],
        graph_evidence={"graph_retrieval": {"retrieval_ids": ["graph-ref-1"]}},
        rag_evidence={"rag_retrieval": {"match_ids": ["rag-ref-1"]}},
        enrichment_refs={
            "enrichment-ref-1": {"provider_id": "companies_house"}
        },
    )
    return Layer2ChallengeRow.from_record(record).model_dump(mode="json")


def _debate_payload() -> dict[str, Any]:
    """Project actual orchestrator messages through the approved safe mapping."""

    from idis.debate.orchestrator import DebateOrchestrator
    from idis.models.debate import DebateConfig, DebateState

    tenant_id = "11111111-1111-1111-1111-111111111111"
    deal_id = "22222222-2222-2222-2222-222222222222"
    run_id = "33333333-3333-3333-3333-333333333333"
    final_state = DebateOrchestrator(config=DebateConfig(max_rounds=1)).run(
        DebateState(
            tenant_id=tenant_id,
            deal_id=deal_id,
            claim_registry_ref=f"claims://{run_id}",
            sanad_graph_ref=f"sanad://{run_id}",
        )
    )
    ordinals: defaultdict[int, int] = defaultdict(int)
    rounds: list[dict[str, Any]] = []
    for message in final_state.messages:
        rounds.append(
            {
                "round_number": message.round_number,
                "message_ordinal": ordinals[message.round_number],
                "message_id": message.message_id,
                "role": message.role.value,
                "claim_refs": list(message.claim_refs),
                "calc_refs": list(message.calc_refs),
            }
        )
        ordinals[message.round_number] += 1
    assert rounds
    return {
        "debate_id": run_id,
        "tenant_id": tenant_id,
        "deal_id": deal_id,
        "run_id": run_id,
        "protocol_version": "v1",
        "max_rounds": 1,
        "rounds": rounds,
        "status": "SUCCEEDED",
    }


_PRODUCTION_PAYLOAD_BUILDERS = {
    "validated-evidence-package-native": _vep_payload,
    "muhasabah-native": _muhasabah_payload,
    "layer2-challenge-native": _layer2_payload,
    "debate-session-native": _debate_payload,
}


def test_v8_candidate_is_void_and_v9_assets_do_not_exist_before_red() -> None:
    assert hashlib.sha256(_V8_ARTIFACT.read_bytes()).hexdigest() == (
        VOID_V8_ARTIFACT_SHA256
    )
    assert hashlib.sha512(_V8_ARTIFACT.read_bytes()).hexdigest() == (_V8_AUTHORITY_SOURCE_SHA512)
    assert _V9_MATERIALIZER.is_file()
    assert _V9_GENERATOR.is_file()
    assert _V9_ARTIFACT.is_file()
    if _load(_V9_ARTIFACT)["metadata"]["append_frozen"] is True:
        assert _V9_FIXTURE.is_file()
    else:
        assert not _V9_FIXTURE.exists(), (
            "an unfrozen v9 candidate must never have an admissible fixture"
        )


def test_v9_adds_exactly_four_closed_schema_rows() -> None:
    v8 = _load(_V8_ARTIFACT)
    v9 = _candidate()
    old_rows = v8["schema_registry"]["rows"]
    new_rows = v9["schema_registry"]["rows"]
    assert new_rows[: len(old_rows)] == old_rows
    added = new_rows[len(old_rows) :]
    assert {row["schema_id"]: row["schema_version"] for row in added} == _SCHEMA_VERSIONS
    assert len(added) == 4
    for row in added:
        _assert_no_open_object_schema(row["normalized_schema_snapshot"])
        assert (
            row["expected_schema_sha256"]
            == hashlib.sha256(
                _canonical_json_bytes(
                    [
                        "typed-schema-definition-v1",
                        row["schema_id"],
                        row["schema_version"],
                        row["authority_locator"],
                        row["normalized_schema_snapshot"],
                        row["collection_paths"],
                    ]
                )
            ).hexdigest()
        )


@pytest.mark.parametrize(
    ("schema_id", "nested_path", "private_field"),
    (
        (
            "validated-evidence-package-native",
            ("safe_summary",),
            "private_text",
        ),
        (
            "muhasabah-native",
            ("uncertainties", 0),
            "private_text",
        ),
        (
            "layer2-challenge-native",
            ("safe_summary",),
            "private_text",
        ),
        (
            "debate-session-native",
            ("rounds", 0),
            "content",
        ),
    ),
)
def test_v9_schema_accepts_real_production_shape_and_rejects_unknown_nested_field(
    schema_id: str,
    nested_path: tuple[str | int, ...],
    private_field: str,
) -> None:
    payload = _PRODUCTION_PAYLOAD_BUILDERS[schema_id]()
    _validate(schema_id, payload)
    corrupted = copy.deepcopy(payload)
    target: Any = corrupted
    for part in nested_path:
        target = target[part]
    target[private_field] = "must never enter the native payload"
    errors = tuple(Draft202012Validator(_schema(schema_id)).iter_errors(corrupted))
    assert errors
    assert any(error.validator == "additionalProperties" for error in errors)


@pytest.mark.parametrize(
    ("schema_id", "mutator"),
    (
        (
            "validated-evidence-package-native",
            lambda payload: payload["safe_summary"]["by_reason"].update(
                {" leading-space": 1}
            ),
        ),
        (
            "validated-evidence-package-native",
            lambda payload: payload["safe_summary"]["by_reason"].update(
                {"valid_reason": "1"}
            ),
        ),
        (
            "layer2-challenge-native",
            lambda payload: payload["safe_summary"]["by_category"].update(
                {"contains space": 1}
            ),
        ),
        (
            "layer2-challenge-native",
            lambda payload: payload["safe_summary"]["stage_emphasis"][
                "weighted_by_category"
            ].update({"market_risk": -1}),
        ),
    ),
)
def test_v9_typed_dynamic_maps_reject_bad_keys_and_values(
    schema_id: str,
    mutator: Any,
) -> None:
    payload = _PRODUCTION_PAYLOAD_BUILDERS[schema_id]()
    mutator(payload)
    assert tuple(Draft202012Validator(_schema(schema_id)).iter_errors(payload))


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("uncertainty", ""),
        ("impact", "UNKNOWN"),
        ("mitigation", ""),
    ),
)
def test_v9_muhasabah_rejects_incomplete_or_blank_uncertainty_triple(
    field: str,
    value: str,
) -> None:
    payload = _muhasabah_payload()
    payload["uncertainties"][0][field] = value
    assert tuple(
        Draft202012Validator(_schema("muhasabah-native")).iter_errors(
            payload
        )
    )
    missing = _muhasabah_payload()
    del missing["uncertainties"][0][field]
    assert tuple(
        Draft202012Validator(_schema("muhasabah-native")).iter_errors(
            missing
        )
    )


@pytest.mark.parametrize(
    ("schema_id", "path"),
    (
        (
            "validated-evidence-package-native",
            ("safe_summary", "evidence_ids"),
        ),
        (
            "muhasabah-native",
            ("supported_claim_ids",),
        ),
        (
            "layer2-challenge-native",
            ("safe_summary", "claim_ids"),
        ),
        (
            "debate-session-native",
            ("rounds", 0, "claim_refs"),
        ),
    ),
)
def test_v9_set_paths_reject_duplicate_members(
    schema_id: str,
    path: tuple[str | int, ...],
) -> None:
    payload = _PRODUCTION_PAYLOAD_BUILDERS[schema_id]()
    target: Any = payload
    for part in path:
        target = target[part]
    if not target:
        target.append("duplicate-id")
    target.append(target[0])
    assert tuple(Draft202012Validator(_schema(schema_id)).iter_errors(payload))


@pytest.mark.parametrize(
    "defect",
    ("out_of_order", "ordinal_gap", "duplicate_coordinate"),
)
def test_v9_debate_semantics_reject_order_gap_and_duplicate_coordinate(
    defect: str,
) -> None:
    generator = _load_module(
        _V9_GENERATOR,
        "_task7_v9_debate_semantic_generator",
    )
    payload = _debate_payload()
    if defect == "out_of_order":
        payload["rounds"][0], payload["rounds"][1] = (
            payload["rounds"][1],
            payload["rounds"][0],
        )
    elif defect == "ordinal_gap":
        payload["rounds"][1]["message_ordinal"] = 9
    else:
        payload["rounds"][1].update(
            {
                "round_number": payload["rounds"][0]["round_number"],
                "message_ordinal": payload["rounds"][0]["message_ordinal"],
                "message_id": payload["rounds"][0]["message_id"],
            }
        )
    with pytest.raises(
        generator.ContractArtifactError,
        match="TASK7_V9_NATIVE_COLLECTION_INVALID",
    ):
        generator.validate_native_payload_semantics(
            "debate-session-native",
            payload,
        )


def test_v9_real_debate_duplicate_message_id_has_distinct_governed_coordinates() -> None:
    generator = _load_module(
        _V9_GENERATOR,
        "_task7_v9_debate_duplicate_generator",
    )
    payload = _debate_payload()
    message_ids = [row["message_id"] for row in payload["rounds"]]
    assert len(message_ids) > len(set(message_ids))
    generator.validate_native_payload_semantics(
        "debate-session-native",
        payload,
    )


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("role", "unreviewed_role"),
        ("message_id", ""),
        ("claim_refs", [""]),
        ("calc_refs", [""]),
    ),
)
def test_v9_debate_schema_rejects_invalid_role_and_blank_references(
    field: str,
    value: Any,
) -> None:
    payload = _debate_payload()
    payload["rounds"][0][field] = value
    assert tuple(
        Draft202012Validator(_schema("debate-session-native")).iter_errors(
            payload
        )
    )


def test_v9_current_output_guard_binds_all_four_closed_schema_hashes() -> None:
    rows = _candidate()["sql_authority_registry"]["rows"]
    authority = next(row for row in rows if row["authority_id"] == "current-output-exact-one-v1")
    sql = "\n".join(
        bytes.fromhex(statement).decode("utf-8")
        for statement in authority["body_sql_utf8_hex_tuple"]
    )
    assert all(schema_id in sql for schema_id in _SCHEMA_VERSIONS)
    assert "native-identity-v1" in sql
    assert "native-output-v1" in sql
    assert "native_identity_sha256" in sql
    assert "native_output_sha256" in sql


def test_v9_manifest_reference_digest_formula_is_literal_and_root_free() -> None:
    rows = _candidate()["sql_authority_registry"]["rows"]
    authority = next(row for row in rows if row["authority_id"] == "current-receipt-equality-v1")
    statements = [
        bytes.fromhex(statement).decode("utf-8")
        for statement in authority["body_sql_utf8_hex_tuple"]
    ]
    digest_helpers = [
        statement
        for statement in statements
        if statement.startswith(
            "CREATE FUNCTION public.idis_task7_manifest_reference_digest_v1("
        )
    ]
    assert len(digest_helpers) == 1
    helper_sql = digest_helpers[0]
    # Inspect the constructed preimage, not the preceding SQL parameter signature.
    preimage_sql = helper_sql[helper_sql.index("preimage:=") :]
    positions = [preimage_sql.index(_MANIFEST_REFERENCE_DOMAIN)]
    positions.extend(preimage_sql.index(field) for field in _MANIFEST_REFERENCE_FIELDS)
    assert positions == sorted(positions)
    assert all(root_field not in helper_sql for root_field in _MANIFEST_REFERENCE_ROOT_FIELDS)


def test_v9_manifest_reference_digest_is_stable_across_root_assignment() -> None:
    generator = _load_module(
        _V9_GENERATOR,
        "_task7_v9_reference_digest_generator",
    )
    record = {
        "tenant_id": "11111111-1111-1111-1111-111111111111",
        "deal_id": "22222222-2222-2222-2222-222222222222",
        "run_id": "33333333-3333-3333-3333-333333333333",
        "pipeline_contract_version": "full-strict-v9",
        "pipeline_contract_hash": "a" * 64,
        "manifest_deliverable_id": ("44444444-4444-4444-4444-444444444444"),
        "member_kind": "ARTIFACT",
        "catalog_ordinal": 0,
        "member_step_output_id": ("55555555-5555-5555-5555-555555555555"),
        "member_output_object_id": ("66666666-6666-6666-6666-666666666666"),
        "member_identity_sha256": "b" * 64,
        "member_value_sha256": "c" * 64,
        "member_content_sha256": "d" * 64,
        "manifest_reference_id": ("77777777-7777-7777-7777-777777777777"),
        "manifest_output_object_id": ("88888888-8888-8888-8888-888888888888"),
        "run_output_object_id": ("99999999-9999-9999-9999-999999999999"),
    }
    baseline = generator.derive_manifest_reference_sha256(record)
    reassigned = dict(record)
    for index, field in enumerate(
        _MANIFEST_REFERENCE_ROOT_FIELDS,
        start=10,
    ):
        reassigned[field] = f"{index:08d}-0000-0000-0000-000000000000"
    assert generator.derive_manifest_reference_sha256(reassigned) == (baseline)
    for field in _MANIFEST_REFERENCE_FIELDS:
        changed = dict(record)
        changed[field] = (
            None if field in {"catalog_ordinal", "member_content_sha256"} else f"changed-{field}"
        )
        assert generator.derive_manifest_reference_sha256(changed) != (baseline)


def test_v9_real_constructor_payloads_roundtrip_actual_postgres_tables() -> None:
    """Prove the four schemas over JSONB returned from the real durable tables."""

    from sqlalchemy import text
    from tests.slice100_task2b._support import disposable_postgres, migrate

    from idis.models.layer1_durability import (
        MuhasabahRecordRow,
        ValidatedEvidencePackageRow,
    )
    from idis.models.layer2_durability import Layer2ChallengeRow
    from idis.persistence.repositories.layer1_evidence import (
        PostgresLayer1EvidenceRepository,
    )
    from idis.persistence.repositories.layer2_challenge import (
        PostgresLayer2ChallengeRepository,
    )

    # Resolve schemas before opening an operator-backed disposable database.
    schemas = {schema_id: _schema(schema_id) for schema_id in _SCHEMA_VERSIONS}
    vep_row = ValidatedEvidencePackageRow.model_validate(_vep_payload())
    muhasabah_row = MuhasabahRecordRow.model_validate(_muhasabah_payload())
    layer2_row = Layer2ChallengeRow.model_validate(_layer2_payload())
    debate_row = _debate_payload()
    tenant_id = vep_row.tenant_id
    deal_id = vep_row.deal_id
    run_id = vep_row.run_id

    with disposable_postgres() as database:
        migrate(database.admin_engine, "0037")
        with database.admin_engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO tenants (tenant_id,name) "
                    "VALUES (:tenant_id,'Task 7 v9 schema census')"
                ),
                {"tenant_id": tenant_id},
            )
            connection.execute(
                text(
                    "INSERT INTO deals "
                    "(deal_id,tenant_id,name,created_at) "
                    "VALUES (:deal_id,:tenant_id,'Task 7 v9 deal',now())"
                ),
                {"deal_id": deal_id, "tenant_id": tenant_id},
            )
            connection.execute(
                text(
                    "INSERT INTO runs "
                    "(run_id,tenant_id,deal_id,mode,status) "
                    "VALUES (:run_id,:tenant_id,:deal_id,'FULL','RUNNING')"
                ),
                {
                    "run_id": run_id,
                    "tenant_id": tenant_id,
                    "deal_id": deal_id,
                },
            )
            layer1 = PostgresLayer1EvidenceRepository(connection, tenant_id)
            layer1.upsert_validated_evidence_package(vep_row)
            layer1.upsert_muhasabah_record(muhasabah_row)
            layer2 = PostgresLayer2ChallengeRepository(connection, tenant_id)
            layer2.upsert_challenge(layer2_row)
            connection.execute(
                text(
                    "INSERT INTO debate_sessions "
                    "(debate_id,tenant_id,deal_id,run_id,protocol_version,"
                    "max_rounds,rounds,status) VALUES "
                    "(:debate_id,:tenant_id,:deal_id,:run_id,"
                    ":protocol_version,:max_rounds,CAST(:rounds AS jsonb),"
                    ":status)"
                ),
                {
                    **{key: value for key, value in debate_row.items() if key != "rounds"},
                    "rounds": json.dumps(
                        debate_row["rounds"],
                        separators=(",", ":"),
                        sort_keys=True,
                    ),
                },
            )

            persisted_vep = layer1.list_validated_evidence_packages(run_id=run_id)[0]
            persisted_muhasabah = layer1.list_muhasabah_records(run_id=run_id)[0]
            persisted_layer2 = layer2.list_challenges(run_id=run_id)[0]
            persisted_layer2["source_debate_id"] = str(
                persisted_layer2["source_debate_id"]
            )
            debate = connection.execute(
                text(
                    "SELECT debate_id,tenant_id,deal_id,run_id,"
                    "protocol_version,max_rounds,rounds,status "
                    "FROM debate_sessions WHERE debate_id=:debate_id"
                ),
                {"debate_id": debate_row["debate_id"]},
            ).one()
            persisted_debate = {
                "debate_id": str(debate.debate_id),
                "tenant_id": str(debate.tenant_id),
                "deal_id": str(debate.deal_id),
                "run_id": str(debate.run_id),
                "protocol_version": debate.protocol_version,
                "max_rounds": debate.max_rounds,
                "rounds": debate.rounds,
                "status": debate.status,
            }

    originals = {
        "validated-evidence-package-native": vep_row.model_dump(mode="json"),
        "muhasabah-native": muhasabah_row.model_dump(mode="json"),
        "layer2-challenge-native": layer2_row.model_dump(mode="json"),
        "debate-session-native": debate_row,
    }
    persisted = {
        "validated-evidence-package-native": persisted_vep,
        "muhasabah-native": persisted_muhasabah,
        "layer2-challenge-native": persisted_layer2,
        "debate-session-native": persisted_debate,
    }
    for schema_id, payload in persisted.items():
        errors = tuple(
            Draft202012Validator(schemas[schema_id]).iter_errors(payload)
        )
        assert errors == (), (
            schema_id,
            tuple(error.message for error in errors),
        )
        original_bytes = _canonical_json_bytes(originals[schema_id])
        persisted_bytes = _canonical_json_bytes(payload)
        assert persisted_bytes == original_bytes, (
            schema_id,
            "serialize/persist/reload widened or lost a governed field",
        )
        assert hashlib.sha256(persisted_bytes).hexdigest() == hashlib.sha256(
            original_bytes
        ).hexdigest()
