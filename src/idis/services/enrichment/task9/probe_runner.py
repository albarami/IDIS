"""Generic hermetic runner and safe receipt intents for provider probe adapters."""

from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Protocol, cast, runtime_checkable

from idis.services.enrichment.task9.bounded_probe_transport import (
    BoundedProbeTransport,
    ProbeDeadline,
    ProbeTransportFailure,
)
from idis.services.enrichment.task9.perplexity_evidence import (
    PerplexityCitationEvidence,
    PerplexityRetrievalProvenance,
    SupportedSynthesisUnit,
)
from idis.services.enrichment.task9.probe_contracts import PROBE_CONTRACTS
from idis.services.enrichment.task9.provider_catalog import PROVIDER_CATALOG

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class ProbeSchemaResult(StrEnum):
    """Connector normalization result; probe acceptance requires a non-empty HIT."""

    HIT = "HIT"
    MISS = "MISS"


class ProbeConnectorError(RuntimeError):
    """Stable payload-free connector rejection."""

    def __init__(
        self,
        reason: str,
        *,
        code: str = "ENRICHMENT_PREFLIGHT_BLOCKED",
    ) -> None:
        self.code = code
        self.reason = reason
        super().__init__(f"{code}:{reason}")


class ProbeRunnerError(RuntimeError):
    """Stable payload-free runner rejection."""

    code = "ENRICHMENT_PREFLIGHT_BLOCKED"

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(f"{self.code}:{reason}")


@dataclass(frozen=True, slots=True)
class ProbeHttpRequest:
    """One connector-built operation request; secret-bearing fields hide from repr."""

    operation_id: str
    method: str
    url: str
    approved_host: str
    expected_media_types: tuple[str, ...]
    headers: tuple[tuple[str, str], ...] = field(repr=False)
    content: bytes = field(default=b"", repr=False)

    def header_mapping(self) -> dict[str, str]:
        """Return a fresh transient header mapping for HTTPX."""

        return dict(self.headers)


@dataclass(frozen=True, slots=True)
class ProbeOperationEvidence:
    """Safe evidence for one one-attempt operation."""

    operation_id: str
    approved_host: str
    http_status_class: str
    response_sha256: str
    attempt_count: int


@dataclass(frozen=True, slots=True)
class ProbeConnectorExecution:
    """Connector-normalized result consumed by the generic runner."""

    provider_id: str
    status: ProbeSchemaResult
    normalized_fields: tuple[tuple[str, object], ...]
    operations: tuple[ProbeOperationEvidence, ...]
    response_sha256: str
    request_builder_version_hash: str
    normalizer_version_hash: str


@runtime_checkable
class ProviderProbeConnector(Protocol):
    """Minimal adapter seam shared by EPO, Semantic Scholar, and later providers."""

    @property
    def provider_id(self) -> str: ...

    @property
    def request_builder_version_hash(self) -> str: ...

    @property
    def normalizer_version_hash(self) -> str: ...

    def run_probe(
        self,
        *,
        transport: BoundedProbeTransport,
        deadline: ProbeDeadline,
        credentials: Mapping[str, str],
    ) -> ProbeConnectorExecution: ...


@dataclass(frozen=True, slots=True)
class ProbeRunRequest:
    """Safe governed context for one hermetic probe invocation."""

    tenant_id: str
    provider_id: str
    safe_request_id: str
    rights_approval_id: str
    credential_version_id: str


@dataclass(frozen=True, slots=True)
class ProviderProbeReceiptIntent:
    """Plan-pinned safe fields for future durable provider probe persistence."""

    tenant_id: str
    provider_id: str
    contract_version: str
    fixture_id: str
    request_builder_version_hash: str
    host_operations: tuple[tuple[str, str], ...]
    safe_request_id: str
    started_at: datetime
    completed_at: datetime
    http_status_class: str
    schema_result: str
    rights_approval_id: str
    credential_version_id: str
    response_sha256: str
    attempt_count: int
    timeout_seconds: int


@dataclass(frozen=True, slots=True)
class ProviderProbeObservation:
    """Hermetic wrapper that can never be serialized as live readiness."""

    receipt: ProviderProbeReceiptIntent
    normalized_fields: tuple[tuple[str, object], ...]
    normalizer_version_hash: str

    @property
    def execution_mode(self) -> str:
        return "HERMETIC_TEST"

    @property
    def acceptance_eligible(self) -> bool:
        return False


