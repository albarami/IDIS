"""Durable, tenant-scoped methodology import/freeze/list API.

Clients select only a methodology family.  Source locations and bytes are
resolved at the production composition root so private operator paths and
workbook contents can never enter the request or response contract.
"""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from collections.abc import Callable, Mapping
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any, BinaryIO, Literal

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from idis.api.auth import RequireTenantContext
from idis.api.errors import IdisHttpError
from idis.methodology.importers.cdd_json import (
    CDD_SOURCE_ALIAS,
    EXPECTED_CDD_QUESTION_COUNT,
    CDDImporterError,
    import_cdd_template,
)
from idis.methodology.importers.fdd_excel import (
    EXPECTED_ROW_COUNTS,
    HEADER_ROW,
    PRODUCTION_SOURCE_NAME,
    REQUIRED_SHEETS,
    FDDImporterError,
    import_fdd_workbook,
)
from idis.methodology.models import MethodologyRegistry, MethodologyType
from idis.persistence.repositories.methodologies import (
    MethodologiesRepositoryError,
    get_methodologies_repository,
    methodology_source_trace_digest,
)

router = APIRouter(prefix="/v1", tags=["MethodologyVersions"])

_FDD_PATH_ENV = "IDIS_FDD_METHODOLOGY_PATH"
_CDD_TEMPLATE_PATH = (
    Path(__file__).resolve().parents[2] / "methodology" / "templates" / "commercial_dd_v1.json"
)
_SOURCE_CONTRACT_VERSION = "methodology-source-contract-v1"


class MethodologyVersionImportRequest(BaseModel):
    """A server-selected methodology import request (never a client path/upload)."""

    model_config = ConfigDict(extra="forbid")

    methodology_type: MethodologyType


class MethodologyVersionSafeResponse(BaseModel):
    """Operator-safe metadata for a durable methodology version."""

    model_config = ConfigDict(extra="forbid")

    methodology_version_record_id: uuid.UUID
    methodology_version_id: str
    methodology_type: MethodologyType
    version_label: str
    source_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    source_byte_count: Annotated[int, Field(gt=0)]
    source_contract_hash: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    source_trace_digest: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    registry_hash: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    version_hash: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    question_count: Annotated[int, Field(gt=0)]
    source_section_count: Annotated[int, Field(gt=0)]
    header_row: Annotated[int, Field(gt=0)] | None
    status: Literal["IMPORTED", "FROZEN"]
    imported_at: datetime
    frozen_at: datetime | None


def _canonical_hash(payload: object) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _source_contract_hash(methodology_type: MethodologyType) -> str:
    if methodology_type is MethodologyType.COMMERCIAL_DD:
        contract: dict[str, object] = {
            "contract_version": _SOURCE_CONTRACT_VERSION,
            "methodology_type": methodology_type.value,
            "question_count": EXPECTED_CDD_QUESTION_COUNT,
            "source_type": "checked_in_template",
        }
    else:
        contract = {
            "contract_version": _SOURCE_CONTRACT_VERSION,
            "methodology_type": methodology_type.value,
            "header_row": HEADER_ROW,
            "sheet_row_counts": [[sheet, EXPECTED_ROW_COUNTS[sheet]] for sheet in REQUIRED_SHEETS],
            "source_type": "operator_workbook",
        }
    return _canonical_hash(contract)


def _source_trace_digest(registry: MethodologyRegistry) -> str:
    return methodology_source_trace_digest(registry)


def _configuration(request: Request) -> Mapping[str, str]:
    profile = getattr(request.app.state, "strict_runtime_profile", None)
    values = getattr(profile, "values", None)
    if isinstance(values, Mapping):
        return values
    return os.environ


