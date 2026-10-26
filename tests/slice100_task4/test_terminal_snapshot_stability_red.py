"""RED contracts for deterministic, diagnosable Task-4 terminal snapshots.

The PostgreSQL case enters through the production ``create_app`` composition
root and performs three independent authenticated durable collections.  The
pure perturbation case then proves that semantically identical nested
collections must not inherit Python/API iteration order.
"""

from __future__ import annotations

import copy
import hashlib
import json
from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from decimal import Decimal, localcontext
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from idis.api.auth import IDIS_API_KEYS_ENV
from idis.api.main import create_app
from idis.api.routes.deals import clear_deals_store
from idis.api.routes.documents import clear_document_store
from idis.audit.postgres_sink import PostgresAuditSink
from idis.evaluation import real_example_gate as gate
from idis.evaluation.real_example_source_package import (
    CompanySourceContract,
    SourceFileContract,
)
from idis.evaluation.real_example_terminal_reconciliation import (
    collect_durable_terminal_rows,
)
from idis.idempotency.postgres_store import PostgresIdempotencyStore
from idis.models.source_package_manifest import (
    SOURCE_PACKAGE_MANIFEST_SCHEMA_VERSION,
    canonical_source_manifest_sha256,
)
from idis.persistence.db import reset_engines
from idis.persistence.repositories.data_room_packages import clear_data_room_packages_store
from tests.abac_seed import seed_deal_access
from tests.slice100_task2b._support import (
    disposable_postgres,
    grant_application_access,
    migrate,
)
from tests.slice100_task4.test_strict_durable_api_gate import _terminal_row

TENANT_ID = "62000000-0000-4000-8000-000000000001"
ACTOR_ID = "task4-snapshot-stability-operator"
API_KEY = "task4-snapshot-stability-key"
SNAPSHOT_SCHEMA_VERSION = "task4-terminal-snapshot-digests-v1"
COMPONENT_KEYS = frozenset({"documents", "artifacts", "spans", "censuses", "audit_counts"})


def _digest(label: str) -> str:
    return hashlib.sha256(label.encode("utf-8")).hexdigest()


def _auth_registry() -> str:
    return json.dumps(
        {
            API_KEY: {
                "tenant_id": TENANT_ID,
                "actor_id": ACTOR_ID,
                "name": "Fictional Snapshot Stability Operator",
                "timezone": "UTC",
                "data_region": "me-south-1",
                "roles": ["ADMIN", "ANALYST"],
            }
        },
        sort_keys=True,
    )


def _company_contract(
    *, alias: str, company_number: int, source: SourceFileContract
) -> CompanySourceContract:
    suffix = f"{company_number:03d}"
    return CompanySourceContract(
        company_alias=alias,
        deal_alias=f"deal-{suffix}",
        document_set_alias=f"documents-{suffix}",
        run_alias=f"run-{suffix}",
        ledger_alias=f"ledger-{suffix}",
        package_alias=f"package-{suffix}",
        source_company_locator_sha256=_digest(f"company-locator:{alias}"),
        file_count=1,
        total_bytes=source.size_bytes,
        source_contract_sha256=_digest(f"source-contract:{alias}"),
        files=(source,),
    )


