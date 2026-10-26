"""Slice100 Task 2 RED: bind every Neo4j operation to ``NEO4J_DATABASE``.

No network is used.  Driver/session/result fakes implement the small Neo4j protocol exercised by
health, read/write helpers, and the namespaced readiness probe.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from typing import Any

import pytest

import idis.persistence.neo4j_driver as neo4j_module

_TENANT_ID = "10000000-0000-4000-8000-000000000002"
_DATABASE = "idis-acceptance-task2"
_PROBE_NAMESPACE = "slice100-task2-hermetic-probe"


def _env(*, database: str | None = _DATABASE) -> dict[str, str]:
    values = {
        "NEO4J_URI": "neo4j+s://private-task2.invalid",
        "NEO4J_USERNAME": "private-task2-user",
        "NEO4J_PASSWORD": "private-task2-password",
    }
    if database is not None:
        values["NEO4J_DATABASE"] = database
    return values


class _Result:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def __iter__(self) -> Iterator[dict[str, Any]]:
        return iter(self._rows)

    def single(self) -> dict[str, Any] | None:
        return self._rows[0] if self._rows else None

    def data(self) -> list[dict[str, Any]]:
        return list(self._rows)

    def consume(self) -> None:
        return None


class _Session:
    def __init__(self, queries: list[tuple[str, dict[str, Any]]]) -> None:
        self._queries = queries

    def __enter__(self) -> _Session:
        return self

    def __exit__(self, *_args: Any) -> None:
        return None

    def close(self) -> None:
        return None

    def run(
        self,
        query: str,
        parameters: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> _Result:
        params = dict(parameters or kwargs)
        self._queries.append((query, params))
        upper = query.upper()
        if "DELETE" in upper:
            return _Result([{"deleted": True, "deleted_count": 1}])
        return _Result(
            [
                {
                    "ok": 1,
                    "found": True,
                    "count": 1,
                    "probe_marker": _PROBE_NAMESPACE,
                    "marker": _PROBE_NAMESPACE,
                    "value": _PROBE_NAMESPACE,
                }
            ]
        )

    def execute_read(self, work: Callable[[Any], Any], *args: Any, **kwargs: Any) -> Any:
        return work(self, *args, **kwargs)

    def execute_write(self, work: Callable[[Any], Any], *args: Any, **kwargs: Any) -> Any:
        return work(self, *args, **kwargs)


class _Driver:
    def __init__(self) -> None:
        self.session_databases: list[str | None] = []
        self.queries: list[tuple[str, dict[str, Any]]] = []
        self.connectivity_verified = False
        self.closed = False

    def verify_connectivity(self, **_kwargs: Any) -> None:
        self.connectivity_verified = True

    def session(self, *, database: str | None = None, **_kwargs: Any) -> _Session:
        self.session_databases.append(database)
        return _Session(self.queries)

    def close(self) -> None:
        self.closed = True


class _CommittedThenUnreadableResult(_Result):
    def single(self) -> dict[str, Any] | None:
        raise RuntimeError("result decoding failed after autocommit")


class _CommittedThenUnreadableSession(_Session):
    def __init__(self, driver: _CommittedThenUnreadableDriver) -> None:
        super().__init__(driver.queries)
        self._driver = driver

    def run(
        self,
        query: str,
        parameters: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> _Result:
        params = dict(parameters or kwargs)
        self._queries.append((query, params))
        if "MERGE" in query.upper():
            self._driver.probe_node_exists = True
            return _CommittedThenUnreadableResult([])
        if "DELETE" in query.upper():
            self._driver.probe_node_exists = False
            return _Result([{"deleted": True}])
        return _Result([])


class _CommittedThenUnreadableDriver(_Driver):
    def __init__(self) -> None:
        super().__init__()
        self.probe_node_exists = False

    def session(
        self, *, database: str | None = None, **_kwargs: Any
    ) -> _CommittedThenUnreadableSession:
        self.session_databases.append(database)
        return _CommittedThenUnreadableSession(self)


@pytest.mark.parametrize("database", [None, "", "   "])
def test_neo4j_health_requires_nonblank_database(database: str | None) -> None:
    driver = _Driver()

    result = neo4j_module.check_neo4j_health(
        env=_env(database=database),
        driver_factory=lambda *_args, **_kwargs: driver,
    )

    assert result.status == neo4j_module.Neo4jHealthStatus.MISSING_CREDENTIALS
    assert result.missing_env_vars == ["NEO4J_DATABASE"]
    assert driver.connectivity_verified is False
    assert driver.session_databases == []


def test_neo4j_health_uses_configured_database_session() -> None:
    driver = _Driver()

    result = neo4j_module.check_neo4j_health(
        env=_env(),
        driver_factory=lambda *_args, **_kwargs: driver,
    )

    assert result.status == neo4j_module.Neo4jHealthStatus.HEALTHY
    assert driver.connectivity_verified is True
    assert driver.session_databases == [_DATABASE]


def test_execute_read_and_write_use_configured_database(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    driver = _Driver()
    for name, value in _env().items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(neo4j_module, "get_neo4j_driver", lambda: driver)

    read_rows = neo4j_module.execute_read(
        "RETURN $tenant_id AS tenant_id", {"tenant_id": _TENANT_ID}
    )
    write_rows = neo4j_module.execute_write(
        "MERGE (n:Task2Probe {tenant_id: $tenant_id}) RETURN 1 AS ok",
        {"tenant_id": _TENANT_ID},
    )

    assert read_rows and write_rows
    assert driver.session_databases == [_DATABASE, _DATABASE]


def test_execute_refuses_default_database_when_database_env_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    driver = _Driver()
    for name, value in _env(database=None).items():
        monkeypatch.setenv(name, value)
    monkeypatch.delenv("NEO4J_DATABASE", raising=False)
    monkeypatch.setattr(neo4j_module, "get_neo4j_driver", lambda: driver)

    with pytest.raises(neo4j_module.Neo4jConfigError, match="NEO4J_DATABASE"):
        neo4j_module.execute_read("RETURN $tenant_id", {"tenant_id": _TENANT_ID})

    assert driver.session_databases == []


def test_namespaced_database_probe_writes_reads_deletes_and_redacts() -> None:
    probe = getattr(neo4j_module, "probe_neo4j_database", None)
    assert callable(probe), "Task 2 requires probe_neo4j_database()"
    driver = _Driver()
    values = _env()

    result = probe(
        env=values,
        driver_factory=lambda *_args, **_kwargs: driver,
        probe_namespace=_PROBE_NAMESPACE,
    )

    payload = result.model_dump(mode="json") if hasattr(result, "model_dump") else dict(result)
    assert payload["passed"] is True
    assert payload["write_proven"] is True
    assert payload["read_proven"] is True
    assert payload["delete_proven"] is True
    assert driver.session_databases and set(driver.session_databases) == {_DATABASE}

    queries = [query.upper() for query, _params in driver.queries]
    write_index = next(
        i for i, query in enumerate(queries) if "MERGE" in query or "CREATE" in query
    )
    read_index = next(
        i
        for i, query in enumerate(queries)
        if i > write_index and "MATCH" in query and "RETURN" in query and "DELETE" not in query
    )
    delete_index = next(
        i for i, query in enumerate(queries) if i > read_index and "DELETE" in query
    )
    assert write_index < read_index < delete_index

    serialized = json.dumps(payload, sort_keys=True)
    for secret in (*values.values(), _PROBE_NAMESPACE):
        assert secret not in serialized


def test_database_probe_cleans_up_when_write_commits_but_result_decoding_fails() -> None:
    """Autocommit can persist MERGE before ``single()`` raises; cleanup is still mandatory."""
    driver = _CommittedThenUnreadableDriver()

    result = neo4j_module.probe_neo4j_database(
        env=_env(),
        driver_factory=lambda *_args, **_kwargs: driver,
        probe_namespace=_PROBE_NAMESPACE,
    )

    assert result.passed is False
    assert result.write_proven is False
    assert driver.probe_node_exists is False
    assert any("DELETE" in query.upper() for query, _params in driver.queries)