def resolve_server_registry(
    methodology_type: MethodologyType,
    *,
    configuration: Mapping[str, str],
) -> tuple[MethodologyRegistry, int, str, str]:
    """Resolve and import one approved server-side source.

    Returns ``(registry, byte_count, source_type, safe_source_alias)``.  No
    caller-controlled path participates in this boundary.
    """

    importer: Callable[[bytes | Path | str | BinaryIO], MethodologyRegistry]
    if methodology_type is MethodologyType.COMMERCIAL_DD:
        source_path = _CDD_TEMPLATE_PATH
        source_type = "checked_in_template"
        source_alias = CDD_SOURCE_ALIAS
        importer = import_cdd_template
        error_code = "CDD_IMPORT_INVALID"
    else:
        configured_path = configuration.get(_FDD_PATH_ENV, "").strip()
        if not configured_path:
            raise IdisHttpError(
                status_code=409,
                code="FDD_IMPORT_INVALID",
                message="The configured Financial DD methodology is unavailable",
            )
        source_path = Path(configured_path)
        if not source_path.is_absolute():
            raise IdisHttpError(
                status_code=409,
                code="FDD_IMPORT_INVALID",
                message="The configured Financial DD methodology is unavailable",
            )
        source_type = "operator_workbook"
        source_alias = PRODUCTION_SOURCE_NAME
        importer = import_fdd_workbook
        error_code = "FDD_IMPORT_INVALID"

    try:
        before_stat = source_path.stat()
        before_bytes = source_path.read_bytes()
        before_sha256 = hashlib.sha256(before_bytes).hexdigest()
        # Keep the real path-backed importer boundary: both importers perform
        # their own before/after source-hash check.  The composition root adds
        # an outer identity/hash check so its byte-count metadata is bound to
        # the exact artifact the importer accepted.
        registry = importer(source_path)
        after_bytes = source_path.read_bytes()
        after_stat = source_path.stat()
    except (OSError, CDDImporterError, FDDImporterError, ValueError) as exc:
        raise IdisHttpError(
            status_code=409,
            code=error_code,
            message="The configured methodology source failed validation",
        ) from exc

    identity_before = (
        before_stat.st_dev,
        before_stat.st_ino,
        before_stat.st_size,
        before_stat.st_mtime_ns,
    )
    identity_after = (
        after_stat.st_dev,
        after_stat.st_ino,
        after_stat.st_size,
        after_stat.st_mtime_ns,
    )
    after_sha256 = hashlib.sha256(after_bytes).hexdigest()
    if (
        identity_before != identity_after
        or before_sha256 != after_sha256
        or after_sha256 != registry.current_version.source_hash
    ):
        raise IdisHttpError(
            status_code=409,
            code=error_code,
            message="The configured methodology source failed validation",
        )
    return registry, len(after_bytes), source_type, source_alias


def _record_value(record: Any, field: str) -> Any:
    if isinstance(record, Mapping):
        return record[field]
    return getattr(record, field)


