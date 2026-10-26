"""Census builders for Task 7 source steps 0 and 1.

This module is used only by tests. It is not wired into orchestration.
``_LEASE_SECONDS`` is a test-path constant.

The builder reads the admitted frozen source graph and the committed step-0
STEP_RECEIPT. It does not parse a sealed package and it does not open a
provider or object-store client. The completion transaction stays in
``Task7CompletionFinalizationService.complete_step``.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, NoReturn

from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine

from idis.audit.postgres_sink import PostgresAuditSink
from idis.models.run_step import RunStep, StepStatus
from idis.persistence.db import set_tenant_local
from idis.persistence.repositories.run_steps import PostgresRunStepsRepository
from idis.persistence.repositories.task7_completion import PostgresTask7CompletionPort
from idis.pipeline.task7_census import build_census_envelope
from idis.pipeline.task7_completion import (
    STEP_OUTPUT_MISSING,
    Task7CompletionError,
    Task7CompletionFinalizationService,
    Task7LeaseScope,
    Task7StepCompletionCommand,
)
from idis.pipeline.task7_semantics import (
    STEP_SEMANTIC_REGISTRY,
    SemanticValidationInput,
)

_LEASE_SECONDS = 120  # test-path constant; this module is not wired into orchestration
_INVENTORY_STEP = "DATA_ROOM_INVENTORY_PACKAGE"
_HANDOFF_STEP = "DATA_ROOM_INGESTION_HANDOFF"
_HANDOFF_KINDS = ("ARTIFACT", "DOCUMENT", "PARSE_ATTEMPT", "SPAN")
_FILE_DISPOSITIONS = ("EVIDENCE_BEARING", "CONFIRMED_EXCLUSION")
_MEMBER_KINDS = ("ARTIFACT", "DOCUMENT", "PARSE_ATTEMPT")
_SCHEMA_VERSION = "1"


@dataclass(frozen=True, slots=True)
class _FrozenRoot:
    """One admitted source-lineage root."""

    source_lineage_object_id: str
    object_kind: str
    source_disposition: str
    object_identity_sha256: str
    content_sha256: str


@dataclass(frozen=True, slots=True)
class _FrozenBinding:
    """One admitted run input binding."""

    binding_id: str
    source_object_kind: str
    selected_identity_sha256: str
    selected_content_sha256: str
    binding_sha256: str
    corpus_receipt_id: str


@dataclass(frozen=True, slots=True)
class _FrozenManifest:
    """File accounting already stored on the frozen package."""

    file_count: int
    total_bytes: int


@dataclass(frozen=True, slots=True)
class _OutputProducer:
    """One committed STEP_RECEIPT producer."""

    output_object_id: str
    native_identity_sha256: str
    native_output_sha256: str


@dataclass(frozen=True, slots=True)
class _FrozenSourceGraph:
    """Frozen inputs required to census steps 0 and 1."""

    roots: tuple[_FrozenRoot, ...]
    bindings: tuple[_FrozenBinding, ...]
    manifest: _FrozenManifest
    inventory_receipt: _OutputProducer | None


def prepare_frozen_source_command(
    *,
    engine: Engine,
    scope: Task7LeaseScope,
    step_order: int,
) -> Task7StepCompletionCommand:
    """Commit the source step as RUNNING and return its completion command.

    Args:
        engine: Application engine for the disposable database.
        scope: Live lease scope of the admitted run.
        step_order: ``0`` for inventory or ``1`` for handoff.

    Returns:
        The command ``complete_step`` accepts. The RUNNING update is already
        committed, so the completion transaction can lock the live step.
    """

    with engine.begin() as connection:
        set_tenant_local(connection, scope.tenant_id)
        step = _load_pending_step(connection, scope, step_order)
        command = build_frozen_source_completion_command(
            connection,
            scope=scope,
            step=step,
        )
        _mark_running(connection, scope, step)
    return command


def complete_frozen_source_step(
    *,
    engine: Engine,
    scope: Task7LeaseScope,
    step_order: int,
) -> None:
    """Build the frozen census and commit the step through ``complete_step``.

    Args:
        engine: Application engine for the disposable database.
        scope: Live lease scope of the admitted run.
        step_order: ``0`` for inventory or ``1`` for handoff.
    """

    command = prepare_frozen_source_command(
        engine=engine,
        scope=scope,
        step_order=step_order,
    )
    service = Task7CompletionFinalizationService(
        connection_factory=engine.begin,
        port_factory=lambda connection, tenant_id: PostgresTask7CompletionPort(
            connection,
            tenant_id,
            PostgresAuditSink(),
            _LEASE_SECONDS,
        ),
    )
    result = service.complete_step(command)
    if result.completed is not True:
        _fail("source step completion did not commit")


def build_frozen_source_completion_command(
    connection: Connection,
    *,
    scope: Task7LeaseScope,
    step: RunStep,
) -> Task7StepCompletionCommand:
    """Build a status-only completion command from the frozen source graph.

    Args:
        connection: Open application connection with the tenant already set.
        scope: Live lease scope of the admitted run.
        step: Persisted step row being completed.

    Returns:
        A command whose census and ``result_payload`` are the canonical census.
    """

    step_name = step.step_name.value if hasattr(step.step_name, "value") else str(step.step_name)
    if step_name not in {_INVENTORY_STEP, _HANDOFF_STEP}:
        _fail("frozen source builder does not cover this step")
    graph = _load_graph(connection, scope, include_inventory_receipt=step_name == _HANDOFF_STEP)
    payload = _inventory_payload(graph) if step_name == _INVENTORY_STEP else _handoff_payload(graph)
    spec = STEP_SEMANTIC_REGISTRY[step_name]
    census = build_census_envelope(schema_id=spec.census_schema_id, payload=payload)
    return Task7StepCompletionCommand(
        scope=scope,
        step_id=step.step_id,
        step_name=step_name,
        semantic_input=SemanticValidationInput(
            step_name=step_name,
            semantic_rule_version=spec.semantic_rule_version,
            semantic_rule_hash=spec.semantic_rule_hash,
            terminal_status="COMPLETED",
            result_payload=_json_ready(census.payload),
            census=census,
            native_outputs=(),
            subrole_receipts=(),
            producer_persistence_confirmed=True,
        ),
        staged_native_writes=(),
    )


def _load_pending_step(
    connection: Connection,
    scope: Task7LeaseScope,
    step_order: int,
) -> RunStep:
    if step_order not in {0, 1}:
        _fail("frozen source builder does not cover this step")
    steps = PostgresRunStepsRepository(connection, scope.tenant_id).get_by_run_id(scope.run_id)
    matches = tuple(row for row in steps if row.step_order == step_order)
    if len(matches) != 1:
        _fail("frozen source step is not the single persisted row")
    return matches[0]


def _mark_running(connection: Connection, scope: Task7LeaseScope, step: RunStep) -> None:
    started_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    PostgresRunStepsRepository(connection, scope.tenant_id).update(
        step.model_copy(update={"status": StepStatus.RUNNING, "started_at": started_at}),
        lease_owner_id=scope.lease_owner_id,
        lease_fencing_token=scope.lease_fencing_token,
    )


def _load_graph(
    connection: Connection,
    scope: Task7LeaseScope,
    *,
    include_inventory_receipt: bool,
) -> _FrozenSourceGraph:
    params = {"tenant_id": scope.tenant_id, "deal_id": scope.deal_id, "run_id": scope.run_id}
    bindings = tuple(
        _FrozenBinding(
            binding_id=_text(row["binding_id"]),
            source_object_kind=_text(row["source_object_kind"]),
            selected_identity_sha256=_text(row["selected_identity_sha256"]),
            selected_content_sha256=_text(row["selected_content_sha256"]),
            binding_sha256=_text(row["binding_sha256"]),
            corpus_receipt_id=_text(row["corpus_receipt_id"]),
        )
        for row in _mappings(
            connection,
            """
            SELECT binding_id, source_object_kind, selected_identity_sha256,
                   selected_content_sha256, binding_sha256, corpus_receipt_id
            FROM run_input_bindings
            WHERE tenant_id = CAST(:tenant_id AS uuid)
              AND deal_id = CAST(:deal_id AS uuid)
              AND run_id = CAST(:run_id AS uuid)
            """,
            params,
        )
    )
    package_ids = {row.corpus_receipt_id for row in bindings}
    if len(package_ids) != 1:
        _fail("frozen source bindings do not share one corpus receipt")
    package_id = next(iter(package_ids))
    roots = tuple(
        _FrozenRoot(
            source_lineage_object_id=_text(row["source_lineage_object_id"]),
            object_kind=_text(row["object_kind"]),
            source_disposition=_text(row["source_disposition"]),
            object_identity_sha256=_text(row["object_identity_sha256"]),
            content_sha256=_text(row["content_sha256"]),
        )
        for row in _mappings(
            connection,
            """
            SELECT source_lineage_object_id, object_kind, source_disposition,
                   object_identity_sha256, content_sha256
            FROM source_lineage_objects
            WHERE tenant_id = CAST(:tenant_id AS uuid)
              AND deal_id = CAST(:deal_id AS uuid)
              AND corpus_receipt_id = CAST(:package_id AS uuid)
              AND write_origin = 'RUNTIME'
            """,
            {**params, "package_id": package_id},
        )
    )
    manifest = _load_manifest(connection, scope, package_id)
    receipt = _load_inventory_receipt(connection, params) if include_inventory_receipt else None
    return _FrozenSourceGraph(
        roots=roots,
        bindings=bindings,
        manifest=manifest,
        inventory_receipt=receipt,
    )


def _load_manifest(
    connection: Connection, scope: Task7LeaseScope, package_id: str
) -> _FrozenManifest:
    rows = _mappings(
        connection,
        """
        SELECT metadata
        FROM data_room_packages
        WHERE tenant_id = CAST(:tenant_id AS uuid)
          AND deal_id = CAST(:deal_id AS uuid)
          AND package_id = CAST(:package_id AS uuid)
        """,
        {"tenant_id": scope.tenant_id, "deal_id": scope.deal_id, "package_id": package_id},
    )
    if len(rows) != 1:
        _fail("frozen package manifest is not the single stored row")
    metadata = rows[0]["metadata"]
    if isinstance(metadata, str):
        metadata = json.loads(metadata)
    if not isinstance(metadata, Mapping):
        _fail("frozen package metadata is not an object")
    manifest = metadata.get("source_manifest")
    if not isinstance(manifest, Mapping):
        _fail("frozen package metadata has no source manifest")
    file_count = manifest.get("file_count")
    total_bytes = manifest.get("total_bytes")
    if isinstance(file_count, bool) or not isinstance(file_count, int) or file_count < 1:
        _fail("frozen manifest file count is not a positive integer")
    if isinstance(total_bytes, bool) or not isinstance(total_bytes, int) or total_bytes < 0:
        _fail("frozen manifest total bytes is not a non-negative integer")
    return _FrozenManifest(file_count=file_count, total_bytes=total_bytes)


def _load_inventory_receipt(
    connection: Connection,
    params: Mapping[str, str],
) -> _OutputProducer:
    rows = _mappings(
        connection,
        """
        SELECT output_row.output_object_id,
               output_row.native_identity_sha256,
               output_row.native_output_sha256
        FROM run_step_outputs AS step_row
        JOIN run_output_objects AS output_row
          ON output_row.tenant_id = step_row.tenant_id
         AND output_row.deal_id = step_row.deal_id
         AND output_row.run_id = step_row.run_id
         AND output_row.output_object_id = step_row.output_object_id
        WHERE step_row.tenant_id = CAST(:tenant_id AS uuid)
          AND step_row.run_id = CAST(:run_id AS uuid)
          AND step_row.step_name = 'DATA_ROOM_INVENTORY_PACKAGE'
          AND step_row.subrole = '__step__'
          AND output_row.output_class = 'STEP_RECEIPT'
        """,
        params,
    )
    if len(rows) != 1:
        _fail("step 0 aggregate STEP_RECEIPT is not committed")
    return _OutputProducer(
        output_object_id=_text(rows[0]["output_object_id"]),
        native_identity_sha256=_text(rows[0]["native_identity_sha256"]),
        native_output_sha256=_text(rows[0]["native_output_sha256"]),
    )


def _inventory_payload(graph: _FrozenSourceGraph) -> dict[str, Any]:
    package_bindings = _bindings(graph, ("PACKAGE",))
    file_bindings = _bindings(graph, ("PACKAGE_FILE",))
    if len(package_bindings) != 1:
        _fail("inventory census requires one package binding")
    package_root = _native_root(_matched_root(graph.roots, package_bindings[0]))
    file_roots = [_native_root(_matched_root(graph.roots, row)) for row in file_bindings]
    dispositions = [_matched_root(graph.roots, row).source_disposition for row in file_bindings]
    deferred_count, unresolved_count = _checked_disposition_counts(dispositions)
    ledger = len(file_bindings)
    return {
        "schema_version": _SCHEMA_VERSION,
        "input_refs": [_binding_ref(row) for row in (*package_bindings, *file_bindings)],
        "package_root": package_root,
        "package_file_roots": file_roots,
        "manifest_file_count": graph.manifest.file_count,
        "ledger_file_count": ledger,
        "total_bytes": graph.manifest.total_bytes,
        "evidence_bearing_count": dispositions.count("EVIDENCE_BEARING"),
        "confirmed_exclusion_count": dispositions.count("CONFIRMED_EXCLUSION"),
        "missing_count": max(0, graph.manifest.file_count - ledger),
        "extra_count": max(0, ledger - graph.manifest.file_count),
        "deferred_count": deferred_count,
        "unresolved_count": unresolved_count,
    }


def _handoff_payload(graph: _FrozenSourceGraph) -> dict[str, Any]:
    if graph.inventory_receipt is None:
        _fail("handoff census requires the step 0 STEP_RECEIPT")
    bindings = _bindings(graph, _HANDOFF_KINDS)
    roots_by_kind = {
        kind: [_native_root(_matched_root(graph.roots, row)) for row in _bindings(graph, (kind,))]
        for kind in _HANDOFF_KINDS
    }
    dispositions = [_matched_root(graph.roots, row).source_disposition for row in bindings]
    _, unresolved_count = _checked_disposition_counts(dispositions)
    member_counts = [len(_bindings(graph, (kind,))) for kind in _MEMBER_KINDS]
    return {
        "schema_version": _SCHEMA_VERSION,
        "input_refs": [
            *[_binding_ref(row) for row in bindings],
            {
                "producer_type": "OUTPUT_OBJECT",
                "producer_id": graph.inventory_receipt.output_object_id,
                "producer_identity_sha256": graph.inventory_receipt.native_identity_sha256,
                "producer_value_sha256": graph.inventory_receipt.native_output_sha256,
            },
        ],
        "artifact_roots": roots_by_kind["ARTIFACT"],
        "document_roots": roots_by_kind["DOCUMENT"],
        "attempt_roots": roots_by_kind["PARSE_ATTEMPT"],
        "span_roots": roots_by_kind["SPAN"],
        "terminal_class_counts": [
            ["CONFIRMED_EXCLUSION", dispositions.count("CONFIRMED_EXCLUSION")],
            ["EVIDENCE_BEARING", dispositions.count("EVIDENCE_BEARING")],
        ],
        "object_proof_count": len(_bindings(graph, ("ARTIFACT",))),
        "object_hash_mismatch_count": _artifact_content_mismatch_count(graph),
        "member_count_mismatch_count": max(member_counts) - min(member_counts),
        "unresolved_count": unresolved_count,
    }


def _bindings(graph: _FrozenSourceGraph, kinds: Sequence[str]) -> tuple[_FrozenBinding, ...]:
    allowed = set(kinds)
    return tuple(row for row in graph.bindings if row.source_object_kind in allowed)


def _matched_root(roots: Sequence[_FrozenRoot], binding: _FrozenBinding) -> _FrozenRoot:
    matches = tuple(
        root
        for root in roots
        if root.object_kind == binding.source_object_kind
        and root.object_identity_sha256 == binding.selected_identity_sha256
    )
    if len(matches) != 1:
        _fail("frozen root for a bound producer is not unique")
    return matches[0]


def _checked_disposition_counts(dispositions: Sequence[str]) -> tuple[int, int]:
    """Return deferred and unresolved counts, or refuse a broken disposition.

    Freeze writes only ``EVIDENCE_BEARING`` and ``CONFIRMED_EXCLUSION``
    (``task7_source_freeze.py:1268`` and ``:1276``). Admission rejects every
    other disposition (``strict_preflight_proofs.py:443-451``). A deferred,
    unresolved, or other disposition is not reported as zero.
    """

    deferred = sum(value == "DEFERRED" for value in dispositions)
    unresolved = sum(value == "UNRESOLVED" for value in dispositions)
    if any(value not in _FILE_DISPOSITIONS for value in dispositions):
        _fail("frozen source disposition is outside the census domain")
    return deferred, unresolved


def _artifact_content_mismatch_count(graph: _FrozenSourceGraph) -> int:
    """Count ARTIFACT roots whose content hash differs from the binding.

    Admission copies the root content hash onto ``selected_content_sha256``
    (``strict_preflight_proofs.py:457`` and ``:507``). Identity matching alone
    does not establish that equality.
    """

    mismatches = 0
    for binding in _bindings(graph, ("ARTIFACT",)):
        root = _matched_root(graph.roots, binding)
        if root.content_sha256 != binding.selected_content_sha256:
            mismatches += 1
    return mismatches


def _binding_ref(binding: _FrozenBinding) -> dict[str, str]:
    return {
        "producer_type": "INPUT_BINDING",
        "producer_id": binding.binding_id,
        "producer_identity_sha256": binding.selected_identity_sha256,
        "producer_value_sha256": binding.binding_sha256,
    }


def _native_root(root: _FrozenRoot) -> dict[str, str]:
    return {
        "output_kind": root.object_kind,
        "native_identity_sha256": root.object_identity_sha256,
        "native_output_sha256": root.content_sha256,
        "output_object_id": root.source_lineage_object_id,
    }


def _json_ready(value: object) -> Any:
    if isinstance(value, tuple):
        return [_json_ready(item) for item in value]
    if isinstance(value, list):
        return [_json_ready(item) for item in value]
    if isinstance(value, Mapping):
        return {str(key): _json_ready(item) for key, item in value.items()}
    return value


def _mappings(
    connection: Connection,
    statement: str,
    params: Mapping[str, str],
) -> tuple[dict[str, Any], ...]:
    return tuple(dict(row) for row in connection.execute(text(statement), dict(params)).mappings())


def _text(value: object) -> str:
    if value is None:
        _fail("frozen source row has a null identity")
    return str(value)


def _fail(message: str) -> NoReturn:
    raise Task7CompletionError(STEP_OUTPUT_MISSING, message)
