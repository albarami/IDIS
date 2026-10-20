"""GL-12 slice 1: scoring uses the admitted deal stage and fails closed when it is unknown."""

from __future__ import annotations

import pytest

from idis.analysis.scoring.models import ScoreDimension, Stage, resolve_stage
from idis.analysis.scoring.stage_packs import get_stage_pack
from idis.api.openapi_loader import load_openapi_spec
from idis.models.layer2_durability import Layer2ChallengeRow
from idis.models.layer2_ic_challenge import (
    Layer2ChallengeCategory,
    Layer2ICChallengeFinding,
    Layer2ICChallengeRecord,
    Layer2ICChallengeStatus,
)
from tests.test_score_engine_aggregation import (
    _build_scoring_response,
    _make_bundle,
    _make_context,
)

TENANT_ID = "11111111-1111-1111-1111-111111111111"
DEAL_ID = "33333333-3333-3333-3333-333333333333"
RUN_ID = "22222222-2222-2222-2222-222222222222"
STAGE_UNKNOWN = "STAGE_UNKNOWN"

# Normalized keys. Case and surrounding whitespace are applied by resolve_stage.
EXPECTED_STAGE_SPELLINGS: dict[str, Stage] = {
    "pre_seed": Stage.PRE_SEED,
    "pre-seed": Stage.PRE_SEED,
    "pre seed": Stage.PRE_SEED,
    "preseed": Stage.PRE_SEED,
    "seed": Stage.SEED,
    "series_a": Stage.SERIES_A,
    "series a": Stage.SERIES_A,
    "series-a": Stage.SERIES_A,
    "a": Stage.SERIES_A,
    "series_b": Stage.SERIES_B,
    "series b": Stage.SERIES_B,
    "series-b": Stage.SERIES_B,
    "b": Stage.SERIES_B,
    "growth": Stage.GROWTH,
}

_CASE_AND_SPACE_VARIANTS: tuple[tuple[str, Stage], ...] = (
    ("  PRE_SEED  ", Stage.PRE_SEED),
    ("Pre-Seed", Stage.PRE_SEED),
    ("PRE SEED", Stage.PRE_SEED),
    ("PreSeed", Stage.PRE_SEED),
    ("\tSeed\n", Stage.SEED),
    ("SERIES_A", Stage.SERIES_A),
    ("  Series   A ", Stage.SERIES_A),
    ("series-a", Stage.SERIES_A),
    (" A ", Stage.SERIES_A),
    ("SERIES_B", Stage.SERIES_B),
    ("Series B", Stage.SERIES_B),
    ("series-b", Stage.SERIES_B),
    ("b", Stage.SERIES_B),
    (" Growth ", Stage.GROWTH),
)

_UNMAPPED_STAGE_TEXT: tuple[str | None, ...] = (
    None,
    "",
    "   ",
    "\n\t",
    "series c",
    "Series C",
    "seed round",
    "pre-series a",
    "growth equity",
    "c",
    "seriesa",
    "late",
)


def _challenge_record() -> Layer2ICChallengeRecord:
    return Layer2ICChallengeRecord(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        layer2_challenge_id="55555555-5555-5555-5555-555555555555",
        source_debate_id="debate-001",
        status=Layer2ICChallengeStatus.COMPLETED,
        claim_ids=["claim-a"],
        calc_ids=["calc-a"],
        findings=[
            Layer2ICChallengeFinding(
                finding_id="layer2-finding-001",
                finding_type="ic_challenge",
                severity="medium",
                category=Layer2ChallengeCategory.MARKET_RISK,
                supported_claim_ids=["claim-a"],
            )
        ],
        unresolved_question_count=0,
        muhasabah_passed=True,
    )


def _quiet_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("IDIS_STRICT_DOTENV_PATH", raising=False)
    monkeypatch.delenv("IDIS_REQUIRE_FULL_LIVE", raising=False)
    monkeypatch.setenv("IDIS_DEBATE_BACKEND", "deterministic")


