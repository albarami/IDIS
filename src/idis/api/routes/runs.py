"""Runs routes for IDIS API.

Provides POST /v1/deals/{dealId}/runs and GET /v1/runs/{runId} per OpenAPI spec.

Supports both Postgres persistence (when configured) and in-memory fallback.

SNAPSHOT mode: INGEST_CHECK -> EXTRACT -> GRADE -> CALC.
FULL mode: INGEST_CHECK -> EXTRACT -> GRADE -> CALC -> GRAPH_EVIDENCE -> RAG_EVIDENCE
           -> ENRICHMENT -> DEBATE -> ANALYSIS -> SCORING -> DELIVERABLES.
"""

from __future__ import annotations

import asyncio
import logging
import os
import uuid
from datetime import UTC, datetime
from typing import Any, cast

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from idis.api.auth import RequireTenantContext
from idis.api.errors import IdisHttpError
from idis.audit.canonical import build_canonical_audit_event
from idis.audit.postgres_sink import PostgresAuditSink
from idis.audit.request_bound_sink import RequestBoundPostgresAuditSink
from idis.audit.sink import AuditSink, AuditSinkError
from idis.models.run_source import RunSource
from idis.persistence.repositories.run_steps import get_run_steps_repository
from idis.persistence.repositories.runs import (
    RunAlreadyActiveError,
    WorkerLeaseFencedError,
    get_runs_repository,
)
from idis.persistence.repositories.strict_preflight_proofs import (
    StrictPreflightProofError,
    get_strict_preflight_proofs_repository,
)
from idis.services.runs import strict_full_live as strict_full_live_module
from idis.services.runs.execution import RunExecutionService
from idis.services.runs.lifecycle import RunLifecycleService
from idis.services.runs.step_runtime.common import _resolve_run_audit_sink
from idis.services.runs.steps import build_run_context
from idis.services.runs.strict_full_live import (
    IDIS_STRICT_DOTENV_PATH_ENV,
    STRICT_FULL_LIVE_BLOCKED,
    build_strict_block_operator_safe_details,
    build_strict_full_live_admission_report,
    is_strict_full_live_required,
)

# The run-summary sanitizer lives in one place (services.webhooks.safe_payload); re-export the
# original private names here so existing importers of routes.runs keep resolving to that single
# implementation.
from idis.services.webhooks.safe_payload import (
    SAFE_PUBLIC_STEP_ERROR_MESSAGE,
)
from idis.services.webhooks.safe_payload import (
    safe_public_summary as _safe_public_run_summary,  # noqa: F401
)
from idis.services.webhooks.safe_payload import (
    safe_public_summary_dict as _safe_public_run_summary_dict,
)

logger = logging.getLogger(__name__)


router = APIRouter(prefix="/v1", tags=["Runs"])


class RunMethodologySelectionRequest(BaseModel):
    """Explicit immutable methodology-version selection for a FULL run."""

    model_config = ConfigDict(extra="forbid")

    commercial_dd_version_record_id: uuid.UUID
    financial_dd_version_record_id: uuid.UUID

    @model_validator(mode="after")
    def _version_records_are_distinct(self) -> RunMethodologySelectionRequest:
        if self.commercial_dd_version_record_id == self.financial_dd_version_record_id:
            raise ValueError("CDD and FDD methodology version records must be distinct")
        return self


class StartRunRequest(BaseModel):
    """Request body for POST /v1/deals/{dealId}/runs."""

    model_config = ConfigDict(extra="forbid")

    mode: str
    source: RunSource | None = None
    strict_preflight_proof_id: str | None = None
    methodology_selection: RunMethodologySelectionRequest | None = None

    @model_validator(mode="after")
    def _full_run_has_complete_methodology_selection(self) -> StartRunRequest:
        if self.mode == "FULL" and self.methodology_selection is None:
            raise ValueError("FULL run requires the frozen CDD and FDD methodology selection")
        if self.mode == "SNAPSHOT" and self.methodology_selection is not None:
            raise ValueError("SNAPSHOT run does not accept a methodology selection")
        return self


PATH_LIKE_RUN_FIELDS = frozenset(
    {
        "data_room_root_path",
        "root_path",
        "file_path",
        "folder_path",
        "local_folder",
        "local_path",
        "uri",
        "uris",
        "paths",
        "files",
    }
)


class StepErrorResponse(BaseModel):
    """Error envelope for a failed step."""

    code: str
    message: str


class RunStepResponse(BaseModel):
    """Single step in the run response."""

    step_name: str
    status: str
    started_at: str | None = None
    finished_at: str | None = None
    summary: dict[str, Any] = Field(default_factory=dict)
    error: StepErrorResponse | None = None
    retry_count: int = 0


class RunRefStepResponse(BaseModel):
    """Single step in the start-run response without observability summaries."""

    step_name: str
    status: str
    started_at: str | None = None
    finished_at: str | None = None
    error: StepErrorResponse | None = None
    retry_count: int = 0


class RunRef(BaseModel):
    """Run reference returned by startRun (202)."""

    run_id: str
    status: str
    steps: list[RunRefStepResponse] = Field(default_factory=list)
    block_reason: str | None = None


class RunStatus(BaseModel):
    """Run status response for GET /v1/runs/{runId}."""

    run_id: str
    status: str
    mode: str
    started_at: str
    finished_at: str | None = None
    source: RunSource | None = None
    steps: list[RunStepResponse] = Field(default_factory=list)
    block_reason: str | None = None


class RunListItem(BaseModel):
    """Safe run summary for the deal run list (detail + steps live in GET /v1/runs/{runId})."""

    run_id: str
    deal_id: str
    status: str
    mode: str
    started_at: str
    finished_at: str | None = None


class PaginatedRunList(BaseModel):
    """Paginated list of a deal's runs (newest first)."""

    items: list[RunListItem]
    next_cursor: str | None = None


