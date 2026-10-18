"""Route bare PostgreSQL URLs through the declared psycopg2 driver.

Policy, cited from ``src/idis/persistence/db.py:62-75``: ``_ensure_psycopg_driver``
rewrites only a leading ``postgres://`` or ``postgresql://`` to
``postgresql+psycopg2://``. An explicit driver is preserved unchanged, including
``postgresql+psycopg2://`` and ``postgresql+psycopg://``. The helper never
rewrites an explicit driver.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from tests.slice100_task2.test_strict_profile_conformance import _reviewed_profile

_BARE = "postgresql://user:secret@localhost/idis"
_PSYCOPG2 = "postgresql+psycopg2://user:secret@localhost/idis"
_PSYCOPG = "postgresql+psycopg://user:secret@localhost/idis"
_POSTGRES = "postgres://user:secret@localhost/idis"
_DECLARED = "postgresql+psycopg2://user:secret@localhost/idis"

_URL_CASES = (
    (_BARE, _DECLARED),
    (_PSYCOPG2, _PSYCOPG2),
    (_PSYCOPG, _PSYCOPG),
    (_POSTGRES, _DECLARED),
)

# get_app_engine and get_admin_engine pass the return value of get_database_url.
# That function returns _ensure_psycopg_driver(url) at db.py:99. get_erasure_engine
# calls the helper at the create_engine argument and is not listed.
CREATE_ENGINE_ALLOWLIST: dict[tuple[str, str], str] = {
    ("src/idis/persistence/db.py", "get_app_engine"): (
        "url is get_database_url(admin=False), which returns _ensure_psycopg_driver "
        "(src/idis/persistence/db.py:99)"
    ),
    ("src/idis/persistence/db.py", "get_admin_engine"): (
        "url is get_database_url(admin=True), which returns _ensure_psycopg_driver "
        "(src/idis/persistence/db.py:99)"
    ),
}

_PRODUCT_SITES = frozenset(
    {
        ("src/idis/services/rag/pgvector_health.py", "_default_extension_probe"),
        ("src/idis/runtime/strict_launcher.py", "_ensure_migration_database"),
        (
            "src/idis/runtime/strict_launcher.py",
            "_grant_and_verify_migration_privileges",
        ),
        (
            "src/idis/runtime/dependency_probes.py",
            "build_default_dependency_probe_adapters.erasure_connection_factory",
        ),
    }
)


class _NoConnect(RuntimeError):
    """Raised by the create_engine double before any connection is opened."""


@dataclass(frozen=True)
class _EngineCall:
    path: str
    function: str
    line: int
    produced_by_helper: bool


def _install_capture(
    monkeypatch: pytest.MonkeyPatch,
) -> list[tuple[object, dict[str, object]]]:
    import sqlalchemy

    captured: list[tuple[object, dict[str, object]]] = []

    def fake_create_engine(*args: object, **kwargs: object) -> object:
        if not args:
            raise AssertionError("create_engine received no URL")
        captured.append((args[0], dict(kwargs)))
        raise _NoConnect

    monkeypatch.setattr(sqlalchemy, "create_engine", fake_create_engine)
    return captured


def _delivered(raw_url: str, *, database: str) -> str:
    expected = dict(_URL_CASES)[raw_url]
    if database == "idis":
        return expected
    prefix, _, _name = expected.rpartition("/idis")
    return f"{prefix}/{database}"


def _migrate_profile(tmp_path: Path, raw_url: str) -> Any:
    return _reviewed_profile(
        tmp_path,
        "migrate",
        {
            "IDIS_DATABASE_URL": raw_url,
            "IDIS_DATABASE_ADMIN_URL": raw_url,
        },
        generation="bootstrap-v1",
        promoted="0",
    )


def _postgres_identity(profile: Any) -> tuple[Any, str, str]:
    """Supply a postgres:// URL object past the postgresql backend gate.

    ``_migration_database_identity`` rejects backend name ``postgres`` before
    either engine site (strict_launcher.py:317-331). The engine sites still have
    to normalise that URL when it is the object they pass to create_engine.
    """
    from sqlalchemy.engine import make_url

    admin_url = make_url(profile.values["IDIS_DATABASE_ADMIN_URL"])
    database = admin_url.database
    username = admin_url.username
    if not isinstance(database, str) or not isinstance(username, str):
        raise AssertionError("postgres URL did not parse")
    return admin_url, database, username


def _invoke_launcher(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    raw_url: str,
    function_name: str,
) -> tuple[object, dict[str, object]]:
    import idis.runtime.strict_launcher as launcher

    captured = _install_capture(monkeypatch)
    if raw_url.startswith("postgres://"):
        monkeypatch.setattr(launcher, "_migration_database_identity", _postgres_identity)
    profile = _migrate_profile(tmp_path, raw_url)
    with pytest.raises(launcher.StrictLauncherError):
        getattr(launcher, function_name)(profile)
    assert len(captured) == 1
    return captured[0]


@pytest.mark.parametrize(("raw_url", "engine_url"), _URL_CASES)
def test_pgvector_probe_delivers_declared_driver(
    monkeypatch: pytest.MonkeyPatch,
    raw_url: str,
    engine_url: str,
) -> None:
    """Bare and postgres schemes reach create_engine as postgresql+psycopg2."""
    from idis.services.rag.pgvector_health import _default_extension_probe

    captured = _install_capture(monkeypatch)
    with pytest.raises(_NoConnect):
        _default_extension_probe(raw_url)
    assert captured == [(engine_url, {"pool_pre_ping": True})]


@pytest.mark.parametrize(("raw_url", "engine_url"), _URL_CASES)
def test_migration_database_engine_delivers_declared_driver(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    raw_url: str,
    engine_url: str,
) -> None:
    """The maintenance engine keeps database postgres and the declared driver."""
    del engine_url
    url, kwargs = _invoke_launcher(
        monkeypatch,
        tmp_path,
        raw_url,
        "_ensure_migration_database",
    )
    assert url == _delivered(raw_url, database="postgres")
    assert kwargs == {"isolation_level": "AUTOCOMMIT"}


@pytest.mark.parametrize(("raw_url", "engine_url"), _URL_CASES)
def test_migration_privilege_engine_delivers_declared_driver(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    raw_url: str,
    engine_url: str,
) -> None:
    """The privilege engine keeps the target database and the declared driver."""
    url, kwargs = _invoke_launcher(
        monkeypatch,
        tmp_path,
        raw_url,
        "_grant_and_verify_migration_privileges",
    )
    assert url == engine_url
    assert kwargs == {}


@pytest.mark.parametrize(("raw_url", "engine_url"), _URL_CASES)
def test_erasure_admission_engine_delivers_declared_driver(
    monkeypatch: pytest.MonkeyPatch,
    raw_url: str,
    engine_url: str,
) -> None:
    """The erasure admission engine receives the helper output and the same pool."""
    from idis.runtime.dependency_probes import build_default_dependency_probe_adapters

    captured = _install_capture(monkeypatch)
    adapters = build_default_dependency_probe_adapters(
        {"IDIS_ERASURE_DATABASE_URL": raw_url},
        expected_public_key_sha256="0" * 64,
    )
    probe = adapters.erasure_executor
    assert probe is not None
    free_vars = probe.__code__.co_freevars
    assert "erasure_connection_factory" in free_vars
    closure = probe.__closure__
    assert closure is not None
    factory = closure[free_vars.index("erasure_connection_factory")].cell_contents
    with pytest.raises(_NoConnect):
        factory()
    assert captured == [
        (
            engine_url,
            {
                "pool_size": 1,
                "max_overflow": 0,
                "pool_pre_ping": True,
                "echo": False,
            },
        )
    ]


def _is_create_engine(node: ast.Call) -> bool:
    func = node.func
    if isinstance(func, ast.Name):
        return func.id == "create_engine"
    return isinstance(func, ast.Attribute) and func.attr == "create_engine"


def _is_helper_call(node: ast.AST) -> bool:
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    if isinstance(func, ast.Name):
        return func.id == "_ensure_psycopg_driver"
    return isinstance(func, ast.Attribute) and func.attr == "_ensure_psycopg_driver"


def _url_argument(node: ast.Call) -> ast.AST | None:
    if node.args:
        return node.args[0]
    for keyword in node.keywords:
        if keyword.arg == "url":
            return keyword.value
    return None


def _name_is_helper_product(function: ast.AST, name: str) -> bool:
    found = False
    for child in ast.walk(function):
        if isinstance(child, ast.Assign):
            targets = child.targets
            value = child.value
        elif isinstance(child, ast.AnnAssign) and child.value is not None:
            targets = [child.target]
            value = child.value
        else:
            continue
        for target in targets:
            if isinstance(target, ast.Name) and target.id == name:
                found = True
                if not _is_helper_call(value):
                    return False
    return found


def _argument_is_helper_product(function: ast.AST | None, argument: ast.AST | None) -> bool:
    if argument is None:
        return False
    if _is_helper_call(argument):
        return True
    if function is not None and isinstance(argument, ast.Name):
        return _name_is_helper_product(function, argument.id)
    return False


def _calls_in_module(relative_path: str, module: ast.AST) -> list[_EngineCall]:
    calls: list[_EngineCall] = []
    stack: list[tuple[str, ast.AST]] = []

    class _Visitor(ast.NodeVisitor):
        def visit_ClassDef(self, node: ast.ClassDef) -> None:
            stack.append((node.name, node))
            self.generic_visit(node)
            stack.pop()

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            stack.append((node.name, node))
            self.generic_visit(node)
            stack.pop()

        visit_AsyncFunctionDef = visit_FunctionDef

        def visit_Call(self, node: ast.Call) -> None:
            if _is_create_engine(node):
                function = stack[-1][1] if stack else None
                names = [item[0] for item in stack if isinstance(item[1], ast.FunctionDef)]
                calls.append(
                    _EngineCall(
                        path=relative_path,
                        function=".".join(names) if names else "<module>",
                        line=node.lineno,
                        produced_by_helper=_argument_is_helper_product(
                            function,
                            _url_argument(node),
                        ),
                    )
                )
            self.generic_visit(node)

    _Visitor().visit(module)
    return calls


def _engine_calls(root: Path) -> list[_EngineCall]:
    calls: list[_EngineCall] = []
    for path in sorted(root.joinpath("src").rglob("*.py")):
        module = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        calls.extend(_calls_in_module(path.relative_to(root).as_posix(), module))
    return calls


def test_every_create_engine_uses_helper_or_explicit_allowlist() -> None:
    """Each src create_engine receives _ensure_psycopg_driver or is allowlisted."""
    root = Path(__file__).resolve().parents[1]
    calls = _engine_calls(root)
    assert calls
    for reason in CREATE_ENGINE_ALLOWLIST.values():
        assert reason.strip()
    assert not (set(CREATE_ENGINE_ALLOWLIST) & _PRODUCT_SITES)
    unprotected = {(call.path, call.function) for call in calls if not call.produced_by_helper}
    rendered = "\n".join(
        f"{call.path}:{call.line} {call.function} helper={call.produced_by_helper}"
        for call in calls
    )
    assert unprotected == set(CREATE_ENGINE_ALLOWLIST), rendered
