"""RED-first signed, recoverable publication contract for full-strict-v26."""

from __future__ import annotations

import base64
import builtins
import hashlib
import importlib.util
import inspect
import json
import os
import subprocess
import sys
from functools import lru_cache
from pathlib import Path
from types import ModuleType

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from . import _task8_void_contract_hash_evidence as void_evidence
from . import _v26_independent_oracle as oracle

PUBLISHER = oracle.REPO_ROOT / "scripts" / "publish_task8_v26_contract_artifact_gate.py"
STAGED_ARTIFACT = oracle.STAGED_ARTIFACT
POLICY_FIXTURE = (
    oracle.REPO_ROOT
    / "tests"
    / "slice100_task8"
    / "fixtures"
    / "fdd_formula_policy_v2_hash_v1.json"
)
CANONICAL_ARTIFACT = oracle.CANONICAL_ARTIFACT
CANONICAL_FIXTURE = oracle.CANONICAL_FIXTURE
_ISOLATED_PYTHON = getattr(sys, "_base_executable", sys.executable)
_SIGNER_ID = "slice100-governance-signer-v1"
_SIGNING_DOMAIN = "task8-v26-postgres-authority-census-signing-v1"
_RUNNER_SHA256 = "34506840551bc49b3eb9c2f0b5ddcbfafd5e8af62143626873e12e283cc630b6"
_SIX_IDS = {
    "artifact_sha256": oracle.ARTIFACT_SHA256,
    "fixture_sha256": oracle.FIXTURE_SHA256,
    "full_strict_contract_hash": oracle.CONTRACT_SHA256,
    "identity_graph_sha256": oracle.GRAPH_SHA256,
    "approved_semantics_conservation_sha256": oracle.CONSERVATION_SHA256,
    "policy_binding_sha256": oracle.POLICY_BINDING_SHA256,
}
_PUBLIC_ARGS = {
    "staged_artifact_raw",
    "staged_fixture_raw",
    "signed_census_receipt_raw",
    "trusted_public_key_pem",
    "parent_artifact_raw",
    "policy_artifact_raw",
    "policy_fixture_raw",
    "reviewer_map_raw",
    "staged_artifact_path",
    "staged_fixture_path",
    "parent_artifact_path",
    "policy_artifact_path",
    "policy_fixture_path",
    "reviewer_map_path",
    "v26_artifact_path",
    "v26_fixture_path",
}


def _render(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2) + "\n").encode()


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()


def _load(path: Path, name: str) -> ModuleType:
    assert path.is_file(), f"TASK8_V26_PUBLISHER_NOT_IMPLEMENTED:{path.name}"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@lru_cache(maxsize=1)
def _exact_raw_assets() -> dict[str, bytes]:
    verifier = oracle.load_verifier()
    parent = oracle.PARENT_ARTIFACT.read_bytes()
    policy = oracle.POLICY_ARTIFACT.read_bytes()
    reviewer_map = oracle.REVIEWER_MAP.read_bytes()
    artifact = STAGED_ARTIFACT.read_bytes()
    fixture = verifier.derive_fixture_bytes(
        parent_artifact_raw=parent,
        policy_artifact_raw=policy,
        reviewer_map_raw=reviewer_map,
        v26_artifact_raw=artifact,
    )
    assert hashlib.sha256(artifact).hexdigest() == oracle.ARTIFACT_SHA256
    assert hashlib.sha256(fixture).hexdigest() == oracle.FIXTURE_SHA256
    return {
        "parent_artifact_raw": parent,
        "policy_artifact_raw": policy,
        "policy_fixture_raw": POLICY_FIXTURE.read_bytes(),
        "reviewer_map_raw": reviewer_map,
        "staged_artifact_raw": artifact,
        "staged_fixture_raw": fixture,
    }


def _database_execution() -> dict[str, object]:
    values: dict[str, object] = {
        "admin_behavior_case_count": 0,
        "admin_observation_case_count": 0,
        "application_case_execution_count": 22,
        "erasure_executor_case_execution_count": 3,
        "legal_execution_count": 6,
        "illegal_rejection_count": 11,
        "direct_sql_rejection_count": 8,
        "rejected_case_projection_count": 19,
        "rejected_case_zero_mutation_count": 19,
        "unexpected_outcome_count": 0,
        "behavior_role_match_count": 25,
        "dashboard_coverage_row_count": 100,
        "dashboard_commercial_coverage_row_count": 21,
        "dashboard_financial_coverage_row_count": 79,
        "dashboard_seed_plan_sha256": "5" * 64,
        "dashboard_seed_db_projection_sha256": "5" * 64,
        "seed_fk_closure_sha256": "5" * 64,
        "seed_native_helper_equality_sha256": "5" * 64,
        "source_freeze_sha256": "5" * 64,
        "main_dashboard_census_sha256": "5" * 64,
        "requirement_matrix_sha256": "5" * 64,
        "requirement_predicate_sha256": "5" * 64,
        "requirement_legal_case_count": 4,
        "requirement_negative_case_count": 13,
        "requirement_negative_zero_mutation_count": 13,
        "forward_lock_race_passed": True,
        "reverse_lock_race_passed": True,
        "rls_behavior_passed": True,
        "installed_tenant_rls_table_count": 69,
        "catalog_observer_application_role": True,
        "partial_authority_target_count": 19,
        "partial_authority_event_count": 57,
        "partial_authority_udu_passed": True,
        "erasure_authority_udu_passed": True,
    }
    digest_names = (
        "rejected_case_projection_sha256",
        "behavior_role_attestation_sha256",
        "rls_behavior_sha256",
        "case_result_sha256",
        "catalog_projection_sha256",
        "race_result_sha256",
        "partial_authority_udu_sha256",
        "erasure_authority_udu_sha256",
    )
    values.update(dict.fromkeys(digest_names, "5" * 64))
    values.update(
        {
            "partial_initial_catalog_sha256": "1" * 64,
            "partial_downgrade_catalog_sha256": "2" * 64,
            "partial_final_catalog_sha256": "1" * 64,
            "erasure_initial_catalog_sha256": "3" * 64,
            "erasure_downgrade_catalog_sha256": "4" * 64,
            "erasure_final_catalog_sha256": "3" * 64,
            "complete_execution_sha256": "5" * 64,
        }
    )
    return values


