"""RED reachability contracts for the dedicated full-strict-v19 erasure path.

The legacy PostgreSQL executor remains historical source, and the in-memory
twin remains useful for hermetic non-PostgreSQL tests.  Neither may be selected
or smuggled into the production route while PostgreSQL is configured.
"""

from __future__ import annotations

import importlib
import inspect
from collections.abc import Callable
from pathlib import Path
from types import ModuleType

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SRC_ROOT = _REPO_ROOT / "src" / "idis"
_HISTORICAL_POSTGRES_EXECUTOR = (_SRC_ROOT / "compliance" / "erasure_postgres.py").resolve()
_TASK7_AUTHORITY_PATH = (_SRC_ROOT / "compliance" / "task7_erasure.py").resolve()
_TASK7_DB_CALLABLE_PATH = (_SRC_ROOT / "persistence" / "erasure_db.py").resolve()


def _task7_authority_module() -> ModuleType:
    try:
        return importlib.import_module("idis.compliance.task7_erasure")
    except ModuleNotFoundError as exc:
        pytest.fail(
            "TASK7_V19_ERASURE_AUTHORITY_MODULE_ABSENT:idis.compliance.task7_erasure",
            pytrace=False,
        )
        raise AssertionError("unreachable") from exc


def _production_legacy_references() -> tuple[str, ...]:
    """Return production files that make the historical direct-delete class reachable."""

    findings: list[str] = []
    for path in sorted(_SRC_ROOT.rglob("*.py")):
        resolved = path.resolve()
        if (
            resolved == _HISTORICAL_POSTGRES_EXECUTOR
            or "persistence/migrations/" in path.as_posix()
        ):
            continue
        source = path.read_text(encoding="utf-8")
        # Importing PostgresExportCollector from the historical module remains
        # legitimate; only the direct-delete executor symbol is unreachable.
        if "PostgresErasureExecutor" in source:
            findings.append(path.relative_to(_REPO_ROOT).as_posix())
    return tuple(findings)


@pytest.fixture(autouse=True)
def _reset_global_executor() -> None:
    erasure = importlib.import_module("idis.compliance.erasure")
    erasure.reset_erasure_executor()
    try:
        yield
    finally:
        erasure.reset_erasure_executor()


def test_postgres_production_has_exactly_one_task7_erasure_authority() -> None:
    candidates = tuple(
        path.relative_to(_REPO_ROOT).as_posix()
        for path in sorted((_SRC_ROOT / "compliance").glob("*task7*erasure*.py"))
    )

    assert candidates == ("src/idis/compliance/task7_erasure.py",), (
        "TASK7_V19_ERASURE_AUTHORITY_MODULE_ABSENT_OR_DUPLICATE"
    )
    assert _TASK7_AUTHORITY_PATH.is_file()
    assert _task7_authority_module().__name__ == "idis.compliance.task7_erasure"


def test_no_production_module_constructs_or_imports_legacy_postgres_executor() -> None:
    assert _HISTORICAL_POSTGRES_EXECUTOR.is_file()
    assert _production_legacy_references() == ()


def test_postgres_default_builder_selects_only_the_task7_authority() -> None:
    erasure = importlib.import_module("idis.compliance.erasure")
    source = inspect.getsource(erasure.build_default_erasure_executor)

    assert "PostgresErasureExecutor" not in source
    assert "idis.compliance.erasure_postgres" not in source
    assert "task7_erasure" in source


