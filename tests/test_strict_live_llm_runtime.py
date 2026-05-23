"""Slice 55 strict live LLM and durable runtime preflight tests."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from idis.api.auth import IDIS_API_KEYS_ENV


def test_strict_report_marks_llm_components_live_only_after_health_check_passes() -> None:
    """Live model env plus health check should clear LLM-only strict blockers."""
    from idis.services.runs.strict_full_live import (
        StrictComponentStatus,
        build_strict_full_live_readiness_report,
    )
    from idis.services.runs.strict_full_live_health import StrictHealthCheckResult

    report = build_strict_full_live_readiness_report(
        env=_live_runtime_env(),
        llm_health_checker=lambda _request: StrictHealthCheckResult.ok(
            service="anthropic",
            message="Anthropic live health check passed",
        ),
        runtime_health_checker=lambda _request: StrictHealthCheckResult.ok(
            service="durable_runtime",
            message="Durable runtime health check passed",
        ),
    )

    for component_name in (
        "supported_parsers_extraction",
        "live_llm_model_clients",
        "agent_analysis",
        "debate_layer_1",
        "scoring",
    ):
        component = report.component(component_name)
        assert component.status == StrictComponentStatus.LIVE_WIRED_AND_USED
        assert component.mode == "live"
        assert component.provenance["provider"] == "anthropic"
        assert component.provenance["fallback"] == "none"

    runtime = report.component("durable_runtime")
    assert runtime.status == StrictComponentStatus.LIVE_WIRED_AND_USED
    assert runtime.mode == "live"
    assert runtime.provenance["backend"] == "postgres+filesystem"


def test_strict_report_blocks_live_llm_when_health_check_fails() -> None:
    """Configured Anthropic env must still fail strict mode when live health fails."""
    from idis.services.runs.strict_full_live import (
        StrictComponentStatus,
        build_strict_full_live_readiness_report,
    )
    from idis.services.runs.strict_full_live_health import StrictHealthCheckResult

    report = build_strict_full_live_readiness_report(
        env=_live_runtime_env(),
        llm_health_checker=lambda _request: StrictHealthCheckResult.failed(
            service="anthropic",
            message="Anthropic health check failed",
        ),
        runtime_health_checker=lambda _request: StrictHealthCheckResult.ok(
            service="durable_runtime",
            message="Durable runtime health check passed",
        ),
    )

    for component_name in (
        "supported_parsers_extraction",
        "live_llm_model_clients",
        "agent_analysis",
        "debate_layer_1",
        "scoring",
    ):
        component = report.component(component_name)
        assert component.status == StrictComponentStatus.CONFIGURED_BUT_FAILED_HEALTH_CHECK
        assert component.may_proceed is False
        assert "Anthropic health check failed" in component.blocker_message
        assert component.provenance["provider"] == "anthropic"
        assert component.provenance["fallback"] == "none"


def test_strict_report_blocks_durable_runtime_when_health_check_fails() -> None:
    """Runtime env presence is insufficient if DB/object-store health fails."""
    from idis.services.runs.strict_full_live import (
        StrictComponentStatus,
        build_strict_full_live_readiness_report,
    )
    from idis.services.runs.strict_full_live_health import StrictHealthCheckResult

    report = build_strict_full_live_readiness_report(
        env=_live_runtime_env(),
        llm_health_checker=lambda _request: StrictHealthCheckResult.ok(
            service="anthropic",
            message="Anthropic live health check passed",
        ),
        runtime_health_checker=lambda _request: StrictHealthCheckResult.failed(
            service="durable_runtime",
            message="Postgres SELECT 1 failed",
        ),
    )

    runtime = report.component("durable_runtime")
    assert runtime.status == StrictComponentStatus.CONFIGURED_BUT_FAILED_HEALTH_CHECK
    assert runtime.may_proceed is False
    assert "Postgres SELECT 1 failed" in runtime.blocker_message
    assert runtime.provenance["backend"] == "postgres+filesystem"


def test_default_anthropic_health_checks_every_configured_model() -> None:
    """Default live health must probe extract, default debate, and arbiter models."""
    from idis.services.runs.strict_full_live_health import (
        StrictLLMHealthCheckRequest,
        run_anthropic_llm_health_check,
    )

    seen_models: list[str] = []

    class FakeAnthropicClient:
        def __init__(self, *, model: str, max_tokens: int) -> None:
            seen_models.append(model)
            assert max_tokens == 4

        def call(self, prompt: str, *, json_mode: bool = False) -> str:
            assert "Health check only" in prompt
            assert json_mode is False
            return "OK"

    with patch(
        "idis.services.extraction.extractors.anthropic_client.AnthropicLLMClient",
        FakeAnthropicClient,
    ):
        result = run_anthropic_llm_health_check(
            StrictLLMHealthCheckRequest(
                extract_model="claude-extract",
                debate_default_model="claude-debate",
                debate_arbiter_model="claude-arbiter",
            )
        )

    assert result.passed is True
    assert seen_models == ["claude-extract", "claude-debate", "claude-arbiter"]


def test_default_anthropic_health_rejects_non_ok_response() -> None:
    """Default live health must verify the non-private health prompt result."""
    from idis.services.runs.strict_full_live_health import (
        StrictLLMHealthCheckRequest,
        run_anthropic_llm_health_check,
    )

    class FakeAnthropicClient:
        def __init__(self, *, model: str, max_tokens: int) -> None:
            pass

        def call(self, prompt: str, *, json_mode: bool = False) -> str:
            return " OK"

    with patch(
        "idis.services.extraction.extractors.anthropic_client.AnthropicLLMClient",
        FakeAnthropicClient,
    ):
        result = run_anthropic_llm_health_check(
            StrictLLMHealthCheckRequest(
                extract_model="claude-extract",
                debate_default_model="claude-debate",
                debate_arbiter_model="claude-arbiter",
            )
        )

    assert result.passed is False
    assert "unexpected response" in result.message


def test_runtime_health_rejects_unusable_api_key_registry() -> None:
    """Durable runtime health should validate API-key registry shape, not just JSON."""
    from idis.services.runs.strict_full_live_health import (
        StrictRuntimeHealthCheckRequest,
        run_durable_runtime_health_check,
    )

    result = run_durable_runtime_health_check(
        StrictRuntimeHealthCheckRequest(
            database_url="postgresql://app:secret@db.example/idis",
            api_keys_json='{"bad-key":{"roles":[]}}',
            object_store_backend="filesystem",
        )
    )

    assert result.passed is False
    assert "tenant_id" in result.message


def test_runtime_health_rejects_api_key_entries_missing_real_auth_fields() -> None:
    """Strict runtime must reject records the real API auth model rejects."""
    from idis.services.runs.strict_full_live_health import (
        StrictRuntimeHealthCheckRequest,
        run_durable_runtime_health_check,
    )

    result = run_durable_runtime_health_check(
        StrictRuntimeHealthCheckRequest(
            database_url="postgresql://app:secret@db.example/idis",
            api_keys_json='{"test-key":{"tenant_id":"tenant-a","roles":["ANALYST"]}}',
            object_store_backend="filesystem",
        )
    )

    assert result.passed is False
    assert "actor_id" in result.message
    assert "name" in result.message
    assert "timezone" in result.message
    assert "data_region" in result.message
    assert "test-key" not in result.message


def test_runtime_health_rejects_unknown_api_key_roles() -> None:
    """Strict runtime role validation must match real auth fail-closed behavior."""
    from idis.services.runs.strict_full_live_health import (
        StrictRuntimeHealthCheckRequest,
        run_durable_runtime_health_check,
    )

    result = run_durable_runtime_health_check(
        StrictRuntimeHealthCheckRequest(
            database_url="postgresql://app:secret@db.example/idis",
            api_keys_json=(
                '{"test-key":{"tenant_id":"tenant-a","actor_id":"actor-a",'
                '"name":"Tenant A","timezone":"UTC","data_region":"me-south-1",'
                '"roles":["ALIEN"]}}'
            ),
            object_store_backend="filesystem",
        )
    )

    assert result.passed is False
    assert result.message == f"{IDIS_API_KEYS_ENV} entry contains invalid role"
    assert "ALIEN" not in result.message
    assert "test-key" not in result.message


def test_runtime_health_accepts_complete_valid_api_key_record() -> None:
    """Strict runtime should accept API-key records shaped like real API tests."""
    from idis.services.runs.strict_full_live_health import (
        StrictRuntimeHealthCheckRequest,
        run_durable_runtime_health_check,
    )

    result = run_durable_runtime_health_check(
        StrictRuntimeHealthCheckRequest(
            database_url="postgresql://app:secret@db.example/idis",
            api_keys_json=_valid_api_keys_json(),
            object_store_backend="filesystem",
            db_conn=_FakeDbConn(),
        )
    )

    assert result.passed is True


def test_dotenv_byol_keys_are_config_present_not_wired_without_secret_leaks(
    tmp_path: Path,
) -> None:
    """BYOL env keys in dotenv must not be reported as simply missing or leak values."""
    from idis.services.runs.strict_full_live import (
        StrictComponentStatus,
        build_strict_full_live_readiness_report,
    )

    dotenv = tmp_path / ".env"
    dotenv.write_text(
        "\n".join(
            [
                "COMPANIES_HOUSE_API_KEY=companies-secret-value",
                "FRED_API_KEY=fred-secret-value",
                "FINNHUB_API_KEY=finnhub-secret-value",
                "FMP_API_KEY=fmp-secret-value",
                "GITHUB_API_TOKEN=github-secret-value",
            ]
        ),
        encoding="utf-8",
    )

    report = build_strict_full_live_readiness_report(env={}, dotenv_path=dotenv)
    enrichment = report.component("external_enrichment_apis")

    assert enrichment.status == StrictComponentStatus.CODE_EXISTS_BUT_NOT_WIRED
    assert enrichment.mode == "config-present-not-wired"
    assert set(enrichment.required_env_vars) == {
        "COMPANIES_HOUSE_API_KEY",
        "FRED_API_KEY",
        "FINNHUB_API_KEY",
        "FMP_API_KEY",
        "GITHUB_API_TOKEN",
    }
    encoded = report.model_dump_json()
    for secret_value in (
        "companies-secret-value",
        "fred-secret-value",
        "finnhub-secret-value",
        "fmp-secret-value",
        "github-secret-value",
    ):
        assert secret_value not in encoded


def test_supabase_vars_are_classified_without_false_product_wiring(tmp_path: Path) -> None:
    """Supabase product vars must not imply Auth/Storage/Vector wiring."""
    from idis.services.runs.strict_full_live import (
        StrictComponentStatus,
        build_strict_full_live_readiness_report,
    )
    from idis.services.runs.strict_full_live_health import StrictHealthCheckResult

    dotenv = tmp_path / ".env"
    dotenv.write_text(
        "\n".join(
            [
                "SUPABASE_URL=https://project.supabase.co",
                "SUPABASE_KEY=supabase-key-secret",
                "SUPABASE_SECRET_KEY=supabase-secret-key",
                "IDIS_DATABASE_URL=postgresql://postgres:secret@db.project.supabase.co/postgres",
                f"{IDIS_API_KEYS_ENV}={_valid_api_keys_json()}",
                "IDIS_OBJECT_STORE_BACKEND=filesystem",
            ]
        ),
        encoding="utf-8",
    )

    report = build_strict_full_live_readiness_report(
        env={},
        dotenv_path=dotenv,
        runtime_health_checker=lambda _request: StrictHealthCheckResult.ok(
            service="durable_runtime",
            message="Durable runtime health check passed",
        ),
    )

    assert report.component("supabase_database").status == (
        StrictComponentStatus.LIVE_WIRED_AND_USED
    )
    assert report.component("supabase_database").mode == "managed-postgres-runtime"
    for component_name in (
        "supabase_auth",
        "supabase_storage",
        "supabase_vectors_rag",
        "supabase_edge_realtime_cron_queues",
    ):
        component = report.component(component_name)
        assert component.status != StrictComponentStatus.LIVE_WIRED_AND_USED
        assert component.mode == "config-present-not-wired"
    encoded = report.model_dump_json()
    assert "supabase-key-secret" not in encoded
    assert "supabase-secret-key" not in encoded
    assert "postgres:secret" not in encoded


def test_dotenv_database_url_needs_connectivity_before_runtime_is_live(
    tmp_path: Path,
) -> None:
    """Dotenv DB config should satisfy presence only after health passes."""
    from idis.services.runs.strict_full_live import (
        StrictComponentStatus,
        build_strict_full_live_readiness_report,
    )
    from idis.services.runs.strict_full_live_health import StrictHealthCheckResult

    dotenv = tmp_path / ".env"
    dotenv.write_text(
        "\n".join(
            [
                "IDIS_DATABASE_URL=postgresql://app:secret@db.example/idis",
                "IDIS_DATABASE_ADMIN_URL=postgresql://admin:secret@db.example/idis",
                f"{IDIS_API_KEYS_ENV}={_valid_api_keys_json()}",
                "IDIS_OBJECT_STORE_BACKEND=filesystem",
            ]
        ),
        encoding="utf-8",
    )

    failed = build_strict_full_live_readiness_report(
        env={},
        dotenv_path=dotenv,
        runtime_health_checker=lambda _request: StrictHealthCheckResult.failed(
            service="durable_runtime",
            message="Postgres SELECT 1 failed",
        ),
    )

    runtime = failed.component("durable_runtime")
    assert runtime.status == StrictComponentStatus.CONFIGURED_BUT_FAILED_HEALTH_CHECK
    assert runtime.mode == "configured-health-check-failed"
    db_inventory = next(
        item for item in failed.env_config_inventory if item.env_var == "IDIS_DATABASE_URL"
    )
    assert db_inventory.present_in_dotenv is True
    assert db_inventory.loaded_in_process is False
    assert db_inventory.health_checked_live is False
    admin_inventory = next(
        item for item in failed.env_config_inventory if item.env_var == "IDIS_DATABASE_ADMIN_URL"
    )
    assert admin_inventory.present_in_dotenv is True
    assert admin_inventory.health_checked_live is False

    passed = build_strict_full_live_readiness_report(
        env={},
        dotenv_path=dotenv,
        runtime_health_checker=lambda _request: StrictHealthCheckResult.ok(
            service="durable_runtime",
            message="Durable runtime health check passed",
        ),
    )

    assert passed.component("durable_runtime").status == StrictComponentStatus.LIVE_WIRED_AND_USED
    passed_admin_inventory = next(
        item for item in passed.env_config_inventory if item.env_var == "IDIS_DATABASE_ADMIN_URL"
    )
    assert passed_admin_inventory.health_checked_live is False
    encoded = failed.model_dump_json() + passed.model_dump_json()
    assert "app:secret" not in encoded
    assert "admin:secret" not in encoded


def test_strict_builders_reject_deterministic_backends() -> None:
    """Strict mode must not allow direct builder calls to select deterministic clients."""
    from idis.api.routes.runs import (
        _build_analysis_llm_client,
        _build_debate_role_runners,
        _build_extraction_llm_client,
        _build_scoring_llm_client,
    )

    env = {
        "IDIS_REQUIRE_FULL_LIVE": "1",
        "IDIS_EXTRACT_BACKEND": "deterministic",
        "IDIS_DEBATE_BACKEND": "deterministic",
    }
    with patch.dict(os.environ, env, clear=True):
        with pytest.raises(ValueError, match="IDIS_EXTRACT_BACKEND=anthropic"):
            _build_extraction_llm_client()
        with pytest.raises(ValueError, match="IDIS_DEBATE_BACKEND=anthropic"):
            _build_debate_role_runners()
        with pytest.raises(ValueError, match="IDIS_DEBATE_BACKEND=anthropic"):
            _build_analysis_llm_client()
        with pytest.raises(ValueError, match="IDIS_DEBATE_BACKEND=anthropic"):
            _build_scoring_llm_client()


def test_strict_builders_reject_implicit_anthropic_model_defaults() -> None:
    """Strict direct builder calls must require explicit model env vars."""
    from idis.api.routes.runs import (
        _build_analysis_llm_client,
        _build_debate_role_runners,
        _build_extraction_llm_client,
        _build_scoring_llm_client,
    )

    env = {
        "IDIS_REQUIRE_FULL_LIVE": "1",
        "IDIS_EXTRACT_BACKEND": "anthropic",
        "IDIS_DEBATE_BACKEND": "anthropic",
        "ANTHROPIC_API_KEY": "sk-ant-test-fake-key",
    }
    with patch.dict(os.environ, env, clear=True):
        with pytest.raises(ValueError, match="IDIS_ANTHROPIC_MODEL_EXTRACT"):
            _build_extraction_llm_client()
        with pytest.raises(ValueError, match="IDIS_ANTHROPIC_MODEL_DEBATE_DEFAULT"):
            _build_analysis_llm_client()
        with pytest.raises(ValueError, match="IDIS_ANTHROPIC_MODEL_DEBATE_DEFAULT"):
            _build_scoring_llm_client()
    debate_env = env | {"IDIS_ANTHROPIC_MODEL_DEBATE_DEFAULT": "claude-test-default"}
    with (
        patch.dict(os.environ, debate_env, clear=True),
        pytest.raises(ValueError, match="IDIS_ANTHROPIC_MODEL_DEBATE_ARBITER"),
    ):
        _build_debate_role_runners()


def test_non_strict_builders_keep_deterministic_defaults() -> None:
    """Slice 55 must preserve non-strict deterministic defaults."""
    from idis.api.routes.runs import (
        _build_analysis_llm_client,
        _build_debate_role_runners,
        _build_extraction_llm_client,
        _build_scoring_llm_client,
    )
    from idis.debate.orchestrator import RoleRunners
    from idis.services.extraction.extractors.llm_client import (
        DeterministicAnalysisLLMClient,
        DeterministicLLMClient,
        DeterministicScoringLLMClient,
    )

    with patch.dict(os.environ, {}, clear=True):
        assert isinstance(_build_extraction_llm_client(), DeterministicLLMClient)
        assert isinstance(_build_debate_role_runners(), RoleRunners)
        assert isinstance(_build_analysis_llm_client(), DeterministicAnalysisLLMClient)
        assert isinstance(_build_scoring_llm_client(), DeterministicScoringLLMClient)


def _live_runtime_env() -> dict[str, str]:
    return {
        "IDIS_REQUIRE_FULL_LIVE": "1",
        "IDIS_EXTRACT_BACKEND": "anthropic",
        "IDIS_DEBATE_BACKEND": "anthropic",
        "ANTHROPIC_API_KEY": "sk-ant-test-fake-key",
        "IDIS_ANTHROPIC_MODEL_EXTRACT": "claude-test-extract",
        "IDIS_ANTHROPIC_MODEL_DEBATE_DEFAULT": "claude-test-default",
        "IDIS_ANTHROPIC_MODEL_DEBATE_ARBITER": "claude-test-arbiter",
        "IDIS_DATABASE_URL": "postgresql://app:secret@db.example/idis",
        IDIS_API_KEYS_ENV: _valid_api_keys_json(),
        "IDIS_OBJECT_STORE_BACKEND": "filesystem",
    }


def _valid_api_keys_json() -> str:
    return (
        '{"test-key":{"tenant_id":"tenant-a","actor_id":"actor-a",'
        '"name":"Tenant A","timezone":"UTC","data_region":"me-south-1",'
        '"roles":["ANALYST"]}}'
    )


class _FakeDbConn:
    def execute(self, _statement: object) -> None:
        return None