def _validate_start_run_body(body: dict[str, Any] | None) -> StartRunRequest:
    """Validate start run request body, returning 400 for missing required fields."""
    if body is None or not isinstance(body, dict):
        raise IdisHttpError(
            status_code=400,
            code="INVALID_REQUEST",
            message="Request body is required",
        )
    if "mode" not in body:
        raise IdisHttpError(
            status_code=400,
            code="INVALID_REQUEST",
            message="Missing required field: mode",
            details={"missing_fields": ["mode"]},
        )
    mode = body.get("mode")
    if mode not in ("SNAPSHOT", "FULL"):
        raise IdisHttpError(
            status_code=400,
            code="INVALID_REQUEST",
            message="Invalid mode; must be SNAPSHOT or FULL",
        )
    rejected_fields = _path_like_fields(body)
    if rejected_fields:
        raise IdisHttpError(
            status_code=400,
            code="INVALID_REQUEST",
            message="Run creation does not accept filesystem paths or raw URI/path lists",
            details={"rejected_fields": rejected_fields},
        )
    try:
        return StartRunRequest.model_validate(body)
    except ValidationError as exc:
        raise IdisHttpError(
            status_code=400,
            code="INVALID_REQUEST",
            message="Invalid run request body",
            # Pydantic includes the original ValueError object in ``ctx`` by
            # default; that object is not JSON serializable and would turn a
            # safe client error into an internal 500 at the real API boundary.
            details={"errors": exc.errors(include_context=False)},
        ) from exc


def _path_like_fields(body: dict[str, Any]) -> list[str]:
    """Return forbidden path-like fields from a potentially nested run request."""
    rejected: set[str] = set()
    for key, value in body.items():
        if key in PATH_LIKE_RUN_FIELDS:
            rejected.add(key)
        if isinstance(value, dict):
            rejected.update(field for field in value if field in PATH_LIKE_RUN_FIELDS)
    return sorted(rejected)


def _methodology_record_value(record: Any, field: str) -> Any:
    if isinstance(record, dict):
        return record[field]
    return getattr(record, field)


def _methodology_configuration(request: Request) -> Any:
    profile = getattr(request.app.state, "strict_runtime_profile", None)
    values = getattr(profile, "values", None)
    return values if values is not None else os.environ


def _validate_full_methodology_selection(
    *,
    request: Request,
    tenant_id: str,
    selection: RunMethodologySelectionRequest,
) -> Any:
    """Return the durable repository after exact frozen-source validation."""

    from idis.api.routes.methodology_versions import resolve_server_registry
    from idis.methodology.models import MethodologyRegistry, MethodologyType
    from idis.persistence.repositories.methodologies import (
        MethodologiesRepositoryError,
        get_methodologies_repository,
    )

    db_conn = getattr(request.state, "db_conn", None)
    try:
        repository = get_methodologies_repository(db_conn, tenant_id)
        selected = (
            (
                MethodologyType.COMMERCIAL_DD,
                repository.get_frozen_version(
                    methodology_version_record_id=str(selection.commercial_dd_version_record_id)
                ),
            ),
            (
                MethodologyType.FINANCIAL_DD,
                repository.get_frozen_version(
                    methodology_version_record_id=str(selection.financial_dd_version_record_id)
                ),
            ),
        )
    except MethodologiesRepositoryError as exc:
        raise IdisHttpError(
            status_code=409,
            code="METHODOLOGY_COVERAGE_INCOMPLETE",
            message="Both frozen CDD and FDD methodology versions are required",
        ) from exc

    configuration = _methodology_configuration(request)
    for expected_type, record in selected:
        try:
            actual_type = MethodologyType(_methodology_record_value(record, "methodology_type"))
            current_registry, _byte_count, _source_type, _source_alias = resolve_server_registry(
                expected_type, configuration=configuration
            )
            persisted_registry = _methodology_record_value(record, "registry")
            if not isinstance(persisted_registry, MethodologyRegistry):
                raise ValueError("persisted registry is unavailable")
            if (
                actual_type is not expected_type
                or str(_methodology_record_value(record, "status")) != "FROZEN"
                or str(_methodology_record_value(record, "source_sha256"))
                != current_registry.current_version.source_hash
                or str(_methodology_record_value(record, "registry_hash"))
                != current_registry.registry_hash
                or persisted_registry.to_deterministic_json()
                != current_registry.to_deterministic_json()
            ):
                raise ValueError("frozen methodology source binding drifted")
        except IdisHttpError:
            raise
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            raise IdisHttpError(
                status_code=409,
                code="METHODOLOGY_COVERAGE_INCOMPLETE",
                message="The selected methodology version failed source validation",
            ) from exc
    return repository


def resolve_strict_preflight_binding(
    *,
    request: Request,
    tenant_id: str,
    deal_id: str,
    preflight_corpus: list[dict[str, Any]],
    strict_report: Any,
) -> Any:
    """Resolve the complete server-side binding for a strict proof.

    Slice100's later tasks will install the producer after the governed
    pipeline, methodology, prompt, provider, and dependency receipts exist.
    Task 1 deliberately provides no partial/default binding: strict admission
    stays closed until that complete resolver is wired.
    """
    resolver = getattr(request.app.state, "strict_preflight_binding_resolver", None)
    if not callable(resolver):
        raise StrictPreflightProofError("STRICT_PREFLIGHT_PROOF_MISMATCH")
    binding = resolver(
        tenant_id=tenant_id,
        deal_id=deal_id,
        preflight_corpus=preflight_corpus,
        strict_report=strict_report,
    )
    if binding is None:
        raise StrictPreflightProofError("STRICT_PREFLIGHT_PROOF_MISMATCH")
    return binding


def _public_source_freeze_code(exc: Exception) -> str | None:
    """Return the safe 409 code for a freeze failure, or None to propagate it.

    Only prerequisite-class codes map to SOURCE_PREPARATION_PREREQUISITE_UNMET.
    Fence races map to the retryable SOURCE_FREEZE_CONFLICT code. The internal
    code is logged server-side and is not placed on the returned public code.
    Any other freeze code returns None so the caller re-raises it as a 500.

    Args:
        exc: Failure raised by ``freeze_source_package``.

    Returns:
        A public 409 code, or None when the failure must not be relabelled.
    """
    from idis.persistence.repositories.task7_source_freeze import (
        TASK7_PARSE_ATTEMPT_LINEAGE_UNRECONSTRUCTIBLE,
        TASK7_SOURCE_FREEZE_FENCED,
        TASK7_SOURCE_TERMINAL_STATE_INVALID,
        Task7SourceFreezeError,
    )

    if not isinstance(exc, Task7SourceFreezeError):
        return None
    logger.error("Task 7 source freeze refused with internal code %s", exc.code)
    if exc.code in {
        TASK7_SOURCE_TERMINAL_STATE_INVALID,
        TASK7_PARSE_ATTEMPT_LINEAGE_UNRECONSTRUCTIBLE,
    }:
        return "SOURCE_PREPARATION_PREREQUISITE_UNMET"
    if exc.code == TASK7_SOURCE_FREEZE_FENCED:
        return "SOURCE_FREEZE_CONFLICT"
    return None


