"""Slice100 Task 2 RED tests for durable dependency admission.

Every service observation is injected as typed facts.  The tests perform no database,
Redis, filesystem-object probe, or network call; production adapters will collect the
same facts after the separately approved live-preflight gate.
"""

from __future__ import annotations

import importlib
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

_TENANT = "10000000-0000-4000-8000-000000000001"
_CANARY_TENANT = "20000000-0000-4000-8000-000000000002"
_OWNER_KEY = "slice100-owner-key-secret"
_CANARY_KEY = "slice100-canary-key-secret"
_ADMIN_KEY = "slice100-governance-key-secret"

_BUDGET_COMPONENTS = frozenset(
    {
        "anthropic",
        "openai_embeddings",
        "sec_edgar",
        "companies_house",
        "github",
        "fred",
        "finnhub",
        "fmp",
        "world_bank",
        "escwa_catalog",
        "escwa_ispar",
        "qatar_open_data",
        "hackernews",
        "gdelt",
        "patentsview",
        "wayback",
        "google_news_rss",
    }
)


def _module() -> Any:
    return importlib.import_module("idis.runtime.dependency_readiness")


def _api_registry(*, canary_tenant: str = _CANARY_TENANT, extra: bool = False) -> str:
    registry: dict[str, dict[str, Any]] = {
        _OWNER_KEY: {
            "tenant_id": _TENANT,
            "actor_id": "slice100-acceptance-service",
            "name": "Slice100 Acceptance Service",
            "timezone": "Asia/Qatar",
            "data_region": "me-south-1",
            "roles": ["INTEGRATION_SERVICE"],
        },
        _CANARY_KEY: {
            "tenant_id": canary_tenant,
            "actor_id": "slice100-isolation-canary",
            "name": "Slice100 Isolation Canary",
            "timezone": "Asia/Qatar",
            "data_region": "me-south-1",
            "roles": ["INTEGRATION_SERVICE"],
        },
        _ADMIN_KEY: {
            "tenant_id": _TENANT,
            "actor_id": "slice100-governance-admin",
            "name": "Slice100 Governance Admin",
            "timezone": "Asia/Qatar",
            "data_region": "me-south-1",
            "roles": ["ADMIN"],
        },
    }
    if extra:
        registry["unplanned-fourth-key"] = dict(registry[_OWNER_KEY])
    return json.dumps(registry, sort_keys=True, separators=(",", ":"))


def _base_env(tmp_path: Path) -> dict[str, str]:
    object_root = tmp_path / "persistent-store"
    object_root.mkdir(parents=True, exist_ok=True)
    policy = tmp_path / "provider-budget-policy.json"
    policy.write_text("{}", encoding="utf-8")
    return {
        "IDIS_DATABASE_URL": "postgresql+psycopg2://app:secret@db/idis",
        "IDIS_IDEMPOTENCY_TTL_DAYS": "30",
        "IDIS_REDIS_URL": "redis://redis.internal:6379/0",
        "IDIS_RATE_LIMIT_USER_RPM": "600",
        "IDIS_RATE_LIMIT_INTEGRATION_RPM": "1200",
        "IDIS_RATE_LIMIT_BURST_MULTIPLIER": "2",
        "IDIS_OBJECT_STORE_BACKEND": "filesystem",
        "IDIS_OBJECT_STORE_BASE_DIR": str(object_root.resolve()),
        # Task 4 controlled drift: strict admission now includes the reviewed
        # bounded spool-to-object-store capacity contract.
        "IDIS_INGEST_MAX_BYTES": "536870912",
        "IDIS_OCR_MAX_PAGES": "72",
        "IDIS_OCR_TIMEOUT_SECONDS": "120",
        "IDIS_OCR_DPI": "200",
        "IDIS_API_KEYS_JSON": _api_registry(),
        "IDIS_REAL_EXAMPLE_API_KEY": _OWNER_KEY,
        "IDIS_ISOLATION_CANARY_API_KEY": _CANARY_KEY,
        "IDIS_GOVERNANCE_ADMIN_API_KEY": _ADMIN_KEY,
        "IDIS_ACCEPTANCE_TENANT_ID": _TENANT,
        "IDIS_ISOLATION_CANARY_TENANT_ID": _CANARY_TENANT,
        "IDIS_SERVICE_REGION": "me-south-1",
        "IDIS_UI_DEV_AUTH_BYPASS": "false",
        "IDIS_PROCESS_ROLE": "worker",
        "IDIS_WORKER_TENANT_IDS": _TENANT,
        "IDIS_WORKER_LEASE_SECONDS": "60",
        "IDIS_WORKER_HEARTBEAT_SECONDS": "10",
        "IDIS_PROVIDER_BUDGET_MAX_CALLS": "500",
        "IDIS_PROVIDER_BUDGET_POLICY_PATH": str(policy.resolve()),
        "IDIS_PROVIDER_BUDGET_CURRENCY": "USD",
    }


