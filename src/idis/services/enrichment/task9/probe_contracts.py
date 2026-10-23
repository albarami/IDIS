"""Exact, secret-free contracts for the Slice100 ``provider-probes-v1`` inventory."""

from __future__ import annotations

import hashlib
import json
import math
import re
import unicodedata
import xml.etree.ElementTree as ET
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Final
from urllib.parse import parse_qsl, urlsplit

from idis.services.enrichment.connectors.escwa_ispar import EscwaIsparConnector
from idis.services.enrichment.task9.provider_catalog import (
    PROBE_CONTRACT_VERSION as _PROBE_CONTRACT_VERSION,
)
from idis.services.enrichment.task9.provider_catalog import (
    PROBE_MAX_ATTEMPTS as _PROBE_MAX_ATTEMPTS,
)
from idis.services.enrichment.task9.provider_catalog import (
    PROBE_MAX_RESPONSE_BYTES as _PROBE_MAX_RESPONSE_BYTES,
)
from idis.services.enrichment.task9.provider_catalog import (
    PROBE_TIMEOUT_SECONDS as _PROBE_TIMEOUT_SECONDS,
)
from idis.services.enrichment.task9.provider_catalog import (
    PROVIDER_CATALOG,
    ProviderProbeContract,
    ProviderProbeOperation,
)

PROBE_CONTRACT_VERSION: Final = _PROBE_CONTRACT_VERSION
PROBE_MAX_ATTEMPTS: Final = _PROBE_MAX_ATTEMPTS
PROBE_MAX_RESPONSE_BYTES: Final = _PROBE_MAX_RESPONSE_BYTES
PROBE_TIMEOUT_SECONDS: Final = _PROBE_TIMEOUT_SECONDS
ProbeOperation = ProviderProbeOperation
EPO_OAUTH_MAX_BODY_BYTES: Final = 65_536
EPO_OAUTH_MAX_FIELDS: Final = 32
EPO_OAUTH_MAX_KEY_CHARS: Final = 64
EPO_OAUTH_MAX_TEXT_CHARS: Final = 8_192
EPO_OAUTH_MAX_COLLECTION_ITEMS: Final = 32
EPO_OAUTH_MAX_VALUE_DEPTH: Final = 2

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class FixtureContractError(ValueError):
    """Secret-safe failure for a malformed fixture or probe response shape."""

    code = "ENRICHMENT_PREFLIGHT_BLOCKED"

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(f"{self.code}:{reason}")


@dataclass(frozen=True, slots=True)
class FixtureEntry:
    """Hash/size/media binding for one sanitized operation response."""

    provider_id: str
    fixture_id: str
    operation_id: str
    file_name: str
    media_type: str
    byte_count: int
    sha256: str


@dataclass(frozen=True, slots=True)
class FixtureManifest:
    """Hermetic fixture manifest that can never represent Gate-A evidence."""

    entries: tuple[FixtureEntry, ...]

    @property
    def execution_mode(self) -> str:
        """Fixtures are always marked as hermetic test observations."""

        return "HERMETIC_TEST"

    @property
    def acceptance_eligible(self) -> bool:
        """Fixture observations can never satisfy Gate-A acceptance."""

        return False


class LegacyProbeClassification(StrEnum):
    """Request-shape characterization only; neither value means readiness."""

    ALIGNED_REQUEST_SHAPE = "ALIGNED_REQUEST_SHAPE"
    BLOCKS_LIVE = "BLOCKS_LIVE"


class LegacyProbeBlockerCode(StrEnum):
    """Closed inventory of reviewed legacy request/provider blockers."""

    COMPANIES_HOUSE_ITEMS_PER_PAGE_10 = "companies_house_items_per_page_10"
    FRED_LIMIT_20_PLUS_SORT = "fred_limit_20_plus_sort"
    WORLD_BANK_PER_PAGE_10 = "world_bank_per_page_10"
    ESCWA_CATALOG_ROWS_10 = "escwa_catalog_rows_10"
    ESCWA_ISPAR_LIVE_HTTP_403 = "escwa_ispar_live_http_403"
    QATAR_OPEN_DATA_LIMIT_10 = "qatar_open_data_limit_10"
    HACKERNEWS_HITS_PER_PAGE_10 = "hackernews_hits_per_page_10"
    GDELT_MODE_CASING_PLUS_MAXRECORDS_10 = "gdelt_mode_casing_plus_maxrecords_10"
    PATENTSVIEW_PER_PAGE_10_RETIRED_ENDPOINT = "patentsview_per_page_10_retired_endpoint"
    WAYBACK_LIMIT_10 = "wayback_limit_10"


