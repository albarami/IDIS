"""RED-first tests for the generic, hermetic provider probe runner."""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import fields, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from idis.services.enrichment.connectors.epo_open_patent import (
    EpoOpenPatentConnector,
)
from idis.services.enrichment.connectors.semantic_scholar import SemanticScholarConnector
from idis.services.enrichment.task9.bounded_probe_transport import (
    BoundedProbeTransport,
    ProbeTransportFailure,
    ProbeTransportFailureReason,
)
from idis.services.enrichment.task9.probe_runner import (
    ProbeConnectorError,
    ProbeConnectorExecution,
    ProbeOperationEvidence,
    ProbeRunner,
    ProbeRunnerError,
    ProbeRunRequest,
    ProbeSchemaResult,
    ProviderProbeConnector,
    ProviderProbeReceiptIntent,
)
from idis.services.enrichment.task9.provider_catalog import PROVIDER_CATALOG

_FIXTURES = Path(__file__).with_name("fixtures")
_EPO_OAUTH = (_FIXTURES / "epo_oauth.json").read_bytes()
_EPO_SEARCH = (_FIXTURES / "epo_search.xml").read_bytes()
_SEMANTIC = (_FIXTURES / "semantic_scholar.json").read_bytes()
_SEMANTIC_CATALOG_BUILDER_HASH = PROVIDER_CATALOG[
    "semantic_scholar"
].runtime_lineage.request_builder_version_hash


class _Clock:
    def __init__(self) -> None:
        self._value = datetime(2026, 7, 21, 12, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        current = self._value
        self._value += timedelta(milliseconds=10)
        return current


class _HostileString(str):
    def strip(self, chars: str | None = None) -> str:
        raise RuntimeError("credential-marker-never-report")


class _TupleSubclass(tuple[object, ...]):
    pass


class _IntSubclass(int):
    pass


def _handler(
    calls: list[httpx.Request],
) -> Callable[[httpx.Request], httpx.Response]:
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.url.path.endswith("/auth/accesstoken"):
            return httpx.Response(
                200,
                content=_EPO_OAUTH,
                headers={"content-type": "application/json"},
            )
        if request.url.host == "ops.epo.org":
            return httpx.Response(
                200,
                content=_EPO_SEARCH,
                headers={"content-type": "application/exchange+xml"},
            )
        return httpx.Response(
            200,
            content=_SEMANTIC,
            headers={"content-type": "application/json"},
        )

    return handler


def _request(provider_id: str) -> ProbeRunRequest:
    return ProbeRunRequest(
        tenant_id="tenant-probe-hermetic",
        provider_id=provider_id,
        safe_request_id=f"safe-request-{provider_id}",
        rights_approval_id=f"rights-{provider_id}",
        credential_version_id=f"credential-version-{provider_id}",
    )


def test_runner_uses_real_connectors_and_emits_exact_safe_ineligible_receipts() -> None:
    calls: list[httpx.Request] = []
    with httpx.Client(transport=httpx.MockTransport(_handler(calls))) as client:
        transport = BoundedProbeTransport(client)
        epo = EpoOpenPatentConnector(http_client=client)
        semantic = SemanticScholarConnector(http_client=client)
        assert isinstance(epo, ProviderProbeConnector)
        assert isinstance(semantic, ProviderProbeConnector)
        runner = ProbeRunner(
            transport=transport,
            connectors={
                "epo_open_patent": epo,
                "semantic_scholar": semantic,
            },
            now=_Clock(),
        )
        epo_result = runner.run_probe(
            _request("epo_open_patent"),
            credentials={
                "consumer_key": "hermetic-key",
                "consumer_secret": "hermetic-secret",
            },
        )
        semantic_result = runner.run_probe(
            _request("semantic_scholar"),
            credentials={"api_key": "hermetic-api-key"},
        )

    assert {field.name for field in fields(ProviderProbeReceiptIntent)} == {
        "tenant_id",
        "provider_id",
        "contract_version",
        "fixture_id",
        "request_builder_version_hash",
        "host_operations",
        "safe_request_id",
        "started_at",
        "completed_at",
        "http_status_class",
        "schema_result",
        "rights_approval_id",
        "credential_version_id",
        "response_sha256",
        "attempt_count",
        "timeout_seconds",
    }
    for observation in (epo_result, semantic_result):
        receipt = observation.receipt
        assert receipt.contract_version == "provider-probes-v1"
        assert receipt.fixture_id
        assert len(receipt.request_builder_version_hash) == 64
        assert receipt.started_at < receipt.completed_at
        assert receipt.http_status_class == "2xx"
        assert receipt.schema_result == "HIT"
        assert len(receipt.response_sha256) == 64
        assert receipt.attempt_count == 1
        assert receipt.timeout_seconds == 15
        assert observation.normalized_fields
        assert len(observation.normalizer_version_hash) == 64
        assert observation.execution_mode == "HERMETIC_TEST"
        assert observation.acceptance_eligible is False
        assert not hasattr(observation, "ready")
    assert epo_result.receipt.host_operations == (
        ("ops.epo.org", "oauth"),
        ("ops.epo.org", "search"),
    )
    assert semantic_result.receipt.host_operations == (("api.semanticscholar.org", "probe"),)
    assert len(calls) == 3


@pytest.mark.parametrize("provider_id", ["unknown-provider", "perplexity"])
def test_unknown_or_unconnected_provider_cannot_produce_a_ready_observation(
    provider_id: str,
) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        runner = ProbeRunner(
            transport=BoundedProbeTransport(client),
            connectors={
                "semantic_scholar": SemanticScholarConnector(http_client=client),
            },
            now=_Clock(),
        )
        with pytest.raises(ProbeRunnerError) as exc_info:
            runner.run_probe(_request(provider_id), credentials={"api_key": "unused"})

    expected_reason = (
        "provider_unknown" if provider_id == "unknown-provider" else "provider_not_connected"
    )
    assert exc_info.value.reason == expected_reason
    assert calls == 0


def test_transport_failure_is_one_attempt_and_has_no_receipt_or_secret_leak() -> None:
    calls = 0
    marker = "credential-marker-never-report"

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(503, content=marker.encode())

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        runner = ProbeRunner(
            transport=BoundedProbeTransport(client),
            connectors={
                "semantic_scholar": SemanticScholarConnector(http_client=client),
            },
            now=_Clock(),
        )
        with pytest.raises(ProbeConnectorError) as exc_info:
            runner.run_probe(
                _request("semantic_scholar"),
                credentials={"api_key": marker},
            )

    assert calls == 1
    assert marker not in str(exc_info.value)


def test_connector_registration_mismatch_fails_before_transport() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={})

    with (
        httpx.Client(transport=httpx.MockTransport(handler)) as client,
        pytest.raises(ProbeRunnerError) as exc_info,
    ):
        ProbeRunner(
            transport=BoundedProbeTransport(client),
            connectors={
                "epo_open_patent": SemanticScholarConnector(http_client=client),
            },
            now=_Clock(),
        )

    assert exc_info.value.reason == "connector_registration_mismatch"
    assert calls == 0


