"""RED-first tests for citations-only Perplexity evidence materialization."""

from __future__ import annotations

import hashlib
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime

import pytest

from idis.models.sanad import SanadGrade
from idis.services.enrichment.task9.perplexity_evidence import (
    PerplexityCitationEvidence,
    PerplexityEvidenceBundle,
    PerplexityEvidenceError,
    PerplexityRetrievalProvenance,
    SupportedSynthesisUnit,
    SynthesisUnitInput,
    build_perplexity_evidence,
    canonicalize_citation_url,
)
from idis.services.sanad.source_tiers import SourceTier, TierUsage

_RETRIEVAL = PerplexityRetrievalProvenance(
    provider_request_id="request-public-001",
    retrieved_at=datetime(2026, 7, 21, 12, 0, tzinfo=UTC),
    response_sha256="a" * 64,
)


def test_https_url_is_canonicalized_with_components_normalized() -> None:
    assert (
        canonicalize_citation_url("HTTPS://Example.COM:443/a/../report/%7eitem?b=2&a=hello+world")
        == "https://example.com/report/~item?a=hello%20world&b=2"
    )
    assert canonicalize_citation_url("https://EXAMPLE.com") == "https://example.com/"
    assert (
        canonicalize_citation_url("https://example.com:8443/report?z=&a=2&a=1")
        == "https://example.com:8443/report?a=1&a=2&z="
    )


@pytest.mark.parametrize(
    "url",
    [
        "",
        "http://example.com/report",
        "ftp://example.com/report",
        "https:///missing-host",
        "https://user@example.com/report",
        "https://user:password@example.com/report",
        "https://bad_host.example/report",
        "https://example.com:invalid/report",
        "https://example..com/report",
        "https://localhost/report",
        "https://service.local/report",
        "https://127.0.0.1/report",
        "https://10.0.0.1/report",
        "https://169.254.169.254/latest/meta-data",
        "https://[::1]/report",
        "https://example.com/report?access_token=must-not-persist",
        "https://example.com/report?X-Amz-Credential=masked",
        "https://example.com/report?X-Amz-Signature=masked",
        "https://example.com/report?X-Amz-Security-Token=masked",
        "https://example.com/report#fragment",
    ],
)
def test_unsafe_or_invalid_citation_url_is_rejected(url: str) -> None:
    with pytest.raises(PerplexityEvidenceError) as exc_info:
        canonicalize_citation_url(url)
    assert exc_info.value.code == "ENRICHMENT_PROVENANCE_MISSING"
    if url:  # The empty string is a substring of every safe error message.
        assert url not in str(exc_info.value)


def test_citation_evidence_is_hash_deduplicated_sorted_and_support_only() -> None:
    first = build_perplexity_evidence(
        citation_urls=(
            "https://second.example/report?b=2&a=1",
            "https://FIRST.example:443/source",
            "https://second.example/report?a=1&b=2",
        ),
        provenance=_RETRIEVAL,
    )
    reordered = build_perplexity_evidence(
        citation_urls=(
            "https://second.example/report?a=1&b=2",
            "https://first.example/source",
        ),
        provenance=_RETRIEVAL,
    )

    assert first == reordered
    assert tuple(item.canonical_url for item in first.citations) == (
        "https://first.example/source",
        "https://second.example/report?a=1&b=2",
    )
    assert len({item.citation_id for item in first.citations}) == 2
    for item in first.citations:
        assert item.citation_id == f"perplexity-citation-{item.canonical_url_sha256}"
        assert item.source_tier is SourceTier.MAQBUL
        assert item.sanad_grade is SanadGrade.C
        assert item.tier_usage is TierUsage.SUPPORT_ONLY
        assert item.primary_eligible is False
        assert item.provenance == _RETRIEVAL


@pytest.mark.parametrize(
    "citation_urls",
    [
        ("javascript:alert(1)", "https://valid.example/public-source"),
        ("https://valid.example/public-source", "https://user@invalid.example/source"),
        ("https://valid.example/public-source", "https://valid.example/source#fragment"),
    ],
)
def test_any_invalid_citation_rejects_the_entire_provider_collection(
    citation_urls: tuple[str, ...],
) -> None:
    with pytest.raises(PerplexityEvidenceError) as exc_info:
        build_perplexity_evidence(citation_urls=citation_urls, provenance=_RETRIEVAL)
    assert exc_info.value.reason == "citations_invalid"


@pytest.mark.parametrize(
    "citation_urls",
    [
        (),
        ("http://unsafe.example/source",),
        ("https://user:password@example.com/source", "ftp://example.com/source"),
    ],
)
def test_missing_or_invalid_only_citations_fail_no_free_facts(
    citation_urls: tuple[str, ...],
) -> None:
    with pytest.raises(PerplexityEvidenceError) as exc_info:
        build_perplexity_evidence(citation_urls=citation_urls, provenance=_RETRIEVAL)
    assert exc_info.value.code == "ENRICHMENT_PROVENANCE_MISSING"
    assert exc_info.value.reason in {"citations_missing", "citations_invalid"}
    assert "example.com" not in str(exc_info.value)


