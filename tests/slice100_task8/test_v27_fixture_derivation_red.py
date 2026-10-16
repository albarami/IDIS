"""RED-first staging-only fixture generation for full-strict-v27."""

from __future__ import annotations

import ast
import copy
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
GENERATOR = REPO_ROOT / "scripts" / "generate_task8_v27_contract_fixtures.py"
VERIFIER = REPO_ROOT / "scripts" / "verify_full_strict_v27_fixture_independently.py"
MATERIALIZER = REPO_ROOT / "scripts" / "materialize_full_strict_v27_preimage.py"
PARENT = REPO_ROOT / "contracts" / "full_strict_v21_reviewed_preimage_v1.json"
POLICY = REPO_ROOT / "contracts" / "fdd_formula_policy_v2.json"
REVIEWER_MAP = (
    REPO_ROOT / "docs" / "plans" / "2026-07-25-slice100-task8-fdd-formula-policy-v2-review-map.md"
)
POLICY_FIXTURE = (
    REPO_ROOT / "tests" / "slice100_task8" / "fixtures" / "fdd_formula_policy_v2_hash_v1.json"
)
STAGED_ARTIFACT = (
    REPO_ROOT
    / ".local_reports"
    / "slice100"
    / "task8"
    / "v27-candidate"
    / "full_strict_v27_reviewed_preimage_v1.json"
)
STAGED_FIXTURE = STAGED_ARTIFACT.with_name("full_strict_v27_contract_hash_v1.json")
CANONICAL_ARTIFACT = REPO_ROOT / "contracts" / "full_strict_v27_reviewed_preimage_v1.json"
CANONICAL_FIXTURE = (
    REPO_ROOT / "tests" / "slice100_task8" / "fixtures" / "full_strict_v27_contract_hash_v1.json"
)

EXPECTED_FIXTURE_SIZE = 310_306
EXPECTED_IDENTITIES = {
    "artifact_sha256": "4169371675ce32c2b4b459528dcdf21914887941d952648597bce59e49a8ac06",
    "fixture_sha256": "78a3aedb04260cc56e98a2a9193ce489ca73bd4b5b95fc1486f676bfbd07b0cf",
    "contract_hash": "5221e622a40c44b4f2fe38e53e0208a7e1dba5358278d75624a0cc1c40ef0985",
    "graph_sha256": "a951b96575f5c2927c59185f20378fde6ce019c207b77510a1033f4947649e3c",
    "conservation_sha256": "32a06ca41841c196ae76ad3d33b3d15da4dec8a247343b9a629390da8128475d",
    "policy_binding_sha256": "9e41ea12908a5c37542f6156d4bbf22dc19d59ac172d096a84fc55c4db83d2ff",
}
EXPECTED_POLICY_FIXTURE_SHA256 = "b95c426da57a64336da990002201f4d5fdfe616dbae095a93e3aaf9372cf242e"