class ProbeRunner:
    """Run registered adapters over one bounded transport and one shared deadline."""

    def __init__(
        self,
        *,
        transport: BoundedProbeTransport,
        connectors: Mapping[str, ProviderProbeConnector],
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        copied: dict[str, ProviderProbeConnector] = {}
        for provider_id, connector in connectors.items():
            try:
                connector_provider_id = connector.provider_id
            except Exception:
                raise ProbeRunnerError("connector_registration_mismatch") from None
            if (
                not isinstance(provider_id, str)
                or not isinstance(connector_provider_id, str)
                or provider_id != connector_provider_id
                or provider_id not in PROVIDER_CATALOG
            ):
                raise ProbeRunnerError("connector_registration_mismatch")
            copied[provider_id] = connector
        self._transport = transport
        self._connectors: Mapping[str, ProviderProbeConnector] = MappingProxyType(copied)
        self._now = now

    def run_probe(
        self,
        request: ProbeRunRequest,
        *,
        credentials: Mapping[str, str],
    ) -> ProviderProbeObservation:
        """Run one connected provider; credentials remain transient and unreported."""

        self._validate_request(request)
        if request.provider_id not in PROVIDER_CATALOG:
            raise ProbeRunnerError("provider_unknown")
        connector = self._connectors.get(request.provider_id)
        if connector is None:
            raise ProbeRunnerError("provider_not_connected")

        started_at = self._aware_now()
        deadline = self._transport.start_deadline()
        try:
            execution = connector.run_probe(
                transport=self._transport,
                deadline=deadline,
                credentials=credentials,
            )
        except (ProbeConnectorError, ProbeTransportFailure):
            raise
        except Exception:
            raise ProbeRunnerError("connector_execution_failed") from None
        self._transport.ensure_before_deadline(deadline)
        completed_at = self._aware_now()
        contract = PROBE_CONTRACTS[request.provider_id]
        self._validate_execution(connector, execution, contract_provider=request.provider_id)
        if execution.status is not ProbeSchemaResult.HIT or not execution.normalized_fields:
            raise ProbeRunnerError("schema_result_empty")
        if completed_at <= started_at:
            raise ProbeRunnerError("probe_time_invalid")

        receipt = ProviderProbeReceiptIntent(
            tenant_id=request.tenant_id,
            provider_id=request.provider_id,
            contract_version=contract.contract_version,
            fixture_id=contract.fixture_id,
            request_builder_version_hash=execution.request_builder_version_hash,
            host_operations=tuple(
                (evidence.approved_host, evidence.operation_id) for evidence in execution.operations
            ),
            safe_request_id=request.safe_request_id,
            started_at=started_at,
            completed_at=completed_at,
            http_status_class="2xx",
            schema_result=execution.status.value,
            rights_approval_id=request.rights_approval_id,
            credential_version_id=request.credential_version_id,
            response_sha256=execution.response_sha256,
            attempt_count=1,
            timeout_seconds=contract.timeout_seconds,
        )
        return ProviderProbeObservation(
            receipt=receipt,
            normalized_fields=execution.normalized_fields,
            normalizer_version_hash=execution.normalizer_version_hash,
        )

    @staticmethod
    def _validate_request(request: ProbeRunRequest) -> None:
        if not isinstance(request, ProbeRunRequest) or any(
            not isinstance(value, str) or not value.strip()
            for value in (
                request.tenant_id,
                request.provider_id,
                request.safe_request_id,
                request.rights_approval_id,
                request.credential_version_id,
            )
        ):
            raise ProbeRunnerError("probe_request_invalid")

    def _aware_now(self) -> datetime:
        try:
            value = self._now()
        except Exception:
            raise ProbeRunnerError("probe_time_invalid") from None
        if not isinstance(value, datetime):
            raise ProbeRunnerError("probe_time_invalid")
        try:
            if value.tzinfo is None or value.utcoffset() is None:
                raise ProbeRunnerError("probe_time_invalid")
        except ProbeRunnerError:
            raise
        except Exception:
            raise ProbeRunnerError("probe_time_invalid") from None
        return value

    @staticmethod
    def _validate_execution(
        connector: ProviderProbeConnector,
        execution: ProbeConnectorExecution,
        *,
        contract_provider: str,
    ) -> None:
        contract = PROBE_CONTRACTS[contract_provider]
        if not isinstance(execution, ProbeConnectorExecution):
            raise ProbeRunnerError("connector_execution_invalid")
        try:
            connector_builder_hash = connector.request_builder_version_hash
            connector_normalizer_hash = connector.normalizer_version_hash
            catalog_builder_hash = PROVIDER_CATALOG[
                contract_provider
            ].runtime_lineage.request_builder_version_hash
        except Exception:
            raise ProbeRunnerError("connector_execution_invalid") from None
        if (
            not isinstance(execution.provider_id, str)
            or not isinstance(execution.status, ProbeSchemaResult)
            or not _is_sha256(execution.request_builder_version_hash)
            or not _is_sha256(execution.normalizer_version_hash)
            or not _is_sha256(execution.response_sha256)
            or not _is_sha256(connector_builder_hash)
            or not _is_sha256(connector_normalizer_hash)
            or not _is_sha256(catalog_builder_hash)
            or not isinstance(execution.operations, tuple)
            or not isinstance(execution.normalized_fields, tuple)
            or execution.provider_id != contract_provider
            or execution.request_builder_version_hash != connector_builder_hash
            or connector_builder_hash != catalog_builder_hash
            or execution.normalizer_version_hash != connector_normalizer_hash
            or len(execution.operations) != len(contract.operations)
        ):
            raise ProbeRunnerError("connector_execution_invalid")
        for evidence, operation in zip(execution.operations, contract.operations, strict=True):
            if (
                not isinstance(evidence, ProbeOperationEvidence)
                or not isinstance(evidence.operation_id, str)
                or not isinstance(evidence.approved_host, str)
                or not isinstance(evidence.http_status_class, str)
                or isinstance(evidence.attempt_count, bool)
                or not isinstance(evidence.attempt_count, int)
                or evidence.operation_id != operation.operation_id
                or evidence.approved_host != operation.approved_host
                or evidence.http_status_class != "2xx"
                or evidence.attempt_count != 1
                or not _is_sha256(evidence.response_sha256)
            ):
                raise ProbeRunnerError("connector_execution_invalid")
        if execution.response_sha256 != _derived_response_sha256(execution.operations):
            raise ProbeRunnerError("connector_execution_invalid")
        normalized_names: list[str] = []
        for field_entry in execution.normalized_fields:
            if (
                not isinstance(field_entry, tuple)
                or len(field_entry) != 2
                or not isinstance(field_entry[0], str)
                or not field_entry[0]
            ):
                raise ProbeRunnerError("connector_execution_invalid")
            field_name, field_value = field_entry
            if not _normalized_value_is_canonical(field_value) and not (
                field_name in contract.empty_tuple_normalized_fields and field_value == ()
            ):
                raise ProbeRunnerError("connector_execution_invalid")
            normalized_names.append(field_name)
        if (
            len(set(normalized_names)) != len(normalized_names)
            or frozenset(normalized_names) != frozenset(contract.required_normalized_fields)
            or len(normalized_names) != len(contract.required_normalized_fields)
        ):
            raise ProbeRunnerError("connector_execution_invalid")


def _derived_response_sha256(operations: tuple[ProbeOperationEvidence, ...]) -> str:
    if len(operations) == 1:
        return operations[0].response_sha256
    material = "|".join(operation.response_sha256 for operation in operations).encode()
    return hashlib.sha256(material).hexdigest()


def _is_sha256(value: object) -> bool:
    return isinstance(value, str) and _SHA256_RE.fullmatch(value) is not None


def _normalized_value_is_canonical(value: object) -> bool:
    """Accept only immutable, deterministic normalized representations."""

    value_type = type(value)
    if value_type is bool:
        return True
    if value_type is int:
        return True
    if value_type is float:
        return math.isfinite(cast(float, value))
    if value_type is str:
        text = cast(str, value)
        return bool(text) and text == text.strip()
    if value_type is tuple:
        items = cast(tuple[object, ...], value)
        return bool(items) and all(_normalized_value_is_canonical(item) for item in items)
    if value_type is PerplexityCitationEvidence:
        return _perplexity_citation_is_canonical(cast(PerplexityCitationEvidence, value))
    if value_type is SupportedSynthesisUnit:
        return _supported_synthesis_is_canonical(cast(SupportedSynthesisUnit, value))
    return False


def _perplexity_citation_is_canonical(value: PerplexityCitationEvidence) -> bool:
    provenance = value.provenance
    if type(provenance) is not PerplexityRetrievalProvenance:
        return False
    try:
        if (
            type(value.citation_id) is not str
            or type(value.canonical_url) is not str
            or type(value.canonical_url_sha256) is not str
            or type(provenance.provider_request_id) is not str
            or type(provenance.retrieved_at) is not datetime
            or type(provenance.response_sha256) is not str
            or provenance.provider_request_id != provenance.provider_request_id.strip()
            or provenance.retrieved_at.tzinfo is not UTC
        ):
            return False
        rebuilt_provenance = PerplexityRetrievalProvenance(
            provider_request_id=provenance.provider_request_id,
            retrieved_at=provenance.retrieved_at,
            response_sha256=provenance.response_sha256,
        )
        rebuilt = PerplexityCitationEvidence(
            citation_id=value.citation_id,
            canonical_url=value.canonical_url,
            canonical_url_sha256=value.canonical_url_sha256,
            provenance=rebuilt_provenance,
        )
    except (AttributeError, TypeError, ValueError):
        return False
    return rebuilt == value


def _supported_synthesis_is_canonical(value: SupportedSynthesisUnit) -> bool:
    try:
        if (
            type(value.text) is not str
            or type(value.text_sha256) is not str
            or not _normalized_value_is_canonical(value.citation_ids)
        ):
            return False
        rebuilt = SupportedSynthesisUnit(
            text=value.text,
            text_sha256=value.text_sha256,
            citation_ids=value.citation_ids,
        )
    except (AttributeError, TypeError, ValueError):
        return False
    return rebuilt == value
