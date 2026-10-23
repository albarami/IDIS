"""Host gate for the ruled Linux-Poppler closed list.

Absence on Linux prints one ``LINUX_POPPLER NOT_EXECUTED`` line, records it,
and returns. The wrapper never skips. A present but rejected binding fails
the node. Windows runs the wrapped test unchanged.
"""

from __future__ import annotations

import functools
import inspect
import os
import platform
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from idis.parsers.poppler_runtime import (
    PopplerRuntimeBindingError,
    resolve_pinned_poppler_runtime,
)

LINUX_POPPLER_ABSENT_LINE = "LINUX_POPPLER NOT_EXECUTED: governed prefix absent"
LINUX_POPPLER_PROPERTY = "linux_poppler"
LINUX_POPPLER_REQUIRE_ENV = "IDIS_REQUIRE_LINUX_POPPLER"
_UNAVAILABLE = "POPPLER_RUNTIME_UNAVAILABLE"
_REPO_ROOT = Path(__file__).resolve().parents[1]
_RECORD_PROPERTY: Callable[[str, object], None] | None = None
_LINUX_PROBE: tuple[bool, BaseException | None] | None = None


_CONDITIONAL_NODE_IDS = (
    "tests/slice100_task4/test_complete_pdf_page_census_red.py::"
    "test_image_only_receipt_without_render_or_no_consensus_provenance_fails_closed"
    "[ocr_page_execution_receipt]",
    "tests/slice100_task4/test_complete_pdf_page_census_red.py::"
    "test_image_only_receipt_without_render_or_no_consensus_provenance_fails_closed"
    "[ocr_render_admission]",
    "tests/slice100_task4/test_complete_pdf_page_census_red.py::"
    "test_page_in_no_disposition_fails_closed",
    "tests/slice100_task4/test_disjoint_page_dispositions_composition_red.py::"
    "test_disjoint_signed_watermark_and_image_only_pages_compose_with_native_evidence",
    "tests/slice100_task4/test_disjoint_page_dispositions_composition_red.py::"
    "test_overlapping_watermark_and_image_only_authorizations_stay_fail_closed",
    "tests/slice100_task4/test_ocr_image_only_composition_root_red.py::"
    "test_default_ingestion_composition_fails_all_reviewed_image_only_document",
    "tests/slice100_task4/test_ocr_image_only_composition_root_red.py::"
    "test_default_ingestion_composition_retains_mixed_document_spans_beside_reviewed_page",
)


