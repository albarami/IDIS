"""RED gate for the one-byte v7 SQL fix and database-executability census."""

from __future__ import annotations

import base64
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import zlib
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
)
from sqlalchemy.exc import DBAPIError
from tests.slice100_task2b._support import disposable_postgres
from tests.slice100_task7 import test_sql_authority_guards_postgres_red as guards

_REPO_ROOT = Path(__file__).resolve().parents[2]
_V6_ARTIFACT = _REPO_ROOT / "contracts" / "full_strict_v6_reviewed_preimage_v1.json"
_V7_ARTIFACT = _REPO_ROOT / "contracts" / "full_strict_v7_reviewed_preimage_v1.json"
_V7_MATERIALIZER = _REPO_ROOT / "scripts" / "materialize_full_strict_v7_preimage.py"
_V7_GENERATOR = _REPO_ROOT / "scripts" / "generate_task7_v7_contract_fixture.py"
_V7_CENSUS = _REPO_ROOT / "scripts" / "run_task7_sql_authority_census.py"
_V7_SELECTOR_EMITTER = _REPO_ROOT / "scripts" / "generate_task7_v7_producer_selector_module.py"
_V7_FIXTURE = (
    _REPO_ROOT / "tests" / "slice100_task7" / "fixtures" / "full_strict_v7_contract_hash_v1.json"
)

_GRADING_AUTHORITY_ID = "grading-dispatch-and-consistency-v1"
_UNCHANGED_SECTIONS = (
    "canonicalization_contract",
    "tuple_schema_registry",
    "uuid_namespace_registry",
    "schema_registry",
    "semantic_step_registry",
    "current_ddl_registry",
    "baseline_parent_authority_registry",
    "future_native_authority_registry",
    "future_reference_authority_registry",
    "future_child_authority_registry",
    "erasure_execution_registry",
    "provider_registry",
    "rights_index_policy_registry",
    "role_registry",
    "deliverable_registry",
    "producer_selector_registry",
    "consumer_selector_registry",
    "applicability_registry",
    "edge_rule_registry",
    "usage_route_registry",
    "usage_extractor_registry",
    "erasure_baseline",
    "erasure_materialized",
)


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _grading_row(artifact: dict[str, Any]) -> dict[str, Any]:
    rows = artifact["sql_authority_registry"]["rows"]
    matches = [row for row in rows if row["authority_id"] == _GRADING_AUTHORITY_ID]
    assert len(matches) == 1
    row = matches[0]
    assert row["authority_ordinal"] == 8
    return row


def _first_grading_body(artifact: dict[str, Any]) -> bytes:
    encoded = _grading_row(artifact)["body_sql_utf8_hex_tuple"][0]
    return bytes.fromhex(encoded)


