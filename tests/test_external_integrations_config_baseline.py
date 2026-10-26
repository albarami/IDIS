"""External integrations config baseline tests for Phase 2.0."""

from __future__ import annotations

from pathlib import Path

from scripts.audit_full_system_wiring import _discover_provider_ids, collect_wiring_inventory

from idis.services.enrichment.task9.provider_catalog import PROVIDER_IDS

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_external_connectors_are_inventoried_without_live_provider_calls() -> None:
    """The audit must inspect the connector registry without calling providers."""
    inventory = collect_wiring_inventory(REPO_ROOT)
    enrichment = inventory["external_enrichment_connectors"]

    assert enrichment.status == "WIRED"
    assert frozenset(enrichment.metadata["provider_ids"]) == PROVIDER_IDS
    assert enrichment.metadata["provider_count"] == len(PROVIDER_IDS)
    assert enrichment.metadata["live_calls_performed"] is False


def test_provider_inventory_is_parsed_from_catalog_not_connector_constants(
    tmp_path: Path,
) -> None:
    """Static discovery must use the catalog authority without importing providers."""
    catalog = tmp_path / "src/idis/services/enrichment/task9/provider_catalog.py"
    catalog.parent.mkdir(parents=True)
    catalog.write_text(
        '_PROVIDER_SPECS = (_spec("alpha"), _spec("beta"))\n',
        encoding="utf-8",
    )
    connector = tmp_path / "src/idis/services/enrichment/connectors/decoy.py"
    connector.parent.mkdir(parents=True)
    connector.write_text('PROVIDER_ID = "stale-connector-only"\n', encoding="utf-8")

    assert _discover_provider_ids(tmp_path) == ["alpha", "beta"]


def test_anthropic_is_config_validated_only_and_openai_is_placeholder() -> None:
    """LLM providers must not be live-called during the baseline."""
    inventory = collect_wiring_inventory(REPO_ROOT)

    anthropic = inventory["anthropic_llm"]
    assert anthropic.status == "PARTIAL"
    assert anthropic.metadata["live_calls_performed"] is False
    assert any("config-validated only" in item for item in anthropic.evidence)

    openai = inventory["openai_llm"]
    assert openai.status == "CONFIG_ONLY"
    assert any("no OpenAI LLM runtime client" in item for item in openai.gaps)
    assert any("embedding health" in item for item in openai.gaps)


def test_openai_sdk_for_embeddings_does_not_mark_llm_partial() -> None:
    """Embedding-only OpenAI SDK use must not upgrade openai_llm to PARTIAL."""
    pyproject = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    inventory = collect_wiring_inventory(REPO_ROOT)

    assert "openai" in pyproject.lower()
    assert inventory["openai_llm"].status == "CONFIG_ONLY"
    assert inventory["rag_vector_retrieval"].status == "WIRED"


def test_report_generation_records_validation_commands_as_pending_until_run() -> None:
    """Initial report content should reserve validation-result slots for command outputs."""
    inventory = collect_wiring_inventory(REPO_ROOT)
    validation = inventory.validation_results

    assert "ruff check ." in validation
    assert "mypy src/idis --ignore-missing-imports" in validation
    assert "pytest -q" in validation
    assert validation["pytest -q"] == "NOT_RUN"