def _reload_frozen_preflight_corpus(
    *,
    db_conn: Any,
    tenant_id: str,
    deal_id: str,
    selected_document_ids: tuple[str, ...],
) -> list[dict[str, Any]]:
    """Reload a frozen corpus, mapping only the missing-source refusal.

    ``FROZEN_SOURCE_REQUIRED`` becomes SOURCE_PACKAGE_REQUIRED. Any other
    ``ValueError`` propagates so a loader bug is a 500, not a package refusal.
    The internal code is logged server-side only.

    Args:
        db_conn: Request-scoped database connection.
        tenant_id: Tenant that owns the deal.
        deal_id: Deal whose frozen corpus is reloaded.
        selected_document_ids: Document ids admitted for this run.

    Returns:
        The frozen preflight corpus.

    Raises:
        StrictPreflightProofError: When the frozen source is absent.
        ValueError: When the loader fails for any other reason.
    """
    from idis.services.runs.steps import load_document_preflight_corpus_for_deal

    try:
        return load_document_preflight_corpus_for_deal(
            db_conn=db_conn,
            tenant_id=tenant_id,
            deal_id=deal_id,
            selected_document_ids=selected_document_ids,
            require_frozen=True,
        )
    except ValueError as exc:
        if exc.args != ("FROZEN_SOURCE_REQUIRED",):
            raise
        logger.error(
            "Frozen source reload refused with internal code %s",
            "FROZEN_SOURCE_REQUIRED",
        )
        raise StrictPreflightProofError("SOURCE_PACKAGE_REQUIRED") from exc


def _freeze_task7_selected_source_packages(
    *,
    request: Request,
    tenant_id: str,
    deal_id: str,
    selected_document_ids: tuple[str, ...],
) -> tuple[str, ...]:
    """Freeze every complete selected package before any run admission."""

    from sqlalchemy import text

    from idis.models.source_package_manifest import SourcePackageManifest
    from idis.persistence.db import get_app_engine
    from idis.persistence.repositories.task7_source_freeze import (
        Task7SourceFreezeError,
        freeze_source_package,
    )
    from idis.services.ingestion.no_substantive_content_attestation import (
        VerifiedDocumentNoSubstantiveContentAttestation,
        VerifiedDocumentNoSubstantiveContentAttestationManifest,
        load_document_no_substantive_content_attestation,
    )

    db_conn = getattr(request.state, "db_conn", None)
    if db_conn is None or not selected_document_ids:
        raise StrictPreflightProofError("SOURCE_PACKAGE_REQUIRED")
    try:
        selected = tuple(sorted(str(uuid.UUID(value)) for value in selected_document_ids))
    except (AttributeError, TypeError, ValueError) as exc:
        raise StrictPreflightProofError("SOURCE_PACKAGE_REQUIRED") from exc
    if len(set(selected)) != len(selected):
        raise StrictPreflightProofError("SOURCE_PACKAGE_REQUIRED")

    package_rows = db_conn.execute(
        text(
            """
            SELECT package_id, metadata -> 'source_manifest' AS source_manifest
            FROM data_room_packages
            WHERE tenant_id = CAST(:tenant_id AS uuid)
              AND deal_id = CAST(:deal_id AS uuid)
              AND metadata ? 'source_manifest'
            ORDER BY package_id
            """
        ),
        {"tenant_id": tenant_id, "deal_id": deal_id},
    ).fetchall()
    selected_set = set(selected)
    matched_packages: list[tuple[str, frozenset[str]]] = []
    document_owner: dict[str, str] = {}
    try:
        for row in package_rows:
            mapping = getattr(row, "_mapping", row)
            manifest = SourcePackageManifest.model_validate(mapping["source_manifest"])
            package_id = str(uuid.UUID(str(mapping["package_id"])))
            members = frozenset(str(member.document_id) for member in manifest.files)
            overlap = selected_set & members
            if not overlap:
                continue
            for document_id in overlap:
                if document_id in document_owner:
                    raise ValueError("selected document belongs to multiple packages")
                document_owner[document_id] = package_id
            matched_packages.append((package_id, members))
    except (KeyError, TypeError, ValueError, ValidationError) as exc:
        raise StrictPreflightProofError("SOURCE_PACKAGE_REQUIRED") from exc

    if set(document_owner) != selected_set or not matched_packages:
        raise StrictPreflightProofError("SOURCE_PACKAGE_REQUIRED")
    selected_package_members = frozenset(
        document_id for _package_id, members in matched_packages for document_id in members
    )
    if selected_package_members != selected_set:
        # Current-limitation diagnostic for RUN_STATE input-support mapping
        # item 3. Subset selection of one document from a multi-document
        # package remains a documented obligation. Until subset binding lands,
        # this refusal only reports that a partial selection is not yet
        # admitted. It does not retire that obligation.
        raise StrictPreflightProofError("PARTIAL_PACKAGE_SELECTION_REFUSED")
    package_ids = tuple(sorted(package_id for package_id, _members in matched_packages))
    if len(set(package_ids)) != len(package_ids):
        raise StrictPreflightProofError("SOURCE_PACKAGE_REQUIRED")

    authority = load_document_no_substantive_content_attestation(
        env=_methodology_configuration(request)
    )
    if authority is not None and not isinstance(
        authority,
        (
            VerifiedDocumentNoSubstantiveContentAttestation,
            VerifiedDocumentNoSubstantiveContentAttestationManifest,
        ),
    ):
        logger.error("NO_SUBSTANTIVE_CONTENT_ATTESTATION_AUTHORITY_INVALID")
        raise RuntimeError("NO_SUBSTANTIVE_CONTENT_ATTESTATION_AUTHORITY_INVALID")
    # CR-C23J-07: freeze_source_package commits on its own get_app_engine()
    # connection before run admission. Request-scoped db_conn relies on READ
    # COMMITTED visibility to observe the newly SEALED/BOUND rows below; if a
    # later 409 (e.g. RUN_ALREADY_ACTIVE) or 500 stops run creation, the
    # package intentionally remains SEALED/BOUND for idempotent reuse.
    engine = get_app_engine()
    try:
        for package_id in package_ids:
            freeze_source_package(
                engine=engine,
                tenant_id=tenant_id,
                deal_id=deal_id,
                package_id=package_id,
                terminal_exclusion_authority=authority,
            )
    except Task7SourceFreezeError as exc:
        public_code = _public_source_freeze_code(exc)
        if public_code is None:
            raise
        raise StrictPreflightProofError(public_code) from exc
    return package_ids


