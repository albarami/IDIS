"""EPO Open Patent Services connector with one bounded OAuth-to-search path."""

from __future__ import annotations

import base64
import hashlib
import json
import xml.etree.ElementTree as ET
from collections.abc import Mapping
from datetime import UTC, datetime
from urllib.parse import quote

import httpx

from idis.services.enrichment.models import (
    CachePolicyConfig,
    EnrichmentContext,
    EnrichmentProvenance,
    EnrichmentRequest,
    EnrichmentResult,
    EnrichmentStatus,
    EntityType,
    RightsClass,
)
from idis.services.enrichment.task9.bounded_probe_transport import (
    BoundedProbeResponse,
    BoundedProbeTransport,
    ProbeDeadline,
    ProbeTransportFailure,
)
from idis.services.enrichment.task9.probe_contracts import (
    EPO_OAUTH_MAX_BODY_BYTES,
    PROBE_CONTRACTS,
    epo_oauth_payload_is_valid,
)
from idis.services.enrichment.task9.probe_runner import (
    ProbeConnectorError,
    ProbeConnectorExecution,
    ProbeHttpRequest,
    ProbeOperationEvidence,
    ProbeSchemaResult,
)

EPO_OPEN_PATENT_PROVIDER_ID = "epo_open_patent"
EPO_REQUEST_BUILDER_IDENTITY = "epo-open-patent-request-builder-v2"
EPO_NORMALIZER_IDENTITY = "epo-open-patent-normalizer-v3"

_REQUEST_BUILDER_VERSION_HASH = hashlib.sha256(EPO_REQUEST_BUILDER_IDENTITY.encode()).hexdigest()
_NORMALIZER_VERSION_HASH = hashlib.sha256(EPO_NORMALIZER_IDENTITY.encode()).hexdigest()
_CACHE_TTL_SECONDS = 604_800
_OPS_NAMESPACE = "http://ops.epo.org"
_EXCHANGE_NAMESPACE = "http://www.epo.org/exchange"
_PROBE_ASSIGNEE = "Microsoft"