class _FakeSemanticConnector:
    provider_id = "semantic_scholar"
    request_builder_version_hash = _SEMANTIC_CATALOG_BUILDER_HASH
    normalizer_version_hash = "2" * 64

    def __init__(self, execution: ProbeConnectorExecution) -> None:
        self._execution = execution

    def run_probe(self, **kwargs: object) -> ProbeConnectorExecution:
        return self._execution


class _AdvancingSemanticConnector(_FakeSemanticConnector):
    def __init__(
        self,
        execution: ProbeConnectorExecution,
        *,
        advance: Callable[[float], None],
    ) -> None:
        super().__init__(execution)
        self._advance = advance

    def run_probe(self, **kwargs: object) -> ProbeConnectorExecution:
        self._advance(16.0)
        return self._execution


class _RaisingSemanticConnector(_FakeSemanticConnector):
    def __init__(self, error: Exception) -> None:
        self._error = error

    def run_probe(self, **kwargs: object) -> ProbeConnectorExecution:
        raise self._error


class _MutableMonotonic:
    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


def _fake_semantic_execution(
    *,
    normalized_fields: tuple[tuple[str, object], ...],
    operation_hash: str = "b" * 64,
    response_hash: str = "b" * 64,
    request_builder_version_hash: str = _SEMANTIC_CATALOG_BUILDER_HASH,
) -> ProbeConnectorExecution:
    return ProbeConnectorExecution(
        provider_id="semantic_scholar",
        status=ProbeSchemaResult.HIT,
        normalized_fields=normalized_fields,
        operations=(
            ProbeOperationEvidence(
                operation_id="probe",
                approved_host="api.semanticscholar.org",
                http_status_class="2xx",
                response_sha256=operation_hash,
                attempt_count=1,
            ),
        ),
        response_sha256=response_hash,
        request_builder_version_hash=request_builder_version_hash,
        normalizer_version_hash="2" * 64,
    )


def _valid_semantic_fields() -> tuple[tuple[str, object], ...]:
    return (
        ("attribution_required", True),
        ("attribution_text", "Data from Semantic Scholar"),
        ("authors", (("1", "Public Author"),)),
        ("external_ids", (("CorpusId", 215416146),)),
        ("paper_id", "0123456789abcdef0123456789abcdef01234567"),
        ("selector_type", "paper"),
        ("title", "Public scholarly paper"),
        ("year", 2024),
    )