def _signed_receipt(
    private_key: Ed25519PrivateKey,
    public_key_pem: bytes,
    *,
    overrides: dict[str, object] | None = None,
) -> bytes:
    fixture = json.loads(_exact_raw_assets()["staged_fixture_raw"])
    payload: dict[str, object] = {
        "schema_version": "task8-v26-postgres-authority-census-payload-v1",
        "result": "PASS",
        "artifact_schema": "full-strict-v26-reviewed-preimage-v1",
        "fixture_schema": "full-strict-v26-contract-hash-fixture-v1",
        "pipeline_contract_version": "full-strict-v26",
        "artifact_sha256": _SIX_IDS["artifact_sha256"],
        "fixture_sha256": _SIX_IDS["fixture_sha256"],
        "full_strict_contract_hash": _SIX_IDS["full_strict_contract_hash"],
        "identity_graph_sha256": _SIX_IDS["identity_graph_sha256"],
        "identity_graph_counts": dict(fixture["identity_hash_dependency_graph"]["counts"]),
        "approved_semantics_conservation_sha256": _SIX_IDS[
            "approved_semantics_conservation_sha256"
        ],
        "policy_id": "fdd-formula-policy-v2",
        "policy_artifact_sha256": oracle.POLICY_ARTIFACT_SHA256,
        "policy_sha256": oracle.POLICY_SHA256,
        "reviewer_map_sha256": oracle.REVIEWER_MAP_SHA256,
        "policy_binding_sha256": _SIX_IDS["policy_binding_sha256"],
        "base_profile_hmac": "b" * 64,
        "runner_sha256": _RUNNER_SHA256,
        "case_manifest_sha256": "a" * 64,
        "database_execution": _database_execution(),
        "disposable_database_dropped": True,
    }
    if overrides:
        payload.update(overrides)
    payload_sha256 = hashlib.sha256(_canonical(payload)).hexdigest()
    public_key_sha256 = hashlib.sha256(public_key_pem).hexdigest()
    signable = [_SIGNING_DOMAIN, _SIGNER_ID, payload_sha256, public_key_sha256, payload]
    envelope = {
        "schema_version": "task8-v26-postgres-authority-census-envelope-v1",
        "signer_id": _SIGNER_ID,
        "payload": payload,
        "payload_sha256": payload_sha256,
        "public_key_sha256": public_key_sha256,
        "signature_base64": base64.b64encode(private_key.sign(_canonical(signable))).decode(),
    }
    return _render(envelope)


def _test_keypair() -> tuple[Ed25519PrivateKey, bytes]:
    private = Ed25519PrivateKey.generate()
    public = private.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return private, public


def _paths(tmp_path: Path) -> dict[str, Path]:
    stage = tmp_path / "stage"
    authorities = tmp_path / "authorities"
    contracts = tmp_path / "contracts"
    fixtures = tmp_path / "fixtures"
    for directory in (stage, authorities, contracts, fixtures):
        directory.mkdir()
    paths = {
        "staged_artifact_path": stage / "v26-artifact.staging.json",
        "staged_fixture_path": stage / "v26-fixture.staging.json",
        "parent_artifact_path": authorities / "full_strict_v21.json",
        "policy_artifact_path": authorities / "fdd_formula_policy_v2.json",
        "policy_fixture_path": authorities / "fdd_formula_policy_v2_fixture.json",
        "reviewer_map_path": authorities / "reviewer-map.md",
        "v26_artifact_path": contracts / "full_strict_v26_reviewed_preimage_v1.json",
        "v26_fixture_path": fixtures / "full_strict_v26_contract_hash_v1.json",
    }
    raw = _exact_raw_assets()
    for name in (
        "staged_artifact",
        "staged_fixture",
        "parent_artifact",
        "policy_artifact",
        "policy_fixture",
        "reviewer_map",
    ):
        paths[f"{name}_path"].write_bytes(raw[f"{name}_raw"])
    return paths


def _inputs(tmp_path: Path) -> tuple[dict[str, object], Ed25519PrivateKey]:
    private, public = _test_keypair()
    values: dict[str, object] = dict(_exact_raw_assets())
    values.update(_paths(tmp_path))
    values["trusted_public_key_pem"] = public
    values["signed_census_receipt_raw"] = _signed_receipt(private, public)
    return values, private