class EpoOpenPatentConnector:
    """Fetch one exact-assignee EPO record through bounded OPS operations."""

    def __init__(self, http_client: httpx.Client | None = None) -> None:
        self._http_client = http_client

    @property
    def provider_id(self) -> str:
        return EPO_OPEN_PATENT_PROVIDER_ID

    @property
    def rights_class(self) -> RightsClass:
        return RightsClass.GREEN

    @property
    def cache_policy(self) -> CachePolicyConfig:
        return CachePolicyConfig(ttl_seconds=_CACHE_TTL_SECONDS, no_store=False)

    @property
    def request_builder_identity(self) -> str:
        return EPO_REQUEST_BUILDER_IDENTITY

    @property
    def normalizer_identity(self) -> str:
        return EPO_NORMALIZER_IDENTITY

    @property
    def request_builder_version_hash(self) -> str:
        return _REQUEST_BUILDER_VERSION_HASH

    @property
    def normalizer_version_hash(self) -> str:
        return _NORMALIZER_VERSION_HASH

    def fetch(self, request: EnrichmentRequest, ctx: EnrichmentContext) -> EnrichmentResult:
        """Fetch one patent for a verified assignee using the probe-identical path."""

        if request.entity_type is not EntityType.COMPANY:
            return EnrichmentResult(
                status=EnrichmentStatus.ERROR,
                normalized={"error": "EPO connector only supports COMPANY entity type"},
            )
        assignee = request.query.company_name
        if not assignee or not assignee.strip():
            return EnrichmentResult(
                status=EnrichmentStatus.ERROR,
                normalized={"error": "verified assignee is required"},
            )
        credentials = ctx.byol_credentials or {}
        try:
            self._validate_credentials(credentials)
        except ProbeConnectorError:
            return EnrichmentResult(
                status=EnrichmentStatus.BLOCKED_MISSING_BYOL,
                normalized={"error": "complete EPO credential bundle is required"},
            )
        if self._http_client is None:
            return EnrichmentResult(
                status=EnrichmentStatus.ERROR,
                normalized={"error": "EPO transport is not connected"},
            )
        transport = BoundedProbeTransport(self._http_client)
        try:
            execution = self._execute(
                selector=assignee.strip(),
                credentials=credentials,
                transport=transport,
                deadline=transport.start_deadline(),
            )
        except ProbeConnectorError:
            return EnrichmentResult(
                status=EnrichmentStatus.ERROR,
                normalized={"error": "EPO provider fetch failed"},
            )
        if execution.status is ProbeSchemaResult.MISS:
            return EnrichmentResult(status=EnrichmentStatus.MISS)
        normalized = dict(execution.normalized_fields)
        provenance = EnrichmentProvenance(
            provider_id=self.provider_id,
            source_id=self.provider_id,
            retrieved_at=datetime.now(UTC),
            rights_class=self.rights_class,
            raw_ref_hash=execution.response_sha256,
            identifiers_used={"assignee": assignee.strip()},
        )
        return EnrichmentResult(
            status=EnrichmentStatus.HIT,
            normalized=normalized,
            provenance=provenance,
            raw=None,
        )

    def run_probe(
        self,
        *,
        transport: BoundedProbeTransport,
        deadline: ProbeDeadline,
        credentials: Mapping[str, str],
    ) -> ProbeConnectorExecution:
        """Run the exact provider-probes-v1 OAuth and search operations."""

        self._validate_credentials(credentials)
        return self._execute(
            selector=_PROBE_ASSIGNEE,
            credentials=credentials,
            transport=transport,
            deadline=deadline,
        )

    def build_operation_request(
        self,
        *,
        operation_id: str,
        selector: str,
        credentials: Mapping[str, str],
        bearer_token: str | None = None,
    ) -> ProbeHttpRequest:
        """Build one OPS operation; both fetch and probe call this identity."""

        contract = PROBE_CONTRACTS[self.provider_id]
        if operation_id == "oauth":
            operation = contract.operations[0]
            raw_basic = f"{credentials['consumer_key']}:{credentials['consumer_secret']}".encode()
            encoded_basic = base64.b64encode(raw_basic).decode("ascii")
            return ProbeHttpRequest(
                operation_id=operation.operation_id,
                method=operation.method,
                url=operation.safe_url,
                approved_host=operation.approved_host,
                expected_media_types=operation.expected_media_types,
                headers=(
                    ("accept", "application/json"),
                    ("authorization", f"Basic {encoded_basic}"),
                    ("content-type", "application/x-www-form-urlencoded"),
                ),
                content=operation.body,
            )
        if operation_id != "search" or bearer_token is None or not bearer_token.strip():
            raise ProbeConnectorError("operation_state_invalid")
        operation = contract.operations[1]
        encoded_query = quote(f"pa={selector}", safe="")
        url = (
            "https://ops.epo.org/3.2/rest-services/published-data/search/biblio"
            f"?q={encoded_query}&Range=1-1"
        )
        return ProbeHttpRequest(
            operation_id=operation.operation_id,
            method=operation.method,
            url=url,
            approved_host=operation.approved_host,
            expected_media_types=operation.expected_media_types,
            headers=(
                ("accept", "application/exchange+xml"),
                ("authorization", f"Bearer {bearer_token}"),
            ),
        )

    def normalize_search_response(
        self,
        body: bytes,
        *,
        selector: str,
    ) -> tuple[ProbeSchemaResult, tuple[tuple[str, object], ...]]:
        """Strictly normalize one range-limited OPS XML response."""

        try:
            root = ET.fromstring(body)
        except (ET.ParseError, UnicodeDecodeError):
            raise ProbeConnectorError("search_xml_malformed") from None
        if root.tag != f"{{{_OPS_NAMESPACE}}}world-patent-data":
            raise ProbeConnectorError("search_xml_schema_invalid")
        biblio_search = _only_direct_child(root, _OPS_NAMESPACE, "biblio-search")
        query = _only_direct_child(biblio_search, _OPS_NAMESPACE, "query")
        range_element = _only_direct_child(biblio_search, _OPS_NAMESPACE, "range")
        search_result = _only_direct_child(biblio_search, _OPS_NAMESPACE, "search-result")
        exchange_documents = _only_direct_child(
            search_result,
            _EXCHANGE_NAMESPACE,
            "exchange-documents",
        )
        total = _required_non_negative_integer_attribute(biblio_search, "total-result-count")
        echoed_query = (query.text or "").strip()
        if not _query_binds_selector(echoed_query, selector):
            raise ProbeConnectorError("search_selector_mismatch")
        if range_element.get("begin") != "1" or range_element.get("end") != "1":
            raise ProbeConnectorError("search_xml_schema_invalid")
        records = tuple(
            element
            for element in exchange_documents
            if element.tag == f"{{{_EXCHANGE_NAMESPACE}}}exchange-document"
        )
        if total == 0:
            if records:
                raise ProbeConnectorError("search_result_count_invalid")
            return ProbeSchemaResult.MISS, ()
        if len(records) != 1:
            raise ProbeConnectorError("search_result_count_invalid")
        record = records[0]
        publication = _only_descendant(record, _EXCHANGE_NAMESPACE, "publication-reference")
        docdb_records = tuple(
            child
            for child in publication
            if child.tag == f"{{{_EXCHANGE_NAMESPACE}}}document-id"
            and child.get("document-id-type") == "docdb"
        )
        if len(docdb_records) != 1:
            raise ProbeConnectorError("search_publication_identifier_invalid")
        docdb = docdb_records[0]
        country = _required_xml_text(docdb, "country", identifier=True).upper()
        doc_number = _required_xml_text(docdb, "doc-number", identifier=True).upper()
        kind = _required_xml_text(docdb, "kind", identifier=True).upper()
        if not all(value.isalnum() for value in (country, doc_number, kind)):
            raise ProbeConnectorError("search_publication_identifier_invalid")
        publication_number = f"{country}{doc_number}{kind}"
        applicant_name = _select_applicant(record)
        if not _applicant_binds_selector(applicant_name, selector):
            raise ProbeConnectorError("search_selector_mismatch")
        title = _select_title(record)
        return (
            ProbeSchemaResult.HIT,
            (
                ("applicant", applicant_name),
                ("applicant_selector_match", True),
                ("publication_country", country),
                ("publication_doc_number", doc_number),
                ("publication_kind", kind),
                ("publication_number", publication_number),
                ("selector", selector.strip()),
                ("title", title),
            ),
        )

    def _execute(
        self,
        *,
        selector: str,
        credentials: Mapping[str, str],
        transport: BoundedProbeTransport,
        deadline: ProbeDeadline,
    ) -> ProbeConnectorExecution:
        oauth_request = self.build_operation_request(
            operation_id="oauth",
            selector=selector,
            credentials=credentials,
        )
        oauth_response = _execute_request(transport, oauth_request, deadline)
        access_token = _normalize_oauth_response(oauth_response.body)
        _ensure_deadline(transport, deadline)
        search_request = self.build_operation_request(
            operation_id="search",
            selector=selector,
            credentials=credentials,
            bearer_token=access_token,
        )
        search_response = _execute_request(transport, search_request, deadline)
        status, normalized_fields = self.normalize_search_response(
            search_response.body,
            selector=selector,
        )
        _ensure_deadline(transport, deadline)
        operations = (
            _operation_evidence(oauth_request, oauth_response),
            _operation_evidence(search_request, search_response),
        )
        response_sha256 = _combined_response_sha256(operations)
        return ProbeConnectorExecution(
            provider_id=self.provider_id,
            status=status,
            normalized_fields=normalized_fields,
            operations=operations,
            response_sha256=response_sha256,
            request_builder_version_hash=self.request_builder_version_hash,
            normalizer_version_hash=self.normalizer_version_hash,
        )

    @staticmethod
    def _validate_credentials(credentials: Mapping[str, str]) -> None:
        if any(
            not isinstance(credentials.get(key), str) or not credentials[key].strip()
            for key in ("consumer_key", "consumer_secret")
        ):
            raise ProbeConnectorError(
                "credential_bundle_incomplete",
                code="ENRICHMENT_PROVIDER_INPUT_MISSING",
            )


