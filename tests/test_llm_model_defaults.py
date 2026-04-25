"""LLM model-default regression (Sprint 2, Task 13).

Locks in the Anthropic model defaults this codebase ships:

- claude-sonnet-4-6 for IDIS_ANTHROPIC_MODEL_EXTRACT and
  IDIS_ANTHROPIC_MODEL_DEBATE_DEFAULT
- claude-opus-4-7 for IDIS_ANTHROPIC_MODEL_DEBATE_ARBITER

The deprecated Claude 4 dated snapshots (claude-sonnet-4-20250514,
claude-opus-4-20250514) must not appear as defaults anywhere we
configure model selection.

The deterministic SNAPSHOT release gate must remain pinned: even when
this test exercises the Anthropic-backed code paths, the gate-side
extractor pin (`tests/test_snapshot_e2e_postgres.py`) is unaffected
because we don't touch that pin here.

No outbound API calls. The Anthropic client construction path checks
for ANTHROPIC_API_KEY and raises before any network use; all the
model-default assertions are made via static reads of the source and
via constructing the client with a placeholder key after stubbing
out the SDK constructor.
"""

from __future__ import annotations

import inspect
import os
import re
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


# Stale dated-snapshot IDs the project must not default to anywhere.
DEPRECATED_DEFAULTS = (
    "claude-sonnet-4-20250514",
    "claude-opus-4-20250514",
)

# Currently approved replacements per the project's documented model list.
EXPECTED_SONNET_DEFAULT = "claude-sonnet-4-6"
EXPECTED_OPUS_DEFAULT = "claude-opus-4-7"

CONFIG_FILES = [
    REPO_ROOT / "src/idis/services/extraction/extractors/anthropic_client.py",
    REPO_ROOT / "src/idis/api/routes/runs.py",
    REPO_ROOT / ".env.example",
]


class TestNoStaleClaude4SnapshotDefaults:
    """Refuse the dated Claude 4 snapshot IDs anywhere we choose defaults."""

    @pytest.mark.parametrize("path", CONFIG_FILES, ids=lambda p: p.name)
    def test_file_contains_no_deprecated_snapshot_ids(self, path: Path) -> None:
        contents = _read(path)
        for stale in DEPRECATED_DEFAULTS:
            assert stale not in contents, (
                f"{path.name} still contains the deprecated default "
                f"{stale!r}; replace with the supported current ID"
            )


class TestExpectedDefaultsAreApplied:
    """Lock in the expected current defaults across each known site."""

    def test_anthropic_client_extract_default(self) -> None:
        from idis.services.extraction.extractors import anthropic_client

        source = inspect.getsource(anthropic_client)
        # The constructor's os.environ.get(...) fallback for
        # IDIS_ANTHROPIC_MODEL_EXTRACT is the live default.
        m = re.search(
            r'IDIS_ANTHROPIC_MODEL_EXTRACT[^\n]*?\n[^\n]*?"([^"]+)"',
            source,
            re.DOTALL,
        )
        assert m is not None, "could not locate IDIS_ANTHROPIC_MODEL_EXTRACT default"
        assert m.group(1) == EXPECTED_SONNET_DEFAULT, (
            f"extract default must be {EXPECTED_SONNET_DEFAULT!r}; "
            f"got {m.group(1)!r}"
        )

    def test_runs_route_extract_default(self) -> None:
        runs_src = _read(REPO_ROOT / "src/idis/api/routes/runs.py")
        # Single fallback for IDIS_ANTHROPIC_MODEL_EXTRACT in routes/runs.py.
        m = re.search(
            r'IDIS_ANTHROPIC_MODEL_EXTRACT", "([^"]+)"',
            runs_src,
        )
        assert m is not None and m.group(1) == EXPECTED_SONNET_DEFAULT, (
            f"runs.py IDIS_ANTHROPIC_MODEL_EXTRACT default must be "
            f"{EXPECTED_SONNET_DEFAULT!r}; got {m.group(1) if m else None!r}"
        )

    def test_runs_route_debate_default(self) -> None:
        runs_src = _read(REPO_ROOT / "src/idis/api/routes/runs.py")
        # Multiple call-sites use IDIS_ANTHROPIC_MODEL_DEBATE_DEFAULT;
        # all must pin to the same current Sonnet ID.
        defaults = re.findall(
            r'IDIS_ANTHROPIC_MODEL_DEBATE_DEFAULT"\s*,\s*"([^"]+)"',
            runs_src,
        )
        assert defaults, "expected at least one debate-default fallback"
        assert all(d == EXPECTED_SONNET_DEFAULT for d in defaults), (
            f"every IDIS_ANTHROPIC_MODEL_DEBATE_DEFAULT fallback must be "
            f"{EXPECTED_SONNET_DEFAULT!r}; got {defaults!r}"
        )

    def test_runs_route_arbiter_default(self) -> None:
        runs_src = _read(REPO_ROOT / "src/idis/api/routes/runs.py")
        m = re.search(
            r'IDIS_ANTHROPIC_MODEL_DEBATE_ARBITER", "([^"]+)"',
            runs_src,
        )
        assert m is not None and m.group(1) == EXPECTED_OPUS_DEFAULT, (
            f"arbiter default must be {EXPECTED_OPUS_DEFAULT!r}; "
            f"got {m.group(1) if m else None!r}"
        )

    @pytest.mark.parametrize(
        "var,expected",
        [
            ("IDIS_ANTHROPIC_MODEL_EXTRACT", EXPECTED_SONNET_DEFAULT),
            ("IDIS_ANTHROPIC_MODEL_DEBATE_DEFAULT", EXPECTED_SONNET_DEFAULT),
            ("IDIS_ANTHROPIC_MODEL_DEBATE_ARBITER", EXPECTED_OPUS_DEFAULT),
        ],
    )
    def test_env_example_uses_current_defaults(self, var: str, expected: str) -> None:
        env_text = _read(REPO_ROOT / ".env.example")
        m = re.search(rf"^{re.escape(var)}=([^\s]+)$", env_text, re.MULTILINE)
        assert m is not None, f".env.example missing {var}"
        assert m.group(1) == expected, (
            f".env.example {var}={m.group(1)!r}; expected {expected!r}"
        )