class LinuxPopplerGatedNodes(tuple[tuple[str, str], ...]):
    __doc__ = (
        "Closed list fixed by the CIR-04H3 run-4 ruling.\n"
        "\n"
        "A row enters LINUX_POPPLER_GATED_NODES only if it was red with\n"
        "POPPLER_RUNTIME_UNAVAILABLE in its traceback or chained cause and green\n"
        "through the governed prefix; a row that passed at baseline without\n"
        "Poppler is never gated.\n"
        "\n"
        "Comparison run 2 (fe2c58a8 + H3, no prefix) check.log SHA-256\n"
        "f8acde68fc2979c4d19c8d489f8c27750aa50dd95fa27da70e66f1f6ff51eef1.\n"
        "Comparison run 4 (fe2c58a8 + H3-1..H3-7b with the governed prefix)\n"
        "check.log SHA-256\n"
        "b0b6ba35eacbafa3c85a51375103b0e5f31cccdd510d789418d5c0418ec1d3f4,\n"
        "row-diff-vs-run2.txt SHA-256\n"
        "5db9fd5218c63a783c57098120c73bd6e6009e533ed7c776168f24dfcfb2c66c,\n"
        "failure-categories.txt SHA-256\n"
        "18725f0fd671230374531a357fb16b2c9168b179c2684a181b2b5306c8d238a8.\n"
        "Gate placement is option B. Chained cause for the seven conditional rows:\n"
        "binding-errors.jsonl SHA-256\n"
        "01c5371d0d1a05dcac0c6ac39b78283fa88e60b0290830055ab4568c3e4194fe.\n"
        "Each conditional record is POPPLER_RUNTIME_UNAVAILABLE raised in that\n"
        "row's OCR worker child:\n"
        "\n" + "\n".join(_CONDITIONAL_NODE_IDS) + "\n"
        "The first three share the module fixture synthetic_mixed_fixture.\n"
        "One setup record, node id\n"
        "test_image_only_receipt_without_render_or_no_consensus_provenance_"
        "fails_closed[ocr_page_execution_receipt]\n"
        "(setup), covers all three. The disjoint and image-only records are calls.\n"
        "\n"
        "Second section. "
        "Finding (e) is closed by option (i): a row whose test-side render resolves "
        "the governed binding is gated; the hosted check job never proves a row "
        "with a renderer the product does not use.\n"
        "The second class is derived mechanically: red in the check configuration "
        "after H3-6 with POPPLER_RUNTIME_UNAVAILABLE and green in hosted check at "
        "checkpoint 22; 'Ungated governed render' is not an allow-list reason.\n"
        "test_watermark_runtime_bounds_red, test_watermark_review_resolution and "
        "test_watermark_runtime_persistence_replay_red reach the governed resolver "
        "through _verified_runtime_config and _manifest; 64 is a floor.\n"
        "Run 5c check.log SHA-256\n"
        "3016b24fa46419e3e48e9fb7cfdd86bcab7ab227bd2fc63f4998d8380a90be4d.\n"
        "second-class.txt SHA-256\n"
        "2584a5e3b869bb0613dd09f2295df0e93bd8e1fe60cd697f589f87ce3f2ace9b.\n"
        "Rows whose binding cause is demonstrated per node by the hook join the "
        "closed list exactly as rows whose block shows it; the product's laundering "
        "of the code does not exclude them.\n"
        "The run-4 exclusion of the three Tesseract-adapter rows rested on a false "
        "premise (the baseline had PATH Poppler) and is withdrawn.\n"
        "binding-errors.jsonl SHA-256\n"
        "3085c49491dd7b217420c769c5e19858f53d9c3e0df58e48294f953be9384ed4.\n"
        "Each record is matched to its node id:\n"
        "tests/test_tesseract_ocr_adapter.py::"
        "test_private_gate_ocr_enabled_remains_aggregate_only (call)\n"
        "tests/test_tesseract_ocr_adapter.py::"
        "test_tesseract_adapter_success_creates_deterministic_spans (call)\n"
        "tests/test_tesseract_ocr_adapter.py::"
        "test_tesseract_adapter_success_creates_deterministic_spans (call)\n"
        "tests/test_tesseract_ocr_adapter.py::"
        "test_private_gate_ocr_enabled_retries_prior_ocr_required_ledger_entry "
        "(call)\n"
        "\n"
        "CIR-04H3-9, 2026-10-19. "
        "H ruling (cir04-h-queue-worker-rows.json). "
        "The five queue-worker rows bind the governed runtime through create_app "
        "→ build_default_ingestion_service (defaults.py:100), the path of their "
        "gated sibling; they are first class and are gated by decoration only.\n"
        "They join only after a prefix-present execution at the current head shows "
        "them green; a second cause returns them for classification.\n"
        "No other row in run6b fails for the governed-Poppler cause.\n"
        "A hosted NOT_EXECUTED result is never a pass.\n"
        "They are the no-database class: the fixture forces is_postgres_configured "
        "false and removes the URL.\n"
        "Complete-input capture run6b check.log SHA-256\n"
        "3dca92fbf1ebb0714476ddcada9ab011a4ac70a6739165882d1d6bdc05f7490f.\n"
    )

    def __new__(cls, values: tuple[tuple[str, str], ...]) -> LinuxPopplerGatedNodes:
        return tuple.__new__(cls, values)


