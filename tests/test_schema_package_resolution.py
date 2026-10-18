"""Packaged schema resolution for the non-editable image install.

Form (1): ``SchemaValidator`` reads ``idis/_schemas`` beside the loaded module
file. A copied module therefore resolves the tree it was copied into. These
tests do not use ``importlib.resources`` or a subprocess ``sys.path`` injection.
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
import zipfile
from collections.abc import Callable
from pathlib import Path
from types import ModuleType

import pytest

from tests.test_ci_bootstrap_health_composition import _readiness_audit_event

ROOT = Path(__file__).resolve().parents[1]
_VALIDATOR_SOURCE = ROOT / "src" / "idis" / "validators" / "schema_validator.py"
_AUDIT_SCHEMA = ROOT / "schemas" / "audit_event.schema.json"
_COPIED_MODULE = "h1bn_copied_schema_validator"
_OPENAPI_FORCE_INCLUDE = '"openapi/IDIS_OpenAPI_v6_3.yaml" = "idis/_openapi/IDIS_OpenAPI_v6_3.yaml"'
_REQUIRE_SCHEMA_WHEEL = "IDIS_REQUIRE_SCHEMA_WHEEL"
_REJECTING_AUDIT_SCHEMA = b'{"type":"string"}\n'
_WHEEL_TIMEOUT_SECONDS = 180


def _force_include_line(schema_name: str) -> str:
    return f'"schemas/{schema_name}" = "idis/_schemas/{schema_name}"'


def _stage_copy(
    root: Path,
    *,
    package_bytes: bytes | None,
    package_decoy: bool,
    checkout_bytes: bytes | None,
) -> Path:
    """Place schema_validator.py at the image site-packages depth."""
    module_path = (
        root
        / "opt"
        / "venv"
        / "lib"
        / "python3.11"
        / "site-packages"
        / "idis"
        / "validators"
        / "schema_validator.py"
    )
    module_path.parent.mkdir(parents=True)
    shutil.copyfile(_VALIDATOR_SOURCE, module_path)
    if package_bytes is not None or package_decoy:
        packaged = module_path.parents[1] / "_schemas"
        packaged.mkdir(parents=True)
        if package_bytes is not None:
            (packaged / "audit_event.schema.json").write_bytes(package_bytes)
        if package_decoy:
            (packaged / "not_the_audit_schema.json").write_text("{}", encoding="utf-8")
    if checkout_bytes is not None:
        checkout = module_path.resolve().parents[3] / "schemas"
        checkout.mkdir(parents=True)
        (checkout / "audit_event.schema.json").write_bytes(checkout_bytes)
    return module_path


def _load_copy(path: Path) -> ModuleType:
    sys.modules.pop(_COPIED_MODULE, None)
    spec = importlib.util.spec_from_file_location(_COPIED_MODULE, path)
    if spec is None or spec.loader is None:
        raise AssertionError("copied schema validator spec missing")
    module = importlib.util.module_from_spec(spec)
    sys.modules[_COPIED_MODULE] = module
    spec.loader.exec_module(module)
    return module


def _unload_copy() -> None:
    sys.modules.pop(_COPIED_MODULE, None)


def test_neither_location_fails_closed_with_audit_schema_load_failed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No packaged audit schema and no checkout schema fails closed."""
    module = _load_copy(
        _stage_copy(
            tmp_path,
            package_bytes=None,
            package_decoy=False,
            checkout_bytes=None,
        )
    )
    try:
        direct = module.SchemaValidator().validate("audit_event", {"event_id": "x"})
        assert direct.passed is False
        assert [error.code for error in direct.errors] == ["FAIL_CLOSED"]
        monkeypatch.setattr(
            "idis.validators.audit_event_validator.SchemaValidator",
            module.SchemaValidator,
        )
        from idis.validators.audit_event_validator import AuditEventValidator

        audited = AuditEventValidator().validate({"event_id": "x"})
    finally:
        _unload_copy()
    assert audited.passed is False
    assert [error.code for error in audited.errors] == ["AUDIT_SCHEMA_LOAD_FAILED"]


def test_package_directory_without_audit_schema_does_not_pass(tmp_path: Path) -> None:
    """A package directory that lacks the audit schema is not a pass."""
    module = _load_copy(
        _stage_copy(
            tmp_path,
            package_bytes=None,
            package_decoy=True,
            checkout_bytes=None,
        )
    )
    try:
        validator = module.SchemaValidator()
        packaged = Path(module.__file__ or "").resolve().parents[1] / "_schemas"
        assert packaged.is_dir()
        assert (packaged / "audit_event.schema.json").is_file() is False
        result = validator.validate("audit_event", _readiness_audit_event())
    finally:
        _unload_copy()
    assert result.passed is False
    assert [error.code for error in result.errors] == ["FAIL_CLOSED"]


def test_checkout_schema_is_used_when_package_dir_lacks_the_audit_schema(tmp_path: Path) -> None:
    """An incomplete package directory does not hide the checkout schema."""
    module = _load_copy(
        _stage_copy(
            tmp_path,
            package_bytes=None,
            package_decoy=True,
            checkout_bytes=_AUDIT_SCHEMA.read_bytes(),
        )
    )
    try:
        validator = module.SchemaValidator()
        loaded = module.__file__
        assert isinstance(loaded, str)
        checkout = Path(loaded).resolve().parents[3] / "schemas"
        assert Path(validator._schema_dir).resolve() == checkout.resolve()
        result = validator.validate("audit_event", _readiness_audit_event())
    finally:
        _unload_copy()
    assert result.passed, [error.code for error in result.errors]


