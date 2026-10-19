"""RED-first contracts for the full-strict-v27 PostgreSQL census.

This module is intentionally hermetic: it may inspect and execute pure planning
code, but it must never load an operator profile or read a signing key.
``test_v27_create_and_drop_disposable_database_against_postgres`` is the only
node that opens PostgreSQL, and only through the admin URL the other database
tests already use. Operator-backed census execution stays a separate gate.
"""

from __future__ import annotations

import ast
import hashlib
import importlib.util
import inspect
import json
import os
import shutil
import stat
import subprocess
import sys
import traceback
import uuid
from copy import deepcopy
from pathlib import Path
from types import ModuleType

import pytest

from tests.slice100_task8 import _task8_void_contract_hash_evidence as void_evidence
from tests.slice100_task8 import _v27_oracle as v27_oracle

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

SQL_RUNNER = REPO_ROOT / "scripts" / "run_task8_v27_sql_authority_census.py"
SIGNED_RUNNER = REPO_ROOT / "scripts" / "run_task8_v27_signed_census.py"
STAGED_ARTIFACT = (
    REPO_ROOT
    / ".local_reports"
    / "slice100"
    / "task8"
    / "v27-candidate"
    / "full_strict_v27_reviewed_preimage_v1.json"
)
PARENT_V21 = REPO_ROOT / "contracts" / "full_strict_v21_reviewed_preimage_v1.json"
POLICY_V2 = REPO_ROOT / "contracts" / "fdd_formula_policy_v2.json"
REVIEWER_MAP = (
    REPO_ROOT / "docs" / "plans" / "2026-07-25-slice100-task8-fdd-formula-policy-v2-review-map.md"
)
INDEPENDENT_VERIFIER = REPO_ROOT / "scripts" / "verify_full_strict_v27_fixture_independently.py"

ARTIFACT_SHA256 = "4169371675ce32c2b4b459528dcdf21914887941d952648597bce59e49a8ac06"
FIXTURE_SHA256 = "78a3aedb04260cc56e98a2a9193ce489ca73bd4b5b95fc1486f676bfbd07b0cf"
CONTRACT_SHA256 = "5221e622a40c44b4f2fe38e53e0208a7e1dba5358278d75624a0cc1c40ef0985"
GRAPH_SHA256 = "a951b96575f5c2927c59185f20378fde6ce019c207b77510a1033f4947649e3c"
CONSERVATION_SHA256 = "32a06ca41841c196ae76ad3d33b3d15da4dec8a247343b9a629390da8128475d"
POLICY_BINDING_SHA256 = "9e41ea12908a5c37542f6156d4bbf22dc19d59ac172d096a84fc55c4db83d2ff"
POLICY_ARTIFACT_SHA256 = "e0e99763e7ffaa0abbf436ac102894a9f3cac15e57448c2f90019ee7cf39e064"
POLICY_SHA256 = "2b9f141a54628dbc8528813cc2f58cdaa2fe3eb09925a74f881f56baac7076e9"
REVIEWER_MAP_SHA256 = "98cf84d4498f57465cd61501ea8bbbd93f7507d8628a61d7cbcf9cede459fe2a"
PARENT_ARTIFACT_SHA256 = "2eb504ab750e9c3c96a5eeada13b22aad36c8772369c21e65d0e93705ff9cffd"
CURRENT_PIPELINE_VERSION = "full-strict-v21"
CURRENT_PIPELINE_HASH = "46d14b1fbf6f7790bdcfd16ee211446d80ed4c95b22ec04810cd912e28706794"

EXPECTED_TASK5_QUESTION_IDENTITY_FIELDS = (
    "tenant_id",
    "methodology_question_record_id",
    "methodology_version_record_id",
    "methodology_version_id",
    "methodology_question_id",
    "methodology_id",
    "methodology_type",
    "question_ordinal",
    "section",
    "source_type",
    "source_name",
    "source_sheet_or_section",
    "source_row_number",
    "source_anchor_cell",
    "source_sha256",
    "semantic_row_hash",
    "question_payload_hash",
    "full_question_payload_sha256",
    "required_calculations_json",
)
EXPECTED_TASK5_REGISTRY_IDENTITY_FIELDS = (
    "schema_version",
    "tenant_id",
    "methodology_version_record_id",
    "methodology_version_id",
    "methodology_id",
    "methodology_type",
    "version_label",
    "source_type",
    "source_alias",
    "source_sha256",
    "source_byte_count",
    "source_contract_hash",
    "source_trace_sha256",
    "registry_sha256",
    "version_sha256",
    "question_count",
    "source_section_count",
    "header_row",
    "status",
    "timestamp_redaction_field_tuple",
    "deterministic_registry_bytes_sha256",
    "methodology_question_payload_hashes",
    "question_identity_field_tuple",
    "question_identity_tuple",
    "question_identity_tuple_sha256",
    "required_calculation_tuple",
    "required_calculation_tuple_sha256",
)
EXPECTED_TASK5_SELECTION_IDENTITY_FIELDS = (
    "selection_id",
    "tenant_id",
    "deal_id",
    "run_id",
    "commercial_dd_version_record_id",
    "financial_dd_version_record_id",
    "selected_version_ids",
    "selected_source_hashes",
    "selected_registry_hashes",
    "selected_version_hashes",
    "selection_hash",
    "selected_question_count",
    "state",
    "immutable",
)

EXPECTED_DML_SEQUENCE_SHA256 = "f36eecb83fdfdb61a5fe91c9b4ece935550fd5aa795400dc66450531e0118e42"
EXPECTED_MIGRATION_EXECUTION_SOURCE_SHA256 = (
    "1b8aa20b7684bb259545b282c9f70a12107bfa15981f54116246a6102c4fb243"
)
# Public historical inputs ONLY; never a replacement for the current runtime.
_HISTORICAL_SOURCE_COMMIT = "17c2b3b265b3c09121febd90c61a368e827a00ab"
_HISTORICAL_INTERNAL_SOURCES = (
    (
        "src/idis/persistence/db.py",
        "4f9818e15368641d252607edcad29b0a57e1832f",
        8274,
        "9e359cb8a69b370ae31c6ef313ac80ffd6f288adb83e17fdd2a3fc71ae9227eb",
    ),
    (
        "src/idis/models/run_step.py",
        "286209b55f4406921fa312b451be6f8c1ca5e330",
        9496,
        "19f6eb21d96f121c6ab9bd8c68413dabec31005bdeef0240c24772a15fcc7715",
    ),
)
EXPECTED_REQUIREMENT_CASE_IDS = (
    "LEGACY_FOUR_FIELD",
    "POLICY_V2_TWO_FIELD",
    "UNKNOWN_FORMULA_TYPE",
    "ORDINAL_DRIFT",
    "SCOPE_DRIFT",
    "UNDECLARED_FDD_CALCULATION_ROOT",
    "MIXED_SHAPES",
    "POLICY_V2_EXTRA_FIELD",
    "POLICY_V2_MISSING_FIELD",
    "LEGACY_EXTRA_FIELD",
    "LEGACY_MISSING_FIELD",
    "CROSS_VERSION_JOIN",
    "POLICY_V2_DUPLICATE_REQUIREMENT_TUPLE",
)
EXPECTED_BEHAVIOR_DML_CASES = (
    ("dashboard-gap-all-not-found", "APPLICATION", "COMMIT", True, False, False, False),
    ("dashboard-mixed-answered", "APPLICATION", "COMMIT", True, False, False, False),
    ("dashboard-mixed-red-flag", "APPLICATION", "COMMIT", True, False, False, False),
    ("dashboard-all-found-answered", "APPLICATION", "COMMIT", True, False, False, False),
    ("dashboard-required-formula-missing", "APPLICATION", "ROLLBACK", True, False, False, False),
    ("dashboard-gap-with-found-result", "APPLICATION", "ROLLBACK", True, False, False, False),
    ("dashboard-answered-without-evidence", "APPLICATION", "ROLLBACK", True, False, False, False),
    ("dashboard-red-flag-without-defect", "APPLICATION", "ROLLBACK", True, False, False, False),
    ("dashboard-global-missing-row", "APPLICATION", "ROLLBACK", True, False, False, False),
    ("dashboard-global-source-count-99", "APPLICATION", "ROLLBACK", True, False, False, False),
    ("dashboard-global-extra-noncoverage", "APPLICATION", "ROLLBACK", True, False, False, False),
    ("dashboard-global-duplicate-coverage", "APPLICATION", "ROLLBACK", True, False, False, False),
    ("dashboard-direct-insert-bypass", "APPLICATION", "ROLLBACK", True, False, False, False),
    ("dashboard-direct-update", "APPLICATION", "ROLLBACK", True, False, False, True),
    ("dashboard-direct-delete", "APPLICATION", "ROLLBACK", True, False, False, True),
    ("dashboard-root-link-direct-update", "APPLICATION", "ROLLBACK", True, False, False, True),
    ("dashboard-root-link-direct-delete", "APPLICATION", "ROLLBACK", True, False, False, True),
    ("partial-root-exactly-one", "APPLICATION", "COMMIT", True, False, False, False),
    ("partial-root-missing", "APPLICATION", "ROLLBACK", True, False, False, False),
    ("partial-root-duplicate", "APPLICATION", "ROLLBACK", True, False, False, False),
    ("partial-root-direct-sql", "APPLICATION", "ROLLBACK", True, False, False, False),
    ("erasure-current48-without-dashboard", "ERASURE", "COMMIT", False, False, True, False),
    ("erasure-dashboard-refusal", "ERASURE", "ROLLBACK", True, True, True, False),
    ("erasure-application-execute-denied", "APPLICATION", "ROLLBACK", False, False, True, False),
    ("erasure-executor-direct-refusal", "ERASURE", "ROLLBACK", True, True, True, False),
)

EXPECTED_STEP_ROWS = (
    (
        4,
        "METHODOLOGY_COVERAGE_INIT",
        "5b157c8329a0a1c229f6ffb3ec2cfc0c0be1c6fb85f0dc0a669883aa1eb75dbc",
    ),
    (
        5,
        "METHODOLOGY_EXTRACTION_TASK_PLANNING",
        "07e0980ebfa04d3aae5042ec39b07200cacc87cc5f8ad40db6338faa56bb38af",
    ),
    (
        6,
        "METHODOLOGY_EXTRACTION_TASK_EXECUTION",
        "0550f5d7335852771e830ae9f376a9633813a24fd7afc677c32c45ee1446dded",
    ),
    (
        7,
        "METHODOLOGY_CLAIM_MATERIALIZATION",
        "63ae11aa758eea76bfa4931cc35ea6bd2b42a827d58b7f058f000db2e439792c",
    ),
    (
        8,
        "METHODOLOGY_EVIDENCE_ITEM_MATERIALIZATION",
        "7ca82a99ce4bf6782d76b17fdc8434bf98fc92e1c4626bcf2121004bed63f94e",
    ),
    (
        9,
        "METHODOLOGY_SANAD_CREATION_LINKING_GRADING",
        "ca4f4b8c77d274999f9593829ff76cbe0839e05f82082a37d8554cc1bff2dd52",
    ),
    (
        10,
        "METHODOLOGY_DETERMINISTIC_CALCULATION",
        "2db75b6695f26cfbf7f09ef0a440da557c15eb9b960be86d78f62f70412617ed",
    ),
    (
        11,
        "METHODOLOGY_TRUTH_DASHBOARD",
        "29a48e733fe07251ed0bb509311ddfbb36936876913c84ce149e79cd5336eea9",
    ),
)


def _load(path: Path, name: str) -> ModuleType:
    assert path.is_file(), f"missing approved implementation: {path.name}"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _sql() -> ModuleType:
    return _load(SQL_RUNNER, "_task8_v27_sql_census_red")


def _signed() -> ModuleType:
    return _load(SIGNED_RUNNER, "_task8_v27_signed_census_red")


def _verifier() -> ModuleType:
    return _load(INDEPENDENT_VERIFIER, "_task8_v27_census_independent_verifier")


def _materialize_historical_internal_sources(destination: Path) -> Path:
    """Extract exactly two pinned local Git blobs; no checkout, fetch or fallback."""
    try:
        if destination.absolute() != destination.resolve():
            raise ValueError("alias")
        destination.mkdir(mode=0o700)
        git = shutil.which("git")
        if git is None:
            raise ValueError("git unavailable")
        # Reuse the existing Git-environment policy without private-CI authority.
        environment = {
            key: os.environ[key] for key in ("PATH", "SYSTEMROOT", "WINDIR") if key in os.environ
        }
        environment.update(
            {
                "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_CONFIG_GLOBAL": os.devnull,
                "GIT_TERMINAL_PROMPT": "0",
                "GIT_ATTR_NOSYSTEM": "1",
                "GIT_NO_LAZY_FETCH": "1",
                "LC_ALL": "C",
            }
        )
        command = [
            str(Path(git).resolve(strict=True)),
            "--no-replace-objects",
            "-c",
            "core.hooksPath=" + os.devnull,
            "-c",
            "core.fsmonitor=false",
            "-c",
            "protocol.allow=never",
            "-c",
            "credential.helper=",
            "-C",
            str(REPO_ROOT),
        ]
        captured: list[tuple[str, bytes]] = []
        for relative, blob, length, sha256 in _HISTORICAL_INTERNAL_SOURCES:
            identity = subprocess.run(
                [*command, "rev-parse", "--verify", _HISTORICAL_SOURCE_COMMIT + ":" + relative],
                env=environment,
                capture_output=True,
                timeout=30,
                check=False,
            )
            if identity.returncode or identity.stdout != (blob + "\n").encode("ascii"):
                raise ValueError("object identity")
            result = subprocess.run(
                [*command, "cat-file", "blob", blob],
                env=environment,
                capture_output=True,
                timeout=30,
                check=False,
            )
            raw = result.stdout
            if result.returncode or len(raw) != length or hashlib.sha256(raw).hexdigest() != sha256:
                raise ValueError("blob integrity")
            captured.append((relative, raw))
        for relative, raw in captured:
            path = destination / relative
            path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            with path.open("xb") as handle:
                handle.write(raw)
                info = os.fstat(handle.fileno())
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_nlink != 1
                or path.resolve() != path.absolute()
                or path.read_bytes() != raw
            ):
                raise ValueError("materialized integrity")
        return destination
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError):
        raise AssertionError("TASK8_V27_HISTORICAL_SOURCE_UNAVAILABLE") from None


@pytest.fixture(
    params=[_HISTORICAL_SOURCE_COMMIT],
    ids=["historical-source-17c2b3b"],
)
def historical_internal_source_root(
    request: pytest.FixtureRequest,
    tmp_path: Path,
) -> Path:
    """Hermetic historical source integrity, NOT an executed PostgreSQL census."""
    assert request.param == _HISTORICAL_SOURCE_COMMIT
    request.node.user_properties.append(("verification_scope", "historical-source"))
    return _materialize_historical_internal_sources(tmp_path / "historical-internal-sources")


def test_v27_historical_internal_sources_are_exact_fresh_git_blobs(
    tmp_path: Path,
    request: pytest.FixtureRequest,
    historical_internal_source_root: Path,
) -> None:
    assert request.node.name.endswith("[historical-source-17c2b3b]")
    assert ("verification_scope", "historical-source") in request.node.user_properties
    assert historical_internal_source_root.is_dir()
    materialize = globals().get("_materialize_historical_internal_sources")
    assert callable(materialize), "explicit historical Git fixture is missing"
    root = materialize(tmp_path / "historical")
    expected = (
        (
            "src/idis/persistence/db.py",
            8274,
            "9e359cb8a69b370ae31c6ef313ac80ffd6f288adb83e17fdd2a3fc71ae9227eb",
        ),
        (
            "src/idis/models/run_step.py",
            9496,
            "19f6eb21d96f121c6ab9bd8c68413dabec31005bdeef0240c24772a15fcc7715",
        ),
    )
    assert root == tmp_path / "historical"
    assert sorted(
        path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file()
    ) == sorted(row[0] for row in expected)
    for relative, length, sha256 in expected:
        path = root / relative
        assert path.resolve() == path.absolute()
        assert stat.S_ISREG(path.lstat().st_mode)
        assert path.stat().st_nlink == 1
        assert len(path.read_bytes()) == length
        assert hashlib.sha256(path.read_bytes()).hexdigest() == sha256
    snapshot = _sql()._capture_migration_source_snapshot(internal_source_root=root)
    assert len(snapshot.file_rows) + len(snapshot.internal_module_rows) == 42
    assert snapshot.expected_sha256 == EXPECTED_MIGRATION_EXECUTION_SOURCE_SHA256


