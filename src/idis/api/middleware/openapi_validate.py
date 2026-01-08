"""OpenAPI request-body validation middleware for IDIS API.

Implements fail-closed validation for /v1 requests:
1. Auth precedence: 401 before any JSON/schema validation
2. JSON parsing: invalid JSON => 400 INVALID_JSON
3. Schema validation: mismatch => 422 INVALID_REQUEST

Security: Tenant isolation enforced via auth-first for all /v1 paths.

Audit support (Phase 2.3):
- Exposes operation_id, path_template, and body_sha256 on request.state
  for downstream audit middleware consumption.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

from idis.api.auth import authenticate_request
from idis.api.error_model import make_error_response_no_request
from idis.api.errors import IdisHttpError
from idis.api.openapi_loader import load_openapi_spec

logger = logging.getLogger(__name__)

METHODS_WITH_JSON_BODY = {"POST", "PUT", "PATCH", "DELETE"}
ALL_HTTP_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"}
JSON_CONTENT_TYPES = {"application/json"}
MAX_REF_DEPTH = 50


class OperationIndex:
    """In-memory index of OpenAPI operations.

    Built once at startup for:
    - Request body validation (POST/PUT/PATCH/DELETE with JSON schemas)
    - Operation ID exposure for audit and RBAC middleware (all methods)
    """

    def __init__(self, spec: dict[str, Any]) -> None:
        self._spec = spec
        self._operations: list[
            tuple[re.Pattern[str], str, str, str | None, dict[str, Any] | None]
        ] = []
        self._build_index()

    def _build_index(self) -> None:
        """Build the operation index from the OpenAPI spec.

        Index entries: (path_regex, method, original_path, operation_id, dereferenced_schema)
        Sorted deterministically: fewer path params first, then longer template, then lexical.
        """
        paths = self._spec.get("paths", {})
        if not isinstance(paths, dict):
            return

        entries: list[
            tuple[int, int, str, re.Pattern[str], str, str | None, dict[str, Any] | None]
        ] = []

        for path_template, path_item in paths.items():
            if not isinstance(path_item, dict):
                continue

            for method in ALL_HTTP_METHODS:
                method_lower = method.lower()
                if method_lower not in path_item:
                    continue

                operation = path_item[method_lower]
                if not isinstance(operation, dict):
                    continue

                schema = None
                if method in METHODS_WITH_JSON_BODY:
                    schema = self._extract_json_request_schema(operation)
                operation_id = operation.get("operationId")

                path_regex = self._compile_path_regex(path_template)
                param_count = path_template.count("{")

                entries.append(
                    (
                        param_count,
                        -len(path_template),
                        path_template,
                        path_regex,
                        method,
                        operation_id,
                        schema,
                    )
                )

        entries.sort(key=lambda x: (x[0], x[1], x[2]))

        for entry in entries:
            _, _, path_template, path_regex, method, operation_id, schema = entry
            self._operations.append((path_regex, method, path_template, operation_id, schema))

    def _compile_path_regex(self, path_template: str) -> re.Pattern[str]:
        """Convert OpenAPI path template to regex.

        E.g., /v1/deals/{dealId} => ^/v1/deals/[^/]+$
        """
        escaped = re.escape(path_template)
        pattern = re.sub(r"\\{[^}]+\\}", r"[^/]+", escaped)
        return re.compile(f"^{pattern}$")

    def _extract_json_request_schema(self, operation: dict[str, Any]) -> dict[str, Any] | None:
        """Extract and dereference the application/json request body schema."""
        request_body = operation.get("requestBody")
        if not isinstance(request_body, dict):
            return None

        content = request_body.get("content")
        if not isinstance(content, dict):
            return None

        for content_type in JSON_CONTENT_TYPES:
            if content_type in content:
                media_type = content[content_type]
                if isinstance(media_type, dict) and "schema" in media_type:
                    return self._dereference_schema(media_type["schema"])

        for content_type, media_type in content.items():
            is_json_type = "+json" in content_type or content_type == "application/json"
            has_schema = isinstance(media_type, dict) and "schema" in media_type
            if is_json_type and has_schema:
                return self._dereference_schema(media_type["schema"])

        return None

    def _dereference_schema(
        self, schema: Any, depth: int = 0, visited: set[str] | None = None
    ) -> dict[str, Any] | None:
        """Dereference $ref within the spec with cycle protection.

        Returns None if dereferencing fails (fail closed).
        """
        if visited is None:
            visited = set()

        if depth > MAX_REF_DEPTH:
            return None

        if not isinstance(schema, dict):
            return None

        if "$ref" in schema:
            ref = schema["$ref"]
            if not isinstance(ref, str):
                return None

            if ref in visited:
                return None
            visited.add(ref)

            resolved = self._resolve_ref(ref)
            if resolved is None:
                return None

            return self._dereference_schema(resolved, depth + 1, visited)

        result: dict[str, Any] = {}
        for key, value in schema.items():
            if key == "properties" and isinstance(value, dict):
                result[key] = {}
                for prop_name, prop_schema in value.items():
                    deref = self._dereference_schema(prop_schema, depth + 1, visited.copy())
                    result[key][prop_name] = deref if deref is not None else prop_schema
            elif key == "items" and isinstance(value, dict):
                deref = self._dereference_schema(value, depth + 1, visited.copy())
                result[key] = deref if deref is not None else value
            elif key in ("allOf", "anyOf", "oneOf") and isinstance(value, list):
                result[key] = []
                for item in value:
                    deref = self._dereference_schema(item, depth + 1, visited.copy())
                    result[key].append(deref if deref is not None else item)
            elif key == "additionalProperties" and isinstance(value, dict):
                deref = self._dereference_schema(value, depth + 1, visited.copy())
                result[key] = deref if deref is not None else value
            else:
                result[key] = value

        return result

    def _resolve_ref(self, ref: str) -> dict[str, Any] | None:
        """Resolve a JSON reference within the spec (local refs only)."""
        if not ref.startswith("#/"):
            return None

        parts = ref[2:].split("/")
        current: Any = self._spec

        for part in parts:
            decoded = part.replace("~1", "/").replace("~0", "~")
            if isinstance(current, dict):
                current = current.get(decoded)
            else:
                return None
            if current is None:
                return None

        return current if isinstance(current, dict) else None

    def match(self, path: str, method: str) -> tuple[str | None, str | None, dict[str, Any] | None]:
        """Match request path and method to an operation.

        Returns:
            (original_path_template, operation_id, schema) if matched,
            else (None, None, None).
            schema may be None if the operation has no JSON request body.
        """
        method_upper = method.upper()

        for path_regex, op_method, path_template, operation_id, schema in self._operations:
            if op_method == method_upper and path_regex.match(path):
                return (path_template, operation_id, schema)

        return (None, None, None)


def _is_json_content_type(content_type: str | None) -> bool:
    """Check if content type indicates JSON."""
    if not content_type:
        return False
    ct_lower = content_type.lower().split(";")[0].strip()
    return ct_lower == "application/json" or ct_lower.endswith("+json")


def _validate_json_schema(
    instance: Any, schema: dict[str, Any], path: str = ""
) -> tuple[bool, str | None, str | None]:
    """Validate JSON instance against schema.

    Minimal schema validation for OpenAPI 3.0 subset.
    Returns (is_valid, error_path, error_message).

    Deterministic: returns first error found in stable order.
    """
    if not isinstance(schema, dict):
        return (True, None, None)

    schema_type = schema.get("type")

    if schema_type == "object":
        if not isinstance(instance, dict):
            return (False, path or "/", f"Expected object, got {type(instance).__name__}")

        required_fields = schema.get("required", [])
        if isinstance(required_fields, list):
            for field in sorted(required_fields):
                if field not in instance:
                    field_path = f"{path}/{field}" if path else f"/{field}"
                    return (False, field_path, f"Missing required field: {field}")

        properties = schema.get("properties", {})
        if isinstance(properties, dict):
            for prop_name in sorted(properties.keys()):
                if prop_name in instance:
                    prop_schema = properties[prop_name]
                    prop_path = f"{path}/{prop_name}" if path else f"/{prop_name}"
                    valid, err_path, err_msg = _validate_json_schema(
                        instance[prop_name], prop_schema, prop_path
                    )
                    if not valid:
                        return (False, err_path, err_msg)

    elif schema_type == "array":
        if not isinstance(instance, list):
            return (False, path or "/", f"Expected array, got {type(instance).__name__}")

        items_schema = schema.get("items")
        if isinstance(items_schema, dict):
            for idx, item in enumerate(instance):
                item_path = f"{path}/{idx}"
                valid, err_path, err_msg = _validate_json_schema(item, items_schema, item_path)
                if not valid:
                    return (False, err_path, err_msg)

    elif schema_type == "string":
        if not isinstance(instance, str):
            return (False, path or "/", f"Expected string, got {type(instance).__name__}")

        enum_values = schema.get("enum")
        if isinstance(enum_values, list) and instance not in enum_values:
            return (False, path or "/", f"Value must be one of: {enum_values}")

    elif schema_type == "integer":
        if not isinstance(instance, int) or isinstance(instance, bool):
            return (False, path or "/", f"Expected integer, got {type(instance).__name__}")

    elif schema_type == "number":
        if not isinstance(instance, (int, float)) or isinstance(instance, bool):
            return (False, path or "/", f"Expected number, got {type(instance).__name__}")

    elif schema_type == "boolean":
        if not isinstance(instance, bool):
            return (False, path or "/", f"Expected boolean, got {type(instance).__name__}")

    return (True, None, None)


def _build_error_response(
    status_code: int,
    code: str,
    message: str,
    request_id: str | None,
    details: dict[str, Any] | None = None,
) -> JSONResponse:
    """Build a structured error JSON response using shared error model."""
    return make_error_response_no_request(
        code=code,
        message=message,
        http_status=status_code,
        request_id=request_id,
        details=details,
    )


class OpenAPIValidationMiddleware(BaseHTTPMiddleware):
    """Middleware for OpenAPI request validation on /v1 paths.

    Behavior:
    1. For /v1 paths: authenticate first (401 if unauthorized).
    2. Match request against OpenAPI spec operations.
    3. If operation has JSON request body schema:
       - Validate Content-Type (415 if not JSON when required)
       - Parse JSON body (400 if invalid JSON)
       - Validate against schema (422 if schema mismatch)
    4. Pass through to next handler if all validations pass.

    Audit support (Phase 2.3):
    - Sets request.state.openapi_operation_id when matched
    - Sets request.state.openapi_path_template when matched
    - Sets request.state.request_body_sha256 when body is read (even for invalid JSON)
    """

    def __init__(self, app: ASGIApp, spec: dict[str, Any] | None = None) -> None:
        super().__init__(app)
        if spec is None:
            spec = load_openapi_spec()
        self._index = OperationIndex(spec)

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        """Process request with validation."""
        request_id: str | None = getattr(request.state, "request_id", None)
        path = request.url.path
        method = request.method

        if not path.startswith("/v1"):
            return await call_next(request)

        try:
            tenant_ctx = authenticate_request(request)
            request.state.tenant_context = tenant_ctx
        except IdisHttpError as auth_err:
            return _build_error_response(
                auth_err.status_code, auth_err.code, auth_err.message, request_id
            )

        db_conn = getattr(request.state, "db_conn", None)
        if db_conn is not None:
            try:
                from idis.persistence.db import set_tenant_local

                set_tenant_local(db_conn, tenant_ctx.tenant_id)
            except Exception as e:
                logger.error(
                    "Failed to set tenant context on DB connection: %s",
                    e,
                    extra={"request_id": request_id},
                )
                return _build_error_response(
                    500,
                    "DATABASE_TENANT_CONTEXT_FAILED",
                    "Failed to set database tenant context",
                    request_id,
                )

        matched_path, operation_id, schema = self._index.match(path, method)

        if matched_path is not None:
            request.state.openapi_path_template = matched_path
        if operation_id is not None:
            request.state.openapi_operation_id = operation_id

        if schema is None:
            return await call_next(request)

        content_type = request.headers.get("content-type")
        if not _is_json_content_type(content_type):
            return _build_error_response(
                415,
                "INVALID_CONTENT_TYPE",
                "Content-Type must be application/json",
                request_id,
            )

        try:
            body_bytes = await request.body()
        except Exception:
            return _build_error_response(
                400, "INVALID_JSON", "Failed to read request body", request_id
            )

        if body_bytes:
            body_hash = hashlib.sha256(body_bytes).hexdigest()
            request.state.request_body_sha256 = f"sha256:{body_hash}"

        if not body_bytes:
            parsed_body: Any = None
        else:
            try:
                parsed_body = json.loads(body_bytes.decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                return _build_error_response(
                    400, "INVALID_JSON", "Request body is not valid JSON", request_id
                )

        try:
            is_valid, error_path, error_message = _validate_json_schema(parsed_body, schema)
        except Exception:
            return _build_error_response(
                422,
                "INVALID_REQUEST",
                "Schema validation failed due to unexpected structure",
                request_id,
            )

        if not is_valid:
            details = {"path": error_path, "message": error_message}
            return _build_error_response(
                422, "INVALID_REQUEST", "Request body does not match schema", request_id, details
            )

        return await call_next(request)