def _timestamp(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    raise TypeError("invalid methodology timestamp")


def _safe_response(record: Any) -> MethodologyVersionSafeResponse:
    imported_at = _timestamp(_record_value(record, "imported_at"))
    if imported_at is None:
        raise RuntimeError("METHODOLOGY_COVERAGE_INCOMPLETE")
    return MethodologyVersionSafeResponse(
        methodology_version_record_id=uuid.UUID(
            str(_record_value(record, "methodology_version_record_id"))
        ),
        methodology_version_id=str(_record_value(record, "methodology_version_id")),
        methodology_type=MethodologyType(_record_value(record, "methodology_type")),
        version_label=str(_record_value(record, "version_label")),
        source_sha256=str(_record_value(record, "source_sha256")),
        source_byte_count=int(_record_value(record, "source_byte_count")),
        source_contract_hash=str(_record_value(record, "source_contract_hash")),
        source_trace_digest=str(_record_value(record, "source_trace_digest")),
        registry_hash=str(_record_value(record, "registry_hash")),
        version_hash=str(_record_value(record, "version_hash")),
        question_count=int(_record_value(record, "question_count")),
        source_section_count=int(_record_value(record, "source_section_count")),
        header_row=(
            None
            if _record_value(record, "header_row") is None
            else int(_record_value(record, "header_row"))
        ),
        status=str(_record_value(record, "status")),  # type: ignore[arg-type]
        imported_at=imported_at,
        frozen_at=_timestamp(_record_value(record, "frozen_at")),
    )


def _repository(request: Request, tenant_id: str) -> Any:
    db_conn = getattr(request.state, "db_conn", None)
    try:
        return get_methodologies_repository(db_conn, tenant_id)
    except MethodologiesRepositoryError as exc:
        raise IdisHttpError(
            status_code=503,
            code=exc.code,
            message="Durable methodology storage is unavailable",
        ) from exc


def _find_version(repository: Any, record_id: str) -> Any:
    for record in repository.list_versions():
        if str(_record_value(record, "methodology_version_record_id")) == record_id:
            return record
    raise IdisHttpError(status_code=404, code="NOT_FOUND", message="Methodology version not found")


@router.post(
    "/methodology-versions/import",
    response_model=MethodologyVersionSafeResponse,
    status_code=201,
    operation_id="importMethodologyVersion",
)
def import_methodology_version(
    request_body: MethodologyVersionImportRequest,
    request: Request,
    tenant_ctx: RequireTenantContext,
) -> MethodologyVersionSafeResponse:
    """Import one approved server-resolved methodology source."""

    registry, byte_count, source_type, source_alias = resolve_server_registry(
        request_body.methodology_type,
        configuration=_configuration(request),
    )
    audit_event_id = str(uuid.uuid4())
    repository = _repository(request, tenant_ctx.tenant_id)
    try:
        record = repository.import_registry(
            registry=registry,
            source_type=source_type,
            source_alias=source_alias,
            source_byte_count=byte_count,
            source_contract_hash=_source_contract_hash(request_body.methodology_type),
            source_trace_digest=_source_trace_digest(registry),
            imported_by_actor_id=tenant_ctx.actor_id,
            import_audit_event_id=audit_event_id,
        )
    except MethodologiesRepositoryError as exc:
        raise IdisHttpError(
            status_code=409,
            code=exc.code,
            message="Methodology import was rejected",
        ) from exc
    request.state.audit_resource_id = str(_record_value(record, "methodology_version_record_id"))
    request.state.audit_event_id = audit_event_id
    return _safe_response(record)


@router.post(
    "/methodology-versions/{methodology_version_record_id}/freeze",
    response_model=MethodologyVersionSafeResponse,
    operation_id="freezeMethodologyVersion",
)
def freeze_methodology_version(
    methodology_version_record_id: uuid.UUID,
    request: Request,
    tenant_ctx: RequireTenantContext,
) -> MethodologyVersionSafeResponse:
    """Re-read/reimport the bound source, then immutably freeze the version."""

    repository = _repository(request, tenant_ctx.tenant_id)
    record_id = str(methodology_version_record_id)
    existing = _find_version(repository, record_id)
    methodology_type = MethodologyType(_record_value(existing, "methodology_type"))
    registry, _byte_count, _source_type, _source_alias = resolve_server_registry(
        methodology_type,
        configuration=_configuration(request),
    )
    audit_event_id = str(uuid.uuid4())
    try:
        frozen = repository.freeze_version(
            methodology_version_record_id=record_id,
            expected_source_sha256=registry.current_version.source_hash,
            expected_registry_hash=registry.registry_hash,
            expected_source_trace_digest=_source_trace_digest(registry),
            expected_registry=registry,
            frozen_by_actor_id=tenant_ctx.actor_id,
            freeze_audit_event_id=audit_event_id,
        )
    except MethodologiesRepositoryError as exc:
        raise IdisHttpError(
            status_code=409,
            code=exc.code,
            message="Methodology freeze was rejected",
        ) from exc
    request.state.audit_resource_id = record_id
    request.state.audit_event_id = audit_event_id
    return _safe_response(frozen)


@router.get(
    "/methodology-versions",
    response_model=list[MethodologyVersionSafeResponse],
    operation_id="listMethodologyVersions",
)
def list_methodology_versions(
    request: Request,
    tenant_ctx: RequireTenantContext,
    methodology_type: Annotated[MethodologyType | None, Query()] = None,
) -> list[MethodologyVersionSafeResponse]:
    """List only safe metadata for this tenant's durable versions."""

    repository = _repository(request, tenant_ctx.tenant_id)
    try:
        records = repository.list_versions(methodology_type=methodology_type)
    except MethodologiesRepositoryError as exc:
        raise IdisHttpError(
            status_code=503,
            code=exc.code,
            message="Durable methodology storage is unavailable",
        ) from exc
    return [_safe_response(record) for record in records]


__all__ = [
    "MethodologyVersionImportRequest",
    "MethodologyVersionSafeResponse",
    "resolve_server_registry",
    "router",
]
