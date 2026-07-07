"""Slice95 Task 1 — characterization: pin the as-built API review surface + the true gaps.

Pins (1) the review routes a fund reviewer already has and (3) the safe shape of their
read-models, plus (2) the three backend gaps that later tasks flip:
  - no reviewer readiness GET endpoint  -> Task 3 / DEC-D
  - no run-LIST endpoint for a deal      -> Task 4
  - the untyped debate ``rounds`` passthrough -> Task 2 / DEC-C

The route-surface pins assert against the **static OpenAPI contract** (``load_openapi_spec``),
not a freshly built app's runtime route table, so they are deterministic and order-independent.
Runtime registration + behaviour is proven separately by the HTTP endpoint tests
(``test_slice95_strict_readiness_endpoint``, ``test_slice95_run_list_endpoint``). Injected fakes
only — no real LLM, no database.
"""

from __future__ import annotations

from idis.api.openapi_loader import load_openapi_spec
from idis.api.routes.debate import DebateSession
from idis.api.routes.deliverables import ProductBundleManifestReview
from idis.api.routes.runs import RunStatus, RunStepResponse

# Field names that must never appear on a reviewer-facing read-model (safe-shape).
_PRIVATE_KEYS = {
    "text",
    "content",
    "transcript",
    "prompt",
    "prompt_body",
    "raw_text",
    "raw",
    "raw_output",
    "model_output",
    "path",
    "bytes",
    "content_b64",
    "embedding",
    "vector",
}

_HTTP_METHODS = frozenset({"get", "post", "put", "patch", "delete", "head", "options"})


def _contract_routes() -> set[tuple[str, str]]:
    """Every (METHOD, path) declared in the static OpenAPI contract (``load_openapi_spec``)."""
    paths = load_openapi_spec()["paths"]
    return {
        (method.upper(), path)
        for path, operations in paths.items()
        for method in operations
        if method in _HTTP_METHODS
    }


# --- (1) The review read surface a reviewer already has, declared in the API contract ---


def test_review_read_routes_declared_in_contract() -> None:
    routes = _contract_routes()
    assert ("GET", "/v1/runs/{runId}") in routes  # run status + step ledger + block_reason
    assert ("GET", "/v1/deals/{dealId}/truth-dashboard") in routes
    assert ("GET", "/v1/claims/{claimId}") in routes
    assert ("GET", "/v1/claims/{claimId}/sanad") in routes
    assert ("GET", "/v1/deals/{dealId}/deliverables") in routes
    assert ("GET", "/v1/deals/{dealId}/runs/{runId}/product-bundle/manifest") in routes
    assert ("GET", "/v1/deals/{dealId}/human-gates") in routes
    assert ("POST", "/v1/deals/{dealId}/human-gates") in routes
    assert ("POST", "/v1/deals/{dealId}/documents/upload") in routes
    assert ("GET", "/v1/debate/{debateId}") in routes
    assert ("POST", "/v1/deals/{dealId}/overrides") in routes


# --- (2) The three backend gaps later tasks flip (declared in the API contract) ---


def test_reviewer_readiness_endpoint_declared_in_contract() -> None:
    # Task 3 / DEC-D closed the gap: a safe reviewer strict-readiness GET is declared in the
    # contract (runtime registration proven by test_slice95_strict_readiness_endpoint).
    assert ("GET", "/v1/strict-readiness") in _contract_routes()


def test_run_list_endpoint_declared_in_contract() -> None:
    # Task 4 closed the gap: a GET run-list for a deal is declared (POST create still declared;
    # runtime registration proven by test_slice95_run_list_endpoint).
    routes = _contract_routes()
    assert ("POST", "/v1/deals/{dealId}/runs") in routes
    assert ("GET", "/v1/deals/{dealId}/runs") in routes


def test_debate_rounds_is_typed_safe_shape() -> None:
    # Task 2 / DEC-C closed the gap: rounds is now a typed safe-shape summary, not an untyped
    # ``list[dict[str, Any]]`` passthrough. (Local import so only this pin flips.)
    from idis.api.routes.debate import DebateRoundSummary

    assert DebateSession.model_fields["rounds"].annotation == list[DebateRoundSummary]


# --- (3) Safe-shape of the existing review read-models (no private field names) ---


def test_run_status_read_model_is_safe_shape() -> None:
    assert set(RunStatus.model_fields) >= {"run_id", "status", "mode", "steps", "block_reason"}
    assert set(RunStepResponse.model_fields) >= {"step_name", "status", "retry_count"}
    assert not (_PRIVATE_KEYS & set(RunStepResponse.model_fields))
    assert not (_PRIVATE_KEYS & set(RunStatus.model_fields))


def test_manifest_review_read_model_is_safe_shape() -> None:
    fields = set(ProductBundleManifestReview.model_fields)
    assert fields >= {"artifact_count", "artifacts", "tenant_id", "deal_id", "run_id"}
    assert not (_PRIVATE_KEYS & fields)
