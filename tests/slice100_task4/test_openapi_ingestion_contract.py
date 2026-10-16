"""Task 4 RED contract for terminal upload and durable-span OpenAPI surfaces."""

from __future__ import annotations

from pathlib import Path

import yaml

from idis.api.routes.documents import SpanResponse

_SPEC = Path(__file__).resolve().parents[2] / "openapi" / "IDIS_OpenAPI_v6_3.yaml"


def _static_spec() -> dict[str, object]:
    return yaml.safe_load(_SPEC.read_text(encoding="utf-8"))


def test_upload_contract_declares_terminal_422_error_envelope() -> None:
    spec = _static_spec()
    operation = spec["paths"]["/v1/deals/{dealId}/documents/upload"]["post"]

    response = operation["responses"]["422"]
    assert response["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/Error"
    }


def test_upload_contract_declares_source_tree_binding_and_explicit_terminal_disposition() -> None:
    spec = _static_spec()
    operation = spec["paths"]["/v1/deals/{dealId}/documents/upload"]["post"]
    parameters = {
        item.get("name"): item
        for item in operation["parameters"]
        if isinstance(item, dict) and "name" in item
    }

    tree = parameters["X-IDIS-Source-Tree-Fingerprint"]
    assert tree["in"] == "header"
    assert tree["required"] is False
    assert tree["schema"] == {"type": "string", "pattern": "^[0-9a-f]{64}$"}

    artifact = spec["components"]["schemas"]["DocumentArtifact"]
    assert artifact["properties"]["terminal_disposition"] == {
        "type": "string",
        "enum": ["DOCUMENT_NO_SUBSTANTIVE_CONTENT_CONFIRMED"],
        "nullable": True,
    }


def test_span_contract_requires_hash_field_but_tolerates_legacy_null_rows() -> None:
    spec = _static_spec()
    operation = spec["paths"]["/v1/documents/{docId}/spans"]["get"]
    item = operation["responses"]["200"]["content"]["application/json"]["schema"]["properties"][
        "items"
    ]["items"]

    assert "content_hash" in item["required"]
    assert item["properties"]["content_hash"] == {
        "type": "string",
        "pattern": "^[0-9a-f]{64}$",
        "nullable": True,
    }

    runtime_schema = SpanResponse.model_json_schema()
    assert "content_hash" in runtime_schema["required"]
    content_schema = runtime_schema["properties"]["content_hash"]
    assert {choice.get("type") for choice in content_schema["anyOf"]} == {"string", "null"}
    assert (
        next(
            choice["pattern"]
            for choice in content_schema["anyOf"]
            if choice.get("type") == "string"
        )
        == "^[0-9a-f]{64}$"
    )
    legacy = SpanResponse(
        span_id="10000000-0000-4000-8000-000000000001",
        document_id="20000000-0000-4000-8000-000000000001",
        span_type="PAGE_TEXT",
        locator={"page": 1},
        content_hash=None,
    )
    assert legacy.content_hash is None
