"""Calc-engine SNAPSHOT integration regression (Sprint 2, Task 12).

Proves the CALC step is no longer a placeholder:

* Seeded claims with predicates matching the RUNWAY formula inputs
  (`cash_balance`, `monthly_burn_rate`) land in the real claims
  table.
* A queued SNAPSHOT run, driven by the worker, reaches the CALC step
  and executes the real deterministic CalcEngine.
* Durable `deterministic_calculations` + `calc_sanads` rows exist
  afterward, linked to the correct tenant + deal; the calc_type
  matches the formula the inputs satisfied; the reproducibility hash
  is non-empty; the calc_sanad references the input claim IDs.

Run path is verified end-to-end through the worker. No private calc
helpers are invoked in the assertions.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import Generator
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import text

from idis.persistence.db import set_tenant_local
from idis.persistence.repositories.documents import (
    DocumentArtifactsRepository,
    DocumentSpansRepository,
    DocumentsRepository,
)
from idis.pipeline.worker import PipelineWorker
from tests._postgres_support import (
    admin_engine_generator,
    migrated_db_generator,
    postgres_configured,
    seed_deal,
    truncate_all,
)

if TYPE_CHECKING:
    from sqlalchemy import Engine


TENANT_ID = "9a9a9a9a-9a9a-9a9a-9a9a-9a9a9a9a9a9a"


@pytest.fixture(scope="module")
def _pg_admin_engine() -> Generator[Engine, None, None]:
    yield from admin_engine_generator()


@pytest.fixture(scope="module")
def _pg_migrated(_pg_admin_engine: Engine) -> Generator[None, None, None]:
    yield from migrated_db_generator(_pg_admin_engine)


@pytest.fixture(autouse=True)
def _pg_clean_state(request: pytest.FixtureRequest) -> Generator[None, None, None]:
    if not postgres_configured():
        pytest.skip("Postgres not configured")
    admin_engine = request.getfixturevalue("_pg_admin_engine")
    request.getfixturevalue("_pg_migrated")
    truncate_all(admin_engine)
    # tenants row is FK target for deterministic_calculations/calc_sanads.
    with admin_engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO tenants (tenant_id, name, created_at)
                VALUES (:t, 'calc-integration-tenant', now())
                ON CONFLICT (tenant_id) DO NOTHING
                """
            ),
            {"t": TENANT_ID},
        )
    yield
    truncate_all(admin_engine)


def _seed_document_spans(admin_engine: Engine, deal_id: str) -> None:
    from idis.persistence.db import get_app_engine

    seed_deal(admin_engine, deal_id=deal_id, tenant_id=TENANT_ID)
    with get_app_engine().begin() as conn:
        set_tenant_local(conn, TENANT_ID)
        art_id = str(uuid.uuid4())
        document_id = str(uuid.uuid4())
        DocumentArtifactsRepository(conn, TENANT_ID).create(
            doc_id=art_id,
            deal_id=deal_id,
            doc_type="PITCH_DECK",
            title="calc.pdf",
            source_system="test",
            version_id="v1",
        )
        DocumentsRepository(conn, TENANT_ID).create(
            document_id=document_id,
            deal_id=deal_id,
            doc_id=art_id,
            doc_type="PDF",
            parse_status="PARSED",
        )
        DocumentSpansRepository(conn, TENANT_ID).create_many(
            [
                {
                    "span_id": str(uuid.uuid4()),
                    "document_id": document_id,
                    "span_type": "PAGE_TEXT",
                    "locator": {"page": 1},
                    "text_excerpt": "Cash balance $12M; monthly burn $1M.",
                }
            ]
        )


def _seed_calc_ready_claims(admin_engine: Engine, deal_id: str) -> list[str]:
    """Insert claims whose predicates match RUNWAY inputs. Grades are A so
    the orchestrator keeps going and the calc step has material inputs.
    """
    claim_ids: list[str] = []
    now = datetime.now(UTC)
    with admin_engine.begin() as conn:
        for predicate, numeric in (
            ("cash_balance", "12000000"),
            ("monthly_burn_rate", "1000000"),
        ):
            cid = str(uuid.uuid4())
            claim_ids.append(cid)
            conn.execute(
                text(
                    """
                    INSERT INTO claims (
                        claim_id, tenant_id, deal_id, claim_class, claim_text,
                        predicate, value, claim_grade, corroboration,
                        claim_verdict, claim_action, defect_ids,
                        materiality, ic_bound, created_at
                    ) VALUES (
                        :cid, :t, :d, 'QUANTITY_ASSERTION',
                        :ctext, :predicate, CAST(:value AS JSONB), 'A',
                        CAST(:corroboration AS JSONB),
                        'VERIFIED', 'NONE', CAST('[]' AS JSONB),
                        'HIGH', FALSE, :created_at
                    )
                    """
                ),
                {
                    "cid": cid,
                    "t": TENANT_ID,
                    "d": deal_id,
                    "ctext": f"{predicate} = {numeric}",
                    "predicate": predicate,
                    "value": json.dumps({"value": numeric, "unit": "USD"}),
                    "corroboration": json.dumps(
                        {"level": "AHAD", "independent_chain_count": 1}
                    ),
                    "created_at": now,
                },
            )
    return claim_ids