def _run_process_death(
    tmp_path: Path,
    values: dict[str, object],
    *,
    checkpoint: str,
    ordinal: int | None,
    label: str,
    raise_checkpoint: str | None = None,
    raise_ordinal: int | None = None,
) -> subprocess.CompletedProcess[bytes]:
    assets = tmp_path / f"child-assets-{label}"
    assets.mkdir()
    serialized: dict[str, str] = {}
    byte_names: list[str] = []
    for name, value in values.items():
        if isinstance(value, bytes):
            path = assets / f"{name}.bin"
            path.write_bytes(value)
            serialized[name] = str(path)
            byte_names.append(name)
        else:
            serialized[name] = str(value)
    child = tmp_path / f"child-{label}.py"
    child.write_text(
        f"""
import importlib.util, os, sys
from pathlib import Path
spec=importlib.util.spec_from_file_location('_v26_child_publisher', {str(PUBLISHER)!r})
module=importlib.util.module_from_spec(spec)
sys.modules[spec.name]=module
spec.loader.exec_module(module)
def checkpoint(name, ordinal=None):
    if name == {checkpoint!r} and ordinal == {ordinal!r}: os._exit(91)
    if name == {raise_checkpoint!r} and ordinal == {raise_ordinal!r}: raise RuntimeError('restore')
module._process_exit_checkpoint=checkpoint
def fixture_verifier(**kwargs): return {_SIX_IDS!r}
def receipt_verifier(**kwargs): return {{'result':'PASS', **{_SIX_IDS!r}}}
values={{}}
byte_names={tuple(byte_names)!r}
paths={serialized!r}
for name, path in paths.items():
    values[name]=Path(path).read_bytes() if name in byte_names else Path(path)
module._publish_task8_v26_gate_with_verifiers(
    **values,
    fixture_verifier=fixture_verifier,
    receipt_verifier=receipt_verifier,
)
""",
        encoding="utf-8",
    )
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return subprocess.run(
        [_ISOLATED_PYTHON, "-B", str(child)],
        env=env,
        check=False,
        capture_output=True,
    )


def _private_fixture_verifier(**kwargs: object) -> dict[str, str]:
    assert kwargs["v26_artifact_raw"] == _exact_raw_assets()["staged_artifact_raw"]
    assert kwargs["fixture_raw"] == _exact_raw_assets()["staged_fixture_raw"]
    return dict(_SIX_IDS)


def _private_receipt_verifier(**kwargs: object) -> dict[str, object]:
    receipt = json.loads(bytes(kwargs["receipt_raw"]))
    payload = receipt["payload"]
    assert {name: payload[name] for name in _SIX_IDS} == kwargs["identities"]
    return payload


def _publish_private(publisher: ModuleType, values: dict[str, object]) -> dict[str, object]:
    return publisher._publish_task8_v26_gate_with_verifiers(
        **values,
        fixture_verifier=_private_fixture_verifier,
        receipt_verifier=_private_receipt_verifier,
    )


