"""Shared product bundle artifact catalog and object-key conventions."""

from __future__ import annotations

from dataclasses import dataclass

JSON_CONTENT_TYPE = "application/json"
PDF_CONTENT_TYPE = "application/pdf"
DOCX_CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

MANIFEST_ARTIFACT_TYPE = "product_bundle_manifest"
MANIFEST_FILENAME = "manifest.json"
PRODUCT_BUNDLE_KEY_PREFIX = "runs/{run_id}/product_bundle/"


@dataclass(frozen=True)
class ArtifactCatalogEntry:
    """One durable product bundle artifact mapping."""

    deliverable_type: str
    format: str
    filename: str
    content_type: str


ARTIFACT_CATALOG: tuple[ArtifactCatalogEntry, ...] = (
    ArtifactCatalogEntry("screening_snapshot", "PDF", "screening_snapshot.pdf", PDF_CONTENT_TYPE),
    ArtifactCatalogEntry(
        "screening_snapshot",
        "DOCX",
        "screening_snapshot.docx",
        DOCX_CONTENT_TYPE,
    ),
    ArtifactCatalogEntry("ic_memo", "PDF", "ic_memo.pdf", PDF_CONTENT_TYPE),
    ArtifactCatalogEntry("ic_memo", "DOCX", "ic_memo.docx", DOCX_CONTENT_TYPE),
    ArtifactCatalogEntry("truth_dashboard", "JSON", "truth_dashboard.json", JSON_CONTENT_TYPE),
    ArtifactCatalogEntry("qa_brief", "JSON", "qa_brief.json", JSON_CONTENT_TYPE),
    ArtifactCatalogEntry("executive_summary", "JSON", "executive_summary.json", JSON_CONTENT_TYPE),
    ArtifactCatalogEntry(
        "commercial_diligence",
        "JSON",
        "commercial_diligence.json",
        JSON_CONTENT_TYPE,
    ),
    ArtifactCatalogEntry(
        "financial_diligence",
        "JSON",
        "financial_diligence.json",
        JSON_CONTENT_TYPE,
    ),
    ArtifactCatalogEntry("risk_register", "JSON", "risk_register.json", JSON_CONTENT_TYPE),
    ArtifactCatalogEntry(
        "layer2_ic_challenge", "JSON", "layer2_ic_challenge.json", JSON_CONTENT_TYPE
    ),
    ArtifactCatalogEntry("evidence_index", "JSON", "evidence_index.json", JSON_CONTENT_TYPE),
    ArtifactCatalogEntry("run_summary", "JSON", "run_summary.json", JSON_CONTENT_TYPE),
    ArtifactCatalogEntry(
        "provenance_appendix", "JSON", "provenance_appendix.json", JSON_CONTENT_TYPE
    ),
    ArtifactCatalogEntry(
        MANIFEST_ARTIFACT_TYPE,
        "JSON",
        MANIFEST_FILENAME,
        JSON_CONTENT_TYPE,
    ),
)

_CATALOG_INDEX: dict[tuple[str, str], ArtifactCatalogEntry] = {
    (entry.deliverable_type, entry.format): entry for entry in ARTIFACT_CATALOG
}


def build_product_bundle_object_key(run_id: str, filename: str) -> str:
    """Return the object-store key for one product bundle artifact."""
    return PRODUCT_BUNDLE_KEY_PREFIX.format(run_id=run_id) + filename


def resolve_artifact_entry(deliverable_type: str, format_: str) -> ArtifactCatalogEntry | None:
    """Return the catalog entry for a deliverable type and format."""
    normalized_format = str(format_).upper()
    return _CATALOG_INDEX.get((deliverable_type, normalized_format))


def resolve_object_key(run_id: str, deliverable_type: str, format_: str) -> str | None:
    """Resolve a tenant-scoped object key from deliverable metadata."""
    entry = resolve_artifact_entry(deliverable_type, format_)
    if entry is None:
        return None
    return build_product_bundle_object_key(run_id, entry.filename)


def resolve_content_type(deliverable_type: str, format_: str) -> str | None:
    """Return the MIME type for a catalog artifact."""
    entry = resolve_artifact_entry(deliverable_type, format_)
    return entry.content_type if entry is not None else None


def resolve_download_filename(deliverable_type: str, format_: str) -> str | None:
    """Return the public download filename for a catalog artifact."""
    entry = resolve_artifact_entry(deliverable_type, format_)
    return entry.filename if entry is not None else None