@pytest.mark.parametrize(
    "forbidden_factory",
    (
        lambda: importlib.import_module(
            "idis.compliance.erasure_postgres"
        ).PostgresErasureExecutor(),
        lambda: importlib.import_module("idis.compliance.erasure").InMemoryErasureExecutor(),
    ),
    ids=("legacy-postgres", "in-memory"),
)
def test_global_override_cannot_smuggle_historical_executor_when_postgres_is_configured(
    monkeypatch: pytest.MonkeyPatch,
    forbidden_factory: Callable[[], object],
) -> None:
    erasure = importlib.import_module("idis.compliance.erasure")
    persistence_db = importlib.import_module("idis.persistence.db")
    safe_authority = object()
    forbidden = forbidden_factory()
    monkeypatch.setattr(persistence_db, "is_postgres_configured", lambda: True)
    monkeypatch.setattr(
        erasure,
        "build_default_erasure_executor",
        lambda: safe_authority,
    )

    try:
        erasure.set_erasure_executor(forbidden)
    except (TypeError, RuntimeError):
        # Rejecting the override at its ingress is the strongest valid posture.
        return

    assert erasure.get_erasure_executor() is safe_authority, (
        "TASK7_V19_ERASURE_GLOBAL_OVERRIDE_SMUGGLED_HISTORICAL_EXECUTOR"
    )


@pytest.mark.parametrize(
    "forbidden_factory",
    (
        lambda: importlib.import_module(
            "idis.compliance.erasure_postgres"
        ).PostgresErasureExecutor(),
        lambda: importlib.import_module("idis.compliance.erasure").InMemoryErasureExecutor(),
    ),
    ids=("explicit-legacy-postgres", "explicit-in-memory"),
)
def test_public_legacy_execute_ingress_fails_before_any_work_when_postgres_is_configured(
    monkeypatch: pytest.MonkeyPatch,
    forbidden_factory: Callable[[], object],
) -> None:
    """An explicit executor argument cannot bypass the production composition."""

    erasure = importlib.import_module("idis.compliance.erasure")
    persistence_db = importlib.import_module("idis.persistence.db")
    monkeypatch.setattr(persistence_db, "is_postgres_configured", lambda: True)

    class _ForbiddenStore:
        def get(self, *_args: object, **_kwargs: object) -> object:
            raise AssertionError("legacy store read occurred before fail-closed ingress")

    def forbidden_hold(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("legacy hold check occurred before fail-closed ingress")

    with pytest.raises(RuntimeError, match="TASK7_ERASURE_API_COMPOSITION_REQUIRED"):
        erasure.execute_erasure(
            object(),
            "44444444-4444-4444-8444-444444444444",
            object(),
            executor=forbidden_factory(),
            hold_checker=forbidden_hold,
            store=_ForbiddenStore(),
        )


def test_in_memory_twin_is_default_only_when_postgres_is_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    erasure = importlib.import_module("idis.compliance.erasure")
    persistence_db = importlib.import_module("idis.persistence.db")
    monkeypatch.setattr(persistence_db, "is_postgres_configured", lambda: False)

    selected = erasure.build_default_erasure_executor()

    assert isinstance(selected, erasure.InMemoryErasureExecutor)


def test_authenticated_execute_route_does_not_use_the_process_global_legacy_seam() -> None:
    route = importlib.import_module("idis.api.routes.erasure_export")
    source = inspect.getsource(route.execute_erasure_request)

    assert "get_erasure_executor" not in source
    assert "PostgresErasureExecutor" not in source
    assert "InMemoryErasureExecutor" not in source
    assert "task7_erasure" in inspect.getsource(route)


def test_dedicated_authority_and_db_adapter_contain_no_application_delete_sql() -> None:
    authority = _task7_authority_module()
    assert authority.__name__ == "idis.compliance.task7_erasure"
    paths = (_TASK7_AUTHORITY_PATH, _TASK7_DB_CALLABLE_PATH)
    missing = tuple(path.relative_to(_REPO_ROOT).as_posix() for path in paths if not path.is_file())
    assert missing == (), f"TASK7_V19_ERASURE_PRODUCTION_MODULE_ABSENT:{missing}"

    for path in paths:
        source = path.read_text(encoding="utf-8").upper()
        assert "DELETE FROM" not in source, path.relative_to(_REPO_ROOT).as_posix()
        assert "POSTGRESERASUREEXECUTOR" not in source
        assert "INMEMORYERASUREEXECUTOR" not in source
