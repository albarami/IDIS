"""Derive the frozen full-strict-v5 contract fixture from its sole byte authority."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import re
import sys
from pathlib import Path
from types import ModuleType
from typing import Any, cast

_SECTION_ORDER = (
    "metadata",
    "canonicalization_contract",
    "tuple_schema_registry",
    "digest_recipe_registry",
    "uuid_namespace_registry",
    "schema_registry",
    "semantic_step_registry",
    "current_ddl_registry",
    "baseline_parent_authority_registry",
    "future_native_authority_registry",
    "future_reference_authority_registry",
    "future_child_authority_registry",
    "sql_authority_registry",
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
    "activation_assertions",
)
_COMPONENT_ORDER = (
    "schema_registry_hash",
    "semantic_step_vector_hash",
    "task7_ddl_registry_hash",
    "baseline_parent_authority_registry_hash",
    "future_native_authority_registry_hash",
    "future_reference_authority_registry_hash",
    "future_child_authority_registry_hash",
    "sql_authority_registry_hash",
    "erasure_execution_registry_hash",
    "provider_registry_hash",
    "rights_index_policy_registry_hash",
    "role_registry_hash",
    "deliverable_registry_hash",
    "selector_registry_hash",
    "applicability_registry_hash",
    "usage_extractor_registry_hash",
    "edge_manifest_hash",
    "uuid_namespace_registry_hash",
    "task7_erasure_schedule_hash",
)
_BASELINE_SCHEMA = (
    "authority_ordinal",
    "table_name",
    "authority_name",
    "authority_kind",
    "column_tuple",
    "predicate",
    "installation_revision",
    "upgrade_ddl",
    "downgrade_disposition",
)
_BASELINE_ROWS = (
    [
        0,
        "claims",
        "ux_claims_task6_scope",
        "UNIQUE_INDEX",
        ["tenant_id", "deal_id", "claim_id"],
        None,
        "0037",
        "CREATE UNIQUE INDEX ux_claims_task6_scope ON claims (tenant_id, deal_id, claim_id)",
        "PRESERVE_PRE_0038",
    ],
    [
        1,
        "deals",
        "uq_task7_deals_tenant_deal",
        "UNIQUE_CONSTRAINT",
        ["tenant_id", "deal_id"],
        None,
        "0038",
        "ALTER TABLE deals ADD CONSTRAINT uq_task7_deals_tenant_deal UNIQUE (tenant_id,deal_id)",
        "DROP_WITH_0038",
    ],
    [
        2,
        "evidence_items",
        "uq_v2_evidence_item_generic_parent",
        "UNIQUE_CONSTRAINT",
        ["tenant_id", "deal_id", "evidence_id", "claim_id", "source_span_id"],
        None,
        "0038",
        "ALTER TABLE evidence_items ADD CONSTRAINT "
        "uq_v2_evidence_item_generic_parent "
        "UNIQUE (tenant_id,deal_id,evidence_id,claim_id,source_span_id)",
        "DROP_WITH_0038",
    ],
    [
        3,
        "sanads",
        "ux_sanads_task6_scope",
        "UNIQUE_INDEX",
        ["tenant_id", "deal_id", "sanad_id"],
        None,
        "0037",
        "CREATE UNIQUE INDEX ux_sanads_task6_scope ON sanads (tenant_id, deal_id, sanad_id)",
        "PRESERVE_PRE_0038",
    ],
)
_CURRENT_UNIQUE_RE = re.compile(r"^(?P<name>[a-z0-9_]+)\((?P<columns>[^)]*)\)(?P<suffix>.*)$")
_CURRENT_FK_RE = re.compile(
    r"^(?P<name>[a-z0-9_]+)\((?P<child>[^)]*)\) -> "
    r"(?P<parent_table>[a-z0-9_]+)\.(?P<parent_key>[a-z0-9_]+)"
    r"\((?P<parent>[^)]*)\) "
)


def _load_v4_helpers() -> ModuleType:
    path = Path(__file__).with_name("generate_task7_v4_contract_fixture.py")
    spec = importlib.util.spec_from_file_location("_task7_v4_fixture_helpers", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("TASK7_V5_HELPER_IMPORT_FAILED")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_V4 = _load_v4_helpers()
ContractArtifactError = _V4.ContractArtifactError


def _canonical_bytes(value: Any) -> bytes:
    return cast(bytes, _V4._canonical_bytes(value))


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _h(value: Any) -> str:
    return _sha256_bytes(_canonical_bytes(value))


def _expect(actual: str, expected: Any, label: str) -> None:
    if not isinstance(expected, str) or actual != expected:
        raise ContractArtifactError(
            "PIPELINE_CONTRACT_HASH_MISMATCH",
            f"{label}: expected {expected!r}, derived {actual}",
        )


def _columns(value: str) -> tuple[str, ...]:
    return tuple(part.strip() for part in value.split(",") if part.strip())


def _validate_baseline_registry(artifact: dict[str, Any]) -> str:
    section = artifact.get("baseline_parent_authority_registry")
    if not isinstance(section, dict) or tuple(section) != (
        "schema_version",
        "row_schema",
        "rows",
        "expected_sha256",
    ):
        raise ContractArtifactError(
            "TASK7_CONTRACT_ARTIFACT_INVALID",
            "baseline parent authority registry shape mismatch",
        )
    if section["schema_version"] != "baseline-parent-authority-registry-v1":
        raise ContractArtifactError(
            "TASK7_CONTRACT_ARTIFACT_INVALID",
            "baseline parent authority registry version mismatch",
        )
    if tuple(section["row_schema"]) != _BASELINE_SCHEMA or section["rows"] != list(_BASELINE_ROWS):
        raise ContractArtifactError(
            "TASK7_CONTRACT_ARTIFACT_INVALID",
            "baseline parent authority reviewed rows mismatch",
        )
    value = _sha256_bytes(_canonical_bytes(section["rows"]))
    _expect(value, section["expected_sha256"], "baseline_parent_authority_registry_hash")
    return value


def _structured_foreign_keys(artifact: dict[str, Any]) -> list[tuple[str, list[Any]]]:
    result: list[tuple[str, list[Any]]] = []
    for section, table_key, fk_key in (
        ("future_native_authority_registry", "planned_table", "reference_fields"),
        ("future_reference_authority_registry", "table_name", "foreign_key_tuple"),
        ("future_child_authority_registry", "table_name", "foreign_key_tuple"),
    ):
        for row in artifact[section]["rows"]:
            result.extend((str(row[table_key]), value) for value in row[fk_key])
    return result


def _validate_parent_authority_closure(artifact: dict[str, Any]) -> None:
    authorities: dict[tuple[str, str], tuple[tuple[str, ...], bool]] = {}

    def register(table: str, name: str, columns: tuple[str, ...], partial: bool) -> None:
        key = table, name
        if key in authorities:
            raise ContractArtifactError(
                "TASK7_CONTRACT_ARTIFACT_INVALID",
                f"duplicate parent authority {table}.{name}",
            )
        authorities[key] = columns, partial

    for row in artifact["current_ddl_registry"]["rows"]:
        for literal in row["unique_tuple"]:
            match = _CURRENT_UNIQUE_RE.fullmatch(literal)
            if match is None:
                raise ContractArtifactError(
                    "TASK7_CONTRACT_ARTIFACT_INVALID",
                    f"unparseable current unique authority {literal!r}",
                )
            register(
                str(row["table_name"]),
                match.group("name"),
                _columns(match.group("columns")),
                match.group("suffix").strip().startswith("WHERE "),
            )
    for section, table_key in (
        ("future_native_authority_registry", "planned_table"),
        ("future_reference_authority_registry", "table_name"),
        ("future_child_authority_registry", "table_name"),
    ):
        for row in artifact[section]["rows"]:
            for name, columns, _nulls_not_distinct in row["unique_tuple"]:
                register(str(row[table_key]), str(name), tuple(columns), False)
    for row in artifact["baseline_parent_authority_registry"]["rows"]:
        register(str(row[1]), str(row[2]), tuple(row[4]), row[5] is not None)

    failures: list[str] = []
    for row in artifact["current_ddl_registry"]["rows"]:
        for literal in row["foreign_key_tuple"]:
            match = _CURRENT_FK_RE.match(literal)
            if match is None:
                failures.append(f"unparseable:{row['table_name']}:{literal}")
                continue
            key = match.group("parent_table"), match.group("parent_key")
            expected = _columns(match.group("parent")), False
            if authorities.get(key) != expected:
                failures.append(f"{row['table_name']}.{match.group('name')}:{key}:{expected[0]}")
    for table, foreign_key in _structured_foreign_keys(artifact):
        key = str(foreign_key[2]), str(foreign_key[3])
        expected = tuple(str(column) for column in foreign_key[4]), False
        if authorities.get(key) != expected:
            failures.append(f"{table}.{foreign_key[0]}:{key}:{expected[0]}")
    if failures:
        raise ContractArtifactError(
            "TASK7_FK_PARENT_AUTHORITY_INVALID",
            "|".join(sorted(failures)),
        )


def _validate_shape(raw: bytes, artifact: dict[str, Any]) -> None:
    if b"\r" in raw or not raw.endswith(b"\n") or raw.endswith(b"\n\n"):
        raise ContractArtifactError(
            "TASK7_CONTRACT_ARTIFACT_INVALID",
            "artifact must be LF-only with one terminal newline",
        )
    if tuple(artifact) != _SECTION_ORDER:
        raise ContractArtifactError(
            "TASK7_CONTRACT_ARTIFACT_INVALID",
            "top-level section order mismatch",
        )
    metadata = artifact.get("metadata")
    if not isinstance(metadata, dict) or (
        metadata.get("artifact_schema") != "full-strict-v5-reviewed-preimage-v1"
        or metadata.get("pipeline_contract_version") != "full-strict-v5"
        or metadata.get("append_frozen") is not True
        or metadata.get("pydantic_version") != "2.12.5"
        or tuple(metadata.get("section_order", ())) != _SECTION_ORDER
    ):
        raise ContractArtifactError(
            "TASK7_CONTRACT_ARTIFACT_INVALID",
            "v5 metadata mismatch",
        )
    reviewed_design = bytes.fromhex(metadata["reviewed_design_utf8_hex"])
    _expect(
        _sha256_bytes(reviewed_design),
        metadata["reviewed_design_sha256"],
        "reviewed_design_sha256",
    )
    recipes = artifact["digest_recipe_registry"]["rows"]
    if recipes[5] != [
        "pipeline_contract_hash",
        '["pipeline-contract-v5","full-strict-v5",'
        "reviewed_preimage_artifact_hash,ORDERED[component_hashes]]",
    ] or recipes[-1] != [
        "baseline_parent_authority_registry_hash",
        "lowercase_hex(SHA256(CJ(ORDERED[baseline_parent_authority_rows])))",
    ]:
        raise ContractArtifactError(
            "TASK7_CONTRACT_ARTIFACT_INVALID",
            "v5 digest recipe mismatch",
        )
    if bytes.fromhex(
        artifact["digest_recipe_registry"]["reviewed_literal_utf8_hex"]
    ) != _canonical_bytes(recipes):
        raise ContractArtifactError(
            "TASK7_CONTRACT_ARTIFACT_INVALID",
            "digest reviewed bytes mismatch",
        )

    counts = {
        "semantic_step_registry": 28,
        "edge_rule_registry": 48,
        "usage_route_registry": 1_756,
        "usage_extractor_registry": 19,
        "rights_index_policy_registry": 18,
        "current_ddl_registry": 34,
        "future_native_authority_registry": 18,
        "future_reference_authority_registry": 15,
        "future_child_authority_registry": 5,
        "sql_authority_registry": 17,
        "erasure_execution_registry": 1,
    }
    for section, expected in counts.items():
        if len(artifact[section]["rows"]) != expected:
            raise ContractArtifactError(
                "TASK7_CONTRACT_ARTIFACT_INVALID",
                f"{section} count mismatch",
            )
    if (
        len(artifact["erasure_baseline"]["rows"]) != 59
        or len(artifact["erasure_materialized"]["rows"]) != 66
    ):
        raise ContractArtifactError(
            "TASK7_CONTRACT_ARTIFACT_INVALID",
            "erasure census mismatch",
        )
    _validate_baseline_registry(artifact)
    _validate_parent_authority_closure(artifact)
    _V4.validate_sql_authority_registry(artifact)
    _V4.validate_erasure_execution_registry(artifact)
    _V4._validate_reviewed_row_bytes(artifact)
    _V4._validate_scalar_tree(artifact)


def _section_hashes(artifact: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "ordinal": ordinal,
            "name": name,
            "sha256": _sha256_bytes(_canonical_bytes(artifact[name])),
        }
        for ordinal, name in enumerate(_SECTION_ORDER)
    ]


def _derive_components(
    artifact: dict[str, Any],
    reviewed_preimage_artifact_hash: str,
) -> dict[str, str]:
    _, schema_hash = _V4._schema_components(artifact)
    _, semantic_hash = _V4._semantic_components(artifact)
    baseline_erasure_hash = _V4._erasure_hash(artifact["erasure_baseline"], "baseline")
    materialized_erasure_hash = _V4._erasure_hash(
        artifact["erasure_materialized"],
        "materialized",
    )
    ddl_hash = _h(
        [
            "task7-ddl-registry-v2",
            artifact["current_ddl_registry"]["rows"],
            materialized_erasure_hash,
        ]
    )
    _expect(
        ddl_hash,
        artifact["current_ddl_registry"]["expected_sha256"],
        "task7_ddl_registry_hash",
    )
    baseline_parent_hash = _validate_baseline_registry(artifact)
    future_native_hash = _V4._generic_registry_hash(
        artifact,
        "future_native_authority_registry",
        "task7-future-native-authority-registry-v2",
    )
    future_reference_hash = _V4._generic_registry_hash(
        artifact,
        "future_reference_authority_registry",
        "task7-future-reference-authority-registry-v2",
    )
    future_child_hash = _V4._generic_registry_hash(
        artifact,
        "future_child_authority_registry",
        "task7-future-child-authority-registry-v2",
    )
    sql_authority_hash = _V4._sql_authority_registry_hash(artifact)
    erasure_execution_hash = _V4._erasure_execution_registry_hash(artifact)
    provider_hash = _V4._generic_registry_hash(
        artifact,
        "provider_registry",
        "task7-provider-registry-v1",
    )
    rights_hash = _V4._rights_index_policy_registry_hash(artifact)
    role_hash = _V4._generic_registry_hash(
        artifact,
        "role_registry",
        "task7-role-registry-v1",
    )
    deliverable_hash = _V4._generic_registry_hash(
        artifact,
        "deliverable_registry",
        "task7-deliverable-registry-v1",
    )
    uuid_hash = _V4._generic_registry_hash(
        artifact,
        "uuid_namespace_registry",
        "task7-uuid-namespace-registry-v1",
    )
    (
        producer_hashes,
        consumer_hashes,
        producer_registry_hash,
        consumer_registry_hash,
        selector_hash,
    ) = _V4._selector_components(artifact)
    predicate_hashes, applicability_hash = _V4._applicability_components(artifact)
    edge_hash = _V4._edge_components(
        artifact,
        producer_hashes,
        consumer_hashes,
        predicate_hashes,
    )
    route_hash, extractor_hash = _V4._usage_components(artifact)
    return {
        "reviewed_preimage_artifact_hash": reviewed_preimage_artifact_hash,
        "schema_registry_hash": schema_hash,
        "semantic_step_vector_hash": semantic_hash,
        "baseline_erasure_schedule_hash": baseline_erasure_hash,
        "task7_erasure_schedule_hash": materialized_erasure_hash,
        "task7_ddl_registry_hash": ddl_hash,
        "baseline_parent_authority_registry_hash": baseline_parent_hash,
        "future_native_authority_registry_hash": future_native_hash,
        "future_reference_authority_registry_hash": future_reference_hash,
        "future_child_authority_registry_hash": future_child_hash,
        "sql_authority_registry_hash": sql_authority_hash,
        "erasure_execution_registry_hash": erasure_execution_hash,
        "provider_registry_hash": provider_hash,
        "rights_index_policy_registry_hash": rights_hash,
        "role_registry_hash": role_hash,
        "deliverable_registry_hash": deliverable_hash,
        "producer_selector_registry_hash": producer_registry_hash,
        "consumer_selector_registry_hash": consumer_registry_hash,
        "selector_registry_hash": selector_hash,
        "applicability_registry_hash": applicability_hash,
        "usage_path_route_hash": route_hash,
        "usage_extractor_registry_hash": extractor_hash,
        "edge_manifest_hash": edge_hash,
        "uuid_namespace_registry_hash": uuid_hash,
    }


def derive_contract_bytes(raw: bytes, *, validate: bool = True) -> dict[str, Any]:
    """Derive v5 exclusively from artifact bytes; validation supplies no inputs."""

    artifact = _V4._decode(raw)
    artifact_sha256 = _sha256_bytes(raw)
    section_hashes = _section_hashes(artifact)
    reviewed_preimage_artifact_hash = _h(
        [
            "full-strict-v5-reviewed-preimage-artifact-v1",
            artifact_sha256,
            [[row["ordinal"], row["name"], row["sha256"]] for row in section_hashes],
        ]
    )
    if validate:
        _validate_shape(raw, artifact)
    components = _derive_components(artifact, reviewed_preimage_artifact_hash)
    pipeline_hash = _h(
        [
            "pipeline-contract-v5",
            artifact["metadata"]["pipeline_contract_version"],
            reviewed_preimage_artifact_hash,
            [components[name] for name in _COMPONENT_ORDER],
        ]
    )
    components["pipeline_contract_hash"] = pipeline_hash
    return {
        "fixture_schema": "full-strict-v5-contract-hash-fixture-v1",
        "artifact_sha256": artifact_sha256,
        "section_hashes": section_hashes,
        "component_hashes": components,
        "activation_assertions": artifact.get("activation_assertions"),
        "FULL_STRICT_V5_CONTRACT_HASH": pipeline_hash,
    }


def _render_fixture(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def _write_exclusive(path: Path, rendered: str) -> None:
    if not path.parent.is_dir():
        raise ContractArtifactError(
            "TASK7_V5_FIXTURE_OUTPUT_PARENT_MISSING",
            str(path.parent),
        )
    try:
        with path.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(rendered)
    except FileExistsError as exc:
        raise ContractArtifactError(
            "TASK7_V5_FIXTURE_OUTPUT_ALREADY_EXISTS",
            str(path),
        ) from exc


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifact", required=True, type=Path)
    destination = parser.add_mutually_exclusive_group(required=True)
    destination.add_argument("--output", type=Path)
    destination.add_argument("--stdout", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    try:
        result = derive_contract_bytes(args.artifact.read_bytes(), validate=True)
        rendered = _render_fixture(result)
        if args.stdout:
            sys.stdout.buffer.write(rendered.encode("utf-8"))
        else:
            if args.output is None:
                raise ContractArtifactError(
                    "TASK7_CONTRACT_ARTIFACT_INVALID",
                    "fixture output path is absent",
                )
            _write_exclusive(args.output, rendered)
        return 0
    except (ContractArtifactError, OSError, UnicodeError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
