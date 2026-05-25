"""Deliverables routes for IDIS API.

Provides GET/POST /v1/deals/{dealId}/deliverables per OpenAPI spec.

Supports both Postgres persistence (when configured) and in-memory fallback.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel
from starlette.responses import Response

from idis.api.auth import RequireTenantContext
from idis.api.errors import IdisHttpError
from idis.deliverables.artifact_catalog import (
    MANIFEST_ARTIFACT_TYPE,
    MANIFEST_FILENAME,
    build_product_bundle_object_key,
)
from idis.deliverables.artifact_resolver import (
    resolve_content_type,
    resolve_download_filename,
    resolve_object_key,
)
from idis.deliverables.manifest_review import sanitize_product_bundle_manifest
from idis.persistence.repositories.deliverables import safe_public_deliverable_uri
from idis.storage.errors import ObjectNotFoundError, ObjectStorageError

router = APIRouter(prefix="/v1", tags=["Deliverables"])

_IN_MEMORY_DELIVERABLES: dict[str, dict[str, Any]] = {}


class GenerateDeliverableRequest(BaseModel):
    """Request body for POST /v1/deals/{dealId}/deliverables."""

    deliverable_type: str
    format: str = "PDF"


class RunRef(BaseModel):
    """Run reference returned by generateDeliverable (202)."""

    run_id: str
    status: str


class Deliverable(BaseModel):
    """Deliverable response model per OpenAPI spec."""

    deliverable_id: str
    deal_id: str
    deliverable_type: str
    status: str
    uri: str | None = None
    created_at: str
    run_id: str | None = None
    format: str | None = None


class ProductBundleManifestReview(BaseModel):
    """Safe product bundle manifest review payload."""

    tenant_id: str
    deal_id: str
    run_id: str
    generated_at: str | None = None
    artifact_count: int
    artifacts: list[dict[str, Any]]


class PaginatedDeliverableList(BaseModel):
    """Paginated list of deliverables per OpenAPI spec."""

    items: list[Deliverable]
    next_cursor: str | None = None


def _list_deliverables_from_postgres(
    conn: Any,
    tenant_id: str,
    deal_id: str,
    limit: int,
    cursor: str | None,
) -> tuple[list[dict[str, Any]], str | None]:
    """List deliverables from Postgres with pagination."""
    from idis.persistence.repositories.deliverables import PostgresDeliverablesRepository

    return PostgresDeliverablesRepository(conn, tenant_id).list_by_deal(
        deal_id=deal_id,
        limit=limit,
        cursor=cursor,
    )


def _create_deliverable_in_postgres(
    conn: Any,
    deliverable_id: str,
    tenant_id: str,
    deal_id: str,
    deliverable_type: str,
    format_: str,
    idempotency_key: str | None,
) -> dict[str, Any]:
    """Create deliverable in Postgres."""
    from sqlalchemy import text

    now = datetime.now(UTC)
    conn.execute(
        text(
            """
            INSERT INTO deliverables
                (deliverable_id, tenant_id, deal_id, deliverable_type,
                 format, status, idempotency_key, created_at)
            VALUES
                (:deliverable_id, :tenant_id, :deal_id, :deliverable_type,
                 :format, 'QUEUED', :idempotency_key, :created_at)
            """
        ),
        {
            "deliverable_id": deliverable_id,
            "tenant_id": tenant_id,
            "deal_id": deal_id,
            "deliverable_type": deliverable_type,
            "format": format_,
            "idempotency_key": idempotency_key,
            "created_at": now,
        },
    )
    return {
        "deliverable_id": deliverable_id,
        "tenant_id": tenant_id,
        "deal_id": deal_id,
        "deliverable_type": deliverable_type,
        "format": format_,
        "status": "QUEUED",
        "uri": None,
        "created_at": now.isoformat().replace("+00:00", "Z"),
    }


def _get_deliverable_row(
    *,
    db_conn: Any,
    tenant_id: str,
    deliverable_id: str,
) -> dict[str, Any] | None:
    """Load one deliverable row from Postgres or the in-memory fallback."""
    if db_conn is not None:
        from idis.persistence.repositories.deliverables import PostgresDeliverablesRepository

        return PostgresDeliverablesRepository(db_conn, tenant_id).get_by_id(
            deliverable_id=deliverable_id,
        )
    row = _IN_MEMORY_DELIVERABLES.get(deliverable_id)
    if row is None or row.get("tenant_id") != tenant_id:
        return None
    return {
        **row,
        "uri": safe_public_deliverable_uri(row.get("uri")),
    }


def _get_configured_object_store() -> Any | None:
    from idis.storage.defaults import build_configured_product_export_object_store

    return build_configured_product_export_object_store()


def _downloadable_row(row: dict[str, Any] | None) -> dict[str, Any]:
    """Validate a deliverable row is eligible for safe download."""
    if row is None:
        raise IdisHttpError(status_code=404, code="NOT_FOUND", message="Deliverable not found")
    if row.get("status") != "COMPLETED":
        raise IdisHttpError(status_code=404, code="NOT_FOUND", message="Deliverable not found")
    if not row.get("run_id"):
        raise IdisHttpError(status_code=404, code="NOT_FOUND", message="Deliverable not found")
    if safe_public_deliverable_uri(row.get("uri")) is None:
        raise IdisHttpError(status_code=404, code="NOT_FOUND", message="Deliverable not found")
    return row


def _serialize_deliverable(row: dict[str, Any]) -> Deliverable:
    """Build a public deliverable response model."""
    return Deliverable(
        deliverable_id=row["deliverable_id"],
        deal_id=row["deal_id"],
        deliverable_type=row["deliverable_type"],
        status=row["status"],
        uri=safe_public_deliverable_uri(row.get("uri")),
        created_at=row["created_at"],
        run_id=row.get("run_id"),
        format=row.get("format"),
    )


def _deal_exists_in_postgres(conn: Any, deal_id: str) -> bool:
    """Check if deal exists in Postgres (RLS enforced)."""
    from sqlalchemy import text

    result = conn.execute(
        text("SELECT 1 FROM deals WHERE deal_id = :deal_id"),
        {"deal_id": deal_id},
    )
    return result.fetchone() is not None


def _validate_cursor(cursor: str | None) -> str | None:
    """Validate cursor format. Returns cursor if valid, raises 400 if invalid."""
    if cursor is None:
        return None
    try:
        from datetime import datetime

        datetime.fromisoformat(cursor.replace("Z", "+00:00"))
        return cursor
    except (ValueError, AttributeError):
        raise IdisHttpError(
            status_code=400,
            code="INVALID_CURSOR",
            message="Invalid cursor format",
        ) from None


@router.get("/deals/{deal_id}/deliverables", response_model=PaginatedDeliverableList)
def list_deliverables(
    deal_id: str,
    request: Request,
    tenant_ctx: RequireTenantContext,
    limit: int = Query(default=50),
    cursor: str | None = Query(default=None),
) -> PaginatedDeliverableList:
    """List deliverables for a deal.

    Args:
        deal_id: UUID of the deal.
        request: FastAPI request for DB connection access.
        tenant_ctx: Injected tenant context from auth dependency.
        limit: Maximum number of items to return.
        cursor: Pagination cursor.

    Returns:
        Paginated list of deliverables.
    """
    if limit < 1 or limit > 200:
        raise IdisHttpError(
            status_code=400,
            code="INVALID_LIMIT",
            message="limit must be between 1 and 200",
        )

    validated_cursor = _validate_cursor(cursor)

    db_conn = getattr(request.state, "db_conn", None)

    if db_conn is not None:
        items, next_cursor = _list_deliverables_from_postgres(
            db_conn, tenant_ctx.tenant_id, deal_id, limit, validated_cursor
        )
    else:
        all_items = [
            d
            for d in _IN_MEMORY_DELIVERABLES.values()
            if d.get("deal_id") == deal_id and d.get("tenant_id") == tenant_ctx.tenant_id
        ]
        all_items.sort(key=lambda x: x["created_at"], reverse=True)
        items = all_items[:limit]
        next_cursor = None

    return PaginatedDeliverableList(
        items=[_serialize_deliverable(d) for d in items],
        next_cursor=next_cursor,
    )


@router.get("/deliverables/{deliverable_id}/content")
def download_deliverable_content(
    deliverable_id: str,
    request: Request,
    tenant_ctx: RequireTenantContext,
) -> Response:
    """Download one completed deliverable artifact via configured object storage."""
    db_conn = getattr(request.state, "db_conn", None)
    row = _downloadable_row(
        _get_deliverable_row(
            db_conn=db_conn,
            tenant_id=tenant_ctx.tenant_id,
            deliverable_id=deliverable_id,
        )
    )
    object_key = resolve_object_key(
        str(row["run_id"]),
        str(row["deliverable_type"]),
        str(row["format"]),
    )
    content_type = resolve_content_type(str(row["deliverable_type"]), str(row["format"]))
    filename = resolve_download_filename(str(row["deliverable_type"]), str(row["format"]))
    if object_key is None or content_type is None or filename is None:
        raise IdisHttpError(status_code=404, code="NOT_FOUND", message="Deliverable not found")

    object_store = _get_configured_object_store()
    if object_store is None:
        raise IdisHttpError(
            status_code=503,
            code="STORAGE_UNAVAILABLE",
            message="Object storage is not configured",
        )

    try:
        stored = object_store.get(tenant_id=tenant_ctx.tenant_id, key=object_key)
    except ObjectNotFoundError as exc:
        raise IdisHttpError(
            status_code=404,
            code="NOT_FOUND",
            message="Deliverable not found",
        ) from exc
    except ObjectStorageError as exc:
        raise IdisHttpError(
            status_code=503,
            code="STORAGE_UNAVAILABLE",
            message="Object storage is unavailable",
        ) from exc

    request.state.audit_resource_id = deliverable_id
    return Response(
        content=stored.body,
        media_type=content_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get(
    "/deals/{deal_id}/runs/{run_id}/product-bundle/manifest",
    response_model=ProductBundleManifestReview,
)
def get_product_bundle_manifest(
    deal_id: str,
    run_id: str,
    request: Request,
    tenant_ctx: RequireTenantContext,
) -> ProductBundleManifestReview:
    """Return a sanitized product bundle manifest for review."""
    db_conn = getattr(request.state, "db_conn", None)
    from idis.persistence.repositories.deliverables import deterministic_deliverable_row_id

    manifest_id = deterministic_deliverable_row_id(
        tenant_id=tenant_ctx.tenant_id,
        run_id=run_id,
        deliverable_type=MANIFEST_ARTIFACT_TYPE,
        format_="JSON",
    )
    manifest_row = _downloadable_row(
        _get_deliverable_row(
            db_conn=db_conn,
            tenant_id=tenant_ctx.tenant_id,
            deliverable_id=manifest_id,
        )
    )
    if manifest_row.get("deal_id") != deal_id:
        raise IdisHttpError(status_code=404, code="NOT_FOUND", message="Manifest not found")

    object_store = _get_configured_object_store()
    if object_store is None:
        raise IdisHttpError(
            status_code=503,
            code="STORAGE_UNAVAILABLE",
            message="Object storage is not configured",
        )

    manifest_key = build_product_bundle_object_key(run_id, MANIFEST_FILENAME)
    try:
        stored = object_store.get(tenant_id=tenant_ctx.tenant_id, key=manifest_key)
    except ObjectNotFoundError as exc:
        raise IdisHttpError(
            status_code=404,
            code="NOT_FOUND",
            message="Manifest not found",
        ) from exc
    except ObjectStorageError as exc:
        raise IdisHttpError(
            status_code=503,
            code="STORAGE_UNAVAILABLE",
            message="Object storage is unavailable",
        ) from exc

    try:
        manifest_body = json.loads(stored.body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise IdisHttpError(
            status_code=404,
            code="NOT_FOUND",
            message="Manifest not found",
        ) from exc
    if not isinstance(manifest_body, dict):
        raise IdisHttpError(status_code=404, code="NOT_FOUND", message="Manifest not found")

    sanitized = sanitize_product_bundle_manifest(manifest_body)
    artifacts = sanitized.get("artifacts", [])
    if not isinstance(artifacts, list):
        artifacts = []
    request.state.audit_resource_id = manifest_id
    return ProductBundleManifestReview(
        tenant_id=str(sanitized.get("tenant_id") or tenant_ctx.tenant_id),
        deal_id=str(sanitized.get("deal_id") or deal_id),
        run_id=str(sanitized.get("run_id") or run_id),
        generated_at=str(sanitized["generated_at"]) if sanitized.get("generated_at") else None,
        artifact_count=len(artifacts),
        artifacts=[artifact for artifact in artifacts if isinstance(artifact, dict) and artifact],
    )


def _validate_generate_deliverable_body(body: dict[str, Any] | None) -> GenerateDeliverableRequest:
    """Validate generate deliverable request body, returning 400 for missing required fields."""
    if body is None or not isinstance(body, dict):
        raise IdisHttpError(
            status_code=400,
            code="INVALID_REQUEST",
            message="Request body is required",
        )
    if "deliverable_type" not in body:
        raise IdisHttpError(
            status_code=400,
            code="INVALID_REQUEST",
            message="Missing required field: deliverable_type",
            details={"missing_fields": ["deliverable_type"]},
        )
    format_ = body.get("format", "PDF")
    if format_ not in ("PDF", "DOCX", "JSON"):
        raise IdisHttpError(
            status_code=400,
            code="INVALID_REQUEST",
            message="Invalid format; must be PDF, DOCX, or JSON",
        )
    return GenerateDeliverableRequest(
        deliverable_type=body["deliverable_type"],
        format=format_,
    )


@router.post("/deals/{deal_id}/deliverables", response_model=RunRef, status_code=202)
async def generate_deliverable(
    deal_id: str,
    request: Request,
    tenant_ctx: RequireTenantContext,
) -> RunRef:
    """Generate a deliverable.

    Args:
        deal_id: UUID of the deal.
        request: FastAPI request for DB connection and body access.
        tenant_ctx: Injected tenant context from auth dependency.

    Returns:
        RunRef with deliverable_id (as run_id) and initial status.

    Raises:
        IdisHttpError: 400 if invalid/missing fields, 404 if deal not found.
    """
    try:
        body = await request.json()
    except Exception:
        body = None
    request_body = _validate_generate_deliverable_body(body)

    deliverable_id = str(uuid.uuid4())
    db_conn = getattr(request.state, "db_conn", None)
    idempotency_key = request.headers.get("Idempotency-Key")

    if db_conn is not None:
        if not _deal_exists_in_postgres(db_conn, deal_id):
            raise IdisHttpError(status_code=404, code="NOT_FOUND", message="Deal not found")

        deliverable_data = _create_deliverable_in_postgres(
            conn=db_conn,
            deliverable_id=deliverable_id,
            tenant_id=tenant_ctx.tenant_id,
            deal_id=deal_id,
            deliverable_type=request_body.deliverable_type,
            format_=request_body.format,
            idempotency_key=idempotency_key,
        )
    else:
        now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        deliverable_data = {
            "deliverable_id": deliverable_id,
            "tenant_id": tenant_ctx.tenant_id,
            "deal_id": deal_id,
            "deliverable_type": request_body.deliverable_type,
            "format": request_body.format,
            "status": "QUEUED",
            "uri": None,
            "created_at": now,
        }
        _IN_MEMORY_DELIVERABLES[deliverable_id] = deliverable_data

    request.state.audit_resource_id = deliverable_id

    return RunRef(
        run_id=deliverable_data["deliverable_id"],
        status=deliverable_data["status"],
    )


def clear_deliverables_store() -> None:
    """Clear the in-memory deliverables store. For testing only."""
    _IN_MEMORY_DELIVERABLES.clear()