@router.post("/deals/{deal_id}/runs", response_model=RunRef, status_code=202)
async def start_run(
    deal_id: str,
    request: Request,
    tenant_ctx: RequireTenantContext,
) -> RunRef:
    """Start an IDIS pipeline run.

    Args:
        deal_id: UUID of the deal to run pipeline for.
        request: FastAPI request for DB connection and body access.
        tenant_ctx: Injected tenant context from auth dependency.

    Raises:
        IdisHttpError: 400 if invalid/missing fields, 404 if deal not found,
            500 if audit emission fails (fail-closed).

    Returns:
        RunRef with run_id and initial status.
    """
    try:
        body = await request.json()
    except Exception:
        body = None
    request_body = _validate_start_run_body(body)

    run_id = str(uuid.uuid4())
    db_conn = getattr(request.state, "db_conn", None)
    idempotency_key = request.headers.get("Idempotency-Key")

    runs_repo = get_runs_repository(db_conn, tenant_ctx.tenant_id)
    if not runs_repo.deal_exists(deal_id):
        raise IdisHttpError(status_code=404, code="NOT_FOUND", message="Deal not found")

    # DEC-D: at most one active (QUEUED/RUNNING) run per (tenant, deal). Fail fast before the
    # preflight/strict work; the create() path + Postgres partial unique index are the backstop.
    if runs_repo.has_active_run(deal_id):
        raise IdisHttpError(
            status_code=409,
            code="RUN_ALREADY_ACTIVE",
            message="An active run already exists for this deal",
        )

    methodologies_repo: Any | None = None
    if request_body.mode == "FULL":
        # Validate both frozen, source-stable methodology families before the
        # run INSERT.  Versions become immutable once frozen, so the later
        # selection INSERT can share the request transaction with run creation
        # without a check-then-mutate race.
        methodologies_repo = _validate_full_methodology_selection(
            request=request,
            tenant_id=tenant_ctx.tenant_id,
            selection=cast(RunMethodologySelectionRequest, request_body.methodology_selection),
        )

    preflight_corpus = _gather_preflight_corpus(request, tenant_ctx.tenant_id, deal_id)
    preflight_corpus = _apply_run_source_to_preflight_corpus(
        preflight_corpus=preflight_corpus,
        source=request_body.source,
    )
    if not preflight_corpus:
        raise IdisHttpError(
            status_code=400,
            code="NO_INGESTED_DOCUMENTS",
            message=(
                "Deal has no ingested documents; ingest at least one document before starting a run"
            ),
        )
    documents = _extraction_ready_documents_from_preflight_corpus(preflight_corpus)

    strict_dotenv_path = os.environ.get(IDIS_STRICT_DOTENV_PATH_ENV)
    strict_live_extraction_required = request_body.mode == "FULL" and is_strict_full_live_required(
        dotenv_path=strict_dotenv_path
    )
    strict_report: Any | None = None
    if strict_live_extraction_required:
        if request_body.strict_preflight_proof_id is None:
            raise IdisHttpError(
                status_code=409,
                code="STRICT_PREFLIGHT_PROOF_REQUIRED",
                message="A fresh strict preflight proof is required for this run",
            )
        strict_report = build_strict_full_live_admission_report(
            db_conn=db_conn,
            tenant_id=tenant_ctx.tenant_id,
            preflight_corpus=preflight_corpus,
            strict_dotenv_path=strict_dotenv_path,
        )
        if not strict_report.may_proceed:
            raise IdisHttpError(
                status_code=409,
                code=STRICT_FULL_LIVE_BLOCKED,
                message=("Strict full-live preflight blocked this FULL run before execution"),
                details=build_strict_block_operator_safe_details(strict_report),
            )

    extractor_configured = getattr(request.app.state, "extractor_configured", True)
    if not extractor_configured:
        raise IdisHttpError(
            status_code=503,
            code="EXTRACTOR_NOT_CONFIGURED",
            message="No claim extractor is configured. Cannot proceed.",
        )

    try:
        selected_document_ids = tuple(
            sorted(
                str(document["document_id"])
                for document in preflight_corpus
                if document.get("document_id") is not None
            )
        )
        source = RunSource.model_validate(
            {"type": "deal_documents", "document_ids": list(selected_document_ids)}
        ).to_storage_dict()
        selected_source_package_ids = _freeze_task7_selected_source_packages(
            request=request,
            tenant_id=tenant_ctx.tenant_id,
            deal_id=deal_id,
            selected_document_ids=selected_document_ids,
        )
        preflight_corpus = _reload_frozen_preflight_corpus(
            db_conn=db_conn,
            tenant_id=tenant_ctx.tenant_id,
            deal_id=deal_id,
            selected_document_ids=selected_document_ids,
        )
        documents = _extraction_ready_documents_from_preflight_corpus(preflight_corpus)
        if strict_live_extraction_required:
            expected_binding = resolve_strict_preflight_binding(
                request=request,
                tenant_id=tenant_ctx.tenant_id,
                deal_id=deal_id,
                preflight_corpus=preflight_corpus,
                strict_report=strict_report,
            )
            proof_repo = get_strict_preflight_proofs_repository(db_conn, tenant_ctx.tenant_id)
            run_data = proof_repo.consume_and_admit_contract_run(
                proof_id=cast(str, request_body.strict_preflight_proof_id),
                expected_binding=expected_binding,
                run_id=run_id,
                deal_id=deal_id,
                mode=request_body.mode,
                idempotency_key=idempotency_key,
                source=source,
                created_by_actor_id=tenant_ctx.actor_id,
                created_by_actor_type=tenant_ctx.actor_type,
                selected_source_package_ids=selected_source_package_ids,
            )
        else:
            run_data = runs_repo.create(
                run_id=run_id,
                deal_id=deal_id,
                mode=request_body.mode,
                idempotency_key=idempotency_key,
                source=source,
                created_by_actor_id=tenant_ctx.actor_id,
                created_by_actor_type=tenant_ctx.actor_type,
            )
    except StrictPreflightProofError as exc:
        message = (
            "Source freeze conflict; retry the request"
            if exc.code == "SOURCE_FREEZE_CONFLICT"
            else "Frozen source preparation or strict proof did not authorize this run"
        )
        raise IdisHttpError(
            status_code=409,
            code=exc.code,
            message=message,
        ) from exc
    except RunAlreadyActiveError as exc:
        # Loser of a concurrent create race (past the fail-fast check) -> same safe 409.
        raise IdisHttpError(
            status_code=409,
            code="RUN_ALREADY_ACTIVE",
            message="An active run already exists for this deal",
        ) from exc

    if methodologies_repo is not None:
        selection_audit_event_id = str(uuid.uuid4())
        try:
            methodologies_repo.create_run_selection(
                deal_id=deal_id,
                run_id=run_id,
                commercial_dd_version_record_id=str(
                    cast(
                        RunMethodologySelectionRequest,
                        request_body.methodology_selection,
                    ).commercial_dd_version_record_id
                ),
                financial_dd_version_record_id=str(
                    cast(
                        RunMethodologySelectionRequest,
                        request_body.methodology_selection,
                    ).financial_dd_version_record_id
                ),
                created_by_actor_id=tenant_ctx.actor_id,
                selection_audit_event_id=selection_audit_event_id,
            )
        except Exception as exc:
            # A failure after run insertion must be a 5xx so the request-scoped
            # transaction rolls back the run and selection together.  Returning
            # a 4xx here would commit an orphan FULL run.
            logger.error("Durable methodology selection failed for run %s", run_id)
            raise IdisHttpError(
                status_code=500,
                code="METHODOLOGY_COVERAGE_INCOMPLETE",
                message="Run methodology selection could not be persisted",
            ) from exc
        request.state.audit_event_id = selection_audit_event_id

    request.state.audit_resource_id = run_id

    if strict_live_extraction_required:
        return RunRef(
            run_id=run_data["run_id"],
            status=run_data["status"],
            steps=[],
            block_reason=None,
        )

    audit_sink = _get_audit_sink(request)
    if db_conn is not None and isinstance(audit_sink, PostgresAuditSink):
        audit_sink = RequestBoundPostgresAuditSink(
            connection=db_conn,
            delegate=audit_sink,
            tenant_id=tenant_ctx.tenant_id,
            actor_id=tenant_ctx.actor_id,
            actor_type=tenant_ctx.actor_type,
            actor_roles=tenant_ctx.roles,
            request_id=request.state.request_id,
            method=request.method,
            path=request.url.path,
            status_code=202,
        )
        request.state.run_audit_sink = audit_sink

    def emit_inline_claim(ctx: Any, _identity: dict[str, str]) -> None:
        claimed = runs_repo.get(ctx.run_id)
        if (
            claimed is None
            or claimed.get("lease_owner_id") != ctx.lease_owner_id
            or claimed.get("lease_fencing_token") != ctx.lease_fencing_token
            or claimed.get("lease_expires_at") is None
        ):
            raise AuditSinkError("INLINE_CLAIM_AUDIT_IDENTITY_UNAVAILABLE")
        audit_sink.emit(
            build_canonical_audit_event(
                tenant_id=tenant_ctx.tenant_id,
                event_type="worker.lease.claimed",
                severity="MEDIUM",
                summary="API-inline execution lease claimed",
                actor_id=tenant_ctx.actor_id,
                actor_type=tenant_ctx.actor_type,
                actor_roles=tenant_ctx.roles,
                request_id=request.state.request_id,
                method=request.method,
                path=request.url.path,
                status_code=202,
                resource_type="deal",
                resource_id=deal_id,
                deal_id=deal_id,
                safe={
                    "code": "WORKER_LEASE_CLAIMED",
                    "run_id": ctx.run_id,
                    "lease_owner_id": ctx.lease_owner_id,
                    "new_fencing_token": ctx.lease_fencing_token,
                    "lease_expires_at": str(claimed["lease_expires_at"]),
                    "execution_origin": "API_INLINE",
                },
            )
        )

    run_steps_repo = get_run_steps_repository(db_conn, tenant_ctx.tenant_id)
    execution_service = RunExecutionService(
        audit_sink=audit_sink,
        runs_repo=runs_repo,
        run_steps_repo=run_steps_repo,
        claim_audit_emitter=emit_inline_claim,
        webhook_connection=db_conn,
    )

    ctx = build_run_context(
        db_conn=db_conn,
        run_id=run_id,
        tenant_id=tenant_ctx.tenant_id,
        deal_id=deal_id,
        mode=request_body.mode,
        created_by_actor_id=tenant_ctx.actor_id,
        created_by_actor_type=tenant_ctx.actor_type,
        documents=documents,
        deal_metadata=_load_deal_metadata_for_run(request, tenant_ctx.tenant_id, deal_id),
        preflight_corpus=preflight_corpus,
        audit_sink=audit_sink,
        strict_live_extraction_required=strict_live_extraction_required,
        strict_live_debate_backend_required=strict_live_extraction_required,
        pipeline_contract_version=(run_data.get("pipeline_contract_version")),
        pipeline_contract_hash=(run_data.get("pipeline_contract_hash")),
        pipeline_resume_state=(run_data.get("pipeline_resume_state")),
    )

    try:
        execution_result = await asyncio.to_thread(execution_service.execute, ctx)
        if isinstance(audit_sink, RequestBoundPostgresAuditSink):
            audit_sink.raise_if_failed()
    except WorkerLeaseFencedError as exc:
        raise IdisHttpError(
            status_code=503,
            code="WORKER_LEASE_FENCED",
            message="Execution lease expired or was fenced; start a fresh frozen-source run",
        ) from exc
    except AuditSinkError as exc:
        logger.error("Audit failure aborted run %s: %s", run_id, exc)
        raise IdisHttpError(
            status_code=500,
            code="AUDIT_FAILURE",
            message="Run aborted: audit event emission failed",
        ) from exc

    if not execution_result.claimed:
        raise IdisHttpError(
            status_code=409,
            code="RUN_ALREADY_CLAIMED",
            message="Run was already claimed for execution",
        )

    finished_at = execution_result.finished_at or datetime.now(UTC).isoformat().replace(
        "+00:00", "Z"
    )
    run_data["status"] = execution_result.status
    run_data["finished_at"] = finished_at
    run_data["block_reason"] = execution_result.block_reason

    try:
        _emit_run_completed_audit(request, run_id, tenant_ctx.tenant_id, run_data["status"])
    except AuditSinkError as exc:
        logger.error("Audit failure on run.completed for run %s: %s", run_id, exc)
        raise IdisHttpError(
            status_code=500,
            code="AUDIT_FAILURE",
            message="Run completed but audit event emission failed",
        ) from exc

    step_responses = _build_run_ref_step_responses(execution_result.steps)

    return RunRef(
        run_id=run_data["run_id"],
        status=run_data["status"],
        steps=step_responses,
        block_reason=execution_result.block_reason,
    )


