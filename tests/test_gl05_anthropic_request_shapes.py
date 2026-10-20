"""GL-05 slice 3: request shape selected by exact model id.

Legacy ids and unknown ids keep today's sampling request. The two candidate ids
use the candidate shape. This module does not activate either candidate.
"""

from __future__ import annotations

import importlib
import importlib.util
import inspect
import json
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import anthropic
import httpx
import httpx2
import pytest

from idis.services.extraction.extractors import anthropic_client

_http = (
    httpx2
    if "httpx2" in str(inspect.signature(anthropic.Anthropic).parameters["http_client"].annotation)
    else httpx
)

_OFFLINE_KEY = "cir01-offline-test-key"
_PROMPT = "Synthetic request only"
_JSON_SYSTEM = (
    "You MUST respond with valid JSON only. No markdown, no explanation, "
    "no code fences. Output raw JSON."
)
_MARKER = "gl05-shape-marker"
_EFFORTS = ("low", "medium", "high", "xhigh", "max")
_BETWEEN_TOOLS_EFFORTS = ("low", "medium", "high")
_LEGACY_IDS = ("claude-sonnet-4-20250514", "claude-opus-4-20250514")
_CANDIDATE_IDS = ("claude-sonnet-5-5", "claude-opus-5-5")
_SRC = Path(__file__).resolve().parents[1] / "src"
_CAPABILITIES = _SRC / "idis" / "providers" / "anthropic_capabilities.py"
_ENV_PRUNE_DIRS = (
    ".git",
    ".venv",
    "venv",
    "node_modules",
    ".local_reports",
    "__pycache__",
    ".mypy_cache",
    ".ruff_cache",
    ".pytest_cache",
    ".claude",
)


def _capabilities() -> Any:
    spec = importlib.util.find_spec("idis.providers.anthropic_capabilities")
    assert spec is not None
    return importlib.import_module("idis.providers.anthropic_capabilities")


def _assert_rejected(exc: BaseException, reason: str, *hidden: str) -> None:
    assert type(exc) is not RuntimeError
    assert isinstance(exc, RuntimeError)
    assert getattr(exc, "code", None) == "LLM_REQUEST_SHAPE_REJECTED"
    assert getattr(exc, "reason", None) == reason
    assert str(exc) == "LLM_REQUEST_SHAPE_REJECTED"
    for item in hidden:
        assert item not in str(exc)
        assert item not in repr(exc)
        assert item not in str(getattr(exc, "reason", ""))


def _user(content: str = _PROMPT) -> dict[str, str]:
    return {"role": "user", "content": content}


def _legacy_body(model: str, *, json_mode: bool) -> dict[str, object]:
    return {
        "model": model,
        "max_tokens": 173,
        "temperature": 0,
        "messages": [_user()],
        "system": _JSON_SYSTEM if json_mode else "",
    }


def _candidate_body(model: str, effort: str, *, json_mode: bool) -> dict[str, object]:
    body: dict[str, object] = {
        "model": model,
        "max_tokens": 173,
        "messages": [_user()],
        "output_config": {"effort": effort},
    }
    if json_mode:
        body["system"] = _JSON_SYSTEM
    return body


def _stream_ok() -> httpx.Response | httpx2.Response:
    """SSE completion whose text matches the JSON fixture used by ``_ok``."""
    events: list[tuple[str, dict[str, Any]]] = [
        (
            "message_start",
            {
                "type": "message_start",
                "message": {
                    "id": "msg_gl05_shape",
                    "type": "message",
                    "role": "assistant",
                    "model": "cir01-observed-model",
                    "content": [],
                    "stop_reason": None,
                    "stop_sequence": None,
                    "usage": {"input_tokens": 3, "output_tokens": 1},
                },
            },
        ),
        (
            "content_block_start",
            {
                "type": "content_block_start",
                "index": 0,
                "content_block": {"type": "text", "text": ""},
            },
        ),
        (
            "content_block_delta",
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "text_delta", "text": '{"ok":true}'},
            },
        ),
        ("content_block_stop", {"type": "content_block_stop", "index": 0}),
        (
            "message_delta",
            {
                "type": "message_delta",
                "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                "usage": {"output_tokens": 4},
            },
        ),
        ("message_stop", {"type": "message_stop"}),
    ]
    payload = "".join(
        f"event: {name}\ndata: {json.dumps(event, separators=(',', ':'))}\n\n"
        for name, event in events
    )
    return _http.Response(
        200,
        content=payload.encode(),
        headers={"content-type": "text/event-stream"},
    )


