"""Slice 61 Neo4j graph projection/retrieval visibility tests."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from idis.api.routes.runs import _run_full_graph_evidence
from idis.audit.sink import InMemoryAuditSink
from idis.deliverables.generator import DeliverablesGenerator
from idis.deliverables.product_bundle import ProductBundleExporter
from idis.models.run_step import StepName, StepStatus
from idis.persistence.graph_consistency import ProjectionResult, ProjectionStatus
from idis.persistence.neo4j_driver import (
    Neo4jHealthCheck,
    Neo4jHealthStatus,
    check_neo4j_health,
)
from idis.persistence.repositories.run_steps import (
    InMemoryRunStepsRepository,
    clear_run_steps_store,
)
from idis.services.graph.retrieval import GraphRetrievalService
from idis.services.runs.orchestrator import RunContext, RunOrchestrator, RunStepBlockedError
from idis.services.runs.strict_full_live import build_strict_full_live_readiness_report
from idis.storage.filesystem_store import FilesystemObjectStore
from tests.test_deliverables_generator import (
    _TIMESTAMP,
    _make_bundle,
    _make_context,
    _make_scorecard,
)
from tests.test_slice59_product_export_bundle import RecordingDeliverablesRepository

TENANT_ID = "11111111-1111-1111-1111-111111111111"
RUN_ID = "22222222-2222-2222-2222-222222222222"
DEAL_ID = "33333333-3333-3333-3333-333333333333"


class FakeNeo4jDriver:
    """Small fake driver for health checks."""

    def __init__(self, *, fail: bool = False) -> None:
        self.closed = False
        self._fail = fail

    def verify_connectivity(self) -> None:
        if self._fail:
            raise RuntimeError(
                "cannot reach neo4j+s://secret-host.databases.neo4j.io as private_user"
            )

    def close(self) -> None:
        self.closed = True


class FakeGraphRepository:
    """GraphRepository-shaped fake that records retrieval calls."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, str]]] = []

    def get_deal_claims_with_grades(self, *, tenant_id: str, deal_id: str) -> list[dict[str, Any]]:
        self.calls.append(("deal_claims_with_grades", {"tenant_id": tenant_id, "deal_id": deal_id}))
        return [
            {
                "claim.claim_id": "claim-001",
                "claim.claim_text": "PRIVATE raw revenue text must not leak",
                "source_docs": ["C:\\Projects\\IDIS\\secret.pdf"],
            }
        ]

    def get_entity_cooccurrence(self, *, tenant_id: str, deal_id: str) -> list[dict[str, Any]]:
        self.calls.append(("entity_cooccurrence", {"tenant_id": tenant_id, "deal_id": deal_id}))
        return [{"entity.name": "neo4j+s://secret-host.databases.neo4j.io"}]

    def get_claim_sanad_chain(self, *, tenant_id: str, claim_id: str) -> list[dict[str, Any]]:
        self.calls.append(("claim_sanad_chain", {"tenant_id": tenant_id, "claim_id": claim_id}))
        return [{"chain_depth": 3, "doc": {"uri": "/Users/private/secret.pdf"}}]

    def get_independence_clusters(self, *, tenant_id: str, claim_id: str) -> list[dict[str, Any]]:
        self.calls.append(("independence_clusters", {"tenant_id": tenant_id, "claim_id": claim_id}))
        return [{"independent_source_count": 2}]

    def get_weakest_link(self, *, tenant_id: str, claim_id: str) -> list[dict[str, Any]]:
        self.calls.append(("weakest_link", {"tenant_id": tenant_id, "claim_id": claim_id}))
        return [{"min_grade": "C", "source_system": "private_user"}]


