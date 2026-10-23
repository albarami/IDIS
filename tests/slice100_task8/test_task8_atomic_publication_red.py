"""RED-first v24 atomic publication contract for the Task-8.1 gate."""

from __future__ import annotations

import ast
import hashlib
import importlib.util
import inspect
import json
import subprocess
import sys
import time
import traceback
from pathlib import Path
from typing import Any

import pytest

from . import _v24_structural_oracle as oracle
from ._artifact_oracle import OPERATOR_WORKBOOK

_GATE_FILES = (
    "fdd_formula_policy_fixture",
    "full_strict_v24_fixture",
    "fdd_formula_policy_artifact",
    "full_strict_v24_artifact",
)
_PREEXISTING = (
    "fdd_formula_policy_fixture",
    "fdd_formula_policy_artifact",
    "fdd_formula_policy_review_map",
)
_CREATED_ORDER = (
    "full_strict_v24_fixture",
    "full_strict_v24_artifact",
)
_FIXTURE_GENERATOR = oracle.REPO_ROOT / "scripts" / "generate_task8_contract_fixtures.py"
_GATE_PUBLISHER = oracle.REPO_ROOT / "scripts" / "publish_task8_contract_artifact_gate.py"
_POLICY_FIXTURE = (
    oracle.REPO_ROOT
    / "tests"
    / "slice100_task8"
    / "fixtures"
    / "fdd_formula_policy_v2_hash_v1.json"
)
_POLICY_REVIEW_MAP = (
    oracle.REPO_ROOT
    / "docs"
    / "plans"
    / "2026-07-25-slice100-task8-fdd-formula-policy-v2-review-map.md"
)
_ARCHIVED_V22_FIXTURE = (
    oracle.REPO_ROOT
    / ".local_reports"
    / "slice100"
    / "task8"
    / "v22-current-output-extension-regeneration-002"
    / "full_strict_v22_contract_hash_v1.json"
)
_CACHED_ASSETS: tuple[Any, dict[str, bytes]] | None = None
_ISOLATED_PYTHON = getattr(sys, "_base_executable", sys.executable)


def _render(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2) + "\n").encode("utf-8")


