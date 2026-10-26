"""Dedicated run_stage_admissions scoped repository and common loader.

One admission-time writer plus one common loader for the dedicated stage
admission table, both on caller-supplied connections.  The writer verifies the
real consumed strict-preflight-binding-V2 proof and the queued FULL run on the
same connection, inserts with ``ON CONFLICT DO NOTHING`` and independently
reloads, decodes and compares every canonical and redundant member before
returning the genuine admission.  The common loader replays that verification
for later readers without requiring the queued admission-time run state.

The dedicated table schema is documentary authority only here; this module
owns no DDL or migration.  The binding stays INACTIVE: no governed successor
hash is bound, no graph completion, provider call, worker dispatch, queue,
retry or recovery success is produced, and every mismatch fails closed.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final, Never
from uuid import UUID

from sqlalchemy import text

from idis.analysis.scoring.stage_admission import (
    STAGE_ADMISSION_INVALID,
    ImmutableStagePack,
    RunStageAdmissionV1,
    StageAdmissionError,
    StagePackRegistry,
    StageResolutionPolicyV1,
    decode_run_stage_admission_v1,
    installed_stage_pack_registry,
    stage_resolution_policy_a,
    validate_stage_selection,
)
from idis.persistence.repositories.strict_preflight_proofs import (
    ScopedExecuteConnection,
    StrictPreflightProofError,
    require_consumed_strict_preflight_binding_v2,
)
from idis.pipeline.contract_registry import (
    PIPELINE_CONTRACT_HASH_MISMATCH,
    PipelineContractError,
)
from idis.pipeline.stage_runtime_binding import (
    stage_admission_binding_projection,
    stage_admission_binding_uuid,
    validate_inactive_stage_runtime_binding,
)

_RUN_STAGE_ADMISSION_SCHEMA: Final = "run-stage-admission-v1"
_STEP_REHYDRATION_FAILED: Final = "STEP_REHYDRATION_FAILED"
_MISSING: Final = object()

_CARRIER_PROJECTION_FIELDS: Final[tuple[str, ...]] = (
    "binding_id",
    "tenant_id",
    "deal_id",
    "run_id",
    "source_lineage_object_id",
    "source_object_kind",
    "source_disposition",
    "binding_purpose",
    "corpus_receipt_id",
    "selected_source_version",
    "selected_identity_sha256",
    "selected_content_sha256",
    "corpus_receipt_sha256",
    "pipeline_contract_version",
    "pipeline_contract_hash",
    "binding_sha256",
    "stage_admission_sha256",
)
_CARRIER_NULLABLE_FIELDS: Final = frozenset(
    {"source_lineage_object_id", "corpus_receipt_id", "corpus_receipt_sha256"},
)

_DEDICATED_SELECT: Final = """
SELECT tenant_id::text AS tenant_id, deal_id::text AS deal_id,
       run_id::text AS run_id, schema_version, mode,
       admission_canonical_bytes, stage_selection_sha256,
       run_stage_admission_sha256, pipeline_contract_version,
       pipeline_contract_hash,
       strict_preflight_proof_id::text AS strict_preflight_proof_id,
       binding_sha256
FROM run_stage_admissions
WHERE tenant_id = CAST(:tenant_id AS uuid)
  AND deal_id = CAST(:deal_id AS uuid)
  AND run_id = CAST(:run_id AS uuid)