def _postgres_facts(module: Any, **overrides: Any) -> Any:
    values = {
        "connected": True,
        "schema_at_head": True,
        "app_role_superuser": False,
        "app_role_bypass_rls": False,
        "forced_rls": True,
        "tenant_context_ok": True,
        "cross_tenant_denied": True,
    }
    values.update(overrides)
    return module.PostgresProbeFacts(**values)


def _audit_facts(module: Any, **overrides: Any) -> Any:
    values = {
        "backend": "postgres",
        "emit_ok": True,
        "read_ok": True,
        "restart_read_ok": True,
    }
    values.update(overrides)
    return module.AuditProbeFacts(**values)


def _idempotency_facts(module: Any, **overrides: Any) -> Any:
    values = {
        "backend": "postgres",
        "put_ok": True,
        "replay_ok": True,
        "conflict_ok": True,
        "restart_replay_ok": True,
        "tenant_isolation_ok": True,
    }
    values.update(overrides)
    return module.IdempotencyProbeFacts(**values)


def _redis_facts(module: Any, **overrides: Any) -> Any:
    values = {
        "backend": "redis",
        "ping_ok": True,
        "atomic_bucket_ok": True,
        "cross_process_ok": True,
    }
    values.update(overrides)
    return module.RedisProbeFacts(**values)


def _object_store_facts(module: Any, **overrides: Any) -> Any:
    values = {
        "backend": "filesystem",
        "write_ok": True,
        "read_ok": True,
        "hash_ok": True,
        "delete_ok": True,
        "restart_read_ok": True,
    }
    values.update(overrides)
    return module.ObjectStoreProbeFacts(**values)


def _ingestion_facts(module: Any, **overrides: Any) -> Any:
    values = {
        "backend": "filesystem",
        "put_stream_supported": True,
        "bounded_read_ok": True,
        "stream_write_ok": True,
        "stream_read_ok": True,
        "stream_hash_ok": True,
        "stream_cleanup_ok": True,
        "staging_write_ok": True,
        "staging_cleanup_ok": True,
        "staging_free_bytes": 536870912,
        "object_store_free_bytes": 536870912,
        "shared_filesystem": False,
        "password_manifest_status": "none_configured",  # Task 4 audited source state.
    }
    values.update(overrides)
    return module.IngestionProbeFacts(**values)


def _worker_facts(module: Any, **overrides: Any) -> Any:
    values = {
        "backend": "postgres",
        "queued_row_ok": True,
        "claim_ok": True,
        "fencing_ok": True,
        "claim_audit_ok": True,
        "heartbeat_ok": True,
        "reclaim_ok": True,
        "fresh_claim_ok": True,
        "stale_write_rejected": True,
        "boundary_rollback_ok": True,
        "no_residue": True,
    }
    values.update(overrides)
    return module.WorkerProbeFacts(**values)


def _budget_facts(module: Any, **overrides: Any) -> Any:
    values = {
        "backend": "postgres",
        "policy_loaded": True,
        "policy_currency": "USD",
        "configured_provider_ids": _BUDGET_COMPONENTS,
        "token_caps_present": True,
        "call_reservation_ok": True,
        "usd_reservation_ok": True,
        "consume_ok": True,
        "refund_ok": True,
        "restart_read_ok": True,
    }
    values.update(overrides)
    return module.BudgetProbeFacts(**values)