@router.get("/runs/{run_id}", response_model=RunStatus)
def get_run(
    run_id: str,
    request: Request,
    tenant_ctx: RequireTenantContext,
) -> RunStatus:
    """Get run status.

    Args:
        run_id: UUID of the run to retrieve.
        request: FastAPI request for DB connection access.
        tenant_ctx: Injected tenant context from auth dependency.

    Returns:
        RunStatus with run details and step ledger.

    Raises:
        IdisHttpError: 404 if run not found or belongs to different tenant.
    """
    db_conn = getattr(request.state, "db_conn", None)

    runs_repo = get_runs_repository(db_conn, tenant_ctx.tenant_id)
    run_data = runs_repo.get(run_id)

    if run_data is None:
        raise IdisHttpError(status_code=404, code="NOT_FOUND", message="Run not found")

    run_steps_repo = get_run_steps_repository(db_conn, tenant_ctx.tenant_id)
    steps = run_steps_repo.get_by_run_id(run_id)
    step_responses = _build_step_responses(steps)

    return RunStatus(
        run_id=run_data["run_id"],
        status=run_data["status"],
        mode=run_data["mode"],
        started_at=run_data["started_at"],
        finished_at=run_data.get("finished_at"),
        source=_run_source_from_storage(run_data.get("source")),
        steps=step_responses,
        block_reason=run_data.get("block_reason") or _derive_block_reason(steps),
    )


