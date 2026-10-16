"""Slice100 Task 1 RED contracts for the strict external HTTP acceptance path."""

from __future__ import annotations

import contextlib
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

import idis.evaluation.real_example_run_harness as harness_module
from idis.evaluation.real_example_run_harness import (
    RealExampleFullRunHarnessOptions,
    run_real_example_full_run_harness,
)


class _Response:
    def __init__(self, status_code: int, body: dict[str, Any]) -> None:
        self.status_code = status_code
        self._body = body

    def json(self) -> dict[str, Any]:
        return dict(self._body)


class _PassingStrictReport:
    may_proceed = True

    def model_dump(self, *, mode: str) -> dict[str, Any]:
        assert mode == "json"
        return {
            "required": True,
            "may_proceed": True,
            "blocker_count": 0,
            "blocking_components": [],
            "components": [],
        }


class _QueuedExternalClient:
    """External-client double: POST queues and GET observes worker completion."""

    def __init__(self, *, post_status: str = "QUEUED") -> None:
        self.post_status = post_status
        self.posts: list[tuple[str, dict[str, Any]]] = []
        self.post_headers: list[tuple[str, dict[str, str]]] = []
        self.gets: list[str] = []
        self._statuses = iter(("QUEUED", "RUNNING", "SUCCEEDED"))

    def post(self, url: str, **kwargs: Any) -> _Response:
        body = dict(kwargs.get("json") or {})
        self.posts.append((url, body))
        self.post_headers.append((url, dict(kwargs.get("headers") or {})))
        if url == "/v1/deals":
            return _Response(201, {"deal_id": "deal-1"})
        if url == "/v1/deals/deal-1/documents/upload":
            return _Response(
                201,
                {
                    "document_id": "document-1",
                    "doc_id": "artifact-1",
                    "parse_status": "PARSED",
                },
            )
        if url == "/v1/deals/deal-1/runs":
            return _Response(
                202,
                {
                    "run_id": "run-1",
                    "status": self.post_status,
                    "steps": [],
                },
            )
        raise AssertionError(f"unexpected POST {url}")

    def get(self, url: str, **_kwargs: Any) -> _Response:
        self.gets.append(url)
        status = next(self._statuses)
        steps = (
            [{"step_name": "INGEST_CHECK", "status": "COMPLETED"}] if status == "SUCCEEDED" else []
        )
        return _Response(
            200,
            {
                "run_id": "run-1",
                "status": status,
                "mode": "FULL",
                "started_at": "2026-07-15T00:00:00Z",
                "finished_at": "2026-07-15T00:00:01Z" if status == "SUCCEEDED" else None,
                "steps": steps,
            },
        )


def _root(tmp_path: Path) -> Path:
    root = tmp_path / "real_example"
    root.mkdir()
    (root / "document.pdf").write_bytes(b"%PDF-1.4\nsafe\n%%EOF")
    return root


def _strict_options(root: Path, **overrides: Any) -> RealExampleFullRunHarnessOptions:
    values: dict[str, Any] = {
        "root": root,
        "require_full_live": True,
        "api_base_url": "http://127.0.0.1:8000",
        "strict_preflight_proof_id": "00000000-0000-4000-8000-000000000100",
        "run_poll_interval_seconds": 0.001,
        "run_timeout_seconds": 2.0,
    }
    values.update(overrides)
    return RealExampleFullRunHarnessOptions(**values)


def test_strict_harness_rejects_injected_client_before_inventory(tmp_path: Path) -> None:
    root = _root(tmp_path)

    with pytest.raises(ValueError, match="API_ACCEPTANCE_PATH_NONCANONICAL"):
        run_real_example_full_run_harness(_strict_options(root, api_client=_QueuedExternalClient()))


@pytest.mark.parametrize(
    "base_url",
    [None, "", "localhost:8000", "file:///tmp/idis", "ftp://127.0.0.1", "/v1"],
)
def test_strict_harness_requires_absolute_http_base_url(
    tmp_path: Path,
    base_url: str | None,
) -> None:
    root = _root(tmp_path)

    with pytest.raises(ValueError, match="API_ACCEPTANCE_PATH_NONCANONICAL"):
        run_real_example_full_run_harness(_strict_options(root, api_base_url=base_url))


def test_strict_harness_uses_external_client_and_polls_worker_status(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _root(tmp_path)
    client = _QueuedExternalClient()
    constructed_urls: list[str] = []

    monkeypatch.setattr(
        harness_module,
        "build_strict_full_live_readiness_report",
        lambda **_kwargs: _PassingStrictReport(),
    )

    @contextlib.contextmanager
    def external_client(
        base_url: str,
        _timeout_seconds: float | None = None,
    ) -> Iterator[_QueuedExternalClient]:
        constructed_urls.append(base_url)
        yield client

    monkeypatch.setattr(harness_module, "_external_api_client", external_client, raising=False)
    monkeypatch.setattr(
        harness_module,
        "_default_api_client",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("strict acceptance must not construct the in-process client")
        ),
    )
    monkeypatch.setattr(
        harness_module,
        "_latest_safe_run_snapshot",
        lambda: (_ for _ in ()).throw(
            AssertionError("strict acceptance must not read in-process run stores")
        ),
    )

    summary = run_real_example_full_run_harness(_strict_options(root))

    assert constructed_urls == ["http://127.0.0.1:8000"]
    assert client.gets == ["/v1/runs/run-1"] * 3
    run_posts = [payload for url, payload in client.posts if url.endswith("/runs")]
    assert run_posts == [
        {
            "mode": "FULL",
            "source": {"type": "deal_documents", "document_ids": ["document-1"]},
            "strict_preflight_proof_id": "00000000-0000-4000-8000-000000000100",
        }
    ]
    assert summary["status"] == "succeeded"
    assert summary["run"]["status"] == "SUCCEEDED"

    mutation_headers = [headers for _url, headers in client.post_headers]
    idempotency_keys = [headers.get("Idempotency-Key") for headers in mutation_headers]
    assert all(isinstance(key, str) and key for key in idempotency_keys)
    assert len(set(idempotency_keys)) == len(idempotency_keys)


