"""Validate and derive the unfrozen full-strict-v14 contract candidate.

The reviewed artifact is the sole normative byte source. V14 admits the
Task-6 BEFORE-trigger call only when it proves an exact, target-literal
normalization of the two unavailable generated fields. Fixture publication
remains unavailable until a separate signed PostgreSQL census freezes the
candidate.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any, Never, cast


def _load_script(path: Path, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"TASK7_V14_HELPER_IMPORT_FAILED:{path.name}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


_SCRIPT_DIR = Path(__file__).resolve().parent
_V13 = _load_script(
    _SCRIPT_DIR / "generate_task7_v13_contract_fixture.py",
    "_task7_v13_fixture_helpers_for_v14",
)
_MATERIALIZER = _load_script(
    _SCRIPT_DIR / "materialize_full_strict_v14_preimage.py",
    "_task7_v14_materializer_helpers",
)

ContractArtifactError = _V13.ContractArtifactError
_SECTION_ORDER = cast(tuple[str, ...], _V13._SECTION_ORDER)
_COMPONENT_ORDER = cast(tuple[str, ...], _V13._COMPONENT_ORDER)
_V13_SQL_REGISTRY_HASH_DOMAIN = "full-strict-v13-sql-authority-registry-v1"
_V14_SQL_REGISTRY_HASH_DOMAIN = "full-strict-v14-sql-authority-registry-v1"
_SOURCE_AUTHORITY_ID = "current-source-bound-v1"
_TASK6_AUTHORITY_ID = "current-task6-immutable-replacement-v1"
_TASK6_GUARD_SIGNATURE = "public.idis_task6_immutable_guard()"
_TASK6_HELPER_SIGNATURE = (
    "public.idis_task6_output_root_attach_is_valid_v1(jsonb,jsonb,uuid,uuid,uuid)"
)
_TASK6_TARGETS = _MATERIALIZER._TASK6_TARGETS
_TASK6_ATTACH_TARGETS = (
    "methodology_extraction_results",
    "methodology_result_factual_outputs",
)
_GENERATED_FIELDS = ("run_output_object_class", "run_output_object_kind")


def _canonical_bytes(value: Any) -> bytes:
    return cast(bytes, _V13._canonical_bytes(value))


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _h(value: Any) -> str:
    return _sha256_bytes(_canonical_bytes(value))


def _error(code: str, detail: str) -> Never:
    raise ContractArtifactError(code, detail)


def _expect(actual: str, expected: Any, label: str) -> None:
    if not isinstance(expected, str) or actual != expected:
        _error(
            "PIPELINE_CONTRACT_HASH_MISMATCH",
            f"{label}: expected {expected!r}, derived {actual}",
        )


def _decode_artifact(raw: bytes) -> dict[str, Any]:
    return cast(dict[str, Any], _V13._decode_artifact(raw))


def _render_artifact(artifact: dict[str, Any]) -> bytes:
    return (json.dumps(artifact, allow_nan=False, ensure_ascii=False, indent=2) + "\n").encode(
        "utf-8"
    )


def invariant_vector(artifact: dict[str, Any]) -> dict[str, int]:
    return cast(dict[str, int], _V13.invariant_vector(artifact))


def current_generated_column_census(
    artifact: dict[str, Any],
) -> tuple[dict[str, Any], ...]:
    return cast(tuple[dict[str, Any], ...], _V13.current_generated_column_census(artifact))


def current_generated_column_authority(
    artifact: dict[str, Any],
) -> dict[str, tuple[str, ...]]:
    return cast(
        dict[str, tuple[str, ...]],
        _V13.current_generated_column_authority(artifact),
    )


def current_generated_column_authority_sha256(artifact: dict[str, Any]) -> str:
    return _h(
        [
            "task7-v14-current-generated-column-authority-v1",
            list(current_generated_column_census(artifact)),
        ]
    )


def _function_sql(artifact: dict[str, Any], function_signature: str) -> str:
    return str(_V13._function_sql(artifact, function_signature))


def _acl(artifact: dict[str, Any]) -> dict[str, Any]:
    return cast(dict[str, Any], _V13._acl(artifact))


def guard_function_authority(
    artifact: dict[str, Any],
) -> tuple[tuple[str, str, str, str], ...]:
    return cast(tuple[tuple[str, str, str, str], ...], _V13.guard_function_authority(artifact))


def guard_target_authority(
    artifact: dict[str, Any],
) -> tuple[tuple[str, str, str], ...]:
    return cast(tuple[tuple[str, str, str], ...], _V13.guard_target_authority(artifact))


def guard_target_event_authority(
    artifact: dict[str, Any],
) -> tuple[tuple[Any, ...], ...]:
    return cast(tuple[tuple[Any, ...], ...], _V13.guard_target_event_authority(artifact))


def guard_helper_call_authority(
    artifact: dict[str, Any],
) -> tuple[tuple[str, str], ...]:
    return cast(tuple[tuple[str, str], ...], _V13.guard_helper_call_authority(artifact))


def _literal_generated_value(
    artifact: dict[str, Any],
    *,
    table_name: str,
    column_name: str,
) -> str:
    matches = [
        row
        for row in current_generated_column_census(artifact)
        if row["table_name"] == table_name and row["column_name"] == column_name
    ]
    if len(matches) != 1 or matches[0]["dependency_column_tuple"] != []:
        _error(
            "TASK7_V14_BEFORE_GENERATED_COLUMN_ACCESS_INVALID",
            f"{table_name}.{column_name}:generated literal shape",
        )
    try:
        value = json.loads(str(matches[0]["expression_token_sql"]))
    except json.JSONDecodeError as exc:
        raise ContractArtifactError(
            "TASK7_V14_BEFORE_GENERATED_COLUMN_ACCESS_INVALID",
            f"{table_name}.{column_name}:generated literal encoding",
        ) from exc
    if not isinstance(value, str) or not value:
        _error(
            "TASK7_V14_BEFORE_GENERATED_COLUMN_ACCESS_INVALID",
            f"{table_name}.{column_name}:generated literal value",
        )
    return value


def _task6_guard_row(artifact: dict[str, Any]) -> dict[str, Any]:
    matches = [
        row
        for row in _acl(artifact)["function_rows"]
        if row["authority_id"] == _TASK6_AUTHORITY_ID
        and row["function_signature"] == _TASK6_GUARD_SIGNATURE
    ]
    if len(matches) != 1:
        _error(
            "TASK7_V14_BEFORE_GENERATED_COLUMN_ACCESS_INVALID",
            "task6 guard authority census",
        )
    row = matches[0]
    coordinates = tuple(tuple(coordinate) for coordinate in row["target_event_coordinate_tuple"])
    expected_coordinates = tuple(
        (
            table_name,
            trigger_name,
            "BEFORE",
            event,
            "ROW",
            "O",
            False,
            False,
            False,
        )
        for table_name, trigger_name in row["target_trigger_tuple"]
        for event in ("INSERT", "UPDATE", "DELETE")
    )
    if (
        row["install_scope"] != "CURRENT_0038"
        or row["function_kind"] != "TRIGGER_GUARD"
        or tuple(target[0] for target in row["target_trigger_tuple"]) != _TASK6_TARGETS
        or coordinates != expected_coordinates
    ):
        _error(
            "TASK7_V14_BEFORE_GENERATED_COLUMN_ACCESS_INVALID",
            "task6 target/event authority",
        )
    return cast(dict[str, Any], row)


def _invalid_bare_new_contexts(tokens: tuple[tuple[str, str], ...]) -> tuple[str, ...]:
    """Reject every bare NEW context outside the closed trigger-row allowlist."""

    invalid: list[str] = []
    for ordinal, token in enumerate(tokens):
        if token != ("IDENT", "new"):
            continue
        previous = tokens[ordinal - 1] if ordinal else None
        following = tokens[ordinal + 1] if ordinal + 1 < len(tokens) else None
        if following == ("SYMBOL", "."):
            continue
        if previous == ("IDENT", "return") and following == ("SYMBOL", ";"):
            continue
        wrapper = tokens[ordinal - 2] if ordinal >= 2 else None
        if (
            previous == ("SYMBOL", "(")
            and following == ("SYMBOL", ")")
            and wrapper is not None
            and wrapper[0] == "IDENT"
            and wrapper[1] in {"to_jsonb", "row_to_json"}
        ):
            continue
        invalid.append(f"{ordinal}:{previous!r}:{following!r}")
    return tuple(invalid)


def task6_target_literal_normalization_coverage(
    artifact: dict[str, Any],
) -> tuple[dict[str, Any], ...]:
    """Prove the exact subtract-then-right-restore Task-6 normalization."""

    _task6_guard_row(artifact)
    sql = _function_sql(artifact, _TASK6_GUARD_SIGNATURE)
    tokens = _V13._sql_tokens(sql)
    generated = current_generated_column_authority(artifact)
    expected_literals: dict[str, tuple[str, str]] = {}
    for table_name in _TASK6_ATTACH_TARGETS:
        if generated.get(table_name) != _GENERATED_FIELDS:
            _error(
                "TASK7_V14_BEFORE_GENERATED_COLUMN_ACCESS_INVALID",
                f"{table_name}:generated field tuple",
            )
        expected_literals[table_name] = (
            _literal_generated_value(
                artifact,
                table_name=table_name,
                column_name="run_output_object_class",
            ),
            _literal_generated_value(
                artifact,
                table_name=table_name,
                column_name="run_output_object_kind",
            ),
        )
    if {value[0] for value in expected_literals.values()} != {"NATIVE_ROW"}:
        _error(
            "TASK7_V14_BEFORE_GENERATED_COLUMN_ACCESS_INVALID",
            "task6 generated class domain",
        )

    branch = (
        ("IDENT", "if"),
        ("IDENT", "tg_op"),
        ("SYMBOL", "="),
        ("STRING", "UPDATE"),
        ("IDENT", "and"),
        ("IDENT", "tg_table_name"),
        ("IDENT", "in"),
        ("SYMBOL", "("),
        ("STRING", "methodology_extraction_results"),
        ("SYMBOL", ","),
        ("STRING", "methodology_result_factual_outputs"),
        ("SYMBOL", ")"),
        ("IDENT", "and"),
        ("IDENT", "public"),
        ("SYMBOL", "."),
        ("IDENT", "idis_task6_output_root_attach_is_valid_v1"),
    )
    if _V13._subsequence_count(tokens, branch) != 1:
        _error(
            "TASK7_V14_BEFORE_GENERATED_COLUMN_ACCESS_INVALID",
            "task6 closed target branch",
        )

    invalid_direct = set(_V13._direct_new_columns(tokens)) & set(_GENERATED_FIELDS)
    if invalid_direct:
        _error(
            "TASK7_V14_BEFORE_GENERATED_COLUMN_ACCESS_INVALID",
            f"task6 direct NEW generated:{sorted(invalid_direct)!r}",
        )
    if _V13._whole_new_sink_count(tokens) != 1:
        _error(
            "TASK7_V14_BEFORE_GENERATED_COLUMN_ACCESS_INVALID",
            "task6 whole NEW sink count",
        )
    invalid_bare_contexts = _invalid_bare_new_contexts(tokens)
    if invalid_bare_contexts:
        _error(
            "TASK7_V14_BEFORE_GENERATED_COLUMN_ACCESS_INVALID",
            f"task6 bare NEW context:{invalid_bare_contexts!r}",
        )
    if _V13._json_subtraction_fields(tokens, "new") != (_GENERATED_FIELDS,):
        _error(
            "TASK7_V14_BEFORE_GENERATED_COLUMN_ACCESS_INVALID",
            "task6 generated subtraction tuple",
        )

    result_class = expected_literals["methodology_extraction_results"][0]
    result_kind = expected_literals["methodology_extraction_results"][1]
    factual_class = expected_literals["methodology_result_factual_outputs"][0]
    factual_kind = expected_literals["methodology_result_factual_outputs"][1]
    if result_class != factual_class:
        _error(
            "TASK7_V14_BEFORE_GENERATED_COLUMN_ACCESS_INVALID",
            "task6 generated class mismatch",
        )
    expected_argument = (
        "(pg_catalog.to_jsonb(NEW)-"
        "ARRAY['run_output_object_class','run_output_object_kind']::text[])"
        "||pg_catalog.jsonb_build_object("
        f"'run_output_object_class','{result_class}',"
        "'run_output_object_kind',"
        "CASE TG_TABLE_NAME "
        f"WHEN 'methodology_extraction_results' THEN '{result_kind}' "
        f"WHEN 'methodology_result_factual_outputs' THEN '{factual_kind}' "
        "END)"
    )
    expected_argument_tokens = _V13._sql_tokens(expected_argument)
    if (
        _V13._subsequence_count(tokens, expected_argument_tokens) != 1
        or expected_argument != _MATERIALIZER._NORMALIZED_NEW_ARGUMENT
    ):
        _error(
            "TASK7_V14_BEFORE_GENERATED_COLUMN_ACCESS_INVALID",
            "task6 target-literal normalization",
        )

    helper_call = (
        "public.idis_task6_output_root_attach_is_valid_v1("
        "pg_catalog.to_jsonb(OLD),"
        f"{expected_argument},"
        "NEW.tenant_id,NEW.deal_id,NEW.run_id)"
    )
    if _V13._subsequence_count(tokens, _V13._sql_tokens(helper_call)) != 1:
        _error(
            "TASK7_V14_BEFORE_GENERATED_COLUMN_ACCESS_INVALID",
            "task6 normalized helper call",
        )
    transition_clause = (
        "IF TG_OP='UPDATE' AND TG_TABLE_NAME IN "
        "('methodology_extraction_results','methodology_result_factual_outputs') "
        f"AND {helper_call} THEN RETURN NEW; END IF; "
        "RAISE EXCEPTION 'METHODOLOGY_EXTRACTION_RECORD_IMMUTABLE'"
    )
    if _V13._subsequence_count(tokens, _V13._sql_tokens(transition_clause)) != 1:
        _error(
            "TASK7_V14_BEFORE_GENERATED_COLUMN_ACCESS_INVALID",
            "task6 closed legal transition clause",
        )

    v13 = _MATERIALIZER.project_v14_to_v13(artifact)
    if _function_sql(artifact, _TASK6_HELPER_SIGNATURE) != _function_sql(
        v13,
        _TASK6_HELPER_SIGNATURE,
    ):
        _error(
            "TASK7_V14_BEFORE_GENERATED_COLUMN_ACCESS_INVALID",
            "task6 helper body drift",
        )
    return tuple(
        {
            "authority_id": _TASK6_AUTHORITY_ID,
            "function_signature": _TASK6_GUARD_SIGNATURE,
            "table_name": table_name,
            "generated_column_tuple": list(_GENERATED_FIELDS),
            "literal_class": expected_literals[table_name][0],
            "literal_kind": expected_literals[table_name][1],
            "mode": "PROVEN_TARGET_LITERAL_NORMALIZATION",
        }
        for table_name in _TASK6_ATTACH_TARGETS
    )


def before_generated_column_coverage(
    artifact: dict[str, Any],
) -> tuple[dict[str, Any], ...]:
    """Validate every current BEFORE guard without a generic whole-NEW waiver."""

    generated = current_generated_column_authority(artifact)
    coverage: list[dict[str, Any]] = []
    for function_row in tuple(_acl(artifact)["function_rows"]):
        if (
            function_row["install_scope"] != "CURRENT_0038"
            or function_row["function_kind"] != "TRIGGER_GUARD"
        ):
            continue
        before_targets = tuple(
            dict.fromkeys(
                str(coordinate[0])
                for coordinate in function_row["target_event_coordinate_tuple"]
                if str(coordinate[2]) == "BEFORE"
            )
        )
        if not before_targets:
            continue
        sql = _function_sql(artifact, str(function_row["function_signature"]))
        tokens = _V13._sql_tokens(sql)
        invalid_bare_contexts = _invalid_bare_new_contexts(tokens)
        if invalid_bare_contexts:
            _error(
                "TASK7_V14_BEFORE_GENERATED_COLUMN_ACCESS_INVALID",
                f"{function_row['authority_id']}:bare-new:{invalid_bare_contexts!r}",
            )
        generated_targets = tuple(
            table_name for table_name in before_targets if generated.get(table_name, ())
        )
        authority_id = str(function_row["authority_id"])
        if authority_id == _SOURCE_AUTHORITY_ID:
            try:
                source_coverage = _V13._source_generated_coverage(
                    artifact,
                    function_row,
                    sql,
                )
            except ContractArtifactError as exc:
                raise ContractArtifactError(
                    "TASK7_V14_BEFORE_GENERATED_COLUMN_ACCESS_INVALID",
                    f"source:{exc}",
                ) from exc
            coverage.extend(
                {
                    "authority_id": _SOURCE_AUTHORITY_ID,
                    "function_signature": function_row["function_signature"],
                    "table_name": table_name,
                    "generated_column_tuple": list(columns),
                    "mode": (
                        "EXEMPT_GENERATED_CORPUS_AND_NORMALIZE_WHOLE_NEW"
                        if table_name in {"data_room_packages", "data_room_package_files"}
                        else "NORMALIZE_WHOLE_NEW"
                    ),
                }
                for table_name, columns in source_coverage
            )
            continue
        if authority_id == _TASK6_AUTHORITY_ID:
            coverage.extend(task6_target_literal_normalization_coverage(artifact))
            continue

        direct = set(_V13._direct_new_columns(tokens))
        invalid_direct = {
            (table_name, column)
            for table_name in generated_targets
            for column in generated[table_name]
            if column in direct
        }
        whole_sink_count = _V13._whole_new_sink_count(tokens)
        if invalid_direct:
            _error(
                "TASK7_V14_BEFORE_GENERATED_COLUMN_ACCESS_INVALID",
                f"{authority_id}:direct:{sorted(invalid_direct)!r}",
            )
        if whole_sink_count and generated_targets:
            output_exempt = (
                authority_id == "current-output-bound-v1"
                and _V13._output_bound_whole_row_exemption(tokens)
            )
            admission_projection_exempt = (
                authority_id == "current-append-only-lease-v1"
                and _V13._append_only_admission_projection_exemption(tokens, generated)
            )
            if not (output_exempt or admission_projection_exempt):
                _error(
                    "TASK7_V14_BEFORE_GENERATED_COLUMN_ACCESS_INVALID",
                    f"{authority_id}:whole-new",
                )
            if output_exempt:
                coverage.extend(
                    {
                        "authority_id": authority_id,
                        "function_signature": function_row["function_signature"],
                        "table_name": table_name,
                        "generated_column_tuple": list(generated[table_name]),
                        "mode": "IMMUTABILITY_REJECTION_ONLY",
                    }
                    for table_name in generated_targets
                )
    source_rows = [row for row in coverage if row["authority_id"] == _SOURCE_AUTHORITY_ID]
    task6_rows = [row for row in coverage if row["authority_id"] == _TASK6_AUTHORITY_ID]
    if len(source_rows) != 6 or len(task6_rows) != 2:
        _error(
            "TASK7_V14_BEFORE_GENERATED_COLUMN_ACCESS_INVALID",
            "generated-column coverage census",
        )
    return tuple(coverage)


def before_generated_column_coverage_sha256(artifact: dict[str, Any]) -> str:
    return _h(
        [
            "task7-v14-before-generated-column-coverage-v1",
            list(before_generated_column_coverage(artifact)),
        ]
    )


def validate_before_generated_column_access(artifact: dict[str, Any]) -> None:
    current_generated_column_census(artifact)
    before_generated_column_coverage(artifact)


def _validate_hash_bound_acl(artifact: dict[str, Any]) -> None:
    sql = artifact["sql_authority_registry"]
    acl = sql["guard_function_acl_registry"]
    _expect(
        _h([_V14_SQL_REGISTRY_HASH_DOMAIN, sql["rows"], acl]),
        sql.get("expected_sha256"),
        "v14 SQL authority registry",
    )
    v13_domain_projection = copy.deepcopy(artifact)
    v13_domain_projection["sql_authority_registry"]["expected_sha256"] = _h(
        [_V13_SQL_REGISTRY_HASH_DOMAIN, sql["rows"], acl]
    )
    _V13._validate_hash_bound_acl(v13_domain_projection)


def _validate_guard_function_acl_registry(artifact: dict[str, Any]) -> None:
    _validate_hash_bound_acl(artifact)
    v13_domain_projection = copy.deepcopy(artifact)
    sql = v13_domain_projection["sql_authority_registry"]
    sql["expected_sha256"] = _h(
        [_V13_SQL_REGISTRY_HASH_DOMAIN, sql["rows"], sql["guard_function_acl_registry"]]
    )
    _V13._validate_guard_function_acl_registry(v13_domain_projection)


def _validate_shape(
    raw: bytes,
    artifact: dict[str, Any],
    *,
    require_frozen: bool = False,
) -> None:
    if b"\r" in raw or not raw.endswith(b"\n") or raw.endswith(b"\n\n"):
        _error(
            "TASK7_CONTRACT_ARTIFACT_INVALID",
            "artifact must be LF-only with one terminal newline",
        )
    if tuple(artifact) != _SECTION_ORDER:
        _error("TASK7_CONTRACT_ARTIFACT_INVALID", "section order differs")
    metadata = artifact.get("metadata")
    if not isinstance(metadata, dict) or (
        metadata.get("artifact_schema") != "full-strict-v14-reviewed-preimage-v1"
        or metadata.get("pipeline_contract_version") != "full-strict-v14"
        or not isinstance(metadata.get("append_frozen"), bool)
        or metadata.get("pydantic_version") != "2.12.5"
        or tuple(metadata.get("section_order", ())) != _SECTION_ORDER
    ):
        _error("TASK7_CONTRACT_ARTIFACT_INVALID", "v14 metadata differs")
    if require_frozen and metadata["append_frozen"] is not True:
        _error(
            "TASK7_V14_APPEND_FREEZE_REQUIRED",
            "fixture admission requires frozen v14 bytes",
        )

    _validate_hash_bound_acl(artifact)
    try:
        reviewed = bytes.fromhex(metadata["reviewed_design_utf8_hex"])
        reviewed_value = json.loads(reviewed)
    except (KeyError, TypeError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        raise ContractArtifactError(
            "TASK7_CONTRACT_ARTIFACT_INVALID",
            "v14 reviewed design encoding differs",
        ) from exc
    _expect(
        _sha256_bytes(reviewed),
        metadata.get("reviewed_design_sha256"),
        "v14 reviewed design",
    )
    if (
        not isinstance(reviewed_value, list)
        or reviewed_value[:2]
        != [
            "full-strict-v14-reviewed-design-literal-v1",
            ["pipeline_contract_version", "full-strict-v14"],
        ]
        or reviewed_value[-1] != _MATERIALIZER._reviewed_acl_design_tuple()
    ):
        _error("TASK7_CONTRACT_ARTIFACT_INVALID", "v14 reviewed design differs")

    recipes = artifact["digest_recipe_registry"]["rows"]
    expected_recipe = [
        "pipeline_contract_hash",
        '["pipeline-contract-v14","full-strict-v14",'
        "reviewed_preimage_artifact_hash,ORDERED[component_hashes]]",
    ]
    if recipes[5] != expected_recipe or bytes.fromhex(
        artifact["digest_recipe_registry"]["reviewed_literal_utf8_hex"]
    ) != _canonical_bytes(recipes):
        _error("TASK7_CONTRACT_ARTIFACT_INVALID", "v14 digest recipe differs")

    candidate = copy.deepcopy(artifact)
    candidate["metadata"]["append_frozen"] = False
    try:
        v13 = _MATERIALIZER.project_v14_to_v13(candidate)
        _MATERIALIZER._validate_exact_diff(v13, candidate)
        v12 = _MATERIALIZER._V13.project_v13_to_v12(v13)
    except _MATERIALIZER.MaterializationError as exc:
        raise ContractArtifactError(
            "TASK7_V14_EXACT_DIFF_INVALID",
            "candidate is not the exact reviewed v13 successor",
        ) from exc
    _V13._V12._validate_shape(_render_artifact(v12), v12)
    _validate_guard_function_acl_registry(artifact)
    validate_before_generated_column_access(artifact)
    if invariant_vector(artifact) != _V13.invariant_vector(v13):
        _error(
            "TASK7_V14_INVARIANT_DRIFT",
            f"derived {invariant_vector(artifact)!r}",
        )


def derive_prospective_frozen_bytes(candidate_raw: bytes) -> bytes:
    """Return exact append-frozen bytes without mutating the candidate."""

    candidate = _decode_artifact(candidate_raw)
    _validate_shape(candidate_raw, candidate)
    if candidate["metadata"]["append_frozen"] is not False:
        _error(
            "TASK7_V14_UNFROZEN_CANDIDATE_REQUIRED",
            "prospective freeze requires append_frozen=false",
        )
    frozen = copy.deepcopy(candidate)
    frozen["metadata"]["append_frozen"] = True
    frozen_raw = _render_artifact(frozen)
    _validate_shape(frozen_raw, frozen, require_frozen=True)
    return frozen_raw


def _section_hashes(artifact: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "ordinal": ordinal,
            "name": name,
            "sha256": _sha256_bytes(_canonical_bytes(artifact[name])),
        }
        for ordinal, name in enumerate(_SECTION_ORDER)
    ]


def derive_contract_bytes(
    raw: bytes,
    *,
    validate: bool = True,
) -> dict[str, Any]:
    """Derive every v14 component solely from the supplied artifact bytes."""

    artifact = _decode_artifact(raw)
    artifact_sha256 = _sha256_bytes(raw)
    sections = _section_hashes(artifact)
    reviewed_hash = _h(
        [
            "full-strict-v14-reviewed-preimage-artifact-v1",
            artifact_sha256,
            [[row["ordinal"], row["name"], row["sha256"]] for row in sections],
        ]
    )
    if validate:
        _validate_shape(raw, artifact)
    v13 = _MATERIALIZER.project_v14_to_v13(artifact)
    v12 = _MATERIALIZER._V13.project_v13_to_v12(v13)
    v11 = _V13._V12._MATERIALIZER.project_v12_to_v11(v12)
    components = cast(
        dict[str, str],
        _V13._V12._V11._V10._V9._V7._V6._V5._derive_components(
            v11,
            reviewed_hash,
        ),
    )
    components["sql_authority_registry_hash"] = str(
        artifact["sql_authority_registry"]["expected_sha256"]
    )
    pipeline_hash = _h(
        [
            "pipeline-contract-v14",
            artifact["metadata"]["pipeline_contract_version"],
            reviewed_hash,
            [components[name] for name in _COMPONENT_ORDER],
        ]
    )
    components["pipeline_contract_hash"] = pipeline_hash
    return {
        "fixture_schema": "full-strict-v14-contract-hash-fixture-v1",
        "pipeline_contract_version": "full-strict-v14",
        "artifact_sha256": artifact_sha256,
        "section_hashes": sections,
        "component_hashes": components,
        "activation_assertions": artifact.get("activation_assertions"),
        "FULL_STRICT_V14_CONTRACT_HASH": pipeline_hash,
    }


def _parse_args(argv: list[str]) -> argparse.Namespace:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--artifact",
        type=Path,
        default=root / "contracts" / "full_strict_v14_reviewed_preimage_v1.json",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    try:
        raw = args.artifact.read_bytes()
        artifact = _decode_artifact(raw)
        _validate_shape(raw, artifact)
        if artifact["metadata"]["append_frozen"] is False:
            print("TASK7_V14_FIXTURE_PUBLICATION_REFUSED_UNFROZEN", file=sys.stderr)
            return 2
    except (ContractArtifactError, OSError, UnicodeError, json.JSONDecodeError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print("TASK7_V14_FIXTURE_PUBLICATION_REQUIRES_REVIEWED_FREEZE", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