def _bind_public_targets(
    publisher: ModuleType,
    values: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(publisher, "_CANONICAL_V26_ARTIFACT", Path(values["v26_artifact_path"]))
    monkeypatch.setattr(publisher, "_CANONICAL_V26_FIXTURE", Path(values["v26_fixture_path"]))


def _sidecars(publisher: ModuleType, values: dict[str, object]) -> list[Path]:
    artifact = Path(values["v26_artifact_path"])
    parents = {artifact.parent, Path(values["v26_fixture_path"]).parent}
    return sorted(
        path
        for parent in parents
        for path in parent.iterdir()
        if path.name.startswith(".") and "task8-v26" in path.name
    )


def _assert_no_outputs_or_sidecars(publisher: ModuleType, values: dict[str, object]) -> None:
    assert not Path(values["v26_fixture_path"]).exists()
    assert not Path(values["v26_artifact_path"]).exists()
    assert _sidecars(publisher, values) == []


def test_v26_publisher_is_new_standalone_surface_with_exact_public_api() -> None:
    publisher = _load(PUBLISHER, "_task8_v26_publisher_surface_red")
    signature = inspect.signature(publisher.publish_task8_v26_gate_atomically)
    assert set(signature.parameters) == _PUBLIC_ARGS
    assert all(
        parameter.kind is inspect.Parameter.KEYWORD_ONLY
        for parameter in signature.parameters.values()
    )
    source = PUBLISHER.read_text(encoding="utf-8")
    assert "publish_task8_contract_artifact_gate" not in source
    assert "materialize_full_strict_v24" not in source
    assert "verify_full_strict_v24" not in source
    assert "verify_full_strict_v26_fixture_independently.py" in source


def test_v26_public_composition_verifies_signature_then_publishes_fixture_first(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    publisher = _load(PUBLISHER, "_task8_v26_publisher_happy_red")
    values, _private = _inputs(tmp_path)
    public = bytes(values["trusted_public_key_pem"])
    monkeypatch.setattr(
        publisher, "_EXPECTED_CENSUS_PUBLIC_KEY_SHA256", hashlib.sha256(public).hexdigest()
    )
    _bind_public_targets(publisher, values, monkeypatch)
    checkpoints: list[tuple[str, int | None, bool, bool]] = []

    def observe(name: str, ordinal: int | None = None) -> None:
        checkpoints.append(
            (
                name,
                ordinal,
                Path(values["v26_fixture_path"]).exists(),
                Path(values["v26_artifact_path"]).exists(),
            )
        )

    monkeypatch.setattr(publisher, "_process_exit_checkpoint", observe)
    result = publisher.publish_task8_v26_gate_atomically(**values)
    assert result["created_publication_order"] == [
        "full_strict_v26_fixture",
        "full_strict_v26_artifact",
    ]
    assert result["commit_point"] == "full_strict_v26_artifact"
    assert result["activation_status"] == "INACTIVE"
    assert result["gate_admitted"] is True
    assert (
        result["receipt_raw_sha256"]
        == hashlib.sha256(bytes(values["signed_census_receipt_raw"])).hexdigest()
    )
    assert result["identity_sha256"] == _SIX_IDS
    assert Path(values["v26_fixture_path"]).read_bytes() == values["staged_fixture_raw"]
    assert Path(values["v26_artifact_path"]).read_bytes() == values["staged_artifact_raw"]
    artifact_link = next(
        row for row in checkpoints if row[:2] == ("after_target_link_before_stage_unlink", 2)
    )
    assert artifact_link[2:] == (True, True)
    fixture_link = next(
        row for row in checkpoints if row[:2] == ("after_target_link_before_stage_unlink", 1)
    )
    assert fixture_link[2:] == (True, False)
    assert _sidecars(publisher, values) == []


def test_v26_public_api_rejects_noncanonical_targets_before_any_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    publisher = _load(PUBLISHER, "_task8_v26_public_target_red")
    values, _private = _inputs(tmp_path)
    public = bytes(values["trusted_public_key_pem"])
    monkeypatch.setattr(
        publisher, "_EXPECTED_CENSUS_PUBLIC_KEY_SHA256", hashlib.sha256(public).hexdigest()
    )
    with pytest.raises(
        publisher.Task8V26GatePublicationError,
        match="^TASK8_V26_GATE_CANONICAL_TARGET_REQUIRED$",
    ):
        publisher.publish_task8_v26_gate_atomically(**values)
    _assert_no_outputs_or_sidecars(publisher, values)


def test_v26_recovery_intent_binds_receipt_raw_sha_and_all_six_ids(tmp_path: Path) -> None:
    publisher = _load(PUBLISHER, "_task8_v26_publisher_intent_red")
    values, _private = _inputs(tmp_path)
    outputs = {
        "full_strict_v26_fixture": bytes(values["staged_fixture_raw"]),
        "full_strict_v26_artifact": bytes(values["staged_artifact_raw"]),
    }
    targets = {name: Path(values[name.replace("full_strict_", "") + "_path"]) for name in ()}
    targets = {
        "full_strict_v26_fixture": Path(values["v26_fixture_path"]),
        "full_strict_v26_artifact": Path(values["v26_artifact_path"]),
    }
    intent = publisher._intent_value(
        outputs,
        targets,
        receipt_raw=bytes(values["signed_census_receipt_raw"]),
        identities=_SIX_IDS,
        authority_raw={
            "parent_v21_artifact": bytes(values["parent_artifact_raw"]),
            "fdd_formula_policy_artifact": bytes(values["policy_artifact_raw"]),
            "fdd_formula_policy_fixture": bytes(values["policy_fixture_raw"]),
            "fdd_formula_policy_review_map": bytes(values["reviewer_map_raw"]),
        },
        authority_paths={
            "parent_v21_artifact": Path(values["parent_artifact_path"]),
            "fdd_formula_policy_artifact": Path(values["policy_artifact_path"]),
            "fdd_formula_policy_fixture": Path(values["policy_fixture_path"]),
            "fdd_formula_policy_review_map": Path(values["reviewer_map_path"]),
        },
    )
    assert (
        intent["signed_census_receipt_raw_sha256"]
        == hashlib.sha256(bytes(values["signed_census_receipt_raw"])).hexdigest()
    )
    assert intent["identity_sha256"] == _SIX_IDS
    rendered = _render(intent)
    assert bytes(values["signed_census_receipt_raw"]) not in rendered
    assert b"signature_base64" not in rendered
    for path_name in ("v26_artifact_path", "v26_fixture_path", "staged_artifact_path"):
        assert str(values[path_name]).encode() not in rendered


@pytest.mark.parametrize("field", tuple(_SIX_IDS))
def test_v26_signed_receipt_wrong_identity_fails_before_any_write(
    field: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    publisher = _load(PUBLISHER, f"_task8_v26_bad_receipt_{field}")
    values, private = _inputs(tmp_path)
    public = bytes(values["trusted_public_key_pem"])
    monkeypatch.setattr(
        publisher, "_EXPECTED_CENSUS_PUBLIC_KEY_SHA256", hashlib.sha256(public).hexdigest()
    )
    _bind_public_targets(publisher, values, monkeypatch)
    values["signed_census_receipt_raw"] = _signed_receipt(
        private, public, overrides={field: "0" * 64}
    )
    with pytest.raises(
        publisher.Task8V26GatePublicationError, match="^TASK8_V26_GATE_CENSUS_RECEIPT_INVALID$"
    ):
        publisher.publish_task8_v26_gate_atomically(**values)
    _assert_no_outputs_or_sidecars(publisher, values)


@pytest.mark.parametrize("mutation", ("signature", "unknown_field", "not_pass", "not_dropped"))
def test_v26_signed_envelope_is_exact_and_fail_closed_before_sidecars(
    mutation: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    publisher = _load(PUBLISHER, f"_task8_v26_bad_envelope_{mutation}")
    values, private = _inputs(tmp_path)
    public = bytes(values["trusted_public_key_pem"])
    monkeypatch.setattr(
        publisher, "_EXPECTED_CENSUS_PUBLIC_KEY_SHA256", hashlib.sha256(public).hexdigest()
    )
    _bind_public_targets(publisher, values, monkeypatch)
    if mutation == "signature":
        envelope = json.loads(bytes(values["signed_census_receipt_raw"]))
        envelope["signature_base64"] = base64.b64encode(b"not-a-signature").decode()
        values["signed_census_receipt_raw"] = _render(envelope)
    elif mutation == "unknown_field":
        envelope = json.loads(bytes(values["signed_census_receipt_raw"]))
        envelope["unexpected"] = "private-prose"
        values["signed_census_receipt_raw"] = _render(envelope)
    else:
        override = (
            {"result": "FAILED"}
            if mutation == "not_pass"
            else {"disposable_database_dropped": False}
        )
        values["signed_census_receipt_raw"] = _signed_receipt(private, public, overrides=override)
    with pytest.raises(
        publisher.Task8V26GatePublicationError, match="^TASK8_V26_GATE_CENSUS_RECEIPT_INVALID$"
    ):
        publisher.publish_task8_v26_gate_atomically(**values)
    _assert_no_outputs_or_sidecars(publisher, values)


@pytest.mark.parametrize(
    "raw_name,path_name",
    (
        ("parent_artifact_raw", "parent_artifact_path"),
        ("policy_artifact_raw", "policy_artifact_path"),
        ("policy_fixture_raw", "policy_fixture_path"),
        ("reviewer_map_raw", "reviewer_map_path"),
        ("staged_artifact_raw", "staged_artifact_path"),
        ("staged_fixture_raw", "staged_fixture_path"),
    ),
)
def test_v26_exact_authority_and_staging_bytes_are_admitted_before_sidecars(
    raw_name: str,
    path_name: str,
    tmp_path: Path,
) -> None:
    publisher = _load(PUBLISHER, f"_task8_v26_bad_authority_{raw_name}")
    values, _private = _inputs(tmp_path)
    changed = bytes(values[raw_name]) + b"x"
    values[raw_name] = changed
    Path(values[path_name]).write_bytes(changed)
    with pytest.raises(
        publisher.Task8V26GatePublicationError, match="^TASK8_V26_GATE_INPUT_INVALID$"
    ):
        _publish_private(publisher, values)
    _assert_no_outputs_or_sidecars(publisher, values)


@pytest.mark.parametrize("canonical", (CANONICAL_ARTIFACT, CANONICAL_FIXTURE))
def test_v26_canonical_paths_are_forbidden_as_staging_inputs(
    canonical: Path,
    tmp_path: Path,
) -> None:
    publisher = _load(PUBLISHER, f"_task8_v26_canonical_stage_{canonical.name}")
    values, _private = _inputs(tmp_path)
    key = "staged_artifact_path" if canonical == CANONICAL_ARTIFACT else "staged_fixture_path"
    values[key] = canonical
    with pytest.raises(
        publisher.Task8V26GatePublicationError, match="^TASK8_V26_GATE_CANONICAL_STAGING_FORBIDDEN$"
    ):
        _publish_private(publisher, values)
    _assert_no_outputs_or_sidecars(publisher, values)


def test_v26_void_v22_through_v25_candidate_ids_and_markers_are_closed() -> None:
    publisher = _load(PUBLISHER, "_task8_v26_void_closure_red")
    assert set(publisher._VOID_CANDIDATE_SHA256) == set(void_evidence.TASK8_VOID_CONTRACT_HASHES)
    assert publisher._VOID_VERSION_MARKERS == (
        b"full-strict-v22",
        b"full-strict-v23",
        b"full-strict-v24",
        b"full-strict-v25",
    )
    for raw in (
        oracle.REPO_ROOT.joinpath(
            "contracts/full_strict_v22_reviewed_preimage_v1.json"
        ).read_bytes(),
        oracle.REPO_ROOT.joinpath(
            "contracts/full_strict_v23_reviewed_preimage_v1.json"
        ).read_bytes(),
        oracle.REPO_ROOT.joinpath(
            "contracts/full_strict_v24_reviewed_preimage_v1.json"
        ).read_bytes(),
    ):
        with pytest.raises(
            publisher.Task8V26GatePublicationError, match="^TASK8_V26_GATE_VOID_INPUT_FORBIDDEN$"
        ):
            publisher._reject_void_input(raw)


@pytest.mark.parametrize("existing", ("fixture", "artifact", "both"))
def test_v26_initial_publication_requires_both_targets_absent(
    existing: str,
    tmp_path: Path,
) -> None:
    publisher = _load(PUBLISHER, f"_task8_v26_target_absence_{existing}")
    values, _private = _inputs(tmp_path)
    if existing in {"fixture", "both"}:
        Path(values["v26_fixture_path"]).write_bytes(bytes(values["staged_fixture_raw"]))
    if existing in {"artifact", "both"}:
        Path(values["v26_artifact_path"]).write_bytes(bytes(values["staged_artifact_raw"]))
    code = (
        "TASK8_V26_GATE_ALREADY_PUBLISHED"
        if existing == "both"
        else "TASK8_V26_GATE_TARGET_ALREADY_EXISTS"
    )
    with pytest.raises(publisher.Task8V26GatePublicationError, match=f"^{code}$"):
        _publish_private(publisher, values)
    assert _sidecars(publisher, values) == []


def test_v26_completed_replay_revalidates_receipt_and_exact_outputs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    publisher = _load(PUBLISHER, "_task8_v26_completed_replay_red")
    values, _private = _inputs(tmp_path)
    _publish_private(publisher, values)
    _bind_public_targets(publisher, values, monkeypatch)
    bad = dict(values)
    bad["signed_census_receipt_raw"] = b"private invalid receipt"
    with pytest.raises(
        publisher.Task8V26GatePublicationError, match="^TASK8_V26_GATE_CENSUS_RECEIPT_INVALID$"
    ):
        publisher.publish_task8_v26_gate_atomically(**bad)
    with pytest.raises(
        publisher.Task8V26GatePublicationError, match="^TASK8_V26_GATE_ALREADY_PUBLISHED$"
    ):
        _publish_private(publisher, values)
    assert Path(values["v26_fixture_path"]).read_bytes() == values["staged_fixture_raw"]
    assert Path(values["v26_artifact_path"]).read_bytes() == values["staged_artifact_raw"]
    assert _sidecars(publisher, values) == []


@pytest.mark.parametrize(
    "checkpoint,ordinal",
    (
        ("after_lock_acquired_before_journal", None),
        ("after_journal_temp_fsync_before_link", None),
        ("after_stage_fsync_before_target_link", 1),
        ("after_target_link_before_stage_unlink", 1),
        ("after_stage_fsync_before_target_link", 2),
        ("after_target_link_before_stage_unlink", 2),
        ("after_journal_removal_before_lock_cleanup", None),
    ),
)
def test_v26_recovers_every_real_process_death_state(
    checkpoint: str,
    ordinal: int | None,
    tmp_path: Path,
) -> None:
    publisher = _load(PUBLISHER, f"_task8_v26_recover_{checkpoint}_{ordinal}")
    values, _private = _inputs(tmp_path)
    assets = tmp_path / "child-assets"
    assets.mkdir()
    serialized: dict[str, str] = {}
    for name, value in values.items():
        if isinstance(value, bytes):
            path = assets / f"{name}.bin"
            path.write_bytes(value)
            serialized[name] = str(path)
        else:
            serialized[name] = str(value)
    child = tmp_path / "child.py"
    child.write_text(
        f"""
import importlib.util, os, sys
from pathlib import Path
spec=importlib.util.spec_from_file_location('_v26_child_publisher', {str(PUBLISHER)!r})
module=importlib.util.module_from_spec(spec)
sys.modules[spec.name]=module
spec.loader.exec_module(module)
def checkpoint(name, ordinal=None):
    if name == {checkpoint!r} and ordinal == {ordinal!r}: os._exit(91)
module._process_exit_checkpoint=checkpoint
def fixture_verifier(**kwargs): return {_SIX_IDS!r}
def receipt_verifier(**kwargs): return {{'result':'PASS', **{_SIX_IDS!r}}}
values={{}}
byte_names={tuple(name for name, value in values.items() if isinstance(value, bytes))!r}
paths={serialized!r}
for name, path in paths.items():
    values[name]=Path(path).read_bytes() if name in byte_names else Path(path)
module._publish_task8_v26_gate_with_verifiers(
    **values,
    fixture_verifier=fixture_verifier,
    receipt_verifier=receipt_verifier,
)
""",
        encoding="utf-8",
    )
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    completed = subprocess.run([_ISOLATED_PYTHON, "-B", str(child)], env=env, check=False)
    assert completed.returncode == 91
    artifact_exists = Path(values["v26_artifact_path"]).exists()
    # The artifact is already the sole commit point after its link or journal removal.
    if (
        checkpoint != "after_target_link_before_stage_unlink" or ordinal != 2
    ) and checkpoint != "after_journal_removal_before_lock_cleanup":
        assert not artifact_exists
    if checkpoint == "after_journal_removal_before_lock_cleanup":
        with pytest.raises(
            publisher.Task8V26GatePublicationError, match="^TASK8_V26_GATE_ALREADY_PUBLISHED$"
        ):
            _publish_private(publisher, values)
    else:
        result = _publish_private(publisher, values)
        assert result["commit_point"] == "full_strict_v26_artifact"
    assert Path(values["v26_fixture_path"]).read_bytes() == values["staged_fixture_raw"]
    assert Path(values["v26_artifact_path"]).read_bytes() == values["staged_artifact_raw"]
    assert _sidecars(publisher, values) == []


def test_v26_recovers_process_death_during_dead_owner_lock_takeover(
    tmp_path: Path,
) -> None:
    publisher = _load(PUBLISHER, "_task8_v26_takeover_recovery_red")
    values, _private = _inputs(tmp_path)
    first = _run_process_death(
        tmp_path,
        values,
        checkpoint="after_lock_acquired_before_journal",
        ordinal=None,
        label="first-owner",
    )
    assert first.returncode == 91, first.stderr.decode(errors="replace")
    second = _run_process_death(
        tmp_path,
        values,
        checkpoint="after_takeover_fsync_before_replace",
        ordinal=None,
        label="takeover-owner",
    )
    assert second.returncode == 91, second.stderr.decode(errors="replace")
    result = _publish_private(publisher, values)
    assert result["commit_point"] == "full_strict_v26_artifact"
    assert Path(values["v26_fixture_path"]).read_bytes() == values["staged_fixture_raw"]
    assert Path(values["v26_artifact_path"]).read_bytes() == values["staged_artifact_raw"]
    assert _sidecars(publisher, values) == []


def test_v26_recovers_process_death_during_old_lock_restoration(
    tmp_path: Path,
) -> None:
    publisher = _load(PUBLISHER, "_task8_v26_restore_recovery_red")
    values, _private = _inputs(tmp_path)
    first = _run_process_death(
        tmp_path,
        values,
        checkpoint="after_lock_acquired_before_journal",
        ordinal=None,
        label="restore-first-owner",
    )
    assert first.returncode == 91, first.stderr.decode(errors="replace")
    second = _run_process_death(
        tmp_path,
        values,
        checkpoint="after_restore_fsync_before_replace",
        ordinal=None,
        label="restore-takeover-owner",
        raise_checkpoint="after_stage_fsync_before_target_link",
        raise_ordinal=1,
    )
    assert second.returncode == 91, second.stderr.decode(errors="replace")
    result = _publish_private(publisher, values)
    assert result["commit_point"] == "full_strict_v26_artifact"
    assert Path(values["v26_fixture_path"]).read_bytes() == values["staged_fixture_raw"]
    assert Path(values["v26_artifact_path"]).read_bytes() == values["staged_artifact_raw"]
    assert _sidecars(publisher, values) == []


def test_v26_stage_mutation_after_fsync_never_reaches_target(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    publisher = _load(PUBLISHER, "_task8_v26_stage_toctou_red")
    values, _private = _inputs(tmp_path)

    def mutate(name: str, ordinal: int | None = None) -> None:
        if name == "after_stage_fsync_before_target_link" and ordinal == 1:
            stages = list(Path(values["v26_fixture_path"]).parent.glob(".*.task8-v26-stage.*.1"))
            assert len(stages) == 1
            stages[0].write_bytes(b"changed-private-stage")

    monkeypatch.setattr(publisher, "_process_exit_checkpoint", mutate)
    with pytest.raises(
        publisher.Task8V26GatePublicationError, match="^TASK8_V26_GATE_RECOVERY_STAGE_INVALID$"
    ):
        _publish_private(publisher, values)
    _assert_no_outputs_or_sidecars(publisher, values)


def test_v26_stage_replacement_in_final_check_link_window_is_removed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    publisher = _load(PUBLISHER, "_task8_v26_stage_link_race_red")
    values, _private = _inputs(tmp_path)
    real_link = publisher.os.link

    def replace_source_at_link(source: object, target: object) -> None:
        source_path = Path(source)
        target_path = Path(target)
        if ".task8-v26-stage." in source_path.name and target_path == Path(
            values["v26_fixture_path"]
        ):
            replacement = source_path.with_name(f"{source_path.name}.attacker")
            replacement.write_bytes(b"private-replacement-bytes")
            real_link(replacement, target_path)
            replacement.unlink()
            return
        real_link(source_path, target_path)

    monkeypatch.setattr(publisher.os, "link", replace_source_at_link)
    with pytest.raises(
        publisher.Task8V26GatePublicationError,
        match="^TASK8_V26_GATE_LINK_IDENTITY_INVALID$",
    ):
        _publish_private(publisher, values)
    _assert_no_outputs_or_sidecars(publisher, values)


def test_v26_ordinary_error_after_artifact_commit_finishes_without_rollback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    publisher = _load(PUBLISHER, "_task8_v26_postcommit_error_red")
    values, _private = _inputs(tmp_path)

    def interrupt(name: str, ordinal: int | None = None) -> None:
        if name == "after_target_link_before_stage_unlink" and ordinal == 2:
            raise RuntimeError("private-post-commit-canary")

    monkeypatch.setattr(publisher, "_process_exit_checkpoint", interrupt)
    result = _publish_private(publisher, values)
    assert result["commit_point"] == "full_strict_v26_artifact"
    assert Path(values["v26_fixture_path"]).read_bytes() == values["staged_fixture_raw"]
    assert Path(values["v26_artifact_path"]).read_bytes() == values["staged_artifact_raw"]
    assert _sidecars(publisher, values) == []


def test_v26_rejects_symlink_and_hardlink_aliases_before_publication(tmp_path: Path) -> None:
    publisher = _load(PUBLISHER, "_task8_v26_alias_red")
    values, _private = _inputs(tmp_path)
    alias = tmp_path / "stage" / "artifact-alias.json"
    try:
        os.link(Path(values["staged_fixture_path"]), alias)
    except OSError as exc:
        pytest.skip(f"hardlinks unavailable: {exc.__class__.__name__}")
    values["staged_artifact_path"] = alias
    values["staged_artifact_raw"] = bytes(values["staged_fixture_raw"])
    with pytest.raises(
        publisher.Task8V26GatePublicationError, match="^TASK8_V26_GATE_FILE_IDENTITY_ALIAS$"
    ):
        _publish_private(publisher, values)
    _assert_no_outputs_or_sidecars(publisher, values)


def test_v26_completed_replay_rejects_external_hardlinked_targets(tmp_path: Path) -> None:
    publisher = _load(PUBLISHER, "_task8_v26_completed_hardlink_red")
    values, _private = _inputs(tmp_path)
    external_fixture = tmp_path / "external-fixture.json"
    external_artifact = tmp_path / "external-artifact.json"
    external_fixture.write_bytes(bytes(values["staged_fixture_raw"]))
    external_artifact.write_bytes(bytes(values["staged_artifact_raw"]))
    try:
        os.link(external_fixture, Path(values["v26_fixture_path"]))
        os.link(external_artifact, Path(values["v26_artifact_path"]))
    except OSError as exc:
        pytest.skip(f"hardlinks unavailable: {exc.__class__.__name__}")
    with pytest.raises(
        publisher.Task8V26GatePublicationError,
        match="^TASK8_V26_GATE_FILE_IDENTITY_ALIAS$",
    ):
        _publish_private(publisher, values)
    assert _sidecars(publisher, values) == []


def test_v26_owned_namespace_rejects_reparse_alias_that_resolves_to_expected_stage(
    tmp_path: Path,
) -> None:
    publisher = _load(PUBLISHER, "_task8_v26_namespace_reparse_red")
    values, _private = _inputs(tmp_path)
    token = "a" * 64
    targets = {
        "full_strict_v26_fixture": Path(values["v26_fixture_path"]),
        "full_strict_v26_artifact": Path(values["v26_artifact_path"]),
    }
    journal = publisher._journal_path(targets["full_strict_v26_artifact"])
    lock = publisher._lock_path(targets["full_strict_v26_artifact"])
    expected_stage = publisher._stage_path(targets["full_strict_v26_fixture"], token, 1)
    expected_stage.write_bytes(bytes(values["staged_fixture_raw"]))
    alias = expected_stage.with_name(
        f".{targets['full_strict_v26_fixture'].name}.task8-v26-stage.{'b' * 64}.1"
    )
    try:
        os.symlink(expected_stage, alias)
    except OSError as exc:
        pytest.skip(f"symlinks unavailable: {exc.__class__.__name__}")
    with pytest.raises(
        publisher.Task8V26GatePublicationError,
        match="^TASK8_V26_GATE_SIDECAR_COLLISION$",
    ):
        publisher._owned_namespace(
            targets=targets,
            journal_path=journal,
            lock_path=lock,
            owner_token=token,
        )


def test_v26_sidecars_are_hash_only_and_never_durable_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    publisher = _load(PUBLISHER, "_task8_v26_sidecar_privacy_red")
    values, _private = _inputs(tmp_path)

    def stop(name: str, ordinal: int | None = None) -> None:
        if name == "after_stage_fsync_before_target_link" and ordinal == 1:
            raise SystemExit(77)

    monkeypatch.setattr(publisher, "_process_exit_checkpoint", stop)
    with pytest.raises(SystemExit, match="77"):
        _publish_private(publisher, values)
    assert not Path(values["v26_artifact_path"]).exists()
    observed = _sidecars(publisher, values)
    assert observed
    private_needles = [
        bytes(values["signed_census_receipt_raw"]),
        str(values["v26_artifact_path"]).encode(),
        str(values["staged_artifact_path"]).encode(),
        b"signature_base64",
    ]
    for path in observed:
        raw = path.read_bytes()
        assert all(needle not in raw for needle in private_needles)


def test_v26_validation_loads_independent_verifier_without_bytecode_cache_writes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    publisher = _load(PUBLISHER, "_task8_v26_no_bytecode_red")
    values, _private = _inputs(tmp_path)
    public = bytes(values["trusted_public_key_pem"])
    monkeypatch.setattr(
        publisher, "_EXPECTED_CENSUS_PUBLIC_KEY_SHA256", hashlib.sha256(public).hexdigest()
    )
    cache_root = PUBLISHER.parent / "__pycache__"

    def snapshot() -> dict[str, tuple[int, int]]:
        if not cache_root.exists():
            return {}
        return {
            path.name: (path.stat().st_size, path.stat().st_mtime_ns)
            for path in cache_root.iterdir()
            if "verify_full_strict_v26" in path.name
        }

    before = snapshot()
    redirected = tmp_path / "redirected-pycache"
    import_states: list[bool] = []
    real_import = builtins.__import__

    def observe_import(*args: object, **kwargs: object) -> object:
        import_states.append(sys.dont_write_bytecode)
        return real_import(*args, **kwargs)

    with monkeypatch.context() as scoped:
        scoped.setattr(sys, "dont_write_bytecode", False)
        scoped.setattr(sys, "pycache_prefix", str(redirected))
        scoped.setattr(builtins, "__import__", observe_import)
        publisher._validate_v26_fixture_composition(
            parent_artifact_raw=bytes(values["parent_artifact_raw"]),
            policy_artifact_raw=bytes(values["policy_artifact_raw"]),
            reviewer_map_raw=bytes(values["reviewer_map_raw"]),
            v26_artifact_raw=bytes(values["staged_artifact_raw"]),
            fixture_raw=bytes(values["staged_fixture_raw"]),
        )
    assert import_states and all(import_states)
    assert not redirected.exists() or not any(redirected.rglob("*"))
    assert snapshot() == before


def test_v26_unexpected_exception_is_code_only_cause_free_and_zero_write(
    tmp_path: Path,
) -> None:
    publisher = _load(PUBLISHER, "_task8_v26_safe_failure_red")
    values, _private = _inputs(tmp_path)
    private_detail = "C:/private/company/name.xlsx"

    def explode(**_kwargs: object) -> object:
        raise RuntimeError(private_detail)

    with pytest.raises(publisher.Task8V26GatePublicationError) as captured:
        publisher._publish_task8_v26_gate_with_verifiers(
            **values,
            fixture_verifier=explode,
            receipt_verifier=_private_receipt_verifier,
        )
    assert str(captured.value) == "TASK8_V26_GATE_INTERNAL_FAILURE"
    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None
    assert private_detail not in repr(captured.value)
    _assert_no_outputs_or_sidecars(publisher, values)


def test_v26_injected_stable_looking_code_is_mapped_to_internal_failure(
    tmp_path: Path,
) -> None:
    publisher = _load(PUBLISHER, "_task8_v26_closed_code_red")
    values, _private = _inputs(tmp_path)

    def inject(**_kwargs: object) -> object:
        raise publisher.Task8V26GatePublicationError("TASK8_V26_GATE_PRIVATE_CANARY")

    with pytest.raises(publisher.Task8V26GatePublicationError) as captured:
        publisher._publish_task8_v26_gate_with_verifiers(
            **values,
            fixture_verifier=inject,
            receipt_verifier=_private_receipt_verifier,
        )
    assert str(captured.value) == "TASK8_V26_GATE_INTERNAL_FAILURE"
    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None
    _assert_no_outputs_or_sidecars(publisher, values)


def test_v26_canonical_outputs_remain_absent_and_runtime_nonselectable() -> None:
    assert not CANONICAL_ARTIFACT.exists()
    assert not CANONICAL_FIXTURE.exists()
    lock = json.loads((oracle.REPO_ROOT / "contracts" / "contract_lock.json").read_text())
    assert "contracts/full_strict_v26_reviewed_preimage_v1.json" not in set(lock["files"])
    artifact = json.loads(STAGED_ARTIFACT.read_text())
    assert artifact["metadata"]["activation_status"] == "INACTIVE"
    assert artifact["activation_assertions"]["runtime_selectable"] is False