_STAGE_UNKNOWN_MESSAGE_LIMIT = 120


class _CountingRunner:
    """Records runner invocations and returns one fixed body."""

    def __init__(self, response: str) -> None:
        self._response = response
        self.calls = 0

    def run(self, payload: dict[str, object]) -> str:
        self.calls += 1
        return self._response


class _RecordingChallengeRepository:
    """Captures durable writes from the FULL Layer-2 route."""

    def __init__(self) -> None:
        self.challenges: list[object] = []
        self.findings: list[object] = []

    def upsert_challenge(self, row: object) -> dict[str, str]:
        self.challenges.append(row)
        return {"challenge_id": "unused", "tenant_id": TENANT_ID}

    def upsert_finding(self, row: object) -> dict[str, str]:
        self.findings.append(row)
        return {"finding_id": "unused", "tenant_id": TENANT_ID}


def _strict_layer2_fakes(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[_CountingRunner, _CountingRunner, dict[str, int]]:
    """Install challenger and arbiter fakes on the strict FULL Layer-2 path."""
    from tests.test_slice65_layer2_ic_challenge import _layer2_response

    _quiet_env(monkeypatch)
    monkeypatch.setenv("IDIS_REQUIRE_FULL_LIVE", "true")
    monkeypatch.setenv("IDIS_DEBATE_BACKEND", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "configured-not-a-real-key")
    monkeypatch.setenv("IDIS_ANTHROPIC_MODEL_DEBATE_DEFAULT", "claude-sonnet-fake")
    monkeypatch.setenv("IDIS_ANTHROPIC_MODEL_DEBATE_ARBITER", "claude-opus-fake")
    response = _layer2_response(
        supported_claim_ids=["claim-a"],
        supported_calc_ids=["calc-a"],
        extra_content={
            "graph_ref_ids": [],
            "rag_ref_ids": [],
            "enrichment_ref_ids": [],
        },
    )
    challenger = _CountingRunner(response)
    arbiter = _CountingRunner(response)
    built = {"runners": 0, "clients": 0}

    def _build(**_kwargs: object) -> tuple[_CountingRunner, _CountingRunner]:
        built["runners"] += 1
        return challenger, arbiter

    def _client(**_kwargs: object) -> object:
        built["clients"] += 1
        return object()

    monkeypatch.setattr(
        "idis.services.runs.layer2_ic_challenge.build_live_layer2_ic_runners",
        _build,
    )
    monkeypatch.setattr(
        "idis.services.runs.step_runtime.layer2._new_budgeted_llm_client",
        _client,
    )
    return challenger, arbiter, built


_UNMAPPED_DEAL_METADATA: tuple[tuple[dict[str, object] | None, str], ...] = (
    (None, ""),
    ({}, ""),
    ({"stage": None}, ""),
    ({"stage": ""}, ""),
    ({"stage": "   "}, "   "),
    ({"stage": "series c"}, "series c"),
    ({"stage": 12}, ""),
)


class _RecordingScoringClient:
    """Returns one prepared scorecard body and counts calls."""

    def __init__(self, response: str) -> None:
        self._response = response
        self.calls = 0

    def call(self, prompt: str, json_mode: bool = False) -> str:
        self.calls += 1
        return self._response


def _product_only_response() -> str:
    scores = {dimension.value: 0.0 for dimension in ScoreDimension}
    scores[ScoreDimension.PRODUCT_DEFENSIBILITY.value] = 1.0
    return _build_scoring_response(scores)


def _weighted_composite(stage: Stage) -> float:
    pack = get_stage_pack(stage)
    return 100.0 * pack.weights[ScoreDimension.PRODUCT_DEFENSIBILITY]


def test_stage_spellings_constant_matches_the_closed_table() -> None:
    from idis.analysis.scoring.models import STAGE_SPELLINGS

    assert STAGE_SPELLINGS == EXPECTED_STAGE_SPELLINGS


@pytest.mark.parametrize(("text", "expected"), list(EXPECTED_STAGE_SPELLINGS.items()))
def test_resolve_stage_maps_each_listed_spelling(text: str, expected: Stage) -> None:
    from idis.analysis.scoring.models import resolve_stage

    assert resolve_stage(text) is expected


@pytest.mark.parametrize(("text", "expected"), _CASE_AND_SPACE_VARIANTS)
def test_resolve_stage_ignores_case_and_surrounding_whitespace(
    text: str,
    expected: Stage,
) -> None:
    from idis.analysis.scoring.models import resolve_stage

    assert resolve_stage(text) is expected


@pytest.mark.parametrize("text", _UNMAPPED_STAGE_TEXT)
def test_resolve_stage_returns_none_for_blank_or_unknown(text: str | None) -> None:
    from idis.analysis.scoring.models import resolve_stage

    assert resolve_stage(text) is None


def test_full_scoring_uses_series_a_pack_for_series_a_deal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A deal stage of 'Series A' selects the SERIES_A pack, not SEED."""
    from idis.api.routes.runs import _run_full_scoring

    _quiet_env(monkeypatch)
    client = _RecordingScoringClient(_product_only_response())
    context = _make_context()
    assert context.stage == "Series A"

    summary = _run_full_scoring(
        run_id=context.run_id,
        tenant_id=context.tenant_id,
        deal_id=context.deal_id,
        analysis_bundle=_make_bundle(),
        analysis_context=context,
        scoring_client_factory=lambda _selection: client,
    )

    series_a_composite = _weighted_composite(Stage.SERIES_A)
    seed_composite = _weighted_composite(Stage.SEED)
    assert series_a_composite != seed_composite
    assert summary["stage"] == Stage.SERIES_A.value
    assert summary["_scorecard"].stage is Stage.SERIES_A
    assert summary["composite_score"] == pytest.approx(series_a_composite)
    assert summary["composite_score"] != pytest.approx(seed_composite)
    assert client.calls == 1


@pytest.mark.parametrize("raw_stage", ["Series C", "", "   "])
def test_full_scoring_unmappable_stage_is_stage_unknown(
    monkeypatch: pytest.MonkeyPatch,
    raw_stage: str,
) -> None:
    """An unmapped deal stage blocks scoring and keeps the raw text visible."""
    from idis.api.routes.runs import _run_full_scoring
    from idis.services.runs.orchestrator import RunStepBlockedError

    _quiet_env(monkeypatch)
    client = _RecordingScoringClient(_product_only_response())
    context = _make_context().model_copy(update={"stage": raw_stage})

    with pytest.raises(RunStepBlockedError) as raised:
        _run_full_scoring(
            run_id=context.run_id,
            tenant_id=context.tenant_id,
            deal_id=context.deal_id,
            analysis_bundle=_make_bundle(),
            analysis_context=context,
            scoring_client_factory=lambda _selection: client,
        )

    assert raised.value.code == STAGE_UNKNOWN
    assert raw_stage in str(raised.value)
    assert raised.value.result_summary == {
        "outcome": STAGE_UNKNOWN,
        "stage_text": raw_stage,
    }
    assert client.calls == 0


def test_layer2_summaries_carry_an_explicit_resolved_stage() -> None:
    record = _challenge_record()
    summary = record.to_run_step_summary(stage=Stage.SERIES_B)
    row = Layer2ChallengeRow.from_record(record, stage=Stage.SERIES_B)
    pack = get_stage_pack(Stage.SERIES_B)
    seed = get_stage_pack(Stage.SEED)
    expected = round(1 * pack.weights[ScoreDimension.MARKET_ATTRACTIVENESS], 6)
    seed_weight = round(1 * seed.weights[ScoreDimension.MARKET_ATTRACTIVENESS], 6)

    assert expected != seed_weight
    assert summary["stage_emphasis"]["stage"] == Stage.SERIES_B.value
    assert summary["stage_emphasis"]["weighted_by_category"]["market_risk"] == expected
    assert row.safe_summary["stage_emphasis"]["stage"] == Stage.SERIES_B.value
    assert row.safe_summary["stage_emphasis"]["weighted_by_category"]["market_risk"] == expected


def test_layer2_summaries_use_stage_carried_on_the_record() -> None:
    """A caller that only has the record still applies the stage stored on it."""
    record = _challenge_record().model_copy(update={"stage": Stage.GROWTH})
    summary = record.to_run_step_summary()
    row = Layer2ChallengeRow.from_record(record)

    assert summary["stage_emphasis"]["stage"] == Stage.GROWTH.value
    assert row.safe_summary["stage_emphasis"]["stage"] == Stage.GROWTH.value


def test_full_layer2_summaries_use_the_admitted_deal_stage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The deal_metadata snapshot selects emphasis, not a fresh repository read."""
    from idis.api.routes.runs import _run_full_layer2_ic_challenge
    from idis.persistence.repositories.deals import (
        clear_in_memory_store,
        seed_deal_in_memory,
    )
    from idis.persistence.repositories.layer2_challenge import (
        InMemoryLayer2ChallengeRepository,
        clear_in_memory_layer2_challenge_store,
    )

    _quiet_env(monkeypatch)
    clear_in_memory_store()
    clear_in_memory_layer2_challenge_store()
    seed_deal_in_memory(
        {
            "deal_id": DEAL_ID,
            "tenant_id": TENANT_ID,
            "stage": "growth",
        }
    )
    try:
        result = _run_full_layer2_ic_challenge(
            run_id=RUN_ID,
            tenant_id=TENANT_ID,
            deal_id=DEAL_ID,
            debate_summary={"debate_id": "debate-001", "muhasabah_passed": True},
            created_claim_ids=["claim-a"],
            calc_ids=["calc-a"],
            deal_metadata={"stage": "series-a"},
        )
        stored = InMemoryLayer2ChallengeRepository(TENANT_ID).list_challenges(run_id=RUN_ID)
    finally:
        clear_in_memory_store()
        clear_in_memory_layer2_challenge_store()

    assert result["stage_emphasis"]["stage"] == Stage.SERIES_A.value
    assert stored[0]["safe_summary"]["stage_emphasis"]["stage"] == Stage.SERIES_A.value


@pytest.mark.parametrize(("deal_metadata", "visible"), _UNMAPPED_DEAL_METADATA)
def test_full_layer2_unmapped_stage_blocks_before_runners(
    monkeypatch: pytest.MonkeyPatch,
    deal_metadata: dict[str, object] | None,
    visible: str,
) -> None:
    """An unmapped snapshot blocks Layer-2 before runners are built or rows written."""
    from idis.api.routes.runs import _run_full_layer2_ic_challenge
    from idis.persistence.repositories.layer2_challenge import (
        InMemoryLayer2ChallengeRepository,
        clear_in_memory_layer2_challenge_store,
    )
    from idis.services.runs.orchestrator import RunStepBlockedError

    challenger, arbiter, built = _strict_layer2_fakes(monkeypatch)
    repository = _RecordingChallengeRepository()
    clear_in_memory_layer2_challenge_store()
    try:
        with pytest.raises(RunStepBlockedError) as raised:
            _run_full_layer2_ic_challenge(
                run_id=RUN_ID,
                tenant_id=TENANT_ID,
                deal_id=DEAL_ID,
                debate_summary={"debate_id": "debate-001", "muhasabah_passed": True},
                created_claim_ids=["claim-a"],
                calc_ids=["calc-a"],
                deal_metadata=deal_metadata,
                challenge_repository=repository,
            )
        stored = InMemoryLayer2ChallengeRepository(TENANT_ID).list_challenges(run_id=RUN_ID)
    finally:
        clear_in_memory_layer2_challenge_store()

    assert raised.value.code == STAGE_UNKNOWN
    assert str(raised.value) == f"layer2 stage did not map: {visible}"
    assert raised.value.result_summary == {
        "outcome": STAGE_UNKNOWN,
        "stage_text": visible,
    }
    assert challenger.calls == 0
    assert arbiter.calls == 0
    assert built == {"runners": 0, "clients": 0}
    assert repository.challenges == []
    assert repository.findings == []
    assert stored == []


def test_full_layer2_deferred_record_carries_the_resolved_stage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A deferred FULL Layer-2 record keeps the stage resolved from deal_metadata."""
    from idis.api.routes.runs import _run_full_layer2_ic_challenge

    _quiet_env(monkeypatch)
    result = _run_full_layer2_ic_challenge(
        run_id=RUN_ID,
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        debate_summary={"debate_id": "debate-001", "muhasabah_passed": True},
        created_claim_ids=["claim-a"],
        calc_ids=["calc-a"],
        deal_metadata={"stage": "series-a"},
        defer_persistence=True,
    )

    assert result["_layer2_ic_challenge_record"].stage is Stage.SERIES_A


def test_stage_unknown_message_caps_raw_text_at_120_characters(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Both STAGE_UNKNOWN messages echo at most 120 characters of the raw stage."""
    from idis.api.routes.runs import _run_full_layer2_ic_challenge, _run_full_scoring
    from idis.services.runs.orchestrator import RunStepBlockedError

    raw = "Q" * (_STAGE_UNKNOWN_MESSAGE_LIMIT + 1)
    capped = "Q" * _STAGE_UNKNOWN_MESSAGE_LIMIT
    exact = "R" * _STAGE_UNKNOWN_MESSAGE_LIMIT
    challenger, arbiter, built = _strict_layer2_fakes(monkeypatch)

    with pytest.raises(RunStepBlockedError) as layer2_raised:
        _run_full_layer2_ic_challenge(
            run_id=RUN_ID,
            tenant_id=TENANT_ID,
            deal_id=DEAL_ID,
            debate_summary={"debate_id": "debate-001", "muhasabah_passed": True},
            created_claim_ids=["claim-a"],
            calc_ids=["calc-a"],
            deal_metadata={"stage": raw},
            challenge_repository=_RecordingChallengeRepository(),
        )

    assert str(layer2_raised.value) == f"layer2 stage did not map: {capped}"
    assert raw not in str(layer2_raised.value)
    assert layer2_raised.value.result_summary == {
        "outcome": STAGE_UNKNOWN,
        "stage_text": raw,
    }
    assert challenger.calls == 0
    assert arbiter.calls == 0
    assert built["runners"] == 0

    with pytest.raises(RunStepBlockedError) as layer2_exact:
        _run_full_layer2_ic_challenge(
            run_id=RUN_ID,
            tenant_id=TENANT_ID,
            deal_id=DEAL_ID,
            debate_summary={"debate_id": "debate-001", "muhasabah_passed": True},
            created_claim_ids=["claim-a"],
            calc_ids=["calc-a"],
            deal_metadata={"stage": exact},
        )

    assert str(layer2_exact.value) == f"layer2 stage did not map: {exact}"
    assert layer2_exact.value.result_summary == {
        "outcome": STAGE_UNKNOWN,
        "stage_text": exact,
    }

    _quiet_env(monkeypatch)
    client = _RecordingScoringClient(_product_only_response())
    context = _make_context().model_copy(update={"stage": raw})
    with pytest.raises(RunStepBlockedError) as scoring_raised:
        _run_full_scoring(
            run_id=context.run_id,
            tenant_id=context.tenant_id,
            deal_id=context.deal_id,
            analysis_bundle=_make_bundle(),
            analysis_context=context,
            scoring_client_factory=lambda _selection: client,
        )

    assert str(scoring_raised.value) == f"scoring stage did not map: {capped}"
    assert raw not in str(scoring_raised.value)
    assert scoring_raised.value.result_summary == {
        "outcome": STAGE_UNKNOWN,
        "stage_text": raw,
    }
    assert client.calls == 0

    exact_client = _RecordingScoringClient(_product_only_response())
    exact_context = _make_context().model_copy(update={"stage": exact})
    with pytest.raises(RunStepBlockedError) as scoring_exact:
        _run_full_scoring(
            run_id=exact_context.run_id,
            tenant_id=exact_context.tenant_id,
            deal_id=exact_context.deal_id,
            analysis_bundle=_make_bundle(),
            analysis_context=exact_context,
            scoring_client_factory=lambda _selection: exact_client,
        )

    assert str(scoring_exact.value) == f"scoring stage did not map: {exact}"
    assert scoring_exact.value.result_summary == {
        "outcome": STAGE_UNKNOWN,
        "stage_text": exact,
    }
    assert exact_client.calls == 0


def test_created_in_memory_deal_stage_reaches_full_partials_unchanged() -> None:
    """A mapped stage stored on an in-memory deal is the FULL partials' snapshot.

    The hosted FULL acceptance tests set this spelling on POST /v1/deals. The
    start-run path loads that row and binds it, unchanged, onto the partials.
    """
    from types import SimpleNamespace

    from idis.api.routes.runs import _load_deal_metadata_for_run
    from idis.audit.sink import InMemoryAuditSink
    from idis.persistence.repositories.deals import (
        InMemoryDealsRepository,
        clear_in_memory_store,
    )
    from idis.services.runs.steps import build_run_context

    cases = (
        (DEAL_ID, "SERIES_A"),
        ("44444444-4444-4444-4444-444444444444", "SERIES_B"),
    )
    request = SimpleNamespace(state=SimpleNamespace(db_conn=None))
    clear_in_memory_store()
    try:
        for deal_id, spelling in cases:
            InMemoryDealsRepository(TENANT_ID).create(
                deal_id=deal_id,
                name="Stage Deal",
                company_name="Synthetic Co",
                stage=spelling,
            )
            loaded = _load_deal_metadata_for_run(request, TENANT_ID, deal_id)
            context = build_run_context(
                db_conn=None,
                tenant_id=TENANT_ID,
                run_id=RUN_ID,
                deal_id=deal_id,
                mode="FULL",
                documents=[],
                deal_metadata=loaded,
                audit_sink=InMemoryAuditSink(),
            )
            assert loaded is not None
            assert loaded["stage"] == spelling
            for partial in (
                context.enrich_fn,
                context.debate_fn,
                context.layer2_ic_challenge_fn,
                context.analysis_fn,
            ):
                assert partial is not None
                assert partial.keywords["deal_metadata"]["stage"] == spelling
    finally:
        clear_in_memory_store()


def test_api_deal_stage_enum_contract_with_the_scoring_mapper() -> None:
    """Pin each OpenAPI DealStage value to the scoring mapper's current result.

    Characterization of the current mapper. It does not choose a stage pack
    for the two API values that still fail closed.
    """
    deal_stage_enum = load_openapi_spec()["components"]["schemas"]["DealStage"]["enum"]
    # LATER and OTHER have no scoring stage; owner/GL-02 decision pending
    # (RUN_STATE record of 2026-10-19).
    pinned_resolution: dict[str, Stage | None] = {
        "SEED": Stage.SEED,
        "SERIES_A": Stage.SERIES_A,
        "SERIES_B": Stage.SERIES_B,
        "GROWTH": Stage.GROWTH,
        "LATER": None,
        "OTHER": None,
    }
    assert set(deal_stage_enum) == set(pinned_resolution)
    for api_value, expected in pinned_resolution.items():
        assert resolve_stage(api_value) is expected
