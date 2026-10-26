"""Source-closure admission for the pre-freeze full-strict-v16 census harness."""

from __future__ import annotations

import _io
import ast
import builtins
import hashlib
import importlib.util
import io
import json
import os
import py_compile
import sys
from collections import Counter
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_RUNNER_PATH = _REPO_ROOT / "scripts" / "run_task7_v16_current_guard_census.py"
_GENERATOR_PATH = _REPO_ROOT / "scripts" / "generate_task7_v16_contract_fixture.py"
_ARTIFACT_PATH = _REPO_ROOT / "contracts" / "full_strict_v16_reviewed_preimage_v1.json"
_CANDIDATE_SHA256 = "dd7b2585ff01909765342549bf60b4ba32d18cec398c8b002a77c55d0f92ce74"
_CANDIDATE_SHA512 = (
    "f002c51ed7ecc9728a98e5f6f23825e235368823fc19a9e41564e90fa2f96925"
    "d9c02f347cc506378da90ee4d4a7ff6bda48184c0a209256d86ea073e408ae2d"
)
_PROSPECTIVE_SHA256 = "0db5c955e02eb580ba4481d6555fca67b9402c31e11fe3359479feb422ae8f35"


def _load(path: Path, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def runner() -> ModuleType:
    return _load(_RUNNER_PATH, "_task7_v16_harness_source_closure_runner")


@pytest.fixture(scope="module")
def generator() -> ModuleType:
    # Independent equivalence oracle only.  The production runner is forbidden
    # from importing or reading this generator.
    return _load(_GENERATOR_PATH, "_task7_v16_harness_source_closure_oracle")


def _manifest_row(
    path: Path,
    *,
    relative_path: str,
    aliases: tuple[str, ...],
    role: str = "test-source",
) -> tuple[str, int, str, str, tuple[str, ...]]:
    raw = path.read_bytes()
    return relative_path, len(raw), hashlib.sha256(raw).hexdigest(), role, aliases


def test_runner_owns_candidate_codec_and_has_no_mutable_helper_dependencies() -> None:
    source = _RUNNER_PATH.read_text(encoding="utf-8")
    assert "generate_task7_v16_contract_fixture" not in source
    # A controlled synthetic compatibility module is allowed for historical
    # imports; a mutable source import or path dependency is not.
    assert "from tests.slice100_task7._postgres_support import" not in source
    assert "_POSTGRES_SUPPORT_PATH" not in source
    assert "def _decode_exact_candidate(" in source
    assert "def _canonical_scalar_json_bytes(" in source
    assert "def _derive_prospective_frozen_bytes(" in source


def test_exact_closure_is_checked_after_census_and_before_receipt_signing() -> None:
    tree = ast.parse(_RUNNER_PATH.read_text(encoding="utf-8"))
    functions = {
        node.name: node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }

    def calls(function_name: str) -> dict[str, list[int]]:
        found: dict[str, list[int]] = {}
        for node in ast.walk(functions[function_name]):
            if not isinstance(node, ast.Call):
                continue
            name = ""
            if isinstance(node.func, ast.Name):
                name = node.func.id
            elif isinstance(node.func, ast.Attribute):
                name = node.func.attr
            if name:
                found.setdefault(name, []).append(node.lineno)
        return found

    census_calls = calls("run_pre_fixture_census")
    census_returns = [
        node.lineno
        for node in ast.walk(functions["run_pre_fixture_census"])
        if isinstance(node, ast.Return)
    ]
    assert max(census_calls["_run_pre_fixture_census_impl"]) < min(
        census_calls["assert_exact_closure"]
    )
    assert max(census_calls["assert_exact_closure"]) < min(census_returns)

    receipt_calls = calls("build_and_verify_signed_receipt")
    assert max(receipt_calls["assert_exact_closure"]) < min(
        receipt_calls["_validate_complete_census_result"]
    )
    assert max(receipt_calls["assert_exact_closure"]) < min(receipt_calls["sign"])


def test_local_candidate_codec_matches_reviewed_oracle(
    runner: ModuleType,
    generator: ModuleType,
) -> None:
    raw = _ARTIFACT_PATH.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == _CANDIDATE_SHA256
    assert hashlib.sha512(raw).hexdigest() == _CANDIDATE_SHA512
    artifact = runner._decode_exact_candidate(raw)
    assert artifact["metadata"]["append_frozen"] is False
    assert runner._canonical_scalar_json_bytes(artifact) == generator._canonical_bytes(artifact)
    prospective = runner._derive_prospective_frozen_bytes(raw)
    assert prospective == generator.derive_prospective_frozen_bytes(raw)
    assert hashlib.sha256(prospective).hexdigest() == _PROSPECTIVE_SHA256


@pytest.mark.parametrize(
    "raw",
    (
        b'{"metadata":{},"metadata":{}}',
        b'{"value":1.0}',
        b'{"value":NaN}',
        b'{"value":"e\\u0301"}',
    ),
)
def test_local_candidate_codec_rejects_duplicate_float_constant_and_non_nfc(
    runner: ModuleType,
    raw: bytes,
) -> None:
    with pytest.raises(
        runner.CensusError,
        match=r"^TASK7_V16_CENSUS_ARTIFACT_BYTES_UNAUTHORIZED$",
    ):
        runner._decode_exact_candidate(raw)
    with pytest.raises(
        runner.CensusError,
        match=r"^TASK7_V16_CENSUS_ARTIFACT_BYTES_UNAUTHORIZED$",
    ):
        runner._decode_integer_only_json(raw)


def test_manifest_is_literal_ordered_and_binds_harness_identity(runner: ModuleType) -> None:
    manifest = runner.HARNESS_SOURCE_MANIFEST
    assert isinstance(manifest, tuple) and manifest
    assert all(isinstance(row, tuple) and len(row) == 5 for row in manifest)
    paths = tuple(row[0] for row in manifest)
    assert paths == tuple(sorted(paths))
    assert len(paths) == len({path.casefold() for path in paths})
    aliases = tuple(alias for row in manifest for alias in row[4])
    assert len(aliases) == len(set(aliases))
    assert all(
        isinstance(row[1], int)
        and row[1] > 0
        and isinstance(row[2], str)
        and len(row[2]) == 64
        and isinstance(row[3], str)
        and row[3]
        and isinstance(row[4], tuple)
        and row[4]
        for row in manifest
    )
    assert runner.HARNESS_SOURCE_CLOSURE_SHA256 in runner.HARNESS_IDENTITY
    assert len(runner._CLOSED_RESULT_FIELDS) == 66
    assert len(manifest) == 21


def test_manifest_names_all_reviewed_historical_and_runtime_helper_families(
    runner: ModuleType,
) -> None:
    paths = {row[0] for row in runner.HARNESS_SOURCE_MANIFEST}
    # Namespace packages have no source bytes; only real package initializers
    # can be literal manifest members.
    expected = {
        "scripts/_task7_v16_census_scaffold.py",
        "scripts/_task7_v11_census_scaffold.py",
        "scripts/_task7_v9_census_scaffold.py",
        "scripts/run_task7_v11_current_guard_census.py",
        "scripts/run_task7_v9_current_guard_census.py",
        "scripts/generate_task7_v11_contract_fixture.py",
        "scripts/generate_task7_v10_contract_fixture.py",
        "scripts/generate_task7_v9_contract_fixture.py",
        "scripts/generate_task7_v7_contract_fixture.py",
        "scripts/generate_task7_v6_contract_fixture.py",
        "scripts/generate_task7_v5_contract_fixture.py",
        "scripts/generate_task7_v4_contract_fixture.py",
        "scripts/materialize_full_strict_v11_preimage.py",
        "scripts/materialize_full_strict_v10_preimage.py",
        "tests/__init__.py",
        "tests/slice100_task2b/__init__.py",
        "tests/slice100_task2b/_support.py",
        "tests/slice100_task7/_void_contract_hash_evidence.py",
        "tests/slice100_task7/test_full_strict_v9_native_schema_red.py",
        "tests/test_run_methodology_deterministic_calculation_service.py",
        "tests/test_run_methodology_validated_evidence_package_service.py",
    }
    assert expected <= paths
    assert paths == expected
    assert "tests/slice100_task7/_postgres_support.py" not in paths
    assert "scripts/run_task7_v16_current_guard_census.py" not in paths


def test_complete_scripts_and_tests_source_roots_are_protected(
    runner: ModuleType,
) -> None:
    store = runner._HARNESS_SOURCE_STORE
    assert store._is_protected("scripts/unknown.py")
    assert store._is_protected("tests/unknown.py")
    assert store._is_protected_module("SCRIPTS.unknown")
    assert store._is_protected_module("TESTS.unknown")


def test_execution_closure_is_literal_counter_not_a_path_set(runner: ModuleType) -> None:
    coordinates = runner.HARNESS_SOURCE_EXECUTION_COORDINATES
    assert isinstance(coordinates, tuple)
    assert len(coordinates) == 32
    counts = Counter(coordinates)
    assert len(counts) < len(coordinates)
    assert (
        counts[
            (
                "scripts/_task7_v9_census_scaffold.py",
                "_task7_v9_census_scaffold_core_for_v11",
                "raw-exact",
                "7ed00e544d3bc92803bd933c1e083829a92d62f6eddad82c594a10fe6a8f645c",
            )
        ]
        == 2
    )
    assert (
        counts[
            (
                "scripts/generate_task7_v10_contract_fixture.py",
                "_task7_v10_fixture_helpers_for_v11",
                "manifest-exact",
                "b26908179dd7c3f4979b4b490a4ddbb925a248e4dde1845bad59ea10670aa13d",
            )
        ]
        == 2
    )
    assert (
        counts[
            (
                "scripts/run_task7_v11_current_guard_census.py",
                "_task7_v11_current_guard_engine_for_v16",
                "adapted-v16",
                "879c0976c233837a9ddd85b90812e632f0a9a6a936b656a5bc3fdd9ad7dbfa1e",
            )
        ]
        == 1
    )
    assert (
        counts[
            (
                "scripts/run_task7_v9_current_guard_census.py",
                "_task7_v11_current_guard_census_engine",
                "adapted-v11-v16",
                "a7a8464e53b024a681b6616cc5660098519c3173e393ba3105e9dea77bf0519f",
            )
        ]
        == 1
    )


def test_store_rejects_member_mutation_missing_alias_and_unused_member(
    runner: ModuleType,
    tmp_path: Path,
) -> None:
    source = tmp_path / "module.py"
    source.write_text("VALUE = 1\n", encoding="utf-8")
    manifest = (
        _manifest_row(
            source,
            relative_path="module.py",
            aliases=("closure_test_module",),
        ),
    )
    store = runner._HarnessSourceStore(tmp_path, manifest, ("",))
    try:
        source.write_text("VALUE = 2\n", encoding="utf-8")
        with pytest.raises(
            runner.CensusError,
            match=r"^TASK7_V16_HARNESS_SOURCE_(?:OPEN|HASH)_INVALID$",
        ):
            store.acquire()
    finally:
        store.close()

    missing = (("missing.py", 1, "0" * 64, "missing", ("closure_missing",)),)
    with pytest.raises(
        runner.CensusError,
        match=r"^TASK7_V16_HARNESS_SOURCE_OPEN_INVALID$",
    ):
        runner._HarnessSourceStore(tmp_path, missing, ("",)).acquire()

    source.write_text("VALUE = 1\n", encoding="utf-8")
    store = runner._HarnessSourceStore(tmp_path, manifest, ("",))
    try:
        store.acquire()
        with pytest.raises(
            runner.CensusError,
            match=r"^TASK7_V16_HARNESS_SOURCE_CLOSURE_INCOMPLETE$",
        ):
            store.assert_exact_closure()
    finally:
        store.close()


def test_exact_counter_rejects_over_execution_replay(
    runner: ModuleType,
    tmp_path: Path,
) -> None:
    source = tmp_path / "replayed.py"
    source.write_text("VALUE = 1\n", encoding="utf-8")
    manifest = (
        _manifest_row(
            source,
            relative_path="replayed.py",
            aliases=("replayed",),
        ),
    )
    store = runner._HarnessSourceStore(tmp_path, manifest, ("replayed.py",))
    try:
        store.acquire()
        held = store._held["replayed.py"]
        store.compile_held(held.path, held.raw, "replayed")
        store.compile_held(held.path, held.raw, "replayed")
        with pytest.raises(
            runner.CensusError,
            match=r"^TASK7_V16_HARNESS_SOURCE_CLOSURE_INCOMPLETE$",
        ):
            store.assert_exact_closure()
    finally:
        store.close()


def test_store_rejects_duplicate_path_alias_and_path_aliasing(
    runner: ModuleType,
    tmp_path: Path,
) -> None:
    source = tmp_path / "module.py"
    source.write_text("VALUE = 1\n", encoding="utf-8")
    row = _manifest_row(
        source,
        relative_path="module.py",
        aliases=("closure_duplicate",),
    )
    duplicate_path = (row, row)
    with pytest.raises(
        runner.CensusError,
        match=r"^TASK7_V16_HARNESS_MANIFEST_INVALID$",
    ):
        runner._HarnessSourceStore(tmp_path, duplicate_path, ("",))

    second = tmp_path / "second.py"
    second.write_text("VALUE = 2\n", encoding="utf-8")
    duplicate_alias = (
        row,
        _manifest_row(
            second,
            relative_path="second.py",
            aliases=("closure_duplicate",),
        ),
    )
    with pytest.raises(
        runner.CensusError,
        match=r"^TASK7_V16_HARNESS_MANIFEST_INVALID$",
    ):
        runner._HarnessSourceStore(tmp_path, duplicate_alias, ("",))

    alias = tmp_path / "alias.py"
    try:
        os.link(source, alias)
    except OSError:
        pytest.skip("hard links are unavailable")
    aliased_paths = tuple(
        sorted(
            (
                row,
                _manifest_row(
                    alias,
                    relative_path="alias.py",
                    aliases=("closure_alias",),
                ),
            ),
            key=lambda item: item[0],
        )
    )
    store = runner._HarnessSourceStore(tmp_path, aliased_paths, ("",))
    try:
        with pytest.raises(
            runner.CensusError,
            match=r"^TASK7_V16_HARNESS_SOURCE_ALIAS_INVALID$",
        ):
            store.acquire()
    finally:
        store.close()


def test_store_rejects_reparse_or_symlink_member(
    runner: ModuleType,
    tmp_path: Path,
) -> None:
    target = tmp_path / "target.py"
    target.write_text("VALUE = 1\n", encoding="utf-8")
    link = tmp_path / "link.py"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("symbolic links are unavailable")
    manifest = (
        _manifest_row(
            target,
            relative_path="link.py",
            aliases=("closure_link",),
        ),
    )
    store = runner._HarnessSourceStore(tmp_path, manifest, ("",))
    try:
        with pytest.raises(
            runner.CensusError,
            match=r"^TASK7_V16_HARNESS_SOURCE_REPARSE_INVALID$",
        ):
            store.acquire()
    finally:
        store.close()


def test_controlled_loader_replaces_injected_alias_and_ignores_stale_pyc(
    runner: ModuleType,
    tmp_path: Path,
) -> None:
    source = tmp_path / "module.py"
    source.write_text("VALUE = 'trusted-source'\n", encoding="utf-8")
    pyc = Path(importlib.util.cache_from_source(str(source)))
    pyc.parent.mkdir(parents=True, exist_ok=True)
    malicious = tmp_path / "malicious.py"
    malicious.write_text("VALUE = 'stale-pyc'\n", encoding="utf-8")
    py_compile.compile(str(malicious), cfile=str(pyc), doraise=True)
    manifest = (
        _manifest_row(
            source,
            relative_path="module.py",
            aliases=("closure_injected",),
        ),
    )
    injected = ModuleType("closure_injected")
    injected.VALUE = "injected"  # type: ignore[attr-defined]
    sys.modules["closure_injected"] = injected
    store = runner._HarnessSourceStore(tmp_path, manifest, ("",))
    try:
        store.acquire()
        with store.activation():
            module = __import__("closure_injected")
            assert module.VALUE == "trusted-source"
        store.assert_exact_closure()
    finally:
        store.close()
        sys.modules.pop("closure_injected", None)


def test_controlled_loader_rejects_unknown_protected_source(
    runner: ModuleType,
    tmp_path: Path,
) -> None:
    known = tmp_path / "known.py"
    known.write_text("VALUE = 1\n", encoding="utf-8")
    unknown = tmp_path / "unknown.py"
    unknown.write_text("VALUE = 2\n", encoding="utf-8")
    manifest = (
        _manifest_row(
            known,
            relative_path="known.py",
            aliases=("closure_known",),
        ),
    )
    store = runner._HarnessSourceStore(tmp_path, manifest, ("",))
    try:
        store.acquire()
        with (
            store.activation(),
            pytest.raises(
                runner.CensusError,
                match=r"^TASK7_V16_HARNESS_SOURCE_UNKNOWN$",
            ),
        ):
            importlib.util.spec_from_file_location("closure_unknown", unknown)
    finally:
        store.close()


def test_controlled_finder_and_open_reject_unknown_protected_sources(
    runner: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    package = tmp_path / "closure_pkg"
    package.mkdir()
    initializer = package / "__init__.py"
    initializer.write_text("PACKAGE = 'trusted'\n", encoding="utf-8")
    known = package / "known.py"
    known.write_text("VALUE = 1\n", encoding="utf-8")
    unknown = package / "unknown.py"
    unknown.write_text("VALUE = 2\n", encoding="utf-8")
    manifest = (
        _manifest_row(
            initializer,
            relative_path="closure_pkg/__init__.py",
            aliases=("closure_pkg",),
            role="package-initializer",
        ),
        _manifest_row(
            known,
            relative_path="closure_pkg/known.py",
            aliases=("closure_pkg.known",),
        ),
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    store = runner._HarnessSourceStore(tmp_path, manifest, ("closure_pkg",))
    try:
        store.acquire()
        with store.activation():
            with pytest.raises(
                runner.CensusError,
                match=r"^TASK7_V16_HARNESS_SOURCE_UNKNOWN$",
            ):
                __import__("closure_pkg.unknown")
            with pytest.raises(
                runner.CensusError,
                match=r"^TASK7_V16_HARNESS_SOURCE_UNKNOWN$",
            ):
                unknown.read_text(encoding="utf-8")
            with (
                pytest.raises(
                    runner.CensusError,
                    match=r"^TASK7_V16_HARNESS_SOURCE_UNKNOWN$",
                ),
                open(unknown, encoding="utf-8"),  # noqa: PTH123
            ):
                pass
    finally:
        store.close()


@pytest.mark.skipif(os.name != "nt", reason="Windows paths are case-insensitive")
def test_windows_mixed_case_unknown_source_cannot_escape_protected_root(
    runner: ModuleType,
    tmp_path: Path,
) -> None:
    package = tmp_path / "pkg"
    package.mkdir()
    known = package / "known.py"
    known.write_text("VALUE = 'trusted'\n", encoding="utf-8")
    unknown = package / "unknown.py"
    unknown.write_text("VALUE = 'evil'\n", encoding="utf-8")
    manifest = (
        _manifest_row(
            known,
            relative_path="pkg/known.py",
            aliases=("pkg.known",),
        ),
    )
    mixed_case_unknown = Path(str(unknown).replace(f"{os.sep}pkg{os.sep}", f"{os.sep}PKG{os.sep}"))
    store = runner._HarnessSourceStore(
        tmp_path,
        manifest,
        ("pkg",),
        synthetic_namespaces={"pkg": "pkg"},
    )
    try:
        store.acquire()
        with store.activation():
            exact = __import__("pkg.known", fromlist=["VALUE"])
            assert exact.VALUE == "trusted"
            with pytest.raises(
                runner.CensusError,
                match=r"^TASK7_V16_HARNESS_SOURCE_UNKNOWN$",
            ):
                spec = importlib.util.spec_from_file_location(
                    "evil_mixed_case",
                    mixed_case_unknown,
                )
                assert spec is not None and spec.loader is not None
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
        store.assert_exact_closure()
    finally:
        store.close()
        sys.modules.pop("evil_mixed_case", None)


@pytest.mark.skipif(os.name != "nt", reason="Win32 namespace path proof")
@pytest.mark.parametrize("namespace", ("extended", "device", "nt", "unsupported"))
def test_windows_namespace_aliases_cannot_escape_source_authority(
    runner: ModuleType,
    tmp_path: Path,
    namespace: str,
) -> None:
    package = tmp_path / "pkg"
    package.mkdir()
    known = package / "known.py"
    known.write_text("VALUE = 'trusted'\n", encoding="utf-8")
    unknown = package / "unknown.py"
    unknown.write_text("SECRET = 2\n", encoding="utf-8")
    if namespace == "extended":
        namespaced_unknown = "\\\\?\\" + str(unknown)
    elif namespace == "device":
        namespaced_unknown = "\\\\.\\" + str(unknown)
    elif namespace == "nt":
        namespaced_unknown = "\\??\\" + str(unknown)
    else:
        namespaced_unknown = "\\\\?\\UNC\\localhost\\idis-unsupported\\unknown.py"
    manifest = (
        _manifest_row(
            known,
            relative_path="pkg/known.py",
            aliases=("pkg.known",),
        ),
    )
    store = runner._HarnessSourceStore(
        tmp_path,
        manifest,
        ("pkg",),
        synthetic_namespaces={"pkg": "pkg"},
    )
    try:
        store.acquire()
        with store.activation():
            exact = __import__("pkg.known", fromlist=["VALUE"])
            assert exact.VALUE == "trusted"
            with pytest.raises(
                runner.CensusError,
                match=r"^TASK7_V16_HARNESS_SOURCE_UNKNOWN$",
            ):
                with open(namespaced_unknown, "rb") as stream:  # noqa: PTH123
                    raw = stream.read()
                namespace_values: dict[str, Any] = {}
                exec(  # noqa: S102 - proves the synthetic-filename bypass is closed
                    compile(raw, "<string>", "exec"),
                    namespace_values,
                )
                assert namespace_values["SECRET"] == 2
        store.assert_exact_closure()
    finally:
        store.close()


@pytest.mark.skipif(os.name != "nt", reason="Windows UNC path proof")
def test_windows_plain_unc_path_fails_closed_before_filesystem_fallback(
    runner: ModuleType,
    tmp_path: Path,
) -> None:
    known = tmp_path / "known.py"
    known.write_text("VALUE = 'trusted'\n", encoding="utf-8")
    manifest = (
        _manifest_row(
            known,
            relative_path="known.py",
            aliases=("known_unc_guard",),
        ),
    )
    store = runner._HarnessSourceStore(tmp_path, manifest, ("",))
    drive, tail = os.path.splitdrive(str(known))
    assert drive.endswith(":")
    unc_alias = Path(f"\\\\localhost\\{drive[0].casefold()}$\\{tail.lstrip(os.sep)}")
    with pytest.raises(
        runner.CensusError,
        match=r"^TASK7_V16_HARNESS_SOURCE_UNKNOWN$",
    ):
        store._relative_for_path(unc_alias)


@pytest.mark.skipif(os.name != "nt", reason="Windows UNC integration proof")
def test_windows_available_unc_alias_cannot_read_protected_source(
    runner: ModuleType,
    tmp_path: Path,
) -> None:
    package = tmp_path / "pkg"
    package.mkdir()
    known = package / "known.py"
    known.write_text("VALUE = 'trusted'\n", encoding="utf-8")
    unknown = package / "unknown.py"
    unknown.write_text("SECRET = 2\n", encoding="utf-8")
    drive, tail = os.path.splitdrive(str(unknown))
    assert drive.endswith(":")
    unc_alias = Path(f"\\\\localhost\\{drive[0].casefold()}$\\{tail.lstrip(os.sep)}")
    if not unc_alias.exists():
        pytest.skip("localhost administrative UNC share is unavailable")
    manifest = (
        _manifest_row(
            known,
            relative_path="pkg/known.py",
            aliases=("pkg.known",),
        ),
    )
    store = runner._HarnessSourceStore(
        tmp_path,
        manifest,
        ("pkg",),
        synthetic_namespaces={"pkg": "pkg"},
    )
    try:
        store.acquire()
        with store.activation():
            exact = __import__("pkg.known", fromlist=["VALUE"])
            assert exact.VALUE == "trusted"
            with pytest.raises(
                runner.CensusError,
                match=r"^TASK7_V16_HARNESS_SOURCE_UNKNOWN$",
            ):
                unc_alias.read_bytes()
        store.assert_exact_closure()
    finally:
        store.close()


def test_external_symlink_into_protected_source_is_not_external_code(
    runner: ModuleType,
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    package = repo / "pkg"
    package.mkdir(parents=True)
    known = package / "known.py"
    known.write_text("VALUE = 'trusted'\n", encoding="utf-8")
    unknown = package / "unknown.py"
    unknown.write_text("SECRET = 2\n", encoding="utf-8")
    outside_alias = tmp_path / "outside_alias.py"
    try:
        outside_alias.symlink_to(unknown)
    except OSError:
        pytest.skip("symbolic links are unavailable")
    manifest = (
        _manifest_row(
            known,
            relative_path="pkg/known.py",
            aliases=("pkg.known",),
        ),
    )
    store = runner._HarnessSourceStore(
        repo,
        manifest,
        ("pkg",),
        synthetic_namespaces={"pkg": "pkg"},
    )
    try:
        store.acquire()
        with store.activation():
            exact = __import__("pkg.known", fromlist=["VALUE"])
            assert exact.VALUE == "trusted"
            with pytest.raises(
                runner.CensusError,
                match=r"^TASK7_V16_HARNESS_SOURCE_UNKNOWN$",
            ):
                with outside_alias.open("rb") as stream:
                    raw = stream.read()
                namespace_values: dict[str, Any] = {}
                exec(  # noqa: S102 - proves the synthetic-filename bypass is closed
                    compile(raw, "<string>", "exec"),
                    namespace_values,
                )
                assert namespace_values["SECRET"] == 2
        store.assert_exact_closure()
    finally:
        store.close()


def test_protected_lexical_path_cannot_resolve_outside_repo(
    runner: ModuleType,
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    package = repo / "pkg"
    package.mkdir(parents=True)
    known = package / "known.py"
    known.write_text("VALUE = 'trusted'\n", encoding="utf-8")
    outside = tmp_path / "outside.py"
    outside.write_text("SECRET = 2\n", encoding="utf-8")
    protected_escape = package / "escape.py"
    try:
        protected_escape.symlink_to(outside)
    except OSError:
        pytest.skip("symbolic links are unavailable")
    manifest = (
        _manifest_row(
            known,
            relative_path="pkg/known.py",
            aliases=("pkg.known",),
        ),
    )
    store = runner._HarnessSourceStore(
        repo,
        manifest,
        ("pkg",),
        synthetic_namespaces={"pkg": "pkg"},
    )
    try:
        store.acquire()
        with store.activation():
            exact = __import__("pkg.known", fromlist=["VALUE"])
            assert exact.VALUE == "trusted"
            with pytest.raises(
                runner.CensusError,
                match=r"^TASK7_V16_HARNESS_SOURCE_REPARSE_INVALID$",
            ):
                protected_escape.read_bytes()
        store.assert_exact_closure()
    finally:
        store.close()


def test_external_hardlink_to_held_member_cannot_replay_outside_counter(
    runner: ModuleType,
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    package = repo / "pkg"
    package.mkdir(parents=True)
    known = package / "known.py"
    known.write_text("VALUE = 'trusted'\n", encoding="utf-8")
    outside_alias = tmp_path / "outside_hardlink.py"
    try:
        os.link(known, outside_alias)
    except OSError:
        pytest.skip("hard links are unavailable")
    manifest = (
        _manifest_row(
            known,
            relative_path="pkg/known.py",
            aliases=("pkg.known",),
        ),
    )
    store = runner._HarnessSourceStore(
        repo,
        manifest,
        ("pkg",),
        synthetic_namespaces={"pkg": "pkg"},
    )
    try:
        store.acquire()
        with store.activation():
            exact = __import__("pkg.known", fromlist=["VALUE"])
            assert exact.VALUE == "trusted"
            with pytest.raises(
                runner.CensusError,
                match=r"^TASK7_V16_HARNESS_SOURCE_ALIAS_INVALID$",
            ):
                with outside_alias.open("rb") as stream:
                    raw = stream.read()
                namespace_values: dict[str, Any] = {}
                exec(  # noqa: S102 - proves the synthetic-filename replay is closed
                    compile(raw, "<string>", "exec"),
                    namespace_values,
                )
                assert namespace_values["VALUE"] == "trusted"
        store.assert_exact_closure()
    finally:
        store.close()


@pytest.mark.parametrize(
    "bypass",
    (
        "os_open",
        "open_code",
        "source_loader",
        "source_get_data",
        "sourceless_loader",
        "sourceless_get_data",
    ),
)
def test_low_level_source_bypasses_are_denied_and_not_hidden_by_exact_closure(
    runner: ModuleType,
    tmp_path: Path,
    bypass: str,
) -> None:
    known = tmp_path / "known.py"
    known.write_text("VALUE = 'trusted'\n", encoding="utf-8")
    unknown = tmp_path / "unknown.py"
    unknown.write_text("VALUE = 'evil-source'\n", encoding="utf-8")
    unknown_pyc = tmp_path / "unknown.pyc"
    py_compile.compile(str(unknown), cfile=str(unknown_pyc), doraise=True)
    manifest = (
        _manifest_row(
            known,
            relative_path="known.py",
            aliases=("known_low_level",),
        ),
    )
    store = runner._HarnessSourceStore(tmp_path, manifest, ("",))
    try:
        store.acquire()
        with store.activation():
            exact = __import__("known_low_level")
            assert exact.VALUE == "trusted"
            with pytest.raises(
                runner.CensusError,
                match=r"^TASK7_V16_HARNESS_SOURCE_UNKNOWN$",
            ):
                if bypass == "os_open":
                    descriptor = os.open(unknown, os.O_RDONLY)
                    try:
                        assert os.read(descriptor, 1)
                    finally:
                        os.close(descriptor)
                elif bypass == "open_code":
                    with _io.open_code(str(unknown)) as stream:
                        assert stream.read(1)
                elif bypass == "source_loader":
                    loader = importlib.machinery.SourceFileLoader(
                        "evil_source_probe",
                        str(unknown),
                    )
                    spec = importlib.util.spec_from_loader(
                        "evil_source_probe",
                        loader,
                    )
                    assert spec is not None
                    module = importlib.util.module_from_spec(spec)
                    loader.exec_module(module)
                    assert module.VALUE == "evil-source"
                elif bypass == "source_get_data":
                    loader = importlib.machinery.SourceFileLoader(
                        "evil_source_data_probe",
                        str(unknown),
                    )
                    assert loader.get_data(str(unknown))
                elif bypass == "sourceless_loader":
                    loader = importlib.machinery.SourcelessFileLoader(
                        "evil_sourceless_probe",
                        str(unknown_pyc),
                    )
                    spec = importlib.util.spec_from_loader(
                        "evil_sourceless_probe",
                        loader,
                    )
                    assert spec is not None
                    module = importlib.util.module_from_spec(spec)
                    loader.exec_module(module)
                    assert module.VALUE == "evil-source"
                else:
                    loader = importlib.machinery.SourcelessFileLoader(
                        "evil_sourceless_data_probe",
                        str(unknown_pyc),
                    )
                    assert loader.get_data(str(unknown_pyc))
        store.assert_exact_closure()
    finally:
        store.close()
        sys.modules.pop("known_low_level", None)
        sys.modules.pop("evil_source_probe", None)
        sys.modules.pop("evil_sourceless_probe", None)


def test_preloaded_unknown_descendant_and_none_alias_are_purged_then_restored(
    runner: ModuleType,
    tmp_path: Path,
) -> None:
    package = tmp_path / "pkg"
    package.mkdir()
    initializer = package / "__init__.py"
    initializer.write_text("PACKAGE = 'trusted'\n", encoding="utf-8")
    known = package / "known.py"
    known.write_text("VALUE = 'trusted-known'\n", encoding="utf-8")
    manifest = (
        _manifest_row(
            initializer,
            relative_path="pkg/__init__.py",
            aliases=("pkg",),
            role="package-initializer",
        ),
        _manifest_row(
            known,
            relative_path="pkg/known.py",
            aliases=("pkg.known",),
        ),
    )
    injected_package = ModuleType("pkg")
    injected_package.__path__ = [str(package)]  # type: ignore[attr-defined]
    injected_unknown = ModuleType("pkg.unknown")
    injected_unknown.VALUE = "cached-evil"  # type: ignore[attr-defined]
    injected_package.known = None  # type: ignore[attr-defined]
    injected_package.unknown = injected_unknown  # type: ignore[attr-defined]
    sys.modules["pkg"] = injected_package
    sys.modules["pkg.known"] = None
    sys.modules["pkg.unknown"] = injected_unknown
    store = runner._HarnessSourceStore(tmp_path, manifest, ("pkg",))
    try:
        store.acquire()
        with store.activation():
            exact = __import__("pkg.known", fromlist=["VALUE"])
            assert exact.VALUE == "trusted-known"
            with pytest.raises(
                runner.CensusError,
                match=r"^TASK7_V16_HARNESS_SOURCE_UNKNOWN$",
            ):
                __import__("pkg.unknown")
        store.assert_exact_closure()
        assert sys.modules["pkg"] is injected_package
        assert "pkg.known" in sys.modules and sys.modules["pkg.known"] is None
        assert sys.modules["pkg.unknown"] is injected_unknown
        assert injected_package.known is None
        assert injected_package.unknown is injected_unknown
    finally:
        store.close()
        sys.modules.pop("pkg.unknown", None)
        sys.modules.pop("pkg.known", None)
        sys.modules.pop("pkg", None)


@pytest.mark.parametrize("fileio_owner", ("_io", "io"))
def test_fileio_cannot_read_and_execute_unknown_protected_source(
    runner: ModuleType,
    tmp_path: Path,
    fileio_owner: str,
) -> None:
    package = tmp_path / "pkg"
    package.mkdir()
    known = package / "known.py"
    known.write_text("VALUE = 'trusted'\n", encoding="utf-8")
    unknown = package / "unknown.py"
    unknown.write_text("SECRET = 2\n", encoding="utf-8")
    manifest = (
        _manifest_row(
            known,
            relative_path="pkg/known.py",
            aliases=("pkg.known",),
        ),
    )
    store = runner._HarnessSourceStore(
        tmp_path,
        manifest,
        ("pkg",),
        synthetic_namespaces={"pkg": "pkg"},
    )
    try:
        store.acquire()
        with store.activation():
            exact = __import__("pkg.known", fromlist=["VALUE"])
            assert exact.VALUE == "trusted"
            file_io = _io.FileIO if fileio_owner == "_io" else io.FileIO
            with pytest.raises(
                runner.CensusError,
                match=r"^TASK7_V16_HARNESS_SOURCE_UNKNOWN$",
            ):
                with file_io(str(unknown), "r") as stream:
                    raw = stream.read()
                namespace: dict[str, Any] = {}
                exec(  # noqa: S102 - proves the synthetic-filename bypass is closed
                    compile(raw, "<string>", "exec"),
                    namespace,
                )
                assert namespace["SECRET"] == 2
        store.assert_exact_closure()
    finally:
        store.close()


def test_adapted_execution_accepts_only_pinned_transformed_digest(
    runner: ModuleType,
    tmp_path: Path,
) -> None:
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    historical = scripts / "run_task7_v11_current_guard_census.py"
    historical.write_text("VALUE = 'historical'\n", encoding="utf-8")
    manifest = (
        _manifest_row(
            historical,
            relative_path="scripts/run_task7_v11_current_guard_census.py",
            aliases=("_task7_v11_current_guard_engine_for_v16",),
            role="raw-adapted-execution",
        ),
    )
    store = runner._HarnessSourceStore(tmp_path, manifest, ("scripts",))
    try:
        store.acquire()
        with pytest.raises(
            runner.CensusError,
            match=r"^TASK7_V16_HARNESS_SOURCE_EXECUTION_INVALID$",
        ):
            store.mark_adapted_execution(historical, "arbitrary executable source")
    finally:
        store.close()


def test_both_adapted_execution_digests_rederive_from_pinned_sources(
    runner: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    v11_path = _REPO_ROOT / "scripts" / "run_task7_v11_current_guard_census.py"
    adapted_v11 = runner._adapt_historical_execution_source(v11_path.read_bytes())
    assert hashlib.sha256(adapted_v11.encode("utf-8")).hexdigest() == (
        "879c0976c233837a9ddd85b90812e632f0a9a6a936b656a5bc3fdd9ad7dbfa1e"
    )

    class _CapturedCompile(RuntimeError):
        pass

    captured: list[str] = []

    def capture_compile(
        source: Any,
        filename: Any,
        mode: str,
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        del filename, mode, args, kwargs
        captured.append(source.decode("utf-8") if isinstance(source, bytes) else source)
        raise _CapturedCompile

    monkeypatch.setattr(builtins, "compile", capture_compile)
    with pytest.raises(_CapturedCompile):
        runner._V11._load_execution_engine()
    assert len(captured) == 1
    assert hashlib.sha256(captured[0].encode("utf-8")).hexdigest() == (
        "a7a8464e53b024a681b6616cc5660098519c3173e393ba3105e9dea77bf0519f"
    )


def test_activation_replaces_and_restores_parent_package_attributes(
    runner: ModuleType,
    tmp_path: Path,
) -> None:
    package = tmp_path / "pkg"
    package.mkdir()
    initializer = package / "__init__.py"
    initializer.write_text("PACKAGE = 'trusted'\n", encoding="utf-8")
    child = package / "child.py"
    child.write_text("VALUE = 'trusted-child'\n", encoding="utf-8")
    manifest = (
        _manifest_row(
            initializer,
            relative_path="pkg/__init__.py",
            aliases=("pkg",),
            role="package-initializer",
        ),
        _manifest_row(
            child,
            relative_path="pkg/child.py",
            aliases=("pkg.child",),
        ),
    )
    injected_package = ModuleType("pkg")
    injected_package.__path__ = [str(package)]  # type: ignore[attr-defined]
    injected_child = ModuleType("pkg.child")
    injected_child.VALUE = "injected"  # type: ignore[attr-defined]
    injected_package.child = injected_child  # type: ignore[attr-defined]
    sys.modules["pkg"] = injected_package
    sys.modules["pkg.child"] = injected_child
    store = runner._HarnessSourceStore(tmp_path, manifest, ("pkg",))
    try:
        store.acquire()
        with store.activation():
            exact_package = __import__("pkg", fromlist=["child"])
            exact_child = __import__("pkg.child", fromlist=["VALUE"])
            assert exact_package is not injected_package
            assert exact_package.child is exact_child
            assert exact_child.VALUE == "trusted-child"
        assert sys.modules["pkg"] is injected_package
        assert sys.modules["pkg.child"] is injected_child
        assert injected_package.child is injected_child
    finally:
        store.close()
        sys.modules.pop("pkg.child", None)
        sys.modules.pop("pkg", None)


def test_manifest_json_preimage_is_canonical_and_repeatable(runner: ModuleType) -> None:
    preimage: Any = [
        [path, byte_length, sha256, role, list(aliases)]
        for path, byte_length, sha256, role, aliases in runner.HARNESS_SOURCE_MANIFEST
    ]
    expected = hashlib.sha256(
        b"task7-v16-harness-source-manifest-v1\x00" + runner._canonical_scalar_json_bytes(preimage)
    ).hexdigest()
    assert expected == runner.HARNESS_SOURCE_MANIFEST_SHA256
    assert json.loads(runner._canonical_scalar_json_bytes(preimage)) == preimage


def test_activation_failure_restores_import_hooks_modules_and_parent_attributes(
    runner: ModuleType,
    tmp_path: Path,
) -> None:
    package = tmp_path / "failure_pkg"
    package.mkdir()
    initializer = package / "__init__.py"
    initializer.write_text("PACKAGE = 'trusted'\n", encoding="utf-8")
    child = package / "child.py"
    child.write_text("raise RuntimeError('expected fixture failure')\n", encoding="utf-8")
    manifest = (
        _manifest_row(
            initializer,
            relative_path="failure_pkg/__init__.py",
            aliases=("failure_pkg",),
            role="package-initializer",
        ),
        _manifest_row(
            child,
            relative_path="failure_pkg/child.py",
            aliases=("failure_pkg.child",),
        ),
    )
    injected_package = ModuleType("failure_pkg")
    injected_package.__path__ = [str(package)]  # type: ignore[attr-defined]
    injected_child = ModuleType("failure_pkg.child")
    injected_package.child = injected_child  # type: ignore[attr-defined]
    sys.modules["failure_pkg"] = injected_package
    sys.modules["failure_pkg.child"] = injected_child
    original_spec_loader = importlib.util.spec_from_file_location
    original_path_reader = Path.read_bytes
    original_os_open = os.open
    original_open_code = _io.open_code
    original_raw_file_io = _io.FileIO
    original_io_file_io = io.FileIO
    original_sourceless_get_code = importlib.machinery.SourcelessFileLoader.get_code
    original_sourceless_get_data = importlib.machinery.SourcelessFileLoader.get_data
    store = runner._HarnessSourceStore(tmp_path, manifest, ("failure_pkg",))
    try:
        store.acquire()
        with (
            pytest.raises(RuntimeError, match="expected fixture failure"),
            store.activation(),
        ):
            __import__("failure_pkg.child")
        assert importlib.util.spec_from_file_location is original_spec_loader
        assert Path.read_bytes is original_path_reader
        assert os.open is original_os_open
        assert _io.open_code is original_open_code
        assert _io.FileIO is original_raw_file_io
        assert io.FileIO is original_io_file_io
        assert importlib.machinery.SourcelessFileLoader.get_code is original_sourceless_get_code
        assert importlib.machinery.SourcelessFileLoader.get_data is original_sourceless_get_data
        assert sys.modules["failure_pkg"] is injected_package
        assert sys.modules["failure_pkg.child"] is injected_child
        assert injected_package.child is injected_child
    finally:
        store.close()
        sys.modules.pop("failure_pkg.child", None)
        sys.modules.pop("failure_pkg", None)


def test_close_releases_handle_and_removes_controlled_alias(
    runner: ModuleType,
    tmp_path: Path,
) -> None:
    source = tmp_path / "close_module.py"
    source.write_text("VALUE = 'trusted'\n", encoding="utf-8")
    manifest = (
        _manifest_row(
            source,
            relative_path="close_module.py",
            aliases=("close_module",),
        ),
    )
    store = runner._HarnessSourceStore(
        tmp_path,
        manifest,
        ("close_module.py",),
        restore_preexisting=False,
    )
    store.acquire()
    with store.activation():
        module = __import__("close_module")
        assert module.VALUE == "trusted"
    assert sys.modules["close_module"] is module
    if os.name == "nt":
        with pytest.raises(PermissionError):
            source.write_text("VALUE = 'blocked'\n", encoding="utf-8")
        with pytest.raises(PermissionError):
            source.rename(tmp_path / "renamed_module.py")
        with pytest.raises(PermissionError):
            source.unlink()
    store.close()
    assert "close_module" not in sys.modules
    source.write_text("VALUE = 'released'\n", encoding="utf-8")
    assert source.read_text(encoding="utf-8") == "VALUE = 'released'\n"


def test_posix_reverify_rejects_append_when_prefix_bytes_are_unchanged(
    runner: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "append_module.py"
    raw = b"VALUE = 1\n"
    source.write_bytes(raw)
    manifest = (
        _manifest_row(
            source,
            relative_path="append_module.py",
            aliases=("append_module",),
        ),
    )
    store = runner._HarnessSourceStore(tmp_path, manifest, ("append_module.py",))
    descriptor = os.open(source, os.O_RDONLY)
    opened = os.fstat(descriptor)
    authority = store._manifest[0]
    store._held[authority.relative_path] = runner._HeldSource(
        authority=authority,
        path=source,
        raw=raw,
        handle=descriptor,
        file_identity=(int(opened.st_dev), int(opened.st_ino)),
    )
    try:
        with source.open("ab") as stream:
            stream.write(b"# unauthorized append\n")
        monkeypatch.setattr(runner.os, "name", "posix")
        monkeypatch.setattr(store, "_reread_posix_handle", lambda _held: raw)
        with pytest.raises(
            runner.CensusError,
            match=r"^TASK7_V16_HARNESS_SOURCE_REVERIFY_INVALID$",
        ):
            store._verify_held_sources()
    finally:
        os.close(descriptor)
        store._held.clear()


@pytest.mark.skipif(os.name == "nt", reason="POSIX advisory-lock mutation proof")
def test_posix_closure_rechecks_fd_and_path_hash(
    runner: ModuleType,
    tmp_path: Path,
) -> None:
    source = tmp_path / "posix_module.py"
    source.write_text("VALUE = 1\n", encoding="utf-8")
    manifest = (
        _manifest_row(
            source,
            relative_path="posix_module.py",
            aliases=("posix_module",),
        ),
    )
    store = runner._HarnessSourceStore(tmp_path, manifest, ("posix_module.py",))
    try:
        store.acquire()
        with store.activation():
            __import__("posix_module")
        source.write_text("VALUE = 2\n", encoding="utf-8")
        with pytest.raises(
            runner.CensusError,
            match=r"^TASK7_V16_HARNESS_SOURCE_REVERIFY_INVALID$",
        ):
            store.assert_exact_closure()
    finally:
        store.close()


@pytest.mark.skipif(os.name == "nt", reason="POSIX advisory-lock append proof")
def test_posix_closure_rejects_noncooperating_append(
    runner: ModuleType,
    tmp_path: Path,
) -> None:
    source = tmp_path / "posix_append_module.py"
    source.write_text("VALUE = 1\n", encoding="utf-8")
    manifest = (
        _manifest_row(
            source,
            relative_path="posix_append_module.py",
            aliases=("posix_append_module",),
        ),
    )
    store = runner._HarnessSourceStore(tmp_path, manifest, ("posix_append_module.py",))
    try:
        store.acquire()
        with store.activation():
            __import__("posix_append_module")
        with source.open("ab") as stream:
            stream.write(b"# unauthorized append\n")
        with pytest.raises(
            runner.CensusError,
            match=r"^TASK7_V16_HARNESS_SOURCE_REVERIFY_INVALID$",
        ):
            store.assert_exact_closure()
    finally:
        store.close()
