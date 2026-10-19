"""Record-and-pass gate for operator PostgreSQL preconditions.

A present variable set runs the wrapped test unchanged. Absence prints one
``OPERATOR_POSTGRES NOT_EXECUTED`` line, records it, and returns. The wrapper
never skips. ``IDIS_REQUIRE_POSTGRES=1`` is left to the caller's existing
failure and is checked before ``IDIS_REQUIRE_OPERATOR_POSTGRES=1``.
"""

from __future__ import annotations

import functools
import inspect
import os
from collections.abc import Callable
from typing import Any

import pytest

_REQUIRE_POSTGRES_ENV = "IDIS_REQUIRE_POSTGRES"
_REQUIRE_OPERATOR_POSTGRES_ENV = "IDIS_REQUIRE_OPERATOR_POSTGRES"
_RECORD_NAME = "operator_postgres"
_RECORD_PROPERTY: Callable[[str, object], None] | None = None

OPERATOR_POSTGRES_GATED_NODES: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    (
        "tests/slice100_task7/test_erasure_executor_operator_postgres_red.py",
        "test_dedicated_erasure_credential_passes_the_real_catalog_readiness_contract",
        ("IDIS_DATABASE_URL", "IDIS_ERASURE_DATABASE_URL"),
    ),
    (
        "tests/slice100_task7/test_v19_erasure_api_postgres_red.py",
        "test_application_role_cannot_execute_the_dedicated_function",
        ("IDIS_DATABASE_ADMIN_URL", "IDIS_ERASURE_DATABASE_URL"),
    ),
    (
        "tests/slice100_task7/test_v19_erasure_api_postgres_red.py",
        "test_same_tenant_api_uses_only_dedicated_authority_and_persists_terminal_lineage",
        ("IDIS_DATABASE_ADMIN_URL", "IDIS_ERASURE_DATABASE_URL"),
    ),
    (
        "tests/slice100_task7/test_v19_erasure_api_postgres_red.py",
        "test_cross_tenant_api_returns_non_enumerating_404_with_zero_mutation",
        ("IDIS_DATABASE_ADMIN_URL", "IDIS_ERASURE_DATABASE_URL"),
    ),
    (
        "tests/slice100_task7/test_v19_erasure_api_postgres_red.py",
        "test_post_function_crash_reconstructs_terminal_once_and_suppresses_only_same_admin",
        ("IDIS_DATABASE_ADMIN_URL", "IDIS_ERASURE_DATABASE_URL"),
    ),
    (
        "tests/slice100_task7/test_v19_erasure_postgres_app_role_red.py",
        "test_v21_owner_acl_and_readiness_are_catalog_proven_without_admin_assertions",
        ("IDIS_ERASURE_DATABASE_URL",),
    ),
    (
        "tests/slice100_task7/test_v19_erasure_postgres_app_role_red.py",
        "test_v21_function_precondition_atomic_terminal_and_crash_open_state",
        ("IDIS_ERASURE_DATABASE_URL",),
    ),
)


def missing_operator_variables(*variable_names: str) -> tuple[str, ...]:
    """Return named variables that are unset or blank, in argument order."""

    return tuple(name for name in variable_names if not os.environ.get(name, "").strip())


def _validate_variables(variable_names: tuple[str, ...]) -> None:
    if not variable_names:
        raise ValueError("operator postgres gate requires at least one variable")
    if any(not isinstance(name, str) or not name.strip() for name in variable_names):
        raise ValueError("operator postgres variable names must be non-empty strings")


def _failure_message(failure_code: str, missing: tuple[str, ...]) -> str:
    if "{name}" not in failure_code:
        return failure_code
    return failure_code.replace("{name}", missing[0])


def _absence_line(missing: tuple[str, ...]) -> str:
    return f"OPERATOR_POSTGRES NOT_EXECUTED: {','.join(missing)} absent"


def _record_absence(
    missing: tuple[str, ...],
    record_property: Callable[[str, object], None] | None,
) -> str:
    line = _absence_line(missing)
    print(line)
    if record_property is not None:
        record_property(_RECORD_NAME, line)
    return line


def operator_postgres_fixture_blocked(*variable_names: str, failure_code: str) -> bool:
    """Return whether a fixture should yield no database.

    ``IDIS_REQUIRE_POSTGRES=1`` fails first with ``failure_code``.
    ``IDIS_REQUIRE_OPERATOR_POSTGRES=1`` fails with that same code. Otherwise
    a missing variable returns True so the fixture can yield without skipping.
    Per-node recording stays on the test decorator: a module fixture runs once.
    """

    _validate_variables(variable_names)
    if not failure_code:
        raise ValueError("operator postgres fixture gate requires a failure code")
    missing = missing_operator_variables(*variable_names)
    if not missing:
        return False
    if os.environ.get(_REQUIRE_POSTGRES_ENV) == "1":
        pytest.fail(_failure_message(failure_code, missing), pytrace=False)
    if os.environ.get(_REQUIRE_OPERATOR_POSTGRES_ENV) == "1":
        pytest.fail(_failure_message(failure_code, missing), pytrace=False)
    return True


def _published_signature(test_fn: Callable[..., Any]) -> inspect.Signature:
    signature = inspect.signature(test_fn)
    if "record_property" in signature.parameters:
        return signature
    parameters = [
        *signature.parameters.values(),
        inspect.Parameter("record_property", inspect.Parameter.KEYWORD_ONLY),
    ]
    return signature.replace(parameters=parameters)


def _call_test(
    test_fn: Callable[..., Any],
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
    *,
    accepts_recorder: bool,
) -> Any:
    call_kwargs = kwargs
    if not accepts_recorder:
        call_kwargs = {key: value for key, value in kwargs.items() if key != "record_property"}
    return test_fn(*args, **call_kwargs)


def requires_operator_postgres(
    *variable_names: str,
    failure_code: str,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Run the test when every named operator-PostgreSQL variable is non-empty.

    Absence prints one ``OPERATOR_POSTGRES NOT_EXECUTED`` line, records it on
    ``operator_postgres``, and returns. ``IDIS_REQUIRE_OPERATOR_POSTGRES=1``
    fails with ``failure_code`` (``{name}`` is the first missing variable).
    ``IDIS_REQUIRE_POSTGRES=1`` runs the wrapped test so its existing failure
    happens first. The wrapper never skips.
    """

    _validate_variables(variable_names)
    if not failure_code:
        raise ValueError("requires_operator_postgres requires a failure code")

    def _decorate(test_fn: Callable[..., Any]) -> Callable[..., Any]:
        signature = inspect.signature(test_fn)
        accepts_recorder = "record_property" in signature.parameters

        @functools.wraps(test_fn)
        def _run(*args: Any, **kwargs: Any) -> Any:
            supplied = kwargs.get("record_property")
            recorder = supplied if isinstance(supplied, Callable) else _RECORD_PROPERTY
            missing = missing_operator_variables(*variable_names)
            if not missing or os.environ.get(_REQUIRE_POSTGRES_ENV) == "1":
                return _call_test(
                    test_fn,
                    args,
                    kwargs,
                    accepts_recorder=accepts_recorder,
                )
            _record_absence(missing, recorder)
            if os.environ.get(_REQUIRE_OPERATOR_POSTGRES_ENV) == "1":
                pytest.fail(_failure_message(failure_code, missing), pytrace=False)
            return None

        _run.__signature__ = _published_signature(test_fn)  # type: ignore[attr-defined]
        # Pytest follows __wrapped__ back to the undecorated signature, which
        # would drop the injected record_property fixture.
        del _run.__wrapped__
        return _run

    return _decorate