def test_runner_rejects_self_consistent_builder_hash_not_pinned_by_catalog() -> None:
    unreviewed_builder_hash = "1" * 64
    execution = _fake_semantic_execution(
        normalized_fields=_valid_semantic_fields(),
        request_builder_version_hash=unreviewed_builder_hash,
    )
    connector = _FakeSemanticConnector(execution)
    connector.request_builder_version_hash = unreviewed_builder_hash

    with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(500))) as client:
        runner = ProbeRunner(
            transport=BoundedProbeTransport(client),
            connectors={"semantic_scholar": connector},
            now=_Clock(),
        )
        with pytest.raises(ProbeRunnerError) as exc_info:
            runner.run_probe(_request("semantic_scholar"), credentials={"api_key": "unused"})

    assert exc_info.value.reason == "connector_execution_invalid"


@pytest.mark.parametrize(
    "normalized_fields",
    [
        (("junk", "truthy"),),
        _valid_semantic_fields() + (("title", "duplicate"),),
        tuple((name, "" if name == "title" else value) for name, value in _valid_semantic_fields()),
    ],
)
def test_runner_requires_exact_unique_non_empty_normalized_field_closure(
    normalized_fields: tuple[tuple[str, object], ...],
) -> None:
    execution = _fake_semantic_execution(normalized_fields=normalized_fields)
    with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(500))) as client:
        runner = ProbeRunner(
            transport=BoundedProbeTransport(client),
            connectors={"semantic_scholar": _FakeSemanticConnector(execution)},
            now=_Clock(),
        )
        with pytest.raises(ProbeRunnerError) as exc_info:
            runner.run_probe(_request("semantic_scholar"), credentials={"api_key": "unused"})

    assert exc_info.value.reason == "connector_execution_invalid"


@pytest.mark.parametrize(
    "noncanonical_value",
    [
        pytest.param(object(), id="opaque-object"),
        pytest.param(math.nan, id="nan"),
        pytest.param(math.inf, id="positive-infinity"),
        pytest.param(b"mutable-byte-buffer", id="bytes"),
        pytest.param(["mutable-sequence"], id="list"),
        pytest.param({"mutable": "mapping"}, id="mapping"),
        pytest.param({"unordered-member"}, id="set"),
        pytest.param(("valid-prefix", object()), id="nested-opaque-object"),
        pytest.param(_HostileString("subclass"), id="hostile-string-subclass"),
        pytest.param(_TupleSubclass(("subclass",)), id="tuple-subclass"),
        pytest.param(_IntSubclass(2024), id="integer-subclass"),
    ],
)
def test_runner_rejects_noncanonical_normalized_values(
    noncanonical_value: object,
) -> None:
    normalized_fields = tuple(
        (name, noncanonical_value if name == "title" else value)
        for name, value in _valid_semantic_fields()
    )
    execution = _fake_semantic_execution(normalized_fields=normalized_fields)

    with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(500))) as client:
        runner = ProbeRunner(
            transport=BoundedProbeTransport(client),
            connectors={"semantic_scholar": _FakeSemanticConnector(execution)},
            now=_Clock(),
        )
        with pytest.raises(ProbeRunnerError) as exc_info:
            runner.run_probe(_request("semantic_scholar"), credentials={"api_key": "unused"})

    assert exc_info.value.reason == "connector_execution_invalid"


def test_runner_independently_derives_top_response_hash_from_ordered_operations() -> None:
    execution = _fake_semantic_execution(
        normalized_fields=_valid_semantic_fields(),
        operation_hash="b" * 64,
        response_hash="a" * 64,
    )
    with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(500))) as client:
        runner = ProbeRunner(
            transport=BoundedProbeTransport(client),
            connectors={"semantic_scholar": _FakeSemanticConnector(execution)},
            now=_Clock(),
        )
        with pytest.raises(ProbeRunnerError) as exc_info:
            runner.run_probe(_request("semantic_scholar"), credentials={"api_key": "unused"})

    assert exc_info.value.reason == "connector_execution_invalid"


def test_runner_type_checks_request_fields_before_string_operations() -> None:
    execution = _fake_semantic_execution(normalized_fields=_valid_semantic_fields())
    invalid_request = ProbeRunRequest(
        tenant_id=123,  # type: ignore[arg-type] -- adversarial boundary input
        provider_id="semantic_scholar",
        safe_request_id="safe-request",
        rights_approval_id="rights",
        credential_version_id="credential",
    )
    with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(500))) as client:
        runner = ProbeRunner(
            transport=BoundedProbeTransport(client),
            connectors={"semantic_scholar": _FakeSemanticConnector(execution)},
            now=_Clock(),
        )
        with pytest.raises(ProbeRunnerError) as exc_info:
            runner.run_probe(invalid_request, credentials={"api_key": "unused"})

    assert exc_info.value.reason == "probe_request_invalid"