def _load_module(path: Path, name: str, code: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise AssertionError(code)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _production_v24_raw() -> bytes:
    materializer = _load_module(
        oracle.V24_MATERIALIZER,
        "_task8_atomic_gate_v24_materializer_red",
        "TASK8_V24_MATERIALIZER_NOT_IMPLEMENTED",
    )
    frozen = oracle.V24_ARTIFACT.read_bytes()
    materializer.validate_frozen_artifact(
        frozen,
        parent_artifact_raw=oracle.V21_ARTIFACT.read_bytes(),
        policy_artifact_raw=oracle.POLICY_V2_ARTIFACT.read_bytes(),
        reviewer_map_raw=_POLICY_REVIEW_MAP.read_bytes(),
    )
    assert json.loads(frozen)["metadata"]["append_frozen"] is True
    return frozen


def _assets() -> tuple[Any, dict[str, bytes]]:
    global _CACHED_ASSETS
    if _CACHED_ASSETS is not None:
        publisher, inputs = _CACHED_ASSETS
        return publisher, dict(inputs)
    policy_raw = oracle.POLICY_V2_ARTIFACT.read_bytes()
    v24_raw = _production_v24_raw()
    generator = _load_module(
        _FIXTURE_GENERATOR,
        "_task8_atomic_gate_fixture_generator_red",
        "TASK8_FIXTURE_GENERATOR_NOT_IMPLEMENTED",
    )
    policy_fixture, v24_fixture = generator.derive_task8_fixtures(
        policy_artifact_raw=policy_raw,
        v24_artifact_raw=v24_raw,
    )
    publisher = _load_module(
        _GATE_PUBLISHER,
        "_task8_atomic_gate_publisher_red",
        "TASK8_GATE_PUBLISHER_NOT_IMPLEMENTED",
    )
    inputs = {
        # The production composition consumes the exact frozen outputs; tests
        # must not manufacture an unfrozen state that no builder emits.
        "policy_candidate_raw": policy_raw,
        "v24_candidate_raw": v24_raw,
        "policy_fixture_raw": _render(policy_fixture),
        "v24_fixture_raw": _render(v24_fixture),
        "policy_review_map_raw": _POLICY_REVIEW_MAP.read_bytes(),
    }
    _CACHED_ASSETS = publisher, inputs
    return publisher, dict(inputs)


def _targets(tmp_path: Path) -> dict[str, Path]:
    contracts = tmp_path / "contracts"
    fixtures = tmp_path / "tests" / "slice100_task8" / "fixtures"
    plans = tmp_path / "docs" / "plans"
    contracts.mkdir(parents=True)
    fixtures.mkdir(parents=True)
    plans.mkdir(parents=True)
    targets = {
        "policy_artifact_path": contracts / "fdd_formula_policy_v2.json",
        "v24_artifact_path": contracts / "full_strict_v24_reviewed_preimage_v1.json",
        "policy_fixture_path": fixtures / "fdd_formula_policy_v2_hash_v1.json",
        "v24_fixture_path": fixtures / "full_strict_v24_contract_hash_v1.json",
        "policy_review_map_path": plans / _POLICY_REVIEW_MAP.name,
    }
    targets["policy_artifact_path"].write_bytes(oracle.POLICY_V2_ARTIFACT.read_bytes())
    targets["policy_fixture_path"].write_bytes(_POLICY_FIXTURE.read_bytes())
    targets["policy_review_map_path"].write_bytes(_POLICY_REVIEW_MAP.read_bytes())
    return targets


def _assert_preexisting_exact(targets: dict[str, Path]) -> None:
    _assert_safe_exact_bytes(
        targets["policy_fixture_path"].read_bytes(),
        _POLICY_FIXTURE.read_bytes(),
        "TASK8_POLICY_V2_FIXTURE_PREREQUISITE_MUTATED",
    )
    _assert_safe_exact_bytes(
        targets["policy_artifact_path"].read_bytes(),
        oracle.POLICY_V2_ARTIFACT.read_bytes(),
        "TASK8_POLICY_V2_ARTIFACT_PREREQUISITE_MUTATED",
    )
    _assert_safe_exact_bytes(
        targets["policy_review_map_path"].read_bytes(),
        _POLICY_REVIEW_MAP.read_bytes(),
        "TASK8_POLICY_V2_REVIEW_MAP_PREREQUISITE_MUTATED",
    )


def _assert_v24_targets_absent(targets: dict[str, Path]) -> None:
    assert not targets["v24_fixture_path"].exists()
    assert not targets["v24_artifact_path"].exists()


def _assert_safe_exact_bytes(actual: bytes, expected: bytes, code: str) -> None:
    if actual != expected:
        actual_sha256 = hashlib.sha256(actual).hexdigest()
        expected_sha256 = hashlib.sha256(expected).hexdigest()
        raise AssertionError(
            f"{code}:actual_sha256={actual_sha256}:expected_sha256={expected_sha256}"
        )


def _safe_file_snapshot(path: Path) -> tuple[int, str, tuple[int, int]]:
    raw = path.read_bytes()
    status = path.stat()
    return (
        len(raw),
        hashlib.sha256(raw).hexdigest(),
        (int(status.st_dev), int(status.st_ino)),
    )


def _task8_sidecars(targets: dict[str, Path]) -> list[Path]:
    parents = {path.parent for path in targets.values()}
    return sorted(
        (
            path
            for parent in parents
            for path in parent.iterdir()
            if ".task8-gate." in path.name or ".task8-stage." in path.name
        ),
        key=lambda path: str(path),
    )


def _publish(
    publisher: Any,
    inputs: dict[str, bytes],
    targets: dict[str, Path],
) -> dict[str, Any]:
    result = publisher.publish_task8_gate_atomically(**inputs, **targets)
    assert isinstance(result, dict)
    return result


def test_task8_gate_verifies_v2_prerequisites_then_publishes_v24_commit_point(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    publisher, inputs = _assets()
    targets = _targets(tmp_path)
    observed: list[Path] = []
    real_publish = publisher._publish_no_clobber

    def record_publish(stage: Path, target: Path, *args: Any, **kwargs: Any) -> None:
        real_publish(stage, target, *args, **kwargs)
        observed.append(Path(target))

    monkeypatch.setattr(publisher, "_publish_no_clobber", record_publish)
    result = _publish(publisher, inputs, targets)

    assert observed == [
        targets["v24_fixture_path"],
        targets["v24_artifact_path"],
    ]
    assert tuple(result["preexisting_verified"]) == _PREEXISTING
    assert tuple(result["created_publication_order"]) == _CREATED_ORDER
    assert result["commit_point"] == "full_strict_v24_artifact"
    assert result["activation_status"] == "INACTIVE"
    assert result["gate_admitted"] is True
    _assert_safe_exact_bytes(
        targets["policy_artifact_path"].read_bytes(),
        oracle.POLICY_V2_ARTIFACT.read_bytes(),
        "TASK8_POLICY_ARTIFACT_PUBLICATION_BYTES_DIFFER",
    )
    _assert_safe_exact_bytes(
        targets["v24_artifact_path"].read_bytes(),
        inputs["v24_candidate_raw"],
        "TASK8_V24_ARTIFACT_PUBLICATION_BYTES_DIFFER",
    )
    _assert_safe_exact_bytes(
        targets["policy_fixture_path"].read_bytes(),
        inputs["policy_fixture_raw"],
        "TASK8_POLICY_FIXTURE_PUBLICATION_BYTES_DIFFER",
    )
    _assert_safe_exact_bytes(
        targets["v24_fixture_path"].read_bytes(),
        inputs["v24_fixture_raw"],
        "TASK8_V24_FIXTURE_PUBLICATION_BYTES_DIFFER",
    )
    if _task8_sidecars(targets):
        raise AssertionError("TASK8_SUCCESS_SIDECAR_REMAINS")


def test_task8_gate_composes_only_production_materializer_generator_and_publisher(
    tmp_path: Path,
) -> None:
    policy_builder = _load_module(
        oracle.REPO_ROOT / "scripts" / "materialize_fdd_formula_policy_v2.py",
        "_task8_production_policy_builder_red",
        "TASK8_POLICY_MATERIALIZER_NOT_IMPLEMENTED",
    )
    generator = _load_module(
        _FIXTURE_GENERATOR,
        "_task8_production_fixture_generator_red",
        "TASK8_FIXTURE_GENERATOR_NOT_IMPLEMENTED",
    )
    publisher = _load_module(
        _GATE_PUBLISHER,
        "_task8_production_gate_publisher_red",
        "TASK8_GATE_PUBLISHER_NOT_IMPLEMENTED",
    )
    policy_bundle = policy_builder.build_policy_publication_bundle(OPERATOR_WORKBOOK)
    v24_raw = _production_v24_raw()
    expected_policy_fixture, expected_v24_fixture = generator.derive_task8_fixtures(
        policy_artifact_raw=policy_bundle["policy_artifact_raw"],
        v24_artifact_raw=v24_raw,
    )
    _assert_safe_exact_bytes(
        policy_bundle["policy_fixture_raw"],
        _render(expected_policy_fixture),
        "TASK8_POLICY_FIXTURE_DERIVATION_BYTES_DIFFER",
    )
    inputs = {
        "policy_candidate_raw": policy_bundle["policy_artifact_raw"],
        "v24_candidate_raw": v24_raw,
        "policy_fixture_raw": policy_bundle["policy_fixture_raw"],
        "v24_fixture_raw": _render(expected_v24_fixture),
        "policy_review_map_raw": policy_bundle["policy_review_map_raw"],
    }
    targets = _targets(tmp_path)

    result = _publish(publisher, inputs, targets)

    assert result["gate_admitted"] is True
    _assert_safe_exact_bytes(
        targets["policy_artifact_path"].read_bytes(),
        inputs["policy_candidate_raw"],
        "TASK8_POLICY_ARTIFACT_COMPOSITION_BYTES_DIFFER",
    )
    _assert_safe_exact_bytes(
        targets["v24_artifact_path"].read_bytes(),
        inputs["v24_candidate_raw"],
        "TASK8_V24_ARTIFACT_COMPOSITION_BYTES_DIFFER",
    )


def test_disposable_full_composition_materializes_v2_then_atomically_completes_v24(
    tmp_path: Path,
) -> None:
    policy_builder = _load_module(
        oracle.REPO_ROOT / "scripts" / "materialize_fdd_formula_policy_v2.py",
        "_task8_disposable_policy_builder_red",
        "TASK8_POLICY_MATERIALIZER_NOT_IMPLEMENTED",
    )
    publisher, inputs = _assets()
    contracts = tmp_path / "contracts"
    fixtures = tmp_path / "tests" / "slice100_task8" / "fixtures"
    plans = tmp_path / "docs" / "plans"
    policy_builder.POLICY_ARTIFACT = contracts / "fdd_formula_policy_v2.json"
    policy_builder.POLICY_FIXTURE = fixtures / "fdd_formula_policy_v2_hash_v1.json"
    policy_builder.REVIEW_MAP = plans / _POLICY_REVIEW_MAP.name

    policy_result = policy_builder.materialize(OPERATOR_WORKBOOK)
    assert policy_result["activation_status"] == "INACTIVE"
    targets = {
        "policy_artifact_path": policy_builder.POLICY_ARTIFACT,
        "v24_artifact_path": contracts / "full_strict_v24_reviewed_preimage_v1.json",
        "policy_fixture_path": policy_builder.POLICY_FIXTURE,
        "v24_fixture_path": fixtures / "full_strict_v24_contract_hash_v1.json",
        "policy_review_map_path": policy_builder.REVIEW_MAP,
    }
    _assert_preexisting_exact(targets)

    result = _publish(publisher, inputs, targets)

    assert tuple(result["preexisting_verified"]) == _PREEXISTING
    assert tuple(result["created_publication_order"]) == _CREATED_ORDER
    assert result["commit_point"] == "full_strict_v24_artifact"
    assert result["gate_admitted"] is True


def test_task8_gate_public_api_cannot_inject_a_fixture_deriver(
    tmp_path: Path,
) -> None:
    publisher, inputs = _assets()
    targets = _targets(tmp_path)

    assert (
        "fixture_deriver"
        not in inspect.signature(publisher.publish_task8_gate_atomically).parameters
    )
    with pytest.raises(TypeError, match="fixture_deriver"):
        publisher.publish_task8_gate_atomically(
            **inputs,
            **targets,
            fixture_deriver=lambda _policy, _v24, _reviewer_map: (
                inputs["policy_fixture_raw"],
                inputs["v24_fixture_raw"],
            ),
        )
    _assert_v24_targets_absent(targets)
    _assert_preexisting_exact(targets)


def test_task8_publisher_has_only_the_v24_public_surface_and_verification_chain() -> None:
    publisher = _load_module(
        _GATE_PUBLISHER,
        "_task8_v24_publisher_source_contract_red",
        "TASK8_GATE_PUBLISHER_NOT_IMPLEMENTED",
    )
    parameters = inspect.signature(publisher.publish_task8_gate_atomically).parameters
    assert tuple(parameters) == (
        "policy_candidate_raw",
        "v24_candidate_raw",
        "policy_fixture_raw",
        "v24_fixture_raw",
        "policy_review_map_raw",
        "policy_artifact_path",
        "v24_artifact_path",
        "policy_fixture_path",
        "v24_fixture_path",
        "policy_review_map_path",
    )
    assert all(
        parameter.kind is inspect.Parameter.KEYWORD_ONLY for parameter in parameters.values()
    )

    source = _GATE_PUBLISHER.read_text(encoding="utf-8")
    tree = ast.parse(source)
    assert "materialize_full_strict_v24_preimage.py" in source
    assert "verify_full_strict_v24_fixture_independently.py" in source
    assert "v22" not in source.lower()
    assert "v23" not in source.lower()
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    ]
    materializer_calls = [node for node in calls if node.func.attr == "validate_frozen_artifact"]
    verifier_calls = [node for node in calls if node.func.attr == "verify_fixture"]
    assert len(materializer_calls) == 1, "TASK8_V24_PUBLISHER_EXACT_DIFF_ADMISSION_MISSING"
    assert {keyword.arg for keyword in materializer_calls[0].keywords} == {
        "parent_artifact_raw",
        "policy_artifact_raw",
        "reviewer_map_raw",
    }
    assert len(verifier_calls) == 1, "TASK8_V24_PUBLISHER_INDEPENDENT_VERIFY_MISSING"
    assert {keyword.arg for keyword in verifier_calls[0].keywords} == {
        "artifact_raw",
        "policy_artifact_raw",
        "reviewer_map_raw",
        "fixture_raw",
    }


def test_task8_gate_private_exact_derivation_interface_receives_frozen_artifact_bytes(
    tmp_path: Path,
) -> None:
    publisher, inputs = _assets()
    targets = _targets(tmp_path)
    observed: list[tuple[str, str, str]] = []

    def derive(
        policy_artifact_raw: bytes,
        v24_artifact_raw: bytes,
        reviewer_map_raw: bytes,
    ) -> tuple[bytes, bytes]:
        observed.append(
            (
                hashlib.sha256(policy_artifact_raw).hexdigest(),
                hashlib.sha256(v24_artifact_raw).hexdigest(),
                hashlib.sha256(reviewer_map_raw).hexdigest(),
            )
        )
        return inputs["policy_fixture_raw"], inputs["v24_fixture_raw"]

    result = publisher._publish_task8_gate_with_deriver(
        **inputs,
        **targets,
        fixture_deriver=derive,
    )

    assert result["gate_admitted"] is True
    assert observed == [
        (
            hashlib.sha256(inputs["policy_candidate_raw"]).hexdigest(),
            hashlib.sha256(inputs["v24_candidate_raw"]).hexdigest(),
            hashlib.sha256(inputs["policy_review_map_raw"]).hexdigest(),
        ),
    ]


@pytest.mark.parametrize(
    ("fixture_name", "mutation_path"),
    (
        ("policy_fixture_raw", ("artifact_schema",)),
        ("policy_fixture_raw", ("policy_id",)),
        ("policy_fixture_raw", ("artifact_sha256",)),
        ("policy_fixture_raw", ("fdd_formula_policy_v2_sha256",)),
        ("policy_fixture_raw", ("component_hashes", "formula_registry")),
        ("policy_fixture_raw", ("review_map_sha256",)),
        ("policy_fixture_raw", ("counts", "formula_count")),
        ("policy_fixture_raw", ("activation_status",)),
        ("v24_fixture_raw", ("artifact_schema",)),
        ("v24_fixture_raw", ("pipeline_contract_version",)),
        ("v24_fixture_raw", ("artifact_sha256",)),
        ("v24_fixture_raw", ("FULL_STRICT_V24_CONTRACT_HASH",)),
        ("v24_fixture_raw", ("component_hashes", "task8_fdd_contract_hash")),
        ("v24_fixture_raw", ("counts", "schema_count")),
        ("v24_fixture_raw", ("policy_binding", "policy_sha256")),
        ("v24_fixture_raw", ("policy_binding", "reviewer_map_relative_path")),
        ("v24_fixture_raw", ("policy_binding", "reviewer_map_sha256")),
        ("v24_fixture_raw", ("policy_binding", "binding_sha256")),
        ("v24_fixture_raw", ("activation_status",)),
    ),
)
def test_task8_gate_rejects_any_fixture_semantic_drift_before_writing(
    tmp_path: Path,
    fixture_name: str,
    mutation_path: tuple[str, ...],
) -> None:
    publisher, inputs = _assets()
    targets = _targets(tmp_path)
    changed = dict(inputs)
    fixture = json.loads(changed[fixture_name])
    cursor: Any = fixture
    for key in mutation_path[:-1]:
        cursor = cursor[key]
    final = mutation_path[-1]
    cursor[final] = 0 if isinstance(cursor[final], int) else "0" * 64
    changed[fixture_name] = _render(fixture)
    if fixture_name == "policy_fixture_raw":
        # The prerequisite byte identity is admitted first; matching bad bytes
        # must still be rejected by semantic fixture verification.
        targets["policy_fixture_path"].write_bytes(changed[fixture_name])

    with pytest.raises(
        publisher.Task8GatePublicationError,
        match="TASK8_GATE_FIXTURE_INVALID",
    ):
        _publish(publisher, changed, targets)

    _assert_v24_targets_absent(targets)
    if fixture_name == "policy_fixture_raw":
        assert targets["policy_fixture_path"].read_bytes() == changed[fixture_name]
    else:
        _assert_preexisting_exact(targets)
    assert not publisher._journal_path(targets["v24_artifact_path"]).exists()


@pytest.mark.parametrize("fixture_name", ("policy_fixture_raw", "v24_fixture_raw"))
def test_task8_gate_rejects_unknown_fixture_fields_before_writing(
    tmp_path: Path,
    fixture_name: str,
) -> None:
    publisher, inputs = _assets()
    targets = _targets(tmp_path)
    changed = dict(inputs)
    fixture = json.loads(changed[fixture_name])
    fixture["unexpected_semantic_authority"] = "FORBIDDEN"
    changed[fixture_name] = _render(fixture)
    if fixture_name == "policy_fixture_raw":
        targets["policy_fixture_path"].write_bytes(changed[fixture_name])

    with pytest.raises(
        publisher.Task8GatePublicationError,
        match="TASK8_GATE_FIXTURE_INVALID",
    ):
        _publish(publisher, changed, targets)

    _assert_v24_targets_absent(targets)
    if fixture_name == "policy_fixture_raw":
        assert targets["policy_fixture_path"].read_bytes() == changed[fixture_name]
    else:
        _assert_preexisting_exact(targets)
    assert not publisher._journal_path(targets["v24_artifact_path"]).exists()


@pytest.mark.parametrize("rejected_version", ("v22", "v23"))
def test_task8_gate_explicitly_rejects_historical_v22_and_v23_artifacts(
    tmp_path: Path,
    rejected_version: str,
) -> None:
    publisher, inputs = _assets()
    targets = _targets(tmp_path)
    changed = dict(inputs)
    changed["v24_candidate_raw"] = (
        oracle.REPO_ROOT / "contracts" / f"full_strict_{rejected_version}_reviewed_preimage_v1.json"
    ).read_bytes()

    with pytest.raises(
        publisher.Task8GatePublicationError,
        match="TASK8_GATE_INPUT_INVALID",
    ):
        _publish(publisher, changed, targets)

    _assert_v24_targets_absent(targets)
    _assert_preexisting_exact(targets)


@pytest.mark.parametrize("rejected_version", ("v22", "v23"))
def test_task8_gate_explicitly_rejects_historical_v22_and_v23_fixtures(
    tmp_path: Path,
    rejected_version: str,
) -> None:
    publisher, inputs = _assets()
    targets = _targets(tmp_path)
    changed = dict(inputs)
    fixture_path = (
        _ARCHIVED_V22_FIXTURE
        if rejected_version == "v22"
        else (
            oracle.REPO_ROOT
            / "tests"
            / "slice100_task8"
            / "fixtures"
            / "full_strict_v23_contract_hash_v1.json"
        )
    )
    changed["v24_fixture_raw"] = fixture_path.read_bytes()

    with pytest.raises(
        publisher.Task8GatePublicationError,
        match="TASK8_GATE_FIXTURE_INVALID",
    ):
        _publish(publisher, changed, targets)

    _assert_v24_targets_absent(targets)
    _assert_preexisting_exact(targets)


def test_task8_gate_rejects_review_map_drift_before_writing(tmp_path: Path) -> None:
    publisher, inputs = _assets()
    targets = _targets(tmp_path)
    changed = dict(inputs)
    changed["policy_review_map_raw"] += b"drift"
    targets["policy_review_map_path"].write_bytes(changed["policy_review_map_raw"])

    with pytest.raises(
        publisher.Task8GatePublicationError,
        match="TASK8_GATE_REVIEW_MAP_INVALID",
    ):
        _publish(publisher, changed, targets)

    _assert_v24_targets_absent(targets)
    assert targets["policy_review_map_path"].read_bytes() == changed["policy_review_map_raw"]
    assert not publisher._journal_path(targets["v24_artifact_path"]).exists()


def test_task8_gate_preflight_rejects_any_differing_target_before_first_write(
    tmp_path: Path,
) -> None:
    publisher, inputs = _assets()
    targets = _targets(tmp_path)
    targets["v24_artifact_path"].write_bytes(b"different-preexisting-bytes")

    with pytest.raises(
        publisher.Task8GatePublicationError,
        match="TASK8_GATE_TARGET_ALREADY_EXISTS",
    ):
        _publish(publisher, inputs, targets)

    assert targets["v24_artifact_path"].read_bytes() == b"different-preexisting-bytes"
    assert not targets["v24_fixture_path"].exists()
    _assert_preexisting_exact(targets)
    assert not publisher._journal_path(targets["v24_artifact_path"]).exists()


@pytest.mark.parametrize(
    ("prerequisite_name", "failure_kind"),
    (
        ("policy_fixture_path", "missing"),
        ("policy_fixture_path", "different"),
        ("policy_artifact_path", "missing"),
        ("policy_artifact_path", "different"),
        ("policy_review_map_path", "missing"),
        ("policy_review_map_path", "different"),
    ),
)
def test_task8_gate_requires_exact_preexisting_v2_prerequisites_before_any_write(
    tmp_path: Path,
    prerequisite_name: str,
    failure_kind: str,
) -> None:
    publisher, inputs = _assets()
    targets = _targets(tmp_path)
    path = targets[prerequisite_name]
    if failure_kind == "missing":
        path.unlink()
    else:
        path.write_bytes(b"different-preexisting-prerequisite")

    with pytest.raises(
        publisher.Task8GatePublicationError,
        match=r"^TASK8_GATE_PREREQUISITE_INVALID$",
    ):
        _publish(publisher, inputs, targets)

    _assert_v24_targets_absent(targets)
    assert not publisher._journal_path(targets["v24_artifact_path"]).exists()
    assert not publisher._lock_path(targets["v24_artifact_path"]).exists()


def test_task8_gate_admits_preexisting_prerequisites_before_dynamic_derivation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    publisher = _load_module(
        _GATE_PUBLISHER,
        "_task8_v24_prerequisite_order_red",
        "TASK8_GATE_PUBLISHER_NOT_IMPLEMENTED",
    )
    targets = _targets(tmp_path)
    targets["policy_review_map_path"].unlink()
    inputs = {
        "policy_candidate_raw": oracle.POLICY_V2_ARTIFACT.read_bytes(),
        "v24_candidate_raw": oracle.V24_ARTIFACT.read_bytes(),
        "policy_fixture_raw": _POLICY_FIXTURE.read_bytes(),
        "v24_fixture_raw": b"must-not-be-read-before-prerequisite-admission",
        "policy_review_map_raw": _POLICY_REVIEW_MAP.read_bytes(),
    }

    def forbidden_derivation(*_args: bytes) -> tuple[bytes, bytes]:
        raise AssertionError("TASK8_V24_DERIVATION_RAN_BEFORE_PREREQUISITE_ADMISSION")

    monkeypatch.setattr(publisher, "_derive_expected_fixture_bytes", forbidden_derivation)
    with pytest.raises(
        publisher.Task8GatePublicationError,
        match=r"^TASK8_GATE_PREREQUISITE_INVALID$",
    ):
        _publish(publisher, inputs, targets)

    _assert_v24_targets_absent(targets)
    assert not publisher._journal_path(targets["v24_artifact_path"]).exists()
    assert not publisher._lock_path(targets["v24_artifact_path"]).exists()


def _child_asset_directory(tmp_path: Path, inputs: dict[str, bytes]) -> Path:
    assets = tmp_path / "child-assets"
    assets.mkdir()
    for name, raw in inputs.items():
        (assets / f"{name}.bin").write_bytes(raw)
    return assets


def _child_publisher_source(
    *,
    assets: Path,
    targets: dict[str, Path],
    checkpoint: str,
    checkpoint_ordinal: int | None = None,
    ready_path: Path | None = None,
) -> str:
    target_literals = {name: str(path) for name, path in targets.items()}
    return f"""
import importlib.util
import os
from pathlib import Path
import time

module_path = Path({str(_GATE_PUBLISHER)!r})
assets = Path({str(assets)!r})
spec = importlib.util.spec_from_file_location("_task8_hard_crash_child", module_path)
module = importlib.util.module_from_spec(spec)
assert spec is not None and spec.loader is not None
spec.loader.exec_module(module)
checkpoint = {checkpoint!r}
checkpoint_ordinal = {checkpoint_ordinal!r}
ready_path = {None if ready_path is None else str(ready_path)!r}
policy_candidate_raw = (assets / "policy_candidate_raw.bin").read_bytes()
v24_candidate_raw = (assets / "v24_candidate_raw.bin").read_bytes()
policy_fixture_raw = (assets / "policy_fixture_raw.bin").read_bytes()
v24_fixture_raw = (assets / "v24_fixture_raw.bin").read_bytes()
policy_review_map_raw = (assets / "policy_review_map_raw.bin").read_bytes()

def derive_exact_fixtures(policy_artifact_raw, v24_artifact_raw, reviewer_map_raw):
    if (
        policy_artifact_raw != policy_candidate_raw
        or v24_artifact_raw != v24_candidate_raw
        or reviewer_map_raw != policy_review_map_raw
    ):
        raise RuntimeError("artifact bytes do not match the crash-test publication set")
    return policy_fixture_raw, v24_fixture_raw

def process_exit_checkpoint(name, ordinal=None):
    if name != checkpoint or ordinal != checkpoint_ordinal:
        return
    if ready_path is not None:
        Path(ready_path).write_text("ready", encoding="utf-8")
        time.sleep(30)
    os._exit(91)

module._process_exit_checkpoint = process_exit_checkpoint

module._publish_task8_gate_with_deriver(
    policy_candidate_raw=policy_candidate_raw,
    v24_candidate_raw=v24_candidate_raw,
    policy_fixture_raw=policy_fixture_raw,
    v24_fixture_raw=v24_fixture_raw,
    policy_review_map_raw=policy_review_map_raw,
    policy_artifact_path=Path({target_literals["policy_artifact_path"]!r}),
    v24_artifact_path=Path({target_literals["v24_artifact_path"]!r}),
    policy_fixture_path=Path({target_literals["policy_fixture_path"]!r}),
    v24_fixture_path=Path({target_literals["v24_fixture_path"]!r}),
    policy_review_map_path=Path({target_literals["policy_review_map_path"]!r}),
    fixture_deriver=derive_exact_fixtures,
)
"""


@pytest.mark.parametrize(
    ("checkpoint", "checkpoint_ordinal", "already_published"),
    (
        ("after_lock_acquired_before_journal", None, False),
        ("after_journal_temp_fsync_before_link", None, False),
        *(("after_stage_fsync_before_target_link", ordinal, False) for ordinal in range(1, 3)),
        *(("after_target_link_before_stage_unlink", ordinal, False) for ordinal in range(1, 3)),
        ("after_journal_removal_before_lock_cleanup", None, True),
    ),
)
def test_task8_gate_recovers_every_exact_process_exit_window(
    tmp_path: Path,
    checkpoint: str,
    checkpoint_ordinal: int | None,
    already_published: bool,
) -> None:
    publisher, inputs = _assets()
    targets = _targets(tmp_path)
    assets = _child_asset_directory(tmp_path, inputs)
    child = subprocess.run(
        [
            _ISOLATED_PYTHON,
            "-I",
            "-c",
            _child_publisher_source(
                assets=assets,
                targets=targets,
                checkpoint=checkpoint,
                checkpoint_ordinal=checkpoint_ordinal,
            ),
        ],
        capture_output=True,
        check=False,
        timeout=30,
    )

    if child.returncode != 91:
        raise AssertionError(f"TASK8_PROCESS_EXIT_CHECKPOINT_MISSED:{child.returncode}")
    if already_published:
        before = {name: _safe_file_snapshot(path) for name, path in targets.items()}
        with pytest.raises(
            publisher.Task8GatePublicationError,
            match="TASK8_GATE_ALREADY_PUBLISHED",
        ):
            _publish(publisher, inputs, targets)
        after = {name: _safe_file_snapshot(path) for name, path in targets.items()}
        if after != before:
            raise AssertionError("TASK8_COMPLETED_OUTPUT_REWRITTEN")
    else:
        result = _publish(publisher, inputs, targets)
        assert result["gate_admitted"] is True
    if _task8_sidecars(targets):
        raise AssertionError("TASK8_RECOVERY_SIDECAR_REMAINS")


def test_task8_gate_rejects_stage_mutation_after_fsync_before_target_link(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    publisher, inputs = _assets()
    targets = _targets(tmp_path)

    def mutate_stage(name: str, ordinal: int | None = None) -> None:
        if name != "after_stage_fsync_before_target_link" or ordinal != 1:
            return
        stages = list(targets["v24_fixture_path"].parent.glob(".*.task8-stage.*.1"))
        if len(stages) != 1:
            raise AssertionError("TASK8_EXPECTED_STAGE_NOT_FOUND")
        size = stages[0].stat().st_size
        stages[0].write_bytes(b"X" * size)

    monkeypatch.setattr(publisher, "_process_exit_checkpoint", mutate_stage)
    with pytest.raises(
        publisher.Task8GatePublicationError,
        match="TASK8_GATE_RECOVERY_STAGE_INVALID",
    ):
        _publish(publisher, inputs, targets)
    _assert_v24_targets_absent(targets)
    _assert_preexisting_exact(targets)


def test_task8_gate_revalidates_all_preexisting_bytes_before_journal_removal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    publisher, inputs = _assets()
    targets = _targets(tmp_path)
    real_publish = publisher._publish_no_clobber

    def mutate_after_last_link(stage: Path, target: Path, *args: Any, **kwargs: Any) -> Any:
        result = real_publish(stage, target, *args, **kwargs)
        if Path(target) == targets["v24_artifact_path"]:
            first = targets["policy_fixture_path"]
            first.write_bytes(b"X" * first.stat().st_size)
        return result

    monkeypatch.setattr(publisher, "_publish_no_clobber", mutate_after_last_link)
    with pytest.raises(
        publisher.Task8GatePublicationError,
        match="TASK8_GATE_PREREQUISITE_INVALID",
    ):
        _publish(publisher, inputs, targets)


def test_task8_gate_rolls_back_exact_target_when_post_link_stage_cleanup_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    publisher, inputs = _assets()
    targets = _targets(tmp_path)
    real_unlink = publisher._unlink_verified_sidecar
    injected = False

    def fail_once(path: Path, **kwargs: Any) -> None:
        nonlocal injected
        if ".task8-stage." in path.name and not injected:
            injected = True
            raise OSError("synthetic-cleanup-failure")
        real_unlink(path, **kwargs)

    monkeypatch.setattr(publisher, "_unlink_verified_sidecar", fail_once)
    with pytest.raises(
        publisher.Task8GatePublicationError,
        match="TASK8_GATE_INTERNAL_FAILURE",
    ):
        _publish(publisher, inputs, targets)
    assert injected is True
    _assert_v24_targets_absent(targets)
    _assert_preexisting_exact(targets)
    if _task8_sidecars(targets):
        raise AssertionError("TASK8_POST_LINK_FAILURE_SIDECAR_REMAINS")


@pytest.mark.parametrize(
    "takeover_checkpoint",
    (
        "after_takeover_temp_fsync_before_replace",
        "after_lock_acquired_before_journal",
    ),
)
def test_task8_gate_recovers_process_exit_during_atomic_lock_takeover(
    tmp_path: Path,
    takeover_checkpoint: str,
) -> None:
    publisher, inputs = _assets()
    targets = _targets(tmp_path)
    assets = _child_asset_directory(tmp_path, inputs)
    first = subprocess.run(
        [
            _ISOLATED_PYTHON,
            "-I",
            "-c",
            _child_publisher_source(
                assets=assets,
                targets=targets,
                checkpoint="after_stage_fsync_before_target_link",
                checkpoint_ordinal=1,
            ),
        ],
        capture_output=True,
        check=False,
        timeout=30,
    )
    if first.returncode != 91:
        raise AssertionError(f"TASK8_INITIAL_PROCESS_EXIT_MISSED:{first.returncode}")
    takeover = subprocess.run(
        [
            _ISOLATED_PYTHON,
            "-I",
            "-c",
            _child_publisher_source(
                assets=assets,
                targets=targets,
                checkpoint=takeover_checkpoint,
            ),
        ],
        capture_output=True,
        check=False,
        timeout=30,
    )
    if takeover.returncode != 91:
        raise AssertionError(f"TASK8_TAKEOVER_PROCESS_EXIT_MISSED:{takeover.returncode}")

    result = _publish(publisher, inputs, targets)
    assert result["gate_admitted"] is True
    if _task8_sidecars(targets):
        raise AssertionError("TASK8_TAKEOVER_RECOVERY_SIDECAR_REMAINS")


def test_task8_gate_never_reclaims_a_live_atomic_takeover(
    tmp_path: Path,
) -> None:
    publisher, inputs = _assets()
    targets = _targets(tmp_path)
    assets = _child_asset_directory(tmp_path, inputs)
    first = subprocess.run(
        [
            _ISOLATED_PYTHON,
            "-I",
            "-c",
            _child_publisher_source(
                assets=assets,
                targets=targets,
                checkpoint="after_stage_fsync_before_target_link",
                checkpoint_ordinal=1,
            ),
        ],
        capture_output=True,
        check=False,
        timeout=30,
    )
    if first.returncode != 91:
        raise AssertionError(f"TASK8_INITIAL_PROCESS_EXIT_MISSED:{first.returncode}")

    ready = tmp_path / "live-takeover-ready"
    takeover = subprocess.Popen(
        [
            _ISOLATED_PYTHON,
            "-I",
            "-c",
            _child_publisher_source(
                assets=assets,
                targets=targets,
                checkpoint="after_takeover_temp_fsync_before_replace",
                ready_path=ready,
            ),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        deadline = time.monotonic() + 10
        while not ready.exists() and takeover.poll() is None and time.monotonic() < deadline:
            time.sleep(0.05)
        if not ready.is_file():
            raise AssertionError("TASK8_LIVE_TAKEOVER_CHECKPOINT_NOT_REACHED")
        with pytest.raises(
            publisher.Task8GatePublicationError,
            match="TASK8_GATE_LOCKED",
        ):
            _publish(publisher, inputs, targets)
    finally:
        takeover.terminate()
        takeover.communicate(timeout=10)

    assert _publish(publisher, inputs, targets)["gate_admitted"] is True


def test_task8_gate_rejects_distinct_inode_stage_target_recovery_alias(
    tmp_path: Path,
) -> None:
    publisher, inputs = _assets()
    targets = _targets(tmp_path)
    assets = _child_asset_directory(tmp_path, inputs)
    child = subprocess.run(
        [
            _ISOLATED_PYTHON,
            "-I",
            "-c",
            _child_publisher_source(
                assets=assets,
                targets=targets,
                checkpoint="after_stage_fsync_before_target_link",
                checkpoint_ordinal=1,
            ),
        ],
        capture_output=True,
        check=False,
        timeout=30,
    )
    if child.returncode != 91:
        raise AssertionError(f"TASK8_PROCESS_EXIT_CHECKPOINT_MISSED:{child.returncode}")
    targets["v24_fixture_path"].write_bytes(inputs["v24_fixture_raw"])

    with pytest.raises(
        publisher.Task8GatePublicationError,
        match="TASK8_GATE_FILE_IDENTITY_ALIAS",
    ):
        _publish(publisher, inputs, targets)


def test_task8_gate_rejects_more_than_the_current_recovery_stage(
    tmp_path: Path,
) -> None:
    publisher, inputs = _assets()
    targets = _targets(tmp_path)
    assets = _child_asset_directory(tmp_path, inputs)
    child = subprocess.run(
        [
            _ISOLATED_PYTHON,
            "-I",
            "-c",
            _child_publisher_source(
                assets=assets,
                targets=targets,
                checkpoint="after_stage_fsync_before_target_link",
                checkpoint_ordinal=1,
            ),
        ],
        capture_output=True,
        check=False,
        timeout=30,
    )
    if child.returncode != 91:
        raise AssertionError(f"TASK8_PROCESS_EXIT_CHECKPOINT_MISSED:{child.returncode}")
    lock_value = json.loads(publisher._lock_path(targets["v24_artifact_path"]).read_bytes())
    second_stage = publisher._stage_path(
        targets["v24_artifact_path"],
        lock_value["owner_token"],
        2,
    )
    second_stage.write_bytes(inputs["v24_candidate_raw"])

    with pytest.raises(
        publisher.Task8GatePublicationError,
        match="TASK8_GATE_RECOVERY_TOPOLOGY_INVALID",
    ):
        _publish(publisher, inputs, targets)


def test_task8_gate_rejects_mismatched_dead_owner_intent_without_mutation(
    tmp_path: Path,
) -> None:
    publisher, inputs = _assets()
    targets = _targets(tmp_path)
    assets = _child_asset_directory(tmp_path, inputs)
    child = subprocess.run(
        [
            _ISOLATED_PYTHON,
            "-I",
            "-c",
            _child_publisher_source(
                assets=assets,
                targets=targets,
                checkpoint="after_stage_fsync_before_target_link",
                checkpoint_ordinal=1,
            ),
        ],
        capture_output=True,
        check=False,
        timeout=30,
    )
    if child.returncode != 91:
        raise AssertionError(f"TASK8_PROCESS_EXIT_CHECKPOINT_MISSED:{child.returncode}")
    sidecars = _task8_sidecars(targets)
    before = {path.name: _safe_file_snapshot(path) for path in sidecars}
    changed_targets = dict(targets)
    changed_targets["v24_fixture_path"] = targets["v24_fixture_path"].with_name(
        "different-v24-fixture.json"
    )

    with pytest.raises(
        publisher.Task8GatePublicationError,
        match="TASK8_GATE_LOCK_MISMATCH",
    ):
        _publish(publisher, inputs, changed_targets)
    after = {path.name: _safe_file_snapshot(path) for path in _task8_sidecars(targets)}
    if after != before:
        raise AssertionError("TASK8_MISMATCHED_RECOVERY_MUTATED_SIDECARS")

    assert _publish(publisher, inputs, targets)["gate_admitted"] is True


@pytest.mark.parametrize(
    ("checkpoint", "checkpoint_ordinal", "sidecar_kind", "expected_code"),
    (
        (
            "after_journal_temp_fsync_before_link",
            None,
            "journal_temp",
            "TASK8_GATE_RECOVERY_JOURNAL_INVALID",
        ),
        (
            "after_stage_fsync_before_target_link",
            1,
            "stage",
            "TASK8_GATE_RECOVERY_STAGE_INVALID",
        ),
    ),
)
def test_task8_gate_rejects_changed_exact_dead_owner_sidecar(
    tmp_path: Path,
    checkpoint: str,
    checkpoint_ordinal: int | None,
    sidecar_kind: str,
    expected_code: str,
) -> None:
    publisher, inputs = _assets()
    targets = _targets(tmp_path)
    assets = _child_asset_directory(tmp_path, inputs)
    child = subprocess.run(
        [
            _ISOLATED_PYTHON,
            "-I",
            "-c",
            _child_publisher_source(
                assets=assets,
                targets=targets,
                checkpoint=checkpoint,
                checkpoint_ordinal=checkpoint_ordinal,
            ),
        ],
        capture_output=True,
        check=False,
        timeout=30,
    )
    if child.returncode != 91:
        raise AssertionError(f"TASK8_PROCESS_EXIT_CHECKPOINT_MISSED:{child.returncode}")
    lock_value = json.loads(publisher._lock_path(targets["v24_artifact_path"]).read_bytes())
    token = lock_value["owner_token"]
    if sidecar_kind == "journal_temp":
        sidecar = publisher._journal_temp_path(
            publisher._journal_path(targets["v24_artifact_path"]), token
        )
    else:
        sidecar = publisher._stage_path(targets["v24_fixture_path"], token, 1)
    sidecar.write_bytes(b"changed")
    before = _safe_file_snapshot(sidecar)

    with pytest.raises(
        publisher.Task8GatePublicationError,
        match=expected_code,
    ):
        _publish(publisher, inputs, targets)
    if _safe_file_snapshot(sidecar) != before:
        raise AssertionError("TASK8_CHANGED_RECOVERY_SIDECAR_MUTATED")


def test_task8_gate_rejects_exact_bytes_at_distinct_stage_target_identity(
    tmp_path: Path,
) -> None:
    publisher, inputs = _assets()
    targets = _targets(tmp_path)
    assets = _child_asset_directory(tmp_path, inputs)
    child = subprocess.run(
        [
            _ISOLATED_PYTHON,
            "-I",
            "-c",
            _child_publisher_source(
                assets=assets,
                targets=targets,
                checkpoint="after_target_link_before_stage_unlink",
                checkpoint_ordinal=1,
            ),
        ],
        capture_output=True,
        check=False,
        timeout=30,
    )
    if child.returncode != 91:
        raise AssertionError(f"TASK8_PROCESS_EXIT_CHECKPOINT_MISSED:{child.returncode}")
    lock_value = json.loads(publisher._lock_path(targets["v24_artifact_path"]).read_bytes())
    stage = publisher._stage_path(targets["v24_fixture_path"], lock_value["owner_token"], 1)
    exact_raw = stage.read_bytes()
    stage.unlink()
    stage.write_bytes(exact_raw)

    with pytest.raises(
        publisher.Task8GatePublicationError,
        match="TASK8_GATE_FILE_IDENTITY_ALIAS",
    ):
        _publish(publisher, inputs, targets)


def test_task8_gate_never_reclaims_a_live_owner_lock(tmp_path: Path) -> None:
    publisher, inputs = _assets()
    targets = _targets(tmp_path)
    assets = _child_asset_directory(tmp_path, inputs)
    ready = tmp_path / "live-owner-ready"
    child = subprocess.Popen(
        [
            _ISOLATED_PYTHON,
            "-I",
            "-c",
            _child_publisher_source(
                assets=assets,
                targets=targets,
                checkpoint="after_lock_acquired_before_journal",
                ready_path=ready,
            ),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        deadline = time.monotonic() + 10
        while not ready.exists() and child.poll() is None and time.monotonic() < deadline:
            time.sleep(0.05)
        assert ready.is_file()
        with pytest.raises(
            publisher.Task8GatePublicationError,
            match="TASK8_GATE_LOCKED",
        ):
            _publish(publisher, inputs, targets)
    finally:
        child.terminate()
        child.communicate(timeout=10)


def test_task8_lock_binds_intent_token_and_real_process_start_identity_without_paths(
    tmp_path: Path,
) -> None:
    publisher, inputs = _assets()
    targets = _targets(tmp_path)
    assets = _child_asset_directory(tmp_path, inputs)
    ready = tmp_path / "live-owner-ready"
    child = subprocess.Popen(
        [
            _ISOLATED_PYTHON,
            "-I",
            "-c",
            _child_publisher_source(
                assets=assets,
                targets=targets,
                checkpoint="after_lock_acquired_before_journal",
                ready_path=ready,
            ),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        deadline = time.monotonic() + 10
        while not ready.exists() and child.poll() is None and time.monotonic() < deadline:
            time.sleep(0.05)
        if not ready.is_file():
            raise AssertionError("TASK8_LIVE_OWNER_CHECKPOINT_NOT_REACHED")
        lock = publisher._lock_path(targets["v24_artifact_path"])
        value = json.loads(lock.read_bytes())
        assert set(value) == {
            "schema_version",
            "intent_sha256",
            "owner_token",
            "owner_pid",
            "owner_start_identity",
        }
        assert publisher._owner_status(value["owner_pid"], value["owner_start_identity"]) == "LIVE"
        assert len(value["intent_sha256"]) == 64
        assert len(value["owner_token"]) == 64
        assert value["owner_start_identity"]
        sidecar_raw = b"".join(path.read_bytes() for path in _task8_sidecars(targets))
        for target in targets.values():
            if str(target).encode("utf-8") in sidecar_raw:
                raise AssertionError("TASK8_SIDECAR_PLAINTEXT_PATH_LEAK")
    finally:
        child.terminate()
        child.communicate(timeout=10)


def test_task8_process_start_identity_distinguishes_live_owner_from_pid_reuse() -> None:
    publisher, _inputs = _assets()
    child = subprocess.Popen(
        [_ISOLATED_PYTHON, "-I", "-c", "import time; time.sleep(30)"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        identity = publisher._process_start_identity(child.pid)
        if not identity:
            raise AssertionError("TASK8_PROCESS_START_IDENTITY_UNAVAILABLE")
        assert publisher._owner_status(child.pid, identity) == "LIVE"
        assert publisher._owner_status(child.pid, "0" * 64) == "DEAD_OR_REUSED"
    finally:
        child.terminate()
        child.wait(timeout=10)


def test_task8_cleanup_rechecks_exact_lock_token_and_bytes_before_unlink(
    tmp_path: Path,
) -> None:
    publisher, _inputs = _assets()
    lock = tmp_path / "lock"
    original = publisher._render(
        {
            "schema_version": "task8-contract-artifact-gate-lock-v2",
            "intent_sha256": "1" * 64,
            "owner_token": "2" * 64,
            "owner_pid": 123,
            "owner_start_identity": "3" * 64,
        }
    )
    lock.write_bytes(original)
    changed = json.loads(original)
    changed["owner_token"] = "4" * 64
    changed_raw = publisher._render(changed)
    lock.write_bytes(changed_raw)

    with pytest.raises(
        publisher.Task8GatePublicationError,
        match="TASK8_GATE_LOCK_CHANGED",
    ):
        publisher._unlink_verified_sidecar(
            lock,
            expected_raw=changed_raw,
            owner_token="2" * 64,
            code="TASK8_GATE_LOCK_CHANGED",
        )
    assert lock.is_file()


def test_task8_primary_stable_error_survives_private_cleanup_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    publisher, inputs = _assets()
    targets = _targets(tmp_path)
    private_canary = "PRIVATE_PATH_AND_WORKBOOK_PROSE_CANARY"
    real_unlink = publisher._unlink_verified_sidecar
    cleanup_attempted = False

    def fail_at_checkpoint(name: str, ordinal: int | None = None) -> None:
        del ordinal
        if name == "after_journal_temp_fsync_before_link":
            raise publisher.Task8GatePublicationError("TASK8_PRIMARY_FAILURE")

    def fail_cleanup(path: Path, **kwargs: Any) -> None:
        nonlocal cleanup_attempted
        if path == publisher._lock_path(targets["v24_artifact_path"]):
            cleanup_attempted = True
            raise OSError(private_canary)
        real_unlink(path, **kwargs)

    monkeypatch.setattr(publisher, "_process_exit_checkpoint", fail_at_checkpoint)
    monkeypatch.setattr(publisher, "_unlink_verified_sidecar", fail_cleanup)
    with pytest.raises(
        publisher.Task8GatePublicationError,
        match="^TASK8_PRIMARY_FAILURE$",
    ) as raised:
        _publish(publisher, inputs, targets)

    formatted = "".join(traceback.format_exception(raised.value))
    if private_canary in formatted or str(tmp_path) in formatted:
        raise AssertionError("TASK8_PRIMARY_FAILURE_PRIVATE_OUTPUT_LEAK")
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None
    assert cleanup_attempted is True


def test_task8_public_error_chain_never_exposes_private_input_or_path(
    tmp_path: Path,
) -> None:
    publisher, inputs = _assets()
    targets = _targets(tmp_path)
    private_canary = "PRIVATE_WORKBOOK_PROSE_CANARY"
    changed = dict(inputs)
    changed["policy_candidate_raw"] = private_canary.encode("utf-8")

    with pytest.raises(
        publisher.Task8GatePublicationError,
        match="^TASK8_GATE_INPUT_INVALID$",
    ) as raised:
        _publish(publisher, changed, targets)

    formatted = "".join(traceback.format_exception(raised.value))
    if private_canary in formatted or str(tmp_path) in formatted:
        raise AssertionError("TASK8_PUBLIC_ERROR_PRIVATE_OUTPUT_LEAK")
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None


@pytest.mark.parametrize("kind", ("journal_temp", "stage"))
def test_task8_gate_rejects_unknown_sidecar_namespace_before_mutation(
    tmp_path: Path,
    kind: str,
) -> None:
    publisher, inputs = _assets()
    targets = _targets(tmp_path)
    if kind == "journal_temp":
        journal = publisher._journal_path(targets["v24_artifact_path"])
        collision = journal.with_name(f"{journal.name}.new.unknown-token")
    else:
        target = targets["v24_fixture_path"]
        collision = target.with_name(f".{target.name}.task8-stage.unknown-token.1")
    collision.write_bytes(b"collision")

    with pytest.raises(
        publisher.Task8GatePublicationError,
        match="TASK8_GATE_SIDECAR_COLLISION",
    ):
        _publish(publisher, inputs, targets)
    _assert_v24_targets_absent(targets)
    _assert_preexisting_exact(targets)


@pytest.mark.parametrize("sidecar_kind", ("lock_directory", "journal_symlink"))
def test_task8_gate_rejects_nonregular_or_reparse_control_sidecar(
    tmp_path: Path,
    sidecar_kind: str,
) -> None:
    publisher, inputs = _assets()
    targets = _targets(tmp_path)
    if sidecar_kind == "lock_directory":
        sidecar = publisher._lock_path(targets["v24_artifact_path"])
        sidecar.mkdir()
    else:
        sidecar = publisher._journal_path(targets["v24_artifact_path"])
        external = tmp_path / "external-journal"
        external.write_bytes(b"sentinel")
        try:
            sidecar.symlink_to(external)
        except OSError:
            return

    with pytest.raises(
        publisher.Task8GatePublicationError,
        match="TASK8_GATE_(LOCK_INVALID|RECOVERY_JOURNAL_INVALID)",
    ):
        _publish(publisher, inputs, targets)
    _assert_v24_targets_absent(targets)
    _assert_preexisting_exact(targets)


def test_task8_gate_rejects_hardlink_alias_between_existing_files(
    tmp_path: Path,
) -> None:
    publisher, inputs = _assets()
    targets = _targets(tmp_path)
    try:
        targets["v24_fixture_path"].hardlink_to(targets["policy_fixture_path"])
    except OSError:
        return

    with pytest.raises(
        publisher.Task8GatePublicationError,
        match="TASK8_GATE_FILE_IDENTITY_ALIAS",
    ):
        _publish(publisher, inputs, targets)


def test_task8_gate_rejects_unknown_token_stage_even_when_hardlinked_to_exact_target(
    tmp_path: Path,
) -> None:
    publisher, inputs = _assets()
    targets = _targets(tmp_path)
    _publish(publisher, inputs, targets)
    target = targets["v24_fixture_path"]
    unknown_stage = target.with_name(f".{target.name}.task8-stage.{'f' * 64}.1")
    try:
        unknown_stage.hardlink_to(target)
    except OSError:
        return

    with pytest.raises(
        publisher.Task8GatePublicationError,
        match="TASK8_GATE_SIDECAR_COLLISION",
    ):
        _publish(publisher, inputs, targets)


def test_task8_gate_rejects_unknown_owner_without_reclaiming_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    publisher, inputs = _assets()
    targets = _targets(tmp_path)
    assets = _child_asset_directory(tmp_path, inputs)
    child = subprocess.run(
        [
            _ISOLATED_PYTHON,
            "-I",
            "-c",
            _child_publisher_source(
                assets=assets,
                targets=targets,
                checkpoint="after_lock_acquired_before_journal",
            ),
        ],
        capture_output=True,
        check=False,
        timeout=30,
    )
    if child.returncode != 91:
        raise AssertionError(f"TASK8_PROCESS_EXIT_CHECKPOINT_MISSED:{child.returncode}")
    lock = publisher._lock_path(targets["v24_artifact_path"])
    before = _safe_file_snapshot(lock)
    monkeypatch.setattr(publisher, "_owner_status", lambda _pid, _identity: "UNKNOWN")

    with pytest.raises(
        publisher.Task8GatePublicationError,
        match="TASK8_GATE_OWNER_UNKNOWN",
    ):
        _publish(publisher, inputs, targets)
    if _safe_file_snapshot(lock) != before:
        raise AssertionError("TASK8_UNKNOWN_OWNER_LOCK_MUTATED")


def test_task8_gate_sidecars_contain_hashes_not_plaintext_target_paths(
    tmp_path: Path,
) -> None:
    publisher, inputs = _assets()
    targets = _targets(tmp_path)
    assets = _child_asset_directory(tmp_path, inputs)
    child = subprocess.run(
        [
            _ISOLATED_PYTHON,
            "-I",
            "-c",
            _child_publisher_source(
                assets=assets,
                targets=targets,
                checkpoint="after_stage_fsync_before_target_link",
                checkpoint_ordinal=1,
            ),
        ],
        capture_output=True,
        check=False,
        timeout=30,
    )
    if child.returncode != 91:
        raise AssertionError(f"TASK8_PROCESS_EXIT_CHECKPOINT_MISSED:{child.returncode}")
    sidecar_raw = b"".join(path.read_bytes() for path in _task8_sidecars(targets))
    for target in targets.values():
        raw_path = str(target).encode("utf-8")
        if raw_path in sidecar_raw:
            raise AssertionError("TASK8_SIDECAR_PLAINTEXT_PATH_LEAK")
        if publisher._path_sha256(target).encode("ascii") not in sidecar_raw:
            raise AssertionError("TASK8_SIDECAR_PATH_HASH_MISSING")


def test_task8_gate_rejects_alias_nonregular_and_symlink_targets(
    tmp_path: Path,
) -> None:
    publisher, inputs = _assets()

    targets = _targets(tmp_path / "alias")
    targets["policy_fixture_path"] = targets["v24_fixture_path"]
    with pytest.raises(
        publisher.Task8GatePublicationError,
        match="TASK8_GATE_PATH_INVALID",
    ):
        _publish(publisher, inputs, targets)

    targets = _targets(tmp_path / "directory")
    targets["policy_artifact_path"].unlink()
    targets["policy_artifact_path"].mkdir()
    with pytest.raises(
        publisher.Task8GatePublicationError,
        match="TASK8_GATE_PATH_INVALID",
    ):
        _publish(publisher, inputs, targets)

    targets = _targets(tmp_path / "symlink")
    real = tmp_path / "symlink-real-v24.json"
    real.write_bytes(b"sentinel")
    try:
        targets["v24_artifact_path"].symlink_to(real)
    except OSError:
        return
    with pytest.raises(
        publisher.Task8GatePublicationError,
        match="TASK8_GATE_PATH_INVALID",
    ):
        _publish(publisher, inputs, targets)
    assert real.read_bytes() == b"sentinel"


def test_task8_completed_gate_cannot_be_replayed_without_recovery_journal(
    tmp_path: Path,
) -> None:
    publisher, inputs = _assets()
    targets = _targets(tmp_path)
    first = _publish(publisher, inputs, targets)
    before = {name: _safe_file_snapshot(path) for name, path in targets.items()}

    with pytest.raises(
        publisher.Task8GatePublicationError,
        match="TASK8_GATE_ALREADY_PUBLISHED",
    ):
        _publish(publisher, inputs, targets)

    assert first["gate_admitted"] is True
    after = {name: _safe_file_snapshot(path) for name, path in targets.items()}
    if after != before:
        raise AssertionError("TASK8_REPLAY_REWROTE_OUTPUT")