def _execute_request(
    transport: BoundedProbeTransport,
    request: ProbeHttpRequest,
    deadline: ProbeDeadline,
) -> BoundedProbeResponse:
    try:
        return transport.execute(
            method=request.method,
            url=request.url,
            approved_host=request.approved_host,
            expected_media_types=request.expected_media_types,
            headers=request.header_mapping(),
            content=request.content,
            deadline=deadline,
        )
    except ProbeTransportFailure as exc:
        raise ProbeConnectorError(f"transport_{exc.reason.value}") from None


def _normalize_oauth_response(body: bytes) -> str:
    if not isinstance(body, bytes) or len(body) > EPO_OAUTH_MAX_BODY_BYTES:
        raise ProbeConnectorError("oauth_response_schema_invalid")
    try:
        payload = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError):
        raise ProbeConnectorError("oauth_response_malformed") from None
    if not epo_oauth_payload_is_valid(payload):
        raise ProbeConnectorError("oauth_response_schema_invalid")
    assert isinstance(payload, dict)
    token = payload["access_token"]
    assert isinstance(token, str)
    return token


def _required_xml_text(
    parent: ET.Element | None,
    local_name: str,
    *,
    identifier: bool = False,
) -> str:
    if parent is None:
        raise ProbeConnectorError("search_xml_schema_invalid")
    element = parent.find(f".//{{{_EXCHANGE_NAMESPACE}}}{local_name}")
    value = "" if element is None else (element.text or "").strip()
    if not value:
        reason = (
            "search_publication_identifier_invalid" if identifier else "search_xml_schema_invalid"
        )
        raise ProbeConnectorError(reason)
    return value