def _assert_check(check: Any, *, component: str, may_proceed: bool, code: str | None) -> None:
    assert check.component == component
    assert check.may_proceed is may_proceed
    assert check.code == code


def _must_not_probe() -> Any:
    raise AssertionError("configuration blocker must be detected before a service probe")


def test_postgres_missing_configuration_blocks_without_probe(tmp_path: Path) -> None:
    module = _module()
    env = _base_env(tmp_path)
    env.pop("IDIS_DATABASE_URL")

    check = module.check_postgres_readiness(env=env, probe=_must_not_probe)

    _assert_check(check, component="postgres", may_proceed=False, code="DATABASE_UNAVAILABLE")


@pytest.mark.parametrize(
    ("overrides", "expected_code"),
    [
        ({"connected": False}, "DATABASE_UNAVAILABLE"),
        ({"schema_at_head": False}, "DATABASE_SCHEMA_NOT_READY"),
        ({"app_role_superuser": True}, "RLS_HEALTH_BLOCKED"),
        ({"app_role_bypass_rls": True}, "RLS_HEALTH_BLOCKED"),
        ({"forced_rls": False}, "RLS_HEALTH_BLOCKED"),
        ({"tenant_context_ok": False}, "RLS_HEALTH_BLOCKED"),
        ({"cross_tenant_denied": False}, "RLS_HEALTH_BLOCKED"),
    ],
)
def test_postgres_readiness_rejects_each_schema_role_and_rls_failure(
    tmp_path: Path,
    overrides: dict[str, Any],
    expected_code: str,
) -> None:
    module = _module()
    check = module.check_postgres_readiness(
        env=_base_env(tmp_path),
        probe=lambda: _postgres_facts(module, **overrides),
    )

    _assert_check(check, component="postgres", may_proceed=False, code=expected_code)


def test_postgres_readiness_accepts_complete_typed_evidence(tmp_path: Path) -> None:
    module = _module()
    check = module.check_postgres_readiness(
        env=_base_env(tmp_path),
        probe=lambda: _postgres_facts(module),
    )
    _assert_check(check, component="postgres", may_proceed=True, code=None)


@pytest.mark.parametrize("backend", ["jsonl", "memory"])
def test_audit_readiness_rejects_non_postgres_backends(backend: str) -> None:
    module = _module()
    check = module.check_audit_readiness(
        probe=lambda: _audit_facts(module, backend=backend),
    )
    _assert_check(check, component="audit", may_proceed=False, code="AUDIT_FAILURE")


@pytest.mark.parametrize("failed_field", ["emit_ok", "read_ok", "restart_read_ok"])
def test_audit_readiness_requires_emit_read_and_restart_survival(failed_field: str) -> None:
    module = _module()
    check = module.check_audit_readiness(
        probe=lambda: _audit_facts(module, **{failed_field: False}),
    )
    _assert_check(check, component="audit", may_proceed=False, code="AUDIT_EMIT_FAILED")


def test_audit_readiness_accepts_postgres_round_trip() -> None:
    module = _module()
    check = module.check_audit_readiness(probe=lambda: _audit_facts(module))
    _assert_check(check, component="audit", may_proceed=True, code=None)


@pytest.mark.parametrize("backend", ["sqlite", "memory"])
def test_idempotency_readiness_rejects_non_postgres_backends(
    tmp_path: Path,
    backend: str,
) -> None:
    module = _module()
    check = module.check_idempotency_readiness(
        env=_base_env(tmp_path),
        probe=lambda: _idempotency_facts(module, backend=backend),
    )
    _assert_check(
        check,
        component="idempotency",
        may_proceed=False,
        code="IDEMPOTENCY_NON_DURABLE_BLOCKED",
    )


def test_idempotency_readiness_rejects_sqlite_path_before_probe(tmp_path: Path) -> None:
    module = _module()
    env = _base_env(tmp_path)
    env["IDIS_IDEMPOTENCY_DB_PATH"] = str(tmp_path / "idempotency.sqlite")
    check = module.check_idempotency_readiness(env=env, probe=_must_not_probe)
    _assert_check(
        check,
        component="idempotency",
        may_proceed=False,
        code="IDEMPOTENCY_NON_DURABLE_BLOCKED",
    )


