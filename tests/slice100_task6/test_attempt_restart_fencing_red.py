"""RED repository contracts for paid-call idempotency, crash, and fencing."""

from __future__ import annotations

import inspect
from pathlib import Path

from tests.slice100_task6._support import require_symbol


def test_attempt_lifecycle_has_only_the_four_approved_states() -> None:
    status = require_symbol(
        "idis.models.methodology_extraction", "MethodologyExtractionAttemptStatus"
    )

    assert {member.value for member in status} == {
        "STARTED",
        "SUCCEEDED",
        "FAILED",
        "OUTCOME_UNKNOWN",
    }


def test_postgres_repository_is_the_only_strict_attempt_repository() -> None:
    repository_cls = require_symbol(
        "idis.persistence.repositories.methodology_extraction",
        "PostgresMethodologyExtractionRepository",
    )

    assert repository_cls.is_durable is True
    assert repository_cls.backend == "postgresql"
    module_path = Path(inspect.getfile(repository_cls))
    source = module_path.read_text(encoding="utf-8")
    assert "class InMemory" not in source
    assert 'persistence_backend = "in_memory"' not in source


def test_every_attempt_write_carries_the_claiming_fencing_token() -> None:
    repository_cls = require_symbol(
        "idis.persistence.repositories.methodology_extraction",
        "PostgresMethodologyExtractionRepository",
    )

    for method_name in (
        "admit_attempt",
        "finalize_attempt_success",
        "finalize_attempt_failure",
        "mark_attempt_outcome_unknown",
    ):
        method = getattr(repository_cls, method_name, None)
        assert callable(method), method_name
        parameters = inspect.signature(method).parameters
        assert "lease_owner_id" in parameters, method_name
        assert "lease_fencing_token" in parameters, method_name


def test_repository_fence_uses_wall_clock_statement_timestamp() -> None:
    repository_cls = require_symbol(
        "idis.persistence.repositories.methodology_extraction",
        "PostgresMethodologyExtractionRepository",
    )
    source = inspect.getsource(repository_cls._require_fence)

    assert "lease_expires_at > statement_timestamp()" in source
    assert "lease_expires_at > now()" not in source


def test_repository_exposes_restart_loaders_instead_of_summary_reconstruction() -> None:
    repository_cls = require_symbol(
        "idis.persistence.repositories.methodology_extraction",
        "PostgresMethodologyExtractionRepository",
    )

    assert callable(getattr(repository_cls, "load_planned_tasks", None))
    assert callable(getattr(repository_cls, "load_terminal_results", None))
    assert callable(getattr(repository_cls, "load_terminal_execution_records", None))
    assert callable(getattr(repository_cls, "load_non_success_attempts", None))
    assert callable(getattr(repository_cls, "load_factual_output_records", None))
    assert callable(getattr(repository_cls, "load_canonical_result_links", None))
    assert callable(getattr(repository_cls, "load_canonical_materialization_snapshot", None))


