"""Citations-only Perplexity evidence materialization.

Perplexity answer prose is never a primary fact. Only canonical cited sources become
evidence, and retained synthesis stays support-only with explicit citation IDs.
"""

from __future__ import annotations

import hashlib
import ipaddress
import posixpath
import re
from dataclasses import dataclass, field
from datetime import datetime
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit

from idis.models.sanad import SanadGrade
from idis.services.sanad.source_tiers import (
    SourceTier,
    TierUsage,
    get_tier_usage,
    is_primary_eligible,
    tier_to_base_grade,
)

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_CITATION_ID_RE = re.compile(r"^perplexity-citation-[0-9a-f]{64}$")
_INVALID_PERCENT_RE = re.compile(r"%(?![0-9A-Fa-f]{2})")
_DNS_LABEL_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
_UNRESERVED = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~")
_SENSITIVE_QUERY_KEYS = frozenset(
    {
        "accesskey",
        "accesskeyid",
        "accesstoken",
        "apikey",
        "auth",
        "authorization",
        "awsaccesskeyid",
        "bearer",
        "clientsecret",
        "consumersecret",
        "credential",
        "jwt",
        "key",
        "password",
        "secret",
        "session",
        "sessionid",
        "sig",
        "signature",
        "token",
    }
)
_SOURCE_TIER = SourceTier.MAQBUL
_SANAD_GRADE = SanadGrade(tier_to_base_grade(_SOURCE_TIER))
_TIER_USAGE = get_tier_usage(_SOURCE_TIER)
_PRIMARY_ELIGIBLE = is_primary_eligible(_SOURCE_TIER)


class PerplexityEvidenceError(ValueError):
    """Stable, content-free failure for unusable citation evidence."""

    code = "ENRICHMENT_PROVENANCE_MISSING"

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(f"{self.code}:{reason}")


@dataclass(frozen=True, slots=True)
class PerplexityRetrievalProvenance:
    """Minimum provider-response lineage required by every citation object."""

    provider_request_id: str
    retrieved_at: datetime
    response_sha256: str
    provider_id: str = field(default="perplexity", init=False)


@dataclass(frozen=True, slots=True)
class PerplexityCitationEvidence:
    """One low-tier cited source; never eligible as a primary fact."""

    citation_id: str
    canonical_url: str
    canonical_url_sha256: str
    provenance: PerplexityRetrievalProvenance
    source_tier: SourceTier = field(default=_SOURCE_TIER, init=False)
    sanad_grade: SanadGrade = field(default=_SANAD_GRADE, init=False)
    tier_usage: TierUsage = field(default=_TIER_USAGE, init=False)
    primary_eligible: bool = field(default=_PRIMARY_ELIGIBLE, init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.canonical_url, str):
            raise PerplexityEvidenceError("citation_record_invalid")
        canonical_url = canonicalize_citation_url(self.canonical_url)
        expected_hash = hashlib.sha256(canonical_url.encode("utf-8")).hexdigest()
        if (
            canonical_url != self.canonical_url
            or self.canonical_url_sha256 != expected_hash
            or self.citation_id != f"perplexity-citation-{expected_hash}"
            or _CITATION_ID_RE.fullmatch(self.citation_id) is None
        ):
            raise PerplexityEvidenceError("citation_record_invalid")
        _validate_provenance(self.provenance)


@dataclass(frozen=True, slots=True)
class SynthesisUnitInput:
    """Provider synthesis proposed for support-only retention."""

    text: str
    citation_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SupportedSynthesisUnit:
    """Citation-bound synthesis kept separate from normalized primary facts."""

    text: str
    text_sha256: str
    citation_ids: tuple[str, ...]
    source_tier: SourceTier = field(default=_SOURCE_TIER, init=False)
    sanad_grade: SanadGrade = field(default=_SANAD_GRADE, init=False)
    tier_usage: TierUsage = field(default=_TIER_USAGE, init=False)
    primary_eligible: bool = field(default=_PRIMARY_ELIGIBLE, init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.text, str) or not self.text or self.text != self.text.strip():
            raise PerplexityEvidenceError("synthesis_record_invalid")
        expected_hash = hashlib.sha256(self.text.encode("utf-8")).hexdigest()
        if self.text_sha256 != expected_hash:
            raise PerplexityEvidenceError("synthesis_record_invalid")
        if (
            not isinstance(self.citation_ids, tuple)
            or not self.citation_ids
            or any(
                not isinstance(citation_id, str) or _CITATION_ID_RE.fullmatch(citation_id) is None
                for citation_id in self.citation_ids
            )
            or self.citation_ids != tuple(sorted(set(self.citation_ids)))
        ):
            raise PerplexityEvidenceError("synthesis_record_invalid")