@router.get("/deals/{deal_id}/runs", response_model=PaginatedRunList)
def list_deal_runs(
    deal_id: str,
    request: Request,
    tenant_ctx: RequireTenantContext,
    limit: int = Query(default=50, ge=1, le=200),
    cursor: str | None = Query(default=None),
) -> PaginatedRunList:
    """List a deal's runs as safe summaries, newest first, cursor-paginated.

    The cursor is an opaque, stable composite of (created_at, run_id) so runs sharing a
    created_at are never dropped across pages. Per-run blocker detail and the step ledger
    stay in GET /v1/runs/{run_id}.
    """
    db_conn = getattr(request.state, "db_conn", None)
    runs_repo = get_runs_repository(db_conn, tenant_ctx.tenant_id)
    rows, next_cursor = runs_repo.list_by_deal(deal_id=deal_id, limit=limit, cursor=cursor)
    items = [
        RunListItem(
            run_id=row["run_id"],
            deal_id=row["deal_id"],
            status=row["status"],
            mode=row["mode"],
            started_at=row["started_at"],
            finished_at=row.get("finished_at"),
        )
        for row in rows
    ]
    return PaginatedRunList(items=items, next_cursor=next_cursor)


@router.post("/runs/{run_id}/retry", response_model=RunRef, status_code=202)
def retry_run(
    run_id: str,
    request: Request,
    tenant_ctx: RequireTenantContext,
) -> RunRef:
    """Requeue a failed run after validating strict admission constraints."""
    return _retry_or_resume_run(run_id=run_id, request=request, tenant_ctx=tenant_ctx)


@router.post("/runs/{run_id}/resume", response_model=RunRef, status_code=202)
def resume_run(
    run_id: str,
    request: Request,
    tenant_ctx: RequireTenantContext,
) -> RunRef:
    """Alias retry for a failed run."""
    return _retry_or_resume_run(run_id=run_id, request=request, tenant_ctx=tenant_ctx)


@router.post("/runs/{run_id}/cancel", response_model=RunRef, status_code=202)
def cancel_run(
    run_id: str,
    request: Request,
    tenant_ctx: RequireTenantContext,
) -> RunRef:
    """Cancel a queued or running run without invoking execution admission."""
    request.state.audit_resource_id = run_id
    db_conn = getattr(request.state, "db_conn", None)
    runs_repo = get_runs_repository(db_conn, tenant_ctx.tenant_id)
    run_steps_repo = get_run_steps_repository(db_conn, tenant_ctx.tenant_id)
    lifecycle = RunLifecycleService(runs_repo=runs_repo, run_steps_repo=run_steps_repo)

    run_data = runs_repo.get(run_id)
    if run_data is None:
        raise IdisHttpError(status_code=404, code="NOT_FOUND", message="Run not found")
    if str(run_data.get("status")) not in {"QUEUED", "RUNNING"}:
        raise IdisHttpError(
            status_code=409,
            code="RUN_NOT_CANCELABLE",
            message="Only queued or running runs can be cancelled",
        )
    if not lifecycle.request_cancel(run_id=run_id, tenant_id=tenant_ctx.tenant_id):
        raise IdisHttpError(
            status_code=409,
            code="RUN_NOT_CANCELABLE",
            message="Only queued or running runs can be cancelled",
        )
    return RunRef(run_id=run_id, status="CANCELLED", steps=[], block_reason=None)


def _retry_or_resume_run(
    *,
    run_id: str,
    request: Request,
    tenant_ctx: RequireTenantContext,
) -> RunRef:
    request.state.audit_resource_id = run_id
    db_conn = getattr(request.state, "db_conn", None)
    runs_repo = get_runs_repository(db_conn, tenant_ctx.tenant_id)
    run_steps_repo = get_run_steps_repository(db_conn, tenant_ctx.tenant_id)
    lifecycle = RunLifecycleService(runs_repo=runs_repo, run_steps_repo=run_steps_repo)

    run_data = runs_repo.get(run_id)
    if run_data is None:
        raise IdisHttpError(status_code=404, code="NOT_FOUND", message="Run not found")
    if str(run_data.get("status")) != "FAILED":
        raise IdisHttpError(
            status_code=409,
            code="RUN_NOT_RETRYABLE",
            message="Only failed runs can be retried",
        )
    if run_data.get("pipeline_resume_state") != "RESUMABLE":
        raise IdisHttpError(
            status_code=409,
            code="LEGACY_RUN_RESUME_UNSAFE",
            message="This run cannot resume; start a new run after frozen-source admission",
        )

    run_source = _run_source_from_storage(run_data.get("source"))
    strict_dotenv_path = os.getenv(IDIS_STRICT_DOTENV_PATH_ENV)
    if str(run_data.get("mode", "")).upper() == "FULL" and is_strict_full_live_required(
        dotenv_path=strict_dotenv_path
    ):
        preflight_corpus = _gather_preflight_corpus(
            request=request,
            tenant_id=tenant_ctx.tenant_id,
            deal_id=str(run_data["deal_id"]),
        )
        try:
            preflight_corpus = _apply_run_source_to_preflight_corpus(
                preflight_corpus=preflight_corpus,
                source=run_source,
            )
        except IdisHttpError as exc:
            if exc.code == "INVALID_RUN_SOURCE":
                lifecycle.persist_failed_block(
                    run_id=run_id,
                    tenant_id=tenant_ctx.tenant_id,
                    reason_code="INVALID_RUN_SOURCE",
                    message="Persisted run source is invalid for strict retry",
                )
                request.state.audit_mutation_occurred_on_error = True
                raise IdisHttpError(
                    status_code=409,
                    code="INVALID_RUN_SOURCE",
                    message="Persisted run source is invalid for strict retry",
                    details=exc.details,
                ) from exc
            raise

        strict_report = strict_full_live_module.build_strict_full_live_admission_report(
            db_conn=db_conn,
            tenant_id=tenant_ctx.tenant_id,
            preflight_corpus=preflight_corpus,
            strict_dotenv_path=strict_dotenv_path,
        )
        if not strict_report.may_proceed:
            blocking_provenance = strict_full_live_module.build_blocking_step_provenance(
                strict_report
            )
            lifecycle.persist_failed_block(
                run_id=run_id,
                tenant_id=tenant_ctx.tenant_id,
                reason_code=STRICT_FULL_LIVE_BLOCKED,
                message="Strict full live retry admission blocked",
                provenance_items=[item.model_dump(mode="json") for item in blocking_provenance],
            )
            request.state.audit_mutation_occurred_on_error = True
            raise IdisHttpError(
                status_code=409,
                code=STRICT_FULL_LIVE_BLOCKED,
                message="Strict full live admission blocked retry",
            )

    if not lifecycle.request_retry(run_id=run_id):
        raise IdisHttpError(
            status_code=409,
            code="RUN_NOT_RETRYABLE",
            message="Only failed runs can be retried",
        )
    return RunRef(
        run_id=run_id,
        status="QUEUED",
        steps=[],
        block_reason=None,
    )


