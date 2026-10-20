"""Fail-closed cache for the application database engine.

Engine creation stays lazy. These tests never open a connection.
"""

from __future__ import annotations

import builtins
import logging
from collections.abc import Iterator
from typing import Any

import pytest

from idis.observability.tracing import TracingConfigError
from idis.persistence import db

_LOOPBACK_DATABASE_URL = "postgresql://127.0.0.1:5432/idis_offline"
_DRIVER_URL = "postgresql+psycopg2://127.0.0.1:5432/idis_offline"
_ENGINE_ARGUMENTS = {
    "pool_size": 5,
    "max_overflow": 10,
    "pool_pre_ping": True,
    "echo": False,
}
_ENGINE_LOG = "Created application database engine"


class _RecordingEngine:
    """Engine stand-in that records dispose and does not connect."""

    def __init__(self) -> None:
        self.disposed = False

    def dispose(self) -> None:
        self.disposed = True


class _EngineConstruction:
    """One create_engine call recorded by the test."""

    def __init__(self, url: str, arguments: dict[str, Any], engine: _RecordingEngine) -> None:
        self.url = url
        self.arguments = arguments
        self.engine = engine


@pytest.fixture(autouse=True)
def _reset_engines_around_test() -> Iterator[None]:
    """Drop cached engines before and after every test."""
    db.reset_engines()
    yield
    db.reset_engines()


def _install_recording_create_engine(
    monkeypatch: pytest.MonkeyPatch,
) -> list[_EngineConstruction]:
    """Replace engine construction with an in-process recorder."""
    constructions: list[_EngineConstruction] = []

    def create_engine(url: str, **arguments: Any) -> _RecordingEngine:
        engine = _RecordingEngine()
        constructions.append(_EngineConstruction(url, arguments, engine))
        return engine

    monkeypatch.setenv("IDIS_DATABASE_URL", _LOOPBACK_DATABASE_URL)
    monkeypatch.setattr(db, "create_engine", create_engine)
    return constructions


def _assert_construction(construction: _EngineConstruction) -> None:
    """The recorded call keeps the application pool arguments and loopback URL."""
    assert construction.url == _DRIVER_URL
    assert construction.arguments == _ENGINE_ARGUMENTS


def test_tracing_config_error_is_reraised_and_engine_is_not_cached(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A registration failure disposes the engine and leaves the cache empty."""
    constructions = _install_recording_create_engine(monkeypatch)
    first_error = TracingConfigError("registration failed")

    def fail_instrument(engine: Any) -> bool:
        raise first_error

    monkeypatch.setattr("idis.observability.tracing.instrument_sqlalchemy", fail_instrument)
    second_error = TracingConfigError("registration failed again")

    def fail_again(engine: Any) -> bool:
        raise second_error

    with caplog.at_level(logging.INFO, logger="idis.persistence.db"):
        with pytest.raises(TracingConfigError) as first_failure:
            db.get_app_engine()
        monkeypatch.setattr("idis.observability.tracing.instrument_sqlalchemy", fail_again)
        with pytest.raises(TracingConfigError) as second_failure:
            db.get_app_engine()

    assert first_failure.value is first_error
    assert second_failure.value is second_error
    assert db._app_engine is None
    assert [item.engine.disposed for item in constructions] == [True, True]
    assert caplog.text.count(_ENGINE_LOG) == 2
    for construction in constructions:
        _assert_construction(construction)


def test_successful_instrumentation_caches_the_engine(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A successful registration is cached and not repeated."""
    constructions = _install_recording_create_engine(monkeypatch)
    seen: list[Any] = []

    def instrument(engine: Any) -> bool:
        seen.append(engine)
        return True

    monkeypatch.setattr("idis.observability.tracing.instrument_sqlalchemy", instrument)
    with caplog.at_level(logging.INFO, logger="idis.persistence.db"):
        first = db.get_app_engine()
        second = db.get_app_engine()

    assert second is first
    assert seen == [first]
    assert first is constructions[0].engine
    assert constructions[0].engine.disposed is False
    assert len(constructions) == 1
    assert caplog.text.count(_ENGINE_LOG) == 1
    _assert_construction(constructions[0])


def test_import_error_still_caches_the_engine(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """An ImportError from the tracing import keeps the existing cache behaviour."""
    constructions = _install_recording_create_engine(monkeypatch)
    real_import = builtins.__import__

    def fail_tracing_import(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "idis.observability.tracing":
            raise ImportError("tracing unavailable")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fail_tracing_import)
    with caplog.at_level(logging.INFO, logger="idis.persistence.db"):
        first = db.get_app_engine()
        second = db.get_app_engine()
    monkeypatch.setattr(builtins, "__import__", real_import)

    assert second is first
    assert db._app_engine is first
    assert len(constructions) == 1
    assert constructions[0].engine.disposed is False
    assert caplog.text.count(_ENGINE_LOG) == 1
    _assert_construction(constructions[0])