class LegacyExternalBlocker(StrEnum):
    """Reviewed provider conditions recorded here, never re-proven by fixtures."""

    ESCWA_ISPAR_LIVE_HTTP_403 = "escwa_ispar_live_http_403"
    PATENTSVIEW_RETIRED_HTTP_410 = "patentsview_retired_http_410"


@dataclass(frozen=True, slots=True)
class LegacyQueryDelta:
    """One safe, reviewed semantic query difference from provider-probes-v1."""

    parameter: str
    probe_values: tuple[str, ...]
    legacy_values: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class LegacyProbeBlocker:
    """Immutable blocks-live record; it cannot become acceptance evidence."""

    provider_id: str
    code: LegacyProbeBlockerCode
    query_deltas: tuple[LegacyQueryDelta, ...]
    external_blockers: tuple[LegacyExternalBlocker, ...] = ()

    @property
    def classification(self) -> LegacyProbeClassification:
        """Every inventory row is explicitly blocks-live."""

        return LegacyProbeClassification.BLOCKS_LIVE

    @property
    def execution_mode(self) -> str:
        """The characterization is hermetic, never a live observation."""

        return "HERMETIC_TEST"

    @property
    def acceptance_eligible(self) -> bool:
        """A known blocker can never satisfy readiness or acceptance."""

        return False


@dataclass(frozen=True, slots=True)
class LegacyProbeCharacterization:
    """Secret-free result of comparing one actual legacy request shape."""

    provider_id: str
    classification: LegacyProbeClassification
    blocker: LegacyProbeBlocker | None

    @property
    def execution_mode(self) -> str:
        """Actual-path tests use only injected hermetic HTTP transport."""

        return "HERMETIC_TEST"

    @property
    def acceptance_eligible(self) -> bool:
        """Request-shape agreement is not provider readiness evidence."""

        return False


class LegacyProbeCharacterizationError(ValueError):
    """Typed, secret-safe denial for an unreviewed legacy request shape."""

    code = "LEGACY_PROBE_CHARACTERIZATION_BLOCKED"

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(f"{self.code}:{reason}")


@dataclass(frozen=True, slots=True)
class _CanonicalLegacyRequest:
    method: str
    scheme: str
    host: str
    port: int | None
    path: str
    query: tuple[tuple[str, tuple[str, ...]], ...]
    body: bytes


PROBE_CONTRACTS: Final[Mapping[str, ProviderProbeContract]] = MappingProxyType(
    {provider_id: spec.probe_contract for provider_id, spec in PROVIDER_CATALOG.items()}
)

_LEGACY_PROBE_PROVIDER_IDS: Final = frozenset(PROBE_CONTRACTS).difference(
    {"epo_open_patent", "semantic_scholar", "perplexity"}
)
_LEGACY_SECRET_QUERY_KEYS: Final[Mapping[str, frozenset[str]]] = MappingProxyType(
    {
        "finnhub": frozenset({"token"}),
        "fmp": frozenset({"apikey"}),
        "fred": frozenset({"api_key"}),
    }
)