def test_runner_type_checks_connector_execution_before_attribute_access() -> None:
    malformed_connector = _FakeSemanticConnector(object())  # type: ignore[arg-type]
    with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(500))) as client:
        runner = ProbeRunner(
            transport=BoundedProbeTransport(client),
            connectors={"semantic_scholar": malformed_connector},
            now=_Clock(),
        )
        with pytest.raises(ProbeRunnerError) as exc_info:
            runner.run_probe(_request("semantic_scholar"), credentials={"api_key": "unused"})

    assert exc_info.value.reason == "connector_execution_invalid"


def test_runner_rejects_adapter_that_returns_after_shared_deadline() -> None:
    monotonic = _MutableMonotonic()
    execution = _fake_semantic_execution(normalized_fields=_valid_semantic_fields())
    connector = _AdvancingSemanticConnector(execution, advance=monotonic.advance)

    with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(500))) as client:
        runner = ProbeRunner(
            transport=BoundedProbeTransport(client, monotonic=monotonic),
            connectors={"semantic_scholar": connector},
            now=_Clock(),
        )
        with pytest.raises(ProbeTransportFailure) as exc_info:
            runner.run_probe(_request("semantic_scholar"), credentials={"api_key": "unused"})

    assert exc_info.value.reason is ProbeTransportFailureReason.TIMEOUT


@pytest.mark.parametrize("malformed_part", ["top_hash", "operation_hash", "operations", "fields"])
def test_runner_malformed_adapter_values_fail_with_stable_error(
    malformed_part: str,
) -> None:
    execution = _fake_semantic_execution(normalized_fields=_valid_semantic_fields())
    if malformed_part == "top_hash":
        execution = replace(execution, response_sha256=123)  # type: ignore[arg-type]
    elif malformed_part == "operation_hash":
        evidence = replace(
            execution.operations[0],
            response_sha256=123,  # type: ignore[arg-type]
        )
        execution = replace(execution, operations=(evidence,))
    elif malformed_part == "operations":
        execution = replace(execution, operations=None)  # type: ignore[arg-type]
    else:
        execution = replace(execution, normalized_fields=None)  # type: ignore[arg-type]

    with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(500))) as client:
        runner = ProbeRunner(
            transport=BoundedProbeTransport(client),
            connectors={"semantic_scholar": _FakeSemanticConnector(execution)},
            now=_Clock(),
        )
        with pytest.raises(ProbeRunnerError) as exc_info:
            runner.run_probe(_request("semantic_scholar"), credentials={"api_key": "unused"})

    assert exc_info.value.reason == "connector_execution_invalid"


def test_runner_type_checks_clock_value_before_datetime_attributes() -> None:
    execution = _fake_semantic_execution(normalized_fields=_valid_semantic_fields())
    with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(500))) as client:
        runner = ProbeRunner(
            transport=BoundedProbeTransport(client),
            connectors={"semantic_scholar": _FakeSemanticConnector(execution)},
            now=lambda: "not-a-datetime",  # type: ignore[arg-type,return-value]
        )
        with pytest.raises(ProbeRunnerError) as exc_info:
            runner.run_probe(_request("semantic_scholar"), credentials={"api_key": "unused"})

    assert exc_info.value.reason == "probe_time_invalid"


def test_runner_wraps_unexpected_adapter_errors_without_echoing_details() -> None:
    marker = "credential-marker-never-report"
    connector = _RaisingSemanticConnector(RuntimeError(marker))
    with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(500))) as client:
        runner = ProbeRunner(
            transport=BoundedProbeTransport(client),
            connectors={"semantic_scholar": connector},
            now=_Clock(),
        )
        with pytest.raises(ProbeRunnerError) as exc_info:
            runner.run_probe(_request("semantic_scholar"), credentials={"api_key": marker})

    assert exc_info.value.code == "ENRICHMENT_PREFLIGHT_BLOCKED"
    assert exc_info.value.reason == "connector_execution_failed"
    assert marker not in str(exc_info.value)


@pytest.mark.parametrize(
    "error",
    [
        ProbeConnectorError("known_connector_failure"),
        ProbeTransportFailure(ProbeTransportFailureReason.TIMEOUT),
    ],
)
def test_runner_preserves_known_connector_and_transport_failures(error: Exception) -> None:
    connector = _RaisingSemanticConnector(error)
    with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(500))) as client:
        runner = ProbeRunner(
            transport=BoundedProbeTransport(client),
            connectors={"semantic_scholar": connector},
            now=_Clock(),
        )
        with pytest.raises(type(error)) as exc_info:
            runner.run_probe(_request("semantic_scholar"), credentials={"api_key": "unused"})

    assert exc_info.value is error