@pytest.mark.parametrize(
    "failed_field",
    ["put_ok", "replay_ok", "conflict_ok", "restart_replay_ok", "tenant_isolation_ok"],
)
def test_idempotency_readiness_requires_full_durable_semantics(
    tmp_path: Path,
    failed_field: str,
) -> None:
    module = _module()
    check = module.check_idempotency_readiness(
        env=_base_env(tmp_path),
        probe=lambda: _idempotency_facts(module, **{failed_field: False}),
    )
    _assert_check(
        check,
        component="idempotency",
        may_proceed=False,
        code="IDEMPOTENCY_STORE_FAILED",
    )


def test_idempotency_readiness_accepts_postgres_replay_conflict_and_isolation(
    tmp_path: Path,
) -> None:
    module = _module()
    check = module.check_idempotency_readiness(
        env=_base_env(tmp_path),
        probe=lambda: _idempotency_facts(module),
    )
    _assert_check(check, component="idempotency", may_proceed=True, code=None)


def test_redis_readiness_rejects_missing_url_without_probe(tmp_path: Path) -> None:
    module = _module()
    env = _base_env(tmp_path)
    env.pop("IDIS_REDIS_URL")
    check = module.check_redis_readiness(env=env, probe=_must_not_probe)
    _assert_check(
        check,
        component="redis",
        may_proceed=False,
        code="RATE_LIMIT_STORE_UNAVAILABLE",
    )


@pytest.mark.parametrize(
    "overrides",
    [
        {"backend": "memory"},
        {"ping_ok": False},
        {"atomic_bucket_ok": False},
        {"cross_process_ok": False},
    ],
)
def test_redis_readiness_requires_real_atomic_cross_process_store(
    tmp_path: Path,
    overrides: dict[str, Any],
) -> None:
    module = _module()
    check = module.check_redis_readiness(
        env=_base_env(tmp_path),
        probe=lambda: _redis_facts(module, **overrides),
    )
    _assert_check(
        check,
        component="redis",
        may_proceed=False,
        code="RATE_LIMIT_STORE_UNAVAILABLE",
    )


def test_redis_readiness_accepts_ping_and_atomic_bucket_proof(tmp_path: Path) -> None:
    module = _module()
    check = module.check_redis_readiness(
        env=_base_env(tmp_path),
        probe=lambda: _redis_facts(module),
    )
    _assert_check(check, component="redis", may_proceed=True, code=None)


@pytest.mark.parametrize("bad_path_kind", ["relative", "temp", "worktree"])
def test_object_store_rejects_nonpersistent_roots_before_probe(
    tmp_path: Path,
    bad_path_kind: str,
) -> None:
    module = _module()
    env = _base_env(tmp_path)
    worktree_root = (tmp_path / "worktree").resolve()
    temp_root = (tmp_path / "os-temp").resolve()
    worktree_root.mkdir()
    temp_root.mkdir()
    if bad_path_kind == "relative":
        env["IDIS_OBJECT_STORE_BASE_DIR"] = "relative/object-store"
    elif bad_path_kind == "temp":
        target = temp_root / "object-store"
        target.mkdir()
        env["IDIS_OBJECT_STORE_BASE_DIR"] = str(target)
    else:
        target = worktree_root / "object-store"
        target.mkdir()
        env["IDIS_OBJECT_STORE_BASE_DIR"] = str(target)

    check = module.check_object_store_readiness(
        env=env,
        probe=_must_not_probe,
        worktree_root=worktree_root,
        temp_root=temp_root,
    )
    _assert_check(
        check,
        component="object_store",
        may_proceed=False,
        code="OBJECT_STORE_HEALTH_BLOCKED",
    )


@pytest.mark.parametrize(
    "overrides",
    [
        {"backend": "memory"},
        {"write_ok": False},
        {"read_ok": False},
        {"hash_ok": False},
        {"delete_ok": False},
        {"restart_read_ok": False},
    ],
)
def test_object_store_requires_real_round_trip_hash_delete_and_restart_proof(
    tmp_path: Path,
    overrides: dict[str, Any],
) -> None:
    module = _module()
    persistent_root = (tmp_path / "persistent").resolve()
    persistent_root.mkdir()
    env = _base_env(tmp_path)
    env["IDIS_OBJECT_STORE_BASE_DIR"] = str(persistent_root)
    check = module.check_object_store_readiness(
        env=env,
        probe=lambda: _object_store_facts(module, **overrides),
        worktree_root=(tmp_path / "different-worktree").resolve(),
        temp_root=(tmp_path / "different-temp").resolve(),
    )
    _assert_check(
        check,
        component="object_store",
        may_proceed=False,
        code="OBJECT_STORE_HEALTH_BLOCKED",
    )