@pytest.mark.parametrize(
    "failure",
    (
        "missing-object",
        "missing-blob",
        "missing-git",
        "tampered-bytes",
        "wrong-length",
        "crlf",
        "timeout",
        "resolve-loop",
    ),
)
def test_v27_historical_internal_sources_fail_closed_without_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    materialize = globals().get("_materialize_historical_internal_sources")
    assert callable(materialize), "explicit historical Git fixture is missing"
    prior_runtime_bytes = {
        relative: (REPO_ROOT / relative).read_bytes()
        for relative, *_ in _HISTORICAL_INTERNAL_SOURCES
    }
    prior_runtime_hashes = {
        relative: hashlib.sha256(raw).hexdigest() for relative, raw in prior_runtime_bytes.items()
    }
    real_run = subprocess.run
    if failure == "missing-object":
        monkeypatch.setitem(globals(), "_HISTORICAL_SOURCE_COMMIT", "0" * 40)
    elif failure == "missing-git":
        monkeypatch.setattr(shutil, "which", lambda _name: None)
    elif failure == "resolve-loop":

        def broken_resolve(_self: Path, strict: bool = False) -> Path:
            del strict
            raise RuntimeError("PRIVATE_DIAGNOSTIC")

        monkeypatch.setattr(Path, "resolve", broken_resolve)
    else:

        def altered_run(
            command: list[str],
            *,
            env: dict[str, str],
            capture_output: bool,
            timeout: int,
            check: bool,
        ) -> subprocess.CompletedProcess[bytes]:
            if env.get("GIT_NO_LAZY_FETCH") != "1":
                raise AssertionError("missing GIT_NO_LAZY_FETCH")
            if failure == "timeout":
                raise subprocess.TimeoutExpired("PRIVATE_DIAGNOSTIC", 30)
            result = real_run(
                command, env=env, capture_output=capture_output, timeout=timeout, check=check
            )
            if "cat-file" not in command:
                return result
            if failure == "missing-blob":
                return subprocess.CompletedProcess(result.args, 128, b"", b"PRIVATE_DIAGNOSTIC")
            raw = result.stdout
            altered = (
                b"x" + raw[1:]
                if failure == "tampered-bytes"
                else raw + b"x"
                if failure == "wrong-length"
                else raw.replace(b"\n", b"\r\n")
            )
            return subprocess.CompletedProcess(
                result.args, result.returncode, altered, b"PRIVATE_DIAGNOSTIC"
            )

        monkeypatch.setattr(subprocess, "run", altered_run)
    destination = tmp_path / "historical"
    with pytest.raises(AssertionError, match="^TASK8_V27_HISTORICAL_SOURCE_UNAVAILABLE$") as raised:
        materialize(destination)
    assert raised.value.__cause__ is None
    assert raised.value.__suppress_context__ is True
    assert str(raised.value) == "TASK8_V27_HISTORICAL_SOURCE_UNAVAILABLE"
    assert "PRIVATE_DIAGNOSTIC" not in "".join(traceback.format_exception(raised.value))
    assert not list(destination.rglob("*.py"))
    after_runtime_bytes = {
        relative: (REPO_ROOT / relative).read_bytes()
        for relative, *_ in _HISTORICAL_INTERNAL_SOURCES
    }
    assert after_runtime_bytes == prior_runtime_bytes
    assert {
        relative: hashlib.sha256(raw).hexdigest() for relative, raw in after_runtime_bytes.items()
    } == prior_runtime_hashes


def test_v27_historical_internal_sources_refuse_existing_destination(tmp_path: Path) -> None:
    materialize = globals().get("_materialize_historical_internal_sources")
    assert callable(materialize), "explicit historical Git fixture is missing"
    destination = tmp_path / "historical"
    destination.mkdir()
    sentinel = destination / "foreign"
    sentinel.write_bytes(b"preserve")
    with pytest.raises(AssertionError, match="^TASK8_V27_HISTORICAL_SOURCE_UNAVAILABLE$"):
        materialize(destination)
    assert sentinel.read_bytes() == b"preserve"
    assert tuple(destination.iterdir()) == (sentinel,)


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="directory symlink creation requires Windows privilege; real Linux case is required",
)
def test_v27_historical_internal_sources_refuse_alias_destination(tmp_path: Path) -> None:
    materialize = globals().get("_materialize_historical_internal_sources")
    assert callable(materialize), "explicit historical Git fixture is missing"
    target = tmp_path / "target"
    target.mkdir()
    alias = tmp_path / "historical"
    alias.symlink_to(target, target_is_directory=True)
    with pytest.raises(AssertionError, match="^TASK8_V27_HISTORICAL_SOURCE_UNAVAILABLE$"):
        materialize(alias)
    assert alias.is_symlink()
    assert not tuple(target.iterdir())


def test_v27_historical_sources_do_not_replace_current_default_or_imports(tmp_path: Path) -> None:
    materialize = globals().get("_materialize_historical_internal_sources")
    assert callable(materialize), "explicit historical Git fixture is missing"
    current_db = importlib.import_module("idis.persistence.db")
    assert isinstance(current_db.__file__, str)
    assert Path(current_db.__file__).resolve() == (SRC_ROOT / "idis/persistence/db.py").resolve()
    assert current_db._ensure_psycopg_driver("postgresql://user:example@localhost/example") == (
        "postgresql+psycopg2://user:example@localhost/example"
    )
    runner = _sql()
    with pytest.raises(
        runner.Task8V27CensusError, match="^TASK8_V27_CENSUS_MIGRATION_SOURCE_INVALID$"
    ):
        runner._capture_migration_source_snapshot()
    root = materialize(tmp_path / "historical")
    snapshot = runner._capture_migration_source_snapshot(internal_source_root=root)
    prior = {
        name: module
        for name, module in sys.modules.items()
        if name == "idis" or name.startswith("idis.")
    }
    prior_path = tuple(sys.path)
    prior_meta = tuple(sys.meta_path)
    with (
        pytest.raises(RuntimeError, match="^controlled-scope-exit$"),
        runner._captured_migration_internal_module_scope(snapshot),
    ):
        assert sys.modules["idis.persistence.db"] is not current_db
        historical_db = sys.modules["idis.persistence.db"]
        assert (
            historical_db._ensure_psycopg_driver("postgresql://user:example@localhost/example")
            == "postgresql://user:example@localhost/example"
        )
        raise RuntimeError("controlled-scope-exit")
    assert {
        name: module
        for name, module in sys.modules.items()
        if name == "idis" or name.startswith("idis.")
    } == prior
    assert sys.modules["idis.persistence.db"] is current_db
    assert tuple(sys.path) == prior_path
    assert tuple(sys.meta_path) == prior_meta
    with pytest.raises(
        runner.Task8V27CensusError, match="^TASK8_V27_CENSUS_MIGRATION_SOURCE_INVALID$"
    ):
        runner._capture_migration_source_snapshot()


