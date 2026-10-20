"""GL-05 slice 1 offline role-config tests. Pre-slice values captured at 4a01a02c."""

from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from idis.models.debate import DebateRole
from idis.providers.role_config import (
    LlmRole,
    RoleConfigError,
    RoleModelConfig,
    admitted_document_sha256,
    resolve_role_config,
    role_row_sha256,
)

_REPO = Path(__file__).resolve().parents[1]
_ADMITTED = _REPO / "contracts" / "llm_role_configs_v1.json"
_SONNET_4 = "claude-sonnet-4-20250514"
_OPUS_4 = "claude-opus-4-20250514"
_EXTRACTION_MAX_TOKENS = 4096
_ANALYSIS_MAX_TOKENS = 8192
_SCORING_MAX_TOKENS = 16384
_DEBATE_MAX_TOKENS = 8192
_IC_MAX_TOKENS = 8192
_PROMPT_VERSION = "1.0.0"
_EXTRACT_ENV = "IDIS_ANTHROPIC_MODEL_EXTRACT"
_DEFAULT_ENV = "IDIS_ANTHROPIC_MODEL_DEBATE_DEFAULT"
_ARBITER_ENV = "IDIS_ANTHROPIC_MODEL_DEBATE_ARBITER"
_UNADMITTED = "LLM_ROLE_CONFIG_UNADMITTED"
_UNKNOWN = "LLM_ROLE_UNKNOWN"
_PATCHED_MODEL = "patched-model"
_PATCHED_MAX_TOKENS = 1234
_CHILD_ENV_KEYS = ("PATH", "SYSTEMROOT", "PATHEXT", "WINDIR", "COMSPEC", "TEMP", "TMP")

_NAMED_ROLES = (
    "extraction",
    "methodology_extraction",
    "financial_agent",
    "historian_agent",
    "market_agent",
    "risk_officer_agent",
    "sector_specialist_agent",
    "team_agent",
    "technical_agent",
    "terms_agent",
    "scoring",
    "advocate",
    "sanad_breaker",
    "contradiction_finder",
    "risk_officer",
    "arbiter",
    "ic_challenger",
    "ic_arbiter",
)
_AGENT_TYPES = _NAMED_ROLES[2:10]
_PATH_ONLY_PROMPTS = {
    **{name: f"prompts/{name}/1.0.0/prompt.md" for name in _AGENT_TYPES},
    "ic_challenger": "prompts/layer2_ic_challenger/1.0.0/prompt.md",
    "ic_arbiter": "prompts/layer2_ic_arbiter/1.0.0/prompt.md",
}

_RECORDED_PROMPT_IDS = {
    "extraction": "EXTRACT_CLAIMS_V1",
    "methodology_extraction": "METHODOLOGY_QUESTION_EXTRACTION_V1",
    "scoring": "scoring_agent",
    "advocate": "DEBATE_ADVOCATE_V1",
    "sanad_breaker": "DEBATE_SANAD_BREAKER_V1",
    "contradiction_finder": "DEBATE_CONTRADICTION_FINDER_V1",
    "risk_officer": "DEBATE_RISK_OFFICER_V1",
    "arbiter": "DEBATE_ARBITER_V1",
}