def test_object_store_accepts_absolute_external_filesystem_round_trip(tmp_path: Path) -> None:
    module = _module()
    persistent_root = (tmp_path / "persistent").resolve()
    persistent_root.mkdir()
    env = _base_env(tmp_path)
    env["IDIS_OBJECT_STORE_BASE_DIR"] = str(persistent_root)
    check = module.check_object_store_readiness(
        env=env,
        probe=lambda: _object_store_facts(module),
        worktree_root=(tmp_path / "different-worktree").resolve(),
        temp_root=(tmp_path / "different-temp").resolve(),
    )
    _assert_check(check, component="object_store", may_proceed=True, code=None)


@pytest.mark.parametrize(
    ("mutation", "expected_code"),
    [
        (
            lambda env: env.__setitem__("IDIS_REAL_EXAMPLE_API_KEY", "not-registered"),
            "AUTH_ACCEPTANCE_KEY_MISMATCH",
        ),
        (
            lambda env: env.__setitem__("IDIS_API_KEYS_JSON", _api_registry(canary_tenant=_TENANT)),
            "AUTH_CANARY_KEY_MISMATCH",
        ),
        (
            lambda env: env.__setitem__("IDIS_API_KEYS_JSON", _api_registry(extra=True)),
            "AUTH_CONFIG_BLOCKED",
        ),
        (lambda env: env.__setitem__("IDIS_UI_DEV_AUTH_BYPASS", "true"), "AUTH_CONFIG_BLOCKED"),
        (
            lambda env: env.__setitem__("IDIS_DEV_API_KEY", "forbidden-dev-key"),
            "AUTH_CONFIG_BLOCKED",
        ),
    ],
)
def test_auth_readiness_rejects_wrong_bindings_extra_keys_and_dev_bypass(
    tmp_path: Path,
    mutation: Callable[[dict[str, str]], None],
    expected_code: str,
) -> None:
    module = _module()
    env = _base_env(tmp_path)
    mutation(env)
    check = module.check_auth_readiness(env=env)
    _assert_check(check, component="auth", may_proceed=False, code=expected_code)


def test_auth_readiness_rejects_governance_key_without_exact_admin_record(tmp_path: Path) -> None:
    module = _module()
    env = _base_env(tmp_path)
    registry = json.loads(env["IDIS_API_KEYS_JSON"])
    registry[_ADMIN_KEY]["roles"] = ["INTEGRATION_SERVICE"]
    env["IDIS_API_KEYS_JSON"] = json.dumps(registry)
    check = module.check_auth_readiness(env=env)
    _assert_check(
        check,
        component="auth",
        may_proceed=False,
        code="GOVERNANCE_ADMIN_UNAUTHORIZED",
    )


def test_auth_readiness_accepts_exact_owner_canary_and_governance_bindings(tmp_path: Path) -> None:
    module = _module()
    check = module.check_auth_readiness(env=_base_env(tmp_path))
    _assert_check(check, component="auth", may_proceed=True, code=None)


@pytest.mark.parametrize(
    "invalid_registry",
    [
        lambda raw: raw.replace(
            '"timezone":"Asia/Qatar"',
            '"timezone":"Asia/Qatar","timezone":"Asia/Qatar"',
            1,
        ),
        lambda raw: raw.replace('"timezone":"Asia/Qatar"', '"timezone":NaN', 1),
        lambda raw: raw.replace('"timezone":"Asia/Qatar"', '"timezone":Infinity', 1),
    ],
    ids=["duplicate-nested-member", "nan", "infinity"],
)
def test_auth_readiness_uses_strict_registry_parser(
    tmp_path: Path,
    invalid_registry: Callable[[str], str],
) -> None:
    env = _base_env(tmp_path)
    env["IDIS_API_KEYS_JSON"] = invalid_registry(env["IDIS_API_KEYS_JSON"])

    check = _module().check_auth_readiness(env=env)

    _assert_check(check, component="auth", may_proceed=False, code="AUTH_CONFIG_BLOCKED")