def test_restart_loaders_preserve_internal_ids_lineage_and_total_order() -> None:
    repository_cls = require_symbol(
        "idis.persistence.repositories.methodology_extraction",
        "PostgresMethodologyExtractionRepository",
    )

    factual_source = inspect.getsource(repository_cls.load_factual_output_records)
    for durable_field in (
        "factual_output_record_id",
        "factual_output_id",
        "task_record_id",
        "coverage_record_id",
        "methodology_question_record_id",
        "accepted_source_span_ids",
        "accepted_locator_sha256s",
    ):
        assert durable_field in factual_source
    assert "ORDER BY" in factual_source
    assert "factual_output_record_id" in factual_source.split("ORDER BY", 1)[1]

    snapshot_source = inspect.getsource(repository_cls.load_canonical_materialization_snapshot)
    for canonical_table in ("claims", "evidence_items", "sanads", "defects"):
        assert canonical_table in snapshot_source
    for lineage_field in (
        "claim_link_id",
        "evidence_link_id",
        "sanad_link_id",
        "defect_link_id",
        "methodology_question_record_id",
        "coverage_record_id",
        "source_span_id",
    ):
        assert lineage_field in snapshot_source
    # Every collection has a unique final tie-breaker, so restart reconstruction
    # never depends on database iteration order.
    assert snapshot_source.count("ORDER BY") == 4

    terminal_source = inspect.getsource(repository_cls.load_terminal_execution_records)
    for terminal_field in (
        "prepared_call_sha256",
        "dispatched_call_sha256",
        "provider_response_sha256",
        "receipt_sha256",
        "prompt_content_sha256",
        "configured_model",
        "observed_model",
        "input_tokens",
        "output_tokens",
        "budget_policy_id",
        "budget_reservation_id",
        "budget_terminal_state",
        "methodology_question_record_id",
        "result_sha256",
    ):
        assert terminal_field in terminal_source
    assert "ORDER BY" in terminal_source
    assert "provider_response" not in terminal_source.replace("provider_response_sha256", "")


def test_repository_source_has_stable_fencing_and_ambiguous_outcome_failures() -> None:
    repository_cls = require_symbol(
        "idis.persistence.repositories.methodology_extraction",
        "PostgresMethodologyExtractionRepository",
    )
    source = Path(inspect.getfile(repository_cls)).read_text(encoding="utf-8")

    # These are externally reviewed stable failure codes, not implementation-shape checks.
    assert "WORKER_LEASE_FENCED" in source
    assert "METHODOLOGY_ATTEMPT_OUTCOME_UNKNOWN" in source
    assert "METHODOLOGY_SOURCE_LOCATOR_REJECTED" in source
    assert source.count("validate_live_source_span_identity") >= 3


def test_attempt_admission_contract_is_one_call_per_task_input() -> None:
    repository_cls = require_symbol(
        "idis.persistence.repositories.methodology_extraction",
        "PostgresMethodologyExtractionRepository",
    )
    method = getattr(repository_cls, "admit_attempt", None)
    assert callable(method)
    parameters = inspect.signature(method).parameters

    for required in (
        "task_record_id",
        "task_input_sha256",
        "prepared_call_sha256",
        "lease_owner_id",
        "lease_fencing_token",
        "provider",
        "configured_model",
        "prompt_id",
        "prompt_content_sha256",
        "budget_reservation_id",
    ):
        assert required in parameters


def test_repository_budget_lineage_uses_verified_policy_and_shared_pricing() -> None:
    repository_cls = require_symbol(
        "idis.persistence.repositories.methodology_extraction",
        "PostgresMethodologyExtractionRepository",
    )
    admission = repository_cls.admit_attempt
    terminal = repository_cls.finalize_attempt_success

    assert "verified_budget_policy" in inspect.signature(admission).parameters
    assert "budget_policy_id" in inspect.signature(terminal).parameters
    admission_source = inspect.getsource(admission)
    terminal_source = inspect.getsource(terminal)
    for governed_binding in (
        "VerifiedActiveProviderBudgetPolicy",
        "resolve_anthropic_standard_price",
        "compute_anthropic_usd",
        "signature_verified",
        "policy_hash",
        "max_input_tokens",
        "max_output_tokens",
    ):
        assert governed_binding in admission_source
    assert 'attempt["budget_policy_id"] != budget_policy_id' in terminal_source
    assert "resolve_anthropic_standard_price" in terminal_source
    assert "compute_anthropic_usd" in terminal_source
    assert "reserved_usd" in terminal_source


def test_canonical_claim_convergence_requires_the_deterministic_claim_id() -> None:
    repository_cls = require_symbol(
        "idis.persistence.repositories.methodology_extraction",
        "PostgresMethodologyExtractionRepository",
    )
    source = inspect.getsource(repository_cls.upsert_canonical_claim)

    # Identity proof is deliberately delegated to the one governed recomputation seam.
    assert "_validated_canonical_claim_identity(" in source
    assert "winner_identity != identity" in source