def _child_env() -> dict[str, str]:
    env = {key: os.environ[key] for key in _CHILD_ENV_KEYS if os.environ.get(key)}
    env["PYTHONPATH"] = os.pathsep.join(("src", "."))
    env["PYTHONUTF8"] = "1"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def _clear_model_bindings(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in (_EXTRACT_ENV, _DEFAULT_ENV, _ARBITER_ENV, "IDIS_STRICT_DOTENV_PATH"):
        monkeypatch.delenv(key, raising=False)


def _admitted_document() -> dict[str, Any]:
    loaded = json.loads(_ADMITTED.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def _install_document(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, document: dict[str, Any]
) -> None:
    path = tmp_path / "llm_role_configs_v1.json"
    path.write_bytes((json.dumps(document) + "\n").encode("utf-8"))
    monkeypatch.setattr("idis.providers.role_config.admitted_config_path", lambda: path)


def _expect_unadmitted(role: LlmRole = LlmRole.EXTRACTION) -> None:
    with pytest.raises(RoleConfigError) as raised:
        resolve_role_config(role, backend="anthropic", environ={})
    assert raised.value.code == _UNADMITTED
    assert str(raised.value) == _UNADMITTED


def test_named_roles_match_debate_roles_and_agent_types() -> None:
    assert tuple(role.value for role in LlmRole) == _NAMED_ROLES
    assert {role.value for role in DebateRole} == {
        "advocate",
        "sanad_breaker",
        "contradiction_finder",
        "risk_officer",
        "arbiter",
    }
    source = (_REPO / "src/idis/services/extraction/extractors/llm_client.py").read_text(
        encoding="utf-8"
    )
    for agent_type in _AGENT_TYPES:
        assert f'"{agent_type}"' in source
        assert LlmRole(agent_type) in LlmRole


def test_unknown_role_is_a_safe_code() -> None:
    with pytest.raises(RoleConfigError) as raised:
        resolve_role_config("not-a-role", backend="anthropic", environ={})
    assert raised.value.code == _UNKNOWN
    assert str(raised.value) == _UNKNOWN


def test_missing_file_is_unadmitted(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    missing = tmp_path / "missing.json"
    monkeypatch.setattr("idis.providers.role_config.admitted_config_path", lambda: missing)
    _expect_unadmitted()


def test_altered_row_is_unadmitted(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    document = _admitted_document()
    document["roles"][0]["max_output_tokens"] = 1
    document["document_sha256"] = admitted_document_sha256(document)
    _install_document(monkeypatch, tmp_path, document)
    _expect_unadmitted()


def test_bad_document_hash_is_unadmitted(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    document = _admitted_document()
    flipped = "0" * 64
    if document["document_sha256"] == flipped:
        flipped = "1" * 64
    document["document_sha256"] = flipped
    _install_document(monkeypatch, tmp_path, document)
    _expect_unadmitted()


def test_malformed_row_is_unadmitted(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    document = _admitted_document()
    document["roles"][0] = {"role": "extraction"}
    document["document_sha256"] = admitted_document_sha256(document)
    _install_document(monkeypatch, tmp_path, document)
    _expect_unadmitted()


def test_prompt_content_drift_is_unadmitted(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    document = _admitted_document()
    row = document["roles"][0]
    drifted = "0" * 64
    if row["prompt_content_sha256"] == drifted:
        drifted = "1" * 64
    row["prompt_content_sha256"] = drifted
    row["config_sha256"] = role_row_sha256(row)
    document["document_sha256"] = admitted_document_sha256(document)
    _install_document(monkeypatch, tmp_path, document)
    _expect_unadmitted()


def test_every_named_role_resolves_to_todays_defaults() -> None:
    from idis.services.prompts.promotion_policy import RUNTIME_PROMPT_IDS

    recorded: set[str] = set()
    for role in LlmRole:
        config = resolve_role_config(role, backend="anthropic", environ={})
        assert config.role is role
        assert config.provider == "anthropic"
        assert config.effort is None
        assert config.temperature == "0"
        assert config.fallback_policy == "none"
        assert config.budget_component_id == "anthropic"
        assert config.prompt_version == _PROMPT_VERSION
        assert config.max_output_tokens > 0
        if role in {LlmRole.ARBITER, LlmRole.IC_ARBITER}:
            assert config.model == _OPUS_4
            assert config.model_binding_env == _ARBITER_ENV
        else:
            assert config.model == _SONNET_4
        if role in {LlmRole.EXTRACTION, LlmRole.METHODOLOGY_EXTRACTION}:
            assert config.max_output_tokens == _EXTRACTION_MAX_TOKENS
            assert config.model_binding_env == _EXTRACT_ENV
        elif role.value.endswith("_agent"):
            assert config.max_output_tokens == _ANALYSIS_MAX_TOKENS
            assert config.model_binding_env == _DEFAULT_ENV
            assert config.prompt_id is None
        elif role is LlmRole.SCORING:
            assert config.max_output_tokens == _SCORING_MAX_TOKENS
            assert config.model_binding_env == _DEFAULT_ENV
        elif role in {LlmRole.IC_CHALLENGER, LlmRole.IC_ARBITER}:
            assert config.max_output_tokens == _IC_MAX_TOKENS
            assert config.prompt_id is None
            if role is LlmRole.IC_CHALLENGER:
                assert config.model_binding_env == _DEFAULT_ENV
        else:
            assert config.max_output_tokens == _DEBATE_MAX_TOKENS
            if role is not LlmRole.ARBITER:
                assert config.model_binding_env == _DEFAULT_ENV
        if config.prompt_id is not None:
            recorded.add(config.prompt_id)
            assert config.prompt_id == _RECORDED_PROMPT_IDS[role.value]
    assert recorded == set(RUNTIME_PROMPT_IDS)


def test_path_only_roles_pin_prompt_paths_without_registry_ids() -> None:
    document = _admitted_document()
    by_role = {row["role"]: row for row in document["roles"]}
    for role, prompt_path in _PATH_ONLY_PROMPTS.items():
        assert by_role[role]["prompt_id"] is None
        assert by_role[role]["prompt_path"] == prompt_path
        assert by_role[role]["prompt_version"] == _PROMPT_VERSION


def test_selection_helpers_match_pre_slice_and_resolver(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from idis.api.routes.runs import (
        _resolve_analysis_selection,
        _resolve_debate_selection,
        _resolve_extraction_selection,
        _resolve_scoring_selection,
    )

    _clear_model_bindings(monkeypatch)
    monkeypatch.setenv("IDIS_DEBATE_BACKEND", "anthropic")
    monkeypatch.setenv("IDIS_EXTRACT_BACKEND", "anthropic")

    analysis = _resolve_analysis_selection()
    scoring = _resolve_scoring_selection()
    debate = _resolve_debate_selection()
    extraction = _resolve_extraction_selection()
    assert (analysis.backend, analysis.model, analysis.max_tokens) == (
        "anthropic",
        _SONNET_4,
        _ANALYSIS_MAX_TOKENS,
    )
    assert (scoring.backend, scoring.model, scoring.max_tokens) == (
        "anthropic",
        _SONNET_4,
        _SCORING_MAX_TOKENS,
    )
    assert (
        debate.backend,
        debate.default_model,
        debate.arbiter_model,
        debate.max_tokens,
    ) == ("anthropic", _SONNET_4, _OPUS_4, _DEBATE_MAX_TOKENS)
    assert (extraction.backend, extraction.model, extraction.max_tokens) == (
        "anthropic",
        _SONNET_4,
        _EXTRACTION_MAX_TOKENS,
    )

    financial = resolve_role_config(LlmRole.FINANCIAL_AGENT, backend="anthropic")
    advocate = resolve_role_config(LlmRole.ADVOCATE, backend="anthropic")
    arbiter = resolve_role_config(LlmRole.ARBITER, backend="anthropic")
    assert analysis.model == financial.model
    assert analysis.max_tokens == financial.max_output_tokens
    assert scoring.model == resolve_role_config(LlmRole.SCORING, backend="anthropic").model
    assert scoring.max_tokens == _SCORING_MAX_TOKENS
    assert debate.default_model == advocate.model
    assert debate.arbiter_model == arbiter.model
    assert debate.max_tokens == advocate.max_output_tokens
    assert extraction.model == resolve_role_config(LlmRole.EXTRACTION, backend="anthropic").model

    monkeypatch.setenv("IDIS_DEBATE_BACKEND", "deterministic")
    monkeypatch.setenv("IDIS_EXTRACT_BACKEND", "deterministic")
    assert _resolve_analysis_selection().model is None
    assert _resolve_analysis_selection().max_tokens == _ANALYSIS_MAX_TOKENS
    assert _resolve_scoring_selection().model is None
    assert _resolve_scoring_selection().max_tokens == _SCORING_MAX_TOKENS
    detached = _resolve_debate_selection()
    assert detached.default_model is None
    assert detached.arbiter_model is None
    assert detached.max_tokens == _DEBATE_MAX_TOKENS
    assert _resolve_extraction_selection().model is None
    assert _resolve_extraction_selection().max_tokens == _EXTRACTION_MAX_TOKENS


def test_pinned_prompt_hashes_match_on_disk_files() -> None:
    document = _admitted_document()
    for row in document["roles"]:
        config = resolve_role_config(row["role"], backend="anthropic", environ={})
        text = (_REPO / row["prompt_path"]).read_text(encoding="utf-8")
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        assert config.prompt_content_sha256 == digest
        assert row["prompt_content_sha256"] == digest
        assert row["config_sha256"] == role_row_sha256(row)
    assert document["document_sha256"] == admitted_document_sha256(document)


def test_fallback_policy_is_none_for_every_role() -> None:
    for role in LlmRole:
        config = resolve_role_config(role, backend="anthropic", environ={})
        assert config.fallback_policy == "none"


def test_two_resolutions_are_equal_by_value() -> None:
    first = resolve_role_config(LlmRole.SCORING, backend="anthropic", environ={})
    second = resolve_role_config("scoring", backend="anthropic", environ={})
    assert first == second
    assert dataclasses.asdict(first) == dataclasses.asdict(second)


def test_non_anthropic_backend_returns_model_none() -> None:
    for role in LlmRole:
        config = resolve_role_config(role, backend="deterministic", environ={})
        assert config.model is None
        assert config.provider == "anthropic"
        assert config.fallback_policy == "none"


def test_binding_env_overrides_model_without_new_admission() -> None:
    base = resolve_role_config(LlmRole.EXTRACTION, backend="anthropic", environ={})
    overridden = resolve_role_config(
        LlmRole.EXTRACTION,
        backend="anthropic",
        environ={_EXTRACT_ENV: "claude-sonnet-fake"},
    )
    arbiter = resolve_role_config(
        LlmRole.IC_ARBITER,
        backend="anthropic",
        environ={_ARBITER_ENV: "claude-opus-fake"},
    )
    assert overridden.model == "claude-sonnet-fake"
    assert overridden.config_sha256 == base.config_sha256
    assert arbiter.model == "claude-opus-fake"
    assert arbiter.config_sha256 != ""


def test_resolution_does_not_import_anthropic() -> None:
    script = "\n".join(
        (
            "import sys",
            "sys.modules.pop('anthropic', None)",
            "from idis.providers.role_config import resolve_role_config",
            "resolve_role_config('extraction', backend='anthropic', environ={})",
            "sys.exit(0 if 'anthropic' not in sys.modules else 2)",
        )
    )
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=_REPO,
        env=_child_env(),
        capture_output=True,
        check=False,
    )
    assert completed.stdout == b""
    assert completed.returncode == 0, completed.stderr.decode("utf-8", errors="replace")[:400]


def test_generator_reproduces_the_admitted_file(tmp_path: Path) -> None:
    destination = tmp_path / "llm_role_configs_v1.json"
    completed = subprocess.run(
        [
            sys.executable,
            "scripts/generate_llm_role_configs_v1.py",
            "--output",
            str(destination),
        ],
        cwd=_REPO,
        env=_child_env(),
        capture_output=True,
        check=False,
    )
    assert completed.stdout == b""
    assert completed.returncode == 0, completed.stderr.decode("utf-8", errors="replace")[:400]
    assert destination.read_bytes() == _ADMITTED.read_bytes()


def test_admitted_file_stays_out_of_the_governed_contract_lock() -> None:
    from idis.contracts import GOVERNED_CONTRACT_RELATIVE_PATHS

    governed = {path.as_posix() for path in GOVERNED_CONTRACT_RELATIVE_PATHS}
    assert "contracts/llm_role_configs_v1.json" not in governed
    lock_text = (_REPO / "contracts" / "contract_lock.json").read_text(encoding="utf-8")
    assert "llm_role_configs_v1" not in lock_text


def _sentinel_config() -> RoleModelConfig:
    return RoleModelConfig(
        role=LlmRole.EXTRACTION,
        provider="anthropic",
        model=_PATCHED_MODEL,
        model_binding_env=_EXTRACT_ENV,
        effort=None,
        temperature="0",
        max_output_tokens=_PATCHED_MAX_TOKENS,
        prompt_id=None,
        prompt_version=None,
        prompt_content_sha256="ab" * 32,
        budget_component_id="anthropic",
        fallback_policy="none",
        config_sha256="cd" * 32,
    )


def test_builders_pass_resolver_model_and_max_tokens(monkeypatch: pytest.MonkeyPatch) -> None:
    from idis.api.routes import runs as runs_module

    sentinel = _sentinel_config()
    calls: list[tuple[str | None, int]] = []

    def _resolve(role: object, *, backend: str, environ: object = None) -> RoleModelConfig:
        del role, backend, environ
        return sentinel

    def _client(*, model: str | None, max_tokens: int, tenant_id: str | None) -> object:
        del tenant_id
        calls.append((model, max_tokens))
        return object()

    monkeypatch.setattr(runs_module, "resolve_role_config", _resolve)
    monkeypatch.setattr(runs_module, "_new_budgeted_llm_client", _client)
    monkeypatch.setenv("IDIS_EXTRACT_BACKEND", "anthropic")
    monkeypatch.setenv("IDIS_DEBATE_BACKEND", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sentinel-not-used")

    runs_module._build_extraction_llm_client()
    runs_module._build_analysis_llm_client()
    runs_module._build_scoring_llm_client()
    runs_module._build_debate_role_runners()

    assert calls
    assert calls == [(_PATCHED_MODEL, _PATCHED_MAX_TOKENS)] * len(calls)


class _StopAfterRunners(Exception):
    """Stops the Layer-2 route once the live clients have been built."""


def test_ic_route_passes_resolver_model_and_max_tokens(monkeypatch: pytest.MonkeyPatch) -> None:
    from idis.api.routes import runs as runs_module

    sentinel = _sentinel_config()
    calls: list[tuple[str | None, int]] = []

    def _resolve(role: object, *, backend: str, environ: object = None) -> RoleModelConfig:
        del role, backend, environ
        return sentinel

    def _client(*, model: str | None, max_tokens: int, tenant_id: str | None) -> object:
        del tenant_id
        calls.append((model, max_tokens))
        return object()

    def _build(*, challenger_client: object, arbiter_client: object) -> tuple[object, object]:
        del challenger_client, arbiter_client
        raise _StopAfterRunners()

    monkeypatch.setattr(runs_module, "resolve_role_config", _resolve)
    monkeypatch.setattr(runs_module, "_new_budgeted_llm_client", _client)
    monkeypatch.setattr(runs_module, "is_strict_full_live_required", lambda **_kwargs: True)
    monkeypatch.setattr(
        "idis.services.runs.layer2_ic_challenge.build_live_layer2_ic_runners",
        _build,
    )
    monkeypatch.setenv("IDIS_DEBATE_BACKEND", "anthropic")
    with pytest.raises(_StopAfterRunners):
        runs_module._run_full_layer2_ic_challenge(
            run_id="22222222-2222-2222-2222-222222222222",
            tenant_id="11111111-1111-1111-1111-111111111111",
            deal_id="33333333-3333-3333-3333-333333333333",
            debate_summary={"debate_id": "debate-001"},
            created_claim_ids=[],
            calc_ids=[],
            deal_metadata={"stage": "seed"},
        )
    assert calls == [(_PATCHED_MODEL, _PATCHED_MAX_TOKENS), (_PATCHED_MODEL, _PATCHED_MAX_TOKENS)]