class TestEnvVarOverridesStillWork:
    """A configured env var must still override the new defaults end to end."""

    def test_anthropic_client_picks_up_extract_env_override(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Stub the SDK's Anthropic() constructor so no real client init runs.
        import anthropic

        from idis.services.extraction.extractors import anthropic_client

        class _FakeAnthropic:
            def __init__(self, *args: Any, **kwargs: Any) -> None:
                pass

        monkeypatch.setattr(anthropic, "Anthropic", _FakeAnthropic)

        custom = "claude-sonnet-4-6-test-override-id"
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-real")
        monkeypatch.setenv("IDIS_ANTHROPIC_MODEL_EXTRACT", custom)
        client = anthropic_client.AnthropicLLMClient()
        assert client._model == custom, (
            f"env override must win over the default; got {client._model!r}"
        )

    def test_anthropic_client_falls_back_to_current_default(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import anthropic

        from idis.services.extraction.extractors import anthropic_client

        class _FakeAnthropic:
            def __init__(self, *args: Any, **kwargs: Any) -> None:
                pass

        monkeypatch.setattr(anthropic, "Anthropic", _FakeAnthropic)
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-real")
        monkeypatch.delenv("IDIS_ANTHROPIC_MODEL_EXTRACT", raising=False)
        client = anthropic_client.AnthropicLLMClient()
        assert client._model == EXPECTED_SONNET_DEFAULT, (
            f"unset env must fall back to {EXPECTED_SONNET_DEFAULT!r}; "
            f"got {client._model!r}"
        )


class TestDeterministicGateExtractorPinUntouched:
    """Sprint 1 SNAPSHOT release gate must keep its deterministic pin
    regardless of model-default migrations elsewhere. The pin lives in
    `tests/test_snapshot_e2e_postgres.py` as an autouse fixture.
    """

    def test_gate_still_force_pins_deterministic_extraction(self) -> None:
        gate_src = _read(REPO_ROOT / "tests/test_snapshot_e2e_postgres.py")
        assert "_pin_deterministic_extraction" in gate_src
        assert 'monkeypatch.setenv("IDIS_EXTRACT_BACKEND", "deterministic")' in gate_src

    def test_no_anthropic_default_referenced_from_gate(self) -> None:
        """The gate must not reference Anthropic model IDs directly — its
        whole point is to validate plumbing on the deterministic backend.
        """
        gate_src = _read(REPO_ROOT / "tests/test_snapshot_e2e_postgres.py")
        for stale in DEPRECATED_DEFAULTS:
            assert stale not in gate_src
        assert "claude-" not in gate_src, (
            "the deterministic SNAPSHOT release gate must not hard-wire "
            "any Claude model ID"
        )


# Help static analyzers; this import is otherwise unused at runtime.
_ = os