def test_fixed_sanad_labels_cannot_be_overridden_by_direct_construction() -> None:
    canonical_url = "https://valid.example/source"
    canonical_hash = hashlib.sha256(canonical_url.encode()).hexdigest()

    with pytest.raises(TypeError):
        PerplexityCitationEvidence(
            citation_id=f"perplexity-citation-{canonical_hash}",
            canonical_url=canonical_url,
            canonical_url_sha256=canonical_hash,
            provenance=_RETRIEVAL,
            primary_eligible=True,  # type: ignore[call-arg]
        )
    with pytest.raises(TypeError):
        SupportedSynthesisUnit(
            text="Supported text.",
            text_sha256=hashlib.sha256(b"Supported text.").hexdigest(),
            citation_ids=(f"perplexity-citation-{canonical_hash}",),
            source_tier=SourceTier.THIQAH,  # type: ignore[call-arg]
        )


def test_direct_evidence_construction_cannot_bypass_hash_or_bundle_invariants() -> None:
    with pytest.raises(PerplexityEvidenceError):
        PerplexityCitationEvidence(
            citation_id=f"perplexity-citation-{'b' * 64}",
            canonical_url="https://valid.example/source",
            canonical_url_sha256="b" * 64,
            provenance=_RETRIEVAL,
        )
    with pytest.raises(PerplexityEvidenceError):
        PerplexityEvidenceBundle(citations=(), synthesis_units=())


@pytest.mark.parametrize(
    "provenance",
    [
        None,
        PerplexityRetrievalProvenance(
            provider_request_id="",
            retrieved_at=datetime(2026, 7, 21, 12, 0, tzinfo=UTC),
            response_sha256="a" * 64,
        ),
        PerplexityRetrievalProvenance(
            provider_request_id="request-public-001",
            retrieved_at=datetime(2026, 7, 21, 12, 0),
            response_sha256="a" * 64,
        ),
        PerplexityRetrievalProvenance(
            provider_request_id="request-public-001",
            retrieved_at=datetime(2026, 7, 21, 12, 0, tzinfo=UTC),
            response_sha256="not-a-hash",
        ),
    ],
)
def test_retrieval_provenance_is_required_and_fail_closed(
    provenance: PerplexityRetrievalProvenance | None,
) -> None:
    with pytest.raises(PerplexityEvidenceError) as exc_info:
        build_perplexity_evidence(
            citation_urls=("https://valid.example/source",),
            provenance=provenance,  # type: ignore[arg-type]
        )
    assert exc_info.value.code == "ENRICHMENT_PROVENANCE_MISSING"
    assert exc_info.value.reason == "retrieval_provenance_invalid"


def test_only_synthesis_units_backed_by_valid_citation_ids_are_retained() -> None:
    citations_only = build_perplexity_evidence(
        citation_urls=("https://valid.example/source",),
        provenance=_RETRIEVAL,
    )
    citation_id = citations_only.citations[0].citation_id

    result = build_perplexity_evidence(
        citation_urls=("https://valid.example/source",),
        provenance=_RETRIEVAL,
        synthesis_units=(
            SynthesisUnitInput(
                text="Public synthesis supported by the cited source.",
                citation_ids=(citation_id, citation_id),
            ),
            SynthesisUnitInput(text="Unsupported answer prose.", citation_ids=()),
            SynthesisUnitInput(text="Unknown citation.", citation_ids=("unknown-id",)),
            SynthesisUnitInput(text="   ", citation_ids=(citation_id,)),
        ),
    )

    assert len(result.synthesis_units) == 1
    retained = result.synthesis_units[0]
    assert retained.text == "Public synthesis supported by the cited source."
    assert retained.citation_ids == (citation_id,)
    assert retained.source_tier is SourceTier.MAQBUL
    assert retained.sanad_grade is SanadGrade.C
    assert retained.tier_usage is TierUsage.SUPPORT_ONLY
    assert retained.primary_eligible is False
    assert "Unsupported answer prose" not in repr(result)
    assert "Unknown citation" not in repr(result)


def test_malformed_synthesis_is_dropped_without_leaking_raw_type_errors() -> None:
    citations_only = build_perplexity_evidence(
        citation_urls=("https://valid.example/source",),
        provenance=_RETRIEVAL,
    )
    citation_id = citations_only.citations[0].citation_id

    result = build_perplexity_evidence(
        citation_urls=("https://valid.example/source",),
        provenance=_RETRIEVAL,
        synthesis_units=(
            SynthesisUnitInput(text=None, citation_ids=(citation_id,)),  # type: ignore[arg-type]
            SynthesisUnitInput(text="bad IDs", citation_ids=([citation_id],)),  # type: ignore[arg-type]
            SynthesisUnitInput(text="good", citation_ids=(citation_id,)),
        ),
    )

    assert tuple(unit.text for unit in result.synthesis_units) == ("good",)


def test_evidence_objects_are_immutable_and_expose_no_primary_fact_surface() -> None:
    result = build_perplexity_evidence(
        citation_urls=("https://valid.example/source",),
        provenance=_RETRIEVAL,
    )
    evidence = result.citations[0]
    assert not hasattr(result, "normalized_facts")
    assert not hasattr(evidence, "answer_text")
    assert not hasattr(evidence, "primary_fact")
    with pytest.raises(FrozenInstanceError):
        evidence.primary_eligible = True  # type: ignore[misc]
