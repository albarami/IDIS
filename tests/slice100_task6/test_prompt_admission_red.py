"""RED governance contracts for the Task 6 methodology prompt."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from idis.models.methodology_extraction import exact_canonical_json_bytes
from idis.services.prompts.promotion_policy import RUNTIME_PROMPT_IDS
from tests.slice100_task6._support import require_symbol

PROMPT_ID = "METHODOLOGY_QUESTION_EXTRACTION_V1"
PROMPTS_ROOT = Path(__file__).resolve().parents[2] / "prompts"
PROMPT_CONTENT_SHA256 = "0eab599586b5022ac53766464980119cdd23d9fa23d0117933727c70fecfa9ec"
INPUT_SCHEMA_SHA256 = "56da7b7726356335a788ac9bc3cd6eda94ef2438a7521ef3e09464f020a3bb0b"
OUTPUT_SCHEMA_SHA256 = "36e192b8e5e94f515399b7f87b997728c1df0836c590503952a110f3ad3343bf"


def _write_realistic_promoted_layout(
    tmp_path: Path,
    *,
    prompt_bytes: bytes = b"System line one.\r\nSystem line two.\r\n",
) -> tuple[Path, dict[str, Path]]:
    """Build the same repo-relative prompt layout consumed by production."""

    repo_root = tmp_path / "repo"
    prompts_root = repo_root / "prompts"
    artifact_dir = prompts_root / "methodology_question_extraction" / "1.0.0"
    schema_dir = repo_root / "schemas" / "extraction"
    artifact_dir.mkdir(parents=True)
    schema_dir.mkdir(parents=True)

    input_schema = json.loads(
        (
            PROMPTS_ROOT.parent / "schemas/extraction/methodology_question_extraction_input.json"
        ).read_text(encoding="utf-8")
    )
    output_schema = json.loads(
        (
            PROMPTS_ROOT.parent / "schemas/extraction/methodology_question_extraction_output.json"
        ).read_text(encoding="utf-8")
    )
    hashes = {
        "prompt_content_sha256": hashlib.sha256(prompt_bytes).hexdigest(),
        "input_schema_sha256": hashlib.sha256(exact_canonical_json_bytes(input_schema)).hexdigest(),
        "output_schema_sha256": hashlib.sha256(
            exact_canonical_json_bytes(output_schema)
        ).hexdigest(),
    }
    entry = dict(
        yaml.safe_load((PROMPTS_ROOT / "registry.yaml").read_text(encoding="utf-8"))["prompts"][
            PROMPT_ID
        ]
    )
    entry.update(status="PROD", **hashes)
    registry = {"prompts": {PROMPT_ID: entry}}
    metadata = json.loads(
        (PROMPTS_ROOT / "methodology_question_extraction" / "1.0.0" / "metadata.json").read_text(
            encoding="utf-8"
        )
    )
    metadata.update(status="PROD", **hashes)

    paths = {
        "pointer": prompts_root / "registry.prod.json",
        "registry": prompts_root / "registry.yaml",
        "prompt": artifact_dir / "prompt.md",
        "metadata": artifact_dir / "metadata.json",
        "input": schema_dir / "methodology_question_extraction_input.json",
        "output": schema_dir / "methodology_question_extraction_output.json",
    }
    paths["pointer"].write_text(
        json.dumps(
            {
                "env": "prod",
                "updated_at": "2026-07-21T00:00:00Z",
                "prompts": {PROMPT_ID: "1.0.0"},
            }
        ),
        encoding="utf-8",
    )
    paths["registry"].write_text(yaml.safe_dump(registry), encoding="utf-8")
    paths["prompt"].write_bytes(prompt_bytes)
    paths["metadata"].write_text(json.dumps(metadata), encoding="utf-8")
    paths["input"].write_text(json.dumps(input_schema), encoding="utf-8")
    paths["output"].write_text(json.dumps(output_schema), encoding="utf-8")
    return prompts_root, paths


def _expect_not_promoted(prompts_root: Path) -> None:
    resolve = require_symbol(
        "idis.services.extraction.methodology_task_extractor",
        "resolve_strict_methodology_prompt",
    )
    error_cls = require_symbol(
        "idis.services.extraction.methodology_task_extractor",
        "MethodologyExtractorConfigurationError",
    )
    with pytest.raises(error_cls) as raised:
        resolve(prompts_root=prompts_root)
    assert raised.value.code == "METHODOLOGY_PROMPT_NOT_PROMOTED"


def test_methodology_prompt_is_registered_and_in_runtime_inventory() -> None:
    registry = yaml.safe_load((PROMPTS_ROOT / "registry.yaml").read_text(encoding="utf-8"))
    prompt = registry["prompts"][PROMPT_ID]

    assert prompt["id"] == PROMPT_ID
    assert prompt["status"] == "DRAFT"
    assert prompt["version"] == "1.0.0"
    assert prompt["input_schema"] == (
        "schemas/extraction/methodology_question_extraction_input.json"
    )
    assert prompt["output_schema"] == (
        "schemas/extraction/methodology_question_extraction_output.json"
    )
    assert PROMPT_ID in RUNTIME_PROMPT_IDS
    assert (PROMPTS_ROOT / "methodology_question_extraction" / "1.0.0" / "prompt.md").is_file()
    assert (PROMPTS_ROOT / "methodology_question_extraction" / "1.0.0" / "metadata.json").is_file()
    assert (PROMPTS_ROOT.parent / prompt["input_schema"]).is_file()
    assert (PROMPTS_ROOT.parent / prompt["output_schema"]).is_file()
    input_schema = json.loads(
        (PROMPTS_ROOT.parent / prompt["input_schema"]).read_text(encoding="utf-8")
    )
    output_schema = json.loads(
        (PROMPTS_ROOT.parent / prompt["output_schema"]).read_text(encoding="utf-8")
    )
    assert {
        "methodology_question_record_id",
        "methodology_question_id",
        "source_row_trace",
        "question_text",
        "expected_answer_schema",
        "required_evidence",
        "candidate_spans",
        "predicates",
        "validation_requirements",
        "required_calculations",
    } <= set(input_schema["required"])
    assert set(output_schema["properties"]["outcome"]["enum"]) == {
        "EVIDENCE_FOUND",
        "EVIDENCE_NOT_FOUND",
    }
    value_variants = json.dumps(output_schema["$defs"]["typed_value"], sort_keys=True)
    assert '"boolean"' in value_variants
    assert '"table_row"' in value_variants


def test_prompt_artifact_preserves_and_binds_the_exact_checked_in_bytes() -> None:
    artifact_cls = require_symbol("idis.models.methodology_extraction", "MethodologyPromptArtifact")
    canonical_json = require_symbol("idis.models.methodology_extraction", "canonical_json_bytes")
    prompt_path = PROMPTS_ROOT / "methodology_question_extraction" / "1.0.0" / "prompt.md"
    input_path = (
        PROMPTS_ROOT.parent / "schemas/extraction/methodology_question_extraction_input.json"
    )
    output_path = (
        PROMPTS_ROOT.parent / "schemas/extraction/methodology_question_extraction_output.json"
    )
    raw_prompt = prompt_path.read_text(encoding="utf-8")
    input_schema = json.loads(input_path.read_text(encoding="utf-8"))
    output_schema = json.loads(output_path.read_text(encoding="utf-8"))

    artifact = artifact_cls(
        prompt_id=PROMPT_ID,
        version="1.0.0",
        status="DRAFT",
        content=raw_prompt,
        content_sha256=hashlib.sha256(raw_prompt.encode("utf-8")).hexdigest(),
        input_schema=input_schema,
        input_schema_sha256=hashlib.sha256(canonical_json(input_schema)).hexdigest(),
        output_schema=output_schema,
        output_schema_sha256=hashlib.sha256(canonical_json(output_schema)).hexdigest(),
    )

    assert artifact.content == raw_prompt
    assert artifact.content_sha256 == hashlib.sha256(artifact.content.encode("utf-8")).hexdigest()


def test_checked_in_prompt_pins_are_literal_governed_values() -> None:
    registry_entry = yaml.safe_load((PROMPTS_ROOT / "registry.yaml").read_text(encoding="utf-8"))[
        "prompts"
    ][PROMPT_ID]
    metadata = json.loads(
        (PROMPTS_ROOT / "methodology_question_extraction" / "1.0.0" / "metadata.json").read_text(
            encoding="utf-8"
        )
    )
    literal_pins = {
        "prompt_content_sha256": PROMPT_CONTENT_SHA256,
        "input_schema_sha256": INPUT_SCHEMA_SHA256,
        "output_schema_sha256": OUTPUT_SCHEMA_SHA256,
    }

    assert {key: registry_entry[key] for key in literal_pins} == literal_pins
    assert {key: metadata[key] for key in literal_pins} == literal_pins


def test_strict_resolver_preserves_crlf_bytes_from_a_realistic_promoted_layout(
    tmp_path: Path,
) -> None:
    raw_prompt = b"System line one.\r\nSystem line two.\r\n"
    prompts_root, _paths = _write_realistic_promoted_layout(tmp_path, prompt_bytes=raw_prompt)
    resolve = require_symbol(
        "idis.services.extraction.methodology_task_extractor",
        "resolve_strict_methodology_prompt",
    )

    artifact = resolve(prompts_root=prompts_root)

    assert artifact.content.encode("utf-8") == raw_prompt
    assert artifact.content_sha256 == hashlib.sha256(raw_prompt).hexdigest()


@pytest.mark.parametrize("target", ["prompt", "input", "output"])
def test_strict_resolver_rejects_any_promoted_content_tamper(tmp_path: Path, target: str) -> None:
    prompts_root, paths = _write_realistic_promoted_layout(tmp_path)
    if target == "prompt":
        paths[target].write_bytes(paths[target].read_bytes() + b"tamper")
    else:
        payload = json.loads(paths[target].read_text(encoding="utf-8"))
        payload["description"] = "tampered after promotion"
        paths[target].write_text(json.dumps(payload), encoding="utf-8")

    _expect_not_promoted(prompts_root)


@pytest.mark.parametrize(
    ("surface", "replacement"),
    [
        ("pointer_env", "staging"),
        ("pointer_updated_at", "not-an-iso8601-timestamp"),
        ("pointer_version", "1.0.1"),
        ("registry_id", "FICTIONAL_DIFFERENT_PROMPT_V1"),
        ("metadata_id", "FICTIONAL_DIFFERENT_PROMPT_V1"),
        ("registry_prompt_path", "prompts/alternate/1.0.0/prompt.md"),
        ("registry_metadata_path", "prompts/alternate/1.0.0/metadata.json"),
        ("registry_input_path", "schemas/extraction/alternate_input.json"),
        ("registry_output_path", "schemas/extraction/alternate_output.json"),
        ("metadata_input_ref", "schemas/extraction/alternate_input.json"),
        ("metadata_output_ref", "schemas/extraction/alternate_output.json"),
        ("registry_pin", "f" * 64),
        ("metadata_pin", "f" * 64),
    ],
)
def test_strict_resolver_rejects_pointer_identity_path_or_reference_mismatch(
    tmp_path: Path, surface: str, replacement: str
) -> None:
    prompts_root, paths = _write_realistic_promoted_layout(tmp_path)
    if surface.startswith("pointer_"):
        payload = json.loads(paths["pointer"].read_text(encoding="utf-8"))
        if surface == "pointer_env":
            payload["env"] = replacement
        elif surface == "pointer_updated_at":
            payload["updated_at"] = replacement
        else:
            payload["prompts"][PROMPT_ID] = replacement
        paths["pointer"].write_text(json.dumps(payload), encoding="utf-8")
    elif surface.startswith("registry_"):
        payload = yaml.safe_load(paths["registry"].read_text(encoding="utf-8"))
        entry: dict[str, Any] = payload["prompts"][PROMPT_ID]
        if surface == "registry_id":
            entry["id"] = replacement
        elif surface == "registry_prompt_path":
            alternate = prompts_root.parent / replacement
            alternate.parent.mkdir(parents=True, exist_ok=True)
            alternate.write_bytes(paths["prompt"].read_bytes())
            entry["file_path"] = replacement
        elif surface == "registry_metadata_path":
            alternate = prompts_root.parent / replacement
            alternate.parent.mkdir(parents=True, exist_ok=True)
            alternate.write_bytes(paths["metadata"].read_bytes())
            entry["metadata_path"] = replacement
        elif surface == "registry_input_path":
            alternate = prompts_root.parent / replacement
            alternate.parent.mkdir(parents=True, exist_ok=True)
            alternate.write_bytes(paths["input"].read_bytes())
            entry["input_schema"] = replacement
        elif surface == "registry_output_path":
            alternate = prompts_root.parent / replacement
            alternate.parent.mkdir(parents=True, exist_ok=True)
            alternate.write_bytes(paths["output"].read_bytes())
            entry["output_schema"] = replacement
        else:
            entry["prompt_content_sha256"] = replacement
        paths["registry"].write_text(yaml.safe_dump(payload), encoding="utf-8")
    else:
        payload = json.loads(paths["metadata"].read_text(encoding="utf-8"))
        if surface == "metadata_id":
            payload["prompt_id"] = replacement
        elif surface == "metadata_input_ref":
            payload["input_schema_ref"] = replacement
        elif surface == "metadata_output_ref":
            payload["output_schema_ref"] = replacement
        else:
            payload["prompt_content_sha256"] = replacement
        paths["metadata"].write_text(json.dumps(payload), encoding="utf-8")

    _expect_not_promoted(prompts_root)


def test_strict_resolver_rejects_an_agreeing_but_unauthorized_version(
    tmp_path: Path,
) -> None:
    prompts_root, paths = _write_realistic_promoted_layout(tmp_path)
    pointer = json.loads(paths["pointer"].read_text(encoding="utf-8"))
    pointer["prompts"][PROMPT_ID] = "not-semver"
    paths["pointer"].write_text(json.dumps(pointer), encoding="utf-8")
    registry = yaml.safe_load(paths["registry"].read_text(encoding="utf-8"))
    registry["prompts"][PROMPT_ID]["version"] = "not-semver"
    paths["registry"].write_text(yaml.safe_dump(registry), encoding="utf-8")
    metadata = json.loads(paths["metadata"].read_text(encoding="utf-8"))
    metadata["version"] = "not-semver"
    paths["metadata"].write_text(json.dumps(metadata), encoding="utf-8")

    _expect_not_promoted(prompts_root)


def test_strict_resolver_rejects_non_utf8_prompt_bytes(tmp_path: Path) -> None:
    prompts_root, paths = _write_realistic_promoted_layout(tmp_path)
    paths["prompt"].write_bytes(b"valid prefix\xffinvalid utf8")

    _expect_not_promoted(prompts_root)


def test_task6_does_not_fabricate_a_prod_pointer_for_the_new_prompt() -> None:
    pointer = PROMPTS_ROOT / "registry.prod.json"
    if pointer.is_file():
        assert PROMPT_ID not in pointer.read_text(encoding="utf-8")


def test_strict_runtime_resolver_fails_closed_without_task12_prod_pointer() -> None:
    resolve = require_symbol(
        "idis.services.extraction.methodology_task_extractor",
        "resolve_strict_methodology_prompt",
    )
    error_cls = require_symbol(
        "idis.services.extraction.methodology_task_extractor",
        "MethodologyExtractorConfigurationError",
    )

    try:
        resolve(prompts_root=PROMPTS_ROOT)
    except error_cls as exc:
        assert exc.code == "METHODOLOGY_PROMPT_NOT_PROMOTED"
    else:
        raise AssertionError("Task 6 must not fabricate a PROD methodology prompt pointer")


def test_prompt_input_schema_uses_the_exact_discriminated_cdd_fdd_source_trace() -> None:
    schema = json.loads(
        (
            PROMPTS_ROOT.parent / "schemas/extraction/methodology_question_extraction_input.json"
        ).read_text(encoding="utf-8")
    )
    trace = schema["properties"]["source_row_trace"]
    variants = {variant["properties"]["kind"]["const"]: variant for variant in trace["oneOf"]}

    assert set(variants) == {"cdd_section", "fdd_workbook_row"}
    assert variants["cdd_section"]["additionalProperties"] is False
    assert set(variants["cdd_section"]["required"]) == {
        "kind",
        "source_section",
        "source_sha256",
    }
    assert set(variants["cdd_section"]["properties"]) == {
        "kind",
        "source_section",
        "source_sha256",
    }
    assert variants["fdd_workbook_row"]["additionalProperties"] is False
    assert set(variants["fdd_workbook_row"]["required"]) == {
        "kind",
        "sheet_name",
        "row_number",
        "anchor_cell",
        "source_sha256",
    }
    prompt_text = (
        PROMPTS_ROOT / "methodology_question_extraction" / "1.0.0" / "prompt.md"
    ).read_text(encoding="utf-8")
    assert "frozen source trace" in prompt_text
    assert "source-row trace" not in prompt_text


def test_output_schema_admits_the_explicit_none_period() -> None:
    schema = json.loads(
        (
            PROMPTS_ROOT.parent / "schemas/extraction/methodology_question_extraction_output.json"
        ).read_text(encoding="utf-8")
    )
    variants = {
        variant["properties"]["kind"]["const"]: variant
        for variant in schema["$defs"]["period"]["oneOf"]
    }

    assert set(variants) == {"none", "as_of", "range"}
    assert variants["none"]["additionalProperties"] is False
    assert variants["none"]["required"] == ["kind"]
