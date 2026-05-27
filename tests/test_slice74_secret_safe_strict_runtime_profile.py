"""Slice74 secret-safe strict runtime profile tests."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest


def test_slice74_runtime_profile_loads_dotenv_with_process_precedence(
    tmp_path: Path,
) -> None:
    """Strict profile inventory exposes sources/status only, never values."""
    from idis.services.runs.strict_full_live import build_strict_runtime_profile_report

    dotenv_secret = "DOTENV_SECRET_SHOULD_NOT_LEAK_SLICE74"
    process_secret = "PROCESS_SECRET_SHOULD_NOT_LEAK_SLICE74"
    dotenv_path = tmp_path / "strict.env"
    dotenv_path.write_text(
        "\n".join(
            [
                "IDIS_REQUIRE_FULL_LIVE=1",
                f"ANTHROPIC_API_KEY={dotenv_secret}",
                "IDIS_EXTRACT_BACKEND=deterministic",
                "IDIS_ANTHROPIC_MODEL_EXTRACT=dotenv-model-secret",
                "IDIS_DATABASE_URL=postgresql://secret-user:secret-pass@localhost/idis",
            ]
        ),
        encoding="utf-8",
    )

    report = build_strict_runtime_profile_report(
        env={
            "IDIS_STRICT_DOTENV_PATH": str(dotenv_path),
            "ANTHROPIC_API_KEY": process_secret,
            "IDIS_EXTRACT_BACKEND": "anthropic",
        },
    )
    encoded = json.dumps(report, sort_keys=True)
    env_by_name = {item["name"]: item for item in report["env_inventory"]}
    model_by_name = {item["name"]: item for item in report["model_env_inventory"]}

    assert report["dotenv_profile"] == {
        "env_name": "IDIS_STRICT_DOTENV_PATH",
        "configured": True,
        "source": "process",
        "load_status": "loaded",
        "reason_codes": [],
    }
    assert env_by_name["ANTHROPIC_API_KEY"]["source"] == "process"
    assert env_by_name["IDIS_ANTHROPIC_MODEL_EXTRACT"]["source"] == "dotenv"
    assert model_by_name["IDIS_EXTRACT_BACKEND"]["validation_status"] == "valid"
    assert model_by_name["IDIS_DEBATE_BACKEND"]["validation_status"] == "missing"
    assert dotenv_secret not in encoded
    assert process_secret not in encoded
    assert str(dotenv_path) not in encoded
    assert "postgresql://" not in encoded
    assert "dotenv-model-secret" not in encoded


@pytest.mark.parametrize(
    ("dotenv_path_factory", "expected_status", "expected_reason"),
    [
        (lambda tmp_path: tmp_path / "missing.env", "missing", "DOTENV_PATH_MISSING"),
        (lambda tmp_path: tmp_path, "not_file", "DOTENV_PATH_NOT_FILE"),
    ],
)
def test_slice74_runtime_profile_reports_dotenv_path_failures_without_leakage(
    tmp_path: Path,
    dotenv_path_factory: Any,
    expected_status: str,
    expected_reason: str,
) -> None:
    """Missing/bad dotenv paths should fail closed with names and reason codes only."""
    from idis.services.runs.strict_full_live import build_strict_runtime_profile_report

    dotenv_path = dotenv_path_factory(tmp_path)
    report = build_strict_runtime_profile_report(env={"IDIS_STRICT_DOTENV_PATH": str(dotenv_path)})
    encoded = json.dumps(report, sort_keys=True).lower()

    assert report["dotenv_profile"]["load_status"] == expected_status
    assert report["dotenv_profile"]["reason_codes"] == [expected_reason]
    assert str(dotenv_path).lower() not in encoded
    assert "c:\\projects" not in encoded


def test_slice74_runtime_profile_reports_malformed_dotenv_lines_without_values(
    tmp_path: Path,
) -> None:
    """Malformed strict dotenv content should produce reason codes without values."""
    from idis.services.runs.strict_full_live import build_strict_runtime_profile_report

    secret_value = "MALFORMED_DOTENV_SECRET_SLICE74"
    dotenv_path = tmp_path / "strict.env"
    dotenv_path.write_text(
        "\n".join(
            [
                f"={secret_value}",
                f"BAD KEY={secret_value}",
                "IDIS_REQUIRE_FULL_LIVE=1",
            ]
        ),
        encoding="utf-8",
    )

    report = build_strict_runtime_profile_report(dotenv_path=dotenv_path)
    encoded = json.dumps(report, sort_keys=True)

    assert report["dotenv_profile"]["load_status"] == "loaded_with_warnings"
    assert report["dotenv_profile"]["reason_codes"] == ["DOTENV_LINE_MALFORMED"]
    assert secret_value not in encoded
    assert str(dotenv_path) not in encoded


def test_slice74_api_key_registry_validation_is_pure_and_redacted(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """API-key shape validation should never expose keys or nested values."""
    from idis.api.auth import validate_api_key_registry_config

    secret_key = "slice74-secret-api-key"
    secret_actor = "slice74-secret-actor"
    report = validate_api_key_registry_config(
        env={
            "IDIS_API_KEYS_JSON": json.dumps(
                {
                    secret_key: {
                        "tenant_id": "00000000-0000-0000-0000-000000000001",
                        "actor_id": secret_actor,
                        "name": "Missing timezone/data_region",
                    }
                }
            ),
            "IDIS_API_KEYS": "legacy-secret-registry",
        }
    )
    data = report.model_dump(mode="json")
    encoded = json.dumps(data, sort_keys=True)
    logs = caplog.text

    assert data["canonical"] == "IDIS_API_KEYS_JSON"
    assert data["status"] == "malformed_entries"
    assert data["valid_entry_count"] == 0
    assert data["malformed_entry_count"] == 1
    assert data["legacy_alias_status"] == "present_ignored"
    assert data["reason_codes"] == ["MALFORMED_API_KEY_ENTRY"]
    for forbidden in [secret_key, secret_actor, "legacy-secret-registry"]:
        assert forbidden not in encoded
        assert forbidden not in logs


def test_slice74_api_key_registry_validation_rejects_unknown_roles_without_leakage() -> None:
    """Registry profile must not report auth-invalid role configs as valid."""
    from idis.api.auth import validate_api_key_registry_config

    secret_key = "slice74-secret-role-key"
    report = validate_api_key_registry_config(
        env={
            "IDIS_API_KEYS_JSON": json.dumps(
                {
                    secret_key: {
                        "tenant_id": "00000000-0000-0000-0000-000000000001",
                        "actor_id": "actor",
                        "name": "Unknown Role",
                        "timezone": "UTC",
                        "data_region": "us-east-1",
                        "roles": ["BOGUS_ROLE"],
                    }
                }
            )
        }
    )
    data = report.model_dump(mode="json")
    encoded = json.dumps(data, sort_keys=True)

    assert data["status"] == "malformed_entries"
    assert data["valid_entry_count"] == 0
    assert data["malformed_entry_count"] == 1
    assert data["reason_codes"] == ["MALFORMED_API_KEY_ENTRY"]
    assert secret_key not in encoded


def test_slice74_api_key_registry_validation_rejects_blank_api_key_names() -> None:
    """Blank API-key map keys are unusable and must not validate as ready."""
    from idis.api.auth import validate_api_key_registry_config

    report = validate_api_key_registry_config(
        env={
            "IDIS_API_KEYS_JSON": json.dumps(
                {
                    "   ": {
                        "tenant_id": "00000000-0000-0000-0000-000000000001",
                        "actor_id": "actor",
                        "name": "Blank Key",
                        "timezone": "UTC",
                        "data_region": "us-east-1",
                        "roles": ["ADMIN"],
                    }
                }
            )
        }
    )
    data = report.model_dump(mode="json")

    assert data["status"] == "malformed_entries"
    assert data["valid_entry_count"] == 0
    assert data["malformed_entry_count"] == 1
    assert data["reason_codes"] == ["MALFORMED_API_KEY_ENTRY"]


def test_slice74_legacy_api_keys_alone_does_not_satisfy_canonical_registry() -> None:
    """Legacy IDIS_API_KEYS must be visible as stale/ignored, not accepted."""
    from idis.api.auth import validate_api_key_registry_config

    report = validate_api_key_registry_config(env={"IDIS_API_KEYS": "legacy-secret"})
    data = report.model_dump(mode="json")

    assert data["canonical"] == "IDIS_API_KEYS_JSON"
    assert data["status"] == "missing"
    assert data["legacy_alias_status"] == "present_ignored"
    assert data["reason_codes"] == ["IDIS_API_KEYS_JSON_MISSING"]


def test_slice74_runtime_profile_inventory_does_not_call_providers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Inventory is config-shape only: no provider, DB, graph, RAG, or BYOL calls."""
    from idis.services.runs import strict_full_live

    def fail_call(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("Slice74 profile inventory must not call runtime providers")

    monkeypatch.setattr(strict_full_live, "check_embedding_health", fail_call)
    monkeypatch.setattr(strict_full_live, "check_neo4j_health", fail_call)
    monkeypatch.setattr(strict_full_live, "check_pgvector_health", fail_call)
    monkeypatch.setattr(strict_full_live, "assess_byol_provider_readiness", fail_call)

    report = strict_full_live.build_strict_runtime_profile_report(
        env={
            "IDIS_EXTRACT_BACKEND": "anthropic",
            "IDIS_DEBATE_BACKEND": "anthropic",
            "ANTHROPIC_API_KEY": "anthropic-secret",
            "OPENAI_API_KEY": "openai-secret",
            "FINNHUB_API_KEY": "finnhub-secret",
        }
    )
    provider_names = {item["name"] for item in report["provider_env_inventory"]}

    assert report["live_provider_calls_made"] is False
    assert report["external_enrichment_calls_made"] is False
    assert "ANTHROPIC_API_KEY" in provider_names
    assert "OPENAI_API_KEY" in provider_names
    assert "FINNHUB_API_KEY" in provider_names


def test_slice74_runtime_profile_leak_guard_error_never_echoes_secret() -> None:
    """If the guard trips, the exception itself must remain secret-free."""
    from idis.services.runs.strict_full_live import _assert_strict_runtime_profile_safe

    secret_value = "slice74-secret-leak-token"
    with pytest.raises(ValueError) as exc_info:
        _assert_strict_runtime_profile_safe(
            report={"bad": secret_value},
            env_values={"ANTHROPIC_API_KEY": secret_value},
            process_env={},
        )

    assert "STRICT_RUNTIME_PROFILE_REPORT_LEAKAGE" in str(exc_info.value)
    assert secret_value not in str(exc_info.value)


def test_slice74_provisioning_truth_leak_guard_error_never_echoes_secret() -> None:
    """Adjacent strict provisioning leakage guard should fail safely too."""
    from idis.services.runs.strict_provisioning_truth import (
        _assert_strict_provisioning_truth_safe,
    )

    secret_value = "slice74-provisioning-secret-token"
    with pytest.raises(ValueError) as exc_info:
        _assert_strict_provisioning_truth_safe(
            {"bad": secret_value},
            env={"ANTHROPIC_API_KEY": secret_value},
        )

    assert "STRICT_PROVISIONING_TRUTH_REPORT_LEAKAGE" in str(exc_info.value)
    assert secret_value not in str(exc_info.value)


def test_slice74_env_example_matches_runtime_profile_expectations() -> None:
    """The example env file should document every Slice74 strict profile key."""
    from idis.services.runs.strict_full_live import build_strict_runtime_profile_report

    repo_root = Path(__file__).resolve().parents[1]
    env_example = (repo_root / ".env.example").read_text(encoding="utf-8")
    report = build_strict_runtime_profile_report(env={})

    for key in report["env_example_required_names"]:
        assert f"{key}=" in env_example or f"# {key}=" in env_example


def test_slice74_documented_command_outputs_secret_safe_profile_json(tmp_path: Path) -> None:
    """Existing-module command should print safe JSON only."""
    repo_root = Path(__file__).resolve().parents[1]
    dotenv_path = tmp_path / "strict.env"
    dotenv_path.write_text(
        "\n".join(
            [
                "IDIS_REQUIRE_FULL_LIVE=1",
                "ANTHROPIC_API_KEY=command-secret-anthropic",
                "IDIS_EXTRACT_BACKEND=anthropic",
                "IDIS_ANTHROPIC_MODEL_EXTRACT=command-secret-model",
            ]
        ),
        encoding="utf-8",
    )
    env = {
        **os.environ,
        "PYTHONPATH": str(repo_root / "src"),
    }

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "idis.services.runs.strict_provisioning_truth",
            "--strict-runtime-profile",
            "--dotenv",
            str(dotenv_path),
        ],
        cwd=repo_root,
        env=env,
        check=False,
        capture_output=True,
        text=True,
        timeout=20,
    )
    stdout = result.stdout.strip()
    stderr = result.stderr.strip()
    profile = json.loads(stdout)

    assert result.returncode == 0
    assert profile["documented_command"]["module"] == "idis.services.runs.strict_provisioning_truth"
    assert profile["strict_global_may_proceed"] is False
    assert profile["readiness_cleared"] is False
    assert profile["real_example_not_run"] is True
    assert profile["strict_full_run_executed"] is False
    assert profile["live_provider_calls_made"] is False
    assert profile["external_enrichment_calls_made"] is False
    assert profile["vc_ready_claim"] is False
    for forbidden in [
        "command-secret-anthropic",
        "command-secret-model",
        str(dotenv_path),
    ]:
        assert forbidden not in stdout
        assert forbidden not in stderr