LINUX_POPPLER_GATED_NODES: tuple[tuple[str, str], ...] = LinuxPopplerGatedNodes(
    (
        (
            "tests/slice100_task2/test_strict_audit_wiring.py",
            "test_strict_app_uses_reviewed_database_after_ambient_environment_changes",
        ),
        (
            "tests/slice100_task4/test_complete_pdf_page_census_red.py",
            "test_image_only_receipt_without_render_or_no_consensus_provenance_fails_closed",
        ),
        (
            "tests/slice100_task4/test_complete_pdf_page_census_red.py",
            "test_page_in_no_disposition_fails_closed",
        ),
        (
            "tests/slice100_task4/test_disjoint_page_dispositions_composition_red.py",
            "test_disjoint_signed_watermark_and_image_only_pages_compose_with_native_evidence",
        ),
        (
            "tests/slice100_task4/test_disjoint_page_dispositions_composition_red.py",
            "test_missing_image_only_authorization_stays_fail_closed",
        ),
        (
            "tests/slice100_task4/test_disjoint_page_dispositions_composition_red.py",
            "test_overlapping_watermark_and_image_only_authorizations_stay_fail_closed",
        ),
        (
            "tests/slice100_task4/test_ocr_image_only_composition_root_red.py",
            "test_default_ingestion_composition_executes_real_faint_content_rescue",
        ),
        (
            "tests/slice100_task4/test_ocr_image_only_composition_root_red.py",
            "test_default_ingestion_composition_fails_all_reviewed_image_only_document",
        ),
        (
            "tests/slice100_task4/test_ocr_image_only_composition_root_red.py",
            "test_default_ingestion_composition_retains_mixed_document_spans_beside_reviewed_page",
        ),
        (
            "tests/slice100_task4/test_oversized_pdf_render_admission_red.py",
            "test_pdf_above_pixel_cap_at_72_dpi_fails_before_ocr_with_stable_code",
        ),
        (
            "tests/slice100_task4/test_oversized_pdf_render_admission_red.py",
            "test_synthetic_oversized_poster_steps_down_dpi_and_persists_exact_provenance",
        ),
        (
            "tests/slice100_task4/test_oversized_pdf_render_admission_red.py",
            "test_synthetic_oversized_poster_uses_real_poppler_and_tesseract_path",
        ),
        (
            "tests/slice100_task4/test_pdf_render_rounding_red.py",
            "test_half_pixel_fixture_persists_exact_v2_provenance_at_ingestion_root",
        ),
        (
            "tests/slice100_task4/test_watermark_composition_root_red.py",
            "test_signed_v2_manifest_composition_persists_watermark_terminal_receipt",
        ),
        (
            "tests/test_cir04c3_ingestion_provenance.py",
            "test_rejected_watermark_receipts_persist_no_attempt",
        ),
        (
            "tests/test_cir04c3_ingestion_provenance.py",
            "test_run_route_loads_verified_v2_exclusion_authority",
        ),
        (
            "tests/test_cir04c3_ingestion_provenance.py",
            "test_verified_watermark_exclusion_persists_one_pre_ocr_attempt",
        ),
        (
            "tests/test_cir04c3_ingestion_provenance.py",
            "test_verified_watermark_receipt_writes_registered_schema_ref",
        ),
        (
            "tests/test_run_orchestrator_steps.py",
            "test_api_corpus_exists_but_no_usable_docs_fails_document_preflight",
        ),
        (
            "tests/test_run_orchestrator_steps.py",
            "test_api_mixed_corpus_continues_with_eligible_docs_only",
        ),
        (
            "tests/test_slice100_task1_queue_worker.py",
            "test_strict_api_role_starts_no_in_process_pipeline_worker",
        ),
        (
            "tests/test_slice100_task1_queue_worker.py",
            "test_strict_api_uses_reviewed_role_despite_ambient_mutation",
        ),
        (
            "tests/test_slice100_task1_queue_worker.py",
            "test_strict_full_create_requires_preflight_proof_before_run_row",
        ),
        (
            "tests/test_slice100_task1_queue_worker.py",
            "test_strict_full_create_returns_queued_without_inline_execution",
        ),
        (
            "tests/test_slice100_task1_queue_worker.py",
            "test_strict_proof_failures_are_stable_and_create_no_run",
        ),
        (
            "tests/slice100_task4/test_watermark_batch_attestation_red.py",
            "test_batch_contract_pins_exact_schema_fields_order_and_governance_signing",
        ),
        (
            "tests/slice100_task4/test_watermark_batch_attestation_red.py",
            "test_batch_rejects_an_unresolved_prescan_even_when_its_hash_and_signature_match",
        ),
        (
            "tests/slice100_task4/test_watermark_batch_attestation_red.py",
            "test_batch_rejects_digest_or_exemplar_drift",
        ),
        (
            "tests/slice100_task4/test_watermark_batch_attestation_red.py",
            "test_batch_rejects_order_duplicates_and_schema_drift",
        ),
        (
            "tests/slice100_task4/test_watermark_batch_attestation_red.py",
            "test_batch_rejects_signed_exemplar_candidate_identity_drift",
        ),
        (
            "tests/slice100_task4/test_watermark_batch_attestation_red.py",
            "test_batch_rejects_tampered_signature_and_untrusted_signer",
        ),
        (
            "tests/slice100_task4/test_watermark_batch_attestation_red.py",
            "test_batch_requires_active_half_open_reviewer_interval",
        ),
        (
            "tests/slice100_task4/test_watermark_batch_attestation_red.py",
            "test_v2_manifest_verifies_both_envelopes_and_preserves_ordered_digests",
        ),
        (
            "tests/slice100_task4/test_watermark_batch_attestation_red.py",
            "test_verified_batch_safe_summary_suppresses_private_material",
        ),
        (
            "tests/slice100_task4/test_watermark_classifier_domains_red.py",
            "test_canonical_exemplar_domain_rejects_degenerate_or_wrong_geometry_mutations",
        ),
        (
            "tests/slice100_task4/test_watermark_classifier_domains_red.py",
            "test_canonical_exemplar_reference_requires_an_integer_page_number",
        ),
        (
            "tests/slice100_task4/test_watermark_classifier_domains_red.py",
            "test_runtime_never_sends_signed_128x128_material_to_full_raster_preparation",
        ),
        (
            "tests/slice100_task4/test_watermark_classifier_domains_red.py",
            "test_signed_canonical_mask_is_not_revalidated_as_a_full_resolution_faint_raster",
        ),
        (
            "tests/slice100_task4/test_watermark_default_api_route_red.py",
            "test_default_api_upload_admits_signed_watermark_only_document",
        ),
        (
            "tests/slice100_task4/test_watermark_ingestion_cap_red.py",
            "test_fresh_service_counts_durable_classifier_receipts_before_persistence",
        ),
        (
            "tests/slice100_task4/test_watermark_ingestion_cap_red.py",
            "test_ninth_company_one_classifier_page_fails_before_document_persistence",
        ),
        (
            "tests/slice100_task4/test_watermark_page_detector_red.py",
            "test_any_content_or_incomplete_rescue_bypasses_classifier",
        ),
        (
            "tests/slice100_task4/test_watermark_page_detector_red.py",
            "test_boolean_page_number_cannot_masquerade_as_page_one",
        ),
        (
            "tests/slice100_task4/test_watermark_page_detector_red.py",
            "test_classification_is_byte_deterministic_and_does_not_mutate_inputs",
        ),
        (
            "tests/slice100_task4/test_watermark_page_detector_red.py",
            "test_faint_signature_bounds_are_inclusive",
        ),
        (
            "tests/slice100_task4/test_watermark_page_detector_red.py",
            "test_faint_signature_fails_immediately_outside_exact_bounds",
        ),
        (
            "tests/slice100_task4/test_watermark_page_detector_red.py",
            "test_provenance_is_complete_but_never_creates_evidence_text_or_spans",
        ),
        (
            "tests/slice100_task4/test_watermark_page_detector_red.py",
            "test_render_dpi_requires_the_exact_pinned_integer_type",
        ),
        (
            "tests/slice100_task4/test_watermark_page_detector_red.py",
            "test_signed_exemplar_canonical_bitmap_drift_fails_closed",
        ),
        (
            "tests/slice100_task4/test_watermark_page_detector_red.py",
            "test_signed_exemplar_classifies_itself_with_exact_similarity",
        ),
        (
            "tests/slice100_task4/test_watermark_page_detector_red.py",
            "test_signed_exemplar_policy_binding_drift_fails_closed",
        ),
        (
            "tests/slice100_task4/test_watermark_page_detector_red.py",
            "test_structurally_similar_fictional_stamp_clears_strict_threshold",
        ),
        (
            "tests/slice100_task4/test_watermark_page_detector_red.py",
            "test_unrelated_faint_structure_remains_hard_blocked",
        ),
        (
            "tests/slice100_task4/test_watermark_pre_ocr_classifier.py",
            "test_pre_ocr_classifier_rejects_non_grayscale_or_wrong_sized_candidates",
        ),
        (
            "tests/slice100_task4/test_watermark_pre_ocr_classifier.py",
            "test_pre_ocr_classifier_requires_an_exact_integer_zero_native_text_count",
        ),
        (
            "tests/slice100_task4/test_watermark_pre_ocr_classifier.py",
            "test_real_512x512_candidate_emits_typed_pre_ocr_v2_without_terminal_claims",
        ),
        (
            "tests/slice100_task4/test_watermark_review_resolution.py",
            "test_resolved_prescan_is_cross_bound_and_exposes_only_safe_image_page_records",
        ),
        (
            "tests/slice100_task4/test_watermark_review_resolution.py",
            "test_resolved_prescan_mutation_fails_closed",
        ),
        (
            "tests/slice100_task4/test_watermark_runtime_bounds_red.py",
            "test_classification_does_not_start_after_render_exhausts_original_ocr_deadline",
        ),
        (
            "tests/slice100_task4/test_watermark_runtime_bounds_red.py",
            "test_oversized_page_is_rejected_before_parent_process_rasterization",
        ),
        (
            "tests/slice100_task4/test_watermark_runtime_persistence_replay_red.py",
            "test_cached_mixed_document_replays_only_with_durable_substantive_span",
        ),
        (
            "tests/slice100_task4/test_watermark_runtime_persistence_replay_red.py",
            "test_cached_terminal_response_replays_only_against_current_v2_admission",
        ),
        (
            "tests/slice100_task4/test_watermark_runtime_persistence_replay_red.py",
            "test_mixed_document_excludes_only_watermark_page_and_keeps_substantive_span",
        ),
        (
            "tests/slice100_task4/test_watermark_runtime_persistence_replay_red.py",
            "test_replay_rejects_current_admission_binding_drift",
        ),
        (
            "tests/slice100_task4/test_watermark_runtime_persistence_replay_red.py",
            "test_source_package_revalidates_v2_receipt_before_membership",
        ),
        (
            "tests/slice100_task4/test_watermark_runtime_persistence_replay_red.py",
            "test_terminal_watermark_classification_persists_v2_and_safe_audit",
        ),
        (
            "tests/slice100_task4/test_watermark_runtime_persistence_replay_red.py",
            "test_worker_threads_current_v2_verifier_into_preflight_and_rejects_drift",
        ),
        (
            "tests/slice100_task4/test_watermark_runtime_provenance_labels_red.py",
            "test_durable_labels_match_spatial_foreground_dice_pipeline",
        ),
        (
            "tests/slice100_task4/test_watermark_v2_loader_readiness.py",
            "test_loader_accepts_canonical_sorted_manifest_member_order",
        ),
        (
            "tests/slice100_task4/test_watermark_v2_loader_readiness.py",
            "test_secure_v2_loader_and_real_collector_admit_exact_hash_bound_summary",
        ),
        (
            "tests/slice100_task4/test_watermark_v2_loader_readiness.py",
            "test_secure_v2_loader_does_not_mutate_the_caller_manifest",
        ),
        (
            "tests/slice100_task4/test_watermark_v2_loader_readiness.py",
            "test_secure_v2_loader_rejects_expired_batch",
        ),
        (
            "tests/slice100_task4/test_watermark_v2_loader_readiness.py",
            "test_secure_v2_loader_rejects_invalid_batch_signature",
        ),
        (
            "tests/test_tesseract_ocr_adapter.py",
            "test_private_gate_ocr_enabled_retries_prior_ocr_required_ledger_entry",
        ),
        (
            "tests/test_tesseract_ocr_adapter.py",
            "test_private_gate_ocr_enabled_remains_aggregate_only",
        ),
        (
            "tests/test_tesseract_ocr_adapter.py",
            "test_tesseract_adapter_success_creates_deterministic_spans",
        ),
    )
)


