"""One-shot materializer for the reviewed full-strict-v5 FK-authority closure.

The append-frozen full-strict-v4 artifact is the sole input.  This script
applies only the reviewer-authorized relational-authority corrections and
creates the v5 artifact exclusively; it never mutates or replaces v4.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

_V4_ARTIFACT_SHA512 = (
    "0e2dc4ee107c189c02b2a8f0668296ba0c4cce04005d9a605462e97e852aaba2"
    "6ebebdcd57d54653947c62cf1b2c4d465e123bcdd2132698fac1fb1ab556775e"
)
_AUTHORITY_ROW_SCHEMA_HASH_DOMAIN = b"full-strict-v4-authority-row-schema-sha256-v1\x00"
_ERASURE_EXECUTION_ROW_HASH_DOMAIN = b"full-strict-v4-erasure-execution-row-sha256-v1\x00"
_ERASURE_ACTIVATION_CENSUS_HASH_DOMAIN = b"full-strict-v4-erasure-future-activation-census-v1\x00"
_V4_SECTION_ORDER = (
    "metadata",
    "canonicalization_contract",
    "tuple_schema_registry",
    "digest_recipe_registry",
    "uuid_namespace_registry",
    "schema_registry",
    "semantic_step_registry",
    "current_ddl_registry",
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
_V5_SECTION_ORDER = (
    *_V4_SECTION_ORDER[:8],
    "baseline_parent_authority_registry",
    *_V4_SECTION_ORDER[8:],
)
_BASELINE_ROW_SCHEMA = (
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
_BASELINE_ROWS: tuple[tuple[Any, ...], ...] = (
    (
        0,
        "claims",
        "ux_claims_task6_scope",
        "UNIQUE_INDEX",
        ("tenant_id", "deal_id", "claim_id"),
        None,
        "0037",
        "CREATE UNIQUE INDEX ux_claims_task6_scope ON claims (tenant_id, deal_id, claim_id)",
        "PRESERVE_PRE_0038",
    ),
    (
        1,
        "deals",
        "uq_task7_deals_tenant_deal",
        "UNIQUE_CONSTRAINT",
        ("tenant_id", "deal_id"),
        None,
        "0038",
        "ALTER TABLE deals ADD CONSTRAINT uq_task7_deals_tenant_deal UNIQUE (tenant_id,deal_id)",
        "DROP_WITH_0038",
    ),
    (
        2,
        "evidence_items",
        "uq_v2_evidence_item_generic_parent",
        "UNIQUE_CONSTRAINT",
        ("tenant_id", "deal_id", "evidence_id", "claim_id", "source_span_id"),
        None,
        "0038",
        "ALTER TABLE evidence_items ADD CONSTRAINT "
        "uq_v2_evidence_item_generic_parent "
        "UNIQUE (tenant_id,deal_id,evidence_id,claim_id,source_span_id)",
        "DROP_WITH_0038",
    ),
    (
        3,
        "sanads",
        "ux_sanads_task6_scope",
        "UNIQUE_INDEX",
        ("tenant_id", "deal_id", "sanad_id"),
        None,
        "0037",
        "CREATE UNIQUE INDEX ux_sanads_task6_scope ON sanads (tenant_id, deal_id, sanad_id)",
        "PRESERVE_PRE_0038",
    ),
)
_SPAN_AUTHORITY_V4 = (
    "ux_document_spans_task6_scope(tenant_id,deal_id,span_id) WHERE deal_id IS NOT NULL"
)
_SPAN_AUTHORITY_V5 = "ux_document_spans_task6_scope(tenant_id,deal_id,document_id,span_id)"
_CURRENT_AUTHORITY_ADDITIONS = (
    (
        "methodology_coverage_records",
        "uq_v2_coverage_dashboard_parent(tenant_id,deal_id,run_id,coverage_record_id)",
    ),
    (
        "validated_evidence_packages",
        "uq_v2_validated_evidence_package_scope(tenant_id,deal_id,run_id,package_id)",
    ),
    (
        "validated_evidence_packages",
        "uq_v2_validated_evidence_packages_reference_parent"
        "(tenant_id,deal_id,run_id,package_id,run_output_object_id)",
    ),
    (
        "debate_sessions",
        "uq_v2_debate_session_scope(tenant_id,deal_id,run_id,debate_id)",
    ),
    (
        "layer2_ic_challenges",
        "uq_v2_layer2_challenge_scope(tenant_id,deal_id,run_id,challenge_id)",
    ),
    (
        "methodology_result_sanad_links",
        "uq_v2_methodology_sanad_grading_parent(tenant_id,deal_id,run_id,sanad_link_id,sanad_id)",
    ),
    (
        "evidence_trust_findings",
        "uq_v2_evidence_trust_findings_reference_parent"
        "(tenant_id,deal_id,run_id,finding_id,run_output_object_id)",
    ),
)
_ALIAS_RETARGETS = {
    ("rag_query_results", "fk_v2_rag_query_index"): (
        "uq_v2_rag_index_result_scope",
        "uq_v2_rag_index_results_scope",
    ),
    (
        "external_intelligence_conflict_executions",
        "fk_v2_conflict_execution_plan",
    ): ("uq_v2_conflict_plan_scope", "uq_v2_conflict_plans_scope"),
}
_STABLE_SECTIONS = (
    "canonicalization_contract",
    "tuple_schema_registry",
    "uuid_namespace_registry",
    "schema_registry",
    "semantic_step_registry",
    "future_child_authority_registry",
    "sql_authority_registry",
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
_CURRENT_UNIQUE_RE = re.compile(r"^(?P<name>[a-z0-9_]+)\((?P<columns>[^)]*)\)(?P<suffix>.*)$")
_CURRENT_FK_RE = re.compile(
    r"^(?P<name>[a-z0-9_]+)\((?P<child>[^)]*)\) -> "
    r"(?P<parent_table>[a-z0-9_]+)\.(?P<parent_key>[a-z0-9_]+)"
    r"\((?P<parent>[^)]*)\) "
)


class MaterializationError(RuntimeError):
    """Stable, fail-closed v5 materialization error."""


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha512(value: bytes) -> str:
    return hashlib.sha512(value).hexdigest()


def _h(value: object) -> str:
    return _sha256(_canonical_bytes(value))


def _columns(value: str) -> tuple[str, ...]:
    return tuple(part.strip() for part in value.split(",") if part.strip())


def _authority_schema_sha256(row: dict[str, Any]) -> str:
    payload = {key: value for key, value in row.items() if key != "schema_sha256"}
    return _sha256(_AUTHORITY_ROW_SCHEMA_HASH_DOMAIN + _canonical_bytes(payload))


def _load_v4(path: Path) -> tuple[bytes, dict[str, Any]]:
    raw = path.read_bytes()
    if _sha512(raw) != _V4_ARTIFACT_SHA512:
        raise MaterializationError("TASK7_V5_BASE_ARTIFACT_HASH_MISMATCH")
    if b"\r" in raw or not raw.endswith(b"\n") or raw.endswith(b"\n\n"):
        raise MaterializationError("TASK7_V5_BASE_ARTIFACT_NOT_CANONICAL")
    value = json.loads(raw.decode("utf-8"))
    if not isinstance(value, dict) or tuple(value) != _V4_SECTION_ORDER:
        raise MaterializationError("TASK7_V5_BASE_SECTION_ORDER_MISMATCH")
    metadata = value.get("metadata")
    if not isinstance(metadata, dict) or (
        metadata.get("artifact_schema") != "full-strict-v4-reviewed-preimage-v1"
        or metadata.get("pipeline_contract_version") != "full-strict-v4"
        or metadata.get("append_frozen") is not True
    ):
        raise MaterializationError("TASK7_V5_BASE_METADATA_INVALID")
    return raw, value


def _table_rows(artifact: dict[str, Any], section: str, key: str) -> dict[str, dict[str, Any]]:
    rows = artifact[section]["rows"]
    result = {str(row[key]): row for row in rows}
    if len(result) != len(rows):
        raise MaterializationError(f"TASK7_V5_DUPLICATE_TABLE:{section}")
    return result


def _reviewed_current_row(row: dict[str, Any]) -> None:
    payload = {key: value for key, value in row.items() if key != "reviewed_authority_utf8_hex"}
    row["reviewed_authority_utf8_hex"] = _canonical_bytes(payload).hex()


def _transform_current_ddl(artifact: dict[str, Any]) -> None:
    rows = artifact["current_ddl_registry"]["rows"]
    by_table = _table_rows(artifact, "current_ddl_registry", "table_name")
    span_uniques = by_table["document_spans"]["unique_tuple"]
    if span_uniques[0] != _SPAN_AUTHORITY_V4:
        raise MaterializationError("TASK7_V5_SPAN_AUTHORITY_BASE_MISMATCH")
    span_uniques[0] = _SPAN_AUTHORITY_V5

    for table, literal in _CURRENT_AUTHORITY_ADDITIONS:
        unique_tuple = by_table[table]["unique_tuple"]
        if literal in unique_tuple:
            raise MaterializationError(f"TASK7_V5_AUTHORITY_ALREADY_PRESENT:{table}:{literal}")
        unique_tuple.append(literal)

    for row in rows:
        _reviewed_current_row(row)
    artifact["current_ddl_registry"]["expected_sha256"] = _h(
        [
            "task7-ddl-registry-v2",
            rows,
            artifact["erasure_materialized"]["expected_sha256"],
        ]
    )


def _transform_future_authorities(artifact: dict[str, Any]) -> None:
    native_rows = artifact["future_native_authority_registry"]["rows"]
    native_by_table = _table_rows(
        artifact,
        "future_native_authority_registry",
        "planned_table",
    )
    alias_count = 0
    for (table, fk_name), (old_name, new_name) in _ALIAS_RETARGETS.items():
        matches = [
            foreign_key
            for foreign_key in native_by_table[table]["reference_fields"]
            if foreign_key[0] == fk_name
        ]
        if len(matches) != 1 or matches[0][3] != old_name:
            raise MaterializationError(f"TASK7_V5_ALIAS_BASE_MISMATCH:{table}:{fk_name}")
        matches[0][3] = new_name
        alias_count += 1
    if alias_count != 2:
        raise MaterializationError("TASK7_V5_ALIAS_COUNT_MISMATCH")
    for row in native_rows:
        row["schema_sha256"] = _authority_schema_sha256(row)
    artifact["future_native_authority_registry"]["expected_sha256"] = _h(
        ["task7-future-native-authority-registry-v2", native_rows]
    )

    reference_rows = artifact["future_reference_authority_registry"]["rows"]
    corrected = 0
    for row in reference_rows:
        matches = [
            foreign_key
            for foreign_key in row["foreign_key_tuple"]
            if foreign_key[2] == "run_input_bindings"
            and foreign_key[3] == "uq_task7_run_input_bindings_scoped_id"
        ]
        if len(matches) != 1:
            raise MaterializationError(
                f"TASK7_V5_INPUT_PARENT_FK_COUNT:{row['table_name']}:{len(matches)}"
            )
        parent_columns = matches[0][4]
        if parent_columns != ["tenant_id", "deal_id", "run_id", "input_binding_id"]:
            raise MaterializationError(f"TASK7_V5_INPUT_PARENT_BASE_MISMATCH:{row['table_name']}")
        parent_columns[-1] = "binding_id"
        corrected += 1
        row["schema_sha256"] = _authority_schema_sha256(row)
    if corrected != 15:
        raise MaterializationError(f"TASK7_V5_INPUT_PARENT_COUNT:{corrected}")
    artifact["future_reference_authority_registry"]["expected_sha256"] = _h(
        ["task7-future-reference-authority-registry-v2", reference_rows]
    )


def _refresh_erasure_activation_census(artifact: dict[str, Any]) -> None:
    execution_rows = artifact["erasure_execution_registry"]["rows"]
    if len(execution_rows) != 1:
        raise MaterializationError("TASK7_V5_ERASURE_EXECUTION_COUNT_MISMATCH")
    row = execution_rows[0]
    existing_census = row["future_activation_census_tuple"]
    expected_order = [entry[0] for entry in existing_census]
    schema_by_table: dict[str, str] = {}
    for section, table_key in (
        ("future_native_authority_registry", "planned_table"),
        ("future_reference_authority_registry", "table_name"),
        ("future_child_authority_registry", "table_name"),
    ):
        for authority in artifact[section]["rows"]:
            table = str(authority[table_key])
            if table in schema_by_table:
                raise MaterializationError(f"TASK7_V5_DUPLICATE_FUTURE_TABLE:{table}")
            schema_by_table[table] = str(authority["schema_sha256"])
    if list(schema_by_table) != expected_order:
        raise MaterializationError("TASK7_V5_FUTURE_CENSUS_ORDER_MISMATCH")
    census = [[table, schema_by_table[table]] for table in expected_order]
    row["future_activation_census_tuple"] = census
    row["future_activation_census_sha256"] = _sha256(
        _ERASURE_ACTIVATION_CENSUS_HASH_DOMAIN + _canonical_bytes(census)
    )
    payload = {key: value for key, value in row.items() if key != "expected_sha256"}
    row["expected_sha256"] = _sha256(_ERASURE_EXECUTION_ROW_HASH_DOMAIN + _canonical_bytes(payload))
    artifact["erasure_execution_registry"]["expected_sha256"] = _h(
        ["full-strict-v4-erasure-execution-registry-v1", execution_rows]
    )
    artifact["activation_assertions"]["expected_erasure_execution_sha256"] = row["expected_sha256"]


def _baseline_section() -> dict[str, Any]:
    rows = [list(row[:4]) + [list(row[4]), *row[5:]] for row in _BASELINE_ROWS]
    return {
        "schema_version": "baseline-parent-authority-registry-v1",
        "row_schema": list(_BASELINE_ROW_SCHEMA),
        "rows": rows,
        "expected_sha256": _sha256(_canonical_bytes(rows)),
    }


def _transform_metadata_and_recipes(
    artifact: dict[str, Any],
    baseline_sha256: str,
) -> None:
    metadata = artifact["metadata"]
    design = json.loads(bytes.fromhex(metadata["reviewed_design_utf8_hex"]).decode("utf-8"))
    if not isinstance(design, list) or design[:2] != [
        "full-strict-v4-reviewed-design-literal-v1",
        ["pipeline_contract_version", "full-strict-v4"],
    ]:
        raise MaterializationError("TASK7_V5_REVIEWED_DESIGN_BASE_MISMATCH")
    design[0] = "full-strict-v5-reviewed-design-literal-v1"
    design[1] = ["pipeline_contract_version", "full-strict-v5"]
    design.extend(
        [
            [
                "fk_parent_authority_class_closure",
                [
                    ["document_span_authority", _SPAN_AUTHORITY_V5],
                    ["input_binding_parent_column_correction_count", 15],
                    ["alias_retarget_count", 2],
                    ["baseline_parent_authority_count", 4],
                    ["installed_current_unique_authority_count", 7],
                    ["baseline_parent_authority_registry_sha256", baseline_sha256],
                ],
            ],
        ]
    )
    design_bytes = _canonical_bytes(design)
    metadata["artifact_schema"] = "full-strict-v5-reviewed-preimage-v1"
    metadata["pipeline_contract_version"] = "full-strict-v5"
    metadata["reviewed_design_sha256"] = _sha256(design_bytes)
    metadata["reviewed_design_utf8_hex"] = design_bytes.hex()
    metadata["section_order"] = list(_V5_SECTION_ORDER)

    recipes = artifact["digest_recipe_registry"]["rows"]
    if recipes[5] != [
        "pipeline_contract_hash",
        '["pipeline-contract-v4","full-strict-v4",'
        "reviewed_preimage_artifact_hash,ORDERED[component_hashes]]",
    ]:
        raise MaterializationError("TASK7_V5_PIPELINE_RECIPE_BASE_MISMATCH")
    recipes[5][1] = (
        '["pipeline-contract-v5","full-strict-v5",'
        "reviewed_preimage_artifact_hash,ORDERED[component_hashes]]"
    )
    recipes.append(
        [
            "baseline_parent_authority_registry_hash",
            "lowercase_hex(SHA256(CJ(ORDERED[baseline_parent_authority_rows])))",
        ]
    )
    artifact["digest_recipe_registry"]["reviewed_literal_utf8_hex"] = _canonical_bytes(
        recipes
    ).hex()


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


def _validate_parent_closure(artifact: dict[str, Any]) -> None:
    authorities: dict[tuple[str, str], tuple[tuple[str, ...], bool]] = {}

    def register(table: str, name: str, columns: tuple[str, ...], partial: bool) -> None:
        key = (table, name)
        if key in authorities:
            raise MaterializationError(f"TASK7_V5_DUPLICATE_PARENT_AUTHORITY:{table}.{name}")
        authorities[key] = columns, partial

    for row in artifact["current_ddl_registry"]["rows"]:
        for literal in row["unique_tuple"]:
            match = _CURRENT_UNIQUE_RE.fullmatch(literal)
            if match is None:
                raise MaterializationError(f"TASK7_V5_UNPARSEABLE_UNIQUE:{literal}")
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
            parent_columns = _columns(match.group("parent"))
            authority = authorities.get(key)
            if authority is None or authority != (parent_columns, False):
                failures.append(f"{row['table_name']}.{match.group('name')}:{key}:{parent_columns}")
    for table, foreign_key in _structured_foreign_keys(artifact):
        key = str(foreign_key[2]), str(foreign_key[3])
        parent_columns = tuple(str(column) for column in foreign_key[4])
        authority = authorities.get(key)
        if authority is None or authority != (parent_columns, False):
            failures.append(f"{table}.{foreign_key[0]}:{key}:{parent_columns}")
    if failures:
        raise MaterializationError(
            "TASK7_V5_FK_PARENT_AUTHORITY_INVALID:" + "|".join(sorted(failures))
        )


def _build_v5(v4: dict[str, Any]) -> dict[str, Any]:
    transformed = copy.deepcopy(v4)
    _transform_current_ddl(transformed)
    _transform_future_authorities(transformed)
    _refresh_erasure_activation_census(transformed)
    baseline = _baseline_section()
    _transform_metadata_and_recipes(transformed, str(baseline["expected_sha256"]))

    artifact: dict[str, Any] = {}
    for section, value in transformed.items():
        artifact[section] = value
        if section == "current_ddl_registry":
            artifact["baseline_parent_authority_registry"] = baseline
    if tuple(artifact) != _V5_SECTION_ORDER:
        raise MaterializationError("TASK7_V5_SECTION_ORDER_MISMATCH")
    for section in _STABLE_SECTIONS:
        if artifact[section] != v4[section]:
            raise MaterializationError(f"TASK7_V5_UNAUTHORIZED_STABLE_DRIFT:{section}")
    _validate_parent_closure(artifact)
    return artifact


def _render(artifact: dict[str, Any]) -> bytes:
    return (
        json.dumps(
            artifact,
            allow_nan=False,
            ensure_ascii=False,
            indent=2,
        )
        + "\n"
    ).encode("utf-8")


def _write_exclusive(path: Path, content: bytes) -> None:
    if not path.parent.is_dir():
        raise MaterializationError("TASK7_V5_OUTPUT_PARENT_MISSING")
    try:
        with path.open("xb") as stream:
            stream.write(content)
            stream.flush()
    except FileExistsError as exc:
        raise MaterializationError("TASK7_V5_OUTPUT_ALREADY_EXISTS") from exc


def _parse_args(argv: list[str]) -> argparse.Namespace:
    repo_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(
        description="Materialize append-frozen full-strict-v5 exactly once.",
    )
    parser.add_argument(
        "--base-artifact",
        type=Path,
        default=repo_root / "contracts" / "full_strict_v4_reviewed_preimage_v1.json",
    )
    parser.add_argument(
        "--output",
        required=True,
        type=Path,
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    try:
        _raw, v4 = _load_v4(args.base_artifact)
        artifact = _build_v5(v4)
        rendered = _render(artifact)
        _write_exclusive(args.output, rendered)
    except (MaterializationError, OSError, UnicodeError, json.JSONDecodeError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(f"TASK7_V5_ARTIFACT_MATERIALIZED:{_sha256(rendered)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