"""

_DEDICATED_INSERT: Final = """
INSERT INTO run_stage_admissions (
    tenant_id, deal_id, run_id, schema_version, mode,
    admission_canonical_bytes, stage_selection_sha256,
    run_stage_admission_sha256, pipeline_contract_version,
    pipeline_contract_hash, strict_preflight_proof_id,
    binding_sha256, created_at
) VALUES (
    CAST(:tenant_id AS uuid), CAST(:deal_id AS uuid), CAST(:run_id AS uuid),
    :schema_version, :mode, :admission_canonical_bytes,
    :stage_selection_sha256, :run_stage_admission_sha256,
    :pipeline_contract_version, :pipeline_contract_hash,
    CAST(:strict_preflight_proof_id AS uuid), :binding_sha256,
    statement_timestamp()
)
ON CONFLICT DO NOTHING
"""

_CARRIER_SELECT: Final = """
SELECT binding_id::text AS binding_id, tenant_id::text AS tenant_id,
       deal_id::text AS deal_id, run_id::text AS run_id,
       source_lineage_object_id::text AS source_lineage_object_id,
       source_object_kind, source_disposition, binding_purpose,
       corpus_receipt_id::text AS corpus_receipt_id,
       selected_source_version, selected_identity_sha256,
       selected_content_sha256,
       corpus_receipt_sha256::text AS corpus_receipt_sha256,
       pipeline_contract_version, pipeline_contract_hash,
       binding_sha256, stage_admission_sha256
FROM run_input_bindings
WHERE tenant_id = CAST(:tenant_id AS uuid)
  AND deal_id = CAST(:deal_id AS uuid)
  AND run_id = CAST(:run_id AS uuid)
  AND source_object_kind = 'RUN_STAGE_ADMISSION'
  AND binding_purpose = 'STAGE_ADMISSION'
  AND selected_source_version = 'run-stage-admission-v1'
  AND stage_admission_sha256 = :stage_admission_sha256