LEGACY_PROBE_BLOCKERS: Final[Mapping[str, LegacyProbeBlocker]] = MappingProxyType(
    {
        "companies_house": LegacyProbeBlocker(
            provider_id="companies_house",
            code=LegacyProbeBlockerCode.COMPANIES_HOUSE_ITEMS_PER_PAGE_10,
            query_deltas=(LegacyQueryDelta("items_per_page", (), ("10",)),),
        ),
        "fred": LegacyProbeBlocker(
            provider_id="fred",
            code=LegacyProbeBlockerCode.FRED_LIMIT_20_PLUS_SORT,
            query_deltas=(
                LegacyQueryDelta("limit", ("1",), ("20",)),
                LegacyQueryDelta("sort_order", (), ("desc",)),
            ),
        ),
        "world_bank": LegacyProbeBlocker(
            provider_id="world_bank",
            code=LegacyProbeBlockerCode.WORLD_BANK_PER_PAGE_10,
            query_deltas=(LegacyQueryDelta("per_page", ("1",), ("10",)),),
        ),
        "escwa_catalog": LegacyProbeBlocker(
            provider_id="escwa_catalog",
            code=LegacyProbeBlockerCode.ESCWA_CATALOG_ROWS_10,
            query_deltas=(LegacyQueryDelta("rows", ("1",), ("10",)),),
        ),
        "escwa_ispar": LegacyProbeBlocker(
            provider_id="escwa_ispar",
            code=LegacyProbeBlockerCode.ESCWA_ISPAR_LIVE_HTTP_403,
            query_deltas=(),
            external_blockers=(LegacyExternalBlocker.ESCWA_ISPAR_LIVE_HTTP_403,),
        ),
        "qatar_open_data": LegacyProbeBlocker(
            provider_id="qatar_open_data",
            code=LegacyProbeBlockerCode.QATAR_OPEN_DATA_LIMIT_10,
            query_deltas=(LegacyQueryDelta("limit", ("1",), ("10",)),),
        ),
        "hackernews": LegacyProbeBlocker(
            provider_id="hackernews",
            code=LegacyProbeBlockerCode.HACKERNEWS_HITS_PER_PAGE_10,
            query_deltas=(LegacyQueryDelta("hitsPerPage", ("1",), ("10",)),),
        ),
        "gdelt": LegacyProbeBlocker(
            provider_id="gdelt",
            code=LegacyProbeBlockerCode.GDELT_MODE_CASING_PLUS_MAXRECORDS_10,
            query_deltas=(
                LegacyQueryDelta("maxrecords", ("1",), ("10",)),
                LegacyQueryDelta("mode", ("ArtList",), ("artlist",)),
            ),
        ),
        "patentsview": LegacyProbeBlocker(
            provider_id="patentsview",
            code=LegacyProbeBlockerCode.PATENTSVIEW_PER_PAGE_10_RETIRED_ENDPOINT,
            query_deltas=(
                LegacyQueryDelta(
                    "o",
                    (json.dumps({"per_page": 1}, sort_keys=True, separators=(",", ":")),),
                    (json.dumps({"per_page": 10}, sort_keys=True, separators=(",", ":")),),
                ),
            ),
            external_blockers=(LegacyExternalBlocker.PATENTSVIEW_RETIRED_HTTP_410,),
        ),
        "wayback": LegacyProbeBlocker(
            provider_id="wayback",
            code=LegacyProbeBlockerCode.WAYBACK_LIMIT_10,
            query_deltas=(LegacyQueryDelta("limit", ("1",), ("10",)),),
        ),
    }
)

if frozenset(LEGACY_PROBE_BLOCKERS) != frozenset(
    {
        "companies_house",
        "fred",
        "world_bank",
        "escwa_catalog",
        "escwa_ispar",
        "qatar_open_data",
        "hackernews",
        "gdelt",
        "patentsview",
        "wayback",
    }
):
    raise RuntimeError("legacy probe blocker inventory must have exact reviewed closure")