@pytest.mark.parametrize(
    ("mutation", "expected_code"),
    [
        (lambda env: env.pop("IDIS_WORKER_TENANT_IDS"), "WORKER_TENANT_SCOPE_MISSING"),
        (
            lambda env: env.__setitem__("IDIS_WORKER_TENANT_IDS", f"{_TENANT},{_CANARY_TENANT}"),
            "WORKER_TENANT_SCOPE_MISSING",
        ),
        (
            lambda env: env.__setitem__("IDIS_WORKER_TENANT_IDS", _CANARY_TENANT),
            "WORKER_TENANT_SCOPE_MISSING",
        ),
        (lambda env: env.__setitem__("IDIS_PROCESS_ROLE", "api"), "STRICT_PROCESS_ROLE_INVALID"),
        (
            lambda env: env.__setitem__("IDIS_WORKER_LEASE_SECONDS", "0"),
            "WORKER_LEASE_CONFIG_INVALID",
        ),
        (
            lambda env: env.__setitem__("IDIS_WORKER_HEARTBEAT_SECONDS", "60"),
            "WORKER_LEASE_CONFIG_INVALID",
        ),
    ],
)
def test_worker_readiness_requires_exact_tenant_role_and_lease_configuration(
    tmp_path: Path,
    mutation: Callable[[dict[str, str]], Any],
    expected_code: str,
) -> None:
    module = _module()
    env = _base_env(tmp_path)
    mutation(env)
    check = module.check_worker_readiness(
        env=env,
        expected_role="worker",
        probe=lambda: _worker_facts(module),
    )
    _assert_check(check, component="worker", may_proceed=False, code=expected_code)


def test_worker_readiness_accepts_exact_single_tenant_scope(tmp_path: Path) -> None:
    module = _module()
    check = module.check_worker_readiness(
        env=_base_env(tmp_path),
        expected_role="worker",
        probe=lambda: _worker_facts(module),
    )
    _assert_check(check, component="worker", may_proceed=True, code=None)


@pytest.mark.parametrize(
    ("env_mutation", "facts_overrides", "expected_code", "probe_expected"),
    [
        (
            lambda env: env.pop("IDIS_PROVIDER_BUDGET_POLICY_PATH"),
            {},
            "PROVIDER_BUDGET_CONFIG_BLOCKED",
            False,
        ),
        (
            lambda env: env.__setitem__("IDIS_PROVIDER_BUDGET_MAX_CALLS", "0"),
            {},
            "PROVIDER_BUDGET_CONFIG_BLOCKED",
            False,
        ),
        (
            lambda env: env.__setitem__("IDIS_PROVIDER_BUDGET_CURRENCY", "EUR"),
            {},
            "PROVIDER_BUDGET_CONFIG_BLOCKED",
            False,
        ),
        (lambda env: None, {"backend": "memory"}, "PROVIDER_BUDGET_CONFIG_BLOCKED", True),
        (lambda env: None, {"policy_loaded": False}, "PROVIDER_BUDGET_CONFIG_BLOCKED", True),
        (
            lambda env: None,
            {"configured_provider_ids": _BUDGET_COMPONENTS - {"google_news_rss"}},
            "PROVIDER_BUDGET_PROVIDER_UNCONFIGURED",
            True,
        ),
        (
            lambda env: None,
            {"token_caps_present": False},
            "PROVIDER_BUDGET_PROVIDER_UNCONFIGURED",
            True,
        ),
        (lambda env: None, {"call_reservation_ok": False}, "PROVIDER_BUDGET_CONFIG_BLOCKED", True),
        (lambda env: None, {"usd_reservation_ok": False}, "PROVIDER_BUDGET_CONFIG_BLOCKED", True),
        (lambda env: None, {"consume_ok": False}, "PROVIDER_BUDGET_CONFIG_BLOCKED", True),
        (lambda env: None, {"refund_ok": False}, "PROVIDER_BUDGET_CONFIG_BLOCKED", True),
        (lambda env: None, {"restart_read_ok": False}, "PROVIDER_BUDGET_CONFIG_BLOCKED", True),
    ],
)
def test_budget_readiness_rejects_every_nondurable_or_incomplete_policy_case(
    tmp_path: Path,
    env_mutation: Callable[[dict[str, str]], Any],
    facts_overrides: dict[str, Any],
    expected_code: str,
    probe_expected: bool,
) -> None:
    module = _module()
    env = _base_env(tmp_path)
    env_mutation(env)
    calls = 0

    def probe() -> Any:
        nonlocal calls
        calls += 1
        return _budget_facts(module, **facts_overrides)

    check = module.check_budget_readiness(env=env, probe=probe)
    _assert_check(check, component="budgets", may_proceed=False, code=expected_code)
    assert calls == int(probe_expected)