class LinuxPopplerDatabaseNodes(tuple[tuple[str, str], ...]):
    __doc__ = (
        "Database-class subset of LINUX_POPPLER_GATED_NODES for CIR-04H3-6g.\n"
        "\n"
        "A row belongs here only when it is a first-class closed-list row and\n"
        "its fixture dependencies reach app_engine or is_postgres_configured.\n"
        "CIR-04H3-6g H ruling (cir04h3-6g-ruling.json, 2026-10-19): this class\n"
        "is exactly the three app_engine rows of\n"
        "tests/test_cir04c3_ingestion_provenance.py.\n"
        "CIR-04H3-6g J ruling (cir04-j-h3-6g-ruling.json, 2026-10-19): H rules\n"
        "the membership of LINUX_POPPLER_DATABASE_NODES and the census pins it.\n"
        "A database-class skip in the hosted check job is a named pre-existing\n"
        "skip, never a pass; its executed evidence is the c3 database-class file.\n"
    )

    def __new__(cls, values: tuple[tuple[str, str], ...]) -> LinuxPopplerDatabaseNodes:
        return tuple.__new__(cls, values)


LINUX_POPPLER_DATABASE_NODES: tuple[tuple[str, str], ...] = LinuxPopplerDatabaseNodes(
    (
        (
            "tests/test_cir04c3_ingestion_provenance.py",
            "test_rejected_watermark_receipts_persist_no_attempt",
        ),
        (
            "tests/test_cir04c3_ingestion_provenance.py",
            "test_verified_watermark_exclusion_persists_one_pre_ocr_attempt",
        ),
        (
            "tests/test_cir04c3_ingestion_provenance.py",
            "test_verified_watermark_receipt_writes_registered_schema_ref",
        ),
    )
)