def _load_module(path: Path, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _generator() -> ModuleType:
    return _load_module(GENERATOR, "_task8_v27_fixture_generator_red")


def _verifier() -> ModuleType:
    return _load_module(VERIFIER, "_task8_v27_fixture_generator_verifier")


def _inputs() -> dict[str, bytes]:
    return {
        "parent_artifact_raw": PARENT.read_bytes(),
        "policy_artifact_raw": POLICY.read_bytes(),
        "reviewer_map_raw": REVIEWER_MAP.read_bytes(),
        "v27_artifact_raw": STAGED_ARTIFACT.read_bytes(),
    }


def _derive(module: ModuleType | None = None) -> tuple[dict[str, Any], dict[str, str]]:
    target = _generator() if module is None else module
    fixture, identities = target.derive_task8_v27_fixture(**_inputs())
    assert isinstance(fixture, dict)
    assert isinstance(identities, dict)
    return fixture, identities


def _render(value: Mapping[str, Any]) -> bytes:
    return (json.dumps(dict(value), ensure_ascii=False, allow_nan=False, indent=2) + "\n").encode(
        "utf-8"
    )


def _safe_snapshot(path: Path) -> tuple[bool, int, str | None]:
    if not path.exists():
        return False, 0, None
    raw = path.read_bytes()
    return True, len(raw), hashlib.sha256(raw).hexdigest()


def _cli_args(*extra: str) -> list[str]:
    return [
        sys.executable,
        str(GENERATOR),
        "--parent-artifact",
        str(PARENT),
        "--policy-artifact",
        str(POLICY),
        "--reviewer-map",
        str(REVIEWER_MAP),
        "--staged-artifact",
        str(STAGED_ARTIFACT),
        *extra,
    ]


def _run(*extra: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        _cli_args(*extra),
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=120,
    )


def _main_args(*extra: str) -> list[str]:
    args = _cli_args(*extra)
    return args[args.index("--parent-artifact") :]


def _safe_json(text: str, code: str) -> dict[str, Any]:
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        raise AssertionError(f"{code}:{len(text)}:{digest}") from exc
    if not isinstance(value, dict):
        raise AssertionError(code)
    return value


def test_v27_generator_exists_and_sources_are_lf_pinned() -> None:
    assert GENERATOR.is_file(), "TASK8_V27_FIXTURE_GENERATOR_NOT_IMPLEMENTED"
    relative_paths = (
        "scripts/generate_task8_v27_contract_fixtures.py",
        "tests/slice100_task8/test_v27_fixture_derivation_red.py",
    )
    attributes = (REPO_ROOT / ".gitattributes").read_text(encoding="utf-8").splitlines()
    for relative_path in relative_paths:
        assert f"{relative_path} text eol=lf" in attributes
        assert b"\r\n" not in (REPO_ROOT / relative_path).read_bytes()


def test_generator_source_is_v27_only_and_calls_both_validation_paths() -> None:
    source = GENERATOR.read_text(encoding="utf-8")
    tree = ast.parse(source)
    string_literals = {
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }
    assert "verify_full_strict_v27_fixture_independently.py" in string_literals
    assert "materialize_full_strict_v27_preimage.py" in string_literals
    assert "full-strict-v27" in string_literals
    retired_markers = tuple(f"full-strict-v{version}" for version in range(22, 27))
    assert all(marker not in source for marker in retired_markers)

    calls = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    assert {"validate_frozen_artifact", "derive_fixture", "verify_fixture"} <= calls


def test_derive_pins_exact_fixture_bytes_and_all_six_outward_identities() -> None:
    fixture, identities = _derive()
    independent = _verifier()
    expected_raw = independent.derive_fixture_bytes(**_inputs())

    assert _render(fixture) == expected_raw
    assert len(expected_raw) == EXPECTED_FIXTURE_SIZE
    assert identities == EXPECTED_IDENTITIES
    assert hashlib.sha256(expected_raw).hexdigest() == EXPECTED_IDENTITIES["fixture_sha256"]
    assert fixture["artifact_sha256"] == EXPECTED_IDENTITIES["artifact_sha256"]
    assert fixture["FULL_STRICT_V27_CONTRACT_HASH"] == EXPECTED_IDENTITIES["contract_hash"]
    assert (
        fixture["identity_hash_dependency_graph"]["graph_sha256"]
        == EXPECTED_IDENTITIES["graph_sha256"]
    )
    assert (
        fixture["approved_semantics_conservation"]["sha256"]
        == EXPECTED_IDENTITIES["conservation_sha256"]
    )
    assert (
        fixture["policy_binding"]["binding_sha256"] == EXPECTED_IDENTITIES["policy_binding_sha256"]
    )
    assert fixture["activation_status"] == "INACTIVE"


def test_generator_validates_installed_and_physical_count_domains() -> None:
    fixture, _identities = _derive()
    assert fixture["counts"]["installed_tenant_rls_table_count"] == 66
    graph_counts = fixture["identity_hash_dependency_graph"]["counts"]
    assert graph_counts["node_count"] == graph_counts["source_pointer_count"] == 67

    module = _generator()
    materializer = module._load_admission_materializer()
    values = _inputs()
    artifact = materializer.validate_frozen_artifact(
        values["v27_artifact_raw"],
        parent_artifact_raw=values["parent_artifact_raw"],
        policy_artifact_raw=values["policy_artifact_raw"],
        reviewer_map_raw=values["reviewer_map_raw"],
    )
    partition = artifact["task8_fdd_contract"]["installed_partition"]
    assert partition["prior_tenant_rls_table_count"] == 64
    assert partition["successor_tenant_rls_table_count"] == 66
    assert artifact["activation_assertions"]["installed_tenant_rls_table_count"] == 66
    assert artifact["activation_assertions"]["baseline_physical_table_count"] == 67
    assert artifact["erasure_baseline"]["physical_table_count"] == 67


@pytest.mark.parametrize(
    ("section", "field"),
    (
        ("installed_partition", "prior_tenant_rls_table_count"),
        ("installed_partition", "successor_tenant_rls_table_count"),
        ("activation_assertions", "installed_tenant_rls_table_count"),
        ("activation_assertions", "baseline_physical_table_count"),
        ("erasure_baseline", "physical_table_count"),
    ),
)
def test_generator_rejects_admission_count_evidence_drift(
    section: str,
    field: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _generator()
    real_materializer = module._load_admission_materializer()
    values = _inputs()
    admitted = real_materializer.validate_frozen_artifact(
        values["v27_artifact_raw"],
        parent_artifact_raw=values["parent_artifact_raw"],
        policy_artifact_raw=values["policy_artifact_raw"],
        reviewer_map_raw=values["reviewer_map_raw"],
    )
    admitted = copy.deepcopy(admitted)
    target = (
        admitted["task8_fdd_contract"][section]
        if section == "installed_partition"
        else admitted[section]
    )
    target[field] += 1
    monkeypatch.setattr(
        module,
        "_load_admission_materializer",
        lambda: SimpleNamespace(validate_frozen_artifact=lambda *_args, **_kwargs: admitted),
    )

    with pytest.raises(
        module.Task8V27FixtureError,
        match="^TASK8_V27_INSTALLED_RLS_COUNT_DISTINCTION_INVALID$",
    ):
        module.derive_task8_v27_fixture(**values)


def test_derivation_is_repeatable_and_writes_nothing() -> None:
    before = tuple(
        _safe_snapshot(path) for path in (STAGED_FIXTURE, CANONICAL_ARTIFACT, CANONICAL_FIXTURE)
    )
    first = _derive()
    second = _derive()
    after = tuple(
        _safe_snapshot(path) for path in (STAGED_FIXTURE, CANONICAL_ARTIFACT, CANONICAL_FIXTURE)
    )

    assert first == second
    assert after == before


def test_exact_policy_fixture_is_an_immutable_prerequisite(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _generator()
    prerequisite = tmp_path / "policy-fixture.json"
    prerequisite.write_bytes(POLICY_FIXTURE.read_bytes() + b" ")
    monkeypatch.setattr(module, "_POLICY_FIXTURE_PATH", prerequisite)

    with pytest.raises(
        module.Task8V27FixtureError,
        match="^TASK8_V27_POLICY_FIXTURE_PREREQUISITE_INVALID$",
    ):
        _derive(module)

    assert hashlib.sha256(POLICY_FIXTURE.read_bytes()).hexdigest() == (
        EXPECTED_POLICY_FIXTURE_SHA256
    )


@pytest.mark.parametrize(
    ("field", "code"),
    (
        ("parent_artifact_raw", "TASK8_V27_PARENT_MUST_BE_FROZEN_V21"),
        ("policy_artifact_raw", "TASK8_V27_POLICY_MUST_BE_FROZEN_V2"),
        ("reviewer_map_raw", "TASK8_V27_REVIEWER_MAP_BYTES_DRIFT"),
        ("v27_artifact_raw", "TASK8_V27_CONTRACT_ARTIFACT_INVALID"),
    ),
)
def test_each_frozen_input_rejects_mutated_bytes_with_a_stable_code(field: str, code: str) -> None:
    module = _generator()
    values = _inputs()
    values[field] += b" "
    with pytest.raises(module.Task8V27FixtureError, match=f"^{code}$"):
        module.derive_task8_v27_fixture(**values)


def test_derive_only_cli_reports_six_identities_and_writes_nothing() -> None:
    before = tuple(
        _safe_snapshot(path) for path in (STAGED_FIXTURE, CANONICAL_ARTIFACT, CANONICAL_FIXTURE)
    )
    result = _run("--derive-only")
    after = tuple(
        _safe_snapshot(path) for path in (STAGED_FIXTURE, CANONICAL_ARTIFACT, CANONICAL_FIXTURE)
    )

    assert result.returncode == 0
    assert result.stderr == ""
    payload = _safe_json(result.stdout, "TASK8_V27_DERIVE_ONLY_OUTPUT_INVALID")
    assert payload == {
        "activation_status": "INACTIVE",
        **EXPECTED_IDENTITIES,
        "result": "DERIVED",
        "verification_path": "TASK8_V27_PRODUCTION_PLUS_INDEPENDENT_V1",
    }
    assert after == before


def test_optional_staging_write_is_exact_exclusive_and_noncanonical(tmp_path: Path) -> None:
    output = tmp_path / "staging" / "fixture.json"
    policy_before = _safe_snapshot(POLICY_FIXTURE)
    canonical_before = (_safe_snapshot(CANONICAL_ARTIFACT), _safe_snapshot(CANONICAL_FIXTURE))

    result = _run("--staging-output", str(output))
    assert result.returncode == 0
    fixture, _identities = _derive()
    assert output.read_bytes() == _render(fixture)
    assert _safe_snapshot(POLICY_FIXTURE) == policy_before
    assert (_safe_snapshot(CANONICAL_ARTIFACT), _safe_snapshot(CANONICAL_FIXTURE)) == (
        canonical_before
    )

    repeated = _run("--staging-output", str(output))
    assert repeated.returncode == 2
    assert _safe_json(repeated.stdout, "TASK8_V27_NO_CLOBBER_OUTPUT_INVALID") == {
        "reason_code": "TASK8_V27_FIXTURE_TARGET_EXISTS",
        "result": "FAILED",
    }
    assert output.read_bytes() == _render(fixture)


@pytest.mark.parametrize("forbidden", (CANONICAL_ARTIFACT, CANONICAL_FIXTURE))
def test_staging_write_refuses_every_canonical_commit_path(forbidden: Path) -> None:
    before = _safe_snapshot(forbidden)
    result = _run("--staging-output", str(forbidden))
    assert result.returncode == 2
    assert _safe_json(result.stdout, "TASK8_V27_CANONICAL_OUTPUT_INVALID") == {
        "reason_code": "TASK8_V27_CANONICAL_PUBLICATION_PATH_FORBIDDEN",
        "result": "FAILED",
    }
    assert _safe_snapshot(forbidden) == before


def test_canonical_artifact_is_forbidden_as_generator_input(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    canonical_input = tmp_path / "canonical-path-copy.json"
    canonical_input.write_bytes(STAGED_ARTIFACT.read_bytes())
    args = _cli_args("--derive-only")
    artifact_index = args.index("--staged-artifact") + 1
    args[artifact_index] = str(canonical_input)

    module = _generator()
    original = module.__dict__["_CANONICAL_ARTIFACT_PATH"]
    module.__dict__["_CANONICAL_ARTIFACT_PATH"] = canonical_input
    try:
        assert module.main(args[args.index("--parent-artifact") :]) == 2
    finally:
        module.__dict__["_CANONICAL_ARTIFACT_PATH"] = original
    captured = capsys.readouterr()
    assert captured.err == ""
    assert _safe_json(captured.out, "TASK8_V27_CANONICAL_INPUT_OUTPUT_INVALID") == {
        "reason_code": "TASK8_V27_CANONICAL_ARTIFACT_INPUT_FORBIDDEN",
        "result": "FAILED",
    }


def test_main_admits_and_reads_artifact_through_the_same_held_handle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = _generator()
    canonical = tmp_path / "canonical-artifact.json"
    artifact = tmp_path / "noncanonical-artifact.json"
    canary = "TASK8_V27_PRIVATE_OPEN_SWAP_CANARY"
    canonical.write_bytes(STAGED_ARTIFACT.read_bytes())
    artifact.write_text(json.dumps({"canary": canary}) + "\n", encoding="utf-8")
    monkeypatch.setattr(module, "_CANONICAL_ARTIFACT_PATH", canonical)
    monkeypatch.setattr(module, "_CANONICAL_FIXTURE_PATH", tmp_path / "canonical-fixture.json")

    real_open = Path.open
    real_stat = Path.stat
    artifact_coordinate = os.path.normcase(os.path.abspath(os.fspath(artifact)))
    swap_observed = False
    canonical_metadata_pending = False

    def open_canonical_during_artifact_read(
        path: Path,
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        nonlocal canonical_metadata_pending, swap_observed
        mode = args[0] if args else kwargs.get("mode", "r")
        coordinate = os.path.normcase(os.path.abspath(os.fspath(path)))
        if coordinate == artifact_coordinate and mode == "rb":
            swap_observed = True
            canonical_metadata_pending = True
            return real_open(canonical, *args, **kwargs)
        return real_open(path, *args, **kwargs)

    def canonical_metadata_during_artifact_read(
        path: Path,
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        nonlocal canonical_metadata_pending
        coordinate = os.path.normcase(os.path.abspath(os.fspath(path)))
        if canonical_metadata_pending and coordinate == artifact_coordinate:
            canonical_metadata_pending = False
            return real_stat(canonical, *args, **kwargs)
        return real_stat(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", open_canonical_during_artifact_read)
    monkeypatch.setattr(Path, "stat", canonical_metadata_during_artifact_read)
    args = _main_args("--derive-only")
    args[args.index("--staged-artifact") + 1] = str(artifact)
    assert module.main(args) == 2
    captured = capsys.readouterr()
    assert swap_observed
    assert captured.err == ""
    assert _safe_json(captured.out, "TASK8_V27_OPEN_SWAP_OUTPUT_INVALID") == {
        "reason_code": "TASK8_V27_CANONICAL_ARTIFACT_INPUT_FORBIDDEN",
        "result": "FAILED",
    }
    assert canary not in captured.out and str(tmp_path) not in captured.out


def test_exact_noncanonical_v27_fixture_is_staged_no_clobber() -> None:
    assert STAGED_FIXTURE.is_file(), "TASK8_V27_STAGED_FIXTURE_NOT_GENERATED"
    before = _safe_snapshot(STAGED_FIXTURE)
    expected_raw = _verifier().derive_fixture_bytes(**_inputs())
    assert STAGED_FIXTURE.read_bytes() == expected_raw
    assert before == (
        True,
        EXPECTED_FIXTURE_SIZE,
        EXPECTED_IDENTITIES["fixture_sha256"],
    )

    repeated = _run("--staging-output", str(STAGED_FIXTURE))
    assert repeated.returncode == 2
    assert _safe_json(repeated.stdout, "TASK8_V27_ASSIGNED_NO_CLOBBER_OUTPUT_INVALID") == {
        "reason_code": "TASK8_V27_FIXTURE_TARGET_EXISTS",
        "result": "FAILED",
    }
    assert _safe_snapshot(STAGED_FIXTURE) == before


def test_canonical_v27_paths_remain_absent_before_publication() -> None:
    assert not CANONICAL_ARTIFACT.exists()
    assert not CANONICAL_FIXTURE.exists()