def _gather_snapshot_documents(
    request: Request,
    tenant_id: str,
    deal_id: str,
) -> list[dict[str, Any]]:
    """Gather ingested document spans for SNAPSHOT extraction.

    Loads from the request-scoped Postgres corpus first when DB is configured.
    Test-only in-memory documents are used only when no DB connection exists.

    Args:
        request: FastAPI request.
        tenant_id: Tenant UUID.
        deal_id: Deal UUID.

    Returns:
        List of document dicts with doc_type, document_id, spans.
    """
    db_conn = getattr(request.state, "db_conn", None)
    if db_conn is not None:
        from idis.services.runs.steps import load_documents_for_deal

        return load_documents_for_deal(
            db_conn=db_conn,
            deal_id=deal_id,
            tenant_id=tenant_id,
        )

    test_docs: list[dict[str, Any]] = getattr(
        request.state,
        "snapshot_documents",
        [],
    )
    if test_docs:
        return test_docs

    deal_documents: dict[str, list[dict[str, Any]]] = getattr(
        request.app.state,
        "deal_documents",
        {},
    )
    if deal_id in deal_documents:
        return deal_documents[deal_id]

    ingestion_service = getattr(request.app.state, "ingestion_service", None)
    if ingestion_service is None:
        return []

    documents: list[dict[str, Any]] = []
    for _key, doc in ingestion_service._documents.items():
        if str(doc.deal_id) != deal_id:
            continue
        if str(doc.tenant_id) != tenant_id:
            continue
        spans = ingestion_service.get_spans(doc.tenant_id, doc.document_id)
        if not spans:
            continue
        span_dicts = [
            {
                "span_id": str(s.span_id),
                "text_excerpt": s.text_excerpt,
                "locator": s.locator if isinstance(s.locator, dict) else {},
                "span_type": (
                    s.span_type.value if hasattr(s.span_type, "value") else str(s.span_type)
                ),
            }
            for s in spans
        ]
        documents.append(
            {
                "document_id": str(doc.document_id),
                "doc_type": (
                    doc.doc_type.value if hasattr(doc.doc_type, "value") else str(doc.doc_type)
                ),
                "document_name": str(doc.document_id),
                "spans": span_dicts,
            }
        )

    return documents


def _gather_preflight_corpus(
    request: Request,
    tenant_id: str,
    deal_id: str,
) -> list[dict[str, Any]]:
    """Gather the full persisted corpus for DOCUMENT_PREFLIGHT."""
    db_conn = getattr(request.state, "db_conn", None)
    if db_conn is not None:
        from idis.services.runs.steps import load_document_preflight_corpus_for_deal

        return load_document_preflight_corpus_for_deal(
            db_conn=db_conn,
            deal_id=deal_id,
            tenant_id=tenant_id,
        )

    test_docs: list[dict[str, Any]] = getattr(
        request.state,
        "snapshot_documents",
        [],
    )
    if test_docs:
        return test_docs

    deal_documents: dict[str, list[dict[str, Any]]] = getattr(
        request.app.state,
        "deal_documents",
        {},
    )
    if deal_id in deal_documents:
        return deal_documents[deal_id]

    ingestion_corpus = _gather_ingestion_service_preflight_corpus(
        request,
        tenant_id=tenant_id,
        deal_id=deal_id,
    )
    if ingestion_corpus:
        return ingestion_corpus

    return _gather_snapshot_documents(request, tenant_id, deal_id)


def _gather_ingestion_service_preflight_corpus(
    request: Request,
    *,
    tenant_id: str,
    deal_id: str,
) -> list[dict[str, Any]]:
    """Load full no-DB upload corpus from the app ingestion service state."""
    ingestion_service = getattr(request.app.state, "ingestion_service", None)
    if ingestion_service is None:
        return []

    corpus: list[dict[str, Any]] = []
    for _key, doc in ingestion_service._documents.items():
        if str(doc.deal_id) != deal_id:
            continue
        if str(doc.tenant_id) != tenant_id:
            continue
        artifact = ingestion_service.get_artifact(doc.tenant_id, doc.doc_id)
        spans = ingestion_service.get_spans(doc.tenant_id, doc.document_id)
        corpus.append(
            {
                "tenant_id": str(doc.tenant_id),
                "deal_id": str(doc.deal_id),
                "document_id": str(doc.document_id),
                "doc_id": str(doc.doc_id),
                "doc_type": doc.doc_type.value
                if hasattr(doc.doc_type, "value")
                else str(doc.doc_type),
                "parse_status": (
                    doc.parse_status.value
                    if hasattr(doc.parse_status, "value")
                    else str(doc.parse_status)
                ),
                "document_name": (
                    str(artifact.title)
                    if artifact is not None and getattr(artifact, "title", None)
                    else str(doc.document_id)
                ),
                "sha256": artifact.sha256 if artifact is not None else None,
                "uri": artifact.uri if artifact is not None else None,
                "metadata": dict(getattr(doc, "metadata", {}) or {}),
                "source_metadata": (
                    dict(getattr(artifact, "metadata", {}) or {}) if artifact is not None else {}
                ),
                "spans": [_ingestion_span_for_preflight(span) for span in spans],
            }
        )
    return corpus