def characterize_legacy_probe_request(
    *,
    provider_id: str,
    method: str,
    url: str,
    body: bytes = b"",
) -> LegacyProbeCharacterization:
    """Compare one actual legacy request to probe v1 without retaining secrets.

    Query ordering, percent-encoding choices, and JSON whitespace are normalized as
    wire-equivalent. Reviewed credential query parameters are discarded before the
    comparison. Any other difference fails secret-safe instead of becoming a new
    implicit blocker or a false match.
    """

    if provider_id not in _LEGACY_PROBE_PROVIDER_IDS:
        raise LegacyProbeCharacterizationError("legacy_provider_unknown")
    contract = PROBE_CONTRACTS[provider_id]
    if len(contract.operations) != 1:
        raise LegacyProbeCharacterizationError("legacy_operation_shape_invalid")
    operation = contract.operations[0]
    expected = _canonicalize_legacy_request(
        provider_id=provider_id,
        method=operation.method,
        url=operation.safe_url,
        body=operation.body,
    )
    observed = _canonicalize_legacy_request(
        provider_id=provider_id,
        method=method,
        url=url,
        body=body,
    )
    if (
        observed.method != expected.method
        or observed.scheme != expected.scheme
        or observed.host != expected.host
        or observed.port != expected.port
        or observed.path != expected.path
        or observed.body != expected.body
    ):
        raise LegacyProbeCharacterizationError("unrecorded_legacy_request_drift")

    deltas = _legacy_query_deltas(expected.query, observed.query)
    blocker = LEGACY_PROBE_BLOCKERS.get(provider_id)
    if blocker is None:
        if deltas:
            raise LegacyProbeCharacterizationError("unrecorded_legacy_request_drift")
        return LegacyProbeCharacterization(
            provider_id=provider_id,
            classification=LegacyProbeClassification.ALIGNED_REQUEST_SHAPE,
            blocker=None,
        )
    if not deltas and blocker.query_deltas:
        raise LegacyProbeCharacterizationError("recorded_legacy_drift_missing")
    if deltas != blocker.query_deltas:
        raise LegacyProbeCharacterizationError("unrecorded_legacy_request_drift")
    return LegacyProbeCharacterization(
        provider_id=provider_id,
        classification=LegacyProbeClassification.BLOCKS_LIVE,
        blocker=blocker,
    )


def _canonicalize_legacy_request(
    *,
    provider_id: str,
    method: str,
    url: str,
    body: bytes,
) -> _CanonicalLegacyRequest:
    try:
        parsed = urlsplit(url)
        port = parsed.port
        query_pairs = parse_qsl(
            parsed.query,
            keep_blank_values=True,
            strict_parsing=True,
            max_num_fields=64,
        )
    except (UnicodeError, ValueError):
        raise LegacyProbeCharacterizationError("legacy_request_malformed") from None
    if (
        parsed.scheme.lower() != "https"
        or parsed.hostname is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
    ):
        raise LegacyProbeCharacterizationError("legacy_request_malformed")

    secret_keys = _LEGACY_SECRET_QUERY_KEYS.get(provider_id, frozenset())
    grouped: dict[str, list[str]] = {}
    for key, value in query_pairs:
        if key in secret_keys:
            continue
        grouped.setdefault(key, []).append(_canonical_query_value(value))
    query = tuple((key, tuple(sorted(values))) for key, values in sorted(grouped.items()))
    return _CanonicalLegacyRequest(
        method=method.upper(),
        scheme=parsed.scheme.lower(),
        host=parsed.hostname.lower().rstrip("."),
        port=port,
        path=parsed.path,
        query=query,
        body=body,
    )


def _canonical_query_value(value: str) -> str:
    try:
        parsed = json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return value
    if not isinstance(parsed, (dict, list)):
        return value
    return json.dumps(parsed, sort_keys=True, separators=(",", ":"))


def _legacy_query_deltas(
    expected: tuple[tuple[str, tuple[str, ...]], ...],
    observed: tuple[tuple[str, tuple[str, ...]], ...],
) -> tuple[LegacyQueryDelta, ...]:
    expected_by_key = dict(expected)
    observed_by_key = dict(observed)
    return tuple(
        LegacyQueryDelta(
            parameter=key,
            probe_values=expected_by_key.get(key, ()),
            legacy_values=observed_by_key.get(key, ()),
        )
        for key in sorted(expected_by_key.keys() | observed_by_key.keys())
        if expected_by_key.get(key, ()) != observed_by_key.get(key, ())
    )


