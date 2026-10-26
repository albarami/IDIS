"""GL-05 P6: inactive candidate role document. The active resolver stays on v1."""

from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from idis.contracts import GOVERNED_CONTRACT_RELATIVE_PATHS
from idis.providers.role_config import LlmRole, admitted_config_path, resolve_role_config

_REPO = Path(__file__).resolve().parents[1]
_COMMITTED = _REPO / "contracts" / "llm_role_configs_v2.json"
_GENERATOR = _REPO / "scripts" / "generate_llm_role_configs_v2.py"
_INVALID = "LLM_ROLE_CANDIDATE_DOCUMENT_INVALID"
_OVERWRITE_REFUSED = "LLM_ROLE_CANDIDATE_OVERWRITE_REFUSED"
_EXTRACT_ENV = "IDIS_ANTHROPIC_MODEL_EXTRACT"
_ARBITER_ENV = "IDIS_ANTHROPIC_MODEL_DEBATE_ARBITER"
_CHILD_ENV_KEYS = ("PATH", "SYSTEMROOT", "PATHEXT", "WINDIR", "COMSPEC", "TEMP", "TMP")
_REFERENCE_NEEDLES = ("llm_role_configs_v2", "role_config_v2", "load_candidate_document")
_SONNET = "claude-sonnet-5-5"
_OPUS = "claude-opus-5-5"

_PLAN_TABLE = {
    ("methodology_extraction", _SONNET, "medium", 16384),
    ("market_agent", _SONNET, "high", 16384),
    ("technical_agent", _SONNET, "high", 16384),
    ("sector_specialist_agent", _SONNET, "high", 16384),
    ("team_agent", _SONNET, "medium", 12288),
    ("historian_agent", _SONNET, "medium", 12288),
    ("financial_agent", _OPUS, "high", 32768),
    ("terms_agent", _OPUS, "high", 16384),
    ("risk_officer_agent", _OPUS, "high", 24576),
    ("advocate", _SONNET, "medium", 16384),
    ("sanad_breaker", _SONNET, "high", 16384),
    ("contradiction_finder", _SONNET, "high", 16384),
    ("risk_officer", _OPUS, "high", 24576),
    ("arbiter", _OPUS, "high", 32768),
    ("scoring", _SONNET, "medium", 16384),
    ("ic_challenger", _OPUS, "high", 24576),
    ("ic_arbiter", _OPUS, "high", 32768),
}

# role, plan_role, proposed_model, reason
_UNBOUND = (
    ("extraction", None, None, "NO_PLAN_ROW"),
    (None, "IC advocate", _SONNET, "NOT_A_RUNTIME_ROLE"),
    (None, "assertion-support check", _SONNET, "NOT_A_RUNTIME_ROLE"),
    (None, "Narrative / IC memo drafting", _SONNET, "NOT_A_RUNTIME_ROLE"),
    (
        None,
        "External research through Perplexity Agent API",
        "anthropic/" + _SONNET,
        "NOT_A_RUNTIME_ROLE",
    ),
    (
        None,
        "Same-evidence comparator, later qualified fallback",
        "gpt-6.1-sol",
        "NOT_A_RUNTIME_ROLE",
    ),
    (None, "Later cheap classification candidate", "claude-haiku-5-5", "NOT_A_RUNTIME_ROLE"),
    (None, "Embeddings", "text-embedding-3-small", "NOT_A_RUNTIME_ROLE"),
)


