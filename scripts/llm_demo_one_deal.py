"""Single-deal LLM demo script — runs the full IDIS pipeline on one GDBS deal.

Loads a GDBS-FULL deal, runs the full pipeline (INGEST_CHECK → EXTRACT →
GRADE → CALC → DEBATE) with real or deterministic LLM calls, and prints
a human-readable summary.

Usage:
    python -m scripts.llm_demo_one_deal --deal-key deal_001 --backend anthropic
    python -m scripts.llm_demo_one_deal --backend deterministic
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import uuid
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("llm_demo")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
GDBS_FULL_PATH = PROJECT_ROOT / "datasets" / "gdbs_full"

VALID_BACKENDS = ("deterministic", "anthropic")
SEPARATOR = "=" * 72

SEMANTIC_TO_FORMAT_DOC_TYPE: dict[str, str] = {
    "PITCH_DECK": "PPTX",
    "FINANCIAL_MODEL": "XLSX",
    "TERM_SHEET": "PDF",
    "DATA_ROOM": "PDF",
}


def _find_project_root() -> Path:
    """Walk up from this file to find pyproject.toml."""
    current = Path(__file__).resolve().parent
    while current != current.parent:
        if (current / "pyproject.toml").exists():
            return current
        current = current.parent
    raise FileNotFoundError("Cannot locate project root (no pyproject.toml found)")


def _parse_args() -> argparse.Namespace:
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(
        description="Run the full IDIS pipeline on a single GDBS-FULL deal.",
    )
    parser.add_argument(
        "--deal-key",
        default=None,
        help="Deal key from manifest (e.g. deal_001). Default: first deal.",
    )
    parser.add_argument(
        "--backend",
        default="anthropic",
        choices=VALID_BACKENDS,
        help="LLM backend: deterministic or anthropic (default: anthropic).",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Show full agent output content (not truncated).",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Show minimal debate summary only.",
    )
    return parser.parse_args()


def _load_deal(deal_key: str | None) -> Any:
    """Load a single deal via GDBSLoader.

    Args:
        deal_key: Deal key to load, or None for the first deal.

    Returns:
        GDBSDeal instance.
    """
    from idis.testing.gdbs_loader import GDBSLoader

    loader = GDBSLoader(GDBS_FULL_PATH)
    dataset = loader.load()

    if deal_key is None:
        deal = dataset.deals[0]
        logger.info("No --deal-key specified, using first deal: %s", deal.deal_key)
    else:
        deal = dataset.get_deal(deal_key)
        if deal is None:
            available = [d.deal_key for d in dataset.deals[:10]]
            logger.error(
                "Deal key '%s' not found. Available (first 10): %s",
                deal_key,
                available,
            )
            sys.exit(1)

    return deal


def _build_documents_from_deal(deal: Any) -> list[dict[str, Any]]:
    """Convert GDBS deal artifacts + spans into the document format the pipeline expects.

    Args:
        deal: GDBSDeal instance.

    Returns:
        List of document dicts with doc_type, document_id, spans.
    """
    docs_by_id: dict[str, dict[str, Any]] = {}

    for artifact in deal.artifacts:
        doc_id = artifact.get("artifact_id", str(uuid.uuid4()))
        raw_doc_type = artifact.get("doc_type", "PITCH_DECK")
        doc_type = SEMANTIC_TO_FORMAT_DOC_TYPE.get(raw_doc_type, raw_doc_type)
        docs_by_id[doc_id] = {
            "document_id": doc_id,
            "doc_type": doc_type,
            "document_name": artifact.get("filename", doc_id),
            "spans": [],
        }

    for span in deal.spans:
        artifact_id = span.get("artifact_id")
        if artifact_id and artifact_id in docs_by_id:
            docs_by_id[artifact_id]["spans"].append(
                {
                    "span_id": span.get("span_id", str(uuid.uuid4())),
                    "text_excerpt": span.get("text_excerpt", ""),
                    "locator": span.get("locator", {}),
                    "span_type": span.get("span_type", "TEXT"),
                }
            )
        elif artifact_id:
            docs_by_id[artifact_id] = {
                "document_id": artifact_id,
                "doc_type": "PPTX",
                "document_name": artifact_id,
                "spans": [
                    {
                        "span_id": span.get("span_id", str(uuid.uuid4())),
                        "text_excerpt": span.get("text_excerpt", ""),
                        "locator": span.get("locator", {}),
                        "span_type": span.get("span_type", "TEXT"),
                    }
                ],
            }

    documents = [doc for doc in docs_by_id.values() if doc["spans"]]

    if not documents and deal.spans:
        fallback_doc_id = str(uuid.uuid4())
        span_dicts = [
            {
                "span_id": s.get("span_id", str(uuid.uuid4())),
                "text_excerpt": s.get("text_excerpt", ""),
                "locator": s.get("locator", {}),
                "span_type": s.get("span_type", "TEXT"),
            }
            for s in deal.spans
        ]
        documents = [
            {
                "document_id": fallback_doc_id,
                "doc_type": "PPTX",
                "document_name": "gdbs_combined",
                "spans": span_dicts,
            }
        ]

    return documents


def _configure_backend(backend: str) -> None:
    """Set env vars for the chosen backend. Fail-closed for anthropic.

    Args:
        backend: 'deterministic' or 'anthropic'.
    """
    if backend == "anthropic":
        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not api_key:
            logger.error(
                "ANTHROPIC_API_KEY not set. Either export it or use --backend deterministic."
            )
            sys.exit(1)
        os.environ["IDIS_EXTRACT_BACKEND"] = "anthropic"
        os.environ["IDIS_DEBATE_BACKEND"] = "anthropic"
        logger.info("Backend: anthropic (real LLM calls)")
    else:
        os.environ["IDIS_EXTRACT_BACKEND"] = "deterministic"
        os.environ["IDIS_DEBATE_BACKEND"] = "deterministic"
        logger.info("Backend: deterministic (no LLM calls)")


def _build_extraction_fn() -> Any:
    """Build the extraction callable, mirroring runs.py _run_snapshot_extraction."""
    from idis.audit.sink import InMemoryAuditSink
    from idis.services.claims.service import ClaimService
    from idis.services.extraction.chunking.service import ChunkingService
    from idis.services.extraction.confidence.scorer import ConfidenceScorer
    from idis.services.extraction.extractors.claim_extractor import LLMClaimExtractor
    from idis.services.extraction.pipeline import ExtractionPipeline
    from idis.services.extraction.resolution.conflict_detector import ConflictDetector
    from idis.services.extraction.resolution.deduplicator import Deduplicator

    root = _find_project_root()

    prompt_path = root / "prompts" / "extract_claims" / "1.0.0" / "prompt.md"
    if not prompt_path.exists():
        raise FileNotFoundError(f"Prompt file not found: {prompt_path}")
    prompt_text = prompt_path.read_text(encoding="utf-8")

    schema_path = root / "schemas" / "extraction" / "extract_claims_output.json"
    if not schema_path.exists():
        raise FileNotFoundError(f"Schema file not found: {schema_path}")
    with open(schema_path, encoding="utf-8") as f:
        output_schema: dict[str, Any] = json.load(f)

    llm_client = _build_llm_client_for_extraction()
    scorer = ConfidenceScorer()
    extractor = LLMClaimExtractor(
        llm_client=llm_client,
        prompt_text=prompt_text,
        output_schema=output_schema,
        confidence_scorer=scorer,
    )

    def extract_fn(
        *,
        run_id: str,
        tenant_id: str,
        deal_id: str,
        documents: list[dict[str, Any]],
    ) -> dict[str, Any]:
        audit_sink = InMemoryAuditSink()
        claim_service = ClaimService(tenant_id=tenant_id, audit_sink=audit_sink)
        pipeline = ExtractionPipeline(
            chunking_service=ChunkingService(),
            claim_extractor=extractor,
            deduplicator=Deduplicator(),
            conflict_detector=ConflictDetector(),
            claim_service=claim_service,
            audit_sink=audit_sink,
        )
        result = pipeline.run(
            run_id=run_id,
            tenant_id=tenant_id,
            deal_id=deal_id,
            documents=documents,
        )
        return {
            "status": result.status,
            "created_claim_ids": result.created_claim_ids,
            "chunk_count": result.chunk_count,
            "unique_claim_count": result.unique_claim_count,
            "conflict_count": result.conflict_count,
        }

    return extract_fn


def _build_llm_client_for_extraction() -> Any:
    """Build extraction LLM client based on env, mirroring runs.py."""
    backend = os.environ.get("IDIS_EXTRACT_BACKEND", "deterministic")
    if backend == "anthropic":
        from idis.services.extraction.extractors.anthropic_client import AnthropicLLMClient

        model = os.environ.get("IDIS_ANTHROPIC_MODEL_EXTRACT", "claude-sonnet-4-20250514")
        return AnthropicLLMClient(model=model)

    from idis.services.extraction.extractors.llm_client import DeterministicLLMClient

    return DeterministicLLMClient()


def _build_grade_fn() -> Any:
    """Build the grading callable, mirroring runs.py _run_snapshot_auto_grade."""
    from idis.services.sanad.auto_grade import auto_grade_claims_for_run

    def grade_fn(
        *,
        run_id: str,
        tenant_id: str,
        deal_id: str,
        created_claim_ids: list[str],
        audit_sink: Any,
    ) -> dict[str, Any]:
        if not created_claim_ids:
            return {
                "graded_count": 0,
                "failed_count": 0,
                "total_defects": 0,
                "all_failed": False,
            }
        grade_result = auto_grade_claims_for_run(
            run_id=run_id,
            tenant_id=tenant_id,
            deal_id=deal_id,
            created_claim_ids=created_claim_ids,
            audit_sink=audit_sink,
        )
        return {
            "graded_count": grade_result.graded_count,
            "failed_count": grade_result.failed_count,
            "total_defects": grade_result.total_defects,
            "all_failed": grade_result.all_failed,
        }

    return grade_fn


def _build_calc_fn() -> Any:
    """Build the calc callable, mirroring runs.py _run_snapshot_calc."""

    def calc_fn(
        *,
        run_id: str,
        tenant_id: str,
        deal_id: str,
        created_claim_ids: list[str],
        calc_types: list[Any] | None = None,
    ) -> dict[str, Any]:
        if not created_claim_ids:
            return {"calc_ids": [], "reproducibility_hashes": []}
        return {
            "calc_ids": [],
            "reproducibility_hashes": [],
            "claim_count": len(created_claim_ids),
        }

    return calc_fn


def _build_debate_fn(deal: Any = None) -> Any:
    """Build the debate callable, mirroring runs.py _run_full_debate.

    Args:
        deal: Optional GDBSDeal for rich context metadata.
    """
    from idis.debate.orchestrator import DebateOrchestrator
    from idis.debate.roles.llm_role_runner import DebateContext
    from idis.models.debate import DebateConfig, DebateState

    def debate_fn(
        *,
        run_id: str,
        tenant_id: str,
        deal_id: str,
        created_claim_ids: list[str],
        calc_ids: list[str],
    ) -> dict[str, Any]:
        context = DebateContext(
            deal_name=deal.company_name if deal else deal_id,
            deal_sector=deal.sector if deal else "Unknown",
            deal_stage=deal.stage if deal else "Unknown",
            deal_summary=deal.scenario if deal else "",
            claims=[
                {
                    "claim_id": cid,
                    "claim_text": "",
                    "claim_class": "",
                    "sanad_grade": "",
                    "source_doc": "",
                    "confidence": 0.0,
                }
                for cid in created_claim_ids
            ],
            calc_results=[
                {
                    "calc_id": cid,
                    "calc_name": "",
                    "result_value": "",
                    "input_claim_ids": [],
                }
                for cid in calc_ids
            ],
            conflicts=[],
        )

        state = DebateState(
            tenant_id=tenant_id,
            deal_id=deal_id,
            claim_registry_ref=f"claims://{run_id}",
            sanad_graph_ref=f"sanad://{run_id}",
            round_number=1,
        )
        role_runners = _build_debate_role_runners(context=context)
        orchestrator = DebateOrchestrator(config=DebateConfig(), role_runners=role_runners)
        final_state = orchestrator.run(state)
        gate_failure = orchestrator.get_gate_failure()
        muhasabah_passed = gate_failure is None

        return {
            "debate_id": run_id,
            "stop_reason": (final_state.stop_reason.value if final_state.stop_reason else None),
            "round_number": final_state.round_number,
            "muhasabah_passed": muhasabah_passed,
            "agent_output_count": len(final_state.agent_outputs),
            "agent_outputs": final_state.agent_outputs,
        }

    return debate_fn


def _build_debate_role_runners(context: Any = None) -> Any:
    """Build role runners mirroring runs.py _build_debate_role_runners.

    Args:
        context: Optional DebateContext with rich pipeline data for LLM agents.
    """
    from idis.debate.orchestrator import RoleRunners

    backend = os.environ.get("IDIS_DEBATE_BACKEND", "deterministic")

    if backend != "anthropic":
        return RoleRunners()

    from idis.debate.roles.llm_role_runner import LLMRoleRunner
    from idis.models.debate import DebateRole
    from idis.services.extraction.extractors.anthropic_client import AnthropicLLMClient

    default_model = os.environ.get(
        "IDIS_ANTHROPIC_MODEL_DEBATE_DEFAULT", "claude-sonnet-4-20250514"
    )
    arbiter_model = os.environ.get("IDIS_ANTHROPIC_MODEL_DEBATE_ARBITER", "claude-opus-4-20250514")

    prompts = _load_debate_prompts()
    default_client = AnthropicLLMClient(model=default_model)
    arbiter_client = AnthropicLLMClient(model=arbiter_model)

    return RoleRunners(
        advocate=LLMRoleRunner(
            role=DebateRole.ADVOCATE,
            llm_client=default_client,
            system_prompt=prompts["advocate"],
            context=context,
        ),
        sanad_breaker=LLMRoleRunner(
            role=DebateRole.SANAD_BREAKER,
            llm_client=default_client,
            system_prompt=prompts["sanad_breaker"],
            context=context,
        ),
        contradiction_finder=LLMRoleRunner(
            role=DebateRole.CONTRADICTION_FINDER,
            llm_client=default_client,
            system_prompt=prompts["contradiction_finder"],
            context=context,
        ),
        risk_officer=LLMRoleRunner(
            role=DebateRole.RISK_OFFICER,
            llm_client=default_client,
            system_prompt=prompts["risk_officer"],
            context=context,
        ),
        arbiter=LLMRoleRunner(
            role=DebateRole.ARBITER,
            llm_client=arbiter_client,
            system_prompt=prompts["arbiter"],
            context=context,
        ),
    )


def _load_debate_prompts() -> dict[str, str]:
    """Load debate role prompts from disk, mirroring runs.py."""
    root = _find_project_root()
    role_dirs = {
        "advocate": "debate_advocate",
        "sanad_breaker": "debate_sanad_breaker",
        "contradiction_finder": "debate_contradiction_finder",
        "risk_officer": "debate_risk_officer",
        "arbiter": "debate_arbiter",
    }
    prompts: dict[str, str] = {}
    for role_key, dir_name in role_dirs.items():
        prompt_path = root / "prompts" / dir_name / "1.0.0" / "prompt.md"
        if not prompt_path.exists():
            raise FileNotFoundError(f"Debate prompt file not found: {prompt_path}")
        prompts[role_key] = prompt_path.read_text(encoding="utf-8")
    return prompts


def _run_pipeline(deal: Any, backend: str) -> Any:
    """Wire and run the full pipeline via RunOrchestrator.

    Args:
        deal: GDBSDeal instance.
        backend: 'deterministic' or 'anthropic'.

    Returns:
        OrchestratorResult.
    """
    from idis.audit.sink import InMemoryAuditSink
    from idis.persistence.repositories.run_steps import InMemoryRunStepsRepository
    from idis.services.runs.orchestrator import RunContext, RunOrchestrator

    run_id = str(uuid.uuid4())
    documents = _build_documents_from_deal(deal)

    if not documents:
        logger.error("No documents/spans found for deal %s", deal.deal_key)
        sys.exit(1)

    logger.info(
        "Loaded %d document(s) with %d total spans",
        len(documents),
        sum(len(d["spans"]) for d in documents),
    )

    audit_sink = InMemoryAuditSink()
    run_steps_repo = InMemoryRunStepsRepository(deal.tenant_id)

    orchestrator = RunOrchestrator(
        audit_sink=audit_sink,
        run_steps_repo=run_steps_repo,
    )

    ctx = RunContext(
        run_id=run_id,
        tenant_id=deal.tenant_id,
        deal_id=deal.deal_id,
        mode="FULL",
        documents=documents,
        extract_fn=_build_extraction_fn(),
        grade_fn=_build_grade_fn(),
        calc_fn=_build_calc_fn(),
        debate_fn=_build_debate_fn(deal=deal),
    )

    logger.info("Starting pipeline run %s (mode=FULL, backend=%s)", run_id, backend)
    result = orchestrator.execute(ctx)
    logger.info("Pipeline finished with status: %s", result.status)

    return result


def _print_summary(
    result: Any,
    deal: Any,
    *,
    verbose: bool = False,
    quiet: bool = False,
) -> None:
    """Print a human-readable summary of the pipeline result.

    Args:
        result: OrchestratorResult.
        deal: GDBSDeal instance.
        verbose: Show full agent output content.
        quiet: Show minimal debate summary only.
    """
    print(f"\n{SEPARATOR}")
    print("IDIS PIPELINE DEMO — SINGLE DEAL RESULT")
    print(SEPARATOR)

    print(f"\n  Deal:      {deal.deal_key} — {deal.company_name}")
    print(f"  Scenario:  {deal.scenario}")
    print(f"  Sector:    {deal.sector}")
    print(f"  Stage:     {deal.stage}")

    print(f"\n  Run Status:     {result.status}")
    if result.block_reason:
        print(f"  Block Reason:   {result.block_reason}")
    if result.error_code:
        print(f"  Error Code:     {result.error_code}")
        print(f"  Error Message:  {result.error_message}")

    print(f"\n  Steps Completed: {_count_steps_by_status(result.steps, 'COMPLETED')}")
    print(f"  Steps Failed:    {_count_steps_by_status(result.steps, 'FAILED')}")
    print(f"  Steps Blocked:   {_count_steps_by_status(result.steps, 'BLOCKED')}")

    print(f"\n{'─' * 72}")
    print("  STEP LEDGER")
    print(f"{'─' * 72}")
    for step in result.steps:
        status_val = step.status.value if hasattr(step.status, "value") else step.status
        name_val = step.step_name.value if hasattr(step.step_name, "value") else step.step_name
        print(f"    {name_val:<16} {status_val:<12} retries={step.retry_count}")

    _print_extraction_summary(result)
    _print_calc_summary(result)
    _print_debate_summary(result, verbose=verbose, quiet=quiet)

    print(f"\n{SEPARATOR}")
    print("END OF DEMO")
    print(SEPARATOR)


def _count_steps_by_status(steps: list[Any], status: str) -> int:
    """Count steps matching a given status string."""
    count = 0
    for step in steps:
        step_status = step.status.value if hasattr(step.status, "value") else step.status
        if step_status == status:
            count += 1
    return count


def _get_step_result(steps: list[Any], step_name: str) -> dict[str, Any]:
    """Get the result_summary dict for a specific step."""
    for step in steps:
        name_val = step.step_name.value if hasattr(step.step_name, "value") else step.step_name
        if name_val == step_name:
            return step.result_summary
    return {}


def _print_extraction_summary(result: Any) -> None:
    """Print extraction step details."""
    extract_data = _get_step_result(result.steps, "EXTRACT")
    if not extract_data:
        return

    claim_ids = extract_data.get("created_claim_ids", [])
    print(f"\n{'─' * 72}")
    print("  EXTRACTION")
    print(f"{'─' * 72}")
    print(f"    Status:          {extract_data.get('status', 'N/A')}")
    print(f"    Chunks:          {extract_data.get('chunk_count', 0)}")
    print(f"    Claims Created:  {len(claim_ids)}")
    print(f"    Unique Claims:   {extract_data.get('unique_claim_count', 0)}")
    print(f"    Conflicts:       {extract_data.get('conflict_count', 0)}")

    if claim_ids:
        sample = claim_ids[:3]
        print(f"    Sample Claim IDs ({min(3, len(claim_ids))} of {len(claim_ids)}):")
        for cid in sample:
            print(f"      - {cid}")


def _print_calc_summary(result: Any) -> None:
    """Print calculation step details."""
    calc_data = _get_step_result(result.steps, "CALC")
    if not calc_data:
        return

    calc_ids = calc_data.get("calc_ids", [])
    print(f"\n{'─' * 72}")
    print("  CALCULATIONS")
    print(f"{'─' * 72}")
    print(f"    Calc Results:    {len(calc_ids)}")
    print(f"    Claim Count:     {calc_data.get('claim_count', 0)}")


def _print_debate_summary(
    result: Any,
    *,
    verbose: bool = False,
    quiet: bool = False,
) -> None:
    """Print debate step details with full transcript.

    Args:
        result: OrchestratorResult.
        verbose: Show full content (not truncated).
        quiet: Show minimal summary only.
    """
    debate_data = _get_step_result(result.steps, "DEBATE")
    if not debate_data:
        return

    stop_reason = debate_data.get("stop_reason", "N/A")
    round_count = debate_data.get("round_number", 0)
    muhasabah_passed = debate_data.get("muhasabah_passed", False)
    agent_outputs = debate_data.get("agent_outputs", [])

    print(f"\n{'─' * 72}")
    print("  DEBATE")
    print(f"{'─' * 72}")
    print(f"    Stop Reason:     {stop_reason}")
    print(f"    Round Count:     {round_count}")
    print(f"    Muhasabah:       {'PASSED' if muhasabah_passed else 'FAILED'}")
    print(f"    Agent Outputs:   {len(agent_outputs)}")

    if quiet or not agent_outputs:
        return

    content_limit = 0 if verbose else 300
    _print_debate_transcript(agent_outputs, content_limit=content_limit)
    _print_debate_final_summary(debate_data, agent_outputs)


def _print_debate_transcript(
    agent_outputs: list[Any],
    *,
    content_limit: int = 300,
) -> None:
    """Print round-by-round agent outputs.

    Args:
        agent_outputs: List of AgentOutput objects or dicts.
        content_limit: Max chars for content display. 0 = unlimited.
    """
    grouped: dict[int, list[Any]] = {}
    for output in agent_outputs:
        rn = _output_field(output, "round_number", 0)
        grouped.setdefault(rn, []).append(output)

    for round_num in sorted(grouped):
        print(f"\n    {'═' * 60}")
        print(f"    ROUND {round_num}")
        print(f"    {'═' * 60}")

        for output in grouped[round_num]:
            _print_agent_output(output, content_limit=content_limit)


def _print_agent_output(output: Any, *, content_limit: int = 300) -> None:
    """Print a single agent output with Muhasabah details.

    Args:
        output: AgentOutput object or dict.
        content_limit: Max chars for content. 0 = unlimited.
    """
    role_val = _output_field(output, "role", "unknown")
    if hasattr(role_val, "value"):
        role_val = role_val.value
    output_type = _output_field(output, "output_type", "")
    content = _output_field(output, "content", {})

    muhasabah = _output_field(output, "muhasabah", None)
    confidence = _muhasabah_field(muhasabah, "confidence", "N/A")
    claim_refs = _muhasabah_field(muhasabah, "supported_claim_ids", [])
    calc_refs = _muhasabah_field(muhasabah, "supported_calc_ids", [])
    uncertainties = _muhasabah_field(muhasabah, "uncertainties", [])
    falsifiability = _muhasabah_field(muhasabah, "falsifiability_tests", [])
    failure_modes = _muhasabah_field(muhasabah, "failure_modes", [])
    is_subjective = _muhasabah_field(muhasabah, "is_subjective", False)

    muhasabah_ok = _check_muhasabah_pass(muhasabah, is_subjective)

    print(f"\n      [{role_val.upper()}] type={output_type}")

    narrative = _extract_narrative(content, content_limit)
    if narrative:
        print(f"        Content: {narrative}")

    print(f"        Muhasabah:  {'PASS' if muhasabah_ok else 'FAIL'}")
    print(f"        Confidence: {confidence}")
    print(f"        Claims:     {len(claim_refs)} refs {_truncate_ids(claim_refs)}")

    if calc_refs:
        print(f"        Calcs:      {len(calc_refs)} refs {_truncate_ids(calc_refs)}")
    if uncertainties:
        print(f"        Uncertainties: {len(uncertainties)}")
        for u in uncertainties[:3]:
            desc = u.get("uncertainty", u.get("description", "")) if isinstance(u, dict) else str(u)
            print(f"          - {str(desc)[:80]}")
    if falsifiability:
        print(f"        Falsifiability: {len(falsifiability)} tests")
    if failure_modes:
        print(f"        Failure Modes: {', '.join(str(f) for f in failure_modes[:5])}")

    _print_role_specific(role_val, content)


def _print_role_specific(role: str, content: Any) -> None:
    """Print role-specific content details.

    Args:
        role: Role name string.
        content: Content dict.
    """
    if not isinstance(content, dict):
        return

    role_lower = str(role).lower()
    if "sanad" in role_lower:
        challenged = content.get("challenged_claim_ids", [])
        if challenged:
            print(f"        Challenged Claims: {len(challenged)}")
        defects = content.get("defects_found", content.get("defects", []))
        if defects:
            print(f"        Defects Found: {len(defects)}")
        cures = content.get("cure_protocols", [])
        if cures:
            print(f"        Cure Protocols: {len(cures)}")

    elif "arbiter" in role_lower:
        rulings = content.get("rulings", content.get("challenges_validated", []))
        if rulings:
            print(f"        Rulings: {len(rulings)}")
        utility = content.get("utility_adjustments", {})
        if utility:
            print(f"        Utility Updates: {utility}")
        dissent = content.get("dissent_preserved", [])
        if dissent:
            print(f"        Dissent Preserved: {len(dissent)}")
        stop = content.get("stop_condition", "")
        if stop:
            print(f"        Stop Condition: {stop}")

    elif "contradiction" in role_lower:
        contradictions = content.get("contradictions_found", [])
        if contradictions:
            print(f"        Contradictions: {len(contradictions)}")

    elif "risk" in role_lower:
        risks = content.get("risks_identified", [])
        if risks:
            print(f"        Risks: {len(risks)}")
        fraud = content.get("fraud_indicators", [])
        if fraud:
            print(f"        Fraud Indicators: {len(fraud)}")


def _print_debate_final_summary(
    debate_data: dict[str, Any],
    agent_outputs: list[Any],
) -> None:
    """Print final debate summary with aggregate stats.

    Args:
        debate_data: Debate step result dict.
        agent_outputs: List of agent outputs.
    """
    print(f"\n    {'─' * 60}")
    print("    DEBATE SUMMARY")
    print(f"    {'─' * 60}")
    print(f"      Rounds Completed: {debate_data.get('round_number', 0)}")
    print(f"      Stop Reason:      {debate_data.get('stop_reason', 'N/A')}")
    print(
        f"      Muhasabah:        {'PASSED' if debate_data.get('muhasabah_passed') else 'FAILED'}"
    )

    role_counts: dict[str, int] = {}
    for output in agent_outputs:
        rv = _output_field(output, "role", "unknown")
        if hasattr(rv, "value"):
            rv = rv.value
        role_counts[rv] = role_counts.get(rv, 0) + 1
    if role_counts:
        print(f"      Outputs by Role:  {role_counts}")


def _output_field(output: Any, field: str, default: Any = None) -> Any:
    """Extract a field from an AgentOutput (object or dict)."""
    if isinstance(output, dict):
        return output.get(field, default)
    return getattr(output, field, default)


def _muhasabah_field(muhasabah: Any, field: str, default: Any = None) -> Any:
    """Extract a field from a MuhasabahRecord (object or dict)."""
    if muhasabah is None:
        return default
    if isinstance(muhasabah, dict):
        return muhasabah.get(field, default)
    return getattr(muhasabah, field, default)


def _check_muhasabah_pass(muhasabah: Any, is_subjective: bool) -> bool:
    """Quick heuristic check if muhasabah would pass validation."""
    if muhasabah is None:
        return False
    claim_refs = _muhasabah_field(muhasabah, "supported_claim_ids", [])
    confidence = _muhasabah_field(muhasabah, "confidence", 0.0)
    uncertainties = _muhasabah_field(muhasabah, "uncertainties", [])
    if not is_subjective and not claim_refs:
        return False
    return not (
        isinstance(confidence, (int, float)) and confidence > 0.80 and not uncertainties
    )


def _extract_narrative(content: Any, limit: int = 300) -> str:
    """Extract narrative text from content dict."""
    if not isinstance(content, dict):
        return str(content)[:limit] if limit else str(content)
    text = content.get("text", content.get("narrative", ""))
    text = str(text)
    if limit and len(text) > limit:
        return text[:limit] + "..."
    return text


def _truncate_ids(ids: list[str], max_show: int = 3) -> str:
    """Format a list of IDs for compact display."""
    if not ids:
        return "[]"
    shown = [s[:12] + "..." if len(s) > 16 else s for s in ids[:max_show]]
    suffix = f" +{len(ids) - max_show} more" if len(ids) > max_show else ""
    return "[" + ", ".join(shown) + suffix + "]"


def main() -> None:
    """Entry point for the demo script."""
    load_dotenv()
    args = _parse_args()
    _configure_backend(args.backend)

    print(f"\n{SEPARATOR}")
    print("  IDIS Single-Deal LLM Demo")
    print(f"  Backend: {args.backend}")
    print(f"  Deal Key: {args.deal_key or '(first in manifest)'}")
    print(SEPARATOR)

    deal = _load_deal(args.deal_key)
    logger.info(
        "Deal loaded: %s — %s (scenario=%s, sector=%s)",
        deal.deal_key,
        deal.company_name,
        deal.scenario,
        deal.sector,
    )

    result = _run_pipeline(deal, args.backend)
    _print_summary(result, deal, verbose=args.verbose, quiet=args.quiet)


if __name__ == "__main__":
    main()