def test_per_task_terminalization_and_exact_set_step_completion_are_separate_boundaries() -> None:
    repository_cls = require_symbol(
        "idis.persistence.repositories.methodology_extraction",
        "PostgresMethodologyExtractionRepository",
    )
    terminalize = getattr(repository_cls, "finalize_attempt_success", None)
    complete_set = getattr(repository_cls, "complete_execution_step_exact_set", None)
    assert callable(terminalize)
    assert callable(complete_set)

    terminalize_parameters = inspect.signature(terminalize).parameters
    complete_parameters = inspect.signature(complete_set).parameters
    assert "run_step_id" in terminalize_parameters
    for required in (
        "run_step_id",
        "expected_task_ids",
        "lease_owner_id",
        "lease_fencing_token",
    ):
        assert required in complete_parameters

    # This narrow introspection is intentional: section 5.1 forbids a per-task
    # success boundary from masquerading as canonical step completion.
    terminalize_source = inspect.getsource(terminalize)
    complete_source = inspect.getsource(complete_set)
    assert "RUNNING" in terminalize_source
    assert "COMPLETED" not in terminalize_source
    assert "COMPLETED" in complete_source
    assert "UPDATE run_steps" not in complete_source
    for task7_root in (
        "run_output_objects",
        "run_step_outputs",
        "run_expected_consumer_edges",
        "run_consumer_receipts",
    ):
        assert task7_root not in terminalize_source


def test_failed_call_boundary_atomically_refunds_and_fails_step() -> None:
    repository_cls = require_symbol(
        "idis.persistence.repositories.methodology_extraction",
        "PostgresMethodologyExtractionRepository",
    )
    method = getattr(repository_cls, "finalize_attempt_failure", None)
    assert callable(method)
    parameters = inspect.signature(method).parameters

    for required in (
        "attempt_id",
        "run_step_id",
        "budget_reservation_id",
        "budget_policy_id",
        "safe_failure_code",
        "lease_owner_id",
        "lease_fencing_token",
    ):
        assert required in parameters


def test_received_response_failure_is_consumed_and_persists_provider_provenance() -> None:
    repository_cls = require_symbol(
        "idis.persistence.repositories.methodology_extraction",
        "PostgresMethodologyExtractionRepository",
    )
    method = getattr(repository_cls, "finalize_received_response_failure", None)
    assert callable(method)
    parameters = inspect.signature(method).parameters

    for required in (
        "attempt_id",
        "run_step_id",
        "budget_reservation_id",
        "budget_policy_id",
        "safe_failure_code",
        "observed_model",
        "provider_request_id",
        "provider_response_sha256",
        "dispatched_call_sha256",
        "latency_ms",
        "input_tokens",
        "output_tokens",
        "stop_reason",
        "lease_owner_id",
        "lease_fencing_token",
    ):
        assert required in parameters
    source = inspect.getsource(method)
    assert "budget_terminal_state = 'CONSUMED'" in source
    assert "status = 'FAILED'" in source
    restart_source = inspect.getsource(repository_cls.load_non_success_attempts)
    for persisted_field in (
        "observed_model",
        "provider_request_id",
        "provider_response_sha256",
        "dispatched_call_sha256",
        "latency_ms",
        "input_tokens",
        "output_tokens",
        "stop_reason",
    ):
        assert persisted_field in restart_source


def test_terminal_repository_itself_rejects_observed_model_drift() -> None:
    repository_cls = require_symbol(
        "idis.persistence.repositories.methodology_extraction",
        "PostgresMethodologyExtractionRepository",
    )
    # This narrow source assertion is intentional: the reviewed terminal trust
    # boundary must compare provider provenance to its own admitted model.
    required_guard = 'observed_model != attempt["configured_model"]'
    assert required_guard in inspect.getsource(repository_cls.finalize_attempt_success)
    # A returned mismatched model is itself received-invalid evidence: the failure
    # path must persist it exactly under PROVENANCE_INVALID rather than roll back.
    failure_source = inspect.getsource(repository_cls.finalize_received_response_failure)
    assert "METHODOLOGY_PROVIDER_PROVENANCE_INVALID" in failure_source


