#!/usr/bin/env python3
"""Start IDIS API server wired for Gate 3 evaluation.

Mirrors the E2E test wiring from tests/test_ingestion_api_e2e.py:
- FilesystemObjectStore backed by a shared directory
- ComplianceEnforcedStore with BYOKPolicyRegistry
- IngestionService injected into create_app()
- BYOK key configured for the harness tenant

The shared store directory is read from the first CLI argument or
the GATE3_SHARED_STORE_DIR environment variable.

Usage:
    python scripts/gates/start_gate3_server.py <shared_store_dir> [--port PORT]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "src"))

GATE3_TENANT_ID = "00000000-0000-0000-0000-000000000001"
GATE3_ACTOR_ID = "00000000-0000-0000-0001-000000000001"
GATE3_API_KEY = "gate3-harness-key"
GATE3_BYOK_ALIAS = "gate3-key-alias"
GATE3_SERVICE_REGION = "me-south-1"
GATE3_DATA_REGION = "me-south-1"
DEFAULT_PORT = 8777


def _configure_environment() -> None:
    """Set environment variables required by the IDIS API server."""
    api_keys_config = {
        GATE3_API_KEY: {
            "tenant_id": GATE3_TENANT_ID,
            "actor_id": GATE3_ACTOR_ID,
            "name": "Gate3 Harness",
            "timezone": "UTC",
            "data_region": GATE3_DATA_REGION,
            "roles": ["ANALYST", "ADMIN"],
        },
    }
    os.environ["IDIS_API_KEYS_JSON"] = json.dumps(api_keys_config)
    os.environ["IDIS_SERVICE_REGION"] = GATE3_SERVICE_REGION


def build_gate3_app(shared_store_dir: Path) -> object:
    """Build a FastAPI app wired for Gate 3 evaluation.

    Mirrors tests/test_ingestion_api_e2e.py::_wired_app_context exactly:
    1. FilesystemObjectStore(base_dir=shared_store_dir)
    2. ComplianceEnforcedStore wrapping inner store with BYOKPolicyRegistry
    3. IngestionService(compliant_store=compliant_store)
    4. configure_key() for the harness tenant
    5. create_app(ingestion_service=ingestion_service, audit_sink=audit_sink)

    Args:
        shared_store_dir: Absolute path to the shared filesystem store.

    Returns:
        Configured FastAPI application.
    """
    from idis.api.auth import TenantContext
    from idis.api.main import create_app
    from idis.api.routes.deals import clear_deals_store
    from idis.api.routes.documents import clear_document_store
    from idis.audit.sink import InMemoryAuditSink
    from idis.compliance.byok import BYOKPolicyRegistry, configure_key
    from idis.idempotency.store import SqliteIdempotencyStore
    from idis.services.ingestion import IngestionService
    from idis.storage.compliant_store import ComplianceEnforcedStore
    from idis.storage.filesystem_store import FilesystemObjectStore

    clear_deals_store()
    clear_document_store()

    inner_store = FilesystemObjectStore(base_dir=shared_store_dir)
    byok_registry = BYOKPolicyRegistry()
    compliant_store = ComplianceEnforcedStore(
        inner_store=inner_store,
        byok_registry=byok_registry,
    )

    audit_sink = InMemoryAuditSink()
    ingestion_service = IngestionService(
        compliant_store=compliant_store,
        audit_sink=audit_sink,
    )

    tenant_ctx = TenantContext(
        tenant_id=GATE3_TENANT_ID,
        actor_id=GATE3_ACTOR_ID,
        name="Gate3 Harness",
        timezone="UTC",
        data_region=GATE3_DATA_REGION,
    )
    configure_key(tenant_ctx, GATE3_BYOK_ALIAS, audit_sink, registry=byok_registry)

    idem_store = SqliteIdempotencyStore(in_memory=True)
    app = create_app(
        audit_sink=audit_sink,
        idempotency_store=idem_store,
        ingestion_service=ingestion_service,
        service_region=GATE3_SERVICE_REGION,
    )

    return app


def main() -> int:
    """Parse args and start the Gate 3 server."""
    parser = argparse.ArgumentParser(description="Start IDIS API for Gate 3 evaluation")
    parser.add_argument(
        "shared_store_dir",
        type=str,
        help="Absolute path to the shared filesystem store directory",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=DEFAULT_PORT,
        help=f"Port to listen on (default: {DEFAULT_PORT})",
    )
    parser.add_argument(
        "--host",
        type=str,
        default="127.0.0.1",
        help="Host to bind to (default: 127.0.0.1)",
    )

    args = parser.parse_args()
    shared_dir = Path(args.shared_store_dir).resolve()

    if not shared_dir.exists():
        shared_dir.mkdir(parents=True, exist_ok=True)

    _configure_environment()
    app = build_gate3_app(shared_dir)

    import uvicorn

    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    return 0


if __name__ == "__main__":
    sys.exit(main())
