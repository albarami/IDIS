"""Request ID middleware for IDIS API.

Ensures every request has a unique request ID for tracing and audit purposes.
"""

import uuid
from collections.abc import Awaitable, Callable

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

REQUEST_ID_HEADER = "X-Request-Id"


class RequestIdMiddleware(BaseHTTPMiddleware):
    """Middleware that attaches a request ID to every request.

    Behavior:
    - If request has header X-Request-Id and it's a non-empty string => use it.
    - Else generate uuid4.
    - Attach to request.state.request_id and add response header X-Request-Id.
    """

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        """Process the request and attach request ID."""
        incoming_request_id = request.headers.get(REQUEST_ID_HEADER)

        if incoming_request_id and incoming_request_id.strip():
            request_id = incoming_request_id.strip()
        else:
            request_id = str(uuid.uuid4())

        request.state.request_id = request_id

        response: Response = await call_next(request)
        response.headers[REQUEST_ID_HEADER] = request_id

        return response
