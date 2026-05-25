"""Durable product export bundle writer."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any

from idis.analysis.models import AnalysisContext
from idis.analysis.scoring.models import Scorecard
from idis.deliverables.export import DeliverableExporter
from idis.models.deliverables import DeliverablesBundle, ICMemo, ScreeningSnapshot
from idis.persistence.repositories.deliverables import (
    DeliverablesRepository,
    deterministic_deliverable_row_id,
)
from idis.storage.models import StoredObjectMetadata
from idis.storage.object_store import ObjectStore

JSON_CONTENT_TYPE = "application/json"
PDF_CONTENT_TYPE = "application/pdf"
DOCX_CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
SENSITIVE_ARTIFACT_KEY_PARTS = frozenset(
    {"local_path", "raw_text", "text_excerpt", "query_text", "embedding"}
)
SENSITIVE_ARTIFACT_VALUE_PARTS = frozenset(
    {".local_reports", "c:\\projects", "confidential marker", "raw_text"}
)
WINDOWS_PATH_PATTERN = re.compile(r"(?i)(^|[^a-z0-9])[a-z]:[\\/]")
POSIX_LOCAL_PATH_PATTERN = re.compile(r"(?i)(^|[^a-z0-9])/(tmp|var|home|users|private|opt)/")


@dataclass(frozen=True)
class _ArtifactDraft:
    artifact_type: str
    format: str
    filename: str
    content_bytes: bytes
    content_type: str


@dataclass(frozen=True)
class _StoredArtifact:
    artifact_type: str
    format: str
    object_key: str
    uri: str
    sha256: str
    size_bytes: int
    content_type: str
    deliverable_id: str


class ProductBundleExporter:
    """Persist product bundle artifacts to object storage and deliverable rows."""

    def __init__(
        self,
        *,
        deliverables_repo: DeliverablesRepository,
        object_store: ObjectStore,
        object_store_backend: str,
    ) -> None:
        """Initialize the durable product bundle exporter."""
        self._repo = deliverables_repo
        self._object_store = object_store
        self._object_store_backend = object_store_backend
        self._deliverable_exporter = DeliverableExporter(validate_before_export=True)

    def export_bundle(
        self,
        *,
        tenant_id: str,
        deal_id: str,
        run_id: str,
        bundle: DeliverablesBundle,
        analysis_context: AnalysisContext,
        scorecard: Scorecard,
        export_timestamp: str,
        graph_evidence: dict[str, Any] | None = None,
        rag_evidence: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Persist a product bundle and return a safe run-step summary."""
        artifacts: list[_StoredArtifact] = []
        for draft in self._artifact_drafts(
            bundle=bundle,
            analysis_context=analysis_context,
            scorecard=scorecard,
            export_timestamp=export_timestamp,
            graph_evidence=graph_evidence,
            rag_evidence=rag_evidence,
        ):
            artifacts.append(
                self._store_artifact(
                    tenant_id=tenant_id,
                    deal_id=deal_id,
                    run_id=run_id,
                    draft=draft,
                )
            )

        manifest_bytes = self._manifest_bytes(
            tenant_id=tenant_id,
            deal_id=deal_id,
            run_id=run_id,
            generated_at=export_timestamp,
            artifacts=artifacts,
        )
        manifest = self._store_artifact(
            tenant_id=tenant_id,
            deal_id=deal_id,
            run_id=run_id,
            draft=_ArtifactDraft(
                artifact_type="product_bundle_manifest",
                format="JSON",
                filename="manifest.json",
                content_bytes=manifest_bytes,
                content_type=JSON_CONTENT_TYPE,
            ),
        )
        all_artifacts = [*artifacts, manifest]
        return {
            "artifact_count": len(all_artifacts),
            "manifest_uri": manifest.uri,
            "deliverable_ids": sorted(artifact.deliverable_id for artifact in all_artifacts),
            "types": sorted({artifact.artifact_type for artifact in all_artifacts}),
        }

    def _artifact_drafts(
        self,
        *,
        bundle: DeliverablesBundle,
        analysis_context: AnalysisContext,
        scorecard: Scorecard,
        export_timestamp: str,
        graph_evidence: dict[str, Any] | None,
        rag_evidence: dict[str, Any] | None,
    ) -> list[_ArtifactDraft]:
        calc_package = self._calc_package(analysis_context)
        graph_package = _graph_package(graph_evidence)
        rag_package = _rag_package(rag_evidence)
        screening_snapshot = self._safe_text_export_deliverable(bundle.screening_snapshot)
        ic_memo = self._safe_text_export_deliverable(bundle.ic_memo)
        screening_pdf = self._deliverable_exporter.export_to_pdf(
            screening_snapshot,
            export_timestamp=export_timestamp,
        )
        screening_docx = self._deliverable_exporter.export_to_docx(
            screening_snapshot,
            export_timestamp=export_timestamp,
        )
        memo_pdf = self._deliverable_exporter.export_to_pdf(
            ic_memo,
            export_timestamp=export_timestamp,
        )
        memo_docx = self._deliverable_exporter.export_to_docx(
            ic_memo,
            export_timestamp=export_timestamp,
        )
        return [
            _ArtifactDraft(
                artifact_type="screening_snapshot",
                format="PDF",
                filename="screening_snapshot.pdf",
                content_bytes=screening_pdf.content_bytes,
                content_type=PDF_CONTENT_TYPE,
            ),
            _ArtifactDraft(
                artifact_type="screening_snapshot",
                format="DOCX",
                filename="screening_snapshot.docx",
                content_bytes=screening_docx.content_bytes,
                content_type=DOCX_CONTENT_TYPE,
            ),
            _ArtifactDraft(
                artifact_type="ic_memo",
                format="PDF",
                filename="ic_memo.pdf",
                content_bytes=memo_pdf.content_bytes,
                content_type=PDF_CONTENT_TYPE,
            ),
            _ArtifactDraft(
                artifact_type="ic_memo",
                format="DOCX",
                filename="ic_memo.docx",
                content_bytes=memo_docx.content_bytes,
                content_type=DOCX_CONTENT_TYPE,
            ),
            self._json_draft(
                "truth_dashboard",
                "truth_dashboard.json",
                bundle.truth_dashboard.model_dump(mode="json"),
            ),
            self._json_draft(
                "qa_brief",
                "qa_brief.json",
                bundle.qa_brief.model_dump(mode="json"),
            ),
            self._json_draft(
                "executive_summary",
                "executive_summary.json",
                bundle.ic_memo.executive_summary.model_dump(mode="json"),
            ),
            self._json_draft(
                "commercial_diligence",
                "commercial_diligence.json",
                {
                    "company_overview": bundle.ic_memo.company_overview.model_dump(mode="json"),
                    "market_analysis": bundle.ic_memo.market_analysis.model_dump(mode="json"),
                    "team_assessment": bundle.ic_memo.team_assessment.model_dump(mode="json"),
                },
            ),
            self._json_draft(
                "financial_diligence",
                "financial_diligence.json",
                {
                    "financials": bundle.ic_memo.financials.model_dump(mode="json"),
                    "scenario_analysis": bundle.ic_memo.scenario_analysis.model_dump(mode="json")
                    if bundle.ic_memo.scenario_analysis is not None
                    else None,
                    "sanad_grade_distribution": bundle.ic_memo.sanad_grade_distribution,
                    "calculation_package": calc_package,
                },
            ),
            self._json_draft(
                "risk_register",
                "risk_register.json",
                bundle.ic_memo.risks_and_mitigations.model_dump(mode="json"),
            ),
            self._json_draft(
                "evidence_index",
                "evidence_index.json",
                self._evidence_index(
                    bundle,
                    calc_package=calc_package,
                    graph_package=graph_package,
                    rag_package=rag_package,
                ),
            ),
            self._json_draft(
                "run_summary",
                "run_summary.json",
                {
                    "tenant_id": analysis_context.tenant_id,
                    "deal_id": analysis_context.deal_id,
                    "run_id": analysis_context.run_id,
                    "generated_at": bundle.generated_at,
                    "composite_score": scorecard.composite_score,
                    "routing": scorecard.routing.value,
                    "calculation_status": calc_package["status"],
                    "calc_count": calc_package["calc_count"],
                    "calc_ids": [item["calc_id"] for item in calc_package["calculations"]],
                    "calc_sanad_count": calc_package["calc_sanad_count"],
                    "calc_sanad_ids": [
                        item["calc_sanad_id"]
                        for item in calc_package["calculations"]
                        if item.get("calc_sanad_id")
                    ],
                    "reproducibility_hashes": [
                        item["reproducibility_hash"]
                        for item in calc_package["calculations"]
                        if item.get("reproducibility_hash")
                    ],
                    "graph_status": graph_package["status"],
                    "graph_projection_status": graph_package["projection"]["status"],
                    "graph_retrieval_status": graph_package["retrieval"]["status"],
                    "graph_projected_claim_count": graph_package["projection"].get(
                        "projected_claim_count",
                        0,
                    ),
                    "graph_retrieval_count": graph_package["retrieval"].get(
                        "retrieval_count",
                        0,
                    ),
                    "rag_status": rag_package["status"],
                    "rag_indexing_status": rag_package["indexing"]["status"],
                    "rag_retrieval_status": rag_package["retrieval"]["status"],
                    "rag_indexed_span_count": rag_package["indexing"].get("indexed_span_count", 0),
                    "rag_probe_count": rag_package["retrieval"].get("probe_count", 0),
                    "rag_match_count": rag_package["retrieval"].get("match_count", 0),
                },
            ),
        ]

    def _json_draft(
        self,
        artifact_type: str,
        filename: str,
        payload: dict[str, Any],
    ) -> _ArtifactDraft:
        data = json.dumps(
            self._safe_json_artifact_payload(payload),
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return _ArtifactDraft(
            artifact_type=artifact_type,
            format="JSON",
            filename=filename,
            content_bytes=data,
            content_type=JSON_CONTENT_TYPE,
        )

    def _safe_json_artifact_payload(self, value: Any) -> Any:
        if isinstance(value, dict):
            sanitized: dict[str, Any] = {}
            for key, item in value.items():
                key_text = str(key)
                if _is_sensitive_artifact_key(key_text):
                    continue
                sanitized[key_text] = self._safe_json_artifact_payload(item)
            return sanitized
        if isinstance(value, list):
            return [self._safe_json_artifact_payload(item) for item in value]
        if isinstance(value, str):
            return "" if _is_sensitive_artifact_string(value) else value
        return value

    def _safe_text_export_deliverable(
        self,
        deliverable: ScreeningSnapshot | ICMemo,
    ) -> ScreeningSnapshot | ICMemo:
        payload = self._safe_json_artifact_payload(deliverable.model_dump(mode="json"))
        if isinstance(deliverable, ScreeningSnapshot):
            return ScreeningSnapshot.model_validate(payload)
        return ICMemo.model_validate(payload)

    def _store_artifact(
        self,
        *,
        tenant_id: str,
        deal_id: str,
        run_id: str,
        draft: _ArtifactDraft,
    ) -> _StoredArtifact:
        object_key = f"runs/{run_id}/product_bundle/{draft.filename}"
        metadata = self._object_store.put(
            tenant_id=tenant_id,
            key=object_key,
            data=draft.content_bytes,
            content_type=draft.content_type,
        )
        uri = self._safe_object_uri(metadata)
        deliverable_id = deterministic_deliverable_row_id(
            tenant_id=tenant_id,
            run_id=run_id,
            deliverable_type=draft.artifact_type,
            format_=draft.format,
        )
        self._repo.create_completed(
            deliverable_id=deliverable_id,
            tenant_id=tenant_id,
            deal_id=deal_id,
            run_id=run_id,
            deliverable_type=draft.artifact_type,
            format_=draft.format,
            uri=uri,
        )
        return _StoredArtifact(
            artifact_type=draft.artifact_type,
            format=draft.format,
            object_key=object_key,
            uri=uri,
            sha256=metadata.sha256,
            size_bytes=metadata.size_bytes,
            content_type=draft.content_type,
            deliverable_id=deliverable_id,
        )

    def _manifest_bytes(
        self,
        *,
        tenant_id: str,
        deal_id: str,
        run_id: str,
        generated_at: str,
        artifacts: list[_StoredArtifact],
    ) -> bytes:
        payload = {
            "tenant_id": tenant_id,
            "deal_id": deal_id,
            "run_id": run_id,
            "generated_at": generated_at,
            "artifact_count": len(artifacts),
            "artifacts": [
                {
                    "type": artifact.artifact_type,
                    "format": artifact.format,
                    "sha256": artifact.sha256,
                    "size_bytes": artifact.size_bytes,
                    "content_type": artifact.content_type,
                    "object_key": artifact.object_key,
                    "uri": artifact.uri,
                    "deliverable_id": artifact.deliverable_id,
                }
                for artifact in artifacts
            ],
        }
        return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")

    def _safe_object_uri(self, metadata: StoredObjectMetadata) -> str:
        key_hash = hashlib.sha256(metadata.key.encode("utf-8")).hexdigest()[:16]
        return f"object:{self._object_store_backend}:{metadata.sha256[:16]}:{key_hash}"

    def _calc_package(self, analysis_context: AnalysisContext) -> dict[str, Any]:
        calculations: list[dict[str, Any]] = []
        for calc_id, calc in sorted(analysis_context.calc_registry.items()):
            if not calc.calc_sanad_id or not _is_sha256_hex(calc.reproducibility_hash):
                continue
            item = {
                "calc_id": calc_id,
                "calc_sanad_id": calc.calc_sanad_id,
                "calc_type": calc.calc_type,
                "input_claim_ids": sorted(calc.input_claim_ids),
                "assumptions": calc.assumptions,
                "output": calc.output,
                "formula_hash": calc.formula_hash,
                "code_version": calc.code_version,
                "reproducibility_hash": calc.reproducibility_hash,
                "calc_grade": calc.calc_grade,
                "input_min_sanad_grade": calc.input_min_sanad_grade,
            }
            calculations.append(item)

        return {
            "status": "calculations_available" if calculations else "no_eligible_calculations",
            "calc_count": len(calculations),
            "calc_sanad_count": sum(1 for item in calculations if item.get("calc_sanad_id")),
            "calculations": calculations,
        }

    def _evidence_index(
        self,
        bundle: DeliverablesBundle,
        *,
        calc_package: dict[str, Any],
        graph_package: dict[str, Any],
        rag_package: dict[str, Any],
    ) -> dict[str, Any]:
        entries: list[dict[str, Any]] = []
        for deliverable in (
            bundle.screening_snapshot,
            bundle.ic_memo,
            bundle.truth_dashboard,
            bundle.qa_brief,
        ):
            entries.extend(
                entry.model_dump(mode="json") for entry in deliverable.audit_appendix.entries
            )
        if bundle.decline_letter is not None:
            entries.extend(
                entry.model_dump(mode="json")
                for entry in bundle.decline_letter.audit_appendix.entries
            )
        calc_entries = [
            {
                "calc_id": item["calc_id"],
                "calc_sanad_id": item.get("calc_sanad_id"),
                "source_claim_ids": item.get("input_claim_ids", []),
                "reproducibility_hash": item.get("reproducibility_hash"),
            }
            for item in calc_package["calculations"]
        ]
        return {
            "entries": entries,
            "calc_entries": calc_entries,
            "graph_evidence": graph_package,
            "rag_evidence": rag_package,
        }


def _is_sensitive_artifact_key(key: str) -> bool:
    normalized = key.lower()
    return any(part in normalized for part in SENSITIVE_ARTIFACT_KEY_PARTS)


def _is_sensitive_artifact_string(value: str) -> bool:
    normalized = value.lower()
    return (
        WINDOWS_PATH_PATTERN.search(value) is not None
        or POSIX_LOCAL_PATH_PATTERN.search(value) is not None
        or any(part in normalized for part in SENSITIVE_ARTIFACT_VALUE_PARTS)
    )


def _is_sha256_hex(value: object) -> bool:
    if not isinstance(value, str) or len(value) != 64:
        return False
    return all(char in "0123456789abcdefABCDEF" for char in value)


def _graph_package(graph_evidence: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(graph_evidence, dict):
        return _empty_graph_package()
    projection = _safe_graph_projection(graph_evidence.get("graph_projection"))
    retrieval = _safe_graph_retrieval(graph_evidence.get("graph_retrieval"))
    status = str(graph_evidence.get("graph_status") or "")
    if status not in {"available", "blocked", "skipped"}:
        status = "available" if projection["status"] == "projected" else "skipped"
    return {"status": status, "projection": projection, "retrieval": retrieval}


def _empty_graph_package() -> dict[str, Any]:
    return {
        "status": "skipped",
        "projection": _empty_graph_projection(),
        "retrieval": _empty_graph_retrieval(),
    }


def _empty_graph_projection() -> dict[str, Any]:
    return {
        "status": "skipped",
        "projected_document_count": 0,
        "projected_span_count": 0,
        "projected_claim_count": 0,
        "projected_calculation_count": 0,
    }


def _empty_graph_retrieval() -> dict[str, Any]:
    return {
        "status": "skipped",
        "retrieval_count": 0,
        "query_summaries": [],
    }


def _safe_graph_projection(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        return _empty_graph_projection()
    status = str(value.get("status") or "skipped")
    if status not in {"projected", "failed", "skipped", "not_attempted"}:
        status = "skipped"
    return {
        "status": status,
        "projected_document_count": _safe_non_negative_int(value.get("projected_document_count")),
        "projected_span_count": _safe_non_negative_int(value.get("projected_span_count")),
        "projected_claim_count": _safe_non_negative_int(value.get("projected_claim_count")),
        "projected_calculation_count": _safe_non_negative_int(
            value.get("projected_calculation_count")
        ),
    }


def _safe_graph_retrieval(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        return _empty_graph_retrieval()
    status = str(value.get("status") or "skipped")
    if status not in {"retrieved", "failed", "skipped", "not_attempted"}:
        status = "skipped"
    query_summaries = []
    raw_summaries = value.get("query_summaries")
    if isinstance(raw_summaries, list):
        for item in raw_summaries:
            if not isinstance(item, dict):
                continue
            query = str(item.get("query") or "")
            if not query.replace("_", "").isalnum():
                continue
            summary: dict[str, Any] = {
                "query": query,
                "record_count": _safe_non_negative_int(item.get("record_count")),
            }
            claim_id = item.get("claim_id")
            if isinstance(claim_id, str) and claim_id:
                summary["claim_id"] = claim_id
            query_summaries.append(summary)
    return {
        "status": status,
        "retrieval_count": _safe_non_negative_int(value.get("retrieval_count")),
        "query_summaries": query_summaries,
    }


def _rag_package(rag_evidence: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(rag_evidence, dict):
        return _empty_rag_package()
    indexing = _safe_rag_indexing(rag_evidence.get("rag_indexing"))
    retrieval = _safe_rag_retrieval(rag_evidence.get("rag_retrieval"))
    status = str(rag_evidence.get("rag_status") or "")
    if status not in {"available", "blocked", "skipped"}:
        status = "available" if indexing["status"] == "indexed" else "skipped"
    return {"status": status, "indexing": indexing, "retrieval": retrieval}


def _empty_rag_package() -> dict[str, Any]:
    return {
        "status": "skipped",
        "indexing": _empty_rag_indexing(),
        "retrieval": _empty_rag_retrieval(),
    }


def _empty_rag_indexing() -> dict[str, Any]:
    return {
        "status": "skipped",
        "indexed_span_count": 0,
        "skipped_span_count": 0,
    }


def _empty_rag_retrieval() -> dict[str, Any]:
    return {
        "status": "skipped",
        "retrieval_mode": "probe",
        "probe_count": 0,
        "match_count": 0,
        "matches": [],
    }


def _safe_rag_indexing(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        return _empty_rag_indexing()
    status = str(value.get("status") or "skipped")
    if status not in {"indexed", "failed", "skipped", "not_attempted"}:
        status = "skipped"
    return {
        "status": status,
        "indexed_span_count": _safe_non_negative_int(value.get("indexed_span_count")),
        "skipped_span_count": _safe_non_negative_int(value.get("skipped_span_count")),
    }


def _safe_rag_retrieval(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        return _empty_rag_retrieval()
    status = str(value.get("status") or "skipped")
    if status not in {"probed", "failed", "skipped", "not_attempted"}:
        status = "skipped"
    retrieval_mode = str(value.get("retrieval_mode") or "probe")
    if retrieval_mode != "probe":
        retrieval_mode = "probe"
    matches: list[dict[str, Any]] = []
    raw_matches = value.get("matches")
    if isinstance(raw_matches, list):
        for item in raw_matches:
            if not isinstance(item, dict):
                continue
            source_type = str(item.get("source_type") or "")
            source_id = str(item.get("source_id") or "")
            if not source_type or not source_id:
                continue
            matches.append(
                {
                    "source_type": source_type,
                    "source_id": source_id,
                    "score": float(item.get("score") or 0.0),
                }
            )
    return {
        "status": status,
        "retrieval_mode": retrieval_mode,
        "probe_count": _safe_non_negative_int(value.get("probe_count")),
        "match_count": _safe_non_negative_int(value.get("match_count")),
        "matches": matches,
    }


def _safe_non_negative_int(value: object) -> int:
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    return 0