def test_claim_link_write_derives_exact_coverage_lineage_from_factual_task() -> None:
    repository_cls = require_symbol(
        "idis.persistence.repositories.methodology_extraction",
        "PostgresMethodologyExtractionRepository",
    )
    source = inspect.getsource(repository_cls.persist_claim_links)

    # The durable writer, not its caller, must bind these columns to the
    # factual-output task row protected by the composite FK.
    for required in (
        "methodology_result_factual_outputs",
        "methodology_extraction_tasks",
        "selection_id",
        "coverage_record_id",
        "methodology_version_record_id",
        "methodology_question_record_id",
    ):
        assert required in source


def test_terminalization_proof_binds_distinct_dispatch_and_raw_response_hashes() -> None:
    repository_cls = require_symbol(
        "idis.persistence.repositories.methodology_extraction",
        "PostgresMethodologyExtractionRepository",
    )
    proof_cls = require_symbol(
        "idis.persistence.repositories.methodology_extraction",
        "DurableMethodologyTerminalizationProof",
    )
    method = repository_cls.finalize_attempt_success
    parameters = inspect.signature(method).parameters

    assert "dispatched_call_sha256" in parameters
    assert "provider_response_sha256" in parameters
    assert "dispatched_call_sha256" in proof_cls.__dataclass_fields__
    assert "provider_response_sha256" in proof_cls.__dataclass_fields__
    for committed_binding in (
        "prepared_call_sha256",
        "validated_result_sha256",
        "prospective_receipt_sha256",
        "task_input_sha256",
        "price_catalog_row_sha256",
        "worker_fencing_token",
        "budget_policy_id",
        "consumed_calls",
        "consumed_input_tokens",
        "consumed_output_tokens",
        "consumed_usd",
    ):
        assert committed_binding in proof_cls.__dataclass_fields__
    source = inspect.getsource(method)
    assert "dispatched_call_sha256 != provider_response_sha256" not in source


def test_terminal_receipt_hash_is_computed_inside_the_terminal_transaction() -> None:
    repository_cls = require_symbol(
        "idis.persistence.repositories.methodology_extraction",
        "PostgresMethodologyExtractionRepository",
    )
    method = repository_cls.finalize_attempt_success
    parameters = inspect.signature(method).parameters
    source = inspect.getsource(method)

    assert "receipt_sha256" not in parameters
    assert "validated_result_sha256" in parameters
    assert "prospective_receipt_sha256" in parameters
    assert "prospective_receipt" in source
    assert "dispatched_call_sha256" in source
    assert "provider_response_sha256" in source
    assert "budget_terminal_state" in source
    assert "compute_methodology_provider_receipt_sha256" in source
    assert "_sha256(prospective" not in source


def test_terminal_repository_revalidates_typed_result_and_admitted_source_scope() -> None:
    repository_cls = require_symbol(
        "idis.persistence.repositories.methodology_extraction",
        "PostgresMethodologyExtractionRepository",
    )
    source = inspect.getsource(repository_cls.finalize_attempt_success)

    assert "ValidatedDispatchedMethodologyResult.model_validate" in source
    assert "_validate_terminal_source_scope" in source
    assert "task_input_sha256" in source
    assert "dispatched_call_sha256" in source

    scope_source = inspect.getsource(repository_cls._validate_terminal_source_scope)
    assert "zip(" in scope_source
    assert "strict=True" in scope_source
    assert "accepted_pairs" in scope_source
    assert "compute_methodology_searched_scope_sha256" in scope_source
    assert "expected_searched_span_ids" in scope_source
    assert "expected_searched_document_ids" in scope_source
    assert "result.searched_scope_sha256 != expected_searched_scope_sha256" in scope_source