def _test_canonical_sha256(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _test_fixture_uuid(label: str, *coordinates: object) -> str:
    namespace = uuid.UUID("5812f9bc-1b42-5f18-bd70-94907cd6c93a")
    preimage = ["task8-v27-postgres-census-fixture-v1", label, *coordinates]
    return str(uuid.uuid5(namespace, _test_canonical_sha256(preimage)))


def _test_expected_dml_sequence() -> tuple[tuple[str, ...], ...]:
    rows: list[tuple[str, ...]] = []

    def add(
        coordinate: str,
        classification: str,
        operation: str,
        role_class: str,
        outcome: str,
    ) -> None:
        rows.append((coordinate, classification, operation, role_class, outcome))

    def add_scope(coordinate: str) -> None:
        add(coordinate, "APPLICATION_FIXTURE", "SCOPE_SEED:0001", "APPLICATION", "COMMIT")

    def add_graph(coordinate: str) -> None:
        add(
            coordinate,
            "APPLICATION_FIXTURE",
            "SOURCE_GRAPH:0001",
            "APPLICATION",
            "COMMIT",
        )
        add(
            coordinate,
            "APPLICATION_FIXTURE",
            "SOURCE_GRAPH:0002",
            "APPLICATION",
            "COMMIT",
        )
        add(
            coordinate,
            "APPLICATION_FIXTURE",
            "RUNTIME_GRAPH:0001",
            "APPLICATION",
            "COMMIT",
        )

    add_scope("MAIN")
    add_graph("MAIN")
    add("MAIN", "APPLICATION_BEHAVIOR", "MAIN_DASHBOARD:0001", "APPLICATION", "COMMIT")
    for ordinal, case_id in enumerate(EXPECTED_REQUIREMENT_CASE_IDS, start=1):
        coordinate = f"REQUIREMENT_{ordinal:02d}"
        add_scope(coordinate)
        add_graph(coordinate)
        if case_id == "UNDECLARED_FDD_CALCULATION_ROOT":
            add(
                coordinate,
                "APPLICATION_FIXTURE",
                "UNDECLARED_CALCULATION_ROOT:0001",
                "APPLICATION",
                "COMMIT",
            )
        add(
            coordinate,
            "APPLICATION_BEHAVIOR",
            f"REQUIREMENT_CASE_{ordinal:02d}:0001",
            "APPLICATION",
            "ROLLBACK",
        )
    for ordinal, (
        _case_id,
        role_class,
        outcome,
        has_graph,
        has_dashboard_preseed,
        has_erasure_seed,
        has_append_baseline,
    ) in enumerate(EXPECTED_BEHAVIOR_DML_CASES, start=1):
        coordinate = f"BEHAVIOR_{ordinal:02d}"
        add_scope(coordinate)
        if has_graph:
            add_graph(coordinate)
        if has_dashboard_preseed:
            add(
                coordinate,
                "APPLICATION_FIXTURE",
                "BEHAVIOR_DASHBOARD_SEED:0001",
                "APPLICATION",
                "COMMIT",
            )
        if has_erasure_seed:
            add(
                coordinate,
                "APPLICATION_FIXTURE",
                "ERASURE_REQUEST_SEED:0001",
                "APPLICATION",
                "COMMIT",
            )
        classification = (
            "APPLICATION_BEHAVIOR" if role_class == "APPLICATION" else "ERASURE_BEHAVIOR"
        )
        prefix = f"BEHAVIOR_CASE_{ordinal:02d}"
        if has_append_baseline:
            add(coordinate, classification, f"{prefix}:0001", role_class, "COMMIT")
            add(coordinate, classification, f"{prefix}:0002", role_class, outcome)
        else:
            add(coordinate, classification, f"{prefix}:0001", role_class, outcome)
    add_scope("RLS")
    add_graph("RLS")
    add("RLS", "APPLICATION_FIXTURE", "RLS_BASELINE:0001", "APPLICATION", "COMMIT")
    for ordinal in range(1, 5):
        add(
            "RLS",
            "APPLICATION_BEHAVIOR",
            f"RLS_PROBE_{ordinal:02d}:0001",
            "APPLICATION",
            "ROLLBACK",
        )
    for coordinate in ("RACE_FORWARD", "RACE_REVERSE"):
        add_scope(coordinate)
        add_graph(coordinate)
        add(
            coordinate,
            "APPLICATION_FIXTURE",
            "ERASURE_REQUEST_SEED:0001",
            "APPLICATION",
            "COMMIT",
        )
        if coordinate == "RACE_FORWARD":
            add(
                coordinate,
                "APPLICATION_BEHAVIOR",
                "RACE_FORWARD_APPLICATION:0001",
                "APPLICATION",
                "ROLLBACK",
            )
            add(
                coordinate,
                "ERASURE_BEHAVIOR",
                "RACE_FORWARD_ERASURE:0001",
                "ERASURE",
                "COMMIT",
            )
            add(
                coordinate,
                "APPLICATION_BEHAVIOR",
                "RACE_FORWARD_APPLICATION:0002",
                "APPLICATION",
                "ROLLBACK",
            )
        else:
            add(
                coordinate,
                "ERASURE_BEHAVIOR",
                "RACE_REVERSE_ERASURE:0001",
                "ERASURE",
                "ROLLBACK",
            )
            add(
                coordinate,
                "APPLICATION_BEHAVIOR",
                "RACE_REVERSE_APPLICATION:0001",
                "APPLICATION",
                "COMMIT",
            )
            add(
                coordinate,
                "ERASURE_BEHAVIOR",
                "RACE_REVERSE_ERASURE:0002",
                "ERASURE",
                "ROLLBACK",
            )
    return tuple(rows)


def _test_closed_dml_ledger() -> list[dict[str, object]]:
    application_role = "idis_application"
    erasure_role = "idis_erasure_executor"
    rows: list[dict[str, object]] = []
    for ordinal, (coordinate, classification, operation, role_class, outcome) in enumerate(
        _test_expected_dml_sequence(), start=1
    ):
        tenant_id = _test_fixture_uuid("case-scope", coordinate, "tenant")
        session_tenant_id = (
            _test_fixture_uuid("rls-probe-tenant", "WRONG_TENANT")
            if operation in {"RLS_PROBE_01:0001", "RLS_PROBE_02:0001"}
            else ""
            if operation in {"RLS_PROBE_03:0001", "RLS_PROBE_04:0001"}
            else tenant_id
        )
        role = application_role if role_class == "APPLICATION" else erasure_role
        statement_hashes = [
            hashlib.sha256(f"{coordinate}|{operation}|DML".encode("ascii")).hexdigest()
        ]
        posture = {
            "current_user": role,
            "session_user": role,
            "rolsuper": False,
            "rolbypassrls": False,
            "session_replication_role": "origin",
        }
        rows.append(
            {
                "ledger_ordinal": ordinal,
                "case_coordinate": coordinate,
                "dml_classification": classification,
                "operation": operation,
                "expected_role": role,
                "current_user": role,
                "session_user": role,
                "tenant_id": tenant_id,
                "session_tenant_id": session_tenant_id,
                "session_replication_role": "origin",
                "transaction_outcome": outcome,
                "dml_statement_sha256_tuple": statement_hashes,
                "dml_statement_count": len(statement_hashes),
                "dml_statement_tuple_sha256": _test_canonical_sha256(
                    ["task8-v27-fixture-dml-statement-sha256-tuple-v1", statement_hashes]
                ),
                "posture_sha256": _test_canonical_sha256(
                    [
                        "task8-v27-connection-role-posture-v1",
                        [[key, value] for key, value in posture.items()],
                    ]
                ),
            }
        )
    return rows


def _task5_roundtrip_fixture(
    runner: ModuleType,
) -> tuple[tuple[dict[str, object], ...], dict[str, object], tuple[str, str, str]]:
    tenant_id = "11111111-1111-4111-8111-111111111111"
    deal_id = "22222222-2222-4222-8222-222222222222"
    run_id = "33333333-3333-4333-8333-333333333333"
    identities: list[dict[str, object]] = []
    for methodology_type, count, header_row, source_type in (
        ("commercial_dd", 21, None, "checked_in_template"),
        ("financial_dd", 79, 2, "operator_workbook"),
    ):
        version_id = f"{methodology_type}:version-v2"
        source_sha256 = hashlib.sha256(f"{methodology_type}:source".encode("ascii")).hexdigest()
        record_id = str(
            uuid.uuid5(
                uuid.NAMESPACE_URL,
                f"idis:methodology-version:{tenant_id}:{version_id}:{source_sha256}",
            )
        )
        source_alias = f"{methodology_type}-source"
        rows: list[list[object]] = []
        for ordinal in range(1, count + 1):
            question_id = f"{methodology_type}:question:{ordinal:03d}"
            question_record_id = str(
                uuid.uuid5(
                    uuid.NAMESPACE_URL,
                    f"idis:methodology-question:{tenant_id}:{record_id}:{question_id}",
                )
            )
            if methodology_type == "commercial_dd" and ordinal == count:
                requirements = [{"calc_type": "LTV_CAC_RATIO", "required": False}]
            elif methodology_type == "financial_dd" and ordinal <= 10:
                requirements = [
                    {
                        "formula_ordinal": ordinal,
                        "formula_type": f"FORMULA_{ordinal:02d}",
                        "required": True,
                        "scope": "line_item",
                    }
                ]
            else:
                requirements = []
            semantic_sha256 = hashlib.sha256(
                f"{methodology_type}:semantic:{ordinal}".encode("ascii")
            ).hexdigest()
            payload_sha256 = hashlib.sha256(
                f"{methodology_type}:payload:{ordinal}".encode("ascii")
            ).hexdigest()
            rows.append(
                [
                    tenant_id,
                    question_record_id,
                    record_id,
                    version_id,
                    question_id,
                    methodology_type,
                    methodology_type,
                    ordinal,
                    "Section A",
                    source_type,
                    source_alias,
                    "Section A",
                    ordinal,
                    f"A{ordinal}",
                    source_sha256,
                    semantic_sha256,
                    payload_sha256,
                    payload_sha256,
                    requirements,
                ]
            )
        required_tuple = [[row[1], row[18]] for row in rows]
        identities.append(
            {
                "schema_version": "task8-v27-task5-version-roundtrip-identity-v2",
                "tenant_id": tenant_id,
                "methodology_version_record_id": record_id,
                "methodology_version_id": version_id,
                "methodology_id": methodology_type,
                "methodology_type": methodology_type,
                "version_label": "v2",
                "source_type": source_type,
                "source_alias": source_alias,
                "source_sha256": source_sha256,
                "source_byte_count": 100 + count,
                "source_contract_hash": hashlib.sha256(
                    f"{methodology_type}:contract".encode("ascii")
                ).hexdigest(),
                "source_trace_sha256": hashlib.sha256(
                    f"{methodology_type}:trace".encode("ascii")
                ).hexdigest(),
                "registry_sha256": hashlib.sha256(
                    f"{methodology_type}:registry".encode("ascii")
                ).hexdigest(),
                "version_sha256": hashlib.sha256(
                    f"{methodology_type}:version".encode("ascii")
                ).hexdigest(),
                "question_count": count,
                "source_section_count": 1,
                "header_row": header_row,
                "status": "FROZEN",
                "timestamp_redaction_field_tuple": ["imported_at", "frozen_at"],
                "deterministic_registry_bytes_sha256": hashlib.sha256(
                    f"{methodology_type}:registry-bytes".encode("ascii")
                ).hexdigest(),
                "methodology_question_payload_hashes": [row[16] for row in rows],
                "question_identity_field_tuple": list(EXPECTED_TASK5_QUESTION_IDENTITY_FIELDS),
                "question_identity_tuple": rows,
                "question_identity_tuple_sha256": runner._canonical_sha256(rows),
                "required_calculation_tuple": required_tuple,
                "required_calculation_tuple_sha256": runner._canonical_sha256(required_tuple),
            }
        )
    commercial, financial = identities
    selection_hash = runner._canonical_sha256(
        {
            "tenant_id": tenant_id,
            "deal_id": deal_id,
            "run_id": run_id,
            "commercial_dd_version_record_id": commercial["methodology_version_record_id"],
            "commercial_methodology_version_id": commercial["methodology_version_id"],
            "commercial_source_sha256": commercial["source_sha256"],
            "commercial_registry_hash": commercial["registry_sha256"],
            "commercial_version_hash": commercial["version_sha256"],
            "financial_dd_version_record_id": financial["methodology_version_record_id"],
            "financial_methodology_version_id": financial["methodology_version_id"],
            "financial_source_sha256": financial["source_sha256"],
            "financial_registry_hash": financial["registry_sha256"],
            "financial_version_hash": financial["version_sha256"],
            "selected_question_count": 100,
        }
    )
    selection = {
        "selection_id": str(
            uuid.uuid5(
                uuid.NAMESPACE_URL,
                f"idis:run-methodology-selection:{tenant_id}:{run_id}:{selection_hash}",
            )
        ),
        "tenant_id": tenant_id,
        "deal_id": deal_id,
        "run_id": run_id,
        "commercial_dd_version_record_id": commercial["methodology_version_record_id"],
        "financial_dd_version_record_id": financial["methodology_version_record_id"],
        "selected_version_ids": [
            commercial["methodology_version_id"],
            financial["methodology_version_id"],
        ],
        "selected_source_hashes": [
            commercial["source_sha256"],
            financial["source_sha256"],
        ],
        "selected_registry_hashes": [
            commercial["registry_sha256"],
            financial["registry_sha256"],
        ],
        "selected_version_hashes": [
            commercial["version_sha256"],
            financial["version_sha256"],
        ],
        "selection_hash": selection_hash,
        "selected_question_count": 100,
        "state": "FROZEN",
        "immutable": True,
    }
    question_projection_sha256 = runner._canonical_sha256(
        [
            "task8-v27-task5-question-db-projection-v2",
            [row for identity in identities for row in identity["question_identity_tuple"]],
        ]
    )
    version_projection_sha256 = runner._canonical_sha256(
        [
            "task8-v27-task5-version-db-projection-v1",
            [
                [
                    identity[field]
                    for field in (
                        "methodology_version_record_id",
                        "methodology_version_id",
                        "methodology_id",
                        "methodology_type",
                        "version_label",
                        "source_type",
                        "source_alias",
                        "source_sha256",
                        "source_byte_count",
                        "source_contract_hash",
                        "source_trace_sha256",
                        "registry_sha256",
                        "version_sha256",
                        "question_count",
                        "source_section_count",
                        "header_row",
                        "status",
                    )
                ]
                for identity in identities
            ],
        ]
    )
    selection_projection_sha256 = runner._canonical_sha256(
        ["task8-v27-task5-selection-db-projection-v1", selection]
    )
    return (
        tuple(identities),
        selection,
        (
            question_projection_sha256,
            version_projection_sha256,
            selection_projection_sha256,
        ),
    )


def test_v27_task5_identity_field_inventories_are_independently_frozen() -> None:
    runner = _sql()
    assert runner._TASK5_QUESTION_IDENTITY_FIELDS == EXPECTED_TASK5_QUESTION_IDENTITY_FIELDS
    assert runner._TASK5_REGISTRY_IDENTITY_FIELDS == EXPECTED_TASK5_REGISTRY_IDENTITY_FIELDS
    assert runner._TASK5_SELECTION_IDENTITY_FIELDS == EXPECTED_TASK5_SELECTION_IDENTITY_FIELDS


def _task5_selection_immutability_sha256() -> str:
    return hashlib.sha256(b"task8-v27-selection-immutability-proof").hexdigest()


def _artifact_raw() -> bytes:
    return STAGED_ARTIFACT.read_bytes()


def _fixture_raw() -> bytes:
    verifier = _verifier()
    return verifier.derive_fixture_bytes(
        parent_artifact_raw=PARENT_V21.read_bytes(),
        policy_artifact_raw=POLICY_V2.read_bytes(),
        reviewer_map_raw=REVIEWER_MAP.read_bytes(),
        v27_artifact_raw=_artifact_raw(),
    )


def test_v27_census_implementation_files_exist() -> None:
    assert SQL_RUNNER.is_file()
    assert SIGNED_RUNNER.is_file()


def test_v27_runner_is_native_and_never_consumes_any_void_predecessor() -> None:
    runner = _sql()
    assert runner.PIPELINE_CONTRACT_VERSION == "full-strict-v27"
    source = SQL_RUNNER.read_text(encoding="utf-8")
    for version in range(22, 27):
        for forbidden in (
            f"run_task8_v{version}_sql_authority_census",
            f"full_strict_v{version}_reviewed_preimage",
            f"full-strict-v{version}",
            f"task8-v{version}",
        ):
            assert forbidden not in source
    assert "20+" not in source.replace(" ", "")
    assert "20 +" not in source
    tree = ast.parse(source)
    exec_owners: set[str] = set()
    for function in (
        node for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ):
        if any(
            isinstance(child, ast.Call)
            and isinstance(child.func, ast.Name)
            and child.func.id == "exec"
            for child in ast.walk(function)
        ):
            exec_owners.add(function.name)
    assert exec_owners == {
        "_captured_migration_execution_scope",
        "_captured_migration_internal_module_scope",
        "load_captured_module",
    }
    assert "importlib" not in source


def test_v27_production_sources_exclude_every_shared_void_candidate_identity() -> None:
    reusable_equation_identities = set(void_evidence.VOID_V26_INHERITED_SEMANTIC_IDENTIFIERS)
    production = SQL_RUNNER.read_text(encoding="utf-8") + SIGNED_RUNNER.read_text(encoding="utf-8")
    for identity in void_evidence.TASK8_VOID_CONTRACT_HASHES:
        if identity not in reusable_equation_identities:
            assert identity not in production


def test_v27_preflight_binds_explicit_staged_bytes_and_all_six_identities() -> None:
    signed = _signed()
    preflight = signed.validate_preflight_assets(
        artifact_raw=_artifact_raw(),
        fixture_raw=_fixture_raw(),
        parent_artifact_raw=PARENT_V21.read_bytes(),
        policy_artifact_raw=POLICY_V2.read_bytes(),
        reviewer_map_raw=REVIEWER_MAP.read_bytes(),
    )
    assert preflight == {
        "schema_version": "task8-v27-postgres-census-preflight-v1",
        "artifact_schema": "full-strict-v27-reviewed-preimage-v1",
        "fixture_schema": "full-strict-v27-contract-hash-fixture-v1",
        "pipeline_contract_version": "full-strict-v27",
        "activation_status": "INACTIVE",
        "artifact_sha256": ARTIFACT_SHA256,
        "fixture_sha256": FIXTURE_SHA256,
        "contract_sha256": CONTRACT_SHA256,
        "identity_graph_sha256": GRAPH_SHA256,
        "identity_graph_counts": {
            "node_count": 67,
            "edge_count": 35,
            "leaf_count": 834,
            "source_pointer_count": 67,
        },
        "artifact_counts": {
            "semantic_step_count": 28,
            "edge_rule_count": 48,
            "usage_route_count": 1756,
            "usage_extractor_count": 19,
            "provider_count": 18,
            "schema_count": 68,
            "current_ddl_table_count": 34,
            "installed_tenant_rls_table_count": 66,
            "erasure_current_relation_count": 48,
            "erasure_future_relation_count": 38,
            "erasure_complete_relation_count": 86,
            "task11_restart_cut_count": 27,
            "task11_undischarged_rule_count": 10,
        },
        "conservation_sha256": CONSERVATION_SHA256,
        "policy_id": "fdd-formula-policy-v2",
        "policy_artifact_sha256": POLICY_ARTIFACT_SHA256,
        "policy_sha256": POLICY_SHA256,
        "reviewer_map_sha256": REVIEWER_MAP_SHA256,
        "policy_binding_sha256": POLICY_BINDING_SHA256,
        "parent_artifact_sha256": PARENT_ARTIFACT_SHA256,
    }


def test_v27_preflight_rejects_any_tamper_before_profile_or_database() -> None:
    signed = _signed()
    fixture = bytearray(_fixture_raw())
    fixture[-2] ^= 1
    with pytest.raises(
        signed.Task8V27SignedCensusError,
        match="^TASK8_V27_SIGNED_CENSUS_PREFLIGHT_INVALID$",
    ):
        signed.validate_preflight_assets(
            artifact_raw=_artifact_raw(),
            fixture_raw=bytes(fixture),
            parent_artifact_raw=PARENT_V21.read_bytes(),
            policy_artifact_raw=POLICY_V2.read_bytes(),
            reviewer_map_raw=REVIEWER_MAP.read_bytes(),
        )


def test_v27_current_run_and_steps_use_exact_0038_v21_coordinates() -> None:
    runner = _sql()
    assert runner.CURRENT_RUNTIME_PIPELINE_CONTRACT_VERSION == CURRENT_PIPELINE_VERSION
    assert runner.CURRENT_RUNTIME_PIPELINE_CONTRACT_HASH == CURRENT_PIPELINE_HASH
    rows = runner.build_current_run_step_plan(
        tenant_id="11111111-1111-4111-8111-111111111111",
        deal_id="22222222-2222-4222-8222-222222222222",
        run_id="33333333-3333-4333-8333-333333333333",
    )
    assert [
        (row["step_order"], row["step_name"], row["semantic_contract_hash"]) for row in rows
    ] == list(EXPECTED_STEP_ROWS)
    assert all(row["semantic_contract_version"] == "2" for row in rows)
    assert all(row["pipeline_contract_version"] == CURRENT_PIPELINE_VERSION for row in rows)
    assert all(row["pipeline_contract_hash"] == CURRENT_PIPELINE_HASH for row in rows)
    assert len({row["step_id"] for row in rows}) == 8


def test_v27_manifest_preserves_25_behavior_cases_and_adds_closed_v3_matrix() -> None:
    runner = _sql()
    artifact = json.loads(_artifact_raw())
    manifest = runner.build_census_case_manifest(artifact)
    runner.validate_census_case_manifest(manifest, artifact)
    assert manifest["dashboard_coverage_row_count"] == 100
    assert manifest["dashboard_commercial_coverage_row_count"] == 21
    assert manifest["dashboard_financial_coverage_row_count"] == 79
    assert manifest["dashboard_expected_row_count"] == 100
    behavior = runner.census_case_rows(manifest)
    assert len(behavior) == 25
    assert all(row["execution_role"] in {"application", "erasure_executor"} for row in behavior)
    assert all(row["execution_role"] != "admin" for row in behavior)
    requirement = manifest["requirement_matrix"]
    assert requirement["legal_case_id_tuple"] == [
        "LEGACY_EMPTY",
        "LEGACY_TWO_FIELD",
        "POLICY_V2_EMPTY",
        "POLICY_V2_TEN_LITERAL_TUPLES",
    ]
    assert set(requirement["negative_case_id_tuple"]) == {
        "LEGACY_FOUR_FIELD",
        "POLICY_V2_TWO_FIELD",
        "UNKNOWN_FORMULA_TYPE",
        "ORDINAL_DRIFT",
        "SCOPE_DRIFT",
        "UNDECLARED_FDD_CALCULATION_ROOT",
        "MIXED_SHAPES",
        "POLICY_V2_EXTRA_FIELD",
        "POLICY_V2_MISSING_FIELD",
        "LEGACY_EXTRA_FIELD",
        "LEGACY_MISSING_FIELD",
        "CROSS_VERSION_JOIN",
        "POLICY_V2_DUPLICATE_REQUIREMENT_TUPLE",
    }


def test_v27_seed_plan_is_named_constructor_and_production_repository_only() -> None:
    runner = _sql()
    source = inspect.getsource(runner.build_v27_dashboard_seed_plan)
    assert "workbook_raw" in inspect.signature(runner.build_v27_dashboard_seed_plan).parameters
    assert "import_cdd_template" in source
    assert "import_fdd_formula_policy_v2_registry" in source
    assert "policy_artifact=policy_artifact_raw" in source
    persistence = inspect.getsource(runner._persist_task5_seed_plan)
    for call in (
        ".import_registry(",
        ".freeze_version(",
        ".get_frozen_version(",
        ".create_run_selection(",
        ".insert_coverage_records(",
    ):
        assert call in persistence
    assert "run_output_object_ids=" in persistence
    assert "_source_contract_hash" in persistence
    assert "task8-v27-methodology-source-contract-v1" not in persistence
    assert "e42ae90f9f8b34657ba022c056cb3a0d4ef20006b89cc71cd1dfbe82b56b9b9a" in persistence
    assert "ed191040179c85ee2298b9d53df024600e069a8a66ba758b7ec50ddb76d1612e" in persistence
    sql_source = SQL_RUNNER.read_text(encoding="utf-8")
    # A direct question INSERT is admitted only for the closed negative matrix.
    # The legal/main/99 production paths must remain constructor/repository-only.
    tree = ast.parse(sql_source)
    direct_insert_functions = {
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and "INSERT INTO public.methodology_questions" in ast.get_source_segment(sql_source, node)
    }
    assert direct_insert_functions == {"_persist_adversarial_requirement_task5_seed"}
    for production_function in (
        runner._persist_task5_seed_plan,
        runner._persist_bound_runtime_graph,
        runner._persist_partial_dashboard_runtime_graph,
        runner._run_main_dashboard_census,
    ):
        assert "_persist_adversarial_requirement_task5_seed" not in inspect.getsource(
            production_function
        )
    assert "INSERT INTO methodology_questions" not in sql_source


def test_v27_cdd_constructor_import_and_persistence_share_one_byte_capture(
    tmp_path: Path,
) -> None:
    runner = _sql()
    original = (
        REPO_ROOT / "src" / "idis" / "methodology" / "templates" / "commercial_dd_v1.json"
    ).read_bytes()
    staged = tmp_path / "commercial_dd_v1.json"
    staged.write_bytes(original)
    captured = runner._capture_cdd_template_bytes(staged)
    staged.write_bytes(b"x" * len(original))
    assert captured == original
    with pytest.raises(
        runner.Task8V27CensusError,
        match="^TASK8_V27_CENSUS_CDD_TEMPLATE_INVALID$",
    ):
        runner._capture_cdd_template_bytes(staged)

    plan = inspect.getsource(runner.build_v27_dashboard_seed_plan)
    registries = inspect.getsource(runner._production_methodology_registries)
    persist = inspect.getsource(runner._persist_task5_seed_plan)
    adversarial = inspect.getsource(runner._persist_adversarial_requirement_task5_seed)
    runtime = inspect.getsource(runner._persist_runtime_graph_core)
    requirement = inspect.getsource(runner._build_requirement_seed_spec)
    assert "cdd_raw" in inspect.signature(runner.build_v27_dashboard_seed_plan).parameters
    assert "cdd_raw" in inspect.signature(runner._production_methodology_registries).parameters
    assert "cdd_byte_count" in inspect.signature(runner._persist_task5_seed_plan).parameters
    assert (
        "cdd_byte_count"
        in inspect.signature(runner._persist_adversarial_requirement_task5_seed).parameters
    )
    assert ".read_bytes(" not in plan + registries + persist + adversarial
    assert "cdd_path.stat" not in persist + adversarial
    assert runtime.count("_capture_cdd_template_bytes") == 1
    assert requirement.count("_capture_cdd_template_bytes") == 1
    assert "cdd_raw=cdd_raw" in runtime + requirement
    assert "cdd_byte_count=len(cdd_raw)" in runtime + requirement


def test_v27_migrations_execute_only_an_lf_pinned_captured_snapshot(
    tmp_path: Path,
    historical_internal_source_root: Path,
) -> None:
    runner = _sql()
    snapshot = runner._capture_migration_source_snapshot(
        internal_source_root=historical_internal_source_root,
    )
    assert len(snapshot.file_rows) == 40
    assert snapshot.file_rows[0][0] == "env.py"
    assert snapshot.file_rows[1][0] == "script.py.mako"
    assert tuple(row[0] for row in snapshot.file_rows[2:]) == tuple(
        f"versions/{Path(path).name}" for path, _sha256 in runner._MIGRATION_LF_SHA256_ROWS
    )
    assert snapshot.expected_sha256 == EXPECTED_MIGRATION_EXECUTION_SOURCE_SHA256

    source_root = tmp_path / "mutable-migrations"
    for relative_path, raw, _sha256 in snapshot.file_rows:
        path = source_root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
    captured = runner._capture_migration_source_snapshot(
        source_root,
        internal_source_root=historical_internal_source_root,
    )
    swapped = source_root / captured.file_rows[-1][0]
    swapped.write_bytes(b"x" * len(captured.file_rows[-1][1]))
    materialized = tmp_path / "execution-snapshot"
    runner._materialize_migration_source_snapshot(captured, materialized)
    assert (materialized / captured.file_rows[-1][0]).read_bytes() == captured.file_rows[-1][1]
    assert (materialized / captured.file_rows[-1][0]).read_bytes() != swapped.read_bytes()
    assert all(
        hashlib.sha256((materialized / relative_path).read_bytes()).hexdigest() == sha256
        for relative_path, _raw, sha256 in captured.file_rows
    )

    migration_runner = inspect.getsource(runner._run_migrations_through_0038)
    public_runner = inspect.getsource(runner._run_disposable_postgres_census_impl)
    closure_manifest = inspect.getsource(runner.build_seed_fk_closure_manifest)
    assert "migration_snapshot" in inspect.signature(runner._run_migrations_through_0038).parameters
    assert "migrations_package" not in migration_runner
    assert "_materialize_migration_source_snapshot" in migration_runner
    assert public_runner.index("_capture_migration_source_snapshot") < public_runner.index(
        "_create_disposable_database"
    )
    assert ".read_bytes(" not in closure_manifest


def test_v27_migration_snapshot_closes_exact_internal_imports_after_capture(
    tmp_path: Path,
    historical_internal_source_root: Path,
) -> None:
    runner = _sql()
    snapshot = runner._capture_migration_source_snapshot(
        internal_source_root=historical_internal_source_root,
    )
    expected_modules = (
        ("idis.persistence.db", "src/idis/persistence/db.py"),
        ("idis.models.run_step", "src/idis/models/run_step.py"),
    )
    assert tuple((row[0], row[1]) for row in snapshot.internal_module_rows) == expected_modules
    assert runner._migration_internal_import_edges(snapshot.file_rows) == (
        ("env.py", "idis.persistence.db"),
        (
            "versions/0037_durable_methodology_extraction.py",
            "idis.models.run_step",
        ),
    )

    mutable_repo = tmp_path / "mutable-repo"
    for _module_name, relative_path, raw, _sha256 in snapshot.internal_module_rows:
        path = mutable_repo / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
    captured = runner._capture_migration_source_snapshot(internal_source_root=mutable_repo)
    for _module_name, relative_path, raw, _sha256 in captured.internal_module_rows:
        (mutable_repo / relative_path).write_bytes(b"x" * len(raw))

    governed_names = (
        "idis",
        "idis.persistence",
        "idis.persistence.db",
        "idis.models",
        "idis.models.run_step",
    )
    previous = {name: sys.modules.get(name) for name in governed_names}
    with runner._captured_migration_internal_module_scope(captured):
        captured_db = sys.modules["idis.persistence.db"]
        captured_run_step = sys.modules["idis.models.run_step"]
        assert captured_db.IDIS_DATABASE_ADMIN_URL_ENV == "IDIS_DATABASE_ADMIN_URL"
        assert (
            captured_run_step.STEP_ORDER[
                captured_run_step.StepName.METHODOLOGY_EXTRACTION_TASK_EXECUTION
            ]
            == 6
        )
        with pytest.raises(
            runner.Task8V27CensusError,
            match="^TASK8_V27_CENSUS_MIGRATION_SOURCE_INVALID$",
        ):
            __import__("idis.uncaptured_migration_dependency")
    for name, module in previous.items():
        assert sys.modules.get(name) is module


def test_v27_migration_execution_loader_never_reopens_swapped_snapshot_bytes(
    tmp_path: Path,
    historical_internal_source_root: Path,
) -> None:
    runner = _sql()
    snapshot = runner._capture_migration_source_snapshot(
        internal_source_root=historical_internal_source_root,
    )
    materialized = tmp_path / "execution-snapshot"
    runner._materialize_migration_source_snapshot(snapshot, materialized)
    relative_path, captured_raw, _sha256 = next(
        row for row in snapshot.file_rows if row[0].startswith("versions/0036_")
    )
    swapped = materialized / relative_path
    malicious = b"raise RuntimeError('swapped migration path executed')\n"
    swapped.write_bytes(malicious + b"#" * (len(captured_raw) - len(malicious)))

    from alembic.util import pyfiles as alembic_pyfiles

    with runner._captured_migration_execution_scope(snapshot, materialized):
        loaded = alembic_pyfiles.load_python_file(swapped.parent, swapped.name)
    assert loaded.revision == "0036"
    assert "_captured_migration_execution_scope" in inspect.getsource(
        runner._run_migrations_through_0038
    )


def test_v27_seed_graph_is_trigger_enabled_final_bound_and_placeholder_free() -> None:
    runner = _sql()
    source = SQL_RUNNER.read_text(encoding="utf-8")
    assert "SET LOCAL session_replication_role" not in source
    assert "SET session_replication_role" not in source
    assert source.count("uuid.uuid4()") <= 1
    assert "datetime.now" not in source
    assert "'RUNTIME'" in source or '"RUNTIME"' in source
    for repeated in "0123456789abcdef":
        assert f'"{repeated}" * 64' not in source
        assert f"'{repeated}' * 64" not in source
    native = inspect.getsource(runner._persist_seed_native_root)
    assert "INSERT INTO public.run_output_objects" in native
    assert "UPDATE public.run_output_objects" not in native
    assert "INSERT INTO public.run_step_outputs" in native
    assert "SET CONSTRAINTS ALL IMMEDIATE" in native
    assert "run_output_object_id" in inspect.getsource(runner._persist_runtime_graph_core)


def test_v27_post_0038_source_freeze_and_root_aware_paths_are_reachable() -> None:
    runner = _sql()
    source_stage = inspect.getsource(runner._stage_and_freeze_source_graph)
    assert "freeze_source_package" in source_stage
    assert "application_engine" in source_stage
    assert "RUNTIME" in source_stage
    assert "SET CONSTRAINTS ALL IMMEDIATE" in source_stage

    execution = inspect.getsource(runner._persist_runtime_graph_core)
    coverage_persistence = inspect.getsource(runner._persist_task5_seed_plan)
    for production_boundary in (
        "_persist_task5_seed_plan",
        "persist_planned_tasks",
        "persist_claim_links",
        "persist_evidence_links",
        "persist_sanad_links",
        "persist_defect_links",
    ):
        assert production_boundary in execution
    assert (execution + coverage_persistence).count("run_output_object_ids=") >= 6


def test_v27_obsolete_unrooted_seed_contracts_are_extinct() -> None:
    runner = _sql()
    source = SQL_RUNNER.read_text(encoding="utf-8")
    for symbol in (
        "_insert_methodology_question_and_coverage",
        "_insert_seed_output",
        "_refresh_seed_output_hashes",
    ):
        assert not hasattr(runner, symbol)
        assert symbol not in source
    assert "'BACKFILL'" not in source
    assert '"BACKFILL"' not in source
    assert source.count("INSERT INTO public.methodology_questions") == 1
    assert "def _persist_adversarial_requirement_task5_seed" in source


def test_v27_requirement_matrix_is_application_observed_and_zero_mutation() -> None:
    runner = _sql()
    case_source = inspect.getsource(runner._execute_requirement_case)
    legal_source = inspect.getsource(runner._observe_legal_requirement_cases)
    assert "_attest_connection_role_posture" in case_source + legal_source
    assert (
        "application_url" in inspect.signature(runner._run_dashboard_requirement_matrix).parameters
    )
    assert {
        "main_scope",
        "main_dashboard_census_sha256",
    } <= set(inspect.signature(runner._run_dashboard_requirement_matrix).parameters)
    assert "_tenant_deal_projection" in case_source
    assert "zero_mutation" in case_source
    vectors = runner._requirement_case_vectors(json.loads(_artifact_raw()))
    assert sum(vector["accepted"] is False for vector in vectors) == 13


def test_v27_requirement_matrix_uses_the_installed_equation_v3_write_path() -> None:
    """The matrix must exercise the installed guard, never a copied predicate."""

    runner = _sql()
    source = inspect.getsource(runner._run_dashboard_requirement_matrix)
    runner_source = SQL_RUNNER.read_text(encoding="utf-8")
    assert 'predicate_sql = """' not in runner_source
    assert "SELECT %s::text AS methodology_version_id" not in runner_source
    assert "predicate_sql" not in source
    assert "_observe_installed_dashboard_authority" in source
    assert "_execute_requirement_case" in source
    case_source = inspect.getsource(runner._execute_requirement_case)
    validator_source = inspect.getsource(runner.validate_requirement_matrix_result_rows)
    assert "_insert_dashboard_batch" in case_source
    assert "SET CONSTRAINTS ctrg_v2_dashboard_root_equation IMMEDIATE" in case_source
    assert "SET CONSTRAINTS ctrg_v3_dashboard_root_equation IMMEDIATE" in case_source
    assert "_DASHBOARD_REJECTION_SQLSTATE" in validator_source
    assert "_DASHBOARD_REJECTION_CODE" in validator_source
    assert "authority_body_sha256" in source


def test_v27_requirement_authority_hashes_exact_artifact_body() -> None:
    runner = _sql()
    artifact = json.loads(_artifact_raw())
    authority = next(
        row
        for row in artifact["sql_authority_registry"]["rows"]
        if row["authority_id"] == "dashboard-root-equation-v3"
    )
    body = runner._dashboard_authority_sql(artifact)
    assert authority["expected_sha256"] == (
        "20250e787f770872cb782c48cb9c65deec186acd5751b6f8ebd39c5c10faeeb5"
    )
    assert hashlib.sha256(body.encode("utf-8")).hexdigest() == (
        "9dc3d588a8014796b2da01ac4942dd0b2037db2f0fff6a809197ed786cdf95d1"
    )
    binding = runner._dashboard_authority_binding(artifact)
    assert binding == {
        "authority_row_sha256": (
            "20250e787f770872cb782c48cb9c65deec186acd5751b6f8ebd39c5c10faeeb5"
        ),
        "install_tuple_sha256": (
            "9dc3d588a8014796b2da01ac4942dd0b2037db2f0fff6a809197ed786cdf95d1"
        ),
        "install_tuple_utf8_bytes": 26016,
        "create_function_sha256": (
            "6433d7c462b90804977dd5999d79a816a7386750e00748f0121dbfa75d088619"
        ),
        "create_function_utf8_bytes": 25367,
        "semantic_body_sha256": (
            "509789807246a51eb4f449e67548ec229dd928d3fac554925d6fa13a8ef713b7"
        ),
        "semantic_body_utf8_bytes": 25212,
    }


def test_v27_requirement_result_validator_enforces_12_trigger_plus_one_schema_preemption() -> None:
    runner = _sql()
    vectors = runner._requirement_case_vectors(json.loads(_artifact_raw()))
    rows: list[dict[str, object]] = []
    for vector in vectors:
        accepted = bool(vector["accepted"])
        case_id = str(vector["case_id"])
        cross_version = case_id == "CROSS_VERSION_JOIN"
        rows.append(
            {
                "case_id": case_id,
                "expected_accepted": accepted,
                "observed_accepted": accepted,
                "observer_role": "app_role",
                "session_replication_role": "origin",
                "observer_posture_sha256": "2" * 64,
                "pre_observer_posture_sha256": "3" * 64,
                "post_observer_posture_sha256": "4" * 64,
                "sqlstate": (None if accepted else "23514"),
                "reason_code": (
                    None
                    if accepted
                    else (
                        "METHODOLOGY_COVERAGE_SCOPE_INVALID"
                        if cross_version
                        else "TASK7_SQL_AUTHORITY_REJECTED:DASHBOARD_ROOT_EQUATION"
                    )
                ),
                "rejection_path": (
                    None if accepted else ("SCHEMA_PREEMPTION" if cross_version else "EQUATION_V3")
                ),
                "guard_reached": accepted or not cross_version,
                "zero_mutation": True,
                "dashboard_row_delta": 0,
                "dashboard_root_delta": 0,
                "pre_projection_sha256": (None if accepted else "1" * 64),
                "post_projection_sha256": (None if accepted else "1" * 64),
                "stable_pre_projection_sha256": (None if accepted else "5" * 64),
                "stable_post_projection_sha256": (None if accepted else "5" * 64),
                "production_graph_sha256": ("6" * 64 if accepted else None),
                "legal_projection_sha256": ("7" * 64 if accepted else None),
            }
        )
    receipt = runner.validate_requirement_matrix_result_rows(
        rows,
        expected_application_role="app_role",
    )
    assert receipt["equation_v3_rejection_count"] == 12
    assert receipt["schema_preemption_count"] == 1
    assert receipt["negative_zero_mutation_count"] == 13
    assert receipt["negative_zero_dashboard_derivation_count"] == 13

    cross_index = next(
        index for index, row in enumerate(rows) if row["case_id"] == "CROSS_VERSION_JOIN"
    )
    rows[cross_index] = {
        **rows[cross_index],
        "reason_code": "TASK7_SQL_AUTHORITY_REJECTED:DASHBOARD_ROOT_EQUATION",
        "rejection_path": "EQUATION_V3",
    }
    with pytest.raises(
        runner.Task8V27CensusError,
        match="^TASK8_V27_CENSUS_REQUIREMENT_MATRIX_INVALID$",
    ):
        runner.validate_requirement_matrix_result_rows(
            rows,
            expected_application_role="app_role",
        )


def test_v27_legal_requirement_vectors_only_observe_the_committed_production_graph() -> None:
    runner = _sql()
    matrix = inspect.getsource(runner._run_dashboard_requirement_matrix)
    case = inspect.getsource(runner._execute_requirement_case)
    seed = inspect.getsource(runner._build_requirement_seed_spec)
    assert "_observe_legal_requirement_cases" in matrix
    assert 'if vector["accepted"] is False' in matrix
    assert "if expected_accepted" in case
    assert "TASK8_V27_CENSUS_REQUIREMENT_FIXTURE_SCOPE_INVALID" in case
    assert 'vector.get("accepted") is not False' in seed


def test_v27_undeclared_calculation_uses_named_production_graph_then_one_valid_root() -> None:
    runner = _sql()
    case = inspect.getsource(runner._execute_requirement_case)
    helper = inspect.getsource(runner._attach_undeclared_calculation_root)
    assert 'case_id in {"CROSS_VERSION_JOIN", "UNDECLARED_FDD_CALCULATION_ROOT"}' in case
    assert "_persist_bound_runtime_graph" in case
    assert "_attach_undeclared_calculation_root" in case
    assert "_persist_seed_native_root" in helper
    assert "deterministic_calculations" in helper
    assert "calc_sanads" in helper
    assert "SET CONSTRAINTS ALL IMMEDIATE" in helper
    assert "_verify_seed_native_roots" in helper


def test_v27_global_99_case_reaches_the_installed_guard_after_valid_partial_seed() -> None:
    """A setup rejection is not evidence for the reviewed 99-row guard case."""

    runner = _sql()
    execution = inspect.getsource(runner._execute_behavior_case)
    dispatcher = inspect.getsource(runner._seed_dashboard_batch)
    partial = inspect.getsource(runner._persist_partial_dashboard_runtime_graph)
    task5 = inspect.getsource(runner._persist_task5_seed_plan)
    assert "_persist_partial_dashboard_runtime_graph" in dispatcher
    assert "global_dashboard_plan.seed_coverage_row_count" in execution
    assert "_insert_coverage_record" in task5
    assert "SET CONSTRAINTS ALL IMMEDIATE" in task5
    assert "_insert_dashboard_batch" in execution
    assert "ctrg_v3_dashboard_root_equation" in execution
    assert "guard_reached" in execution
    assert "TASK8_V27_CENSUS_COVERAGE_ROW_COUNT_INVALID" not in partial


def test_v27_partial_99_plan_is_an_explicit_exact_delta_from_normative_100() -> None:
    runner = _sql()
    rows = [
        {
            "coverage_record_id": f"mc_{ordinal:024d}",
            "methodology_type": "commercial_dd" if ordinal <= 21 else "financial_dd",
            "required_calculation_count": 1 if 22 <= ordinal <= 31 else 0,
            "coverage_ordinal": ordinal,
        }
        for ordinal in range(1, 101)
    ]
    payload = {
        "schema_version": "task8-v27-dashboard-seed-plan-v2",
        "coverage_row_count": 100,
        "commercial_row_count": 21,
        "financial_row_count": 79,
        "required_calculation_count": 10,
        "row_tuple": rows,
    }
    full = {**payload, "expected_sha256": runner._canonical_sha256(payload)}
    partial = runner._partial_dashboard_seed_plan(
        full,
        coverage_row_count=99,
        financial_coverage_row_count=78,
    )
    assert partial["coverage_row_count"] == 99
    assert partial["commercial_row_count"] == 21
    assert partial["financial_row_count"] == 78
    assert sum(row["required_calculation_count"] for row in partial["row_tuple"]) == 10
    omitted = {row["coverage_record_id"] for row in rows} - {
        row["coverage_record_id"] for row in partial["row_tuple"]
    }
    assert len(omitted) == 1


def test_v27_task5_roundtrip_binding_rejects_any_reloaded_byte_drift() -> None:
    runner = _sql()
    identities, selection, projections = _task5_roundtrip_fixture(runner)
    question_projection, version_projection, selection_projection = projections
    receipt = runner.build_task5_roundtrip_binding(
        planned_registry_identity_tuple=identities,
        reloaded_registry_identity_tuple=deepcopy(identities),
        planned_selection_identity=selection,
        reloaded_selection_identity=deepcopy(selection),
        app_observed_question_projection_sha256=question_projection,
        app_observed_version_projection_sha256=version_projection,
        app_observed_selection_projection_sha256=selection_projection,
        selection_immutability_sha256=_task5_selection_immutability_sha256(),
    )
    assert receipt["schema_version"] == "task8-v27-task5-roundtrip-binding-v2"
    assert receipt["registry_roundtrip_equal"] is True
    assert receipt["selection_roundtrip_equal"] is True
    assert receipt["question_projection_sha256"] == question_projection
    assert receipt["version_projection_sha256"] == version_projection
    assert receipt["selection_projection_sha256"] == selection_projection
    assert len(receipt["expected_sha256"]) == 64

    drifted = list(deepcopy(identities))
    drifted[1]["required_calculation_tuple_sha256"] = "0" * 64
    with pytest.raises(
        runner.Task8V27CensusError,
        match="^TASK8_V27_CENSUS_TASK5_ROUNDTRIP_INVALID$",
    ):
        runner.build_task5_roundtrip_binding(
            planned_registry_identity_tuple=identities,
            reloaded_registry_identity_tuple=tuple(drifted),
            planned_selection_identity=selection,
            reloaded_selection_identity=selection,
            app_observed_question_projection_sha256=question_projection,
            app_observed_version_projection_sha256=version_projection,
            app_observed_selection_projection_sha256=selection_projection,
            selection_immutability_sha256=_task5_selection_immutability_sha256(),
        )

    invalid_family = list(deepcopy(identities))
    invalid_family[1]["source_type"] = "checked_in_template"
    with pytest.raises(
        runner.Task8V27CensusError,
        match="^TASK8_V27_CENSUS_TASK5_ROUNDTRIP_INVALID$",
    ):
        runner.build_task5_roundtrip_binding(
            planned_registry_identity_tuple=invalid_family,
            reloaded_registry_identity_tuple=deepcopy(invalid_family),
            planned_selection_identity=selection,
            reloaded_selection_identity=selection,
            app_observed_question_projection_sha256=question_projection,
            app_observed_version_projection_sha256=version_projection,
            app_observed_selection_projection_sha256=selection_projection,
            selection_immutability_sha256=_task5_selection_immutability_sha256(),
        )

    with pytest.raises(
        runner.Task8V27CensusError,
        match="^TASK8_V27_CENSUS_TASK5_ROUNDTRIP_INVALID$",
    ):
        runner.build_task5_roundtrip_binding(
            planned_registry_identity_tuple=identities,
            reloaded_registry_identity_tuple=identities,
            planned_selection_identity=selection,
            reloaded_selection_identity=selection,
            app_observed_question_projection_sha256="d" * 64,
            app_observed_version_projection_sha256=version_projection,
            app_observed_selection_projection_sha256=selection_projection,
            selection_immutability_sha256=_task5_selection_immutability_sha256(),
        )


def test_v27_task5_roundtrip_rejects_missing_extra_and_order_drift() -> None:
    runner = _sql()
    identities, selection, projections = _task5_roundtrip_fixture(runner)
    question_projection, version_projection, selection_projection = projections
    mutations = (
        identities[:1],
        (*identities, {**identities[1], "methodology_type": "extra"}),
        tuple(reversed(identities)),
    )
    for observed in mutations:
        with pytest.raises(
            runner.Task8V27CensusError,
            match="^TASK8_V27_CENSUS_TASK5_ROUNDTRIP_INVALID$",
        ):
            runner.build_task5_roundtrip_binding(
                planned_registry_identity_tuple=identities,
                reloaded_registry_identity_tuple=observed,
                planned_selection_identity=selection,
                reloaded_selection_identity=selection,
                app_observed_question_projection_sha256=question_projection,
                app_observed_version_projection_sha256=version_projection,
                app_observed_selection_projection_sha256=selection_projection,
                selection_immutability_sha256=_task5_selection_immutability_sha256(),
            )

    for invalid_selection in (
        {key: value for key, value in selection.items() if key != "immutable"},
        {**selection, "unknown": True},
        {**selection, "selected_question_count": 99},
    ):
        with pytest.raises(
            runner.Task8V27CensusError,
            match="^TASK8_V27_CENSUS_TASK5_ROUNDTRIP_INVALID$",
        ):
            runner.build_task5_roundtrip_binding(
                planned_registry_identity_tuple=identities,
                reloaded_registry_identity_tuple=identities,
                planned_selection_identity=invalid_selection,
                reloaded_selection_identity=deepcopy(invalid_selection),
                app_observed_question_projection_sha256=question_projection,
                app_observed_version_projection_sha256=version_projection,
                app_observed_selection_projection_sha256=selection_projection,
                selection_immutability_sha256=_task5_selection_immutability_sha256(),
            )


def test_v27_task5_db_projection_binds_full_reloaded_registry_and_selection() -> None:
    runner = _sql()
    persistence = inspect.getsource(runner._persist_task5_seed_plan)
    identity = inspect.getsource(runner._methodology_registry_roundtrip_identity)
    for authority in (
        "methodology_question_payload_hashes",
        "methodology_source_trace_digest",
        "methodology_version_hash",
        "to_deterministic_json",
        "load_selected_registries",
        "required_calculation_tuple_sha256",
        "app_observed_version_projection_sha256",
        "app_observed_selection_projection_sha256",
        "build_task5_roundtrip_binding",
    ):
        assert authority in persistence + identity


def test_v27_task5_selection_immutability_is_installed_and_behaviorally_proven() -> None:
    runner = _sql()
    authority = runner._task5_selection_guard_authority(json.loads(_artifact_raw()))
    assert authority["upgrade_statement_sha256"] == (
        "564ea8d682639b79cf12ef4f4e96b634deffc9facf3f4a354354079c5975ee47"
    )
    assert authority["semantic_body_sha256"] == (
        "56083eb83dadb9d1ea7855af2f0f964ae01222f623e70bf548498d598ac1a763"
    )
    assert authority["proconfig_tuple"] == ["search_path=pg_catalog, pg_temp"]
    assert authority["trigger_tuple"] == [
        "public.run_methodology_selections",
        "trg_run_methodology_selections_guard",
        "BEFORE",
        ["INSERT", "UPDATE", "DELETE"],
        "ROW",
        "O",
        False,
        False,
    ]
    observer = inspect.getsource(runner._observe_task5_selection_immutability)
    persistence = inspect.getsource(runner._persist_task5_seed_plan)
    for required in (
        "pg_catalog.pg_proc",
        "pg_catalog.pg_trigger",
        "_attest_connection_role_posture",
        "SAVEPOINT task8_v27_task5_immutability",
        "UPDATE public.run_methodology_selections",
        "DELETE FROM public.run_methodology_selections",
        "RUN_METHODOLOGY_SELECTION_IMMUTABLE",
        "zero_mutation",
    ):
        assert required in observer
    assert "_observe_task5_selection_immutability" in persistence
    assert '"immutable": True' not in persistence
    assert "selection_immutability_sha256" in inspect.getsource(
        runner.build_task5_roundtrip_binding
    )


def test_v27_task5_roundtrip_rejects_every_field_and_row_shape_drift() -> None:
    runner = _sql()
    identities, selection, projections = _task5_roundtrip_fixture(runner)
    question_projection, version_projection, selection_projection = projections

    def changed(value: object) -> object:
        if type(value) is bool:
            return not value
        if type(value) is int:
            return value + 1
        if value is None:
            return "drift"
        if isinstance(value, str):
            return value + "-drift"
        if isinstance(value, list):
            return [*deepcopy(value), "drift"]
        raise AssertionError(type(value))

    def reject(
        reloaded_identities: object,
        reloaded_selection: object = selection,
    ) -> None:
        with pytest.raises(
            runner.Task8V27CensusError,
            match="^TASK8_V27_CENSUS_TASK5_ROUNDTRIP_INVALID$",
        ):
            runner.build_task5_roundtrip_binding(
                planned_registry_identity_tuple=identities,
                reloaded_registry_identity_tuple=reloaded_identities,
                planned_selection_identity=selection,
                reloaded_selection_identity=reloaded_selection,
                app_observed_question_projection_sha256=question_projection,
                app_observed_version_projection_sha256=version_projection,
                app_observed_selection_projection_sha256=selection_projection,
                selection_immutability_sha256=_task5_selection_immutability_sha256(),
            )

    for registry_index, identity in enumerate(identities):
        for field in EXPECTED_TASK5_REGISTRY_IDENTITY_FIELDS:
            mutated = list(deepcopy(identities))
            mutated[registry_index][field] = changed(identity[field])
            reject(tuple(mutated))
        for question_field_index in range(len(EXPECTED_TASK5_QUESTION_IDENTITY_FIELDS)):
            mutated = list(deepcopy(identities))
            row = mutated[registry_index]["question_identity_tuple"][0]
            row[question_field_index] = changed(row[question_field_index])
            mutated[registry_index]["question_identity_tuple_sha256"] = runner._canonical_sha256(
                mutated[registry_index]["question_identity_tuple"]
            )
            reject(tuple(mutated))
        for shape in ("missing", "extra", "reordered"):
            mutated = list(deepcopy(identities))
            rows = mutated[registry_index]["question_identity_tuple"]
            if shape == "missing":
                rows.pop()
            elif shape == "extra":
                rows.append(deepcopy(rows[-1]))
            else:
                rows[0], rows[1] = rows[1], rows[0]
            mutated[registry_index]["question_identity_tuple_sha256"] = runner._canonical_sha256(
                rows
            )
            reject(tuple(mutated))

    for field in EXPECTED_TASK5_SELECTION_IDENTITY_FIELDS:
        mutated_selection = deepcopy(selection)
        mutated_selection[field] = changed(selection[field])
        reject(identities, mutated_selection)

    for registry_index, row_index in ((0, 20), (1, 0)):
        requirements = identities[registry_index]["question_identity_tuple"][row_index][18]
        for requirement_index, requirement in enumerate(requirements):
            for field in tuple(requirement):
                mutated = list(deepcopy(identities))
                target = mutated[registry_index]["question_identity_tuple"][row_index][18][
                    requirement_index
                ]
                target[field] = changed(target[field])
                mutated[registry_index]["question_identity_tuple_sha256"] = (
                    runner._canonical_sha256(mutated[registry_index]["question_identity_tuple"])
                )
                mutated[registry_index]["required_calculation_tuple"] = [
                    [row[1], row[18]] for row in mutated[registry_index]["question_identity_tuple"]
                ]
                mutated[registry_index]["required_calculation_tuple_sha256"] = (
                    runner._canonical_sha256(mutated[registry_index]["required_calculation_tuple"])
                )
                reject(tuple(mutated))

        base_requirement = requirements[0]
        requirement_shapes: list[dict[str, object]] = [
            {
                key: value
                for key, value in base_requirement.items()
                if key != next(iter(base_requirement))
            },
            {**base_requirement, "unknown": "forbidden"},
        ]
        for field, value in base_requirement.items():
            wrong_type = list(value) if isinstance(value, str) else str(value)
            requirement_shapes.append({**base_requirement, field: wrong_type})
        for replacement in requirement_shapes:
            mutated = list(deepcopy(identities))
            target_row = mutated[registry_index]["question_identity_tuple"][row_index]
            target_row[18] = [replacement]
            replacement_payload_sha256 = runner._canonical_sha256(
                ["task8-v27-mutated-question-payload-v1", target_row[:16], target_row[18]]
            )
            target_row[16] = replacement_payload_sha256
            target_row[17] = replacement_payload_sha256
            mutated[registry_index]["methodology_question_payload_hashes"] = [
                row[16] for row in mutated[registry_index]["question_identity_tuple"]
            ]
            mutated[registry_index]["question_identity_tuple_sha256"] = runner._canonical_sha256(
                mutated[registry_index]["question_identity_tuple"]
            )
            mutated[registry_index]["required_calculation_tuple"] = [
                [row[1], row[18]] for row in mutated[registry_index]["question_identity_tuple"]
            ]
            mutated[registry_index]["required_calculation_tuple_sha256"] = runner._canonical_sha256(
                mutated[registry_index]["required_calculation_tuple"]
            )
            reject(tuple(mutated))

    reordered = list(deepcopy(identities))
    reordered[1]["required_calculation_tuple"][0], reordered[1]["required_calculation_tuple"][1] = (
        reordered[1]["required_calculation_tuple"][1],
        reordered[1]["required_calculation_tuple"][0],
    )
    reordered[1]["required_calculation_tuple_sha256"] = runner._canonical_sha256(
        reordered[1]["required_calculation_tuple"]
    )
    reject(tuple(reordered))


def test_v27_admin_is_schema_only_and_all_fixture_dml_is_role_attested() -> None:
    runner = _sql()
    setup = inspect.getsource(runner._setup_disposable_schema)
    grants = inspect.getsource(runner._grant_and_verify_application_schema_privileges)
    seed_scope = inspect.getsource(runner._seed_scope)
    seed_source = inspect.getsource(runner._stage_and_freeze_source_graph)
    seed_erasure = inspect.getsource(runner._seed_erasure_request)
    raw_fixture_connection = inspect.getsource(runner._connect_fixture_dml)
    runner_source = SQL_RUNNER.read_text(encoding="utf-8")

    assert "_grant_and_verify_application_schema_privileges" in setup
    assert "_migration_privilege_catalog_is_exact" in grants
    assert "GRANT SELECT, INSERT, UPDATE, DELETE" in grants
    assert "application_url" in inspect.signature(runner._seed_scope).parameters
    assert "expected_application_role" in inspect.signature(runner._seed_scope).parameters
    assert "application_url" in inspect.signature(runner._seed_erasure_request).parameters
    assert "expected_application_role" in inspect.signature(runner._seed_erasure_request).parameters
    assert "admin_url" not in inspect.signature(runner._stage_and_freeze_source_graph).parameters
    assert "_connect_fixture_dml" in seed_scope
    assert "_attest_connection_role_posture" in seed_source
    assert "_connect_fixture_dml" in seed_erasure
    assert "_FixtureDmlConnection" in raw_fixture_connection
    assert "_set_tenant" in seed_scope
    assert "set_tenant_local" in seed_source
    assert "_set_tenant" in seed_erasure
    assert "_record_fixture_dml_role" not in seed_scope + seed_erasure
    assert "_instrument_fixture_dml_engine" in seed_source
    assert "_seed_scope(urls.admin" not in runner_source
    assert "_seed_erasure_request(urls.admin" not in runner_source
    assert "create_engine(admin_url)" not in seed_source


def test_v27_pregrant_catalog_is_exact_physical67_application66() -> None:
    runner = _sql()
    assert runner._APPLICATION_RELATION_TUPLE == (v27_oracle.SUCCESSOR_APPLICATION_RELATION_TUPLE)
    assert len(runner._APPLICATION_RELATION_TUPLE) == 66
    assert runner._PHYSICAL_PUBLIC_RELATION_TUPLE == (
        v27_oracle.PHYSICAL_PUBLIC_ORDINARY_RELATION_TUPLE
    )
    assert len(runner._PHYSICAL_PUBLIC_RELATION_TUPLE) == 67
    assert runner._EXCLUDED_APPLICATION_RELATION_TUPLE == ("alembic_version",)
    assert runner._APPLICATION_RELATION_TUPLE_SHA256 == (
        "e06330cdf5eabf5e1f95da4e66537de60476460ff9af6c0ebd7fd920a5ae6c39"
    )
    assert runner._PHYSICAL_PUBLIC_RELATION_TUPLE_SHA256 == (
        "85f9d8162c0241f88cf560e83689ce1ea7baff6c0de9a6cdddda512ac4df27f6"
    )
    observer = inspect.getsource(runner._observe_pregrant_application_catalog)
    assert "pg_catalog.pg_class" in observer
    assert "has_table_privilege" in observer
    assert "has_any_column_privilege" in observer
    assert "aclexplode" in observer
    assert "alembic_version" in observer
    assert (
        runner._pregrant_application_catalog_payload()[
            "excluded_application_direct_privilege_count"
        ]
        == 0
    )
    grants = inspect.getsource(runner._grant_and_verify_application_schema_privileges)
    assert (
        "pregrant_catalog"
        in inspect.signature(runner._grant_and_verify_application_schema_privileges).parameters
    )
    assert "table_names = _APPLICATION_RELATION_TUPLE" in grants
    assert "table_names = _PHYSICAL_PUBLIC_RELATION_TUPLE" not in grants
    setup = inspect.getsource(runner._setup_disposable_schema)
    assert setup.index("_observe_pregrant_application_catalog") < setup.index(
        "execute_hash_bound_authority_sql"
    )
    assert setup.index("_observe_pregrant_application_catalog") < setup.index(
        "_grant_and_verify_application_schema_privileges"
    )


def test_v27_setup_observes_catalog_before_any_reviewed_grant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = _sql()
    events: list[str] = []

    class Begin:
        def __init__(self, connection: FakeConnection) -> None:
            self.connection = connection

        def __enter__(self) -> FakeConnection:
            return self.connection

        def __exit__(self, *_exc_info: object) -> None:
            return None

    class FakeConnection:
        def exec_driver_sql(self, statement: str) -> None:
            events.append(f"sql:{' '.join(statement.split())}")

    class FakeEngine:
        def __init__(self) -> None:
            self.connection = FakeConnection()

        def begin(self) -> Begin:
            return Begin(self.connection)

        def dispose(self) -> None:
            events.append("dispose")

    import sqlalchemy

    monkeypatch.setattr(sqlalchemy, "create_engine", lambda _url: FakeEngine())
    monkeypatch.setattr(
        runner,
        "_run_migrations_through_0038",
        lambda _engine, _snapshot: events.append("migrations"),
    )
    monkeypatch.setattr(
        runner,
        "build_future_table_scaffold",
        lambda *_args: {"statement_tuple": ("CREATE TABLE public.future_probe(id int)",)},
    )
    monkeypatch.setattr(
        runner,
        "build_application_behavior_grants",
        lambda *_args: {"statement_tuple": ("GRANT SELECT ON public.future_probe TO role",)},
    )

    def observe(*_args: object, **_kwargs: object) -> dict[str, object]:
        events.append("observe")
        return {"expected_sha256": "1" * 64}

    def install(*_args: object, **_kwargs: object) -> dict[str, object]:
        events.append("authority-install")
        return {"bundle_sha256": "2" * 64}

    def grant(*_args: object, **_kwargs: object) -> dict[str, object]:
        events.append("application-grant")
        return {"expected_sha256": "3" * 64}

    monkeypatch.setattr(runner, "_observe_pregrant_application_catalog", observe)
    monkeypatch.setattr(runner, "execute_hash_bound_authority_sql", install)
    monkeypatch.setattr(runner, "_grant_and_verify_application_schema_privileges", grant)
    runner._setup_disposable_schema(
        "postgresql://admin/db",
        migration_snapshot=object(),
        application_role="idis_application",
        erasure_role="idis_erasure",
        v27={},
        authority_execution={"expected_sha256": "2" * 64},
    )
    assert events.index("observe") < events.index("authority-install")
    assert events.index("observe") < events.index("application-grant")
    assert not any(event.startswith("sql:GRANT ") for event in events[: events.index("observe")])


def test_v27_execution_flattens_and_revalidates_pregrant_catalog_evidence() -> None:
    runner = _sql()
    pregrant_payload = {
        **runner._pregrant_application_catalog_payload(),
        "observed_before_any_grant": True,
        "application_role": "idis_application",
    }
    pregrant = {
        **pregrant_payload,
        "expected_sha256": runner._canonical_sha256(
            ["task8-v27-pregrant-application-catalog-v1", pregrant_payload]
        ),
    }
    privilege_payload = {
        "schema_version": "task8-v27-application-privilege-boundary-v1",
        "pregrant_catalog_sha256": pregrant["expected_sha256"],
        "granted_application_relation_count": 66,
        "granted_application_relation_tuple_sha256": (
            "e06330cdf5eabf5e1f95da4e66537de60476460ff9af6c0ebd7fd920a5ae6c39"
        ),
        "excluded_application_relation_tuple": ["alembic_version"],
        "excluded_application_direct_privilege_count": 0,
        "excluded_application_effective_privilege_count": 0,
        "verified_by_production_catalog_predicate": True,
    }
    privilege = {
        **privilege_payload,
        "expected_sha256": runner._canonical_sha256(
            ["task8-v27-application-privilege-boundary-v1", privilege_payload]
        ),
    }
    authority_binding = {
        "pregrant_application_catalog": pregrant,
        "application_privilege_boundary": privilege,
    }
    assert runner._pregrant_execution_binding(authority_binding) == {
        "pregrant_physical_public_relation_count": 67,
        "pregrant_application_relation_count": 66,
        "pregrant_excluded_relation_count": 1,
        "pregrant_excluded_application_direct_privilege_count": 0,
        "pregrant_excluded_application_effective_privilege_count": 0,
        "pregrant_application_catalog_sha256": pregrant["expected_sha256"],
        "application_privilege_boundary_sha256": privilege["expected_sha256"],
    }

    def reject(mutated_binding: dict[str, object]) -> None:
        with pytest.raises(
            runner.Task8V27CensusError,
            match="^TASK8_V27_CENSUS_APPLICATION_CATALOG_INVALID$",
        ):
            runner._pregrant_execution_binding(mutated_binding)

    for section, field, value in (
        ("pregrant_application_catalog", "physical_public_relation_count", 66),
        ("pregrant_application_catalog", "application_relation_count", 65),
        ("pregrant_application_catalog", "excluded_application_relation_tuple", []),
        ("pregrant_application_catalog", "excluded_application_direct_privilege_count", 1),
        ("pregrant_application_catalog", "excluded_application_effective_privilege_count", 1),
        ("pregrant_application_catalog", "observed_before_any_grant", False),
        ("application_privilege_boundary", "granted_application_relation_count", 67),
        ("application_privilege_boundary", "excluded_application_direct_privilege_count", 1),
        ("application_privilege_boundary", "excluded_application_effective_privilege_count", 1),
    ):
        mutated = deepcopy(authority_binding)
        mutated_section = mutated[section]
        assert isinstance(mutated_section, dict)
        mutated_section[field] = value
        reject(mutated)

    producer = inspect.getsource(runner._run_database_execution)
    validator = inspect.getsource(runner.validate_census_receipt)
    assert "_pregrant_execution_binding" in producer
    assert "_pregrant_execution_binding" in validator


def test_v27_fixture_dml_ledger_binds_tenant_origin_and_internal_freeze() -> None:
    runner = _sql()
    record = inspect.getsource(runner._record_fixture_dml_role)
    raw_connection = inspect.getsource(runner._connect_fixture_dml)
    engine = inspect.getsource(runner._instrument_fixture_dml_engine)
    source_freeze = inspect.getsource(runner._stage_and_freeze_source_graph)
    runtime_graph = inspect.getsource(runner._persist_runtime_graph_core)
    seed_scope = inspect.getsource(runner._seed_scope)
    seed_erasure = inspect.getsource(runner._seed_erasure_request)
    undeclared_root = inspect.getsource(runner._attach_undeclared_calculation_root)
    for field in (
        "tenant_id",
        "session_replication_role",
        "transaction_outcome",
        "dml_statement_sha256_tuple",
        "dml_statement_count",
        "dml_statement_tuple_sha256",
        "ledger_ordinal",
    ):
        assert field in record + raw_connection + engine
    assert "_connect_fixture_dml" in seed_scope + seed_erasure
    assert "_record_fixture_dml_role" not in seed_scope + seed_erasure
    assert "_connect_fixture_dml" in undeclared_root
    assert "before_cursor_execute" in engine
    assert "commit" in engine
    assert "rollback" in engine
    assert "_instrument_fixture_dml_engine" in source_freeze
    assert "_instrument_fixture_dml_engine" in runtime_graph
    execution = inspect.getsource(runner._run_database_execution)
    validator = inspect.getsource(runner.validate_census_receipt)
    assert "_validate_census_dml_closed_ledger" in execution
    assert "_validate_census_dml_closed_ledger" in validator


def test_v27_every_census_dml_call_site_uses_one_observed_boundary() -> None:
    runner = _sql()
    requirement = inspect.getsource(runner._execute_requirement_case)
    rls = inspect.getsource(runner._run_rls_behavior_matrix)
    behavior = inspect.getsource(runner._execute_behavior_case)
    races = inspect.getsource(runner._run_lock_races)
    assert "_connect_behavior_dml" in requirement
    assert "_connect_fixture_dml" in rls
    assert "_connect_behavior_dml" in rls
    assert "_connect_fixture_dml" in behavior
    assert "_connect_behavior_dml" in behavior
    assert races.count("_connect_behavior_dml") >= 4
    execution = inspect.getsource(runner._run_database_execution)
    validator = inspect.getsource(runner.validate_census_receipt)
    assert "_validate_census_dml_closed_ledger" in execution + validator


def test_v27_sqlalchemy_fixture_dml_wraps_every_direct_dbapi_cursor() -> None:
    runner = _sql()
    for function in (
        runner._observe_task5_selection_immutability,
        runner._persist_task5_seed_plan,
        runner._persist_adversarial_requirement_task5_seed,
        runner._stage_and_freeze_source_graph,
        runner._persist_runtime_graph_core,
    ):
        source = inspect.getsource(function)
        assert "connection.connection.cursor()" not in source
        assert "_instrumented_dbapi_cursor" in source


def test_v27_dml_recorder_rejects_empty_non_dml_and_cte_led_statements() -> None:
    runner = _sql()
    posture = {
        "current_user": "idis_application",
        "session_user": "idis_application",
        "rolsuper": False,
        "rolbypassrls": False,
        "session_replication_role": "origin",
    }
    common = {
        "case_coordinate": "MAIN",
        "dml_classification": "APPLICATION_FIXTURE",
        "operation": "NEGATIVE_RECORDER:0001",
        "posture": posture,
        "expected_application_role": "idis_application",
        "tenant_id": _test_fixture_uuid("case-scope", "MAIN", "tenant"),
        "session_tenant_id": _test_fixture_uuid("case-scope", "MAIN", "tenant"),
    }
    for statements in ((), ("SELECT 1",)):
        with pytest.raises(
            runner.Task8V27CensusError,
            match="^TASK8_V27_CENSUS_FIXTURE_DML_ROLE_INVALID$",
        ):
            runner._record_fixture_dml_role(
                **common,
                dml_statement_tuple=statements,
            )
    with pytest.raises(
        runner.Task8V27CensusError,
        match="^TASK8_V27_CENSUS_FIXTURE_DML_CTE_UNSUPPORTED$",
    ):
        runner._normalized_fixture_dml_statement(
            "WITH selected AS (SELECT 1) INSERT INTO public.tenants SELECT * FROM selected"
        )
    for statement in (
        "/* comment */ INSERT INTO public.tenants DEFAULT VALUES",
        "-- comment\nUPDATE public.tenants SET name='x'",
        "/* outer /* nested */ tail */ -- second\nDELETE FROM public.tenants",
        "INSERT/*x*/INTO public.tenants DEFAULT VALUES",
        "UPDATE/*x*/public.tenants SET name='x'",
        "DELETE/*x*/FROM public.tenants",
        (
            "MERGE/*x*/INTO public.tenants USING public.tenants ON false "
            "WHEN NOT MATCHED THEN DO NOTHING"
        ),
        "TRUNCATE/*x*/public.tenants",
        "CALL public.fixture_mutator()",
        "DO $$ BEGIN DELETE FROM public.tenants; END $$",
    ):
        with pytest.raises(
            runner.Task8V27CensusError,
            match="^TASK8_V27_CENSUS_FIXTURE_DML_FORM_UNSUPPORTED$",
        ):
            runner._normalized_fixture_dml_statement(statement)


@pytest.mark.parametrize(
    "statement",
    (
        "SELECT 1; CALL public.fixture_mutator()",
        "SELECT 1; COPY public.tenants FROM STDIN",
        "SELECT 1; REFRESH MATERIALIZED VIEW public.fixture_projection",
        "SELECT 1; CREATE TABLE public.fixture_copy AS SELECT 1",
        "SET LOCAL idis.tenant_id='00000000-0000-4000-8000-000000000000'; "
        "CALL public.fixture_mutator()",
        "; CALL public.fixture_mutator()",
        " ;\nCOPY public.tenants FROM STDIN",
        ";;; REFRESH MATERIALIZED VIEW public.fixture_projection",
        "; CREATE TABLE public.fixture_copy AS SELECT 1",
        ("SELECT 1 AS foo$task8$; CALL public.fixture_mutator(); SELECT 1 AS bar$task8$"),
        (
            "SELECT 1 AS fooe\u0301$task8$; CALL public.fixture_mutator(); "
            "SELECT 1 AS bare\u0301$task8$"
        ),
    ),
    ids=(
        "call",
        "copy",
        "refresh",
        "create-as",
        "set-call",
        "leading-empty-call",
        "leading-empty-copy",
        "leading-empty-refresh",
        "leading-empty-create-as",
        "embedded-dollar-tag-identifiers",
        "unicode-identifier-dollar-tag-boundary",
    ),
)
def test_v27_multicommand_or_leading_empty_mutation_wrappers_fail_closed_at_every_dml_boundary(
    statement: str,
) -> None:
    runner = _sql()

    class FakeCursor:
        def __init__(self) -> None:
            self.executed: list[str] = []

        def execute(self, sql: object, _parameters: object = None) -> None:
            self.executed.append(str(sql))

    class FakeRawConnection:
        def __init__(self) -> None:
            self.cursor_value = FakeCursor()

        def cursor(self) -> FakeCursor:
            return self.cursor_value

    with pytest.raises(
        runner.Task8V27CensusError,
        match="^TASK8_V27_CENSUS_FIXTURE_DML_FORM_UNSUPPORTED$",
    ):
        runner._normalized_fixture_dml_statement(statement)

    raw_connection = FakeRawConnection()
    rejecting = runner._UnclassifiedDmlRejectingConnection(raw_connection)
    with pytest.raises(
        runner.Task8V27CensusError,
        match="^TASK8_V27_CENSUS_FIXTURE_DML_FORM_UNSUPPORTED$",
    ):
        rejecting.cursor().execute(statement)
    assert raw_connection.cursor_value.executed == []

    engine_connection = type(
        "FakeEngineConnection",
        (),
        {"info": {runner._ENGINE_DML_CONFIG_KEY: {}}},
    )()
    with pytest.raises(
        runner.Task8V27CensusError,
        match="^TASK8_V27_CENSUS_FIXTURE_DML_FORM_UNSUPPORTED$",
    ):
        runner._observe_fixture_dml_engine_statement(engine_connection, statement)


def test_v27_fixture_dml_allows_semicolons_only_inside_one_quoted_statement() -> None:
    runner = _sql()
    for statement in (
        "UPDATE public.tenants SET name='quoted;value'",
        "UPDATE public.tenants SET name='doubled'';quote'",
        "UPDATE public.tenants SET name=$task8$quoted;value$task8$",
        "UPDATE public.tenants SET name=$é$quoted;value$é$",
        "UPDATE public.tenants SET name=$e\u0301$quoted;value$e\u0301$",
        'UPDATE public.tenants SET "quoted;column"=name',
        "UPDATE public.tenants SET name='trailing semicolon';",
    ):
        assert runner._normalized_fixture_dml_statement(statement) == statement


@pytest.mark.parametrize(
    "statement",
    (
        "SELECT public.idis_task7_execute_approved_erasure('case-id');",
        "SELECT public.idis_task7_execute_approved_erasure('case-id') ;",
        "SELECT public.idis_task7_execute_approved_erasure('case-id')\n;",
    ),
    ids=("adjacent", "space", "newline"),
)
def test_v27_trailing_terminator_erasure_call_is_classified_at_every_dml_boundary(
    statement: str,
) -> None:
    runner = _sql()
    normalized = " ".join(statement.split())
    assert runner._normalized_fixture_dml_statement(statement) == normalized

    class FakeCursor:
        def __init__(self) -> None:
            self.executed: list[str] = []

        def execute(self, sql: object, _parameters: object = None) -> None:
            self.executed.append(str(sql))

    class FakeRawConnection:
        def __init__(self) -> None:
            self.cursor_value = FakeCursor()

        def cursor(self) -> FakeCursor:
            return self.cursor_value

    raw_connection = FakeRawConnection()
    rejecting = runner._UnclassifiedDmlRejectingConnection(raw_connection)
    with pytest.raises(
        runner.Task8V27CensusError,
        match="^TASK8_V27_CENSUS_UNCLASSIFIED_DML_FORBIDDEN$",
    ):
        rejecting.cursor().execute(statement)
    assert raw_connection.cursor_value.executed == []

    info_key = "task8_v27_test_erasure_transaction"
    transaction: dict[str, object] = {
        "statements": [],
        "posture": {"current_user": "idis_erasure_executor"},
    }
    engine_connection = type(
        "FakeEngineConnection",
        (),
        {
            "info": {
                runner._ENGINE_DML_CONFIG_KEY: {
                    "info_key": info_key,
                    "expected_role": "idis_erasure_executor",
                    "expected_tenant": "00000000-0000-4000-8000-000000000000",
                },
                info_key: transaction,
            }
        },
    )()
    runner._observe_fixture_dml_engine_statement(engine_connection, statement)
    assert transaction["statements"] == [normalized]


def test_v27_raw_fixture_dml_connection_records_actual_closed_transactions() -> None:
    runner = _sql()
    tenant_id = uuid.UUID(_test_fixture_uuid("case-scope", "MAIN", "tenant"))
    application_role = "idis_application"

    class FakeCursor:
        def __init__(self, owner: FakeConnection) -> None:
            self.owner = owner
            self.result: tuple[object, ...] | None = None

        def execute(self, statement: object, parameters: object = None) -> None:
            normalized = " ".join(str(statement).split())
            self.owner.executed.append((normalized, parameters))
            if "FROM pg_catalog.pg_roles" in normalized:
                self.result = (application_role, application_role, False, False, "origin")
            elif "current_setting('idis.tenant_id',true)" in normalized:
                self.result = (str(tenant_id),)
            else:
                self.result = None

        def fetchone(self) -> tuple[object, ...] | None:
            return self.result

        def close(self) -> None:
            return None

        def __enter__(self) -> FakeCursor:
            return self

        def __exit__(self, *_exc_info: object) -> None:
            return None

    class FakeConnection:
        def __init__(self) -> None:
            self.executed: list[tuple[str, object]] = []
            self.commits = 0
            self.rollbacks = 0
            self.closed = False

        def cursor(self, *_args: object, **_kwargs: object) -> FakeCursor:
            return FakeCursor(self)

        def commit(self) -> None:
            self.commits += 1

        def rollback(self) -> None:
            self.rollbacks += 1

        def close(self) -> None:
            self.closed = True

    runner._reset_fixture_uuid_sequence()
    raw = FakeConnection()
    connection = runner._FixtureDmlConnection(
        raw,
        expected_application_role=application_role,
        tenant_id=tenant_id,
        session_tenant_id=tenant_id,
        case_coordinate="MAIN",
        dml_classification="APPLICATION_FIXTURE",
        operation_prefix="RAW_FIXTURE",
    )
    with connection.cursor() as cursor:
        cursor.execute("INSERT INTO public.tenants(tenant_id) VALUES (%s)", (tenant_id,))
    connection.commit()
    with connection.cursor() as cursor:
        cursor.execute("UPDATE public.tenants SET name='x'")
    connection.rollback()
    with connection.cursor() as cursor:
        cursor.execute("DELETE FROM public.tenants")
    connection.close()

    rows = runner._fixture_dml_role_rows()
    assert tuple(row["operation"] for row in rows) == (
        "RAW_FIXTURE:0001",
        "RAW_FIXTURE:0002",
        "RAW_FIXTURE:0003",
    )
    assert tuple(row["transaction_outcome"] for row in rows) == (
        "COMMIT",
        "ROLLBACK",
        "ROLLBACK",
    )
    assert all(row["tenant_id"] == str(tenant_id) for row in rows)
    assert all(row["current_user"] == application_role for row in rows)
    assert all(row["session_user"] == application_role for row in rows)
    assert all(row["session_replication_role"] == "origin" for row in rows)
    assert all(row["dml_statement_count"] == 1 for row in rows)
    assert raw.commits == 1
    assert raw.rollbacks == 2
    assert raw.closed is True


def test_v27_closed_dml_ledger_is_independent_exact_and_mutation_complete() -> None:
    runner = _sql()
    sequence = _test_expected_dml_sequence()
    assert len(sequence) == 225
    assert sum(row[1] == "APPLICATION_FIXTURE" for row in sequence) == 172
    assert sum(row[1] == "APPLICATION_BEHAVIOR" for row in sequence) == 47
    assert sum(row[1] == "ERASURE_BEHAVIOR" for row in sequence) == 6
    assert (
        _test_canonical_sha256(["task8-v27-census-dml-operation-sequence-v1", sequence])
        == EXPECTED_DML_SEQUENCE_SHA256
    )
    assert runner._expected_census_dml_operation_sequence() == sequence

    ledger = _test_closed_dml_ledger()
    binding = runner._validate_census_dml_closed_ledger(
        ledger,
        application_role="idis_application",
        erasure_role="idis_erasure_executor",
    )
    assert binding["fixture_dml_transaction_count"] == 225
    assert binding["application_fixture_dml_count"] == 172
    assert binding["application_behavior_dml_count"] == 47
    assert binding["erasure_behavior_dml_count"] == 6
    assert binding["admin_dml_case_count"] == 0
    assert binding["fixture_dml_operation_sequence_sha256"] == EXPECTED_DML_SEQUENCE_SHA256
    assert '"admin_dml_case_count": 0' not in inspect.getsource(
        runner._validate_census_dml_closed_ledger
    )

    def reject(mutated: list[dict[str, object]]) -> None:
        with pytest.raises(
            runner.Task8V27CensusError,
            match="^TASK8_V27_CENSUS_FIXTURE_DML_LEDGER_INVALID$",
        ):
            runner._validate_census_dml_closed_ledger(
                mutated,
                application_role="idis_application",
                erasure_role="idis_erasure_executor",
            )

    reject(deepcopy(ledger[:-1]))
    reject([*deepcopy(ledger), deepcopy(ledger[-1])])
    duplicate = deepcopy(ledger)
    duplicate[1] = deepcopy(duplicate[0])
    duplicate[1]["ledger_ordinal"] = 2
    reject(duplicate)
    reordered = deepcopy(ledger)
    reordered[0], reordered[1] = reordered[1], reordered[0]
    reordered[0]["ledger_ordinal"] = 1
    reordered[1]["ledger_ordinal"] = 2
    reject(reordered)

    field_mutations: dict[str, object] = {
        "ledger_ordinal": 2,
        "case_coordinate": "RLS",
        "dml_classification": "ERASURE_BEHAVIOR",
        "operation": "UNKNOWN:0001",
        "expected_role": "rogue_role",
        "current_user": "rogue_role",
        "session_user": "rogue_role",
        "tenant_id": "11111111-1111-4111-8111-111111111111",
        "session_tenant_id": "11111111-1111-4111-8111-111111111111",
        "session_replication_role": "replica",
        "transaction_outcome": "ROLLBACK",
        "dml_statement_sha256_tuple": ["1" * 64],
        "dml_statement_count": 2,
        "dml_statement_tuple_sha256": "2" * 64,
        "posture_sha256": "3" * 64,
    }
    for field, value in field_mutations.items():
        mutated = deepcopy(ledger)
        mutated[0][field] = value
        reject(mutated)


def test_v27_receipt_binds_zero_admin_dml_to_fail_closed_connection_inventory() -> None:
    runner = _sql()
    execution_source = inspect.getsource(runner._run_database_execution)
    validator_source = inspect.getsource(runner.validate_census_receipt)
    assert "_fixture_dml_role_rows" in execution_source
    assert "_validate_census_dml_closed_ledger" in execution_source
    assert "_validate_census_dml_closed_ledger" in validator_source

    source = SQL_RUNNER.read_text(encoding="utf-8")
    tree = ast.parse(source)
    raw_connection_owners: list[str] = []

    class RawConnectionVisitor(ast.NodeVisitor):
        def __init__(self) -> None:
            self.functions: list[str] = []

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            self.functions.append(node.name)
            self.generic_visit(node)
            self.functions.pop()

        def visit_Call(self, node: ast.Call) -> None:
            if isinstance(node.func, ast.Name) and node.func.id == "_connect_raw":
                raw_connection_owners.append(self.functions[-1])
            self.generic_visit(node)

    RawConnectionVisitor().visit(tree)
    assert raw_connection_owners == [
        "_connect",
        "_connect_fixture_dml",
        "_connect_behavior_dml",
        "_apply_admin_authority_ddl",
    ]

    class FakeCursor:
        def __init__(self) -> None:
            self.executed: list[str] = []

        def execute(self, statement: object, _parameters: object = None) -> None:
            self.executed.append(str(statement))

    class FakeConnection:
        def __init__(self) -> None:
            self.cursor_value = FakeCursor()

        def cursor(self) -> FakeCursor:
            return self.cursor_value

    rejecting = runner._UnclassifiedDmlRejectingConnection(FakeConnection())
    rejecting.cursor().execute("SELECT 1")
    assert rejecting._connection.cursor_value.executed == ["SELECT 1"]
    for statement in (
        "INSERT INTO public.tenants DEFAULT VALUES",
        "UPDATE public.tenants SET name='x'",
        "DELETE FROM public.tenants",
        "WITH x AS (SELECT 1) INSERT INTO public.tenants DEFAULT VALUES",
    ):
        with pytest.raises(
            runner.Task8V27CensusError,
            match="^(TASK8_V27_CENSUS_UNCLASSIFIED_DML_FORBIDDEN|TASK8_V27_CENSUS_FIXTURE_DML_CTE_UNSUPPORTED)$",
        ):
            rejecting.cursor().execute(statement)


def test_v27_complete_execution_digest_commits_every_central_evidence_section() -> None:
    runner = _sql()
    expected_fields = (
        "case_result_sha256",
        "catalog_projection_sha256",
        "race_result_sha256",
        "rls_behavior_sha256",
        "partial_authority_udu_sha256",
        "erasure_authority_udu_sha256",
        "dashboard_seed_plan_sha256",
        "dashboard_seed_db_projection_sha256",
        "task5_roundtrip_sha256",
        "seed_fk_closure_sha256",
        "seed_native_helper_equality_sha256",
        "source_freeze_sha256",
        "main_dashboard_census_sha256",
        "requirement_matrix_sha256",
        "requirement_predicate_sha256",
    )
    assert expected_fields == runner.COMPLETE_EXECUTION_SHA256_FIELDS
    base = {field: hashlib.sha256(field.encode("ascii")).hexdigest() for field in expected_fields}
    original = runner.complete_execution_sha256(base)
    for field in expected_fields:
        changed = dict(base)
        changed[field] = hashlib.sha256((field + "-changed").encode("ascii")).hexdigest()
        assert runner.complete_execution_sha256(changed) != original, field
    expected_preimage = [
        "task8-v27-census-complete-execution-v2",
        [[field, base[field]] for field in expected_fields],
    ]
    assert original == runner._canonical_sha256(expected_preimage)
    with pytest.raises(
        runner.Task8V27CensusError,
        match="^TASK8_V27_CENSUS_EXECUTION_DIGEST_INVALID$",
    ):
        runner.complete_execution_sha256(
            {key: value for key, value in base.items() if key != "task5_roundtrip_sha256"}
        )
    with pytest.raises(
        runner.Task8V27CensusError,
        match="^TASK8_V27_CENSUS_EXECUTION_DIGEST_INVALID$",
    ):
        runner.complete_execution_sha256({**base, "non_evidence_field": "rejected"})
    producer = inspect.getsource(runner._run_database_execution)
    validator = inspect.getsource(runner.validate_census_receipt)
    for source in (producer, validator):
        assert "complete_execution_sha256(" in source
        assert "_complete_execution_evidence(execution)" in source


def test_v27_fk_manifest_and_actual_catalog_inventory_are_runtime_bound() -> None:
    runner = _sql()
    artifact = json.loads(_artifact_raw())
    manifest = runner.build_seed_fk_closure_manifest(artifact)
    assert manifest["seed_write_relation_count"] == 36
    assert len(manifest["seed_write_relation_tuple"]) == 36
    assert manifest["seed_write_relation_sha256"] == (
        "0ef6453642b8f475ba4b724da27af34a1cf68387e959e00fb01625fa8c58b79b"
    )
    assert manifest["seed_fk_relation_closure_count"] == 37
    assert len(manifest["seed_fk_relation_closure_tuple"]) == 37
    assert manifest["seed_fk_relation_closure_sha256"] == (
        "dfbf294032fb6ffa4ec99d51996c4863a34dd9976c15c2a19be1f417974c475c"
    )
    assert {
        "deterministic_calculations",
        "calc_sanads",
        "methodology_truth_dashboard_rows",
        "methodology_truth_dashboard_root_links",
    } <= set(manifest["seed_write_relation_tuple"])
    assert len(manifest["migration_lf_sha256_tuple"]) == 38
    assert manifest["migration_lf_sha256_tuple"][-3:] == [
        [
            "src/idis/persistence/migrations/versions/0036_durable_methodology_selection.py",
            "b3f69b79b6ffb1a827900670d50f4d2dc3813ef0e40a81604f70b6c756ba8fb5",
        ],
        [
            "src/idis/persistence/migrations/versions/0037_durable_methodology_extraction.py",
            "41faa2cfc3f37ae008aea39d6ad7cd1ce46013266cd747990b84e6b403271794",
        ],
        [
            "src/idis/persistence/migrations/versions/0038_full_strict_v16_receipts_and_edges.py",
            "37ba0627fc9bccc13dc332e1540a74f09d9fb0ef557bc1336e88e6765c3ca4ce",
        ],
    ]
    assert manifest["migration_lf_sha256_tuple_sha256"] == (
        "67d6048ed049b328e0fe08e53f3faed8056ef91f0644af33eb493a32c6ae9813"
    )
    assert manifest["expected_foreign_key_inventory_count"] == 92
    assert manifest["expected_foreign_key_inventory_sha256"] == (
        "655a8654badf793301af4cc08683d22daa5517b5718e540d7d6ad11db494c7c3"
    )
    assert len(manifest["expected_foreign_key_inventory_tuple"]) == 92
    observer = inspect.getsource(runner._observe_seed_fk_closure)
    for required in (
        "closure_manifest",
        "pg_catalog.pg_constraint",
        "conkey",
        "confkey",
        "relation_inventory_sha256",
        "foreign_key_inventory_sha256",
        "foreign_key_inventory_count",
        "confupdtype",
        "confdeltype",
        "confmatchtype",
        "closure_manifest_sha256",
        "session_replication_role",
    ):
        assert required in observer
    execution = inspect.getsource(runner._run_main_dashboard_census)
    assert "build_seed_fk_closure_manifest" in execution
    assert "closure_manifest=" in execution


def test_v27_every_evidence_connection_rejects_replica_role() -> None:
    runner = _sql()

    class Cursor:
        def __init__(self, row: tuple[object, ...]) -> None:
            self.row = row

        def execute(self, _sql: str) -> None:
            return None

        def fetchone(self) -> tuple[object, ...]:
            return self.row

    posture = runner._attest_connection_role_posture(
        Cursor(("app", "app", False, False, "origin")), "app"
    )
    assert posture == {
        "current_user": "app",
        "session_user": "app",
        "rolsuper": False,
        "rolbypassrls": False,
        "session_replication_role": "origin",
    }
    assert len(runner._connection_posture_sha256(posture)) == 64
    with pytest.raises(
        runner.Task8V27CensusError,
        match="^TASK8_V27_CENSUS_ROLE_POSTURE_INVALID$",
    ):
        runner._attest_connection_role_posture(
            Cursor(("app", "app", False, False, "replica")), "app"
        )
    helper = inspect.getsource(runner._attest_connection_role_posture)
    assert "current_setting('session_replication_role')" in helper
    source = SQL_RUNNER.read_text(encoding="utf-8")
    for forbidden in (
        "SET LOCAL session_replication_role",
        "SET session_replication_role",
        "set_config('session_replication_role'",
        "DISABLE TRIGGER",
    ):
        assert forbidden not in source


def test_v27_execution_binds_seed_plan_and_app_observed_closure_receipts() -> None:
    runner = _sql()
    execution = inspect.getsource(runner._run_database_execution)
    validation = inspect.getsource(runner.validate_census_receipt)
    for field in (
        "dashboard_seed_plan_sha256",
        "dashboard_seed_db_projection_sha256",
        "seed_fk_closure_sha256",
        "seed_native_helper_equality_sha256",
        "requirement_matrix_sha256",
        "requirement_legal_case_count",
        "requirement_negative_case_count",
        "requirement_negative_zero_mutation_count",
    ):
        assert field in execution
        assert field in validation


def test_v27_native_and_fk_closure_manifests_are_complete() -> None:
    runner = _sql()
    native = runner.build_seed_native_output_manifest()
    assert set(native["output_kind_tuple"]) == {
        "METHODOLOGY_COVERAGE_IDENTITY",
        "METHODOLOGY_EXTRACTION_TASK",
        "METHODOLOGY_EXTRACTION_RESULT",
        "METHODOLOGY_FACTUAL_OUTPUT",
        "METHODOLOGY_CLAIM_LINK",
        "METHODOLOGY_EVIDENCE_LINK",
        "METHODOLOGY_SANAD_LINK",
        "METHODOLOGY_DEFECT_LINK",
        "METHODOLOGY_CALCULATION",
        "METHODOLOGY_TRUTH_DASHBOARD_ROW",
    }
    closure = runner.build_seed_fk_closure_manifest(json.loads(_artifact_raw()))
    assert closure["session_replication_role_bypass_count"] == 0
    assert closure["expected_fk_violation_count"] == 0
    assert closure["observer_role"] == "application"
    assert {
        "methodology_versions",
        "run_methodology_selections",
        "documents",
        "document_spans",
        "methodology_extraction_attempts",
        "claims",
        "evidence_items",
        "sanads",
        "defects",
        "run_output_objects",
        "run_step_outputs",
    } <= set(closure["seed_fk_relation_closure_tuple"])
    assert closure["seed_write_relation_count"] == 36
    assert closure["seed_fk_relation_closure_count"] == 37
    assert closure["expected_foreign_key_inventory_count"] == 92


def test_v27_every_observation_uses_shared_least_privilege_posture_attestation() -> None:
    runner = _sql()
    source = SQL_RUNNER.read_text(encoding="utf-8")
    helper = inspect.getsource(runner._attest_connection_role_posture)
    assert source.count("SELECT current_user,session_user") == 1
    assert "rolsuper" in helper and "rolbypassrls" in helper
    for function in (
        runner._tenant_deal_projection,
        runner._run_rls_behavior_matrix,
        runner._execute_behavior_case,
        runner._catalog_projection,
        runner._run_lock_races,
        runner._observe_authority_catalog,
        runner._observe_seed_fk_closure,
        runner._run_database_execution,
    ):
        assert "_attest_connection_role_posture" in inspect.getsource(function)


def test_v27_database_lifecycle_drops_only_a_database_created_by_this_run(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    runner = _sql()
    root = _materialize_historical_internal_sources(
        tmp_path / "lifecycle-historical-internal-sources"
    )
    historical_snapshot = runner._capture_migration_source_snapshot(internal_source_root=root)
    assert historical_snapshot.expected_sha256 == EXPECTED_MIGRATION_EXECUTION_SOURCE_SHA256
    events: list[str] = []

    def record_migration_snapshot(*args: object, **kwargs: object) -> object:
        assert args == ()
        assert kwargs == {}
        events.append("CAPTURE")
        return historical_snapshot

    monkeypatch.setattr(runner, "_capture_migration_source_snapshot", record_migration_snapshot)
    monkeypatch.setattr(runner, "_create_disposable_database", lambda *_: events.append("CREATE"))
    monkeypatch.setattr(runner, "_setup_disposable_schema", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(runner, "_run_database_execution", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(runner, "build_v27_dashboard_seed_plan", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(runner, "validate_census_receipt", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(runner, "_drop_disposable_database", lambda *_: events.append("DROP"))
    receipt = runner.run_disposable_postgres_census(
        "postgresql://admin@localhost/base",
        "postgresql://app@localhost/base",
        "postgresql://eraser@localhost/base",
        _artifact_raw(),
        _fixture_raw(),
        PARENT_V21.read_bytes(),
        POLICY_V2.read_bytes(),
        REVIEWER_MAP.read_bytes(),
        b"operator-workbook-held-by-test-double",
    )
    assert events == ["CAPTURE", "CREATE", "DROP"]
    assert receipt["disposable_database_dropped"] is True

    events.clear()
    monkeypatch.setattr(
        runner,
        "_create_disposable_database",
        lambda *_: (_ for _ in ()).throw(RuntimeError("create failed")),
    )
    with pytest.raises(
        runner.Task8V27CensusError,
        match="^TASK8_V27_CENSUS_UNEXPECTED$",
    ) as caught:
        runner.run_disposable_postgres_census(
            "postgresql://admin@localhost/base",
            "postgresql://app@localhost/base",
            "postgresql://eraser@localhost/base",
            _artifact_raw(),
            _fixture_raw(),
            PARENT_V21.read_bytes(),
            POLICY_V2.read_bytes(),
            REVIEWER_MAP.read_bytes(),
            b"operator-workbook-held-by-test-double",
        )
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    assert events == ["CAPTURE"]


_DISPOSABLE_TERMINATE_BACKENDS = """
                SELECT pg_terminate_backend(pid)
                  FROM pg_stat_activity
                 WHERE datname = %s
                   AND pid <> pg_backend_pid()
                """


class _ObservedCursor:
    def __init__(
        self,
        cursor: object,
        statements: list[tuple[str, object]],
    ) -> None:
        self._cursor = cursor
        self._statements = statements

    def execute(self, statement: object, parameters: object = None) -> object:
        self._statements.append((str(statement), parameters))
        if parameters is None:
            return self._cursor.execute(statement)
        return self._cursor.execute(statement, parameters)

    def __enter__(self) -> _ObservedCursor:
        self._cursor.__enter__()
        return self

    def __exit__(self, *exc_info: object) -> object:
        return self._cursor.__exit__(*exc_info)

    def __getattr__(self, name: str) -> object:
        return getattr(self._cursor, name)


class _ObservedConnection:
    """Forward attribute writes so the production proxy can reach psycopg2."""

    def __init__(self, connection: object, statements: list[tuple[str, object]]) -> None:
        object.__setattr__(self, "_connection", connection)
        object.__setattr__(self, "_statements", statements)

    def cursor(self, *args: object, **kwargs: object) -> _ObservedCursor:
        return _ObservedCursor(self._connection.cursor(*args, **kwargs), self._statements)

    def close(self) -> None:
        self._connection.close()

    def __setattr__(self, name: str, value: object) -> None:
        setattr(self._connection, name, value)

    def __getattr__(self, name: str) -> object:
        return getattr(self._connection, name)


def _normalize_admin_database_url(admin_url: str) -> str:
    """Strip a SQLAlchemy driver suffix, matching pg_bootstrap_ci.py.

    The runner ``_replace_database`` contract stays plain ``postgres`` or
    ``postgresql``. A non-postgres scheme is rejected.
    """
    if admin_url.startswith("postgresql+psycopg2://"):
        admin_url = admin_url.replace("postgresql+psycopg2://", "postgresql://", 1)
    if admin_url.startswith("postgresql+psycopg://"):
        admin_url = admin_url.replace("postgresql+psycopg://", "postgresql://", 1)
    scheme, separator, _remainder = admin_url.partition("://")
    if separator != "://" or scheme not in {"postgres", "postgresql"}:
        raise ValueError("admin database URL scheme is not postgres or postgresql")
    return admin_url


def _require_admin_database_url() -> str:
    admin_url = os.environ.get("IDIS_DATABASE_ADMIN_URL", "")
    if admin_url:
        return _normalize_admin_database_url(admin_url)
    message = "Postgres create/drop requires IDIS_DATABASE_ADMIN_URL"
    if os.environ.get("IDIS_REQUIRE_POSTGRES") == "1":
        pytest.fail(f"REQUIRED: {message} (IDIS_REQUIRE_POSTGRES=1)")
    pytest.skip(message)


def test_normalize_admin_database_url_strips_driver_and_rejects_other_schemes() -> None:
    plain = "postgresql://postgres@127.0.0.1:1/postgres"
    assert _normalize_admin_database_url(plain) == plain
    assert (
        _normalize_admin_database_url("postgresql+psycopg2://postgres@127.0.0.1:1/postgres")
        == plain
    )
    assert (
        _normalize_admin_database_url("postgresql+psycopg://postgres@127.0.0.1:1/postgres") == plain
    )
    with pytest.raises(ValueError, match="postgres or postgresql"):
        _normalize_admin_database_url("mysql://postgres@127.0.0.1:1/postgres")


def _database_exists(admin_url: str, database_name: str) -> bool:
    import psycopg2

    connection = psycopg2.connect(admin_url)
    try:
        connection.autocommit = True
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1 FROM pg_database WHERE datname = %s", (database_name,))
            return cursor.fetchone() is not None
    finally:
        connection.close()


def test_v27_unclassified_dml_proxy_delegates_attribute_assignment() -> None:
    runner = _sql()

    class Inner:
        def __init__(self) -> None:
            self.autocommit = False
            self.marker = "inner-read"

    inner = Inner()
    proxy = runner._UnclassifiedDmlRejectingConnection(inner)
    assert proxy._connection is inner
    assert proxy.marker == "inner-read"
    proxy.autocommit = True
    assert inner.autocommit is True
    assert "autocommit" not in vars(proxy)
    assert proxy.autocommit is True
    assert proxy._connection is inner


def test_v27_create_and_drop_disposable_database_against_postgres(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    admin_url = _require_admin_database_url()
    runner = _sql()
    database_name = f"idis_task8_census_{uuid.uuid4().hex[:16]}"
    quoted = f'"{database_name}"'
    statements: list[tuple[str, object]] = []
    connect_raw = runner._connect_raw

    def observe(database_url: str) -> _ObservedConnection:
        return _ObservedConnection(connect_raw(database_url), statements)

    monkeypatch.setattr(runner, "_connect_raw", observe)
    created = False
    try:
        assert _database_exists(admin_url, database_name) is False
        runner._create_disposable_database(admin_url, database_name)
        created = True
        assert _database_exists(admin_url, database_name) is True
        runner._drop_disposable_database(admin_url, database_name)
        created = False
        assert _database_exists(admin_url, database_name) is False
    finally:
        if created:
            runner._drop_disposable_database(admin_url, database_name)
    assert statements == [
        (f"CREATE DATABASE {quoted} TEMPLATE template0", None),
        (_DISPOSABLE_TERMINATE_BACKENDS, (database_name,)),
        (f"DROP DATABASE IF EXISTS {quoted}", None),
    ]


def test_v27_public_runner_sanitizes_exceptions_and_suppresses_bytecode(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = _sql()
    dependency_name = "task8_v27_fresh_bytecode_canary"
    dependency = tmp_path / f"{dependency_name}.py"
    dependency.write_text(
        "import sys\nBYTECODE_SUPPRESSED = sys.dont_write_bytecode\n",
        encoding="utf-8",
    )
    cache = tmp_path / "redirected-cache"
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setattr(sys, "pycache_prefix", str(cache))
    previous = sys.dont_write_bytecode
    sentinel_result = {"result": "PASS"}

    def import_dependency(*_args: object, **_kwargs: object) -> dict[str, str]:
        imported = __import__(dependency_name)
        assert imported.BYTECODE_SUPPRESSED is True
        return sentinel_result

    monkeypatch.setattr(runner, "_run_disposable_postgres_census_impl", import_dependency)
    result = runner.run_disposable_postgres_census(
        "admin",
        "application",
        "erasure",
        b"artifact",
        b"fixture",
        b"parent",
        b"policy",
        b"map",
        b"workbook",
    )
    assert result is sentinel_result
    assert sys.dont_write_bytecode is previous
    assert not list(cache.rglob("*.pyc"))
    sys.modules.pop(dependency_name, None)

    def raise_known(*_args: object, **_kwargs: object) -> None:
        try:
            raise RuntimeError("private-profile-prose")
        except RuntimeError as exc:
            raise runner.Task8V27CensusError("TASK8_V27_CENSUS_POLICY_INVALID") from exc

    monkeypatch.setattr(runner, "_run_disposable_postgres_census_impl", raise_known)
    with pytest.raises(
        runner.Task8V27CensusError,
        match="^TASK8_V27_CENSUS_POLICY_INVALID$",
    ) as known:
        runner.run_disposable_postgres_census(
            "admin", "application", "erasure", b"a", b"f", b"p", b"q", b"m", b"w"
        )
    assert known.value.__cause__ is None
    assert known.value.__context__ is None

    def raise_unknown_well_shaped(*_args: object, **_kwargs: object) -> None:
        raise runner.Task8V27CensusError("TASK8_V27_CENSUS_PRIVATE_PROFILE_PASSWORD")

    monkeypatch.setattr(
        runner,
        "_run_disposable_postgres_census_impl",
        raise_unknown_well_shaped,
    )
    with pytest.raises(
        runner.Task8V27CensusError,
        match="^TASK8_V27_CENSUS_UNEXPECTED$",
    ) as unknown:
        runner.run_disposable_postgres_census(
            "admin", "application", "erasure", b"a", b"f", b"p", b"q", b"m", b"w"
        )
    assert unknown.value.__cause__ is None
    assert unknown.value.__context__ is None

    class StopRun(BaseException):
        pass

    stop = StopRun()

    def raise_base(*_args: object, **_kwargs: object) -> None:
        raise stop

    monkeypatch.setattr(runner, "_run_disposable_postgres_census_impl", raise_base)
    with pytest.raises(StopRun) as base:
        runner.run_disposable_postgres_census(
            "admin", "application", "erasure", b"a", b"f", b"p", b"q", b"m", b"w"
        )
    assert base.value is stop
    assert sys.dont_write_bytecode is previous


def test_v27_runner_import_is_hermetic_and_has_no_profile_or_environment_access(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    monkeypatch.setattr("os.getenv", lambda key, default=None: calls.append(key) or default)
    _sql()
    assert calls == []
    source = SQL_RUNNER.read_text(encoding="utf-8")
    assert "load_strict_runtime_profile" not in source
    assert "os.environ" not in source
    assert "IDIS_STRICT_DOTENV_PATH" not in source


def test_v27_input_digests_are_exact_and_not_self_affirmed() -> None:
    assert hashlib.sha256(_artifact_raw()).hexdigest() == ARTIFACT_SHA256
    assert hashlib.sha256(_fixture_raw()).hexdigest() == FIXTURE_SHA256
    assert hashlib.sha256(PARENT_V21.read_bytes()).hexdigest() == PARENT_ARTIFACT_SHA256
    assert hashlib.sha256(POLICY_V2.read_bytes()).hexdigest() == POLICY_ARTIFACT_SHA256
    assert hashlib.sha256(REVIEWER_MAP.read_bytes()).hexdigest() == REVIEWER_MAP_SHA256
