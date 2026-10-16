"""RED-first exact retained-function restoration authority for v17."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[2]
_V16_ARTIFACT = _REPO_ROOT / "contracts" / "full_strict_v16_reviewed_preimage_v1.json"
_V17_ARTIFACT = _REPO_ROOT / "contracts" / "full_strict_v17_reviewed_preimage_v1.json"
_SOURCE_SHA512 = {
    "src/idis/persistence/migrations/versions/0031_strict_preflight_proofs.py": (
        "a2d2f3da6e35505d7b8681a0a722dcfc4b1d486f0325193541c4a571537fae3"
        "b7abd175aeed629b4bbb1de54d40a29b8e0933f3e01cf4bbe1f65411c80e17402"
    ),
    "src/idis/persistence/migrations/versions/0036_durable_methodology_selection.py": (
        "679e84f8a9d4378178a4f076bbabddef40a2bbcb7ddcd11a0e9e7b4073e8d1e04"
        "fa2b2cb665097248bf9cd0fbf68d13b326291cbe393d355abcfce3edf891904"
    ),
    "src/idis/persistence/migrations/versions/0037_durable_methodology_extraction.py": (
        "de166216d565872b88c84d7d5abaff721b157e358e7c12110b0cb95d1468ec4b"
        "811371bd955ea584df754b4f3d4452b9fb2c6370f74cf2d83a9d0c2d61aab232"
    ),
}

_EXPECTED = (
    (
        "public.idis_prevent_strict_preflight_proof_mutation()",
        "src/idis/persistence/migrations/versions/0031_strict_preflight_proofs.py",
        4_261,
        "4d48af69c18fb948c5200b92998063be5a2b34305c4af16201e1d5d2c28f8484",
        4_147,
        "b2a1ab9b247bdf45a7028584fc8adb722f4672990698e7d9780700cf9704498e",
    ),
    (
        "public.idis_task5_selection_guard()",
        "src/idis/persistence/migrations/versions/0036_durable_methodology_selection.py",
        1_982,
        "11d57d2867f6de27c2f74e6e947c08df1a71017cbac8262cce777ac4ebe1f76a",
        1_860,
        "6dbb745f2ce3e2d81e422e8575fe66d07452bbd4e44eb595c4d9f95bcb71e99f",
    ),
    (
        "public.idis_task5_coverage_scope_guard()",
        "src/idis/persistence/migrations/versions/0036_durable_methodology_selection.py",
        2_716,
        "3d4767d2bf5678f8a9a003c3168d79e3050e16eaf0849bc49b8d79e6668b5fbf",
        2_589,
        "ba593849e358a5c772a6af3531d32b193fd01d314432e71b872522c3127cd3ab",
    ),
    (
        "public.idis_task6_erasure_requested(uuid,uuid)",
        "src/idis/persistence/migrations/versions/0037_durable_methodology_extraction.py",
        422,
        "13210ca6e61419f0bdc953e44141b3a711270a773397b74698a51c087bdbd1da",
        243,
        "36ad20a33e39b322dd8a582653fa0e3174508a7bb9f3d98d3533aea6bdac3e7f",
    ),
    (
        "public.idis_task6_current_lease_is_valid(uuid,uuid,uuid)",
        "src/idis/persistence/migrations/versions/0037_durable_methodology_extraction.py",
        1_883,
        "e83e6e6d60c3ea5bcdf65b010dc26b91a0abfe48513a8fabf3176308022dfda2",
        1_677,
        "cde8f9e3b33a469ce43ec9b7751465d588267fbb1166ed4e8cd155557fb3b499",
    ),
    (
        "public.idis_task6_immutable_guard()",
        "src/idis/persistence/migrations/versions/0037_durable_methodology_extraction.py",
        761,
        "136aa75e5bb1f8a26288f532ef81eab0e22d7143759ebb2b7c89dc754db942d1",
        639,
        "97db59522cf42dd91797850e5f9ae438d4d8aa9914d24058bbb4943e7fa3114a",
    ),
    (
        "public.idis_task6_attempt_guard()",
        "src/idis/persistence/migrations/versions/0037_durable_methodology_extraction.py",
        5_193,
        "ac02bf2865a239e32d4ee553c13809970773f9a04edeb0d6852ff934e1303b23",
        5_073,
        "545883a6a413f03bc12ead2f8680ec7a8c00518b01a4da30be6317b39aec3ffc",
    ),
    (
        "public.idis_task6_validate_result_outputs()",
        "src/idis/persistence/migrations/versions/0037_durable_methodology_extraction.py",
        1_056,
        "3c5210854a409d1629925ab636a852dd0961478f66b7f9f6cd3e9d7c3d3bf5e9",
        926,
        "18cc3215c17de18e530bcfba0a3551693c24815f805e5b86848beb54c294d8ad",
    ),
)


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_bytes())
    assert isinstance(value, dict)
    return value


def test_v17_retained_projection_expands_exactly_three_to_eight() -> None:
    v16 = _load(_V16_ARTIFACT)
    v17 = _load(_V17_ARTIFACT)
    old = v16["sql_authority_registry"]["guard_function_acl_registry"]
    new = v17["sql_authority_registry"]["guard_function_acl_registry"]

    assert len(old["retained_0037_catalog_projection_tuple"]) == 3
    projection = new["retained_0037_catalog_projection_tuple"]
    assert [row[0] for row in projection] == [row[0] for row in _EXPECTED]

    for actual, expected in zip(projection, _EXPECTED, strict=True):
        signature, path, sql_bytes, sql_sha256, prosrc_bytes, prosrc_sha256 = expected
        function_sql = bytes.fromhex(actual[1])
        prosrc = bytes.fromhex(actual[2])
        assert actual == [
            signature,
            actual[1],
            actual[2],
            path,
            _SOURCE_SHA512[path],
            "MIGRATION_CURRENT_USER_MATCHES_0037_OWNER",
            False,
            None,
            None,
        ]
        assert len(function_sql) == sql_bytes
        assert hashlib.sha256(function_sql).hexdigest() == sql_sha256
        assert len(prosrc) == prosrc_bytes
        assert hashlib.sha256(prosrc).hexdigest() == prosrc_sha256


def test_v17_downgrade_authority_replays_all_eight_exact_functions() -> None:
    artifact = _load(_V17_ARTIFACT)
    acl = artifact["sql_authority_registry"]["guard_function_acl_registry"]
    downgrade = acl["downgrade_authority"]

    assert downgrade["retained_function_signature_tuple"] == [row[0] for row in _EXPECTED]
    assert len(downgrade["retained_function_restore_sql_utf8_hex_tuple"]) == 8
    assert len(downgrade["retained_trigger_restore_sql_utf8_hex_tuple"]) == 9
    # V17 already adds five source-projection helper drops (180 -> 185);
    # restoring the five previously compact-only functions makes the final 190.
    assert len(downgrade["ordered_sql_utf8_hex_tuple"]) == 190
    restored = tuple(
        bytes.fromhex(value) for value in downgrade["retained_function_restore_sql_utf8_hex_tuple"]
    )
    assert tuple(hashlib.sha256(value).hexdigest() for value in restored) == tuple(
        row[3] for row in _EXPECTED
    )
    assertion = bytes.fromhex(
        downgrade["retained_catalog_postrestore_assertion_sql_utf8_hex"]
    ).decode("utf-8")
    assert all(signature in assertion for signature, *_rest in _EXPECTED)
