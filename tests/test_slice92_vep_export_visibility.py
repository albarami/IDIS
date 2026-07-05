"""Slice92 Task 5 — safe VEP export visibility in the product bundle (DEC-E).

The durable Layer-1 VEP output becomes output-visible following the established
package conventions (whitelist sanitizer + empty-shape degrade, like the rag/layer2
packages): ``evidence_index.vep_evidence`` and ``run_summary`` counts carry
IDs/counts/status ONLY — never claim text, transcripts, or raw debate content.

Data source: the deliverables step threads ``accumulated["layer1_persistence"]``
(the VEP step's persisted summary block) as ``vep_evidence`` — no new persistence,
no Layer-2 wiring.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from idis.audit.sink import InMemoryAuditSink
from idis.deliverables.generator import DeliverablesGenerator
from idis.deliverables.product_bundle import ProductBundleExporter
from idis.persistence.repositories.run_steps import InMemoryRunStepsRepository
from idis.services.runs.orchestrator import RunContext, RunOrchestrator
from idis.storage.filesystem_store import FilesystemObjectStore
from tests.test_deliverables_generator import (
    _TIMESTAMP,
    _make_bundle,
    _make_context,
    _make_scorecard,
)
from tests.test_slice59_product_export_bundle import RecordingDeliverablesRepository
from tests.test_slice63_rag_full_wiring import DEAL_ID, RUN_ID, TENANT_ID

VEP_ID_A = "55555555-5555-5555-5555-555555555555"
VEP_ID_B = "99999999-5555-5555-5555-555555555555"

_EMPTY_VEP_PACKAGE = {"status": "skipped", "package_count": 0, "package_ids": []}


# --- Whitelist sanitizer: IDs/counts/status only ---


def test_vep_package_whitelists_ids_counts_status_only() -> None:
    from idis.deliverables.product_bundle import _vep_package

    package = _vep_package(
        {
            "status": "persisted",
            "package_row_count": 7,
            "package_ids": [VEP_ID_B, VEP_ID_A, VEP_ID_A, "", 9],
            "claim_text": "PRIVATE CLAIM TEXT MUST NOT LEAK",
            "transcript": "PRIVATE DEBATE TRANSCRIPT MUST NOT LEAK",
        }
    )
    # Exactly the safe keys; count derived from the sanitized ids, not declared.
    assert package == {
        "status": "persisted",
        "package_count": 2,
        "package_ids": sorted([VEP_ID_A, VEP_ID_B]),
    }
    assert "PRIVATE" not in json.dumps(package)


def test_vep_package_drops_non_uuid_ids_including_free_text() -> None:
    # Production package ids are bare UUID5 (deterministic_validated_evidence_package_id),
    # so anything that does not parse as a UUID is dropped — a free-text "id" is the
    # one channel that could smuggle transcript/claim text through the ids list.
    from idis.deliverables.product_bundle import _vep_package

    package = _vep_package(
        {
            "status": "persisted",
            "package_ids": [
                VEP_ID_A,
                "PRIVATE TRANSCRIPT TEXT SHOULD NOT BE AN ID",
                "not-a-uuid",
                "claim_mth_0123456789abcdef01234567",
                "",
                9,
            ],
        }
    )
    assert package["package_ids"] == [VEP_ID_A]
    assert package["package_count"] == 1
    assert "PRIVATE" not in json.dumps(package)


def test_vep_package_empty_shape_when_absent_or_malformed() -> None:
    from idis.deliverables.product_bundle import _vep_package

    for value in (None, "not-a-dict", 7, {"status": "weird", "package_ids": "nope"}):
        package = _vep_package(value)
        assert package["package_count"] == 0
        assert package["package_ids"] == []
        assert package["status"] == "skipped"


# --- Exporter: evidence_index.vep_evidence + run_summary counts ---


def test_bundle_exports_vep_evidence_in_evidence_index_and_run_summary(
    tmp_path: Path,
) -> None:
    object_store = FilesystemObjectStore(base_dir=tmp_path / "objects")
    exporter = ProductBundleExporter(
        deliverables_repo=RecordingDeliverablesRepository(),
        object_store=object_store,
        object_store_backend="filesystem",
    )
    deliverables_bundle = DeliverablesGenerator(audit_sink=InMemoryAuditSink()).generate(
        ctx=_make_context(),
        bundle=_make_bundle(),
        scorecard=_make_scorecard(),
        deal_name="Acme Corp",
        generated_at=_TIMESTAMP,
        deliverable_id_prefix="del-slice92",
    )

    exporter.export_bundle(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        bundle=deliverables_bundle,
        analysis_context=_make_context(),
        scorecard=_make_scorecard(),
        export_timestamp=_TIMESTAMP,
        vep_evidence={
            "status": "persisted",
            "package_row_count": 1,
            "package_ids": [VEP_ID_A],
            "claim_text": "PRIVATE CLAIM TEXT MUST NOT LEAK",
        },
    )

    evidence_index = json.loads(
        object_store.get(
            tenant_id=TENANT_ID,
            key=f"runs/{RUN_ID}/product_bundle/evidence_index.json",
        ).body.decode("utf-8")
    )
    run_summary = json.loads(
        object_store.get(
            tenant_id=TENANT_ID,
            key=f"runs/{RUN_ID}/product_bundle/run_summary.json",
        ).body.decode("utf-8")
    )

    assert evidence_index["vep_evidence"] == {
        "status": "persisted",
        "package_count": 1,
        "package_ids": [VEP_ID_A],
    }
    assert run_summary["vep_status"] == "persisted"
    assert run_summary["vep_package_count"] == 1
    assert run_summary["vep_package_ids"] == [VEP_ID_A]

    encoded = json.dumps({"evidence_index": evidence_index, "run_summary": run_summary})
    assert "PRIVATE" not in encoded
    assert "transcript" not in encoded


def test_bundle_vep_evidence_empty_shape_when_absent(tmp_path: Path) -> None:
    object_store = FilesystemObjectStore(base_dir=tmp_path / "objects")
    exporter = ProductBundleExporter(
        deliverables_repo=RecordingDeliverablesRepository(),
        object_store=object_store,
        object_store_backend="filesystem",
    )
    deliverables_bundle = DeliverablesGenerator(audit_sink=InMemoryAuditSink()).generate(
        ctx=_make_context(),
        bundle=_make_bundle(),
        scorecard=_make_scorecard(),
        deal_name="Acme Corp",
        generated_at=_TIMESTAMP,
        deliverable_id_prefix="del-slice92",
    )
    exporter.export_bundle(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        bundle=deliverables_bundle,
        analysis_context=_make_context(),
        scorecard=_make_scorecard(),
        export_timestamp=_TIMESTAMP,
    )
    evidence_index = json.loads(
        object_store.get(
            tenant_id=TENANT_ID,
            key=f"runs/{RUN_ID}/product_bundle/evidence_index.json",
        ).body.decode("utf-8")
    )
    assert evidence_index["vep_evidence"] == _EMPTY_VEP_PACKAGE


# --- Deliverables threading: accumulated["layer1_persistence"] -> vep_evidence ---


def test_execute_deliverables_threads_layer1_persistence_as_vep_evidence() -> None:
    captured: list[dict[str, Any]] = []

    def deliverables_fn(**kwargs: Any) -> dict[str, Any]:
        captured.append(kwargs)
        return {"deliverable_count": 1}

    ctx = RunContext(
        run_id=RUN_ID,
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        mode="FULL",
        documents=[],
        extract_fn=lambda **_kwargs: {},
        grade_fn=lambda **_kwargs: {},
        deliverables_fn=deliverables_fn,
    )
    orchestrator = RunOrchestrator(
        audit_sink=InMemoryAuditSink(),
        run_steps_repo=InMemoryRunStepsRepository(TENANT_ID),
    )

    layer1_block = {
        "status": "persisted",
        "package_row_count": 1,
        "package_ids": [VEP_ID_A],
    }
    orchestrator._execute_deliverables(  # noqa: SLF001
        ctx, {"layer1_persistence": layer1_block}
    )
    assert captured[0]["vep_evidence"] == layer1_block

    # Absent or malformed degrades to None (null-safe, Slice91 pattern).
    orchestrator._execute_deliverables(ctx, {})  # noqa: SLF001
    assert captured[1]["vep_evidence"] is None
    orchestrator._execute_deliverables(  # noqa: SLF001
        ctx, {"layer1_persistence": "not-a-dict"}
    )
    assert captured[2]["vep_evidence"] is None
