"""Regression tests for request-scoped DB transaction finality."""

from __future__ import annotations

import asyncio
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


def test_document_content_stream_commits_lookup_before_first_body_without_buffering() -> None:
    """Only the read-only content route settles RLS lookup before streaming bytes."""
    events: list[str] = []
    conn = MagicMock()
    trans = MagicMock()

    async def streaming_inner(scope: object, receive: object, send: object) -> None:
        del scope, receive
        await send(  # type: ignore[operator]
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-type", b"application/octet-stream")],
            }
        )
        events.append("start-returned")
        await send(  # type: ignore[operator]
            {"type": "http.response.body", "body": b"first", "more_body": True}
        )
        events.append("first-body-returned")
        await send(  # type: ignore[operator]
            {"type": "http.response.body", "body": b"second", "more_body": False}
        )

    async def receive() -> dict[str, object]:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def outer_send(message: dict[str, object]) -> None:
        events.append(f"sent-{message['type']}")

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": "/v1/documents/44444444-4444-4444-4444-444444444444/content",
        "raw_path": b"/v1/documents/44444444-4444-4444-4444-444444444444/content",
        "query_string": b"",
        "headers": [],
        "client": ("127.0.0.1", 1234),
        "server": ("testserver", 80),
    }

    with (
        patch("idis.persistence.db.is_postgres_configured", return_value=True),
        patch("idis.api.middleware.db_tx._open_connection", return_value=(conn, trans)),
        patch(
            "idis.api.middleware.db_tx._commit",
            side_effect=lambda _trans: events.append("commit"),
        ),
        patch("idis.api.middleware.db_tx._close"),
    ):
        asyncio.run(DBTransactionMiddleware(streaming_inner)(scope, receive, outer_send))  # type: ignore[arg-type]

    assert events == [
        "commit",
        "sent-http.response.start",
        "start-returned",
        "sent-http.response.body",
        "first-body-returned",
        "sent-http.response.body",
    ]


def test_document_content_stream_commit_failure_sends_no_false_bytes() -> None:
    """A failed lookup commit replaces the stream before any object byte is sent."""

    async def streaming_inner(scope: object, receive: object, send: object) -> None:
        del scope, receive
        await send(  # type: ignore[operator]
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-type", b"application/octet-stream")],
            }
        )
        await send(  # type: ignore[operator]
            {"type": "http.response.body", "body": b"private-object-byte", "more_body": False}
        )

    conn = MagicMock()
    trans = MagicMock()
    app = DBTransactionMiddleware(streaming_inner)
    with (
        patch("idis.persistence.db.is_postgres_configured", return_value=True),
        patch("idis.api.middleware.db_tx._open_connection", return_value=(conn, trans)),
        patch("idis.api.middleware.db_tx._commit", side_effect=RuntimeError("commit failed")),
    ):
        response = TestClient(app, raise_server_exceptions=False).get(
            "/v1/documents/44444444-4444-4444-4444-444444444444/content"
        )

    assert response.status_code == 500
    assert response.json()["code"] == "DATABASE_COMMIT_FAILED"
    assert b"private-object-byte" not in response.content
