"""Slice95 Task 1 — characterization: pin the as-built API review surface + the true gaps.

GREEN-on-arrival. Pins (1) the review routes a fund reviewer already has and (3) the safe
shape of their read-models, plus (2) the three backend gaps that later tasks flip:
  - no reviewer readiness GET endpoint  -> Task 3 / DEC-D
  - no run-LIST endpoint for a deal      -> Task 4
  - the untyped debate ``rounds`` passthrough -> Task 2 / DEC-C

Any RED here is a real as-built surprise -> STOP and investigate. Injected fakes only — no
real LLM, no database (the app is built in-memory purely for route introspection).
"""

from __future__ import annotations

from idis.api.main import create_app
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


def _routes() -> set[tuple[str, str]]:
    """Every (method, path-template) registered on a freshly built app."""
    app = create_app()
    out: set[tuple[str, str]] = set()
    for route in app.routes:
        methods = getattr(route, "methods", None) or set()
        path = getattr(route, "path", "")
        for method in methods:
            out.add((method, path))
    return out


# --- (1) The review read surface a reviewer already has (reuse-before-create) ---


def test_review_read_routes_are_registered() -> None:
    routes = _routes()

    def has(method: str, suffix: str) -> bool:
        return any(m == method and p.endswith(suffix) for m, p in routes)

    assert has("GET", "/runs/{run_id}")  # run status + step ledger + block_reason
    assert has("GET", "/deals/{deal_id}/truth-dashboard")
    assert has("GET", "/claims/{claim_id}")
    assert has("GET", "/claims/{claim_id}/sanad")
    assert has("GET", "/deals/{deal_id}/deliverables")
    assert has("GET", "/product-bundle/manifest")
    assert has("GET", "/deals/{deal_id}/human-gates")
    assert has("POST", "/deals/{deal_id}/human-gates")
    assert has("POST", "/deals/{deal_id}/documents/upload")
    assert has("GET", "/debate/{debate_id}")
    assert has("POST", "/deals/{deal_id}/overrides")


# --- (2) The three backend gaps later tasks flip ---


def test_reviewer_readiness_endpoint_exists() -> None:
    # Task 3 / DEC-D closed the gap: a safe reviewer strict-readiness GET is now served.
    assert ("GET", "/v1/strict-readiness") in _routes()


def test_run_list_endpoint_exists() -> None:
    # Task 4 closed the gap: a GET run-list for a deal is now served (POST create still exists).
    routes = _routes()
    assert ("POST", "/v1/deals/{deal_id}/runs") in routes
    assert ("GET", "/v1/deals/{deal_id}/runs") in routes


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