def _child_env() -> dict[str, str]:
    env = {key: os.environ[key] for key in _CHILD_ENV_KEYS if os.environ.get(key)}
    env["PYTHONPATH"] = os.pathsep.join(("src", "."))
    env["PYTHONUTF8"] = "1"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def _document() -> dict[str, Any]:
    assert _COMMITTED.is_file()
    loaded = json.loads(_COMMITTED.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def _copy(document: dict[str, Any]) -> dict[str, Any]:
    cloned = json.loads(json.dumps(document))
    assert isinstance(cloned, dict)
    return cloned


def _other_sha256(current: str) -> str:
    replacement = "ab" * 32
    if replacement == current:
        return "cd" * 32
    return replacement


def _rehash(document: dict[str, Any]) -> dict[str, Any]:
    from idis.providers.role_config_v2 import candidate_document_sha256, candidate_row_sha256

    roles: list[dict[str, Any]] = []
    for row in document["roles"]:
        identity = dict(row)
        identity.pop("config_sha256", None)
        identity["config_sha256"] = candidate_row_sha256(identity)
        roles.append(identity)
    updated = dict(document)
    updated["roles"] = roles
    updated.pop("document_sha256", None)
    updated["document_sha256"] = candidate_document_sha256(updated)
    return updated


def _rehash_document_only(document: dict[str, Any]) -> dict[str, Any]:
    from idis.providers.role_config_v2 import candidate_document_sha256

    updated = dict(document)
    updated.pop("document_sha256", None)
    updated["document_sha256"] = candidate_document_sha256(updated)
    return updated


def _expect_invalid(document: dict[str, Any]) -> None:
    from idis.providers.role_config_v2 import CandidateDocumentError, verify_candidate_document

    with pytest.raises(CandidateDocumentError) as raised:
        verify_candidate_document(document)
    assert raised.value.code == _INVALID
    assert str(raised.value) == _INVALID


def _run_generator(*args: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        [sys.executable, str(_GENERATOR), *args],
        cwd=_REPO,
        env=_child_env(),
        capture_output=True,
        check=False,
    )


def source_modules_referencing_candidate_document(root: Path) -> list[str]:
    """Return src modules other than the verifier that name the candidate document."""
    src = root / "src"
    if not src.is_dir():
        return []
    allowed = (src / "idis" / "providers" / "role_config_v2.py").resolve()
    hits: list[str] = []
    for path in sorted(src.rglob("*.py")):
        if path.resolve() == allowed:
            continue
        text = path.read_text(encoding="utf-8")
        if _text_names_candidate_document(text) or _ast_names_candidate_document(text):
            hits.append(path.relative_to(root).as_posix())
    return hits


def _text_names_candidate_document(text: str) -> bool:
    return any(needle in text for needle in _REFERENCE_NEEDLES)


def _name_has_needle(name: str) -> bool:
    return any(needle in name for needle in _REFERENCE_NEEDLES)


def _ast_names_candidate_document(text: str) -> bool:
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return False
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if any(_name_has_needle(alias.name) for alias in node.names):
                return True
        elif isinstance(node, ast.ImportFrom):
            if node.module is not None and _name_has_needle(node.module):
                return True
            if any(_name_has_needle(alias.name) for alias in node.names):
                return True
        elif (isinstance(node, ast.Attribute) and _name_has_needle(node.attr)) or (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and _text_names_candidate_document(node.value)
        ):
            return True
    return False


def test_generated_candidate_document_verifies() -> None:
    """The committed file loads through the candidate verifier and stays inactive."""
    assert _COMMITTED.is_file()
    from idis.providers.role_config_v2 import load_candidate_document

    view = load_candidate_document()
    assert view.document_version == "llm-role-configs-v2"
    assert view.generator == "scripts/generate_llm_role_configs_v2.py"
    assert view.qualification_status == "CANDIDATE_NOT_QUALIFIED"
    assert view.activation == "INACTIVE"
    assert isinstance(view.roles, tuple)
    assert isinstance(view.unbound_plan_roles, tuple)
    for role in view.roles:
        assert role.provider == "anthropic"
        assert role.request_shape_id == "candidate-5-5-v1"
        assert role.thinking_mode == "omitted"
        assert role.sampling_policy == "provider-default-no-sampling-fields-v1"
        assert role.fallback_policy == "none"
        assert role.qualification_status == "CANDIDATE_NOT_QUALIFIED"
        assert role.budget_component_id == "anthropic"


def test_regenerating_into_a_temporary_directory_reproduces_the_file(tmp_path: Path) -> None:
    """A second run into an empty path matches the committed bytes."""
    assert _COMMITTED.is_file()
    destination = tmp_path / "llm_role_configs_v2.json"
    completed = _run_generator("--output", str(destination))
    assert completed.stdout == b""
    assert completed.returncode == 0, completed.stderr.decode("utf-8", errors="replace")[:400]
    assert destination.read_bytes() == _COMMITTED.read_bytes()


def test_generator_refuses_to_overwrite_a_different_file(tmp_path: Path) -> None:
    """A different existing file is left untouched unless --force is set."""
    assert _COMMITTED.is_file()
    destination = tmp_path / "llm_role_configs_v2.json"
    destination.write_bytes(b"{}\n")
    refused = _run_generator("--output", str(destination))
    assert refused.returncode == 1
    assert refused.stderr == f"{_OVERWRITE_REFUSED}\n".encode()
    assert destination.read_bytes() == b"{}\n"
    forced = _run_generator("--output", str(destination), "--force")
    assert forced.returncode == 0
    assert destination.read_bytes() == _COMMITTED.read_bytes()


def test_plan_table_pin_matches_the_proposal() -> None:
    """Role, model, effort, and ceiling are the plan proposal and need a test edit to change."""
    from idis.providers.role_config_v2 import load_candidate_document

    view = load_candidate_document()
    actual = {(row.role.value, row.model, row.effort, row.max_output_tokens) for row in view.roles}
    assert actual == _PLAN_TABLE


def test_unbound_plan_roles_are_pinned() -> None:
    """Runtime roles without a plan row, and plan rows without a runtime role, stay listed."""
    from idis.providers.role_config_v2 import load_candidate_document

    view = load_candidate_document()
    actual = tuple(
        (row.role, row.plan_role, row.proposed_model, row.reason) for row in view.unbound_plan_roles
    )
    assert actual == _UNBOUND
    assert LlmRole.EXTRACTION.value not in {row.role.value for row in view.roles}


def _mutate_unsupported_effort(document: dict[str, Any]) -> dict[str, Any]:
    document["roles"][0]["effort"] = "ultra"
    return _rehash(document)


def _mutate_shape_id(document: dict[str, Any]) -> dict[str, Any]:
    document["roles"][0]["request_shape_id"] = "legacy-sampling-v1"
    return _rehash(document)


def _mutate_price_hash(document: dict[str, Any]) -> dict[str, Any]:
    current = document["roles"][0]["price_row_sha256"]
    document["roles"][0]["price_row_sha256"] = _other_sha256(current)
    return _rehash(document)


def _mutate_prompt_pin(document: dict[str, Any]) -> dict[str, Any]:
    current = document["roles"][0]["prompt_content_sha256"]
    document["roles"][0]["prompt_content_sha256"] = _other_sha256(current)
    return _rehash(document)


def _mutate_duplicate_role(document: dict[str, Any]) -> dict[str, Any]:
    document["roles"].append(dict(document["roles"][0]))
    return _rehash(document)


def _mutate_role_in_both_lists(document: dict[str, Any]) -> dict[str, Any]:
    document["unbound_plan_roles"].append(
        {"role": document["roles"][0]["role"], "reason": "NO_PLAN_ROW"}
    )
    return _rehash(document)


def _mutate_missing_field(document: dict[str, Any]) -> dict[str, Any]:
    del document["roles"][0]["effort"]
    return document


def _mutate_extra_field(document: dict[str, Any]) -> dict[str, Any]:
    document["roles"][0]["temperature"] = "0"
    return document


def _mutate_row_hash(document: dict[str, Any]) -> dict[str, Any]:
    document["roles"][0]["config_sha256"] = _other_sha256(document["roles"][0]["config_sha256"])
    return _rehash_document_only(document)


def _mutate_document_hash(document: dict[str, Any]) -> dict[str, Any]:
    document["document_sha256"] = _other_sha256(document["document_sha256"])
    return document


def _mutate_fallback_policy(document: dict[str, Any]) -> dict[str, Any]:
    document["roles"][0]["fallback_policy"] = "block"
    return _rehash(document)


def _mutate_activation(document: dict[str, Any]) -> dict[str, Any]:
    document["activation"] = "ACTIVE"
    return _rehash(document)


@pytest.mark.parametrize(
    "mutate",
    [
        _mutate_unsupported_effort,
        _mutate_shape_id,
        _mutate_price_hash,
        _mutate_prompt_pin,
        _mutate_duplicate_role,
        _mutate_role_in_both_lists,
        _mutate_missing_field,
        _mutate_extra_field,
        _mutate_row_hash,
        _mutate_document_hash,
        _mutate_fallback_policy,
        _mutate_activation,
    ],
)
def test_mutation_is_refused(mutate: Callable[[dict[str, Any]], dict[str, Any]]) -> None:
    """Each listed drift is refused with the candidate-document code."""
    _expect_invalid(mutate(_copy(_document())))


def test_ceiling_plus_input_allowance_must_fit_the_price_window() -> None:
    """Output plus the stated input allowance may meet the window and may not pass it."""
    from idis.models.anthropic_price_catalog_v2 import resolve_candidate_price
    from idis.providers.role_config_v2 import (
        CANDIDATE_INPUT_TOKEN_ALLOWANCE,
        verify_candidate_document,
    )

    document = _copy(_document())
    model = document["roles"][0]["model"]
    window = resolve_candidate_price(model).context_window_limit
    document["roles"][0]["max_output_tokens"] = window - CANDIDATE_INPUT_TOKEN_ALLOWANCE
    verify_candidate_document(_rehash(document))
    document["roles"][0]["max_output_tokens"] = window - CANDIDATE_INPUT_TOKEN_ALLOWANCE + 1
    _expect_invalid(_rehash(document))


def test_active_resolver_still_returns_v1_rows() -> None:
    """Importing the candidate module leaves admission on the v1 file."""
    assert _COMMITTED.is_file()
    from idis.providers.role_config_v2 import load_candidate_document

    load_candidate_document()
    path = admitted_config_path()
    assert path.as_posix().endswith("contracts/llm_role_configs_v1.json")
    admitted = json.loads(path.read_text(encoding="utf-8"))
    assert {row["role"] for row in admitted["roles"]} == {role.value for role in LlmRole}
    for row in admitted["roles"]:
        resolved = resolve_role_config(row["role"], backend="anthropic", environ={})
        assert resolved.model == row["model"]
        assert resolved.max_output_tokens == row["max_output_tokens"]
        assert resolved.effort is None
        assert resolved.temperature == "0"
        assert resolved.config_sha256 == row["config_sha256"]
        assert resolved.prompt_id == row["prompt_id"]
        assert resolved.prompt_version == row["prompt_version"]
        assert resolved.prompt_content_sha256 == row["prompt_content_sha256"]
        assert resolved.fallback_policy == "none"
        assert resolved.budget_component_id == "anthropic"
        assert resolved.provider == "anthropic"


def test_binding_env_overrides_model_without_new_admission() -> None:
    """A binding variable still replaces the admitted model and keeps the v1 row hash."""
    assert _COMMITTED.is_file()
    from idis.providers.role_config_v2 import load_candidate_document

    load_candidate_document()
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


def test_candidate_document_is_not_referenced_or_locked() -> None:
    """No other src module names v2, and the contract lock does not list the file."""
    assert source_modules_referencing_candidate_document(_REPO) == []
    governed = {path.as_posix() for path in GOVERNED_CONTRACT_RELATIVE_PATHS}
    assert "contracts/llm_role_configs_v2.json" not in governed
    lock_text = (_REPO / "contracts" / "contract_lock.json").read_text(encoding="utf-8")
    assert "llm_role_configs_v2" not in lock_text