def _ok() -> httpx.Response | httpx2.Response:
    return _http.Response(
        200,
        json={
            "id": "msg_gl05_shape",
            "type": "message",
            "role": "assistant",
            "model": "cir01-observed-model",
            "content": [{"type": "text", "text": '{"ok":true}'}],
            "stop_reason": "end_turn",
            "stop_sequence": None,
            "usage": {"input_tokens": 3, "output_tokens": 4},
        },
    )


def _capture(
    monkeypatch: pytest.MonkeyPatch,
    *,
    model: str | None,
    json_mode: bool,
    effort: str | None = None,
    max_tokens: int | None = 173,
    prompt: str = _PROMPT,
) -> dict[str, Any]:
    requests: list[httpx.Request | httpx2.Request] = []

    def handle(request: httpx.Request | httpx2.Request) -> httpx.Response | httpx2.Response:
        requests.append(request)
        body = json.loads(request.content)
        if isinstance(body, dict) and body.get("stream") is True:
            return _stream_ok()
        return _ok()

    monkeypatch.setenv("ANTHROPIC_API_KEY", _OFFLINE_KEY)
    kwargs: dict[str, Any] = {}
    if model is not None:
        kwargs["model"] = model
    if max_tokens is not None:
        kwargs["max_tokens"] = max_tokens
    if effort is not None:
        kwargs["effort"] = effort
    client = anthropic_client.AnthropicLLMClient(**kwargs)
    client._client.close()
    try:
        with anthropic.Anthropic(
            api_key=_OFFLINE_KEY,
            http_client=_http.Client(transport=_http.MockTransport(handle)),
            max_retries=0,
        ) as sdk_client:
            client._client = sdk_client
            assert client.call(prompt, json_mode=json_mode) == '{"ok":true}'
    finally:
        client._client.close()
    assert len(requests) == 1
    payload = json.loads(requests[0].content)
    assert isinstance(payload, dict)
    return payload


@pytest.mark.parametrize("model", (*_LEGACY_IDS, "cir01-test-model"))
@pytest.mark.parametrize("json_mode", [False, True])
def test_legacy_and_unknown_ids_keep_the_cir01_sampling_body(
    monkeypatch: pytest.MonkeyPatch, model: str, json_mode: bool
) -> None:
    payload = _capture(monkeypatch, model=model, json_mode=json_mode)
    assert payload == _legacy_body(model, json_mode=json_mode)


def test_default_model_chain_keeps_the_legacy_sampling_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("IDIS_ANTHROPIC_MODEL_EXTRACT", raising=False)
    payload = _capture(monkeypatch, model=None, json_mode=False, max_tokens=None)
    assert payload["model"] == "claude-sonnet-4-20250514"
    assert payload["temperature"] == 0
    assert payload["system"] == ""
    assert "output_config" not in payload
    assert "thinking" not in payload


@pytest.mark.parametrize("model", _CANDIDATE_IDS)
@pytest.mark.parametrize("effort", _EFFORTS)
def test_candidate_body_sends_explicit_effort_and_omits_sampling(
    monkeypatch: pytest.MonkeyPatch, model: str, effort: str
) -> None:
    payload = _capture(monkeypatch, model=model, json_mode=False, effort=effort)
    assert payload == {**_candidate_body(model, effort, json_mode=False), "stream": True}
    assert "temperature" not in payload
    assert "thinking" not in payload
    assert "system" not in payload
    assert "top_p" not in payload
    assert "top_k" not in payload
    assert "tool_choice" not in payload


@pytest.mark.parametrize(
    ("model", "effort"),
    [("claude-sonnet-5-5", "medium"), ("claude-opus-5-5", "high")],
)
def test_candidate_json_mode_keeps_the_system_sentence(
    monkeypatch: pytest.MonkeyPatch, model: str, effort: str
) -> None:
    payload = _capture(monkeypatch, model=model, json_mode=True, effort=effort)
    assert payload == {**_candidate_body(model, effort, json_mode=True), "stream": True}
    assert "temperature" not in payload
    assert "thinking" not in payload


