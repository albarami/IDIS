"""Real PostgreSQL proof for Task 5 import/freeze/select/coverage durability."""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest
from sqlalchemy import text

from idis.methodology.importers.cdd_json import import_cdd_template
from idis.methodology.importers.fdd_excel import import_fdd_workbook
from idis.methodology.models import MethodologyType
from idis.persistence.repositories.methodologies import (
    MethodologiesRepositoryError,
    PostgresMethodologiesRepository,
    methodology_source_trace_digest,
)
from idis.persistence.repositories.runs import (
    PostgresRunsRepository,
    WorkerLeaseFencedError,
)
from idis.pipeline.worker import build_postgres_methodology_coverage_callbacks
from idis.services.runs.methodology_coverage_init import (
    DurableRunMethodologyCoverageInitService,
)
from tests.slice100_task2b._support import (
    disposable_postgres,
    grant_application_access,
    migrate,
)
from tests.slice100_task8._artifact_oracle import requires_operator_workbook

_CDD_PATH = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "idis"
    / "methodology"
    / "templates"
    / "commercial_dd_v1.json"
)


class _BoundTransaction:
    @contextmanager
    def transaction(self) -> Iterator[None]:
        yield


def _operator_fdd_path(record_property: pytest.RecordProperty | None = None) -> Path:
    from tests.slice100_task8._artifact_oracle import require_operator_workbook

    return require_operator_workbook(record_property)


def _contract_hash(label: str) -> str:
    return hashlib.sha256(label.encode("utf-8")).hexdigest()