def test_budget_readiness_accepts_exact_signed_policy_component_set(tmp_path: Path) -> None:
    module = _module()
    check = module.check_budget_readiness(
        env=_base_env(tmp_path),
        probe=lambda: _budget_facts(module),
    )
    _assert_check(check, component="budgets", may_proceed=True, code=None)


def test_dependency_report_requires_all_ten_durable_checks(tmp_path: Path) -> None:
    module = _module()
    env = _base_env(tmp_path)
    checks = [
        module.check_postgres_readiness(env=env, probe=lambda: _postgres_facts(module)),
        module.ReadinessCheck(component="erasure_executor", may_proceed=True, code=None),
        module.check_audit_readiness(probe=lambda: _audit_facts(module)),
        module.check_idempotency_readiness(
            env=env,
            probe=lambda: _idempotency_facts(module),
        ),
        module.check_redis_readiness(env=env, probe=lambda: _redis_facts(module)),
        module.check_object_store_readiness(
            env=env,
            probe=lambda: _object_store_facts(module),
            worktree_root=(tmp_path / "worktree").resolve(),
            temp_root=(tmp_path / "temp").resolve(),
        ),
        module.check_ingestion_readiness(env=env, probe=lambda: _ingestion_facts(module)),
        module.check_auth_readiness(env=env),
        module.check_worker_readiness(
            env=env,
            expected_role="worker",
            probe=lambda: _worker_facts(module),
        ),
        module.check_budget_readiness(env=env, probe=lambda: _budget_facts(module)),
    ]

    report = module.build_dependency_admission_report(checks)

    assert report.may_proceed is True
    assert report.blocker_codes == ()
    assert [check.component for check in report.checks] == [
        "postgres",
        "erasure_executor",
        "audit",
        "idempotency",
        "redis",
        "object_store",
        "ingestion",
        "auth",
        "worker",
        "budgets",
    ]


def test_dependency_report_fails_closed_on_one_internal_partial_result(tmp_path: Path) -> None:
    module = _module()
    env = _base_env(tmp_path)
    checks = [
        module.check_postgres_readiness(env=env, probe=lambda: _postgres_facts(module)),
        module.ReadinessCheck(component="erasure_executor", may_proceed=True, code=None),
        module.check_audit_readiness(probe=lambda: _audit_facts(module, restart_read_ok=False)),
        module.check_idempotency_readiness(
            env=env,
            probe=lambda: _idempotency_facts(module),
        ),
        module.check_redis_readiness(env=env, probe=lambda: _redis_facts(module)),
        module.check_object_store_readiness(
            env=env,
            probe=lambda: _object_store_facts(module),
            worktree_root=(tmp_path / "worktree").resolve(),
            temp_root=(tmp_path / "temp").resolve(),
        ),
        module.check_ingestion_readiness(env=env, probe=lambda: _ingestion_facts(module)),
        module.check_auth_readiness(env=env),
        module.check_worker_readiness(
            env=env,
            expected_role="worker",
            probe=lambda: _worker_facts(module),
        ),
        module.check_budget_readiness(env=env, probe=lambda: _budget_facts(module)),
    ]

    report = module.build_dependency_admission_report(checks)

    assert report.may_proceed is False
    assert report.blocker_codes == ("AUDIT_EMIT_FAILED",)