"""


def _fail_rehydration() -> Never:
    raise StrictPreflightProofError(_STEP_REHYDRATION_FAILED)


def _uuid_scope(value: object) -> str:
    if type(value) is not str:
        raise StageAdmissionError(STAGE_ADMISSION_INVALID)
    try:
        canonical = str(UUID(value))
    except (TypeError, ValueError) as exc:
        raise StageAdmissionError(STAGE_ADMISSION_INVALID) from exc
    if canonical != value:
        raise StageAdmissionError(STAGE_ADMISSION_INVALID)
    return canonical


def _stored_str(row: Mapping[str, object], key: str) -> str:
    value = row.get(key, _MISSING)
    if type(value) is not str:
        _fail_rehydration()
    return value


def _stored_canonical_bytes(row: Mapping[str, object]) -> bytes:
    value = row.get("admission_canonical_bytes", _MISSING)
    if type(value) is bytes:
        return bytes(bytearray(value))
    if isinstance(value, memoryview):
        return value.tobytes()
    _fail_rehydration()


def _project_carrier_row(row: Mapping[str, object]) -> dict[str, str | None]:
    projected: dict[str, str | None] = {}
    for field in _CARRIER_PROJECTION_FIELDS:
        value = row.get(field, _MISSING)
        if value is _MISSING:
            _fail_rehydration()
        if value is None:
            if field not in _CARRIER_NULLABLE_FIELDS:
                _fail_rehydration()
            projected[field] = None
            continue
        if type(value) is not str:
            _fail_rehydration()
        projected[field] = value
    return projected


@dataclass(frozen=True, slots=True)
class LoadedRunStageAdmission:
    """A verified dedicated admission and its immutable executable pack."""

    admission: RunStageAdmissionV1
    pack: ImmutableStagePack


class RunStageAdmissionRepository:
    """Scoped admission writer/reader on a caller-supplied connection."""

    def __init__(self, conn: ScopedExecuteConnection, tenant_id: object) -> None:
        self._conn = conn
        self._tenant_id = _uuid_scope(tenant_id)

    def insert_and_require(
        self,
        *,
        deal_id: object,
        run_id: object,
        admission: object,
        binding: object,
    ) -> RunStageAdmissionV1:
        """Verify, insert and independently reload one dedicated admission."""
        checked = validate_inactive_stage_runtime_binding(binding)
        spec = checked.base_candidate_contract
        expected_hash = spec.contract_hash
        if type(expected_hash) is not str:
            raise PipelineContractError(
                PIPELINE_CONTRACT_HASH_MISMATCH,
                "inactive v28 runtime binding differs from the reviewed prerequisite",
            )
        if type(admission) is not RunStageAdmissionV1 or admission.mode != "FULL":
            raise StageAdmissionError(STAGE_ADMISSION_INVALID)
        checked_deal = _uuid_scope(deal_id)
        checked_run = _uuid_scope(run_id)
        if (
            admission.tenant_id != self._tenant_id
            or admission.deal_id != checked_deal
            or admission.run_id != checked_run
        ):
            raise StageAdmissionError(STAGE_ADMISSION_INVALID)
        if (
            admission.pipeline_contract_version != spec.contract_version
            or admission.pipeline_contract_hash != expected_hash
        ):
            raise PipelineContractError(
                PIPELINE_CONTRACT_HASH_MISMATCH,
                "stage admission contract differs from the inactive v28 context",
            )
        canonical_bytes = admission.canonical_bytes()
        verified = decode_run_stage_admission_v1(canonical_bytes)
        if verified != admission or verified.canonical_bytes() != canonical_bytes:
            raise StageAdmissionError(STAGE_ADMISSION_INVALID)
        proof_id = verified.strict_preflight_proof_id
        binding_hash = verified.binding_sha256
        if type(proof_id) is not str or type(binding_hash) is not str:
            raise StrictPreflightProofError("STRICT_PREFLIGHT_PROOF_MISMATCH")

        require_consumed_strict_preflight_binding_v2(
            self._conn,
            tenant_id=self._tenant_id,
            deal_id=checked_deal,
            run_id=checked_run,
            proof_id=proof_id,
            expected_pipeline_contract_version=spec.contract_version,
            expected_pipeline_contract_hash=expected_hash,
            expected_stage_selection_sha256=verified.stage_selection_sha256,
            expected_binding_hash=binding_hash,
            require_queued_full_admission_state=True,
        )

        self._conn.execute(
            text(_DEDICATED_INSERT),
            {
                "tenant_id": self._tenant_id,
                "deal_id": checked_deal,
                "run_id": checked_run,
                "schema_version": _RUN_STAGE_ADMISSION_SCHEMA,
                "mode": verified.mode,
                "admission_canonical_bytes": canonical_bytes,
                "stage_selection_sha256": verified.stage_selection_sha256,
                "run_stage_admission_sha256": verified.canonical_sha256(),
                "pipeline_contract_version": verified.pipeline_contract_version,
                "pipeline_contract_hash": verified.pipeline_contract_hash,
                "strict_preflight_proof_id": proof_id,
                "binding_sha256": binding_hash,
            },
        )

        loaded = self.load_for_run(
            deal_id=checked_deal,
            run_id=checked_run,
            binding=checked,
        )
        if (
            loaded.admission != verified
            or loaded.admission.canonical_bytes() != canonical_bytes
            or loaded.admission.stage_selection_sha256 != verified.stage_selection_sha256
            or loaded.admission.strict_preflight_proof_id != proof_id
            or loaded.admission.binding_sha256 != binding_hash
        ):
            _fail_rehydration()
        return loaded.admission

    def load_for_run(
        self,
        *,
        deal_id: object,
        run_id: object,
        binding: object,
        policy: StageResolutionPolicyV1 | None = None,
        packs: StagePackRegistry | None = None,
    ) -> LoadedRunStageAdmission:
        """Load and verify one dedicated admission plus its carrier.

        The common read deliberately does not require the queued FULL run
        state; later worker and retry callers load after admission.  Every
        missing, ambiguous, malformed or divergent member fails closed and no
        graph completion, queue, retry or recovery success is produced.
        """
        checked = validate_inactive_stage_runtime_binding(binding)
        spec = checked.base_candidate_contract
        expected_hash = spec.contract_hash
        if type(expected_hash) is not str:
            raise PipelineContractError(
                PIPELINE_CONTRACT_HASH_MISMATCH,
                "inactive v28 runtime binding differs from the reviewed prerequisite",
            )
        checked_deal = _uuid_scope(deal_id)
        checked_run = _uuid_scope(run_id)

        rows = (
            self._conn.execute(
                text(_DEDICATED_SELECT),
                {
                    "tenant_id": self._tenant_id,
                    "deal_id": checked_deal,
                    "run_id": checked_run,
                },
            )
            .mappings()
            .all()
        )
        if len(rows) != 1:
            _fail_rehydration()
        row = rows[0]

        canonical = _stored_canonical_bytes(row)
        stored_sha = _stored_str(row, "run_stage_admission_sha256")
        canonical_sha = hashlib.sha256(canonical).hexdigest()
        if stored_sha != canonical_sha:
            _fail_rehydration()
        admission = decode_run_stage_admission_v1(canonical)
        if admission.canonical_sha256() != canonical_sha:
            _fail_rehydration()

        if (
            _stored_str(row, "tenant_id") != self._tenant_id
            or _stored_str(row, "deal_id") != checked_deal
            or _stored_str(row, "run_id") != checked_run
            or admission.tenant_id != self._tenant_id
            or admission.deal_id != checked_deal
            or admission.run_id != checked_run
        ):
            _fail_rehydration()
        if (
            admission.pipeline_contract_version != spec.contract_version
            or admission.pipeline_contract_hash != expected_hash
            or _stored_str(row, "pipeline_contract_version") != spec.contract_version
            or _stored_str(row, "pipeline_contract_hash") != expected_hash
        ):
            raise PipelineContractError(
                PIPELINE_CONTRACT_HASH_MISMATCH,
                "dedicated admission contract differs from the inactive v28 context",
            )
        if (
            _stored_str(row, "schema_version") != _RUN_STAGE_ADMISSION_SCHEMA
            or _stored_str(row, "mode") != admission.mode
            or _stored_str(row, "stage_selection_sha256") != admission.stage_selection_sha256
        ):
            _fail_rehydration()

        stored_proof_id = row.get("strict_preflight_proof_id", _MISSING)
        stored_binding_sha = row.get("binding_sha256", _MISSING)
        if (
            type(stored_proof_id) is not str
            or type(stored_binding_sha) is not str
            or stored_proof_id != admission.strict_preflight_proof_id
            or stored_binding_sha != admission.binding_sha256
        ):
            raise StrictPreflightProofError("STRICT_PREFLIGHT_PROOF_MISMATCH")

        require_consumed_strict_preflight_binding_v2(
            self._conn,
            tenant_id=self._tenant_id,
            deal_id=checked_deal,
            run_id=checked_run,
            proof_id=stored_proof_id,
            expected_pipeline_contract_version=spec.contract_version,
            expected_pipeline_contract_hash=expected_hash,
            expected_stage_selection_sha256=admission.stage_selection_sha256,
            expected_binding_hash=stored_binding_sha,
        )

        carrier_rows = (
            self._conn.execute(
                text(_CARRIER_SELECT),
                {
                    "tenant_id": self._tenant_id,
                    "deal_id": checked_deal,
                    "run_id": checked_run,
                    "stage_admission_sha256": admission.canonical_sha256(),
                },
            )
            .mappings()
            .all()
        )
        if len(carrier_rows) != 1:
            _fail_rehydration()
        carrier = _project_carrier_row(carrier_rows[0])
        binding_uuid = stage_admission_binding_uuid(
            binding=checked,
            admission=admission,
        )
        expected = stage_admission_binding_projection(
            admission=admission,
            binding_uuid=binding_uuid,
        )
        if carrier != dict(expected):
            _fail_rehydration()

        resolved_policy = stage_resolution_policy_a() if policy is None else policy
        resolved_packs = installed_stage_pack_registry() if packs is None else packs
        pack = validate_stage_selection(
            selection=admission.selection,
            policy=resolved_policy,
            packs=resolved_packs,
        )
        return LoadedRunStageAdmission(admission=admission, pack=pack)