@dataclass(frozen=True, slots=True)
class PerplexityEvidenceBundle:
    """Deterministic citation evidence and optional supported synthesis."""

    citations: tuple[PerplexityCitationEvidence, ...]
    synthesis_units: tuple[SupportedSynthesisUnit, ...]

    def __post_init__(self) -> None:
        if (
            not isinstance(self.citations, tuple)
            or not self.citations
            or any(
                not isinstance(citation, PerplexityCitationEvidence) for citation in self.citations
            )
        ):
            raise PerplexityEvidenceError("evidence_bundle_invalid")
        expected_order = tuple(
            sorted(self.citations, key=lambda item: (item.canonical_url, item.canonical_url_sha256))
        )
        citation_ids = tuple(citation.citation_id for citation in self.citations)
        if self.citations != expected_order or len(citation_ids) != len(set(citation_ids)):
            raise PerplexityEvidenceError("evidence_bundle_invalid")
        if not isinstance(self.synthesis_units, tuple) or any(
            not isinstance(unit, SupportedSynthesisUnit) for unit in self.synthesis_units
        ):
            raise PerplexityEvidenceError("evidence_bundle_invalid")
        valid_citation_ids = frozenset(citation_ids)
        if any(
            any(citation_id not in valid_citation_ids for citation_id in unit.citation_ids)
            for unit in self.synthesis_units
        ):
            raise PerplexityEvidenceError("evidence_bundle_invalid")


def canonicalize_citation_url(raw_url: str) -> str:
    """Return one deterministic HTTPS citation URL or fail without echoing input."""

    if not isinstance(raw_url, str):
        raise PerplexityEvidenceError("citation_url_invalid")
    candidate = raw_url.strip()
    if not candidate or any(character in candidate for character in "\x00\r\n\t"):
        raise PerplexityEvidenceError("citation_url_invalid")
    try:
        parsed = urlsplit(candidate)
        port = parsed.port
    except ValueError:
        raise PerplexityEvidenceError("citation_url_invalid") from None
    if parsed.scheme.lower() != "https":
        raise PerplexityEvidenceError("citation_scheme_unsafe")
    if parsed.username is not None or parsed.password is not None:
        raise PerplexityEvidenceError("citation_userinfo_forbidden")
    if parsed.hostname is None:
        raise PerplexityEvidenceError("citation_host_invalid")
    if parsed.fragment:
        raise PerplexityEvidenceError("citation_fragment_forbidden")

    host = _canonical_host(parsed.hostname)
    if port is not None and not 1 <= port <= 65535:
        raise PerplexityEvidenceError("citation_port_invalid")
    host_for_url = f"[{host}]" if ":" in host else host
    netloc = host_for_url if port in {None, 443} else f"{host_for_url}:{port}"
    path = _canonical_path(parsed.path)
    query = _canonical_query(parsed.query)
    return urlunsplit(("https", netloc, path, query, ""))


def build_perplexity_evidence(
    *,
    citation_urls: tuple[str, ...],
    provenance: PerplexityRetrievalProvenance,
    synthesis_units: tuple[SynthesisUnitInput, ...] = (),
) -> PerplexityEvidenceBundle:
    """Build deterministic support evidence and drop unsupported synthesis."""

    _validate_provenance(provenance)
    if not citation_urls:
        raise PerplexityEvidenceError("citations_missing")

    canonical_by_hash: dict[str, str] = {}
    for raw_url in citation_urls:
        try:
            canonical_url = canonicalize_citation_url(raw_url)
        except PerplexityEvidenceError:
            raise PerplexityEvidenceError("citations_invalid") from None
        url_hash = hashlib.sha256(canonical_url.encode("utf-8")).hexdigest()
        previous = canonical_by_hash.setdefault(url_hash, canonical_url)
        if previous != canonical_url:
            raise PerplexityEvidenceError("citation_hash_collision")
    if not canonical_by_hash:
        raise PerplexityEvidenceError("citations_invalid")

    citations = tuple(
        PerplexityCitationEvidence(
            citation_id=f"perplexity-citation-{url_hash}",
            canonical_url=canonical_url,
            canonical_url_sha256=url_hash,
            provenance=provenance,
        )
        for url_hash, canonical_url in sorted(
            canonical_by_hash.items(), key=lambda item: (item[1], item[0])
        )
    )
    citation_ids = frozenset(item.citation_id for item in citations)
    retained_synthesis = _retain_supported_synthesis(synthesis_units, citation_ids)
    return PerplexityEvidenceBundle(citations=citations, synthesis_units=retained_synthesis)