def _ingestion_span_for_preflight(span: Any) -> dict[str, Any]:
    return {
        "span_id": str(span.span_id),
        "tenant_id": str(span.tenant_id),
        "deal_id": str(span.deal_id) if getattr(span, "deal_id", None) is not None else None,
        "document_id": str(span.document_id),
        "span_type": span.span_type.value
        if hasattr(span.span_type, "value")
        else str(span.span_type),
        "locator": span.locator if isinstance(span.locator, dict) else {},
        "text_excerpt": span.text_excerpt,
        "content_hash": span.content_hash,
    }


def _apply_run_source_to_preflight_corpus(
    *,
    preflight_corpus: list[dict[str, Any]],
    source: RunSource | None,
) -> list[dict[str, Any]]:
    """Validate and apply a durable deal-documents run source."""
    if source is None:
        return preflight_corpus

    from idis.services.runs.steps import (
        filter_preflight_corpus_by_run_source,
        missing_document_ids_for_run_source,
    )

    missing_ids = missing_document_ids_for_run_source(preflight_corpus, source)
    if missing_ids:
        raise IdisHttpError(
            status_code=400,
            code="INVALID_RUN_SOURCE",
            message="Selected run-source documents are not present in this deal corpus",
            details={"missing_document_ids": missing_ids},
        )
    return filter_preflight_corpus_by_run_source(preflight_corpus, source)


def _load_deal_metadata_for_run(
    request: Request,
    tenant_id: str,
    deal_id: str,
) -> dict[str, Any] | None:
    """Load explicit deal metadata for safe identity-boundary steps."""
    from idis.persistence.repositories.deals import DealsRepository, InMemoryDealsRepository

    db_conn = getattr(request.state, "db_conn", None)
    repo = (
        DealsRepository(db_conn, tenant_id)
        if db_conn is not None
        else InMemoryDealsRepository(tenant_id)
    )
    return repo.get(deal_id)


def _extraction_ready_documents_from_preflight_corpus(
    corpus: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Derive parsed extraction-ready documents from the full preflight corpus."""
    from idis.services.runs.steps import extraction_ready_documents_from_preflight_corpus

    return extraction_ready_documents_from_preflight_corpus(corpus)


def _get_audit_sink(request: Request) -> AuditSink:
    """Get the application audit sink under the strict durability contract.

    Args:
        request: FastAPI request.

    Returns:
        AuditSink instance.
    """
    sink: AuditSink | None = getattr(request.state, "run_audit_sink", None)
    if sink is None:
        sink = getattr(request.app.state, "audit_sink", None)
    return _resolve_run_audit_sink(sink)


def _build_step_responses(steps: list[Any]) -> list[RunStepResponse]:
    """Convert RunStep models to API response format.

    Args:
        steps: List of RunStep instances (already ordered by step_order).

    Returns:
        List of RunStepResponse dicts for JSON serialization.
    """
    from idis.models.run_step import StepStatus

    responses: list[RunStepResponse] = []
    for step in steps:
        error = None
        if step.status in (StepStatus.FAILED, StepStatus.BLOCKED) and step.error_code:
            error = StepErrorResponse(
                code=step.error_code,
                message=SAFE_PUBLIC_STEP_ERROR_MESSAGE,
            )
        responses.append(
            RunStepResponse(
                step_name=step.step_name.value
                if hasattr(step.step_name, "value")
                else step.step_name,
                status=step.status.value if hasattr(step.status, "value") else step.status,
                started_at=step.started_at,
                finished_at=step.finished_at,
                summary=_safe_public_run_summary_dict(step.result_summary),
                error=error,
                retry_count=step.retry_count,
            )
        )
    return responses


def _build_run_ref_step_responses(steps: list[Any]) -> list[RunRefStepResponse]:
    """Convert RunStep models to the start-run response without summaries."""
    responses: list[RunRefStepResponse] = []
    for step in steps:
        error = None
        if step.status in ("FAILED", "BLOCKED") and step.error_code:
            error = StepErrorResponse(
                code=step.error_code,
                message=SAFE_PUBLIC_STEP_ERROR_MESSAGE,
            )
        responses.append(
            RunRefStepResponse(
                step_name=step.step_name.value
                if hasattr(step.step_name, "value")
                else step.step_name,
                status=step.status.value if hasattr(step.status, "value") else step.status,
                started_at=step.started_at,
                finished_at=step.finished_at,
                error=error,
                retry_count=step.retry_count,
            )
        )
    return responses


def _derive_block_reason(steps: list[Any]) -> str | None:
    """Derive a stable run-level block reason from the durable step ledger."""
    from idis.models.run_step import StepStatus

    for step in steps:
        if step.status in (StepStatus.FAILED, StepStatus.BLOCKED) and step.error_code:
            return str(step.error_code)
    return None


def _run_source_from_storage(source: object) -> RunSource | None:
    """Return only the public persisted run-source contract."""
    if source is None:
        return None
    if isinstance(source, RunSource):
        return source
    if isinstance(source, dict):
        public_source = {
            "type": source.get("type"),
            "document_ids": source.get("document_ids"),
        }
        try:
            return RunSource.model_validate(public_source)
        except ValidationError:
            return None
    return None


def _emit_run_completed_audit(
    request: Request,
    run_id: str,
    tenant_id: str,
    status: str,
) -> None:
    """Emit deal.run.completed audit event after pipeline finishes.

    Args:
        request: FastAPI request for audit sink access.
        run_id: Pipeline run UUID.
        tenant_id: Tenant UUID.
        status: Final run status.
    """
    audit_sink = _get_audit_sink(request)
    tenant_ctx = request.state.tenant_context
    deal_id = str(request.path_params["deal_id"])
    event = build_canonical_audit_event(
        tenant_id=tenant_id,
        event_type="deal.run.completed",
        severity="LOW",
        summary="API-inline run execution completed",
        actor_id=tenant_ctx.actor_id,
        actor_type=tenant_ctx.actor_type,
        actor_roles=tenant_ctx.roles,
        request_id=request.state.request_id,
        method=request.method,
        path=request.url.path,
        status_code=202,
        resource_type="deal",
        resource_id=deal_id,
        deal_id=deal_id,
        safe={"run_id": run_id, "status": status, "execution_origin": "API_INLINE"},
    )
    audit_sink.emit(event)


def clear_runs_store() -> None:
    """Clear the in-memory runs store. For testing only."""
    from idis.persistence.repositories.runs import clear_in_memory_runs_store

    clear_in_memory_runs_store()
