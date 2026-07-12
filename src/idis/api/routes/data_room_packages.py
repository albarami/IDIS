"""Deal-scoped data-room package routes (Slice77).

Public API to create a durable data-room package from a deal's existing documents,
list a deal's packages, and read one package with its safe file ledger. All routes
are deal-scoped (RBAC + ABAC resolve the deal from the path). Responses are
whitelist-only safe refs/records — no storage keys, raw paths, filenames, or content.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, ValidationError

from idis.api.auth import RequireTenantContext
from idis.api.errors import IdisHttpError
from idis.api.routes.runs import _gather_preflight_corpus
from idis.persistence.repositories.data_room_packages import get_data_room_packages_repository
from idis.persistence.repositories.runs import get_runs_repository
from idis.services.data_room.package_service import (
    DataRoomPackageError,
    build_data_room_package_summary,
    create_data_room_package,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1", tags=["DataRoomPackages"])


class CreateDataRoomPackageRequest(BaseModel):
    """Request body for creating a data-room package (document ids only)."""

    model_config = ConfigDict(extra="forbid")

    document_ids: list[str]


def _validate_create_body(body: Any) -> CreateDataRoomPackageRequest:
    if body is None or not isinstance(body, dict):
        raise IdisHttpError(
            status_code=400, code="INVALID_REQUEST", message="Request body is required"
        )
    try:
        return CreateDataRoomPackageRequest.model_validate(body)
    except ValidationError as exc:
        raise IdisHttpError(
            status_code=400,
            code="INVALID_REQUEST",
            message="Invalid data-room package request body",
        ) from exc


@router.post("/deals/{deal_id}/data-room-packages", status_code=201)
async def create_data_room_package_route(
    deal_id: str,
    request: Request,
    tenant_ctx: RequireTenantContext,
) -> dict[str, Any]:
    """Create a data-room package from existing deal documents."""
    try:
        body = await request.json()
    except Exception:
        body = None
    request_body = _validate_create_body(body)

    db_conn = getattr(request.state, "db_conn", None)
    runs_repo = get_runs_repository(db_conn, tenant_ctx.tenant_id)
    if not runs_repo.deal_exists(deal_id):
        raise IdisHttpError(status_code=404, code="NOT_FOUND", message="Deal not found")

    documents = _gather_preflight_corpus(request, tenant_ctx.tenant_id, deal_id)
    repo = get_data_room_packages_repository(db_conn, tenant_ctx.tenant_id)
    try:
        summary = create_data_room_package(
            repo=repo,
            tenant_id=tenant_ctx.tenant_id,
            deal_id=deal_id,
            created_by_actor_id=tenant_ctx.actor_id,
            created_by_actor_type=tenant_ctx.actor_type,
            document_ids=request_body.document_ids,
            documents=documents,
        )
    except DataRoomPackageError as exc:
        raise IdisHttpError(status_code=400, code=exc.reason_code, message=str(exc)) from exc

    request.state.audit_resource_id = summary["package_id"]

    from idis.services.webhooks.lifecycle import (
        DATA_ROOM_PACKAGE_CREATED,
        notify_webhook_lifecycle,
    )

    notify_webhook_lifecycle(
        tenant_id=tenant_ctx.tenant_id,
        event_type=DATA_ROOM_PACKAGE_CREATED,
        resource_type="data_room_package",
        resource_id=summary["package_id"],
        conn=db_conn,
    )
    return summary


@router.get("/deals/{deal_id}/data-room-packages")
def list_data_room_packages_route(
    deal_id: str,
    request: Request,
    tenant_ctx: RequireTenantContext,
) -> dict[str, Any]:
    """List the data-room packages for a deal (safe refs only)."""
    db_conn = getattr(request.state, "db_conn", None)
    runs_repo = get_runs_repository(db_conn, tenant_ctx.tenant_id)
    if not runs_repo.deal_exists(deal_id):
        raise IdisHttpError(status_code=404, code="NOT_FOUND", message="Deal not found")

    repo = get_data_room_packages_repository(db_conn, tenant_ctx.tenant_id)
    items = [
        build_data_room_package_summary(
            package, repo.list_files_by_package(str(package.package_id), deal_id)
        )
        for package in repo.list_packages_by_deal(deal_id)
    ]
    return {"items": items}


@router.get("/deals/{deal_id}/data-room-packages/{package_id}")
def get_data_room_package_route(
    deal_id: str,
    package_id: str,
    request: Request,
    tenant_ctx: RequireTenantContext,
) -> dict[str, Any]:
    """Return one data-room package with its safe file ledger (masked 404 on miss)."""
    db_conn = getattr(request.state, "db_conn", None)
    repo = get_data_room_packages_repository(db_conn, tenant_ctx.tenant_id)
    package = repo.get_package(package_id, deal_id)
    if package is None:
        raise IdisHttpError(
            status_code=404,
            code="DATA_ROOM_PACKAGE_NOT_FOUND",
            message="Data-room package not found",
        )
    files = repo.list_files_by_package(package_id, deal_id)
    record = build_data_room_package_summary(package, files)
    record["files"] = [file.safe_dict() for file in files]
    return record