def _evaluate_linux_probe() -> tuple[bool, BaseException | None]:
    if platform.system() != "Linux":
        return False, None
    try:
        resolve_pinned_poppler_runtime()
    except PopplerRuntimeBindingError as exc:
        if exc.code == _UNAVAILABLE:
            return True, None
        return False, exc
    return False, None


def linux_governed_poppler_absent() -> bool:
    """Return whether this Linux session has no governed Poppler prefix.

    The resolver runs once per process. ``POPPLER_RUNTIME_UNAVAILABLE`` on
    Linux is absence. Any other binding error is re-raised. Windows returns
    False without consulting the resolver.
    """

    global _LINUX_PROBE
    if _LINUX_PROBE is None:
        _LINUX_PROBE = _evaluate_linux_probe()
    absent, error = _LINUX_PROBE
    if error is not None:
        raise error
    return absent


def _published_signature(test_fn: Callable[..., Any]) -> inspect.Signature:
    signature = inspect.signature(test_fn)
    if "record_property" in signature.parameters:
        return signature
    parameters = [
        *signature.parameters.values(),
        inspect.Parameter("record_property", inspect.Parameter.KEYWORD_ONLY),
    ]
    return signature.replace(parameters=parameters)


def _call_test(
    test_fn: Callable[..., Any],
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
    *,
    accepts_recorder: bool,
) -> Any:
    call_kwargs = kwargs
    if not accepts_recorder:
        call_kwargs = {key: value for key, value in kwargs.items() if key != "record_property"}
    return test_fn(*args, **call_kwargs)