@requires_operator_workbook
def test_real_cdd_fdd_freeze_selection_and_100_row_resume_are_durable(
    record_property: pytest.RecordProperty,
) -> None:
    """Exercise canonical sources through the real repository and durable service."""
    fdd_path = _operator_fdd_path(record_property)
    cdd_bytes = _CDD_PATH.read_bytes()
    fdd_bytes = fdd_path.read_bytes()
    cdd = import_cdd_template(_CDD_PATH)
    fdd = import_fdd_workbook(fdd_path)
    assert len(cdd.current_version.questions) == 21
    assert len(fdd.current_version.questions) == 79

    with disposable_postgres() as database:
        migrate(database.admin_engine, "0036")
        grant_application_access(database)
        tenant_a = str(uuid.uuid4())
        tenant_b = str(uuid.uuid4())
        deal_id = str(uuid.uuid4())
        run_id = str(uuid.uuid4())
        first_worker_id = str(uuid.uuid4())
        with database.admin_engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO tenants (tenant_id,name) VALUES "
                    "(CAST(:tenant_a AS uuid),'Task5 Alpha'),"
                    "(CAST(:tenant_b AS uuid),'Task5 Beta')"
                ),
                {"tenant_a": tenant_a, "tenant_b": tenant_b},
            )
            connection.execute(
                text(
                    "INSERT INTO deals (deal_id,tenant_id,name,created_at) "
                    "VALUES (CAST(:deal_id AS uuid),CAST(:tenant_id AS uuid),"
                    "'Fictional Task5 Deal',now())"
                ),
                {"deal_id": deal_id, "tenant_id": tenant_a},
            )
            connection.execute(
                text(
                    "INSERT INTO runs "
                    "(run_id,tenant_id,deal_id,mode,status,started_at,created_at) "
                    "VALUES (CAST(:run_id AS uuid),CAST(:tenant_id AS uuid),"
                    "CAST(:deal_id AS uuid),'FULL','QUEUED',now(),now())"
                ),
                {"run_id": run_id, "tenant_id": tenant_a, "deal_id": deal_id},
            )

        with database.app_engine.begin() as connection:
            repository = PostgresMethodologiesRepository(connection, tenant_a)
            cdd_imported = repository.import_registry(
                registry=cdd,
                source_type="checked_in_template",
                source_alias=str(cdd.current_version.source_name),
                source_byte_count=len(cdd_bytes),
                source_contract_hash=_contract_hash("cdd-source-contract-v1"),
                source_trace_digest=methodology_source_trace_digest(cdd),
                imported_by_actor_id="task5-test-operator",
                import_audit_event_id=str(uuid.uuid4()),
            )
            fdd_imported = repository.import_registry(
                registry=fdd,
                source_type="operator_workbook",
                source_alias=str(fdd.current_version.source_name),
                source_byte_count=len(fdd_bytes),
                source_contract_hash=_contract_hash("fdd-source-contract-v1"),
                source_trace_digest=methodology_source_trace_digest(fdd),
                imported_by_actor_id="task5-test-operator",
                import_audit_event_id=str(uuid.uuid4()),
            )
            cdd_retry = repository.import_registry(
                registry=cdd,
                source_type="checked_in_template",
                source_alias=str(cdd.current_version.source_name),
                source_byte_count=len(cdd_bytes),
                source_contract_hash=_contract_hash("cdd-source-contract-v1"),
                source_trace_digest=methodology_source_trace_digest(cdd),
                imported_by_actor_id="task5-test-operator",
                import_audit_event_id=str(uuid.uuid4()),
            )
            assert cdd_retry == cdd_imported
            cdd_frozen = repository.freeze_version(
                methodology_version_record_id=cdd_imported.methodology_version_record_id,
                expected_source_sha256=cdd.current_version.source_hash,
                expected_registry=cdd,
                frozen_by_actor_id="task5-test-reviewer",
                freeze_audit_event_id=str(uuid.uuid4()),
            )
            fdd_frozen = repository.freeze_version(
                methodology_version_record_id=fdd_imported.methodology_version_record_id,
                expected_source_sha256=fdd.current_version.source_hash,
                expected_registry=fdd,
                frozen_by_actor_id="task5-test-reviewer",
                freeze_audit_event_id=str(uuid.uuid4()),
            )
            selection = repository.create_run_selection(
                deal_id=deal_id,
                run_id=run_id,
                commercial_dd_version_record_id=cdd_frozen.methodology_version_record_id,
                financial_dd_version_record_id=fdd_frozen.methodology_version_record_id,
                created_by_actor_id="task5-test-operator",
                selection_audit_event_id=str(uuid.uuid4()),
            )
            runs_repository = PostgresRunsRepository(connection, tenant_a)
            claimed = runs_repository.claim_queued_runs(
                limit=1,
                lease_owner_id=first_worker_id,
                lease_seconds=60,
            )
            assert len(claimed) == 1
            first_fencing_token = int(claimed[0]["lease_fencing_token"])
            service = DurableRunMethodologyCoverageInitService(
                methodology_repository=repository,
                runs_repository=runs_repository,
                unit_of_work=_BoundTransaction(),
                lease_owner_id=first_worker_id,
                lease_fencing_token=first_fencing_token,
            )
            first_result, first_records = service.initialize_for_run(
                tenant_id=tenant_a,
                deal_id=deal_id,
                run_id=run_id,
            )

        assert selection.selected_question_count == 100
        assert first_result.summary.total_questions == 100
        assert len(first_records) == 100
        assert (
            sum(
                record.methodology_type is MethodologyType.COMMERCIAL_DD for record in first_records
            )
            == 21
        )
        assert (
            sum(record.methodology_type is MethodologyType.FINANCIAL_DD for record in first_records)
            == 79
        )

        with database.app_engine.begin() as connection:
            resumed_repository = PostgresMethodologiesRepository(connection, tenant_a)
            resumed_service = DurableRunMethodologyCoverageInitService(
                methodology_repository=resumed_repository,
                runs_repository=PostgresRunsRepository(connection, tenant_a),
                unit_of_work=_BoundTransaction(),
                lease_owner_id=first_worker_id,
                lease_fencing_token=first_fencing_token,
            )
            resumed_result, resumed_records = resumed_service.initialize_for_run(
                tenant_id=tenant_a,
                deal_id=deal_id,
                run_id=run_id,
            )
            loaded_selection = resumed_repository.load_run_selection(
                tenant_id=tenant_a,
                deal_id=deal_id,
                run_id=run_id,
            )

        assert resumed_records == first_records
        assert resumed_result == first_result
        assert loaded_selection == selection

        with database.app_engine.begin() as connection:
            foreign_repository = PostgresMethodologiesRepository(connection, tenant_b)
            with pytest.raises(MethodologiesRepositoryError):
                foreign_repository.get_frozen_version(
                    methodology_version_record_id=cdd_frozen.methodology_version_record_id
                )
            with pytest.raises(MethodologiesRepositoryError):
                foreign_repository.load_run_selection(
                    tenant_id=tenant_b,
                    deal_id=deal_id,
                    run_id=run_id,
                )

        with database.admin_engine.begin() as connection:
            connection.execute(
                text(
                    "DELETE FROM methodology_coverage_records WHERE run_id = CAST(:run_id AS uuid)"
                ),
                {"run_id": run_id},
            )
            connection.execute(
                text(
                    "UPDATE runs SET lease_expires_at = statement_timestamp() "
                    "- interval '1 second' WHERE run_id = CAST(:run_id AS uuid)"
                ),
                {"run_id": run_id},
            )

        second_worker_id = str(uuid.uuid4())
        with database.app_engine.begin() as connection:
            runs_repository = PostgresRunsRepository(connection, tenant_a)
            reclaimed = runs_repository.requeue_expired_worker_leases(limit=1)
            assert len(reclaimed) == 1
            second_claim = runs_repository.claim_queued_runs(
                limit=1,
                lease_owner_id=second_worker_id,
                lease_seconds=60,
            )
            assert len(second_claim) == 1
            second_fencing_token = int(second_claim[0]["lease_fencing_token"])
            assert second_fencing_token > first_fencing_token

        initialize, _rehydrate = build_postgres_methodology_coverage_callbacks(
            connection_factory=database.app_engine.begin,
            tenant_id=tenant_a,
            lease_owner_id=first_worker_id,
            lease_fencing_token=first_fencing_token,
        )
        with pytest.raises(WorkerLeaseFencedError):
            initialize(tenant_id=tenant_a, deal_id=deal_id, run_id=run_id)
        with database.admin_engine.connect() as connection:
            assert (
                connection.execute(
                    text(
                        "SELECT count(*) FROM methodology_coverage_records "
                        "WHERE run_id = CAST(:run_id AS uuid)"
                    ),
                    {"run_id": run_id},
                ).scalar_one()
                == 0
            )

        # Simulate privileged storage corruption that ordinary immutable/RLS
        # writes correctly prevent.  Read and initialization must independently
        # rederive the canonical selection hash rather than trust its shape.
        with database.admin_engine.begin() as connection:
            connection.execute(
                text(
                    "ALTER TABLE run_methodology_selections "
                    "DISABLE TRIGGER trg_run_methodology_selections_guard"
                )
            )
            connection.execute(
                text(
                    "UPDATE run_methodology_selections SET selection_hash = :selection_hash "
                    "WHERE selection_id = CAST(:selection_id AS uuid)"
                ),
                {"selection_hash": "0" * 64, "selection_id": selection.selection_id},
            )
            connection.execute(
                text(
                    "ALTER TABLE run_methodology_selections "
                    "ENABLE TRIGGER trg_run_methodology_selections_guard"
                )
            )

        with database.app_engine.begin() as connection:
            corrupted_repository = PostgresMethodologiesRepository(connection, tenant_a)
            with pytest.raises(MethodologiesRepositoryError):
                corrupted_repository.load_run_selection(
                    tenant_id=tenant_a,
                    deal_id=deal_id,
                    run_id=run_id,
                )
            corrupted_service = DurableRunMethodologyCoverageInitService(
                methodology_repository=corrupted_repository,
                runs_repository=PostgresRunsRepository(connection, tenant_a),
                unit_of_work=_BoundTransaction(),
                lease_owner_id=second_worker_id,
                lease_fencing_token=second_fencing_token,
            )
            with pytest.raises(MethodologiesRepositoryError):
                corrupted_service.initialize_for_run(
                    tenant_id=tenant_a,
                    deal_id=deal_id,
                    run_id=run_id,
                )
