"""GL-12 dedicated stage admission repository and common loader controls.

Controls 4-6 over fictional scoped rows on a recording execute seam: the
admission-time writer (real V2 proof verification, ON CONFLICT DO NOTHING
insert, independent reload and compare) and the common loader (dedicated row,
reciprocal V2 proof, stage-arm carrier and genuine pack) including the
fail-closed refusal matrix.  No runtime, database, provider, queue, retry or
recovery claims.
"""

from __future__ import annotations

import hashlib
import json
import unicodedata
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import UUID, uuid5

import pytest

from idis.analysis.scoring import stage_admission as sa
from idis.persistence.repositories import run_stage_admissions as rsa
from idis.persistence.repositories import strict_preflight_proofs as spp
from idis.pipeline import stage_runtime_binding as srb
from idis.pipeline.contract_registry import (
    PIPELINE_CONTRACT_HASH_MISMATCH,
    PIPELINE_CONTRACT_UNAVAILABLE,
    PipelineContractError,
)

_TENANT = "11111111-1111-4111-8111-111111111111"
_DEAL = "22222222-2222-4222-8222-222222222222"
_RUN = "33333333-3333-4333-8333-333333333333"
_PROOF = "44444444-4444-4444-8444-444444444444"
_TENANT_FOREIGN = "55555555-5555-4555-8555-555555555555"
_DEAL_FOREIGN = "66666666-6666-4666-8666-666666666666"
_RUN_FOREIGN = "77777777-7777-4777-8777-777777777777"
_PROOF_FOREIGN = "88888888-8888-4888-8888-888888888888"
_CONTRACT_VERSION = "full-strict-v28"
_CONTRACT_HASH = "28fa5a009d723d76de4c515e9131445d83bd3b9662621d292aff17c47f2ee4fa"
_SELECTION_SHA256 = "25a102bc19268154fd25c200b94191ee859e5c889650286a7c502599c06b8a86"
_STAGE_BINDING_NAMESPACE = UUID("591d30bc-4dce-479b-9164-5f4020c311ae")
_CONSUMED_AT = datetime(2026, 1, 1, tzinfo=UTC)

_ScriptRows = Sequence[Sequence[Mapping[str, object]]]

_CARRIER_VALUE_FIELDS: tuple[str, ...] = (
    "tenant_id",
    "deal_id",
    "run_id",
    "pipeline_contract_version",
    "pipeline_contract_hash",
    "corpus_receipt_id",
    "source_lineage_object_id",
    "source_object_kind",
    "source_disposition",
    "binding_purpose",
    "selected_source_version",
    "selected_identity_sha256",
    "selected_content_sha256",
    "corpus_receipt_sha256",
)


def _normalize(value: object) -> object:
    if isinstance(value, str):
        return unicodedata.normalize("NFC", value)
    if isinstance(value, dict):
        normalized: dict[str, object] = {}
        for key, item in value.items():
            normalized[unicodedata.normalize("NFC", key)] = _normalize(item)
        return normalized
    if isinstance(value, (list, tuple)):
        return [_normalize(item) for item in value]
    return value