def requires_linux_governed_poppler(test_fn: Callable[..., Any]) -> Callable[..., Any]:
    """Run the test unless Linux has no governed Poppler prefix.

    Absence prints ``LINUX_POPPLER NOT_EXECUTED: governed prefix absent``,
    records that line on ``linux_poppler``, and returns. ``IDIS_REQUIRE_LINUX_POPPLER=1``
    fails the node. Any other binding code fails the node. The wrapper never skips.
    """

    signature = inspect.signature(test_fn)
    accepts_recorder = "record_property" in signature.parameters

    @functools.wraps(test_fn)
    def _run(*args: Any, **kwargs: Any) -> Any:
        supplied = kwargs.get("record_property")
        recorder = supplied if callable(supplied) else _RECORD_PROPERTY
        if not linux_governed_poppler_absent():
            return _call_test(test_fn, args, kwargs, accepts_recorder=accepts_recorder)
        print(LINUX_POPPLER_ABSENT_LINE)
        if recorder is not None:
            recorder(LINUX_POPPLER_PROPERTY, LINUX_POPPLER_ABSENT_LINE)
        if os.environ.get(LINUX_POPPLER_REQUIRE_ENV) == "1":
            pytest.fail(LINUX_POPPLER_ABSENT_LINE, pytrace=False)
        return None

    _run.__signature__ = _published_signature(test_fn)  # type: ignore[attr-defined]
    # Pytest follows __wrapped__ back to the undecorated signature, which
    # would drop the injected record_property fixture.
    del _run.__wrapped__
    return _run