def test_strict_mutation_idempotency_keys_are_stable_for_safe_replay(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _root(tmp_path)
    monkeypatch.setattr(
        harness_module,
        "build_strict_full_live_readiness_report",
        lambda **_kwargs: _PassingStrictReport(),
    )
    options = _strict_options(root)
    replay_one = _QueuedExternalClient()
    replay_two = _QueuedExternalClient()
    replay_clients = [replay_one, replay_two]

    @contextlib.contextmanager
    def replay_client(
        _base_url: str,
        _timeout_seconds: float | None = None,
    ) -> Iterator[_QueuedExternalClient]:
        yield replay_clients.pop(0)

    monkeypatch.setattr(harness_module, "_external_api_client", replay_client, raising=False)
    first = run_real_example_full_run_harness(options)
    second = run_real_example_full_run_harness(options)

    assert first["status"] == second["status"] == "succeeded"
    first_keys = [headers["Idempotency-Key"] for _url, headers in replay_one.post_headers]
    replay_keys = [headers["Idempotency-Key"] for _url, headers in replay_two.post_headers]
    assert first_keys == replay_keys
    assert len(first_keys) == 3  # deal, one upload, run enqueue
    assert all(options.strict_preflight_proof_id not in key for key in first_keys)


def test_strict_idempotency_key_canonicalizes_equivalent_proof_uuid_spellings() -> None:
    canonical = "00000000-0000-4000-8000-000000000100"
    braced_upper = "{00000000-0000-4000-8000-000000000100}".upper()

    first = harness_module._strict_mutation_idempotency_key(
        canonical,
        operation="run-enqueue",
    )
    second = harness_module._strict_mutation_idempotency_key(
        braced_upper,
        operation="run-enqueue",
    )

    assert first == second


def test_bounded_external_client_uses_only_the_remaining_global_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: list[float] = []

    class HttpClient:
        def post(self, _url: str, **kwargs: Any) -> _Response:
            observed.append(float(kwargs["timeout"]))
            return _Response(201, {"deal_id": "deal-1"})

    clock = iter((100.0, 100.25))
    monkeypatch.setattr(harness_module.time, "monotonic", lambda: next(clock))
    client = harness_module._BoundedExternalApiClient(HttpClient(), timeout_seconds=2.0)

    client.post("/v1/deals")

    assert observed == [pytest.approx(1.75)]


def test_strict_harness_converts_external_timeout_to_safe_blocker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _root(tmp_path)
    monkeypatch.setattr(
        harness_module,
        "build_strict_full_live_readiness_report",
        lambda **_kwargs: _PassingStrictReport(),
    )

    class TimeoutClient(_QueuedExternalClient):
        def post(self, url: str, **kwargs: Any) -> _Response:
            if url == "/v1/deals":
                raise TimeoutError("bounded external deadline elapsed")
            return super().post(url, **kwargs)

    @contextlib.contextmanager
    def external_client(
        _base_url: str,
        _timeout_seconds: float | None = None,
    ) -> Iterator[TimeoutClient]:
        yield TimeoutClient()

    monkeypatch.setattr(harness_module, "_external_api_client", external_client, raising=False)

    summary = run_real_example_full_run_harness(_strict_options(root))

    assert summary["status"] == "blocked"
    assert summary["blocker"] == {
        "stage": "deal_create",
        "reason_code": "RUN_TIMEOUT",
        "http_status": None,
    }


def test_strict_harness_rejects_terminal_looking_post_response(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _root(tmp_path)
    client = _QueuedExternalClient(post_status="SUCCEEDED")
    monkeypatch.setattr(
        harness_module,
        "build_strict_full_live_readiness_report",
        lambda **_kwargs: _PassingStrictReport(),
    )

    @contextlib.contextmanager
    def external_client(
        _base_url: str,
        _timeout_seconds: float | None = None,
    ) -> Iterator[_QueuedExternalClient]:
        yield client

    monkeypatch.setattr(harness_module, "_external_api_client", external_client, raising=False)

    summary = run_real_example_full_run_harness(_strict_options(root))

    assert summary["status"] == "blocked"
    assert summary["blocker"]["reason_code"] == "API_ACCEPTANCE_PATH_NONCANONICAL"
    assert client.gets == []


def test_non_strict_harness_keeps_hermetic_injected_client_contract(tmp_path: Path) -> None:
    root = _root(tmp_path)

    class LegacyClient(_QueuedExternalClient):
        def post(self, url: str, **kwargs: Any) -> _Response:
            if url == "/v1/deals/deal-1/runs":
                self.posts.append((url, dict(kwargs.get("json") or {})))
                return _Response(202, {"status": "SUCCEEDED", "steps": []})
            return super().post(url, **kwargs)

    client = LegacyClient()
    summary = run_real_example_full_run_harness(
        RealExampleFullRunHarnessOptions(root=root, api_client=client)
    )

    assert summary["status"] == "succeeded"
    assert client.gets == []
