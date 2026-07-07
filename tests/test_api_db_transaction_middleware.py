"""Regression tests for request-scoped DB transaction finality."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from idis.api.middleware.db_tx import DBTransactionMiddleware


async def _successful_inner_app(scope: object, receive: object, send: object) -> None:
    """ASGI app that attempts to return a successful response."""
    response = JSONResponse({"ok": True}, status_code=200)
    await response(scope, receive, send)  # type: ignore[arg-type]


def test_commit_failure_does_not_send_false_success() -> None:
    """A failed DB commit must be returned as an error, never as a false 2xx."""
    conn = MagicMock()
    trans = MagicMock()
    app = DBTransactionMiddleware(_successful_inner_app)

    with (
        patch("idis.persistence.db.is_postgres_configured", return_value=True),
        patch("idis.api.middleware.db_tx._open_connection", return_value=(conn, trans)),
        patch("idis.api.middleware.db_tx._commit", side_effect=RuntimeError("commit failed")),
    ):
        client = TestClient(app, raise_server_exceptions=False)
        response = client.get("/v1/test")

    assert response.status_code == 500
    body = response.json()
    assert body["code"] == "DATABASE_COMMIT_FAILED"
    assert body["message"] == "Database transaction commit failed"


def test_strict_readiness_is_exempt_from_db_transaction() -> None:
    """Config-only reviewer readiness GET bypasses the DB transaction (works when DB is down)."""
    app = DBTransactionMiddleware(_successful_inner_app)

    with (
        patch("idis.persistence.db.is_postgres_configured", return_value=True),
        patch("idis.api.middleware.db_tx._open_connection") as open_conn,
    ):
        client = TestClient(app, raise_server_exceptions=False)
        response = client.get("/v1/strict-readiness")

    assert response.status_code == 200
    open_conn.assert_not_called()


def test_non_exempt_v1_path_still_opens_db_transaction() -> None:
    """The exemption is narrow: other /v1 paths still get a request-scoped transaction."""
    conn = MagicMock()
    trans = MagicMock()
    app = DBTransactionMiddleware(_successful_inner_app)

    with (
        patch("idis.persistence.db.is_postgres_configured", return_value=True),
        patch(
            "idis.api.middleware.db_tx._open_connection", return_value=(conn, trans)
        ) as open_conn,
        patch("idis.api.middleware.db_tx._commit"),
        patch("idis.api.middleware.db_tx._close"),
    ):
        client = TestClient(app, raise_server_exceptions=False)
        response = client.get("/v1/deals")

    assert response.status_code == 200
    open_conn.assert_called_once()