def parse_fixture_manifest(raw: bytes) -> FixtureManifest:
    """Parse a sanitized fixture manifest without reading the filesystem."""

    try:
        payload = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError):
        raise FixtureContractError("manifest_malformed") from None
    if not isinstance(payload, dict):
        raise FixtureContractError("manifest_malformed")
    if (
        payload.get("execution_mode") != "HERMETIC_TEST"
        or payload.get("acceptance_eligible") is not False
    ):
        raise FixtureContractError("manifest_not_hermetic")
    rows = payload.get("entries")
    if not isinstance(rows, list) or not rows:
        raise FixtureContractError("manifest_entries_missing")

    entries: list[FixtureEntry] = []
    seen: set[tuple[str, str]] = set()
    seen_file_names: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            raise FixtureContractError("manifest_entry_malformed")
        try:
            entry = FixtureEntry(
                provider_id=_required_string(row, "provider_id"),
                fixture_id=_required_string(row, "fixture_id"),
                operation_id=_required_string(row, "operation_id"),
                file_name=_required_file_name(row),
                media_type=_required_string(row, "media_type"),
                byte_count=_required_positive_integer(row, "byte_count"),
                sha256=_required_sha256(row),
            )
        except (KeyError, TypeError, ValueError):
            raise FixtureContractError("manifest_entry_malformed") from None
        contract = PROBE_CONTRACTS.get(entry.provider_id)
        if contract is None or entry.fixture_id != contract.fixture_id:
            raise FixtureContractError("manifest_provider_binding_invalid")
        operation = next(
            (item for item in contract.operations if item.operation_id == entry.operation_id), None
        )
        if operation is None:
            raise FixtureContractError("manifest_operation_binding_invalid")
        normalized_media_type = entry.media_type.partition(";")[0].strip().lower()
        if normalized_media_type not in operation.expected_media_types:
            raise FixtureContractError("manifest_media_type_binding_invalid")
        identity = (entry.provider_id, entry.operation_id)
        if identity in seen:
            raise FixtureContractError("manifest_operation_duplicate")
        if entry.file_name in seen_file_names:
            raise FixtureContractError("manifest_file_name_duplicate")
        seen.add(identity)
        seen_file_names.add(entry.file_name)
        entries.append(entry)
    expected = {
        (provider_id, operation.operation_id)
        for provider_id, contract in PROBE_CONTRACTS.items()
        for operation in contract.operations
    }
    if seen != expected:
        raise FixtureContractError("manifest_operation_closure_invalid")
    return FixtureManifest(entries=tuple(entries))


def verify_fixture_entry(entry: FixtureEntry, body: bytes) -> None:
    """Verify one fixture's exact byte count and SHA-256 binding."""

    if len(body) != entry.byte_count:
        raise FixtureContractError("fixture_byte_count_mismatch")
    if hashlib.sha256(body).hexdigest() != entry.sha256:
        raise FixtureContractError("fixture_sha256_mismatch")


def validate_probe_payload(
    contract: ProviderProbeContract,
    *,
    operation_id: str,
    media_type: str,
    body: bytes,
) -> None:
    """Validate sanitized response syntax and the probe's minimum non-empty schema."""

    operation = next(
        (item for item in contract.operations if item.operation_id == operation_id), None
    )
    if operation is None:
        raise FixtureContractError("operation_unknown")
    normalized_media_type = media_type.partition(";")[0].strip().lower()
    if normalized_media_type not in operation.expected_media_types:
        raise FixtureContractError("content_type_blocked")
    if not body:
        raise FixtureContractError("body_empty")

    if normalized_media_type in {
        "application/exchange+xml",
        "application/xml",
        "application/rss+xml",
        "text/xml",
    }:
        try:
            root = ET.fromstring(body)
        except (ET.ParseError, UnicodeDecodeError):
            raise FixtureContractError("body_malformed") from None
        if not _xml_probe_has_required_result(contract.provider_id, operation_id, root):
            raise FixtureContractError("schema_result_empty")
        return

    try:
        payload = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError):
        raise FixtureContractError("body_malformed") from None
    if not _json_probe_has_required_result(contract.provider_id, operation_id, payload):
        raise FixtureContractError("schema_result_empty")