def _only_direct_child(parent: ET.Element, namespace: str, local_name: str) -> ET.Element:
    matches = tuple(parent.findall(f"{{{namespace}}}{local_name}"))
    if len(matches) != 1:
        raise ProbeConnectorError("search_xml_schema_invalid")
    return matches[0]


def _only_descendant(parent: ET.Element, namespace: str, local_name: str) -> ET.Element:
    matches = tuple(parent.findall(f".//{{{namespace}}}{local_name}"))
    if len(matches) != 1:
        raise ProbeConnectorError("search_xml_schema_invalid")
    return matches[0]


def _required_non_negative_integer_attribute(element: ET.Element, name: str) -> int:
    raw_value = element.get(name)
    try:
        value = int(raw_value) if raw_value is not None else -1
    except ValueError:
        value = -1
    if value < 0:
        raise ProbeConnectorError("search_result_count_invalid")
    return value


def _canonical_selector(value: str) -> str:
    alphanumeric_words = "".join(
        character if character.isalnum() else " " for character in value
    ).split()
    return " ".join(alphanumeric_words).casefold()


def _query_binds_selector(query: str, selector: str) -> bool:
    field, separator, value = query.partition("=")
    return (
        separator == "="
        and field.strip().casefold() == "pa"
        and _canonical_selector(value.strip().strip('"')) == _canonical_selector(selector)
    )


def _applicant_binds_selector(applicant: str, selector: str) -> bool:
    applicant_tokens = tuple(_canonical_selector(applicant).split())
    selector_tokens = tuple(_canonical_selector(selector).split())
    if not selector_tokens or len(selector_tokens) > len(applicant_tokens):
        return False
    width = len(selector_tokens)
    return any(
        applicant_tokens[index : index + width] == selector_tokens
        for index in range(len(applicant_tokens) - width + 1)
    )


def _select_applicant(record: ET.Element) -> str:
    candidates: list[tuple[int, str]] = []
    for applicant in record.findall(f".//{{{_EXCHANGE_NAMESPACE}}}applicant"):
        if applicant.get("data-format") != "epodoc":
            continue
        raw_sequence = applicant.get("sequence")
        try:
            sequence = int(raw_sequence) if raw_sequence is not None else -1
        except ValueError:
            sequence = -1
        if sequence < 0:
            raise ProbeConnectorError("search_xml_schema_invalid")
        candidates.append((sequence, _required_xml_text(applicant, "name")))
    if not candidates:
        raise ProbeConnectorError("search_xml_schema_invalid")
    return min(candidates, key=lambda candidate: (candidate[0], candidate[1].casefold()))[1]


def _select_title(record: ET.Element) -> str:
    candidates: list[tuple[bool, str, int, str]] = []
    for position, element in enumerate(
        record.findall(f".//{{{_EXCHANGE_NAMESPACE}}}invention-title")
    ):
        value = (element.text or "").strip()
        language = (element.get("lang") or "").strip().casefold()
        if value:
            candidates.append((language != "en", language, position, value))
    if not candidates:
        raise ProbeConnectorError("search_xml_schema_invalid")
    return min(candidates)[3]


def _ensure_deadline(transport: BoundedProbeTransport, deadline: ProbeDeadline) -> None:
    try:
        transport.ensure_before_deadline(deadline)
    except ProbeTransportFailure as exc:
        raise ProbeConnectorError(f"transport_{exc.reason.value}") from None


def _operation_evidence(
    request: ProbeHttpRequest,
    response: BoundedProbeResponse,
) -> ProbeOperationEvidence:
    return ProbeOperationEvidence(
        operation_id=request.operation_id,
        approved_host=request.approved_host,
        http_status_class=f"{response.status_code // 100}xx",
        response_sha256=response.response_sha256,
        attempt_count=1,
    )


def _combined_response_sha256(operations: tuple[ProbeOperationEvidence, ...]) -> str:
    material = "|".join(item.response_sha256 for item in operations).encode()
    return hashlib.sha256(material).hexdigest()