def _load_module(path: Path, name: str) -> ModuleType:
    assert path.is_file(), f"required module is absent: {path}"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def test_v6_grading_body_reproduces_exact_postgres_42601_at_then(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Characterize the reviewed v6 defect through real PostgreSQL."""

    monkeypatch.setattr(guards, "_ARTIFACT", _V6_ARTIFACT)
    with disposable_postgres() as database, pytest.raises(DBAPIError) as captured:
        guards._install(
            database,
            _GRADING_AUTHORITY_ID,
            guards._GRADING_SCHEMA,
        )

    original = captured.value.orig
    sqlstate = getattr(original, "sqlstate", None) or getattr(
        original,
        "pgcode",
        None,
    )
    assert sqlstate == "42601"
    rendered = str(original).upper()
    assert "MISMATCHED PARENTHESES" in rendered
    body = _first_grading_body(_load(_V6_ARTIFACT))
    first_then = body.index(b"THEN RAISE")
    assert body[first_then - 4 : first_then] == b">1) "
    assert body[first_then - 1 : first_then] == b" "


def test_v7_assets_exist_only_after_red_gate_is_satisfied() -> None:
    assert _V7_MATERIALIZER.is_file()
    assert _V7_GENERATOR.is_file()
    assert _V7_CENSUS.is_file()
    assert _V7_SELECTOR_EMITTER.is_file()
    assert _V7_ARTIFACT.is_file()
    assert not _V7_FIXTURE.exists()


def test_v6_to_v7_exact_diff_is_one_parenthesis_plus_dependent_metadata() -> None:
    v6 = _load(_V6_ARTIFACT)
    v7 = _load(_V7_ARTIFACT)

    assert tuple(v7) == tuple(v6)
    for section in _UNCHANGED_SECTIONS:
        assert v7[section] == v6[section], section

    old_rows = v6["sql_authority_registry"]["rows"]
    new_rows = v7["sql_authority_registry"]["rows"]
    assert len(old_rows) == len(new_rows) == 17
    for ordinal, (old_row, new_row) in enumerate(zip(old_rows, new_rows, strict=True)):
        if ordinal != 8:
            assert new_row == old_row, ordinal
            continue
        old_body = bytes.fromhex(old_row["body_sql_utf8_hex_tuple"][0])
        new_body = bytes.fromhex(new_row["body_sql_utf8_hex_tuple"][0])
        insertion = old_body.index(b"THEN RAISE")
        assert new_body == old_body[:insertion] + b")" + old_body[insertion:]
        assert {
            key: value
            for key, value in new_row.items()
            if key not in {"body_sql_utf8_hex_tuple", "expected_sha256"}
        } == {
            key: value
            for key, value in old_row.items()
            if key not in {"body_sql_utf8_hex_tuple", "expected_sha256"}
        }
        assert new_row["body_sql_utf8_hex_tuple"][1:] == (old_row["body_sql_utf8_hex_tuple"][1:])
        assert new_row["expected_sha256"] != old_row["expected_sha256"]

    old_registry = v6["sql_authority_registry"]
    new_registry = v7["sql_authority_registry"]
    assert new_registry["expected_sha256"] != old_registry["expected_sha256"]

    variable_metadata = {
        "artifact_schema",
        "pipeline_contract_version",
        "reviewed_design_sha256",
        "reviewed_design_utf8_hex",
    }
    assert {
        key: value for key, value in v7["metadata"].items() if key not in variable_metadata
    } == {key: value for key, value in v6["metadata"].items() if key not in variable_metadata}
    assert v7["metadata"]["artifact_schema"] == ("full-strict-v7-reviewed-preimage-v1")
    assert v7["metadata"]["pipeline_contract_version"] == "full-strict-v7"

    old_recipes = v6["digest_recipe_registry"]["rows"]
    new_recipes = v7["digest_recipe_registry"]["rows"]
    assert new_recipes[:5] == old_recipes[:5]
    assert new_recipes[6:] == old_recipes[6:]
    assert new_recipes[5] == [
        "pipeline_contract_hash",
        '["pipeline-contract-v7","full-strict-v7",'
        "reviewed_preimage_artifact_hash,ORDERED[component_hashes]]",
    ]
    assert bytes.fromhex(v7["digest_recipe_registry"]["reviewed_literal_utf8_hex"]) == json.dumps(
        new_recipes,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")

    assert v7["activation_assertions"] == v6["activation_assertions"]


def test_v7_fixture_write_fails_closed_without_postgres_census_receipt(
    tmp_path: Path,
) -> None:
    output = tmp_path / "fixture.json"
    completed = subprocess.run(
        [
            sys.executable,
            str(_V7_GENERATOR),
            "--artifact",
            str(_V7_ARTIFACT),
            "--output",
            str(output),
        ],
        cwd=_REPO_ROOT,
        env={**os.environ, "PYTHONPATH": str(_REPO_ROOT / "src")},
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 2
    assert "TASK7_V7_POSTGRES_CENSUS_RECEIPT_REQUIRED" in completed.stderr
    assert not output.exists()


def test_v7_generator_exposes_invariant_producer_selector_projection() -> None:
    generator = _load_module(_V7_GENERATOR, "task7_v7_generator_selector_projection")
    artifact = _load(_V7_ARTIFACT)

    projection = generator.derive_producer_selector_authority(_V7_ARTIFACT.read_bytes())

    assert projection == {
        "schema_version": "task7-producer-selector-authority-v1",
        "row_count": 48,
        "rows": artifact["producer_selector_registry"]["rows"],
        "expected_sha256": ("3b23e5bfdee6fe78762412eec2e37430559f00833a698b17c14809876ca2d4f4"),
    }
    assert (
        hashlib.sha256(
            json.dumps(
                projection["rows"],
                allow_nan=False,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()
        != projection["expected_sha256"]
    )


def test_v7_selector_emitter_reproduces_admitted_projection_exclusively(
    tmp_path: Path,
) -> None:
    fixture_path = _write_signed_test_fixture(
        tmp_path,
        module_name="task7_v7_generator_for_selector_fixture",
    )
    output = tmp_path / "task7_producer_selectors_generated.py"
    command = [
        sys.executable,
        str(_V7_SELECTOR_EMITTER),
        "--artifact",
        str(_V7_ARTIFACT),
        "--fixture",
        str(fixture_path),
        "--output",
        str(output),
    ]
    first = subprocess.run(
        command,
        cwd=_REPO_ROOT,
        env={**os.environ, "PYTHONPATH": str(_REPO_ROOT / "src")},
        capture_output=True,
        text=True,
        check=False,
    )
    assert first.returncode == 0, first.stderr

    generated = _load_module(output, "task7_v7_generated_selector_fixture")
    projection = _load_module(
        _V7_GENERATOR,
        "task7_v7_generator_for_emitted_selector",
    ).derive_producer_selector_authority(_V7_ARTIFACT.read_bytes())
    fixture = _load(fixture_path)
    canonical_rows = json.dumps(
        projection["rows"],
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")

    assert zlib.decompress(generated.PRODUCER_SELECTOR_CANONICAL_JSON_ZLIB) == canonical_rows
    assert generated.PRODUCER_SELECTOR_ROW_COUNT == 48
    assert (projection["expected_sha256"]) == generated.PRODUCER_SELECTOR_REGISTRY_SHA256
    assert (fixture["FULL_STRICT_V7_CONTRACT_HASH"]) == generated.PIPELINE_CONTRACT_HASH
    assert fixture["artifact_sha256"] == generated.SOURCE_ARTIFACT_SHA256

    second = subprocess.run(
        command,
        cwd=_REPO_ROOT,
        env={**os.environ, "PYTHONPATH": str(_REPO_ROOT / "src")},
        capture_output=True,
        text=True,
        check=False,
    )
    assert second.returncode == 2
    assert "TASK7_V7_SELECTOR_OUTPUT_ALREADY_EXISTS" in second.stderr


def test_v7_census_contract_covers_all_registry_authority_bytes() -> None:
    census = _load_module(_V7_CENSUS, "task7_v7_postgres_census_contract")
    artifact = _load(_V7_ARTIFACT)

    inventory = census.derive_authority_inventory(artifact)

    assert inventory["authority_row_count"] == 17
    assert inventory["check_authority_group_count"] == 3
    assert inventory["check_expression_count"] == 5
    assert inventory["function_trigger_group_count"] == 14
    assert inventory["create_function_count"] == 14
    assert inventory["create_trigger_count"] == 81
    assert (
        inventory["authority_registry_sha256"]
        == (artifact["sql_authority_registry"]["expected_sha256"])
    )
    assert [row["authority_ordinal"] for row in inventory["authorities"]] == (list(range(17)))


def _signed_test_census_receipt(
    *,
    generator: ModuleType,
    artifact_raw: bytes,
    private_key: Ed25519PrivateKey,
    public_key_raw: bytes,
) -> dict[str, Any]:
    artifact = generator._decode_artifact(artifact_raw)
    inventory = generator.derive_authority_inventory(artifact)
    payload = {
        "schema_version": "task7-sql-authority-postgres-census-v1",
        "result": "PASS",
        "artifact_schema": "full-strict-v7-reviewed-preimage-v1",
        "pipeline_contract_version": "full-strict-v7",
        "artifact_sha256": hashlib.sha256(artifact_raw).hexdigest(),
        "authority_inventory": inventory,
        "database_execution": {
            "database_product": "PostgreSQL",
            "server_version_num": 160010,
            "disposable_database_count": 17,
            "executed_authority_group_count": 17,
            "executed_statement_count": 115,
            "database_rejection_count": 0,
        },
    }
    canonical = generator._canonical_bytes
    payload_sha256 = hashlib.sha256(canonical(payload)).hexdigest()
    public_key_sha256 = hashlib.sha256(public_key_raw).hexdigest()
    signable = [
        "task7-sql-authority-postgres-census-signable-v1",
        "task7-test-signer-v1",
        payload_sha256,
        public_key_sha256,
        payload,
    ]
    return {
        "schema_version": ("task7-sql-authority-postgres-census-envelope-v1"),
        "signer_id": "task7-test-signer-v1",
        "payload": payload,
        "payload_sha256": payload_sha256,
        "public_key_sha256": public_key_sha256,
        "signature_base64": base64.b64encode(private_key.sign(canonical(signable))).decode("ascii"),
    }


def _write_signed_test_fixture(tmp_path: Path, *, module_name: str) -> Path:
    generator = _load_module(_V7_GENERATOR, module_name)
    artifact_raw = _V7_ARTIFACT.read_bytes()
    private_key = Ed25519PrivateKey.generate()
    public_key_raw = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    receipt = _signed_test_census_receipt(
        generator=generator,
        artifact_raw=artifact_raw,
        private_key=private_key,
        public_key_raw=public_key_raw,
    )
    receipt_path = tmp_path / f"{module_name}.census.json"
    public_key_path = tmp_path / f"{module_name}.public.pem"
    output_path = tmp_path / f"{module_name}.fixture.json"
    receipt_path.write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    public_key_path.write_bytes(public_key_raw)
    completed = subprocess.run(
        [
            sys.executable,
            str(_V7_GENERATOR),
            "--artifact",
            str(_V7_ARTIFACT),
            "--authority-census-receipt",
            str(receipt_path),
            "--trusted-public-key",
            str(public_key_path),
            "--output",
            str(output_path),
        ],
        cwd=_REPO_ROOT,
        env={**os.environ, "PYTHONPATH": str(_REPO_ROOT / "src")},
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    return output_path


def test_v7_fixture_write_requires_and_verifies_artifact_bound_census(
    tmp_path: Path,
) -> None:
    generator = _load_module(
        _V7_GENERATOR,
        "task7_v7_generator_signed_census",
    )
    artifact_raw = _V7_ARTIFACT.read_bytes()
    private_key = Ed25519PrivateKey.generate()
    public_key_raw = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    receipt = _signed_test_census_receipt(
        generator=generator,
        artifact_raw=artifact_raw,
        private_key=private_key,
        public_key_raw=public_key_raw,
    )
    receipt_path = tmp_path / "census.json"
    public_key_path = tmp_path / "public.pem"
    output_path = tmp_path / "fixture.json"
    receipt_path.write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    public_key_path.write_bytes(public_key_raw)

    completed = subprocess.run(
        [
            sys.executable,
            str(_V7_GENERATOR),
            "--artifact",
            str(_V7_ARTIFACT),
            "--authority-census-receipt",
            str(receipt_path),
            "--trusted-public-key",
            str(public_key_path),
            "--output",
            str(output_path),
        ],
        cwd=_REPO_ROOT,
        env={**os.environ, "PYTHONPATH": str(_REPO_ROOT / "src")},
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert json.loads(output_path.read_text(encoding="utf-8")) == (
        generator.derive_contract_bytes(artifact_raw)
    )


def test_v7_census_signature_or_artifact_tamper_is_rejected(
    tmp_path: Path,
) -> None:
    generator = _load_module(
        _V7_GENERATOR,
        "task7_v7_generator_census_tamper",
    )
    artifact_raw = _V7_ARTIFACT.read_bytes()
    private_key = Ed25519PrivateKey.generate()
    public_key_raw = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    receipt = _signed_test_census_receipt(
        generator=generator,
        artifact_raw=artifact_raw,
        private_key=private_key,
        public_key_raw=public_key_raw,
    )
    receipt["payload"]["database_execution"]["executed_statement_count"] = 114
    tampered_raw = (json.dumps(receipt, ensure_ascii=False, indent=2) + "\n").encode("utf-8")

    with pytest.raises(
        ValueError,
        match="TASK7_V7_POSTGRES_CENSUS_RECEIPT_INVALID",
    ):
        generator.verify_postgres_census_receipt(
            receipt_raw=tampered_raw,
            artifact_raw=artifact_raw,
            trusted_public_key_raw=public_key_raw,
        )
