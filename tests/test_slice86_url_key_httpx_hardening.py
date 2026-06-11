"""Slice86 Task 6 — URL-key + httpx log redaction hardening (RED-first).

Closes the two Slice85 follow-ups (plan §2.5/§3 G6, decision D-H):
  1. FRED/Finnhub/FMP embed the API key in the request URL, and their ``*FetchError`` messages
     interpolate ``str(last_error)`` — which for httpx status errors contains the full URL.
     A shared ``redact_secret_params`` helper now scrubs api_key/apikey/token values at
     FetchError construction, so those messages can never carry a credential.
  2. httpx logs ``HTTP Request: GET <full URL>`` at INFO when a handler is attached — a
     central, idempotent redaction filter on the ``httpx`` logger scrubs secret query values
     from every such record.

FINNHUB AUTH DECISION (primary-source verification, 2026-06-11): header auth could NOT be
verified from primary docs — finnhub.io's documentation pages are JS-rendered (unfetchable),
and Finnhub's OWN official Python client (Finnhub-Stock-API/finnhub-python, client.py)
transmits the key as the ``token`` QUERY PARAMETER (``session.params["token"] = api_key``)
with no auth header. Per the locked instruction, Finnhub therefore STAYS on query-param auth
and receives redaction-only hardening, like FRED/FMP.

Connector behavior/response parsing unchanged (regressions via the existing connector suites).
No conflict/optional-policy/VC changes, no DB, no real provider calls (MockTransport only),
no Slice87.
"""

from __future__ import annotations

import logging
from pathlib import Path

import httpx
import pytest

from idis.services.enrichment.models import (
    EnrichmentContext,
    EnrichmentQuery,
    EnrichmentRequest,
    EnrichmentStatus,
    EntityType,
)

_KEY = "sk-s86-hard-LEAK-0001"

_ENRICHMENT_DIR = Path("src/idis/services/enrichment")


def _failing_client() -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code=500, json={"error": "upstream"})

    return httpx.Client(transport=httpx.MockTransport(handler))


def _ok_client(payload: dict) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code=200, json=payload)

    return httpx.Client(transport=httpx.MockTransport(handler))


def _ctx() -> EnrichmentContext:
    return EnrichmentContext(
        timeout_seconds=2.0,
        max_retries=0,
        request_id="slice86-hardening",
        byol_credentials={"api_key": _KEY, "token": _KEY},
    )


def _request(ticker: str = "AAPL") -> EnrichmentRequest:
    return EnrichmentRequest(
        tenant_id="tenant-slice86",
        entity_type=EntityType.COMPANY,
        query=EnrichmentQuery(ticker=ticker, company_name="safe-public-company"),
    )


# --- the shared redaction helper ---


def test_redact_secret_params_helper() -> None:
    from idis.services.enrichment.redaction import redact_secret_params

    url = f"https://api.example/x?series_id=GDP&api_key={_KEY}&file_type=json"
    redacted = redact_secret_params(url)
    assert _KEY not in redacted
    assert "series_id=GDP" in redacted  # non-secret params untouched
    assert "file_type=json" in redacted
    assert "api_key=[redacted]" in redacted

    assert _KEY not in redact_secret_params(f"?apikey={_KEY}")
    assert _KEY not in redact_secret_params(f"&token={_KEY}&x=1")
    assert _KEY not in redact_secret_params(
        f"Server error '500' for url 'https://a/b?TOKEN={_KEY}'"
    )
    assert redact_secret_params("no secrets here") == "no secrets here"


# --- FetchError messages can never carry the key ---


def test_fetch_error_messages_redact_secret_params() -> None:
    from idis.services.enrichment.connectors.finnhub import FinnhubConnector, FinnhubFetchError
    from idis.services.enrichment.connectors.fmp import FmpConnector, FmpFetchError
    from idis.services.enrichment.connectors.fred import FredConnector, FredFetchError

    cases = (
        (
            FredConnector(http_client=_failing_client()),
            FredFetchError,
            f"https://api.stlouisfed.org/fred/series/observations?series_id=GDP&api_key={_KEY}",
        ),
        (
            FinnhubConnector(http_client=_failing_client()),
            FinnhubFetchError,
            f"https://finnhub.io/api/v1/quote?symbol=AAPL&token={_KEY}",
        ),
        (
            FmpConnector(http_client=_failing_client()),
            FmpFetchError,
            f"https://financialmodelingprep.com/api/v3/profile/AAPL?apikey={_KEY}",
        ),
    )
    for connector, error_type, url in cases:
        with pytest.raises(error_type) as exc_info:
            connector._make_request(url=url, ctx=_ctx())
        message = f"{exc_info.value!s}|{exc_info.value!r}"
        assert _KEY not in message, type(connector).__name__
        assert "[redacted]" in message  # the URL is kept for debugging, minus the secret


# --- central httpx request-log redaction ---


def test_httpx_request_log_is_redacted(caplog: pytest.LogCaptureFixture) -> None:
    from idis.services.enrichment.connectors.fred import FredConnector

    payload = {"observations": [{"date": "2026-01-01", "value": "1.0"}]}
    connector = FredConnector(http_client=_ok_client(payload))
    with caplog.at_level(logging.INFO, logger="httpx"):
        result = connector.fetch(_request(ticker="GDP"), _ctx())
    assert result.status == EnrichmentStatus.HIT  # behavior preserved
    httpx_messages = [
        record.getMessage() for record in caplog.records if record.name.startswith("httpx")
    ]
    assert httpx_messages  # httpx did log the request line
    for message in httpx_messages:
        assert _KEY not in message  # central filter scrubbed the URL secret


def test_httpx_redaction_filter_is_idempotent() -> None:
    from idis.services.enrichment.redaction import install_httpx_redaction_filter

    logger = logging.getLogger("httpx")
    install_httpx_redaction_filter()
    install_httpx_redaction_filter()
    redaction_filters = [
        f for f in logger.filters if type(f).__name__ == "SecretParamRedactionFilter"
    ]
    assert len(redaction_filters) == 1  # installed once, never stacked


# --- Finnhub decision pin: query-param auth retained, redaction-only ---


def test_finnhub_retains_query_param_auth_redaction_only() -> None:
    # Primary-source verification (see module docstring) could not confirm header auth;
    # Finnhub's own official client uses the token query parameter. Pin the decision.
    finnhub_source = (_ENRICHMENT_DIR / "connectors" / "finnhub.py").read_text(encoding="utf-8")
    assert "&token={api_key}" in finnhub_source  # query-param auth retained
    assert "X-Finnhub-Token" not in finnhub_source  # no unverified header switch
    assert "redact_secret_params" in finnhub_source  # redaction applied instead