def _queue_snapshot_run(admin_engine: Engine, *, run_id: str, deal_id: str) -> None:
    with admin_engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO runs (
                    run_id, tenant_id, deal_id, mode, status,
                    started_at, created_at
                ) VALUES (
                    :r, :t, :d, 'SNAPSHOT', 'QUEUED', now(), now()
                )
                """
            ),
            {"r": run_id, "t": TENANT_ID, "d": deal_id},
        )


class TestSnapshotCalcStepPersistsRealCalculations:
    def test_worker_snapshot_produces_durable_runway_calc(
        self, _pg_admin_engine: Engine
    ) -> None:
        admin_engine = _pg_admin_engine
        deal_id = str(uuid.uuid4())
        run_id = str(uuid.uuid4())
        _seed_document_spans(admin_engine, deal_id)
        calc_input_ids = _seed_calc_ready_claims(admin_engine, deal_id)
        _queue_snapshot_run(admin_engine, run_id=run_id, deal_id=deal_id)

        asyncio.run(PipelineWorker(poll_interval=0)._process_queued_runs())

        with admin_engine.begin() as conn:
            run_row = conn.execute(
                text("SELECT status FROM runs WHERE run_id = :r"),
                {"r": run_id},
            ).fetchone()
            calc_rows = conn.execute(
                text(
                    """
                    SELECT calc_id, tenant_id, deal_id, calc_type,
                           reproducibility_hash, output
                    FROM deterministic_calculations
                    WHERE deal_id = :d
                    """
                ),
                {"d": deal_id},
            ).fetchall()
            sanad_rows = conn.execute(
                text(
                    """
                    SELECT calc_id, input_claim_ids, calc_grade
                    FROM calc_sanads
                    WHERE calc_id IN (
                        SELECT calc_id FROM deterministic_calculations
                        WHERE deal_id = :d
                    )
                    """
                ),
                {"d": deal_id},
            ).fetchall()

        # Run succeeded (prereq; the worker regressions elsewhere prove
        # the SUCCEEDED terminal transition in isolation).
        assert run_row is not None and run_row.status == "SUCCEEDED", (
            f"run must complete; got {getattr(run_row, 'status', None)!r}"
        )

        # At least one RUNWAY calc landed in the durable table,
        # correctly scoped to this tenant/deal, with a real
        # reproducibility hash and an output payload.
        runway = [r for r in calc_rows if r.calc_type == "RUNWAY"]
        assert len(runway) == 1, (
            f"exactly one durable RUNWAY calc expected; got {len(runway)} "
            f"({[(r.calc_type, str(r.calc_id)) for r in calc_rows]!r})"
        )
        row = runway[0]
        assert str(row.tenant_id) == TENANT_ID
        assert str(row.deal_id) == deal_id
        assert row.reproducibility_hash and len(row.reproducibility_hash) == 64
        # cash_balance / monthly_burn_rate → 12,000,000 / 1,000,000 = 12.0000
        output = row.output if isinstance(row.output, dict) else json.loads(row.output)
        assert output["primary_value"] in ("12", "12.0000"), (
            f"expected runway of 12 months; got {output['primary_value']!r}"
        )

        # CalcSanad links back to the calc and names the input claims.
        sanad_for_calc = [s for s in sanad_rows if str(s.calc_id) == str(row.calc_id)]
        assert sanad_for_calc, "calc_sanads must carry a row for the new calc"
        sanad = sanad_for_calc[0]
        input_ids = (
            sanad.input_claim_ids
            if isinstance(sanad.input_claim_ids, list)
            else json.loads(sanad.input_claim_ids)
        )
        assert set(input_ids) == set(calc_input_ids), (
            f"calc_sanad must reference exactly the seeded claim IDs; "
            f"expected {set(calc_input_ids)}, got {set(input_ids)}"
        )
        assert sanad.calc_grade == "A", (
            f"all input grades were A; expected calc_grade=A, got {sanad.calc_grade!r}"
        )


class TestSnapshotCalcStepReturnsHonestEmptyWhenNoInputs:
    """When no claim predicates match any registered formula inputs, the
    step must not raise and must not silently fabricate a calc. The run
    still succeeds (the step completes with an empty calc set).
    """

    def test_worker_snapshot_without_calc_inputs_produces_no_calc_rows(
        self, _pg_admin_engine: Engine
    ) -> None:
        admin_engine = _pg_admin_engine
        deal_id = str(uuid.uuid4())
        run_id = str(uuid.uuid4())
        _seed_document_spans(admin_engine, deal_id)
        _queue_snapshot_run(admin_engine, run_id=run_id, deal_id=deal_id)

        asyncio.run(PipelineWorker(poll_interval=0)._process_queued_runs())

        with admin_engine.begin() as conn:
            run_row = conn.execute(
                text("SELECT status FROM runs WHERE run_id = :r"),
                {"r": run_id},
            ).fetchone()
            calc_rows = conn.execute(
                text(
                    "SELECT 1 FROM deterministic_calculations WHERE deal_id = :d"
                ),
                {"d": deal_id},
            ).fetchall()

        assert run_row is not None and run_row.status == "SUCCEEDED"
        assert calc_rows == [], (
            "no calc inputs ⇒ no calc rows; fabrication would mean a "
            "silent placeholder snuck in"
        )


class TestSnapshotCalcSourceGuard:
    """Guard against a silent regression back to the placeholder body."""

    def test_calc_step_body_actually_executes_engine(self) -> None:
        import inspect

        from idis.api.routes import runs as runs_route

        source = inspect.getsource(runs_route._run_snapshot_calc)
        assert "CalcEngine" in source, (
            "_run_snapshot_calc must route through CalcEngine on the "
            "real path; reverting to the placeholder regresses Task 12."
        )
        assert "CalculationsRepository" in source, (
            "_run_snapshot_calc must persist via CalculationsRepository"
        )