def _canonical_host(raw_host: str) -> str:
    host = raw_host.rstrip(".").lower()
    if not host:
        raise PerplexityEvidenceError("citation_host_invalid")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        if not address.is_global:
            raise PerplexityEvidenceError("citation_host_unsafe")
        return address.compressed.lower()
    try:
        ascii_host = host.encode("idna").decode("ascii").lower()
    except UnicodeError:
        raise PerplexityEvidenceError("citation_host_invalid") from None
    labels = ascii_host.split(".")
    if (
        len(labels) < 2
        or ascii_host.endswith(".local")
        or ascii_host.endswith(".localhost")
        or any(_DNS_LABEL_RE.fullmatch(label) is None for label in labels)
    ):
        raise PerplexityEvidenceError("citation_host_invalid")
    return ascii_host


def _canonical_path(raw_path: str) -> str:
    if _INVALID_PERCENT_RE.search(raw_path):
        raise PerplexityEvidenceError("citation_path_invalid")
    encoded = quote(raw_path, safe="/%:@!$&'()*+,;=-._~")
    percent_normalized = _normalize_percent_encoding(encoded)
    trailing_slash = percent_normalized.endswith("/")
    normalized = posixpath.normpath(percent_normalized or "/")
    if not normalized.startswith("/"):
        normalized = f"/{normalized}"
    if trailing_slash and normalized != "/":
        normalized = f"{normalized}/"
    return normalized


def _canonical_query(raw_query: str) -> str:
    if not raw_query:
        return ""
    if _INVALID_PERCENT_RE.search(raw_query):
        raise PerplexityEvidenceError("citation_query_invalid")
    try:
        pairs = parse_qsl(
            raw_query,
            keep_blank_values=True,
            strict_parsing=False,
            encoding="utf-8",
            errors="strict",
        )
    except (UnicodeDecodeError, ValueError):
        raise PerplexityEvidenceError("citation_query_invalid") from None
    for key, _value in pairs:
        normalized_key = re.sub(r"[^a-z0-9]", "", key.casefold())
        if normalized_key.startswith("xamz") or normalized_key in _SENSITIVE_QUERY_KEYS:
            raise PerplexityEvidenceError("citation_query_sensitive")
    pairs.sort(key=lambda item: (item[0], item[1]))
    return urlencode(pairs, doseq=True, safe="-._~", quote_via=quote)


def _normalize_percent_encoding(value: str) -> str:
    def replace(match: re.Match[str]) -> str:
        character = chr(int(match.group(0)[1:], 16))
        return character if character in _UNRESERVED else match.group(0).upper()

    return re.sub(r"%[0-9A-Fa-f]{2}", replace, value)


def _validate_provenance(provenance: PerplexityRetrievalProvenance) -> None:
    if not isinstance(provenance, PerplexityRetrievalProvenance):
        raise PerplexityEvidenceError("retrieval_provenance_invalid")
    if (
        not isinstance(provenance.provider_request_id, str)
        or not provenance.provider_request_id.strip()
    ):
        raise PerplexityEvidenceError("retrieval_provenance_invalid")
    if (
        not isinstance(provenance.retrieved_at, datetime)
        or provenance.retrieved_at.tzinfo is None
        or provenance.retrieved_at.utcoffset() is None
    ):
        raise PerplexityEvidenceError("retrieval_provenance_invalid")
    if (
        not isinstance(provenance.response_sha256, str)
        or _SHA256_RE.fullmatch(provenance.response_sha256) is None
    ):
        raise PerplexityEvidenceError("retrieval_provenance_invalid")


def _retain_supported_synthesis(
    units: tuple[SynthesisUnitInput, ...],
    valid_citation_ids: frozenset[str],
) -> tuple[SupportedSynthesisUnit, ...]:
    retained: dict[tuple[str, tuple[str, ...]], SupportedSynthesisUnit] = {}
    for unit in units:
        if not isinstance(unit, SynthesisUnitInput):
            continue
        if not isinstance(unit.text, str) or not isinstance(unit.citation_ids, tuple):
            continue
        if any(not isinstance(citation_id, str) for citation_id in unit.citation_ids):
            continue
        text = unit.text.strip()
        citation_ids = tuple(sorted(set(unit.citation_ids)))
        if (
            not text
            or not citation_ids
            or any(citation_id not in valid_citation_ids for citation_id in citation_ids)
        ):
            continue
        text_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
        retained[(text_hash, citation_ids)] = SupportedSynthesisUnit(
            text=text,
            text_sha256=text_hash,
            citation_ids=citation_ids,
        )
    return tuple(retained[key] for key in sorted(retained))