@pytest.mark.parametrize("model", _CANDIDATE_IDS)
def test_candidate_without_effort_fails_before_the_sdk_client(
    monkeypatch: pytest.MonkeyPatch, model: str
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", _OFFLINE_KEY)
    built: list[int] = []
    original = anthropic_client.anthropic.Anthropic

    def spy(*args: object, **kwargs: object) -> anthropic.Anthropic:
        built.append(1)
        return original(*args, **kwargs)

    monkeypatch.setattr(anthropic_client.anthropic, "Anthropic", spy)
    client: anthropic_client.AnthropicLLMClient | None = None
    try:
        with pytest.raises(RuntimeError) as caught:
            client = anthropic_client.AnthropicLLMClient(model=model)
        assert built == []
        _assert_rejected(caught.value, "effort_required")
    finally:
        if client is not None:
            client._client.close()


def test_env_candidate_without_effort_fails_before_the_sdk_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", _OFFLINE_KEY)
    monkeypatch.setenv("IDIS_ANTHROPIC_MODEL_EXTRACT", "claude-sonnet-5-5")
    built: list[int] = []
    original = anthropic_client.anthropic.Anthropic

    def spy(*args: object, **kwargs: object) -> anthropic.Anthropic:
        built.append(1)
        return original(*args, **kwargs)

    monkeypatch.setattr(anthropic_client.anthropic, "Anthropic", spy)
    client: anthropic_client.AnthropicLLMClient | None = None
    try:
        with pytest.raises(RuntimeError) as caught:
            client = anthropic_client.AnthropicLLMClient()
        assert built == []
        _assert_rejected(caught.value, "effort_required")
    finally:
        if client is not None:
            client._client.close()


def test_candidate_with_effort_still_requires_the_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(ValueError, match="ANTHROPIC_API_KEY"):
        anthropic_client.AnthropicLLMClient(model="claude-sonnet-5-5", effort="medium")


@pytest.mark.parametrize("model", (*_LEGACY_IDS, "not-a-known-model"))
@pytest.mark.parametrize("effort", ("high", "low"))
def test_effort_on_legacy_or_unknown_id_is_rejected_at_construction(
    monkeypatch: pytest.MonkeyPatch, model: str, effort: str
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", _OFFLINE_KEY)
    built: list[int] = []
    original = anthropic_client.anthropic.Anthropic

    def spy(*args: object, **kwargs: object) -> anthropic.Anthropic:
        built.append(1)
        return original(*args, **kwargs)

    monkeypatch.setattr(anthropic_client.anthropic, "Anthropic", spy)
    client: anthropic_client.AnthropicLLMClient | None = None
    try:
        with pytest.raises(RuntimeError) as caught:
            client = anthropic_client.AnthropicLLMClient(model=model, effort=effort)
        assert built == []
        _assert_rejected(caught.value, "effort_not_applicable", effort)
    finally:
        if client is not None:
            client._client.close()


@pytest.mark.parametrize("effort", ("", "HIGH", "minimal", "default"))
def test_effort_outside_the_candidate_set_is_rejected_at_construction(
    monkeypatch: pytest.MonkeyPatch, effort: str
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", _OFFLINE_KEY)
    with pytest.raises(RuntimeError) as caught:
        anthropic_client.AnthropicLLMClient(model="claude-opus-5-5", effort=effort)
    hidden = () if effort == "" else (effort,)
    _assert_rejected(caught.value, "effort_unsupported", *hidden)


def test_table_rows_are_unique_and_candidates_are_not_qualified() -> None:
    caps = _capabilities()
    rows = caps.MODEL_CAPABILITIES
    ids = [row.model_id for row in rows]
    assert ids == [
        "claude-sonnet-4-20250514",
        "claude-opus-4-20250514",
        "claude-sonnet-5-5",
        "claude-opus-5-5",
    ]
    assert len(ids) == len(set(ids))
    assert {row.shape_id for row in rows} == {"legacy-sampling-v1", "candidate-5-5-v1"}
    assert set(caps.ThinkingPolicy) == {caps.ThinkingPolicy.OMIT, caps.ThinkingPolicy.ADAPTIVE}
    by_id = {row.model_id: row for row in rows}
    for model_id in _LEGACY_IDS:
        row = by_id[model_id]
        assert row.shape_id == "legacy-sampling-v1"
        assert row.supports_sampling_controls is True
        assert row.supported_efforts == frozenset()
        assert row.default_effort_known is None
        assert row.thinking_policy == "OMIT"
        assert row.between_tools_allowed is False
        assert row.forced_tool_choice_allowed is False
        assert row.qualification_status == "ADMITTED_V1"
    sonnet = by_id["claude-sonnet-5-5"]
    opus = by_id["claude-opus-5-5"]
    for row in (sonnet, opus):
        assert row.shape_id == "candidate-5-5-v1"
        assert row.supports_sampling_controls is False
        assert row.supported_efforts == frozenset(_EFFORTS)
        assert row.thinking_policy == "OMIT"
        assert row.forced_tool_choice_allowed is False
        assert row.qualification_status == "CANDIDATE_NOT_QUALIFIED"
    assert sonnet.default_effort_known == "high"
    assert sonnet.between_tools_allowed is True
    assert opus.default_effort_known == "medium"
    assert opus.between_tools_allowed is False
    assert all(row.thinking_policy == "OMIT" for row in rows)


def test_unknown_id_resolves_to_the_legacy_shape_without_admission() -> None:
    caps = _capabilities()
    row = caps.resolve_model_capability("cir01-test-model")
    assert row.model_id == "cir01-test-model"
    assert row.shape_id == "legacy-sampling-v1"
    assert row.supports_sampling_controls is True
    assert row.supported_efforts == frozenset()
    assert row.default_effort_known is None
    assert row.thinking_policy == "OMIT"
    assert row.between_tools_allowed is False
    assert row.forced_tool_choice_allowed is False
    assert row.qualification_status == "UNKNOWN_ID_LEGACY_BOUND"
    assert "cir01-test-model" not in {item.model_id for item in caps.MODEL_CAPABILITIES}


def test_known_candidate_default_effort_is_not_applied_when_omitted() -> None:
    caps = _capabilities()
    messages = [_user()]
    for model_id, known in (("claude-sonnet-5-5", "high"), ("claude-opus-5-5", "medium")):
        assert caps.resolve_model_capability(model_id).default_effort_known == known
        with pytest.raises(RuntimeError) as caught:
            caps.build_request(
                model=model_id,
                max_tokens=173,
                system="",
                messages=messages,
                effort=None,
            )
        _assert_rejected(caught.value, "effort_required", known)


def test_candidate_ids_are_named_only_by_the_capability_table() -> None:
    assert _candidate_id_hits(_SRC.parent) == []
    assert _CAPABILITIES.is_file()
    table = _CAPABILITIES.read_text(encoding="utf-8")
    assert "claude-sonnet-5-5" in table
    assert "claude-opus-5-5" in table


def test_widened_candidate_scan_fails_when_a_contract_names_a_candidate(
    tmp_path: Path,
) -> None:
    """A contracts file that names a candidate id fails the widened scan.

    A real dotenv and a non-template ``.env*`` file are not opened. An
    environment template is.
    """
    contracts = tmp_path / "contracts"
    contracts.mkdir()
    (contracts / "sample.json").write_text('{"model": "claude-sonnet-5-5"}\n', encoding="utf-8")
    (tmp_path / ".env").write_text("MODEL=claude-opus-5-5\n", encoding="utf-8")
    (tmp_path / ".env.local").write_text("MODEL=claude-opus-5-5\n", encoding="utf-8")
    (tmp_path / ".env.example").write_text("MODEL=legacy\n", encoding="utf-8")
    assert _candidate_id_hits(tmp_path) == ["contracts/sample.json"]
    (tmp_path / ".env.example").write_text("MODEL=claude-opus-5-5\n", encoding="utf-8")
    assert _candidate_id_hits(tmp_path) == [".env.example", "contracts/sample.json"]


def test_client_file_that_names_a_candidate_is_reported(tmp_path: Path) -> None:
    """The client module is outside the allow-list. The capability table stays inside it."""
    caps = tmp_path / "src" / "idis" / "providers" / "anthropic_capabilities.py"
    caps.parent.mkdir(parents=True)
    caps.write_text('MODEL_ID = "claude-opus-5-5"\n', encoding="utf-8")
    client = (
        tmp_path / "src" / "idis" / "services" / "extraction" / "extractors" / "anthropic_client.py"
    )
    client.parent.mkdir(parents=True)
    client.write_text('MODEL_ID = "claude-sonnet-5-5"\n', encoding="utf-8")
    assert _candidate_id_hits(tmp_path) == [
        "src/idis/services/extraction/extractors/anthropic_client.py"
    ]


def test_environment_template_filter_reports_sample_and_nested_templates(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """`.env.sample` and a nested template are reported. Sealed dotenv files are never read."""
    (tmp_path / ".env.sample").write_text("MODEL=claude-sonnet-5-5\n", encoding="utf-8")
    nested = tmp_path / "infra" / "app"
    nested.mkdir(parents=True)
    (nested / ".env.example").write_text("MODEL=claude-opus-5-5\n", encoding="utf-8")
    sealed = (
        tmp_path / ".env",
        tmp_path / ".env.local",
        tmp_path / "staging.env.local",
    )
    for path in sealed:
        path.write_text("MODEL=claude-sonnet-5-5\n", encoding="utf-8")
    for dirname in _ENV_PRUNE_DIRS:
        folder = tmp_path / dirname
        folder.mkdir()
        (folder / ".env.sample").write_text("MODEL=claude-opus-5-5\n", encoding="utf-8")

    reads: list[str] = []
    original = Path.read_text

    def tracking(self: Path, *args: Any, **kwargs: Any) -> str:
        reads.append(self.resolve().as_posix())
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", tracking)
    hits = _candidate_id_hits(tmp_path)
    assert hits == [".env.sample", "infra/app/.env.example"]
    sealed_resolved = {path.resolve().as_posix() for path in sealed}
    assert sealed_resolved.isdisjoint(reads)
    listed = {path.resolve() for path in _widened_candidate_paths(tmp_path)}
    assert all(path.resolve() not in listed for path in sealed)


_ENV_TEMPLATE_NAMES = (
    ".env.example",
    ".env.sample",
    ".env.template",
    "staging.env.example",
    ".env.local.example",
    ".env.production.example",
)
_SEALED_ENV_NAMES = (
    ".env",
    ".env.local",
    ".env.production",
    ".env.development",
    ".env.test",
    ".env.staging",
    ".envrc",
    "prod.env",
    "staging.env.local",
    "secrets.env",
)
_INFRA_SEALED_NAMES = (
    ".env.production",
    ".envrc",
    "prod.env",
    "terraform.tfvars",
    "server.pem",
    "deploy.key",
    "id_rsa",
    "kubeconfig",
    "credentials.json",
    "secrets.env",
)
_DENY_LIST_WOULD_OPEN = (".env.production", ".envrc", "prod.env")
_CANDIDATE_ENV_LINE = "MODEL=claude-sonnet-5-5\n"


def _slice4b_deny_list_template_name(name: str) -> bool:
    """Slice 4b deny-list, kept only so the allow-list proof can name that rule."""
    if name == ".env" or name == ".env.local" or name.endswith(".local"):
        return False
    if name.startswith(".env"):
        return True
    return name.endswith((".env.example", ".env.sample", ".env.template", ".env"))


def test_environment_template_allow_list_reports_templates_and_never_opens_sealed_names(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Allow-listed templates are reported. Sealed names are never opened.

    The slice 4b deny-list selects ``.env.production``, ``.envrc``, and
    ``prod.env``. A template under ``.claude/worktrees/`` is not reported.
    """
    for name in _DENY_LIST_WOULD_OPEN:
        assert _slice4b_deny_list_template_name(name) is True

    subdirectory = tmp_path / "nested"
    subdirectory.mkdir()
    claude_template = tmp_path / ".claude" / "worktrees" / "x" / ".env.example"
    claude_template.parent.mkdir(parents=True)
    claude_template.write_text(_CANDIDATE_ENV_LINE, encoding="utf-8")
    template_paths: list[Path] = []
    sealed_paths: list[Path] = []
    for directory in (tmp_path, subdirectory):
        for name in _ENV_TEMPLATE_NAMES:
            path = directory / name
            path.write_text(_CANDIDATE_ENV_LINE, encoding="utf-8")
            template_paths.append(path)
        for name in _SEALED_ENV_NAMES:
            path = directory / name
            path.write_text(_CANDIDATE_ENV_LINE, encoding="utf-8")
            sealed_paths.append(path)

    reads: list[str] = []
    original = Path.read_text

    def tracking(self: Path, *args: Any, **kwargs: Any) -> str:
        reads.append(self.resolve().as_posix())
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", tracking)
    hits = _candidate_id_hits(tmp_path)
    expected = sorted(path.relative_to(tmp_path).as_posix() for path in template_paths)
    assert hits == expected
    sealed_resolved = {path.resolve().as_posix() for path in sealed_paths}
    assert sealed_resolved.isdisjoint(reads)
    claude_rel = claude_template.relative_to(tmp_path).as_posix()
    assert claude_rel not in hits
    assert claude_template.resolve().as_posix() not in reads


def test_infrastructure_walks_never_open_sealed_names(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Sealed names under deploy, k8s, and infra are never opened.

    ``infra/app/values.yaml`` and ``infra/.env.example`` are read and reported.
    The widened listing contains none of the sealed names.
    """
    model_id = _CANDIDATE_IDS[0]
    sealed_paths: list[Path] = []
    for folder in (tmp_path / "infra", tmp_path / "deploy" / "app", tmp_path / "k8s"):
        folder.mkdir(parents=True, exist_ok=True)
        for name in _INFRA_SEALED_NAMES:
            path = folder / name
            path.write_text(model_id, encoding="utf-8")
            sealed_paths.append(path)
    values = tmp_path / "infra" / "app" / "values.yaml"
    values.parent.mkdir(parents=True, exist_ok=True)
    values.write_text(model_id, encoding="utf-8")
    template = tmp_path / "infra" / ".env.example"
    template.write_text(model_id, encoding="utf-8")

    reads: list[str] = []
    original = Path.read_text

    def tracking(self: Path, *args: Any, **kwargs: Any) -> str:
        reads.append(self.resolve().as_posix())
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", tracking)
    hits = _candidate_id_hits(tmp_path)
    listed = _widened_candidate_paths(tmp_path)
    opened = sorted(
        path.relative_to(tmp_path).as_posix()
        for path in sealed_paths
        if path.resolve().as_posix() in reads
    )
    assert opened == []
    assert hits == ["infra/.env.example", "infra/app/values.yaml"]
    assert template.resolve().as_posix() in reads
    assert values.resolve().as_posix() in reads
    listed_sealed = sorted(
        path.relative_to(tmp_path).as_posix() for path in listed if path.name in _INFRA_SEALED_NAMES
    )
    assert listed_sealed == []


def _candidate_id_hits(root: Path) -> list[str]:
    hits: list[str] = []
    src = root / "src"
    allowed = {(src / "idis" / "providers" / "anthropic_capabilities.py").resolve()}
    if src.is_dir():
        for path in src.rglob("*.py"):
            if path.resolve() not in allowed and _names_candidate(path):
                hits.append(path.relative_to(root).as_posix())
    for path in _widened_candidate_paths(root):
        if _names_candidate(path):
            hits.append(path.relative_to(root).as_posix())
    return sorted(hits)


_PRUNED_SCAN_DIRS = frozenset(
    {
        ".git",
        ".venv",
        "venv",
        "node_modules",
        ".local_reports",
        "__pycache__",
        ".mypy_cache",
        ".ruff_cache",
        ".pytest_cache",
        ".claude",
    }
)
_ENV_TEMPLATE_ENDINGS = (".example", ".sample", ".template")
_NEVER_OPEN_SUFFIXES = (".pem", ".key", ".p12", ".pfx", ".tfvars", ".tfstate")
_NEVER_OPEN_PREFIXES = ("id_rsa", "kubeconfig", "credentials")


def _widened_candidate_paths(root: Path) -> list[Path]:
    collected: list[Path] = []
    collected.extend(path for path in root.glob("contracts/*.json") if path.is_file())
    collected.extend(_environment_templates(root))
    collected.extend(path for path in root.glob("docker-compose*.yml") if path.is_file())
    workflows = root / ".github" / "workflows"
    collected.extend(path for path in workflows.glob("*.yml") if path.is_file())
    for folder in ("deploy", "k8s", "infra"):
        base = root / folder
        if base.is_dir():
            collected.extend(path for path in base.rglob("*") if path.is_file())
    scripts = root / "scripts"
    if scripts.is_dir():
        collected.extend(path for path in scripts.rglob("*.py") if path.is_file())
    unique: list[Path] = []
    seen: set[Path] = set()
    for path in collected:
        if not _may_open(path):
            continue
        resolved = path.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        unique.append(path)
    return unique


def _environment_templates(root: Path) -> list[Path]:
    templates: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [name for name in dirnames if name not in _PRUNED_SCAN_DIRS]
        for filename in filenames:
            if _is_environment_template_name(filename):
                templates.append(Path(dirpath) / filename)
    return templates


def _is_environment_template_name(name: str) -> bool:
    folded = name.casefold()
    return ".env" in folded and folded.endswith(_ENV_TEMPLATE_ENDINGS)


def _may_open(path: Path) -> bool:
    """Return whether the candidate-id scan may open this file.

    Rules use the case-folded name. A name containing ``.env`` is opened only
    when it is an environment template. Key, certificate, terraform, and
    credential names are never opened, and neither is a name ending in
    ``.local``.
    """
    folded = path.name.casefold()
    if ".env" in folded and not _is_environment_template_name(folded):
        return False
    if folded.endswith(_NEVER_OPEN_SUFFIXES):
        return False
    if folded.startswith(_NEVER_OPEN_PREFIXES):
        return False
    return not folded.endswith(".local")


def _names_candidate(path: Path) -> bool:
    if not _may_open(path):
        return False
    text = path.read_text(encoding="utf-8")
    return any(needle in text for needle in _CANDIDATE_IDS)


@pytest.mark.parametrize(
    ("thinking", "reason"),
    [
        ("disabled", "thinking_disabled"),
        ({"type": "disabled"}, "thinking_disabled"),
        ("enabled", "thinking_enabled"),
        ({"type": "enabled", "budget_tokens": 2048}, "thinking_enabled"),
        ("between_tools", "thinking_between_tools_not_allowed"),
        ({"type": "between_tools"}, "thinking_between_tools_not_allowed"),
        ("adaptive", "thinking_unsupported"),
        ({"type": "adaptive"}, "thinking_unsupported"),
    ],
)
def test_legacy_shape_rejects_thinking_overrides(thinking: object, reason: str) -> None:
    caps = _capabilities()
    with pytest.raises(RuntimeError) as caught:
        caps.build_request(
            model="claude-sonnet-4-20250514",
            max_tokens=173,
            system="",
            messages=[_user()],
            thinking=thinking,
        )
    hidden = ("2048",) if reason == "thinking_enabled" else ()
    _assert_rejected(caught.value, reason, *hidden)


@pytest.mark.parametrize("model", _CANDIDATE_IDS)
@pytest.mark.parametrize(
    ("thinking", "reason", "hidden"),
    [
        ("disabled", "thinking_disabled", ()),
        ({"type": "disabled"}, "thinking_disabled", ()),
        ("enabled", "thinking_enabled", ()),
        ({"type": "enabled", "budget_tokens": 2048}, "thinking_enabled", ("2048",)),
    ],
)
def test_disabled_and_enabled_thinking_are_always_rejected(
    model: str, thinking: object, reason: str, hidden: tuple[str, ...]
) -> None:
    caps = _capabilities()
    with pytest.raises(RuntimeError) as caught:
        caps.build_request(
            model=model,
            max_tokens=173,
            system="",
            messages=[_user()],
            effort="low",
            thinking=thinking,
        )
    _assert_rejected(caught.value, reason, *hidden)


@pytest.mark.parametrize("effort", ("xhigh", "max"))
def test_between_tools_is_rejected_for_sonnet_above_high(effort: str) -> None:
    caps = _capabilities()
    with pytest.raises(RuntimeError) as caught:
        caps.build_request(
            model="claude-sonnet-5-5",
            max_tokens=173,
            system="",
            messages=[_user()],
            effort=effort,
            thinking="between_tools",
        )
    _assert_rejected(caught.value, "thinking_between_tools_not_allowed", effort)


@pytest.mark.parametrize("effort", _EFFORTS)
def test_between_tools_is_rejected_for_opus(effort: str) -> None:
    caps = _capabilities()
    with pytest.raises(RuntimeError) as caught:
        caps.build_request(
            model="claude-opus-5-5",
            max_tokens=173,
            system="",
            messages=[_user()],
            effort=effort,
            thinking={"type": "between_tools"},
        )
    _assert_rejected(caught.value, "thinking_between_tools_not_allowed")


@pytest.mark.parametrize("effort", _BETWEEN_TOOLS_EFFORTS)
def test_between_tools_on_sonnet_sends_only_its_type(effort: str) -> None:
    caps = _capabilities()
    built = caps.build_request(
        model="claude-sonnet-5-5",
        max_tokens=173,
        system="",
        messages=[_user()],
        effort=effort,
        thinking="between_tools",
    )
    assert built["thinking"] == {"type": "between_tools"}
    assert built["output_config"] == {"effort": effort}
    assert "temperature" not in built
    assert "system" not in built


def test_between_tools_with_an_extra_field_is_rejected() -> None:
    caps = _capabilities()
    with pytest.raises(RuntimeError) as caught:
        caps.build_request(
            model="claude-sonnet-5-5",
            max_tokens=173,
            system="",
            messages=[_user()],
            effort="low",
            thinking={"type": "between_tools", "display": "summarized"},
        )
    _assert_rejected(caught.value, "thinking_unsupported", "summarized")


@pytest.mark.parametrize("model", _CANDIDATE_IDS)
def test_adaptive_thinking_sends_only_its_type(model: str) -> None:
    caps = _capabilities()
    built = caps.build_request(
        model=model,
        max_tokens=173,
        system="kept",
        messages=[_user()],
        effort="high",
        thinking={"type": "adaptive"},
    )
    assert built["thinking"] == {"type": "adaptive"}
    assert built["system"] == "kept"
    assert "temperature" not in built


def test_explicit_omit_matches_the_default_candidate_shape() -> None:
    caps = _capabilities()
    messages = [_user()]
    omitted = caps.build_request(
        model="claude-sonnet-5-5",
        max_tokens=173,
        system="",
        messages=messages,
        effort="medium",
        thinking="omit",
    )
    default = caps.build_request(
        model="claude-sonnet-5-5",
        max_tokens=173,
        system="",
        messages=messages,
        effort="medium",
    )
    assert omitted == default
    assert "thinking" not in omitted


def test_trailing_assistant_message_is_rejected() -> None:
    caps = _capabilities()
    with pytest.raises(RuntimeError) as caught:
        caps.build_request(
            model="claude-sonnet-5-5",
            max_tokens=173,
            system="",
            messages=[_user(), {"role": "assistant", "content": _MARKER}],
            effort="medium",
        )
    _assert_rejected(caught.value, "assistant_prefill", _MARKER)


def test_earlier_assistant_turn_is_not_prefill() -> None:
    caps = _capabilities()
    messages = [
        _user("one"),
        {"role": "assistant", "content": "earlier turn"},
        _user("three"),
    ]
    built = caps.build_request(
        model="claude-opus-5-5",
        max_tokens=173,
        system="",
        messages=messages,
        effort="low",
    )
    assert built["messages"] == messages
    assert "thinking" not in built


@pytest.mark.parametrize(
    "tool_choice",
    [
        {"type": "tool", "name": _MARKER},
        {"type": "any"},
        {"type": "auto"},
        {"type": "none"},
    ],
)
def test_tool_choice_argument_is_rejected(tool_choice: Mapping[str, str]) -> None:
    caps = _capabilities()
    with pytest.raises(RuntimeError) as caught:
        caps.build_request(
            model="claude-sonnet-4-20250514",
            max_tokens=173,
            system="",
            messages=[_user()],
            tool_choice=tool_choice,
        )
    _assert_rejected(caught.value, "tool_choice_forbidden", _MARKER)


def test_rejection_does_not_echo_prompt_text() -> None:
    caps = _capabilities()
    with pytest.raises(RuntimeError) as caught:
        caps.build_request(
            model="cir01-test-model",
            max_tokens=173,
            system=_MARKER,
            messages=[_user(_MARKER)],
            effort="high",
        )
    _assert_rejected(caught.value, "effort_not_applicable", _MARKER)


@pytest.mark.parametrize(
    ("model", "effort", "thinking"),
    [
        ("claude-sonnet-5-5", "low", "between_tools"),
        ("claude-sonnet-5-5", "medium", "adaptive"),
        ("claude-opus-5-5", "high", {"type": "adaptive"}),
    ],
)
def test_sdk_encoder_accepts_the_candidate_thinking_cast(
    model: str, effort: str, thinking: object
) -> None:
    caps = _capabilities()
    built = caps.build_request(
        model=model,
        max_tokens=173,
        system="",
        messages=[_user()],
        effort=effort,
        thinking=thinking,
    )
    params = anthropic_client.to_message_create_params(built)
    requests: list[httpx.Request | httpx2.Request] = []

    def handle(request: httpx.Request | httpx2.Request) -> httpx.Response | httpx2.Response:
        requests.append(request)
        return _ok()

    with anthropic.Anthropic(
        api_key=_OFFLINE_KEY,
        http_client=_http.Client(transport=_http.MockTransport(handle)),
        max_retries=0,
    ) as sdk_client:
        message = sdk_client.messages.create(**params)

    assert message.content[0].text == '{"ok":true}'
    assert len(requests) == 1
    payload = json.loads(requests[0].content)
    assert payload["model"] == model
    assert payload["output_config"] == {"effort": effort}
    assert "temperature" not in payload
    assert "system" not in payload
    if thinking == "between_tools":
        assert payload["thinking"] == {"type": "between_tools"}
    else:
        assert payload["thinking"] == {"type": "adaptive"}
    assert set(payload["thinking"]) == {"type"}