def _node_function(node_id: str) -> str:
    head = node_id.split("[", 1)[0]
    return head.rsplit("::", 1)[-1]


def _node_module(node_id: str) -> str:
    return node_id.split("::", 1)[0].replace("\\", "/")


def collected_linux_poppler_node_ids() -> tuple[str, ...]:
    """Return every collected parametrisation of ``LINUX_POPPLER_GATED_NODES``."""

    import io
    from contextlib import redirect_stderr, redirect_stdout

    wanted = set(LINUX_POPPLER_GATED_NODES)
    found: list[str] = []

    class _Collect:
        def pytest_collection_modifyitems(
            self, config: pytest.Config, items: list[pytest.Item]
        ) -> None:
            del config
            for item in items:
                node_id = item.nodeid.replace("\\", "/")
                key = (_node_module(node_id), _node_function(node_id))
                if key in wanted:
                    found.append(node_id)

    modules = sorted(
        {str((_REPO_ROOT / module).resolve()) for module, _name in LINUX_POPPLER_GATED_NODES}
    )
    stdout = io.StringIO()
    stderr = io.StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        code = pytest.main(
            [
                "--rootdir",
                str(_REPO_ROOT),
                "--collect-only",
                "-q",
                "-p",
                "no:cacheprovider",
                *modules,
            ],
            plugins=[_Collect()],
        )
    if code != 0:
        sys.stderr.write(stderr.getvalue())
        sys.stderr.write(stdout.getvalue())
        raise SystemExit(code)
    return tuple(found)


def main(argv: list[str] | None = None) -> int:
    """Print collected closed-list node ids, one bare id per line.

    ``--node-ids`` prints the full set. ``--node-ids --database-class`` prints
    the database class. ``--node-ids --no-database-class`` prints the remainder.
    """

    args = list(sys.argv[1:] if argv is None else argv)
    if args == ["--node-ids"]:
        database_class = False
    elif args == ["--node-ids", "--database-class"]:
        database_class = True
    elif args == ["--node-ids", "--no-database-class"]:
        database_class = False
    else:
        return 2
    nodes = collected_linux_poppler_node_ids()
    if args != ["--node-ids"]:
        database = set(LINUX_POPPLER_DATABASE_NODES)
        nodes = tuple(
            node_id
            for node_id in nodes
            if ((_node_module(node_id), _node_function(node_id)) in database) is database_class
        )
    for node_id in nodes:
        print(node_id)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