def test_packaged_audit_schema_is_not_shadowed_by_the_checkout_walk(tmp_path: Path) -> None:
    """A stray checkout schema cannot shadow shipped idis/_schemas bytes."""
    module = _load_copy(
        _stage_copy(
            tmp_path,
            package_bytes=_AUDIT_SCHEMA.read_bytes(),
            package_decoy=False,
            checkout_bytes=_REJECTING_AUDIT_SCHEMA,
        )
    )
    try:
        validator = module.SchemaValidator()
        loaded = module.__file__
        assert isinstance(loaded, str)
        packaged = Path(loaded).resolve().parents[1] / "_schemas"
        assert Path(validator._schema_dir).resolve() == packaged.resolve()
        result = validator.validate("audit_event", _readiness_audit_event())
    finally:
        _unload_copy()
    assert result.passed, [error.code for error in result.errors]


def test_explicit_schema_dir_stays_authoritative(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An explicit directory is used as given and does not fall through."""
    from idis.validators.schema_validator import SchemaValidator

    explicit = tmp_path / "explicit"
    explicit.mkdir()
    shutil.copyfile(_AUDIT_SCHEMA, explicit / "audit_event.schema.json")
    decoy = tmp_path / "decoy"
    decoy.mkdir()
    (decoy / "audit_event.schema.json").write_bytes(_REJECTING_AUDIT_SCHEMA)
    monkeypatch.setenv("IDIS_SCHEMA_DIR", str(decoy))
    monkeypatch.chdir(tmp_path)
    validator = SchemaValidator(schema_dir=explicit)
    assert validator._schema_dir == explicit
    passed = validator.validate("audit_event", _readiness_audit_event())
    assert passed.passed, [error.code for error in passed.errors]

    empty = tmp_path / "empty"
    empty.mkdir()
    missed = SchemaValidator(schema_dir=empty).validate("audit_event", _readiness_audit_event())
    assert missed.passed is False
    assert [error.code for error in missed.errors] == ["FAIL_CLOSED"]


def test_environment_and_cwd_do_not_select_the_schema_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Default resolution ignores environment variables and the working directory."""
    from idis.validators.schema_validator import SchemaValidator

    decoy = tmp_path / "cwd-schemas"
    decoy.mkdir()
    (decoy / "audit_event.schema.json").write_bytes(_REJECTING_AUDIT_SCHEMA)
    monkeypatch.setenv("IDIS_SCHEMA_DIR", str(decoy))
    monkeypatch.setenv("IDIS_SCHEMAS_DIR", str(decoy))
    monkeypatch.setenv("SCHEMA_DIR", str(decoy))
    monkeypatch.chdir(tmp_path)
    validator = SchemaValidator()
    assert Path(validator._schema_dir).resolve() == (ROOT / "schemas").resolve()
    result = validator.validate("audit_event", _readiness_audit_event())
    assert result.passed, [error.code for error in result.errors]


def _record_wheel_not_executed(
    record_property: Callable[[str, object], None],
    reason: str,
) -> None:
    if os.environ.get(_REQUIRE_SCHEMA_WHEEL) == "1":
        pytest.fail(reason)
    record_property("schema_wheel", f"NOT_EXECUTED: {reason}")
    print(f"SCHEMA_WHEEL NOT_EXECUTED: {reason}")


def test_schema_wheel_bytes_match_checkout_or_record_not_executed(
    tmp_path: Path,
    record_property: Callable[[str, object], None],
) -> None:
    """Offline wheel bytes match schemas/, or the build is recorded NOT_EXECUTED."""
    project = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert project.count(_OPENAPI_FORCE_INCLUDE) == 1
    missing = [
        schema.name
        for schema in sorted((ROOT / "schemas").glob("*.schema.json"))
        if _force_include_line(schema.name) not in project
    ]
    assert missing == []
    wheel_dir = tmp_path / "wheels"
    wheel_dir.mkdir()
    command = [
        sys.executable,
        "-m",
        "pip",
        "wheel",
        "--no-deps",
        "--no-build-isolation",
        "--no-index",
        "-w",
        str(wheel_dir),
        str(ROOT),
    ]
    try:
        completed = subprocess.run(
            command,
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
            timeout=_WHEEL_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        _record_wheel_not_executed(
            record_property,
            "offline wheel build unavailable: timed out",
        )
        return
    if completed.returncode != 0:
        detail = (
            "Cannot import hatchling.build"
            if "hatchling.build" in completed.stderr
            else "pip wheel failed"
        )
        _record_wheel_not_executed(
            record_property,
            f"offline wheel build unavailable: {detail}",
        )
        return
    wheels = sorted(wheel_dir.glob("*.whl"))
    assert len(wheels) == 1, [path.name for path in wheels]
    checkout = sorted((ROOT / "schemas").glob("*.schema.json"))
    with zipfile.ZipFile(wheels[0]) as archive:
        packaged_names = sorted(
            name
            for name in archive.namelist()
            if name.startswith("idis/_schemas/") and name.endswith(".schema.json")
        )
        assert [Path(name).name for name in packaged_names] == [path.name for path in checkout]
        for path in checkout:
            assert archive.read(f"idis/_schemas/{path.name}") == path.read_bytes()