def _required_string(row: dict[object, object], key: str) -> str:
    value = row[key]
    if not isinstance(value, str) or not value:
        raise ValueError(key)
    return value


def _required_file_name(row: dict[object, object]) -> str:
    value = _required_string(row, "file_name")
    if "/" in value or "\\" in value or value in {".", ".."}:
        raise ValueError("file_name")
    return value


def _required_positive_integer(row: dict[object, object], key: str) -> int:
    value = row[key]
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(key)
    return value


def _required_sha256(row: dict[object, object]) -> str:
    value = _required_string(row, "sha256")
    if _SHA256_RE.fullmatch(value) is None:
        raise ValueError("sha256")
    return value


def _is_non_empty_string(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _json_probe_has_required_result(provider_id: str, operation_id: str, payload: object) -> bool:
    if provider_id == "epo_open_patent" and operation_id == "oauth":
        return epo_oauth_payload_is_valid(payload)
    if provider_id == "sec_edgar":
        return (
            isinstance(payload, dict)
            and str(payload.get("cik", "")).zfill(10) == "0000320193"
            and _is_non_empty_string(payload.get("name"))
            and isinstance(payload.get("filings"), dict)
        )
    if provider_id == "companies_house":
        return _list_has_mapping_with_strings(payload, "items", ("company_number", "title"))
    if provider_id == "github":
        return (
            isinstance(payload, dict)
            and payload.get("login") == "github"
            and isinstance(payload.get("id"), int)
            and not isinstance(payload.get("id"), bool)
        )
    if provider_id == "fred":
        return _list_has_mapping_with_strings(payload, "observations", ("date", "value"))
    if provider_id == "finnhub":
        return (
            isinstance(payload, dict)
            and payload.get("ticker") == "AAPL"
            and _is_non_empty_string(payload.get("name"))
        )
    if provider_id == "fmp":
        return _sequence_first_has_strings(payload, ("symbol", "companyName"), symbol="AAPL")
    if provider_id == "world_bank":
        return (
            isinstance(payload, list)
            and len(payload) >= 2
            and isinstance(payload[0], dict)
            and isinstance(payload[1], list)
            and bool(payload[1])
            and isinstance(payload[1][0], dict)
            and _is_non_empty_string(payload[1][0].get("date"))
            and payload[1][0].get("value") is not None
        )
    if provider_id == "escwa_catalog":
        return (
            isinstance(payload, dict)
            and payload.get("success") is True
            and isinstance(payload.get("result"), dict)
            and isinstance(payload["result"].get("results"), list)
            and bool(payload["result"]["results"])
        )
    if provider_id == "escwa_ispar":
        if not isinstance(payload, dict):
            return False
        raw_datasets = payload.get("datasets", payload.get("results"))
        normalized = EscwaIsparConnector._normalize_response(payload, "qat")
        datasets = normalized.get("datasets")
        return (
            isinstance(raw_datasets, list)
            and bool(raw_datasets)
            and isinstance(datasets, list)
            and bool(datasets)
            and isinstance(datasets[0], dict)
            and normalized.get("jurisdiction") == "qat"
            and normalized.get("source_filter") == "escwa"
            and _is_non_empty_string(datasets[0].get("id"))
            and _is_non_empty_string(datasets[0].get("title"))
            and _is_non_empty_string(datasets[0].get("source"))
        )
    if provider_id == "qatar_open_data":
        return _list_has_mapping_with_strings(payload, "results", ("dataset_id",))
    if provider_id == "hackernews":
        hits = payload.get("hits") if isinstance(payload, dict) else None
        return (
            isinstance(hits, list)
            and bool(hits)
            and isinstance(hits[0], dict)
            and _is_non_empty_string(hits[0].get("objectID"))
            and (
                _is_non_empty_string(hits[0].get("title"))
                or _is_non_empty_string(hits[0].get("url"))
            )
        )
    if provider_id == "gdelt":
        return _list_has_mapping_with_strings(payload, "articles", ("url", "title", "seendate"))
    if provider_id == "patentsview":
        return _list_has_mapping_with_strings(
            payload,
            "patents",
            ("patent_number", "patent_title", "patent_date", "assignee_organization"),
        )
    if provider_id == "wayback":
        return (
            isinstance(payload, list)
            and len(payload) == 2
            and isinstance(payload[0], list)
            and payload[0] == ["timestamp", "original", "statuscode"]
            and isinstance(payload[1], list)
            and len(payload[1]) == 3
            and isinstance(payload[1][0], str)
            and re.fullmatch(r"[0-9]{14}", payload[1][0]) is not None
            and _is_non_empty_string(payload[1][1])
            and isinstance(payload[1][2], str)
            and re.fullmatch(r"[0-9]{3}", payload[1][2]) is not None
        )
    if provider_id == "semantic_scholar":
        authors = payload.get("authors") if isinstance(payload, dict) else None
        external_ids = payload.get("externalIds") if isinstance(payload, dict) else None
        return (
            isinstance(payload, dict)
            and _is_non_empty_string(payload.get("paperId"))
            and _is_non_empty_string(payload.get("title"))
            and isinstance(authors, list)
            and bool(authors)
            and all(
                isinstance(author, dict)
                and _is_non_empty_string(author.get("authorId"))
                and _is_non_empty_string(author.get("name"))
                for author in authors
            )
            and isinstance(external_ids, dict)
            and bool(external_ids)
            and all(
                _is_non_empty_string(key) and _semantic_external_id_is_valid(key, value)
                for key, value in external_ids.items()
            )
        )
    if provider_id == "perplexity":
        return (
            isinstance(payload, dict)
            and isinstance(payload.get("citations"), list)
            and bool(payload["citations"])
            and all(_is_non_empty_string(value) for value in payload["citations"])
        )
    return False


def _list_has_mapping_with_strings(
    payload: object, list_key: str, required_keys: tuple[str, ...]
) -> bool:
    if not isinstance(payload, dict):
        return False
    rows = payload.get(list_key)
    return (
        isinstance(rows, list)
        and bool(rows)
        and isinstance(rows[0], dict)
        and all(_is_non_empty_string(rows[0].get(key)) for key in required_keys)
    )


def _sequence_first_has_strings(
    payload: object, required_keys: tuple[str, ...], *, symbol: str
) -> bool:
    return (
        isinstance(payload, list)
        and bool(payload)
        and isinstance(payload[0], dict)
        and payload[0].get("symbol") == symbol
        and all(_is_non_empty_string(payload[0].get(key)) for key in required_keys)
    )


def _xml_probe_has_required_result(provider_id: str, operation_id: str, root: ET.Element) -> bool:
    if provider_id == "google_news_rss":
        if _xml_local_name(root) != "rss":
            return False
        channel = next((element for element in root if _xml_local_name(element) == "channel"), None)
        if channel is None:
            return False
        item = next(
            (element for element in channel if _xml_local_name(element) == "item"),
            None,
        )
        if item is None:
            return False
        item_values = {_xml_local_name(element): (element.text or "").strip() for element in item}
        return all(item_values.get(key) for key in ("title", "link", "pubDate"))
    if provider_id == "epo_open_patent" and operation_id == "search":
        if root.tag != "{http://ops.epo.org}world-patent-data":
            return False
        biblio_search = root.find("{http://ops.epo.org}biblio-search")
        if biblio_search is None or biblio_search.get("total-result-count") is None:
            return False
        try:
            total = int(biblio_search.attrib["total-result-count"])
        except ValueError:
            return False
        query = biblio_search.find("{http://ops.epo.org}query")
        range_element = biblio_search.find("{http://ops.epo.org}range")
        result = biblio_search.find("{http://ops.epo.org}search-result")
        exchange_documents = (
            None
            if result is None
            else result.find("{http://www.epo.org/exchange}exchange-documents")
        )
        if (
            total <= 0
            or query is None
            or (query.text or "").strip() != "pa=Microsoft"
            or range_element is None
            or range_element.get("begin") != "1"
            or range_element.get("end") != "1"
            or exchange_documents is None
        ):
            return False
        records = tuple(
            element
            for element in exchange_documents
            if element.tag == "{http://www.epo.org/exchange}exchange-document"
        )
        return len(records) == 1 and _epo_exchange_document_is_complete(records[0])
    return False


def _xml_local_name(element: ET.Element) -> str:
    return str(element.tag).rsplit("}", 1)[-1]


def _first_xml_descendant(element: ET.Element, local_name: str) -> ET.Element | None:
    return next(
        (candidate for candidate in element.iter() if _xml_local_name(candidate) == local_name),
        None,
    )


def _xml_descendant_text(element: ET.Element, local_name: str) -> str:
    candidate = _first_xml_descendant(element, local_name)
    return "" if candidate is None else (candidate.text or "").strip()


def _epo_exchange_document_is_complete(record: ET.Element) -> bool:
    publication_reference = _first_xml_descendant(record, "publication-reference")
    applicant = _first_xml_descendant(record, "applicant")
    docdb = (
        None
        if publication_reference is None
        else next(
            (
                child
                for child in publication_reference
                if _xml_local_name(child) == "document-id"
                and child.get("document-id-type") == "docdb"
            ),
            None,
        )
    )
    return (
        docdb is not None
        and bool(_xml_descendant_text(docdb, "country"))
        and bool(_xml_descendant_text(docdb, "doc-number"))
        and bool(_xml_descendant_text(docdb, "kind"))
        and bool(_xml_descendant_text(record, "invention-title"))
        and applicant is not None
        and bool(_xml_descendant_text(applicant, "name"))
    )


def _semantic_external_id_is_valid(key: object, value: object) -> bool:
    if key == "CorpusId":
        return isinstance(value, int) and not isinstance(value, bool) and value > 0
    return _is_non_empty_string(value)


def epo_oauth_payload_is_valid(payload: object) -> bool:
    """Validate required OAuth fields while bounding ignored provider metadata."""

    if (
        not isinstance(payload, dict)
        or not {"access_token", "token_type", "expires_in"}.issubset(payload)
        or len(payload) > EPO_OAUTH_MAX_FIELDS
        or not all(
            _bounded_json_key(key) and _bounded_json_value(value, depth=0)
            for key, value in payload.items()
        )
    ):
        return False
    token = payload["access_token"]
    expires_in = payload["expires_in"]
    return (
        _bounded_clean_text(token, allow_empty=False)
        and token == token.strip()
        and payload["token_type"] == "Bearer"
        and isinstance(expires_in, str)
        and expires_in == expires_in.strip()
        and expires_in.isdecimal()
        and int(expires_in) > 0
    )


def _bounded_json_key(value: object) -> bool:
    return (
        isinstance(value, str)
        and 0 < len(value) <= EPO_OAUTH_MAX_KEY_CHARS
        and _is_control_free(value)
    )


def _bounded_clean_text(value: object, *, allow_empty: bool) -> bool:
    return (
        isinstance(value, str)
        and (allow_empty or bool(value))
        and len(value) <= EPO_OAUTH_MAX_TEXT_CHARS
        and _is_control_free(value)
    )


def _bounded_json_value(value: object, *, depth: int) -> bool:
    if value is None or isinstance(value, bool):
        return True
    if isinstance(value, str):
        return _bounded_clean_text(value, allow_empty=True)
    if isinstance(value, int):
        return -(2**63) <= value <= 2**63 - 1
    if isinstance(value, float):
        return math.isfinite(value)
    if isinstance(value, list):
        return (
            depth < EPO_OAUTH_MAX_VALUE_DEPTH
            and len(value) <= EPO_OAUTH_MAX_COLLECTION_ITEMS
            and all(_bounded_json_value(item, depth=depth + 1) for item in value)
        )
    if isinstance(value, dict):
        return (
            depth < EPO_OAUTH_MAX_VALUE_DEPTH
            and len(value) <= EPO_OAUTH_MAX_COLLECTION_ITEMS
            and all(
                _bounded_json_key(key) and _bounded_json_value(item, depth=depth + 1)
                for key, item in value.items()
            )
        )
    return False


def _is_control_free(value: str) -> bool:
    return all(not unicodedata.category(character).startswith("C") for character in value)