def _seed_two_company_collection(
    client: TestClient,
) -> tuple[tuple[CompanySourceContract, ...], dict[str, Any]]:
    companies: list[CompanySourceContract] = []
    states: list[dict[str, Any]] = []
    headers = {"X-IDIS-API-Key": API_KEY}
    for company_number, alias in enumerate(("company-001", "company-002"), start=1):
        created = client.post(
            "/v1/deals",
            headers={**headers, "Content-Type": "application/json"},
            json={
                "name": f"{alias}-snapshot-deal",
                "company_name": f"Fictional Snapshot Company {company_number}",
            },
        )
        assert created.status_code == 201, created.text
        deal_id = str(created.json()["deal_id"])
        seed_deal_access(TENANT_ID, deal_id, ACTOR_ID)

        payload = (
            f"Fictional durable snapshot evidence for {alias}.\n"
            f"Second evidence line for ordering {company_number}."
        ).encode()
        source = SourceFileContract(
            sequence=0,
            generated_upload_name=f"{alias}-document-00001.txt",
            source_locator_sha256=_digest(f"source-locator:{alias}"),
            content_sha256=hashlib.sha256(payload).hexdigest(),
            size_bytes=len(payload),
            extension="txt",
        )
        uploaded = client.post(
            f"/v1/deals/{deal_id}/documents/upload",
            headers={
                **headers,
                "Content-Type": "application/octet-stream",
                "X-IDIS-Source-Tree-Fingerprint": _digest("snapshot-source-tree"),
            },
            params={
                "filename": source.generated_upload_name,
                "doc_type": "DATA_ROOM_FILE",
                "source_system": "slice100-snapshot-stability-red",
                "sha256": source.content_sha256,
            },
            content=payload,
        )
        assert uploaded.status_code == 201, uploaded.text
        company = _company_contract(
            alias=alias,
            company_number=company_number,
            source=source,
        )
        source_manifest: dict[str, Any] = {
            "schema_version": SOURCE_PACKAGE_MANIFEST_SCHEMA_VERSION,
            "company_alias": company.company_alias,
            "deal_alias": company.deal_alias,
            "document_set_alias": company.document_set_alias,
            "run_alias": company.run_alias,
            "ledger_alias": company.ledger_alias,
            "package_alias": company.package_alias,
            "source_tree_fingerprint": _digest("snapshot-source-tree"),
            "file_count": 1,
            "total_bytes": source.size_bytes,
            "files": [
                {
                    **source.safe_dict(),
                    "document_id": str(uploaded.json()["document_id"]),
                    "doc_id": str(uploaded.json()["doc_id"]),
                }
            ],
            "manifest_sha256": "",
        }
        source_manifest["manifest_sha256"] = canonical_source_manifest_sha256(source_manifest)
        package = client.post(
            f"/v1/deals/{deal_id}/data-room-packages",
            headers={**headers, "Content-Type": "application/json"},
            json={
                "document_ids": [str(uploaded.json()["document_id"])],
                "source_manifest": source_manifest,
            },
        )
        assert package.status_code == 201, package.text
        companies.append(company)
        states.append(
            {
                **company.safe_header(),
                "deal_id": deal_id,
                "package_id": str(package.json()["package_id"]),
                "manifest_sha256": source_manifest["manifest_sha256"],
            }
        )
    return tuple(companies), {
        "schema_version": "slice100-source-preparation-summary-v1",
        "total_file_count": 2,
        "companies": states,
    }


def _assert_snapshot_digest_shape(value: object) -> None:
    assert isinstance(value, Mapping)
    assert set(value) == {"schema_version", "terminal_rows_sha256", "component_sha256"}
    assert value["schema_version"] == SNAPSHOT_SCHEMA_VERSION
    assert isinstance(value["terminal_rows_sha256"], str)
    assert len(value["terminal_rows_sha256"]) == 64
    components = value["component_sha256"]
    assert isinstance(components, Mapping)
    assert set(components) == COMPONENT_KEYS
    assert all(
        isinstance(component_digest, str)
        and len(component_digest) == 64
        and component_digest == component_digest.lower()
        and all(character in "0123456789abcdef" for character in component_digest)
        for component_digest in components.values()
    )