class FailingProjectionService:
    """Projection service fake that reports a failed projection."""

    def project_deal(self, **kwargs: Any) -> ProjectionResult:
        return ProjectionResult(
            status=ProjectionStatus.FAILED,
            entity_type="deal",
            entity_id=kwargs["deal_id"],
            tenant_id=kwargs["tenant_id"],
            error="Neo4j failed at neo4j+s://secret-host as private_user",
        )

    def project_claim_sanad(self, **kwargs: Any) -> ProjectionResult:
        return ProjectionResult(
            status=ProjectionStatus.SUCCESS,
            entity_type="claim_sanad",
            entity_id=kwargs["claim"]["claim_id"],
            tenant_id=kwargs["tenant_id"],
        )


class FailingRetrievalService:
    """Retrieval service fake that raises a private backend failure."""

    def retrieve_deal_graph_summary(self, **_kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("Neo4j retrieval failed for neo4j+s://secret-host private_user")


class SkippingProjectionService:
    """Projection service fake that reports a configured skip."""

    def project_deal(self, **kwargs: Any) -> ProjectionResult:
        return ProjectionResult(
            status=ProjectionStatus.SKIPPED,
            entity_type="deal",
            entity_id=kwargs["deal_id"],
            tenant_id=kwargs["tenant_id"],
        )

    def project_claim_sanad(self, **kwargs: Any) -> ProjectionResult:
        raise AssertionError("claim projection should not run when deal projection is skipped")


class RaisingProjectionService:
    """Projection service fake that raises private backend details."""

    def project_deal(self, **_kwargs: Any) -> ProjectionResult:
        raise RuntimeError("Neo4j projection failed for neo4j+s://secret-host private_user")


class RecordingProjectionService:
    """Projection service fake that records claim projection inputs."""

    def __init__(self) -> None:
        self.claim_projection_kwargs: list[dict[str, Any]] = []

    def project_deal(self, **kwargs: Any) -> ProjectionResult:
        return ProjectionResult(
            status=ProjectionStatus.SUCCESS,
            entity_type="deal",
            entity_id=kwargs["deal_id"],
            tenant_id=kwargs["tenant_id"],
        )

    def project_claim_sanad(self, **kwargs: Any) -> ProjectionResult:
        self.claim_projection_kwargs.append(kwargs)
        return ProjectionResult(
            status=ProjectionStatus.SUCCESS,
            entity_type="claim_sanad",
            entity_id=kwargs["claim"]["claim_id"],
            tenant_id=kwargs["tenant_id"],
        )


@pytest.fixture(autouse=True)
def _clear_steps() -> None:
    clear_run_steps_store()


def test_neo4j_health_helper_uses_injected_driver_and_redacts_failures() -> None:
    """Health checks are real but unit-testable without opening a network connection."""
    healthy_driver = FakeNeo4jDriver()

    healthy = check_neo4j_health(
        env={
            "NEO4J_URI": "neo4j+s://secret-host.databases.neo4j.io",
            "NEO4J_USERNAME": "private_user",
            "NEO4J_PASSWORD": "private_password",
            "NEO4J_DATABASE": "neo4j",
        },
        driver_factory=lambda *_args, **_kwargs: healthy_driver,
    )

    assert healthy.status == Neo4jHealthStatus.HEALTHY
    assert healthy.config_present is True
    assert healthy.missing_env_vars == []
    assert healthy_driver.closed is True

    failed = check_neo4j_health(
        env={
            "NEO4J_URI": "neo4j+s://secret-host.databases.neo4j.io",
            "NEO4J_USERNAME": "private_user",
            "NEO4J_PASSWORD": "private_password",
        },
        driver_factory=lambda *_args, **_kwargs: FakeNeo4jDriver(fail=True),
    )

    encoded = json.dumps(failed.model_dump(mode="json"), sort_keys=True)
    assert failed.status == Neo4jHealthStatus.FAILED
    assert "secret-host" not in encoded
    assert "private_user" not in encoded
    assert "neo4j+s://" not in encoded


def test_strict_readiness_graph_blocks_until_env_health_wiring_and_product_visibility(
    tmp_path: Path,
) -> None:
    """Graph strict readiness clears only when all Slice61 proof points are present."""
    product_env = {
        "IDIS_DATABASE_URL": "postgresql://configured/db",
        "IDIS_OBJECT_STORE_BACKEND": "filesystem",
        "IDIS_OBJECT_STORE_BASE_DIR": str(tmp_path / "objects"),
    }
    graph_env = {
        **product_env,
        "NEO4J_URI": "neo4j+s://secret-host.databases.neo4j.io",
        "NEO4J_USERNAME": "private_user",
        "NEO4J_PASSWORD": "private_password",
    }

    missing_report = build_strict_full_live_readiness_report(
        env=product_env,
        neo4j_health_checker=lambda _env: Neo4jHealthCheck.missing(
            missing_env_vars=["NEO4J_URI", "NEO4J_USERNAME", "NEO4J_PASSWORD"]
        ),
    )
    missing_inventory = {item.component_name: item for item in missing_report.component_inventory}
    assert missing_inventory["Neo4j graph projection"].full_wired is False
    assert missing_inventory["graph retrieval"].output_visible is False

    failed_report = build_strict_full_live_readiness_report(
        env=graph_env,
        neo4j_health_checker=lambda _env: Neo4jHealthCheck.failed(),
    )
    failed_inventory = {item.component_name: item for item in failed_report.component_inventory}
    assert failed_inventory["Neo4j graph projection"].health_check_status == "configured_failed"
    assert "Neo4j graph projection" in failed_report.blocking_components

    ready_report = build_strict_full_live_readiness_report(
        env=graph_env,
        neo4j_health_checker=lambda _env: Neo4jHealthCheck.healthy(),
    )
    ready_inventory = {item.component_name: item for item in ready_report.component_inventory}
    assert ready_inventory["Neo4j graph projection"].full_wired is True
    assert ready_inventory["Neo4j graph projection"].output_visible is True
    assert ready_inventory["graph retrieval"].full_wired is True
    assert ready_inventory["graph retrieval"].output_visible is True
    assert "graph_evidence_layer" not in {
        component.component_name
        for component in ready_report.components
        if not component.may_proceed
    }


def test_full_run_calls_graph_step_after_calc_and_before_enrichment() -> None:
    """The canonical FULL path invokes graph projection/retrieval after CALC."""
    calls: list[str] = []

    def extract_fn(**_kwargs: Any) -> dict[str, Any]:
        calls.append("extract")
        return {"created_claim_ids": ["claim-001"]}

    def grade_fn(**_kwargs: Any) -> dict[str, Any]:
        calls.append("grade")
        return {"graded_count": 1}

    def calc_fn(**_kwargs: Any) -> dict[str, Any]:
        calls.append("calc")
        return {"calc_ids": ["calc-001"], "reproducibility_hashes": ["a" * 64]}

    def graph_fn(**kwargs: Any) -> dict[str, Any]:
        calls.append("graph")
        assert kwargs["created_claim_ids"] == ["claim-001"]
        assert kwargs["calc_ids"] == ["calc-001"]
        assert kwargs["documents"][0]["document_id"] == "doc-001"
        return {
            "graph_status": "available",
            "graph_projection": {"status": "projected", "projected_claim_count": 1},
            "graph_retrieval": {"status": "retrieved", "retrieval_count": 3},
        }

    def enrichment_fn(**_kwargs: Any) -> dict[str, Any]:
        calls.append("enrichment")
        return {"provider_count": 0, "result_count": 0, "blocked_count": 0, "enrichment_refs": {}}

    ctx = RunContext(
        run_id=RUN_ID,
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        mode="FULL",
        deal_metadata={"tenant_id": TENANT_ID, "company_name": "Acme Corp"},
        documents=[
            {
                "document_id": "doc-001",
                "doc_type": "PDF",
                "document_name": "safe.pdf",
                "spans": [{"span_id": "span-001", "span_type": "PAGE_TEXT"}],
            }
        ],
        extract_fn=extract_fn,
        grade_fn=grade_fn,
        calc_fn=calc_fn,
        graph_fn=graph_fn,
        enrich_fn=enrichment_fn,
        debate_fn=lambda **_kwargs: {"debate_id": RUN_ID},
        analysis_fn=lambda **_kwargs: {
            "_analysis_bundle": _make_bundle(),
            "_analysis_context": _make_context(),
        },
        scoring_fn=lambda **_kwargs: {"_scorecard": _make_scorecard()},
        deliverables_fn=lambda **_kwargs: {"deliverable_count": 0},
    )
    orchestrator = RunOrchestrator(
        audit_sink=InMemoryAuditSink(),
        run_steps_repo=InMemoryRunStepsRepository(TENANT_ID),
    )

    result = orchestrator.execute(ctx)

    assert result.status == "SUCCEEDED"
    assert calls.index("calc") < calls.index("graph") < calls.index("enrichment")
    completed_names = [
        step.step_name for step in result.steps if step.status == StepStatus.COMPLETED
    ]
    assert StepName.GRAPH_EVIDENCE in completed_names


def test_graph_retrieval_service_returns_safe_tenant_scoped_summary() -> None:
    """Retrieval wraps existing GraphRepository methods without exposing raw records."""
    repo = FakeGraphRepository()
    service = GraphRetrievalService(graph_repo=repo)

    summary = service.retrieve_deal_graph_summary(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        claim_ids=["claim-001"],
    )

    assert summary["status"] == "retrieved"
    assert summary["tenant_id"] == TENANT_ID
    assert summary["deal_id"] == DEAL_ID
    assert summary["claim_ids"] == ["claim-001"]
    assert summary["query_summaries"] == [
        {"query": "deal_claims_with_grades", "record_count": 1},
        {"query": "entity_cooccurrence", "record_count": 1},
        {"query": "claim_sanad_chain", "claim_id": "claim-001", "record_count": 1},
        {"query": "independence_clusters", "claim_id": "claim-001", "record_count": 1},
        {"query": "weakest_link", "claim_id": "claim-001", "record_count": 1},
    ]
    encoded = json.dumps(summary, sort_keys=True)
    assert "PRIVATE raw revenue text" not in encoded
    assert "secret-host" not in encoded
    assert "private_user" not in encoded
    assert "C:\\Projects" not in encoded
    assert "/Users/private" not in encoded


def test_strict_graph_projection_failure_blocks_without_leaking_backend_details() -> None:
    """Strict mode fails closed if graph projection fails."""
    with pytest.raises(RunStepBlockedError) as exc_info:
        _run_full_graph_evidence(
            run_id=RUN_ID,
            tenant_id=TENANT_ID,
            deal_id=DEAL_ID,
            documents=[],
            created_claim_ids=["claim-001"],
            calc_ids=[],
            strict_full_live=True,
            neo4j_health_checker=lambda _env: Neo4jHealthCheck.healthy(),
            projection_service=FailingProjectionService(),
            retrieval_service=GraphRetrievalService(graph_repo=FakeGraphRepository()),
        )

    assert exc_info.value.code == "GRAPH_PROJECTION_BLOCKED"
    encoded = json.dumps(exc_info.value.result_summary, sort_keys=True)
    assert "secret-host" not in encoded
    assert "private_user" not in encoded
    assert "neo4j+s://" not in encoded


def test_non_strict_graph_failures_report_blocked_without_fake_success() -> None:
    """Non-strict mode keeps the run honest without claiming graph availability."""
    projection_summary = _run_full_graph_evidence(
        run_id=RUN_ID,
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        documents=[],
        created_claim_ids=["claim-001"],
        calc_ids=[],
        strict_full_live=False,
        neo4j_health_checker=lambda _env: Neo4jHealthCheck.healthy(),
        projection_service=FailingProjectionService(),
        retrieval_service=GraphRetrievalService(graph_repo=FakeGraphRepository()),
    )
    assert projection_summary["graph_status"] == "blocked"
    assert projection_summary["graph_projection"]["status"] == "failed"
    assert projection_summary["graph_retrieval"]["status"] == "not_attempted"

    retrieval_summary = _run_full_graph_evidence(
        run_id=RUN_ID,
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        documents=[],
        created_claim_ids=["claim-001"],
        calc_ids=[],
        strict_full_live=False,
        neo4j_health_checker=lambda _env: Neo4jHealthCheck.healthy(),
        projection_service=RecordingProjectionService(),
        retrieval_service=FailingRetrievalService(),
    )
    assert retrieval_summary["graph_status"] == "blocked"
    assert retrieval_summary["graph_projection"]["status"] in {"skipped", "projected"}
    assert retrieval_summary["graph_retrieval"]["status"] == "failed"

    encoded = json.dumps({"projection": projection_summary, "retrieval": retrieval_summary})
    assert "secret-host" not in encoded
    assert "private_user" not in encoded
    assert "neo4j+s://" not in encoded


def test_skipped_graph_projection_is_not_reported_available() -> None:
    """Skipped projection must remain skipped/non-available in public graph status."""
    summary = _run_full_graph_evidence(
        run_id=RUN_ID,
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        documents=[],
        created_claim_ids=["claim-001"],
        calc_ids=[],
        strict_full_live=False,
        neo4j_health_checker=lambda _env: Neo4jHealthCheck.healthy(),
        projection_service=SkippingProjectionService(),
        retrieval_service=GraphRetrievalService(graph_repo=FakeGraphRepository()),
    )

    assert summary["graph_status"] == "skipped"
    assert summary["graph_projection"]["status"] == "skipped"
    assert summary["graph_retrieval"]["status"] == "not_attempted"

    with pytest.raises(RunStepBlockedError) as exc_info:
        _run_full_graph_evidence(
            run_id=RUN_ID,
            tenant_id=TENANT_ID,
            deal_id=DEAL_ID,
            documents=[],
            created_claim_ids=["claim-001"],
            calc_ids=[],
            strict_full_live=True,
            neo4j_health_checker=lambda _env: Neo4jHealthCheck.healthy(),
            projection_service=SkippingProjectionService(),
            retrieval_service=GraphRetrievalService(graph_repo=FakeGraphRepository()),
        )
    assert exc_info.value.code == "GRAPH_PROJECTION_BLOCKED"


def test_projection_exceptions_are_sanitized_and_non_strict_blocked() -> None:
    """Projection exceptions are normalized like retrieval exceptions."""
    summary = _run_full_graph_evidence(
        run_id=RUN_ID,
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        documents=[],
        created_claim_ids=["claim-001"],
        calc_ids=[],
        strict_full_live=False,
        neo4j_health_checker=lambda _env: Neo4jHealthCheck.healthy(),
        projection_service=RaisingProjectionService(),
        retrieval_service=GraphRetrievalService(graph_repo=FakeGraphRepository()),
    )

    assert summary["graph_status"] == "blocked"
    assert summary["graph_projection"]["status"] == "failed"
    assert summary["graph_retrieval"]["status"] == "not_attempted"
    encoded = json.dumps(summary, sort_keys=True)
    assert "secret-host" not in encoded
    assert "private_user" not in encoded
    assert "neo4j+s://" not in encoded


def test_projection_repository_exceptions_are_sanitized(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Repository failures during projection are normalized before escaping."""

    def raising_graph_claims(**_kwargs: Any) -> list[dict[str, Any]]:
        raise RuntimeError("Neo4j projection repo failed for neo4j+s://secret-host private_user")

    monkeypatch.setattr("idis.api.routes.runs._graph_claims", raising_graph_claims)

    summary = _run_full_graph_evidence(
        run_id=RUN_ID,
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        documents=[],
        created_claim_ids=["claim-001"],
        calc_ids=[],
        strict_full_live=False,
        neo4j_health_checker=lambda _env: Neo4jHealthCheck.healthy(),
        projection_service=RecordingProjectionService(),
        retrieval_service=GraphRetrievalService(graph_repo=FakeGraphRepository()),
    )

    assert summary["graph_status"] == "blocked"
    assert summary["graph_projection"]["status"] == "failed"
    assert summary["graph_retrieval"]["status"] == "not_attempted"
    encoded = json.dumps(summary, sort_keys=True)
    assert "secret-host" not in encoded
    assert "private_user" not in encoded
    assert "neo4j+s://" not in encoded


def test_graph_projection_does_not_invent_calc_or_transmission_edges() -> None:
    """Projection omits CalcSanad/transmission edges unless persisted relationships exist."""
    projection_service = RecordingProjectionService()

    summary = _run_full_graph_evidence(
        run_id=RUN_ID,
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        documents=[],
        created_claim_ids=["claim-001"],
        calc_ids=["calc-001"],
        strict_full_live=False,
        neo4j_health_checker=lambda _env: Neo4jHealthCheck.healthy(),
        projection_service=projection_service,
        retrieval_service=GraphRetrievalService(graph_repo=FakeGraphRepository()),
    )

    assert summary["graph_projection"]["projected_calculation_count"] == 0
    assert projection_service.claim_projection_kwargs
    claim_projection = projection_service.claim_projection_kwargs[0]
    assert claim_projection["calculations"] == []
    assert claim_projection["transmission_nodes"] == []


def test_product_bundle_includes_safe_graph_visibility(tmp_path: Path) -> None:
    """Graph projection/retrieval visibility is exported without private graph details."""
    repository = RecordingDeliverablesRepository()
    object_store = FilesystemObjectStore(base_dir=tmp_path / "objects")
    exporter = ProductBundleExporter(
        deliverables_repo=repository,
        object_store=object_store,
        object_store_backend="filesystem",
    )
    deliverables_bundle = DeliverablesGenerator(audit_sink=InMemoryAuditSink()).generate(
        ctx=_make_context(),
        bundle=_make_bundle(),
        scorecard=_make_scorecard(),
        deal_name="Acme Corp",
        generated_at=_TIMESTAMP,
        deliverable_id_prefix="del-slice61",
    )

    graph_evidence = {
        "graph_status": "available",
        "graph_projection": {
            "status": "projected",
            "projected_document_count": 1,
            "projected_span_count": 1,
            "projected_claim_count": 1,
            "neo4j_uri": "neo4j+s://secret-host.databases.neo4j.io",
            "username": "private_user",
        },
        "graph_retrieval": {
            "status": "retrieved",
            "retrieval_count": 5,
            "query_summaries": [{"query": "deal_claims_with_grades", "record_count": 1}],
            "raw_records": [{"claim_text": "PRIVATE raw revenue text"}],
        },
    }

    exporter.export_bundle(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        bundle=deliverables_bundle,
        analysis_context=_make_context(),
        scorecard=_make_scorecard(),
        export_timestamp=_TIMESTAMP,
        graph_evidence=graph_evidence,
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

    assert evidence_index["graph_evidence"]["status"] == "available"
    assert evidence_index["graph_evidence"]["projection"]["projected_claim_count"] == 1
    assert evidence_index["graph_evidence"]["retrieval"]["retrieval_count"] == 5
    assert run_summary["graph_status"] == "available"
    assert run_summary["graph_projection_status"] == "projected"
    assert run_summary["graph_retrieval_status"] == "retrieved"

    encoded = json.dumps({"evidence_index": evidence_index, "run_summary": run_summary})
    assert "PRIVATE raw revenue text" not in encoded
    assert "secret-host" not in encoded
    assert "private_user" not in encoded
    assert "neo4j+s://" not in encoded
