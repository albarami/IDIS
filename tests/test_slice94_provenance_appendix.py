"""Slice94 Task 2 — the run-level provenance appendix (closes G1).

The exported bundle gains a safe `provenance_appendix` artifact consolidating the five
run-level LLM provenance blocks (extraction/debate/analysis/scoring/layer2) — safe fields
only (provider/model/prompt-ids/versions/strict-flags, sanitized request ids, executed
booleans); never prompt bodies, model output, raw rationale, API keys, paths, or exception
text. It is registered in the catalog + manifest and cross-referenced from run_summary /
evidence_index. It is emitted only when run provenance is supplied (absent → bundle unchanged).

Injected fakes only — no real Anthropic; filesystem object store; no database.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from idis.storage.filesystem_store import FilesystemObjectStore
from tests.test_deliverables_generator import _make_context, _make_scorecard
from tests.test_slice59_product_export_bundle import (
    DEAL_ID,
    RUN_ID,
    TENANT_ID,
    RecordingDeliverablesRepository,
    _make_deliverables_bundle,
)

_TIMESTAMP = "2026-01-01T00:00:00Z"

_BLOCKS = (
    "extraction_provenance",
    "debate_provenance",
    "analysis_provenance",
    "scoring_provenance",
    "layer2_provenance",
)


def _safe_provenance() -> dict[str, Any]:
    """The five safe provenance blocks as the run steps already build them."""
    return {
        "extraction_provenance": {
            "provider": "anthropic",
            "backend": "anthropic",
            "model": "claude-extract",
            "prompt_id": "EXTRACT_CLAIMS_V1",
            "prompt_version": "1.0.0",
            "provider_request_id": "req_extract",
        },
        "debate_provenance": {
            "provider": "anthropic",
            "backend": "anthropic",
            "default_model": "claude-default",
            "arbiter_model": "claude-arbiter",
            "prompt_ids": ["advocate", "arbiter"],
            "prompt_version": "1.0.0",
            "default_provider_request_id": "req_debate",
            "arbiter_provider_request_id": "req_arb",
        },
        "analysis_provenance": {
            "provider": "anthropic",
            "backend": "anthropic",
            "model": "claude-analysis",
            "prompt_id": None,
            "prompt_version": None,
            "provider_request_id": "req_analysis",
        },
        "scoring_provenance": {
            "provider": "anthropic",
            "backend": "anthropic",
            "model": "claude-scoring",
            "prompt_id": "scoring_agent",
            "prompt_version": "1.0.0",
            "provider_request_id": "req_scoring",
        },
        "layer2_provenance": {
            "provider": "anthropic",
            "backend": "anthropic",
            "challenger_model": "claude-challenger",
            "arbiter_model": "claude-arbiter",
            "prompt_ids": ["layer2_ic_challenger", "layer2_ic_arbiter"],
            "prompt_version": "1.0.0",
            "challenger_executed": True,
            "arbiter_executed": True,
            "live_calls_executed": True,
        },
    }


def _adversarial_provenance() -> dict[str, Any]:
    """Safe blocks polluted with forbidden fields that must never surface."""
    prov = _safe_provenance()
    prov["debate_provenance"]["raw_prompt"] = "SECRET PROMPT BODY do not leak"
    prov["debate_provenance"]["api_key"] = "sk-LEAK1234567890"
    prov["analysis_provenance"]["response_text"] = "PRIVATE MODEL OUTPUT revenue is fabricated"
    prov["scoring_provenance"]["exception"] = "Traceback: boom at C:\\secret\\reports\\deal.pdf"
    return prov


def _export(
    tmp_path: Path, run_provenance: dict[str, Any] | None = None
) -> tuple[dict[str, Any], FilesystemObjectStore]:
    from idis.deliverables.product_bundle import ProductBundleExporter

    object_store = FilesystemObjectStore(base_dir=tmp_path / "objects")
    exporter = ProductBundleExporter(
        deliverables_repo=RecordingDeliverablesRepository(),
        object_store=object_store,
        object_store_backend="filesystem",
    )
    exporter.export_bundle(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        bundle=_make_deliverables_bundle(),
        analysis_context=_make_context(),
        scorecard=_make_scorecard(),
        export_timestamp=_TIMESTAMP,
        run_provenance=run_provenance,
    )
    manifest = object_store.get(
        tenant_id=TENANT_ID, key=f"runs/{RUN_ID}/product_bundle/manifest.json"
    )
    return json.loads(manifest.body.decode("utf-8")), object_store


def _read(store: FilesystemObjectStore, filename: str) -> Any:
    obj = store.get(tenant_id=TENANT_ID, key=f"runs/{RUN_ID}/product_bundle/{filename}")
    return json.loads(obj.body.decode("utf-8"))


# --- The provenance appendix emits with safe fields only ---


def test_bundle_emits_safe_provenance_appendix(tmp_path: Path) -> None:
    manifest, store = _export(tmp_path, run_provenance=_adversarial_provenance())
    types = {artifact["type"] for artifact in manifest["artifacts"]}
    assert "provenance_appendix" in types

    appendix = _read(store, "provenance_appendix.json")
    assert appendix["status"] == "present"
    for block in _BLOCKS:
        assert block in appendix["provenance"]
    # Safe fields are preserved.
    assert appendix["provenance"]["layer2_provenance"]["live_calls_executed"] is True
    assert appendix["provenance"]["debate_provenance"]["default_model"] == "claude-default"
    assert appendix["provenance"]["scoring_provenance"]["prompt_id"] == "scoring_agent"
    # Adversarial fields are dropped by the key allowlist — never surface.
    encoded = json.dumps(appendix)
    for forbidden in (
        "SECRET PROMPT",
        "sk-LEAK",
        "PRIVATE MODEL OUTPUT",
        "Traceback",
        "C:\\secret",
        "raw_prompt",
        "api_key",
        "response_text",
        "exception",
    ):
        assert forbidden not in encoded


# --- Cross-referenced from run_summary + evidence_index ---


def test_provenance_cross_referenced_in_run_summary_and_evidence_index(tmp_path: Path) -> None:
    _manifest, store = _export(tmp_path, run_provenance=_safe_provenance())
    run_summary = _read(store, "run_summary.json")
    evidence_index = _read(store, "evidence_index.json")

    assert run_summary["provenance_status"] == "present"
    assert sorted(run_summary["provenance_blocks"]) == sorted(_BLOCKS)
    # The source/provenance appendix (evidence_index) now carries the provenance side too.
    assert set(evidence_index["provenance_appendix"]["provenance"]).issuperset(set(_BLOCKS))


# --- Absent run provenance -> the bundle is unchanged ---


def test_no_provenance_appendix_when_run_provenance_absent(tmp_path: Path) -> None:
    manifest, store = _export(tmp_path, run_provenance=None)
    types = {artifact["type"] for artifact in manifest["artifacts"]}
    assert "provenance_appendix" not in types  # no standalone artifact when absent
    run_summary = _read(store, "run_summary.json")
    evidence_index = _read(store, "evidence_index.json")
    assert run_summary["provenance_status"] == "absent"
    assert run_summary["provenance_blocks"] == []
    assert "provenance_appendix" not in evidence_index


# --- Typed value filtering: nested dict/list values under allowed keys are dropped ---


def _assert_no_nested_containers(provenance_blocks: dict[str, Any]) -> None:
    for block in provenance_blocks.values():
        assert isinstance(block, dict)
        for value in block.values():
            assert not isinstance(value, dict), f"nested dict survived: {value!r}"
            if isinstance(value, list):
                for item in value:
                    assert not isinstance(item, (dict, list)), f"nested container in list: {item!r}"


def test_provenance_appendix_drops_nested_values_in_allowed_keys(tmp_path: Path) -> None:
    prov = _safe_provenance()
    # A nested dict smuggled into an allowed string field must be dropped whole.
    prov["debate_provenance"]["default_provider_request_id"] = {"nested": "ordinary model answer"}
    # prompt_ids must keep only the safe string entries.
    prov["debate_provenance"]["prompt_ids"] = [
        "advocate",
        {"bad": "ordinary model answer"},
        "arbiter",
    ]
    # A non-bool value in a boolean field must be dropped (never coerced).
    prov["layer2_provenance"]["live_calls_executed"] = {"coerced": "ordinary model answer"}

    _manifest, store = _export(tmp_path, run_provenance=prov)
    appendix = _read(store, "provenance_appendix.json")

    debate = appendix["provenance"]["debate_provenance"]
    assert "default_provider_request_id" not in debate
    assert debate["prompt_ids"] == ["advocate", "arbiter"]
    assert "live_calls_executed" not in appendix["provenance"]["layer2_provenance"]
    # No nested dict/list-of-dict survives anywhere, and the smuggled text never surfaces.
    assert "ordinary model answer" not in json.dumps(appendix)
    _assert_no_nested_containers(appendix["provenance"])


def test_provenance_builder_type_filters_values() -> None:
    from idis.deliverables.product_bundle import _provenance_appendix

    result = _provenance_appendix(
        {
            "debate_provenance": {
                "provider": "anthropic",
                "default_provider_request_id": {"nested": "ordinary model answer"},
                "prompt_ids": ["ok", {"bad": "x"}, 123],
                "strict_full_live": "true",  # string in a boolean field -> dropped
            },
            "layer2_provenance": {
                "live_calls_executed": True,
                "challenger_model": ["not", "a", "string"],  # list in a string field -> dropped
            },
        }
    )
    assert result["provenance"]["debate_provenance"] == {
        "provider": "anthropic",
        "prompt_ids": ["ok"],
    }
    assert result["provenance"]["layer2_provenance"] == {"live_calls_executed": True}


# --- The export path threads the step provenance blocks through (DEC-C) ---


def test_export_path_threads_run_provenance() -> None:
    runs_src = Path("src/idis/api/routes/runs.py").read_text(encoding="utf-8")
    assert "run_provenance=run_provenance" in runs_src
    orchestrator_src = Path("src/idis/services/runs/orchestrator.py").read_text(encoding="utf-8")
    assert '"debate_provenance": accumulated.get("debate_provenance")' in orchestrator_src
    assert '"layer2_provenance": accumulated.get("layer2_provenance")' in orchestrator_src