def test_three_real_postgres_composition_collections_have_identical_snapshot_digests(
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    """N=3 durable collections must freeze both the top and section hashes."""

    with disposable_postgres() as database:
        migrate(database.admin_engine, "0035")
        grant_application_access(database)
        monkeypatch.delenv("IDIS_STRICT_DOTENV_PATH", raising=False)
        monkeypatch.delenv("IDIS_REQUIRE_FULL_LIVE", raising=False)
        monkeypatch.setenv(
            "IDIS_DATABASE_ADMIN_URL",
            database.admin_engine.url.render_as_string(hide_password=False),
        )
        monkeypatch.setenv(
            "IDIS_DATABASE_URL",
            database.app_engine.url.render_as_string(hide_password=False),
        )
        monkeypatch.setenv(IDIS_API_KEYS_ENV, _auth_registry())
        monkeypatch.setenv("IDIS_OBJECT_STORE_BASE_DIR", str(tmp_path / "durable-objects"))
        monkeypatch.setenv("IDIS_WORKER_LEASE_SECONDS", "60")
        monkeypatch.setenv("IDIS_WORKER_HEARTBEAT_SECONDS", "20")
        reset_engines()
        clear_deals_store()
        clear_document_store()
        clear_data_room_packages_store()

        audit_sink = PostgresAuditSink()
        app = create_app(
            audit_sink=audit_sink,
            postgres_audit_sink=audit_sink,
            postgres_idempotency_store=PostgresIdempotencyStore(),
        )
        try:
            with TestClient(app, raise_server_exceptions=False) as client:
                companies, preparation = _seed_two_company_collection(client)
                collections = [
                    collect_durable_terminal_rows(
                        api_client=client,
                        api_key=API_KEY,
                        source_companies=companies,
                        preparation_summary=preparation,
                        large_file_threshold_bytes=50 * 1024 * 1024,
                    )
                    for _ in range(3)
                ]

            with database.admin_engine.connect() as connection:
                audit_count = int(
                    connection.execute(
                        text(
                            "SELECT count(*) FROM audit_events WHERE tenant_id::text = :tenant_id"
                        ),
                        {"tenant_id": TENANT_ID},
                    ).scalar_one()
                )
            assert audit_count > 0
            assert all(len(rows) == 2 for rows in collections)
            assert (
                len({gate._canonical_reduced_terminal_rows_sha256(rows) for rows in collections})
                == 1
            )

            snapshot_digest = getattr(gate, "_canonical_terminal_snapshot_digests", None)
            assert callable(snapshot_digest), (
                "RED: the gate has no diagnosable task4-terminal-snapshot-digests-v1 canonicalizer"
            )
            snapshots = [snapshot_digest(rows) for rows in collections]
            for snapshot in snapshots:
                _assert_snapshot_digest_shape(snapshot)
            assert snapshots[0] == snapshots[1] == snapshots[2]
        finally:
            reset_engines()
            clear_deals_store()
            clear_document_store()
            clear_data_room_packages_store()


def _reverse_every_collection(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            key: _reverse_every_collection(nested) for key, nested in reversed(tuple(value.items()))
        }
    if isinstance(value, list):
        return [_reverse_every_collection(nested) for nested in reversed(value)]
    return value


def _nested_snapshot_fixture() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for index in range(2):
        row = _terminal_row(private_token=f"private-snapshot-{index}")
        row["company_alias"] = f"company-{index + 1:03d}"
        row["source_sequence"] = index
        row["reason_codes"] = ["pdf_parse_completed", "pdf_ocr_completed"]
        second_span = copy.deepcopy(row["durable_spans"][0])
        second_span["span_id"] = f"50000000-0000-4000-8000-{index + 2:012d}"
        second_span["content_hash"] = _digest(f"second-span:{index}")
        row["durable_spans"].append(second_span)
        rows.append(row)
    return rows


def test_recursive_ordering_perturbation_is_neutralized_in_top_and_component_digests() -> None:
    baseline = _nested_snapshot_fixture()
    perturbed = _reverse_every_collection(copy.deepcopy(baseline))
    assert baseline != perturbed
    assert gate._canonical_reduced_terminal_rows_sha256(
        baseline
    ) == gate._canonical_reduced_terminal_rows_sha256(perturbed), (
        "RED: the legacy top digest still inherits nested collection order instead of "
        "routing through the deterministic snapshot canonicalizer"
    )

    snapshot_digest = getattr(gate, "_canonical_terminal_snapshot_digests", None)
    assert callable(snapshot_digest), (
        "RED: recursive snapshot canonicalization and component digests are not implemented"
    )
    first = snapshot_digest(baseline)
    second = snapshot_digest(perturbed)
    _assert_snapshot_digest_shape(first)
    _assert_snapshot_digest_shape(second)
    assert first == second


def test_semantic_terminal_change_alters_top_and_document_component_only() -> None:
    baseline = _nested_snapshot_fixture()
    changed = copy.deepcopy(baseline)
    changed[0]["terminal_status"] = "PARTIAL"

    snapshot_digest = getattr(gate, "_canonical_terminal_snapshot_digests", None)
    assert callable(snapshot_digest), (
        "RED: section-addressable terminal snapshot digests are not implemented"
    )
    first = snapshot_digest(baseline)
    second = snapshot_digest(changed)
    _assert_snapshot_digest_shape(first)
    _assert_snapshot_digest_shape(second)
    assert first["terminal_rows_sha256"] != second["terminal_rows_sha256"]
    assert first["component_sha256"]["documents"] != second["component_sha256"]["documents"]
    assert {
        key: digest for key, digest in first["component_sha256"].items() if key != "documents"
    } == {key: digest for key, digest in second["component_sha256"].items() if key != "documents"}


def test_snapshot_scalar_canonicalization_has_one_pinned_representation() -> None:
    canonical_uuid = "abcdefab-cdef-4abc-8def-abcdefabcdef"
    first = {
        "uuid": UUID(canonical_uuid),
        "sha256": "A" * 64,
        "timestamp": datetime(2026, 7, 20, 12, 30, tzinfo=timezone(timedelta(hours=3))),
        "number": Decimal("-0.000"),
        "unicode": "e\u0301vidence",
    }
    second = {
        "unicode": "évidence",
        "number": -0.0,
        "timestamp": "2026-07-20T09:30:00Z",
        "sha256": "a" * 64,
        "uuid": canonical_uuid.upper(),
    }

    assert gate._canonical_snapshot_sha256(first) == gate._canonical_snapshot_sha256(second)
    numeric_digests = {
        gate._canonical_snapshot_sha256({"number": value}) for value in (1, 1.0, Decimal("1.000"))
    }
    assert len(numeric_digests) == 1

    precise_value = Decimal("12345678901234567890.12345678901234567890")
    context_digests: set[str] = set()
    for precision in (10, 28, 60):
        with localcontext() as context:
            context.prec = precision
            context_digests.add(gate._canonical_snapshot_sha256({"number": precise_value}))
    assert len(context_digests) == 1

    with pytest.raises(gate.DurableIngestionGateError) as caught:
        gate._canonical_snapshot_sha256({"timestamp": datetime(2026, 7, 20, 9, 30)})
    assert caught.value.code == "DATA_ROOM_RECONCILIATION_INCOMPLETE"


def test_snapshot_mismatch_receipt_persists_only_safe_component_digests(
    tmp_path: Path,
) -> None:
    baseline = _nested_snapshot_fixture()
    changed = copy.deepcopy(baseline)
    private_canary = "fictional-private-snapshot-canary"
    changed[0]["private_path"] = private_canary
    changed[0]["terminal_status"] = "PARTIAL"
    changed[0]["durable_spans"][0]["content_hash"] = _digest("changed-span")
    before = gate._canonical_terminal_snapshot_digests(baseline)
    path = tmp_path / gate.TASK4_TERMINAL_SNAPSHOT_MISMATCH_REPORT_NAME

    with pytest.raises(gate.DurableIngestionGateError) as caught:
        gate._require_terminal_snapshot_stable(
            before=before,
            after_rows=changed,
            mismatch_path=path,
        )
    assert caught.value.code == "DATA_ROOM_TERMINAL_SNAPSHOT_DRIFT"

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert set(payload) == {
        "schema_version",
        "status",
        "reason_code",
        "before",
        "after",
        "differing_components",
    }
    assert payload["differing_components"] == ["documents", "spans"]
    assert private_canary not in path.read_text(encoding="utf-8")
