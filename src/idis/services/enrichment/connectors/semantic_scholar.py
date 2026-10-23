"""Semantic Scholar connector with typed, selector-bound graph operations."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final, TypeAlias
from urllib.parse import quote, urlencode

import httpx

from idis.services.enrichment.models import (
    CachePolicyConfig,
    EnrichmentContext,
    EnrichmentProvenance,
    EnrichmentRequest,
    EnrichmentResult,
    EnrichmentStatus,
    RightsClass,
)
from idis.services.enrichment.task9.bounded_probe_transport import (
    BoundedProbeResponse,
    BoundedProbeTransport,
    ProbeDeadline,
    ProbeTransportFailure,
)
from idis.services.enrichment.task9.probe_contracts import PROBE_CONTRACTS
from idis.services.enrichment.task9.probe_runner import (
    ProbeConnectorError,
    ProbeConnectorExecution,
    ProbeHttpRequest,
    ProbeOperationEvidence,
    ProbeSchemaResult,
)

SEMANTIC_SCHOLAR_PROVIDER_ID = "semantic_scholar"
SEMANTIC_SCHOLAR_REQUEST_BUILDER_IDENTITY = "semantic-scholar-request-builder-v3"
SEMANTIC_SCHOLAR_NORMALIZER_IDENTITY = "semantic-scholar-normalizer-v3"
SEMANTIC_SCHOLAR_SELECTOR_MAX_CHARS: Final = 512
SEMANTIC_SCHOLAR_QUERY_MAX_CHARS: Final = 512
SEMANTIC_SCHOLAR_API_KEY_MAX_CHARS: Final = 4_096
SEMANTIC_SCHOLAR_AUTHOR_ID_MAX_CHARS: Final = 32
SEMANTIC_SCHOLAR_ROR_ID_MAX_CHARS: Final = 64

_REQUEST_BUILDER_VERSION_HASH = hashlib.sha256(
    SEMANTIC_SCHOLAR_REQUEST_BUILDER_IDENTITY.encode()
).hexdigest()
_NORMALIZER_VERSION_HASH = hashlib.sha256(SEMANTIC_SCHOLAR_NORMALIZER_IDENTITY.encode()).hexdigest()
_CACHE_TTL_SECONDS = 86_400
_PROBE_PAPER_ID = "CorpusId:215416146"
_PAPER_FIELDS = "paperId,title,year,authors,externalIds"
_TOPIC_FIELDS = f"{_PAPER_FIELDS},s2FieldsOfStudy"
_AUTHOR_DETAIL_FIELDS = "name,affiliations,normalizedAffiliations"
_PAPER_RESPONSE_FIELDS = frozenset({"paperId", "title", "year", "authors", "externalIds"})
_PAPER_ID_RE = re.compile(r"^[0-9a-fA-F]{40}$")
_ROR_ID_RE = re.compile(r"^https://ror\.org/[0-9a-z]{9}$")
_KNOWN_EXTERNAL_ID_FIELDS: Final = frozenset(
    {
        "ACL",
        "ArXiv",
        "CorpusId",
        "DBLP",
        "DOI",
        "MAG",
        "PubMed",
        "PubMedCentral",
    }
)
_EXTERNAL_SELECTOR_KEYS: Final[Mapping[str, str]] = {
    "ACL": "ACL",
    "ARXIV": "ArXiv",
    "DOI": "DOI",
    "MAG": "MAG",
    "PMID": "PubMed",
    "PMCID": "PubMedCentral",
}
SEMANTIC_SCHOLAR_FIELDS_OF_STUDY: Final = frozenset(
    {
        "Agricultural and Food Sciences",
        "Art",
        "Biology",
        "Business",
        "Chemistry",
        "Computer Science",
        "Economics",
        "Education",
        "Engineering",
        "Environmental Science",
        "Geography",
        "Geology",
        "History",
        "Law",
        "Linguistics",
        "Materials Science",
        "Mathematics",
        "Medicine",
        "Philosophy",
        "Physics",
        "Political Science",
        "Psychology",
        "Sociology",
    }
)


def _validated_text(
    value: object,
    *,
    max_chars: int,
    reason: str = "selector_invalid",
) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or len(value) > max_chars
        or not _is_control_free(value)
    ):
        raise ProbeConnectorError(reason, code="ENRICHMENT_PROVIDER_INPUT_MISSING")
    return value


@dataclass(frozen=True, slots=True)
class SemanticScholarPaperSelector:
    """One provider-documented Semantic Scholar paper identifier."""

    paper_id: str

    def __post_init__(self) -> None:
        paper_id = _validated_text(
            self.paper_id,
            max_chars=SEMANTIC_SCHOLAR_SELECTOR_MAX_CHARS,
        )
        if _PAPER_ID_RE.fullmatch(paper_id) is None:
            prefix, separator, value = paper_id.partition(":")
            if (
                not separator
                or not value
                or (prefix == "CorpusId" and (not value.isdecimal() or int(value) <= 0))
                or (prefix != "CorpusId" and prefix not in _EXTERNAL_SELECTOR_KEYS)
            ):
                raise ProbeConnectorError(
                    "selector_invalid",
                    code="ENRICHMENT_PROVIDER_INPUT_MISSING",
                )
        object.__setattr__(self, "paper_id", paper_id)


@dataclass(frozen=True, slots=True)
class SemanticScholarAuthorSelector:
    """One verified Semantic Scholar author identifier."""

    author_id: str

    def __post_init__(self) -> None:
        author_id = _validated_text(
            self.author_id,
            max_chars=SEMANTIC_SCHOLAR_AUTHOR_ID_MAX_CHARS,
        )
        if not author_id.isdecimal() or int(author_id) <= 0:
            raise ProbeConnectorError("selector_invalid", code="ENRICHMENT_PROVIDER_INPUT_MISSING")
        object.__setattr__(self, "author_id", author_id)


@dataclass(frozen=True, slots=True)
class SemanticScholarTopicSelector:
    """One bounded topic query paired with a provider field-of-study category."""

    query: str
    field_of_study: str

    def __post_init__(self) -> None:
        query = _validated_text(
            self.query,
            max_chars=SEMANTIC_SCHOLAR_QUERY_MAX_CHARS,
        )
        field_of_study = _validated_text(self.field_of_study, max_chars=64)
        if field_of_study not in SEMANTIC_SCHOLAR_FIELDS_OF_STUDY:
            raise ProbeConnectorError("selector_invalid", code="ENRICHMENT_PROVIDER_INPUT_MISSING")
        object.__setattr__(self, "query", query)
        object.__setattr__(self, "field_of_study", field_of_study)


@dataclass(frozen=True, slots=True)
class SemanticScholarOrganizationSelector:
    """A verified ROR identifier paired with one verified S2 author identifier."""

    ror_id: str
    author_id: str

    def __post_init__(self) -> None:
        ror_id = _validated_text(
            self.ror_id,
            max_chars=SEMANTIC_SCHOLAR_ROR_ID_MAX_CHARS,
        ).lower()
        author_id = _validated_text(
            self.author_id,
            max_chars=SEMANTIC_SCHOLAR_AUTHOR_ID_MAX_CHARS,
        )
        if _ROR_ID_RE.fullmatch(ror_id) is None or not author_id.isdecimal() or int(author_id) <= 0:
            raise ProbeConnectorError("selector_invalid", code="ENRICHMENT_PROVIDER_INPUT_MISSING")
        object.__setattr__(self, "ror_id", ror_id)
        object.__setattr__(self, "author_id", author_id)


SemanticScholarSelector: TypeAlias = (
    SemanticScholarPaperSelector
    | SemanticScholarAuthorSelector
    | SemanticScholarTopicSelector
    | SemanticScholarOrganizationSelector
)


class SemanticScholarConnector:
    """Fetch a selector-bound paper record through bounded Graph API operations."""

    def __init__(self, http_client: httpx.Client | None = None) -> None:
        self._http_client = http_client

    @property
    def provider_id(self) -> str:
        return SEMANTIC_SCHOLAR_PROVIDER_ID

    @property
    def rights_class(self) -> RightsClass:
        return RightsClass.YELLOW

    @property
    def cache_policy(self) -> CachePolicyConfig:
        return CachePolicyConfig(ttl_seconds=_CACHE_TTL_SECONDS, no_store=False)

    @property
    def request_builder_identity(self) -> str:
        return SEMANTIC_SCHOLAR_REQUEST_BUILDER_IDENTITY

    @property
    def normalizer_identity(self) -> str:
        return SEMANTIC_SCHOLAR_NORMALIZER_IDENTITY

    @property
    def request_builder_version_hash(self) -> str:
        return _REQUEST_BUILDER_VERSION_HASH

    @property
    def normalizer_version_hash(self) -> str:
        return _NORMALIZER_VERSION_HASH

    def fetch(self, request: EnrichmentRequest, ctx: EnrichmentContext) -> EnrichmentResult:
        """Refuse the legacy company-name seam because it cannot identify a selector type."""

        del request, ctx
        return EnrichmentResult(
            status=EnrichmentStatus.ERROR,
            normalized={"error": "typed Semantic Scholar selector is required"},
        )

    def fetch_selected(
        self,
        selector: SemanticScholarSelector,
        ctx: EnrichmentContext,
    ) -> EnrichmentResult:
        """Fetch through the one typed builder/normalizer dispatcher."""

        if not _is_selector(selector):
            return EnrichmentResult(
                status=EnrichmentStatus.ERROR,
                normalized={"error": "typed Semantic Scholar selector is required"},
            )
        credentials = ctx.byol_credentials or {}
        try:
            self._validate_credentials(credentials)
        except ProbeConnectorError:
            return EnrichmentResult(
                status=EnrichmentStatus.BLOCKED_MISSING_BYOL,
                normalized={"error": "Semantic Scholar API key is required"},
            )
        if self._http_client is None:
            return EnrichmentResult(
                status=EnrichmentStatus.ERROR,
                normalized={"error": "Semantic Scholar transport is not connected"},
            )
        transport = BoundedProbeTransport(self._http_client)
        try:
            execution = self._execute_selected(
                selector=selector,
                credentials=credentials,
                transport=transport,
                deadline=transport.start_deadline(),
            )
        except ProbeConnectorError:
            return EnrichmentResult(
                status=EnrichmentStatus.ERROR,
                normalized={"error": "Semantic Scholar provider fetch failed"},
            )
        if execution.status is ProbeSchemaResult.MISS:
            return EnrichmentResult(status=EnrichmentStatus.MISS)
        normalized = dict(execution.normalized_fields)
        return EnrichmentResult(
            status=EnrichmentStatus.HIT,
            normalized=normalized,
            provenance=EnrichmentProvenance(
                provider_id=self.provider_id,
                source_id=self.provider_id,
                retrieved_at=datetime.now(UTC),
                rights_class=self.rights_class,
                raw_ref_hash=execution.response_sha256,
                identifiers_used=_selector_identifiers(selector),
            ),
            raw=None,
        )

    def run_probe(
        self,
        *,
        transport: BoundedProbeTransport,
        deadline: ProbeDeadline,
        credentials: Mapping[str, str],
    ) -> ProbeConnectorExecution:
        """Run the exact paper-identifier Gate-A probe over the shared dispatcher."""

        self._validate_credentials(credentials)
        return self._execute_selected(
            selector=SemanticScholarPaperSelector(_PROBE_PAPER_ID),
            credentials=credentials,
            transport=transport,
            deadline=deadline,
        )

    def build_operation_requests(
        self,
        selector: SemanticScholarSelector,
        *,
        credentials: Mapping[str, str],
    ) -> tuple[ProbeHttpRequest, ...]:
        """Dispatch one typed selector to its exact Graph API operation sequence."""

        if not _is_selector(selector):
            raise ProbeConnectorError("selector_invalid", code="ENRICHMENT_PROVIDER_INPUT_MISSING")
        api_key = _validated_api_key(credentials)
        if isinstance(selector, SemanticScholarPaperSelector):
            paper_id = quote(selector.paper_id, safe=":")
            return (
                self._request(
                    "probe",
                    f"https://api.semanticscholar.org/graph/v1/paper/{paper_id}"
                    f"?fields={_PAPER_FIELDS}",
                    api_key,
                ),
            )
        if isinstance(selector, SemanticScholarAuthorSelector):
            return (self._author_papers_request(selector.author_id, api_key),)
        if isinstance(selector, SemanticScholarTopicSelector):
            query = urlencode(
                (
                    ("query", selector.query),
                    ("offset", "0"),
                    ("limit", "1"),
                    ("fields", _TOPIC_FIELDS),
                    ("fieldsOfStudy", selector.field_of_study),
                ),
                quote_via=quote,
                safe=",",
            )
            return (
                self._request(
                    "topic_search",
                    f"https://api.semanticscholar.org/graph/v1/paper/search?{query}",
                    api_key,
                ),
            )
        return (
            self._request(
                "author_detail",
                f"https://api.semanticscholar.org/graph/v1/author/{selector.author_id}"
                f"?fields={_AUTHOR_DETAIL_FIELDS}",
                api_key,
            ),
            self._author_papers_request(selector.author_id, api_key),
        )

    def normalize_selected_responses(
        self,
        selector: SemanticScholarSelector,
        bodies: tuple[bytes, ...],
    ) -> tuple[ProbeSchemaResult, tuple[tuple[str, object], ...]]:
        """Dispatch provider-shaped JSON to its selector-specific binding contract."""

        if not _is_selector(selector):
            raise ProbeConnectorError("selector_invalid", code="ENRICHMENT_PROVIDER_INPUT_MISSING")
        expected_count = 2 if isinstance(selector, SemanticScholarOrganizationSelector) else 1
        if len(bodies) != expected_count:
            raise ProbeConnectorError("response_json_schema_invalid")
        payloads = tuple(_decode_json(body) for body in bodies)
        if isinstance(selector, SemanticScholarPaperSelector):
            fields = _normalize_paper(payloads[0], selector_type="paper")
            _bind_paper_identifier(selector.paper_id, dict(fields))
            return ProbeSchemaResult.HIT, fields
        if isinstance(selector, SemanticScholarAuthorSelector):
            paper = _one_author_paper(payloads[0])
            if paper is None:
                return ProbeSchemaResult.MISS, ()
            fields = _normalize_paper(paper, selector_type="author")
            _bind_author(selector.author_id, dict(fields))
            return ProbeSchemaResult.HIT, fields + (("selector_author_id", selector.author_id),)
        if isinstance(selector, SemanticScholarTopicSelector):
            paper = _one_topic_paper(payloads[0])
            if paper is None:
                return ProbeSchemaResult.MISS, ()
            fields = _normalize_paper(
                paper,
                selector_type="topic",
                allowed_extra_fields=frozenset({"s2FieldsOfStudy"}),
            )
            _bind_topic(selector.field_of_study, paper)
            query_hash = hashlib.sha256(_canonical_query(selector.query).encode()).hexdigest()
            return ProbeSchemaResult.HIT, fields + (
                ("selector_field_of_study", selector.field_of_study),
                ("selector_query_sha256", query_hash),
            )
        _bind_organization(selector, payloads[0])
        paper = _one_author_paper(payloads[1])
        if paper is None:
            return ProbeSchemaResult.MISS, ()
        fields = _normalize_paper(paper, selector_type="organization")
        _bind_author(selector.author_id, dict(fields))
        return ProbeSchemaResult.HIT, fields + (
            ("selector_author_id", selector.author_id),
            ("selector_ror_id", selector.ror_id),
        )

    def _execute_selected(
        self,
        *,
        selector: SemanticScholarSelector,
        credentials: Mapping[str, str],
        transport: BoundedProbeTransport,
        deadline: ProbeDeadline,
    ) -> ProbeConnectorExecution:
        requests = self.build_operation_requests(selector, credentials=credentials)
        responses: list[BoundedProbeResponse] = []
        try:
            for request in requests:
                responses.append(
                    transport.execute(
                        method=request.method,
                        url=request.url,
                        approved_host=request.approved_host,
                        expected_media_types=request.expected_media_types,
                        headers=request.header_mapping(),
                        content=request.content,
                        deadline=deadline,
                    )
                )
        except ProbeTransportFailure as exc:
            raise ProbeConnectorError(f"transport_{exc.reason.value}") from None
        status, normalized_fields = self.normalize_selected_responses(
            selector,
            tuple(response.body for response in responses),
        )
        try:
            transport.ensure_before_deadline(deadline)
        except ProbeTransportFailure as exc:
            raise ProbeConnectorError(f"transport_{exc.reason.value}") from None
        operations = tuple(
            ProbeOperationEvidence(
                operation_id=request.operation_id,
                approved_host=request.approved_host,
                http_status_class=f"{response.status_code // 100}xx",
                response_sha256=response.response_sha256,
                attempt_count=1,
            )
            for request, response in zip(requests, responses, strict=True)
        )
        return ProbeConnectorExecution(
            provider_id=self.provider_id,
            status=status,
            normalized_fields=normalized_fields,
            operations=operations,
            response_sha256=_combined_response_sha256(operations),
            request_builder_version_hash=self.request_builder_version_hash,
            normalizer_version_hash=self.normalizer_version_hash,
        )

    def _request(self, operation_id: str, url: str, api_key: str) -> ProbeHttpRequest:
        operation = PROBE_CONTRACTS[self.provider_id].operations[0]
        return ProbeHttpRequest(
            operation_id=operation_id,
            method="GET",
            url=url,
            approved_host=operation.approved_host,
            expected_media_types=operation.expected_media_types,
            headers=(("accept", "application/json"), ("x-api-key", api_key)),
        )

    def _author_papers_request(self, author_id: str, api_key: str) -> ProbeHttpRequest:
        return self._request(
            "author_papers",
            f"https://api.semanticscholar.org/graph/v1/author/{quote(author_id, safe='')}"
            f"/papers?offset=0&limit=1&fields={_PAPER_FIELDS}",
            api_key,
        )

    @staticmethod
    def _validate_credentials(credentials: Mapping[str, str]) -> None:
        _validated_api_key(credentials)


def _is_selector(value: object) -> bool:
    return isinstance(
        value,
        (
            SemanticScholarPaperSelector,
            SemanticScholarAuthorSelector,
            SemanticScholarTopicSelector,
            SemanticScholarOrganizationSelector,
        ),
    )


def _decode_json(body: bytes) -> object:
    try:
        return json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError):
        raise ProbeConnectorError("response_json_malformed") from None


def _normalize_paper(
    payload: object,
    *,
    selector_type: str,
    allowed_extra_fields: frozenset[str] = frozenset(),
) -> tuple[tuple[str, object], ...]:
    if not isinstance(payload, dict) or frozenset(payload) != (
        _PAPER_RESPONSE_FIELDS | allowed_extra_fields
    ):
        raise ProbeConnectorError("response_json_schema_invalid")
    paper_id = payload["paperId"]
    title = payload["title"]
    year = payload["year"]
    authors = payload["authors"]
    external_ids = payload["externalIds"]
    if (
        not _non_empty_text(paper_id)
        or _PAPER_ID_RE.fullmatch(paper_id.strip()) is None
        or not _non_empty_text(title)
        or isinstance(year, bool)
        or not isinstance(year, int)
        or year <= 0
        or not isinstance(authors, list)
        or not authors
        or not isinstance(external_ids, dict)
        or not external_ids
    ):
        raise ProbeConnectorError("response_json_schema_invalid")
    normalized_authors: list[tuple[str, str]] = []
    for author in authors:
        if (
            not isinstance(author, dict)
            or frozenset(author) != {"authorId", "name"}
            or not _non_empty_text(author.get("authorId"))
            or not _non_empty_text(author.get("name"))
        ):
            raise ProbeConnectorError("response_json_schema_invalid")
        normalized_authors.append((author["authorId"].strip(), author["name"].strip()))
    normalized_external_ids: list[tuple[str, str | int]] = []
    for key, value in external_ids.items():
        if not isinstance(key, str) or key not in _KNOWN_EXTERNAL_ID_FIELDS:
            raise ProbeConnectorError("response_json_schema_invalid")
        if key == "CorpusId":
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ProbeConnectorError("response_json_schema_invalid")
            normalized_external_ids.append((key, value))
        else:
            if not _non_empty_text(value):
                raise ProbeConnectorError("response_json_schema_invalid")
            normalized_external_ids.append((key, value.strip()))
    return (
        ("attribution_required", True),
        ("attribution_text", "Data from Semantic Scholar"),
        ("authors", tuple(sorted(normalized_authors))),
        ("external_ids", tuple(sorted(normalized_external_ids, key=lambda item: item[0]))),
        ("paper_id", paper_id.strip()),
        ("selector_type", selector_type),
        ("title", title.strip()),
        ("year", year),
    )


def _bind_paper_identifier(selector: str, fields: dict[str, object]) -> None:
    raw_external_ids = fields.get("external_ids")
    paper_id = fields.get("paper_id")
    if not isinstance(raw_external_ids, tuple) or not isinstance(paper_id, str):
        raise ProbeConnectorError("response_json_schema_invalid")
    external_ids = dict(raw_external_ids)
    if _PAPER_ID_RE.fullmatch(selector):
        matches = paper_id.casefold() == selector.casefold()
    else:
        prefix, separator, value = selector.partition(":")
        if not separator or not value:
            raise ProbeConnectorError("selector_invalid", code="ENRICHMENT_PROVIDER_INPUT_MISSING")
        if prefix == "CorpusId":
            matches = value.isdecimal() and external_ids.get("CorpusId") == int(value)
        else:
            external_key = _EXTERNAL_SELECTOR_KEYS.get(prefix)
            if external_key is None:
                raise ProbeConnectorError(
                    "selector_invalid",
                    code="ENRICHMENT_PROVIDER_INPUT_MISSING",
                )
            external_value = external_ids.get(external_key)
            matches = (
                isinstance(external_value, str) and external_value.casefold() == value.casefold()
            )
    if not matches:
        raise ProbeConnectorError("selector_response_mismatch")


def _bind_author(author_id: str, fields: dict[str, object]) -> None:
    authors = fields["authors"]
    if not isinstance(authors, tuple) or not any(item[0] == author_id for item in authors):
        raise ProbeConnectorError("selector_response_mismatch")


def _one_author_paper(payload: object) -> dict[object, object] | None:
    if not isinstance(payload, dict) or frozenset(payload) not in (
        {"data", "offset"},
        {"data", "next", "offset"},
    ):
        raise ProbeConnectorError("response_json_schema_invalid")
    data = payload["data"]
    offset = payload["offset"]
    next_offset = payload.get("next")
    if (
        isinstance(offset, bool)
        or not isinstance(offset, int)
        or offset != 0
        or not isinstance(data, list)
        or len(data) > 1
        or (
            next_offset is not None
            and (
                isinstance(next_offset, bool)
                or not isinstance(next_offset, int)
                or next_offset <= offset
            )
        )
    ):
        raise ProbeConnectorError("response_json_schema_invalid")
    if not data:
        if next_offset is not None:
            raise ProbeConnectorError("response_json_schema_invalid")
        return None
    if not isinstance(data[0], dict):
        raise ProbeConnectorError("response_json_schema_invalid")
    return data[0]


def _one_topic_paper(payload: object) -> dict[object, object] | None:
    if not isinstance(payload, dict) or frozenset(payload) not in (
        {"data", "offset", "total"},
        {"data", "next", "offset", "total"},
    ):
        raise ProbeConnectorError("response_json_schema_invalid")
    total = payload["total"]
    data = payload["data"]
    offset = payload["offset"]
    next_offset = payload.get("next")
    if (
        isinstance(offset, bool)
        or not isinstance(offset, int)
        or offset != 0
        or isinstance(total, bool)
        or not isinstance(total, int)
        or total < 0
        or not isinstance(data, list)
        or len(data) > 1
        or total < len(data)
        or (
            next_offset is not None
            and (
                isinstance(next_offset, bool)
                or not isinstance(next_offset, int)
                or next_offset <= offset
            )
        )
    ):
        raise ProbeConnectorError("response_json_schema_invalid")
    returned_end = offset + len(data)
    if (next_offset is None and total != returned_end) or (
        next_offset is not None and (next_offset != returned_end or total <= returned_end)
    ):
        raise ProbeConnectorError("response_json_schema_invalid")
    if not data:
        if total != 0 or next_offset is not None:
            raise ProbeConnectorError("response_json_schema_invalid")
        return None
    if not isinstance(data[0], dict):
        raise ProbeConnectorError("response_json_schema_invalid")
    return data[0]


def _bind_topic(field_of_study: str, paper: Mapping[object, object]) -> None:
    rows = paper.get("s2FieldsOfStudy")
    if not isinstance(rows, list) or not rows:
        raise ProbeConnectorError("response_json_schema_invalid")
    categories: set[str] = set()
    for row in rows:
        if (
            not isinstance(row, dict)
            or frozenset(row) != {"category", "source"}
            or not _non_empty_text(row.get("category"))
            or not _non_empty_text(row.get("source"))
        ):
            raise ProbeConnectorError("response_json_schema_invalid")
        categories.add(row["category"].strip())
    if field_of_study not in categories:
        raise ProbeConnectorError("selector_response_mismatch")


def _bind_organization(
    selector: SemanticScholarOrganizationSelector,
    payload: object,
) -> None:
    expected_fields = {"affiliations", "authorId", "name", "normalizedAffiliations"}
    if not isinstance(payload, dict) or frozenset(payload) != expected_fields:
        raise ProbeConnectorError("response_json_schema_invalid")
    if (
        payload["authorId"] != selector.author_id
        or not _non_empty_text(payload["name"])
        or not isinstance(payload["affiliations"], list)
        or not all(_non_empty_text(value) for value in payload["affiliations"])
        or not isinstance(payload["normalizedAffiliations"], list)
        or not payload["normalizedAffiliations"]
    ):
        raise ProbeConnectorError("selector_response_mismatch")
    ror_ids: set[str] = set()
    for affiliation in payload["normalizedAffiliations"]:
        if (
            not isinstance(affiliation, dict)
            or frozenset(affiliation) != {"rorDisplayName", "rorId"}
            or not _non_empty_text(affiliation.get("rorId"))
            or not _non_empty_text(affiliation.get("rorDisplayName"))
        ):
            raise ProbeConnectorError("response_json_schema_invalid")
        ror_ids.add(affiliation["rorId"].strip().lower())
    if selector.ror_id not in ror_ids:
        raise ProbeConnectorError("selector_response_mismatch")


def _selector_identifiers(selector: SemanticScholarSelector) -> dict[str, str]:
    if isinstance(selector, SemanticScholarPaperSelector):
        return {"semantic_scholar_paper_id": selector.paper_id}
    if isinstance(selector, SemanticScholarAuthorSelector):
        return {"semantic_scholar_author_id": selector.author_id}
    if isinstance(selector, SemanticScholarTopicSelector):
        return {
            "semantic_scholar_topic_query_sha256": hashlib.sha256(
                _canonical_query(selector.query).encode()
            ).hexdigest(),
            "semantic_scholar_field_of_study": selector.field_of_study,
        }
    return {
        "ror_id": selector.ror_id,
        "semantic_scholar_author_id": selector.author_id,
    }


def _canonical_query(value: str) -> str:
    return " ".join(value.split()).casefold()


def _validated_api_key(credentials: Mapping[str, str]) -> str:
    api_key = credentials.get("api_key")
    if (
        not isinstance(api_key, str)
        or not api_key
        or api_key != api_key.strip()
        or len(api_key) > SEMANTIC_SCHOLAR_API_KEY_MAX_CHARS
        or not _is_control_free(api_key)
    ):
        raise ProbeConnectorError(
            "credential_bundle_incomplete",
            code="ENRICHMENT_PROVIDER_INPUT_MISSING",
        )
    return api_key


def _is_control_free(value: str) -> bool:
    return all(not unicodedata.category(character).startswith("C") for character in value)


def _combined_response_sha256(operations: tuple[ProbeOperationEvidence, ...]) -> str:
    if len(operations) == 1:
        return operations[0].response_sha256
    material = "|".join(operation.response_sha256 for operation in operations).encode()
    return hashlib.sha256(material).hexdigest()


def _non_empty_text(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())