def _task7_sha256(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            _normalize(value),
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()


def _selection() -> sa.StageSelectionV1:
    return sa.capture_stage_selection(
        tenant_id=_TENANT,
        deal_id=_DEAL,
        raw_stage="LATER",
        policy=sa.stage_resolution_policy_a(),
        packs=sa.installed_stage_pack_registry(),
    )


def _v2_binding_fields() -> dict[str, object]:
    return {
        "tenant_id": _TENANT,
        "deal_id": _DEAL,
        "corpus_hash": "a" * 64,
        "strict_profile_generation": "synthetic-generation-1",
        "strict_profile_hmac": "2" * 64,
        "pipeline_contract_version": _CONTRACT_VERSION,
        "pipeline_contract_hash": _CONTRACT_HASH,
        "expected_consumer_edge_rule_hash": "4" * 64,
        "static_decision_set_hash": "5" * 64,
        "methodology_version_hashes": ["6" * 64, "7" * 64],
        "promoted_pointer_hash": "8" * 64,
        "provider_receipt_set_hash": "9" * 64,
        "dependency_receipt_set_hash": "a" * 64,
        "stage_selection_sha256": _SELECTION_SHA256,
    }


def _v2_digest(fields: Mapping[str, object]) -> str:
    return hashlib.sha256(
        json.dumps(
            ["idis-strict-preflight-binding-v2", dict(fields)],
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


def _run_identity() -> sa.RunIdentityV1:
    return sa.RunIdentityV1(
        tenant_id=_TENANT,
        deal_id=_DEAL,
        run_id=_RUN,
        pipeline_contract_version=_CONTRACT_VERSION,
        pipeline_contract_hash=_CONTRACT_HASH,
    )


def _admission() -> sa.RunStageAdmissionV1:
    return sa.build_run_stage_admission(
        selection=_selection(),
        run_identity=_run_identity(),
        proof_identity=sa.StrictProofIdentityV1(
            strict_preflight_proof_id=_PROOF,
            binding_sha256=_v2_digest(_v2_binding_fields()),
        ),
    )


def _tampered_selection_admission(selection_field: str) -> sa.RunStageAdmissionV1:
    """Rebuild a consistent admission with one forged selection member."""
    admission = _admission()
    body = sa.stage_canonical_json_v1_decode(
        sa.DOMAIN_RUN_STAGE_ADMISSION_V1,
        admission.canonical_bytes(),
    )
    selection = body["selection"]
    if not isinstance(selection, dict):
        raise AssertionError("expected a selection mapping inside the canonical body")
    tampered_selection = dict(selection)
    tampered_selection[selection_field] = "0" * 64
    selection_bytes = sa.stage_canonical_json_v1_bytes(
        sa.DOMAIN_STAGE_SELECTION_V1,
        tampered_selection,
    )
    tampered_selection_sha256 = hashlib.sha256(selection_bytes).hexdigest()
    body["selection"] = tampered_selection
    body["stage_selection_sha256"] = tampered_selection_sha256
    body["binding_sha256"] = _v2_digest(
        {
            **_v2_binding_fields(),
            "stage_selection_sha256": tampered_selection_sha256,
        },
    )
    canonical = sa.stage_canonical_json_v1_bytes(sa.DOMAIN_RUN_STAGE_ADMISSION_V1, body)
    return sa.decode_run_stage_admission_v1(canonical)


def _foreign_proof_row(
    tampered: sa.RunStageAdmissionV1,
    **overrides: object,
) -> dict[str, object]:
    fields = {**_v2_binding_fields(), "stage_selection_sha256": tampered.stage_selection_sha256}
    return _proof_row(
        stage_selection_sha256=tampered.stage_selection_sha256,
        binding_hash=_v2_digest(fields),
        **overrides,
    )


def _proof_row(**overrides: object) -> dict[str, object]:
    fields = _v2_binding_fields()
    row: dict[str, object] = dict(fields)
    row.update(
        {
            "proof_id": _PROOF,
            "binding_schema_version": "strict-preflight-binding-v2",
            "binding_hash": _v2_digest(fields),
            "consumed_at": _CONSUMED_AT,
            "consumed_by_run_id": _RUN,
            "run_strict_preflight_proof_id": _PROOF,
            "run_pipeline_contract_version": _CONTRACT_VERSION,
            "run_pipeline_contract_hash": _CONTRACT_HASH,
            "run_status": "QUEUED",
            "run_mode": "FULL",
        },
    )
    row.update(overrides)
    return row


def _dedicated_row(admission: sa.RunStageAdmissionV1) -> dict[str, object]:
    canonical = admission.canonical_bytes()
    return {
        "tenant_id": admission.tenant_id,
        "deal_id": admission.deal_id,
        "run_id": admission.run_id,
        "schema_version": "run-stage-admission-v1",
        "mode": "FULL",
        "admission_canonical_bytes": canonical,
        "stage_selection_sha256": admission.stage_selection_sha256,
        "run_stage_admission_sha256": hashlib.sha256(canonical).hexdigest(),
        "pipeline_contract_version": admission.pipeline_contract_version,
        "pipeline_contract_hash": admission.pipeline_contract_hash,
        "strict_preflight_proof_id": admission.strict_preflight_proof_id,
        "binding_sha256": admission.binding_sha256,
    }


def _carrier_row(admission: sa.RunStageAdmissionV1) -> dict[str, object]:
    canonical_sha = hashlib.sha256(admission.canonical_bytes()).hexdigest()
    flat_sha = _task7_sha256(
        [
            "idis-stage-admission-input-identity-v1",
            admission.tenant_id,
            admission.deal_id,
            admission.run_id,
            canonical_sha,
        ],
    )
    values: dict[str, object] = {
        "tenant_id": admission.tenant_id,
        "deal_id": admission.deal_id,
        "run_id": admission.run_id,
        "pipeline_contract_version": admission.pipeline_contract_version,
        "pipeline_contract_hash": admission.pipeline_contract_hash,
        "corpus_receipt_id": None,
        "source_lineage_object_id": None,
        "source_object_kind": "RUN_STAGE_ADMISSION",
        "source_disposition": "ADMITTED_METADATA",
        "binding_purpose": "STAGE_ADMISSION",
        "selected_source_version": "run-stage-admission-v1",
        "selected_identity_sha256": flat_sha,
        "selected_content_sha256": canonical_sha,
        "corpus_receipt_sha256": None,
    }
    name_sha = _task7_sha256(
        ["run-input-binding-v2", *(values[f] for f in _CARRIER_VALUE_FIELDS)],
    )
    digest_sha = _task7_sha256(
        [
            "run-input-binding-digest-v2",
            *(values[f] for f in _CARRIER_VALUE_FIELDS),
        ],
    )
    return {
        "binding_id": str(uuid5(_STAGE_BINDING_NAMESPACE, name_sha)),
        "tenant_id": admission.tenant_id,
        "deal_id": admission.deal_id,
        "run_id": admission.run_id,
        "source_lineage_object_id": None,
        "source_object_kind": "RUN_STAGE_ADMISSION",
        "source_disposition": "ADMITTED_METADATA",
        "binding_purpose": "STAGE_ADMISSION",
        "corpus_receipt_id": None,
        "selected_source_version": "run-stage-admission-v1",
        "selected_identity_sha256": flat_sha,
        "selected_content_sha256": canonical_sha,
        "corpus_receipt_sha256": None,
        "pipeline_contract_version": admission.pipeline_contract_version,
        "pipeline_contract_hash": admission.pipeline_contract_hash,
        "binding_sha256": digest_sha,
        "stage_admission_sha256": canonical_sha,
    }


class _RecordingMappings:
    def __init__(self, rows: Sequence[Mapping[str, object]]) -> None:
        self._rows = list(rows)

    def all(self) -> list[Mapping[str, object]]:
        return list(self._rows)

    def one_or_none(self) -> Mapping[str, object] | None:
        if len(self._rows) > 1:
            raise AssertionError("ambiguous scripted rows")
        return self._rows[0] if self._rows else None


class _RecordingResult:
    def __init__(self, rows: Sequence[Mapping[str, object]]) -> None:
        self._mappings = _RecordingMappings(rows)

    def mappings(self) -> _RecordingMappings:
        return self._mappings


class _RecordingConnection:
    """Scripted execute seam: records every call, returns scripted rows."""

    def __init__(self, script: _ScriptRows) -> None:
        self._script = [list(rows) for rows in script]
        self.ledger: list[tuple[str, dict[str, object] | None]] = []

    @property
    def remaining_responses(self) -> int:
        return len(self._script)

    def execute(
        self,
        statement: object,
        parameters: Mapping[str, object] | None = None,
    ) -> _RecordingResult:
        self.ledger.append(
            (str(statement), None if parameters is None else dict(parameters)),
        )
        if not self._script:
            raise AssertionError("unexpected scoped execute")
        return _RecordingResult(self._script.pop(0))


if TYPE_CHECKING:
    from sqlalchemy.engine import Connection, MappingResult, Result
    from sqlalchemy.sql.base import Executable


class _SqlalchemyScopedMappings:
    """Materialize real SQLAlchemy RowMappings into the scoped row type."""

    def __init__(self, mappings: MappingResult) -> None:
        self._mappings = mappings

    def all(self) -> Sequence[Mapping[str, object]]:
        """Return every real row materialized as a plain mapping."""
        return [dict(row) for row in self._mappings.all()]

    def one_or_none(self) -> Mapping[str, object] | None:
        """Return the single real row or ``None``."""
        row = self._mappings.one_or_none()
        return None if row is None else dict(row)


class _SqlalchemyScopedResult:
    """Wrap a real CursorResult behind the scoped result seam."""

    def __init__(self, result: Result[object]) -> None:
        self._result = result

    def mappings(self) -> _SqlalchemyScopedMappings:
        """Return the real mappings view behind the scoped seam."""
        return _SqlalchemyScopedMappings(self._result.mappings())


class _SqlalchemyScopedExecuteConnection:
    """Inert adapter: the scoped protocol over a real SQLAlchemy Connection."""

    def __init__(self, conn: Connection) -> None:
        self._conn = conn

    def execute(
        self,
        statement: Executable,
        parameters: Mapping[str, object] | None = None,
    ) -> _SqlalchemyScopedResult:
        """Delegate to the real Connection.execute seam."""
        return _SqlalchemyScopedResult(self._conn.execute(statement, parameters))


def _sqlalchemy_connection_as_scoped_execute(
    conn: Connection,
) -> spp.ScopedExecuteConnection:
    """Static seam: a real SQLAlchemy Connection adapts to the scoped protocol."""
    return _SqlalchemyScopedExecuteConnection(conn)


def _insert_script(admission: sa.RunStageAdmissionV1) -> _ScriptRows:
    return [
        [_proof_row()],
        [],
        [_dedicated_row(admission)],
        [_proof_row()],
        [_carrier_row(admission)],
    ]


def test_insert_requires_proof_then_persists_and_reloads() -> None:
    """Writer: codec gate, real V2 verification, ON CONFLICT insert, reload."""
    admission = _admission()
    binding = srb.inactive_stage_runtime_binding()
    conn = _RecordingConnection(_insert_script(admission))
    repo = rsa.RunStageAdmissionRepository(conn, _TENANT)

    result = repo.insert_and_require(
        deal_id=_DEAL,
        run_id=_RUN,
        admission=admission,
        binding=binding,
    )
    assert result == admission

    assert len(conn.ledger) == 5
    proof_sql, proof_params = conn.ledger[0]
    assert "FROM strict_preflight_proofs" in proof_sql
    assert "JOIN runs" in proof_sql
    assert "r.status = 'QUEUED'" in proof_sql
    assert "r.mode = 'FULL'" in proof_sql
    assert proof_params == {
        "tenant_id": _TENANT,
        "deal_id": _DEAL,
        "run_id": _RUN,
        "proof_id": _PROOF,
    }

    insert_sql, insert_params = conn.ledger[1]
    assert "INSERT INTO run_stage_admissions" in insert_sql
    assert "ON CONFLICT DO NOTHING" in insert_sql
    assert "statement_timestamp()" in insert_sql
    assert insert_params == {
        "tenant_id": _TENANT,
        "deal_id": _DEAL,
        "run_id": _RUN,
        "schema_version": "run-stage-admission-v1",
        "mode": "FULL",
        "admission_canonical_bytes": admission.canonical_bytes(),
        "stage_selection_sha256": _SELECTION_SHA256,
        "run_stage_admission_sha256": admission.canonical_sha256(),
        "pipeline_contract_version": _CONTRACT_VERSION,
        "pipeline_contract_hash": _CONTRACT_HASH,
        "strict_preflight_proof_id": _PROOF,
        "binding_sha256": _v2_digest(_v2_binding_fields()),
    }
    assert "created_at" not in insert_params

    select_sql, select_params = conn.ledger[2]
    assert "FROM run_stage_admissions" in select_sql
    assert "INSERT INTO" not in select_sql
    assert select_params == {"tenant_id": _TENANT, "deal_id": _DEAL, "run_id": _RUN}

    reload_proof_sql, reload_proof_params = conn.ledger[3]
    assert "FROM strict_preflight_proofs" in reload_proof_sql
    assert "r.status = 'QUEUED'" not in reload_proof_sql
    assert reload_proof_params == {
        "tenant_id": _TENANT,
        "deal_id": _DEAL,
        "run_id": _RUN,
        "proof_id": _PROOF,
    }

    carrier_sql, carrier_params = conn.ledger[4]
    assert "FROM run_input_bindings" in carrier_sql
    assert "'RUN_STAGE_ADMISSION'" in carrier_sql
    assert "'STAGE_ADMISSION'" in carrier_sql
    assert "'run-stage-admission-v1'" in carrier_sql
    assert carrier_params == {
        "tenant_id": _TENANT,
        "deal_id": _DEAL,
        "run_id": _RUN,
        "stage_admission_sha256": admission.canonical_sha256(),
    }
    assert conn.remaining_responses == 0

    for sql, _params in conn.ledger:
        for forbidden in ("UPDATE ", "DELETE ", "COMMIT", "ROLLBACK", "SET ROLE"):
            assert forbidden not in sql

    retry_conn = _RecordingConnection(_insert_script(admission))
    retry = rsa.RunStageAdmissionRepository(retry_conn, _TENANT).insert_and_require(
        deal_id=_DEAL,
        run_id=_RUN,
        admission=admission,
        binding=binding,
    )
    assert retry == admission
    assert len(retry_conn.ledger) == 5
    assert retry_conn.remaining_responses == 0

    effects: list[str] = []
    foreign_binding = srb.InactiveStageRuntimeBinding(
        base_candidate_contract=binding.base_candidate_contract,
        authority_amendment_proposal_sha256="0" * 64,
        literal_vectors_sha256=binding.literal_vectors_sha256,
        base_component_identities=binding.base_component_identities,
        governed_successor_contract_hash=None,
    )
    boundary_refusals: tuple[
        tuple[str, object, object, object, object, type[Exception], str],
        ...,
    ] = (
        (
            "bad_deal_uuid",
            "not-a-uuid",
            _RUN,
            admission,
            binding,
            sa.StageAdmissionError,
            sa.STAGE_ADMISSION_INVALID,
        ),
        (
            "bad_run_uuid",
            _DEAL,
            17,
            admission,
            binding,
            sa.StageAdmissionError,
            sa.STAGE_ADMISSION_INVALID,
        ),
        (
            "foreign_deal",
            _DEAL_FOREIGN,
            _RUN,
            admission,
            binding,
            sa.StageAdmissionError,
            sa.STAGE_ADMISSION_INVALID,
        ),
        (
            "foreign_run",
            _DEAL,
            _RUN_FOREIGN,
            admission,
            binding,
            sa.StageAdmissionError,
            sa.STAGE_ADMISSION_INVALID,
        ),
        (
            "selection_object",
            _DEAL,
            _RUN,
            admission.selection,
            binding,
            sa.StageAdmissionError,
            sa.STAGE_ADMISSION_INVALID,
        ),
        (
            "non_full_mode",
            _DEAL,
            _RUN,
            admission.model_copy(update={"mode": "EARLY"}),
            binding,
            sa.StageAdmissionError,
            sa.STAGE_ADMISSION_INVALID,
        ),
        (
            "copied_bad_selection_digest",
            _DEAL,
            _RUN,
            admission.model_copy(update={"stage_selection_sha256": "0" * 64}),
            binding,
            sa.StageAdmissionError,
            sa.STAGE_ADMISSION_INVALID,
        ),
        (
            "constructed_bad_selection_digest",
            _DEAL,
            _RUN,
            sa.RunStageAdmissionV1.model_construct(
                schema_version=admission.schema_version,
                tenant_id=admission.tenant_id,
                deal_id=admission.deal_id,
                run_id=admission.run_id,
                mode=admission.mode,
                selection=admission.selection,
                stage_selection_sha256="0" * 64,
                pipeline_contract_version=admission.pipeline_contract_version,
                pipeline_contract_hash=admission.pipeline_contract_hash,
                strict_preflight_proof_id=admission.strict_preflight_proof_id,
                binding_sha256=admission.binding_sha256,
            ),
            binding,
            sa.StageAdmissionError,
            sa.STAGE_ADMISSION_INVALID,
        ),
        (
            "wrong_contract_hash",
            _DEAL,
            _RUN,
            admission.model_copy(update={"pipeline_contract_hash": "0" * 64}),
            binding,
            PipelineContractError,
            PIPELINE_CONTRACT_HASH_MISMATCH,
        ),
        (
            "foreign_binding",
            _DEAL,
            _RUN,
            admission,
            foreign_binding,
            PipelineContractError,
            PIPELINE_CONTRACT_HASH_MISMATCH,
        ),
    )
    for name, deal_id, run_id, candidate, candidate_binding, family, code in boundary_refusals:
        refusing = _RecordingConnection([])
        with pytest.raises(family) as refused:
            rsa.RunStageAdmissionRepository(refusing, _TENANT).insert_and_require(
                deal_id=deal_id,
                run_id=run_id,
                admission=candidate,
                binding=candidate_binding,
            )
        value = refused.value
        assert isinstance(
            value,
            (sa.StageAdmissionError, spp.StrictPreflightProofError, PipelineContractError),
        )
        assert value.code == code, name
        assert refusing.ledger == [], name
    assert effects == []

    foreign_tenant_conn = _RecordingConnection([])
    with pytest.raises(sa.StageAdmissionError) as tenant_refused:
        rsa.RunStageAdmissionRepository(foreign_tenant_conn, _TENANT_FOREIGN).insert_and_require(
            deal_id=_DEAL,
            run_id=_RUN,
            admission=admission,
            binding=binding,
        )
    assert tenant_refused.value.code == sa.STAGE_ADMISSION_INVALID
    assert foreign_tenant_conn.ledger == []

    with pytest.raises(sa.StageAdmissionError) as tenant_type_refused:
        rsa.RunStageAdmissionRepository(_RecordingConnection([]), 99)
    assert tenant_type_refused.value.code == sa.STAGE_ADMISSION_INVALID

    unproven = sa.build_run_stage_admission(
        selection=_selection(),
        run_identity=_run_identity(),
        proof_identity=None,
    )
    unproven_conn = _RecordingConnection([])
    with pytest.raises(spp.StrictPreflightProofError) as unproven_refused:
        rsa.RunStageAdmissionRepository(unproven_conn, _TENANT).insert_and_require(
            deal_id=_DEAL,
            run_id=_RUN,
            admission=unproven,
            binding=binding,
        )
    assert unproven_refused.value.code == "STRICT_PREFLIGHT_PROOF_MISMATCH"
    assert unproven_conn.ledger == []

    proof_refusals: tuple[tuple[str, Sequence[Mapping[str, object]]], ...] = (
        ("missing_proof", []),
        ("unconsumed_proof", [_proof_row(consumed_at=None, consumed_by_run_id=None)]),
        ("foreign_run_reciprocal", [_proof_row(run_strict_preflight_proof_id=_DEAL)]),
        ("run_not_queued", [_proof_row(run_status="RUNNING")]),
    )
    for name, first_rows in proof_refusals:
        refusing = _RecordingConnection([first_rows])
        with pytest.raises(spp.StrictPreflightProofError) as proof_refused:
            rsa.RunStageAdmissionRepository(refusing, _TENANT).insert_and_require(
                deal_id=_DEAL,
                run_id=_RUN,
                admission=admission,
                binding=binding,
            )
        assert proof_refused.value.code == "STRICT_PREFLIGHT_PROOF_MISMATCH", name
        assert len(refusing.ledger) == 1, name
        assert "FROM strict_preflight_proofs" in refusing.ledger[0][0], name
    assert effects == []

    dedicated = _dedicated_row(admission)
    carrier = _carrier_row(admission)
    readback_refusals: tuple[
        tuple[str, Sequence[Mapping[str, object]], type[Exception], str],
        ...,
    ] = (
        ("missing_dedicated", [], spp.StrictPreflightProofError, "STEP_REHYDRATION_FAILED"),
        (
            "duplicate_dedicated",
            [dedicated, dedicated],
            spp.StrictPreflightProofError,
            "STEP_REHYDRATION_FAILED",
        ),
        (
            "bytea_type",
            [{**dedicated, "admission_canonical_bytes": "not-bytes"}],
            spp.StrictPreflightProofError,
            "STEP_REHYDRATION_FAILED",
        ),
        (
            "stored_sha_mismatch",
            [{**dedicated, "run_stage_admission_sha256": "0" * 64}],
            spp.StrictPreflightProofError,
            "STEP_REHYDRATION_FAILED",
        ),
        (
            "foreign_stored_deal",
            [{**dedicated, "deal_id": _DEAL_FOREIGN}],
            spp.StrictPreflightProofError,
            "STEP_REHYDRATION_FAILED",
        ),
        (
            "stored_contract",
            [{**dedicated, "pipeline_contract_hash": "0" * 64}],
            PipelineContractError,
            "PIPELINE_CONTRACT_HASH_MISMATCH",
        ),
        (
            "stored_mode",
            [{**dedicated, "mode": "REPLAY"}],
            spp.StrictPreflightProofError,
            "STEP_REHYDRATION_FAILED",
        ),
        (
            "stored_schema",
            [{**dedicated, "schema_version": "run-stage-admission-v2"}],
            spp.StrictPreflightProofError,
            "STEP_REHYDRATION_FAILED",
        ),
        (
            "stored_selection_sha",
            [{**dedicated, "stage_selection_sha256": "0" * 64}],
            spp.StrictPreflightProofError,
            "STEP_REHYDRATION_FAILED",
        ),
        (
            "stored_proof_id",
            [{**dedicated, "strict_preflight_proof_id": _PROOF_FOREIGN}],
            spp.StrictPreflightProofError,
            "STRICT_PREFLIGHT_PROOF_MISMATCH",
        ),
        (
            "stored_binding",
            [{**dedicated, "binding_sha256": "0" * 64}],
            spp.StrictPreflightProofError,
            "STRICT_PREFLIGHT_PROOF_MISMATCH",
        ),
    )
    for name, rows, family, code in readback_refusals:
        refusing = _RecordingConnection([[_proof_row()], [], rows, [_proof_row()], [carrier]])
        with pytest.raises(family) as readback_refused:
            rsa.RunStageAdmissionRepository(refusing, _TENANT).insert_and_require(
                deal_id=_DEAL,
                run_id=_RUN,
                admission=admission,
                binding=binding,
            )
        readback_value = readback_refused.value
        assert isinstance(
            readback_value,
            (sa.StageAdmissionError, spp.StrictPreflightProofError, PipelineContractError),
        )
        assert readback_value.code == code, name
        assert len(refusing.ledger) == 3, name
        assert "FROM run_stage_admissions" in refusing.ledger[-1][0], name
        assert refusing.remaining_responses == 2, name
    assert effects == []

    carrier_refusals: tuple[tuple[str, Sequence[Mapping[str, object]]], ...] = (
        ("missing_carrier", []),
        ("duplicate_carrier", [carrier, carrier]),
        ("tampered_carrier", [{**carrier, "binding_sha256": "0" * 64}]),
    )
    for name, carrier_rows in carrier_refusals:
        refusing = _RecordingConnection(
            [[_proof_row()], [], [dedicated], [_proof_row()], carrier_rows],
        )
        with pytest.raises(spp.StrictPreflightProofError) as carrier_refused:
            rsa.RunStageAdmissionRepository(refusing, _TENANT).insert_and_require(
                deal_id=_DEAL,
                run_id=_RUN,
                admission=admission,
                binding=binding,
            )
        assert carrier_refused.value.code == "STEP_REHYDRATION_FAILED", name
        assert len(refusing.ledger) == 5, name
        assert "FROM run_input_bindings" in refusing.ledger[-1][0], name
    assert effects == []

    conflicting = sa.build_run_stage_admission(
        selection=_selection(),
        run_identity=_run_identity(),
        proof_identity=sa.StrictProofIdentityV1(
            strict_preflight_proof_id=_PROOF_FOREIGN,
            binding_sha256=_v2_digest(_v2_binding_fields()),
        ),
    )
    conflict_conn = _RecordingConnection(
        [
            [_proof_row()],
            [],
            [_dedicated_row(conflicting)],
            [
                _proof_row(
                    proof_id=_PROOF_FOREIGN,
                    run_strict_preflight_proof_id=_PROOF_FOREIGN,
                ),
            ],
            [_carrier_row(conflicting)],
        ],
    )
    with pytest.raises(spp.StrictPreflightProofError) as conflict_refused:
        rsa.RunStageAdmissionRepository(conflict_conn, _TENANT).insert_and_require(
            deal_id=_DEAL,
            run_id=_RUN,
            admission=admission,
            binding=binding,
        )
    assert conflict_refused.value.code == "STEP_REHYDRATION_FAILED"
    assert len(conflict_conn.ledger) == 5
    assert "FROM run_input_bindings" in conflict_conn.ledger[-1][0]
    assert conflict_conn.remaining_responses == 0
    assert effects == []

    final_conn = _RecordingConnection(_insert_script(admission))
    final = rsa.RunStageAdmissionRepository(final_conn, _TENANT).insert_and_require(
        deal_id=_DEAL,
        run_id=_RUN,
        admission=admission,
        binding=binding,
    )
    effects.append("inserted")
    assert final == admission
    assert effects == ["inserted"]


def test_common_loader_verifies_dedicated_proof_carrier_and_pack() -> None:
    """Loader: exact scoped row, reciprocal V2 proof, carrier, real pack."""
    admission = _admission()
    binding = srb.inactive_stage_runtime_binding()
    conn = _RecordingConnection(
        [
            [_dedicated_row(admission)],
            [
                _proof_row(
                    run_status="RUNNING",
                    run_mode="FULL",
                    consumed_at=datetime(2024, 1, 1, tzinfo=UTC),
                    proof_expires_at=datetime(2024, 2, 1, tzinfo=UTC),
                ),
            ],
            [_carrier_row(admission)],
            [{"current_live_stage": "LIVE", "live_stage_sha256": "0" * 64}],
        ],
    )
    repo = rsa.RunStageAdmissionRepository(conn, _TENANT)
    loaded = repo.load_for_run(deal_id=_DEAL, run_id=_RUN, binding=binding)

    assert loaded.admission == admission
    assert loaded.admission is not admission
    assert type(loaded.admission) is sa.RunStageAdmissionV1
    assert loaded.admission.selection.raw_stage == "LATER"
    assert loaded.admission.selection.resolved_stage.name == "GROWTH"
    assert type(loaded.pack) is sa.ImmutableStagePack
    assert loaded.pack.stage.name == "GROWTH"
    assert loaded.pack.pack_sha256 == admission.selection.pack_sha256
    assert loaded.admission.canonical_sha256() == admission.canonical_sha256()

    assert len(conn.ledger) == 3
    dedicated_sql, dedicated_params = conn.ledger[0]
    assert "FROM run_stage_admissions" in dedicated_sql
    assert "INSERT INTO" not in dedicated_sql
    assert dedicated_params == {"tenant_id": _TENANT, "deal_id": _DEAL, "run_id": _RUN}
    proof_sql, proof_params = conn.ledger[1]
    assert "FROM strict_preflight_proofs" in proof_sql
    assert "JOIN runs" in proof_sql
    assert "r.status = 'QUEUED'" not in proof_sql
    assert proof_params == {
        "tenant_id": _TENANT,
        "deal_id": _DEAL,
        "run_id": _RUN,
        "proof_id": _PROOF,
    }
    carrier_sql, carrier_params = conn.ledger[2]
    assert "FROM run_input_bindings" in carrier_sql
    assert "'RUN_STAGE_ADMISSION'" in carrier_sql
    assert carrier_params == {
        "tenant_id": _TENANT,
        "deal_id": _DEAL,
        "run_id": _RUN,
        "stage_admission_sha256": admission.canonical_sha256(),
    }
    for sql, _params in conn.ledger:
        assert "current_live_stage" not in sql
        for forbidden in ("UPDATE ", "DELETE ", "COMMIT", "ROLLBACK", "SET ROLE"):
            assert forbidden not in sql
    assert conn.remaining_responses == 1


def test_common_loader_fails_closed_without_authority() -> None:
    """Loader refusal matrix: every corruption fails closed at its gate."""
    admission = _admission()
    binding = srb.inactive_stage_runtime_binding()
    dedicated = _dedicated_row(admission)
    proof = _proof_row()
    carrier = _carrier_row(admission)

    malformed_sha = hashlib.sha256(b"\x00").hexdigest()
    tampered_pack = _tampered_selection_admission("pack_sha256")
    tampered_policy = _tampered_selection_admission("resolution_policy_sha256")
    missing_member = {key: value for key, value in proof.items() if key != "corpus_hash"}
    missing_carrier_member = {key: value for key, value in carrier.items() if key != "binding_id"}
    malformed_carrier_member = {**carrier, "binding_id": 7}

    cases: tuple[
        tuple[str, _ScriptRows, type[Exception], str, int, str | None],
        ...,
    ] = (
        (
            "missing_admission",
            [[], [proof], [carrier]],
            spp.StrictPreflightProofError,
            "STEP_REHYDRATION_FAILED",
            1,
            "FROM run_stage_admissions",
        ),
        (
            "ambiguous_admission",
            [[dedicated, dedicated], [proof], [carrier]],
            spp.StrictPreflightProofError,
            "STEP_REHYDRATION_FAILED",
            1,
            "FROM run_stage_admissions",
        ),
        (
            "malformed_canonical",
            [
                [
                    {
                        **dedicated,
                        "admission_canonical_bytes": b"\x00",
                        "run_stage_admission_sha256": malformed_sha,
                    }
                ],
                [proof],
                [carrier],
            ],
            sa.StageAdmissionError,
            "STAGE_ADMISSION_INVALID",
            1,
            "FROM run_stage_admissions",
        ),
        (
            "bytea_type",
            [
                [{**dedicated, "admission_canonical_bytes": "not-bytes"}],
                [proof],
                [carrier],
            ],
            spp.StrictPreflightProofError,
            "STEP_REHYDRATION_FAILED",
            1,
            "FROM run_stage_admissions",
        ),
        (
            "stored_sha_mismatch",
            [
                [{**dedicated, "run_stage_admission_sha256": "0" * 64}],
                [proof],
                [carrier],
            ],
            spp.StrictPreflightProofError,
            "STEP_REHYDRATION_FAILED",
            1,
            "FROM run_stage_admissions",
        ),
        (
            "foreign_stored_scope",
            [[{**dedicated, "deal_id": _DEAL_FOREIGN}], [proof], [carrier]],
            spp.StrictPreflightProofError,
            "STEP_REHYDRATION_FAILED",
            1,
            "FROM run_stage_admissions",
        ),
        (
            "wrong_contract",
            [[{**dedicated, "pipeline_contract_hash": "0" * 64}], [proof], [carrier]],
            PipelineContractError,
            "PIPELINE_CONTRACT_HASH_MISMATCH",
            1,
            "FROM run_stage_admissions",
        ),
        (
            "stored_mode",
            [[{**dedicated, "mode": "REPLAY"}], [proof], [carrier]],
            spp.StrictPreflightProofError,
            "STEP_REHYDRATION_FAILED",
            1,
            "FROM run_stage_admissions",
        ),
        (
            "stored_schema",
            [[{**dedicated, "schema_version": "run-stage-admission-v2"}], [proof], [carrier]],
            spp.StrictPreflightProofError,
            "STEP_REHYDRATION_FAILED",
            1,
            "FROM run_stage_admissions",
        ),
        (
            "stored_selection_sha",
            [[{**dedicated, "stage_selection_sha256": "0" * 64}], [proof], [carrier]],
            spp.StrictPreflightProofError,
            "STEP_REHYDRATION_FAILED",
            1,
            "FROM run_stage_admissions",
        ),
        (
            "null_proof_pair",
            [
                [
                    {
                        **dedicated,
                        "strict_preflight_proof_id": None,
                        "binding_sha256": None,
                    }
                ],
                [proof],
                [carrier],
            ],
            spp.StrictPreflightProofError,
            "STRICT_PREFLIGHT_PROOF_MISMATCH",
            1,
            "FROM run_stage_admissions",
        ),
        (
            "stored_proof_id",
            [
                [{**dedicated, "strict_preflight_proof_id": _PROOF_FOREIGN}],
                [proof],
                [carrier],
            ],
            spp.StrictPreflightProofError,
            "STRICT_PREFLIGHT_PROOF_MISMATCH",
            1,
            "FROM run_stage_admissions",
        ),
        (
            "stored_binding",
            [[{**dedicated, "binding_sha256": "0" * 64}], [proof], [carrier]],
            spp.StrictPreflightProofError,
            "STRICT_PREFLIGHT_PROOF_MISMATCH",
            1,
            "FROM run_stage_admissions",
        ),
        (
            "missing_proof",
            [[dedicated], [], [carrier]],
            spp.StrictPreflightProofError,
            "STRICT_PREFLIGHT_PROOF_MISMATCH",
            2,
            "FROM strict_preflight_proofs",
        ),
        (
            "v1_proof_discriminator",
            [
                [dedicated],
                [_proof_row(binding_schema_version="strict-preflight-binding-v1")],
                [carrier],
            ],
            spp.StrictPreflightProofError,
            "STRICT_PREFLIGHT_PROOF_INVALID",
            2,
            "FROM strict_preflight_proofs",
        ),
        (
            "unknown_schema",
            [
                [dedicated],
                [_proof_row(binding_schema_version="strict-preflight-binding-v3")],
                [carrier],
            ],
            spp.StrictPreflightProofError,
            "STRICT_PREFLIGHT_PROOF_INVALID",
            2,
            "FROM strict_preflight_proofs",
        ),
        (
            "v2_body_digest",
            [[dedicated], [_proof_row(corpus_hash="b" * 64)], [carrier]],
            spp.StrictPreflightProofError,
            "STRICT_PREFLIGHT_PROOF_INVALID",
            2,
            "FROM strict_preflight_proofs",
        ),
        (
            "missing_v2_member",
            [[dedicated], [missing_member], [carrier]],
            spp.StrictPreflightProofError,
            "STRICT_PREFLIGHT_PROOF_INVALID",
            2,
            "FROM strict_preflight_proofs",
        ),
        (
            "stage_member_mismatch",
            [
                [dedicated],
                [
                    _proof_row(
                        stage_selection_sha256="0" * 64,
                        binding_hash=_v2_digest(
                            {**_v2_binding_fields(), "stage_selection_sha256": "0" * 64},
                        ),
                    )
                ],
                [carrier],
            ],
            spp.StrictPreflightProofError,
            "STRICT_PREFLIGHT_PROOF_MISMATCH",
            2,
            "FROM strict_preflight_proofs",
        ),
        (
            "stored_binding_hash_digest",
            [[dedicated], [_proof_row(binding_hash="0" * 64)], [carrier]],
            spp.StrictPreflightProofError,
            "STRICT_PREFLIGHT_PROOF_INVALID",
            2,
            "FROM strict_preflight_proofs",
        ),
        (
            "unconsumed_proof",
            [[dedicated], [_proof_row(consumed_at=None, consumed_by_run_id=None)], [carrier]],
            spp.StrictPreflightProofError,
            "STRICT_PREFLIGHT_PROOF_MISMATCH",
            2,
            "FROM strict_preflight_proofs",
        ),
        (
            "consumed_by_foreign",
            [[dedicated], [_proof_row(consumed_by_run_id=_DEAL_FOREIGN)], [carrier]],
            spp.StrictPreflightProofError,
            "STRICT_PREFLIGHT_PROOF_MISMATCH",
            2,
            "FROM strict_preflight_proofs",
        ),
        (
            "consumed_at_text",
            [[dedicated], [_proof_row(consumed_at="yesterday")], [carrier]],
            spp.StrictPreflightProofError,
            "STRICT_PREFLIGHT_PROOF_MISMATCH",
            2,
            "FROM strict_preflight_proofs",
        ),
        (
            "foreign_run_reciprocal",
            [[dedicated], [_proof_row(run_strict_preflight_proof_id=_TENANT)], [carrier]],
            spp.StrictPreflightProofError,
            "STRICT_PREFLIGHT_PROOF_MISMATCH",
            2,
            "FROM strict_preflight_proofs",
        ),
        (
            "reciprocal_run_contract",
            [
                [dedicated],
                [_proof_row(run_pipeline_contract_hash="0" * 64)],
                [carrier],
            ],
            spp.StrictPreflightProofError,
            "STRICT_PREFLIGHT_PROOF_MISMATCH",
            2,
            "FROM strict_preflight_proofs",
        ),
        (
            "reciprocal_run_version",
            [
                [dedicated],
                [_proof_row(run_pipeline_contract_version="full-strict-v21")],
                [carrier],
            ],
            spp.StrictPreflightProofError,
            "STRICT_PREFLIGHT_PROOF_MISMATCH",
            2,
            "FROM strict_preflight_proofs",
        ),
        (
            "missing_carrier",
            [[dedicated], [proof], []],
            spp.StrictPreflightProofError,
            "STEP_REHYDRATION_FAILED",
            3,
            "FROM run_input_bindings",
        ),
        (
            "duplicate_carrier",
            [[dedicated], [proof], [carrier, carrier]],
            spp.StrictPreflightProofError,
            "STEP_REHYDRATION_FAILED",
            3,
            "FROM run_input_bindings",
        ),
        (
            "missing_carrier_member",
            [[dedicated], [proof], [missing_carrier_member]],
            spp.StrictPreflightProofError,
            "STEP_REHYDRATION_FAILED",
            3,
            "FROM run_input_bindings",
        ),
        (
            "malformed_carrier",
            [[dedicated], [proof], [malformed_carrier_member]],
            spp.StrictPreflightProofError,
            "STEP_REHYDRATION_FAILED",
            3,
            "FROM run_input_bindings",
        ),
        (
            "v21_carrier_contract",
            [
                [dedicated],
                [proof],
                [
                    {
                        **carrier,
                        "pipeline_contract_hash": (
                            "46d14b1fbf6f7790bdcfd16ee211446d80ed4c95b22ec04810cd912e28706794"
                        ),
                    }
                ],
            ],
            spp.StrictPreflightProofError,
            "STEP_REHYDRATION_FAILED",
            3,
            "FROM run_input_bindings",
        ),
        (
            "tampered_carrier_null",
            [[dedicated], [proof], [{**carrier, "source_object_kind": None}]],
            spp.StrictPreflightProofError,
            "STEP_REHYDRATION_FAILED",
            3,
            "FROM run_input_bindings",
        ),
        (
            "tampered_carrier_sha",
            [[dedicated], [proof], [{**carrier, "binding_sha256": "0" * 64}]],
            spp.StrictPreflightProofError,
            "STEP_REHYDRATION_FAILED",
            3,
            "FROM run_input_bindings",
        ),
        (
            "unknown_pack",
            [
                [_dedicated_row(tampered_pack)],
                [_foreign_proof_row(tampered_pack)],
                [_carrier_row(tampered_pack)],
            ],
            sa.StageAdmissionError,
            "STAGE_PACK_MISMATCH",
            3,
            "FROM run_input_bindings",
        ),
        (
            "foreign_policy",
            [
                [_dedicated_row(tampered_policy)],
                [_foreign_proof_row(tampered_policy)],
                [_carrier_row(tampered_policy)],
            ],
            sa.StageAdmissionError,
            "STAGE_RESOLUTION_POLICY_MISMATCH",
            3,
            "FROM run_input_bindings",
        ),
    )
    for name, script, family, code, boundary, sql_fragment in cases:
        conn = _RecordingConnection(script)
        repo = rsa.RunStageAdmissionRepository(conn, _TENANT)
        with pytest.raises(family) as refused:
            repo.load_for_run(deal_id=_DEAL, run_id=_RUN, binding=binding)
        value = refused.value
        assert isinstance(
            value,
            (sa.StageAdmissionError, spp.StrictPreflightProofError, PipelineContractError),
        )
        assert value.code == code, name
        assert len(conn.ledger) == boundary, name
        if sql_fragment is not None:
            assert sql_fragment in conn.ledger[-1][0], name
        assert conn.remaining_responses == len(script) - boundary, name

    effects: list[str] = []
    scope_refusals: tuple[tuple[str, object, object], ...] = (
        ("bad_deal_uuid", "not-a-uuid", _RUN),
        ("bad_run_uuid", _DEAL, "also-not-a-uuid"),
        ("non_str_scope", _DEAL, 17),
    )
    for name, deal_id, run_id in scope_refusals:
        scoped = _RecordingConnection([[dedicated], [proof], [carrier]])
        with pytest.raises(sa.StageAdmissionError) as scope_refused:
            rsa.RunStageAdmissionRepository(scoped, _TENANT).load_for_run(
                deal_id=deal_id,
                run_id=run_id,
                binding=binding,
            )
        assert scope_refused.value.code == sa.STAGE_ADMISSION_INVALID, name
        assert scoped.ledger == [], name
    assert effects == []

    foreign_binding = srb.InactiveStageRuntimeBinding(
        base_candidate_contract=binding.base_candidate_contract,
        authority_amendment_proposal_sha256="0" * 64,
        literal_vectors_sha256=binding.literal_vectors_sha256,
        base_component_identities=binding.base_component_identities,
        governed_successor_contract_hash=None,
    )
    wrong_context_conn = _RecordingConnection([[dedicated], [proof], [carrier]])
    with pytest.raises(PipelineContractError) as wrong_context:
        rsa.RunStageAdmissionRepository(wrong_context_conn, _TENANT).load_for_run(
            deal_id=_DEAL,
            run_id=_RUN,
            binding=foreign_binding,
        )
    assert wrong_context.value.code == PIPELINE_CONTRACT_HASH_MISMATCH
    assert wrong_context_conn.ledger == []

    forged_binding = srb.InactiveStageRuntimeBinding(
        base_candidate_contract=binding.base_candidate_contract,
        authority_amendment_proposal_sha256=binding.authority_amendment_proposal_sha256,
        literal_vectors_sha256=binding.literal_vectors_sha256,
        base_component_identities=binding.base_component_identities,
        governed_successor_contract_hash="0" * 64,
    )
    with pytest.raises(PipelineContractError) as forged:
        rsa.RunStageAdmissionRepository(_RecordingConnection([]), _TENANT).load_for_run(
            deal_id=_DEAL,
            run_id=_RUN,
            binding=forged_binding,
        )
    assert forged.value.code == PIPELINE_CONTRACT_HASH_MISMATCH
    with pytest.raises(PipelineContractError) as forged_authority:
        forged_binding.require_governed_successor_contract()
    assert forged_authority.value.code == PIPELINE_CONTRACT_HASH_MISMATCH

    conn = _RecordingConnection([[dedicated], [proof], [carrier]])
    repo = rsa.RunStageAdmissionRepository(conn, _TENANT)
    loaded = repo.load_for_run(deal_id=_DEAL, run_id=_RUN, binding=binding)
    effects.append("loaded")
    assert loaded.admission == admission
    assert type(loaded.pack) is sa.ImmutableStagePack
    assert len(conn.ledger) == 3
    assert effects == ["loaded"]
    with pytest.raises(PipelineContractError) as unavailable:
        binding.require_governed_successor_contract()
    assert unavailable.value.code == PIPELINE_CONTRACT_UNAVAILABLE
