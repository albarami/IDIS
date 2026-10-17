# ruff: noqa: F403, F405
"""Worker-lease restart-cut and native-output rehydration contracts."""

from __future__ import annotations

# The shared harness deliberately exports private fixtures and probes.
from ._support import *  # noqa: F403


def test_real_vep_row_is_staged_until_the_completion_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The default production stager must move a real VEP write to the completion conn."""
    import idis.persistence.repositories.layer1_evidence as layer1_repository_module

    polling_connection = object()
    record = _lease_vep_record()
    expected_row = ValidatedEvidencePackageRow.from_record(record)
    polling_writes: list[ValidatedEvidencePackageRow] = []
    constructed_repositories: list[tuple[Any, str]] = []
    committed_rows: list[ValidatedEvidencePackageRow] = []

    class PollingConnectionRepository:
        def upsert_validated_evidence_package(self, row: ValidatedEvidencePackageRow) -> None:
            polling_writes.append(row)
            raise AssertionError("VEP wrote on the polling/provider connection before COMPLETE")

    polling_repository = PollingConnectionRepository()

    class CompletionConnectionRepository:
        def __init__(self, conn: Any, tenant_id: str) -> None:
            constructed_repositories.append((conn, tenant_id))

        def upsert_validated_evidence_package(self, row: ValidatedEvidencePackageRow) -> None:
            committed_rows.append(row)

    class RunResult:
        status = SimpleNamespace(value="completed")

        @staticmethod
        def to_run_step_summary() -> dict[str, Any]:
            return {"status": "completed", "package_ids": [record.package_id]}

    monkeypatch.setattr(worker_module, "_load_worker_preflight_corpus", lambda **_kwargs: [])
    monkeypatch.setattr(worker_module, "_load_worker_deal_metadata", lambda **_kwargs: None)
    monkeypatch.setattr(worker_module, "is_strict_full_live_required", lambda **_kwargs: False)
    monkeypatch.setattr(
        worker_module,
        "load_worker_lease_config",
        lambda **_kwargs: SimpleNamespace(
            lease_seconds=LEASE_SECONDS,
            heartbeat_seconds=20,
        ),
        raising=False,
    )
    monkeypatch.setattr(
        execution_module,
        "build_postgres_step_boundary_uow",
        lambda **_kwargs: lambda **_boundary_kwargs: None,
        raising=False,
    )
    monkeypatch.setattr(
        worker_module,
        "build_worker_lease_renewal",
        lambda **_kwargs: lambda: True,
        raising=False,
    )
    monkeypatch.setattr(
        steps_module,
        "get_layer1_evidence_repository",
        lambda conn, tenant_id: (
            polling_repository
            if conn is polling_connection and tenant_id == TENANT_ID
            else (_ for _ in ()).throw(AssertionError("unexpected provider repository binding"))
        ),
    )
    monkeypatch.setattr(
        layer1_repository_module,
        "PostgresLayer1EvidenceRepository",
        CompletionConnectionRepository,
    )
    monkeypatch.setattr(layer1_repository_module, "is_postgres_configured", lambda: True)

    context = worker_module._default_run_context_factory(
        db_conn=polling_connection,
        tenant_id=TENANT_ID,
        run_data={
            "run_id": RUN_ID,
            "deal_id": DEAL_ID,
            "mode": "SNAPSHOT",
            "pipeline_contract_version": "snapshot-v1",
            "pipeline_contract_hash": (
                "6365adbfe07f1818fe9e8e634587feb59a6bda5238b625481940d9ea9efce723"
            ),
            "pipeline_resume_state": "NON_RESUMABLE",
            "source": {"type": "deal_documents", "document_ids": []},
            "lease_owner_id": SECOND_WORKER_ID,
            "lease_fencing_token": 3,
        },
        audit_sink=SimpleNamespace(),
    )
    context.methodology_evidence_trust_court = object()
    context.methodology_validated_evidence_package_fn = lambda **_kwargs: (RunResult(), [record])
    summary = RunOrchestrator(
        audit_sink=InMemoryAuditSink(),
        run_steps_repo=InMemoryRunStepsRepository(TENANT_ID),
    )._execute_methodology_validated_evidence_package(context)

    assert polling_writes == []
    assert context.methodology_validated_evidence_package is record
    stager = context.stage_native_output_writes_fn
    writers = stager(
        ctx=context,
        step_name=StepName.METHODOLOGY_VALIDATED_EVIDENCE_PACKAGE,
        result_summary=summary,
    )
    assert len(writers) == 1
    writer = writers[0]
    assert not inspect.isfunction(writer)
    assert writer.contract.step_name == StepName.METHODOLOGY_VALIDATED_EVIDENCE_PACKAGE
    assert (
        writer.contract.output_type_id
        == EXPECTED_NATIVE_OUTPUT_CONTRACTS[StepName.METHODOLOGY_VALIDATED_EVIDENCE_PACKAGE][0]
    )
    assert isinstance(writer.output, writer.contract.output_model_type)
    assert writer.output == expected_row
    _require_parameters(
        writer.persist,
        {"db_conn", "lease_owner_id", "lease_fencing_token"},
        contract="production staged VEP writer",
    )
    completion_connection = object()
    writer.persist(
        db_conn=completion_connection,
        lease_owner_id=SECOND_WORKER_ID,
        lease_fencing_token=3,
    )
    assert constructed_repositories == [(completion_connection, TENANT_ID)]
    assert committed_rows == [expected_row]
    assert polling_connection not in {conn for conn, _tenant in constructed_repositories}


def test_reviewed_binding_inventory_covers_the_exact_full_strict_v2_step_set() -> None:
    assert len(STRICT_V2_STEPS) == 28
    assert len(set(STRICT_V2_STEPS)) == 28
    assert set(STRICT_V2_STEPS) == set(EXPECTED_NATIVE_OUTPUT_CONTRACTS)
    assert set(STRICT_V2_STEPS) == set(REVIEWED_STEP_OUTPUT_BINDINGS)
    assert set(STRICT_V2_STEPS) == set(REVIEWED_REHYDRATION_CONTEXT_FIELDS)
    assert set(RunContext.__dataclass_fields__) >= _CONTEXT_BUSINESS_FIELDS
    assert all(
        reviewed_fields <= _CONTEXT_BUSINESS_FIELDS
        for reviewed_fields in REVIEWED_REHYDRATION_CONTEXT_FIELDS.values()
    )
    assert tuple(FULL_STEPS) != STRICT_V2_STEPS, (
        "legacy full-v1 unexpectedly aliases the approved full-strict-v2 order"
    )
    for expected in REVIEWED_STEP_OUTPUT_BINDINGS.values():
        if expected.aggregate_type_qname is not None:
            aggregate_type = _resolve_qualified_name(expected.aggregate_type_qname)
            assert _qualified_name(aggregate_type) == expected.aggregate_type_qname
        for binding in expected.all_source_grounded_bindings:
            if binding.row_type_qname is not None:
                row_type = _resolve_qualified_name(binding.row_type_qname)
                assert _qualified_name(row_type) == binding.row_type_qname
            repository_type = _resolve_qualified_name(binding.repository_type_qname)
            assert _qualified_name(repository_type) == binding.repository_type_qname
            if binding.persist_method is not None:
                persist_method = getattr(repository_type, binding.persist_method, None)
                assert callable(persist_method)
                target_source = inspect.getsource(persist_method).lower()
            else:
                assert binding in expected.read_dependencies
                assert binding.load_methods
                target_source = "\n".join(
                    inspect.getsource(getattr(repository_type, method_name)).lower()
                    for method_name in binding.load_methods
                )
            for sql_target in binding.sql_targets:
                assert sql_target in target_source, (
                    f"{binding.repository_type_qname} does not access reviewed target "
                    f"{sql_target} through its bound method(s)"
                )
            loader_targets: set[str] = set()
            for load_method_name in binding.load_methods:
                load_method = getattr(repository_type, load_method_name, None)
                assert callable(load_method)
                loader_targets.update(_method_sql_relation_targets(load_method))
            unreviewed_loader_targets = loader_targets - set(binding.sql_targets)
            assert not unreviewed_loader_targets, (
                f"{binding.repository_type_qname} loader(s) access unreviewed SQL targets "
                f"{sorted(unreviewed_loader_targets)}"
            )
            if binding.persist_method is None:
                assert loader_targets == set(binding.sql_targets), (
                    f"read dependency {binding.repository_type_qname} must declare its complete "
                    f"FROM/JOIN target set; declared={sorted(binding.sql_targets)}, "
                    f"accessed={sorted(loader_targets)}"
                )
            for non_sql_method_name in binding.non_sql_methods:
                assert callable(getattr(repository_type, non_sql_method_name, None))


@pytest.mark.parametrize(
    "step_name",
    STRICT_V2_STEPS,
    ids=[step.value.lower() for step in STRICT_V2_STEPS],
)
def test_each_native_output_binding_stays_red_until_its_reviewed_gaps_are_resolved(
    step_name: StepName,
) -> None:
    # Task 1 may not implement Task 7 schema; keep every exclusion explicit and review-gated.
    expected = REVIEWED_STEP_OUTPUT_BINDINGS[step_name]
    assert expected.unresolved_requirements
    assert any(
        "Task 7 schema-review gate" in requirement
        for requirement in expected.unresolved_requirements
    )


def test_completed_resume_branch_never_reads_result_summary_as_state_authority() -> None:
    source = textwrap.dedent(inspect.getsource(RunOrchestrator.execute))
    tree = ast.parse(source)
    completed_branches = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.If) and "StepStatus.COMPLETED" in ast.unparse(node.test)
    ]
    assert len(completed_branches) == 1
    # The validator lives in a helper; require its call before legacy summary rehydration.
    branch_source = "\n".join(ast.unparse(statement) for statement in completed_branches[0].body)
    validator_source = inspect.getsource(RunOrchestrator._validate_completed_step_for_resume)
    assert "completed_step_resume_validator" in validator_source
    assert "_validate_completed_step_for_resume" in branch_source
    assert "result_summary" in branch_source
    assert branch_source.index("_validate_completed_step_for_resume") < branch_source.index(
        "result_summary"
    )
    assert "_fail_completed_resume_boundary" in branch_source
    assert "self._fail_step(existing" not in branch_source


def test_production_native_output_registry_has_an_explicit_writer_set_for_all_28_steps() -> None:
    registry_builder = getattr(
        steps_module,
        "build_postgres_native_output_stager_registry",
        None,
    )
    assert callable(registry_builder), (
        "canonical runtime is missing the explicit 28-step native-output producer registry"
    )
    registry = registry_builder()
    assert isinstance(registry, dict)
    assert set(EXPECTED_NATIVE_OUTPUT_CONTRACTS) == set(STRICT_V2_STEPS)
    # Task 1 verifies only existing concrete writers; Task 7 owns the other 26 schemas/loaders.
    concrete_steps = {
        StepName.METHODOLOGY_VALIDATED_EVIDENCE_PACKAGE,
        StepName.LAYER2_IC_CHALLENGE,
    }
    assert set(registry) == concrete_steps
    for step_name in concrete_steps:
        contract = registry[step_name]
        expected_output_type, expected_target = EXPECTED_NATIVE_OUTPUT_CONTRACTS[step_name]
        assert contract.step_name == step_name
        assert contract.output_type_id == expected_output_type
        assert contract.persistence_target == expected_target
        expected_bindings = REVIEWED_STEP_OUTPUT_BINDINGS[step_name].produced_write_bindings
        assert tuple(
            _contract_binding_fingerprint(item) for item in contract.write_bindings
        ) == tuple(_reviewed_binding_fingerprint(item) for item in expected_bindings)
        assert callable(contract.stage), f"{step_name.value} has no typed native-output producer"
        _require_parameters(
            contract.stage,
            {"ctx", "result_summary"},
            contract=f"{step_name.value} native-output producer",
        )


def test_real_layer2_rows_use_the_production_stager_and_completion_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A second output family proves staging is not a VEP-only special case."""
    import idis.persistence.repositories.layer2_challenge as layer2_repository_module
    from idis.models.layer2_durability import Layer2ChallengeRow, Layer2FindingRow
    from idis.models.layer2_ic_challenge import (
        Layer2ChallengeCategory,
        Layer2ICChallengeFinding,
        Layer2ICChallengeRecord,
        Layer2ICChallengeStatus,
    )

    claim_id = "claim_mth_0123456789abcdef01234567"
    record = Layer2ICChallengeRecord(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        layer2_challenge_id="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        source_debate_id="bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
        status=Layer2ICChallengeStatus.COMPLETED,
        claim_ids=[claim_id],
        calc_ids=["calc-1"],
        findings=[
            Layer2ICChallengeFinding(
                finding_id="layer2-finding-1",
                finding_type="ic_challenge",
                severity="HIGH",
                category=Layer2ChallengeCategory.MARKET_RISK,
                supported_claim_ids=[claim_id],
                supported_calc_ids=["calc-1"],
            )
        ],
        unresolved_question_count=1,
        muhasabah_passed=True,
    )
    expected_challenge = Layer2ChallengeRow.from_record(record)
    expected_finding = Layer2FindingRow.from_finding(
        record.findings[0],
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        challenge_id=record.layer2_challenge_id,
    )
    repositories: list[tuple[Any, str]] = []
    challenges: list[Layer2ChallengeRow] = []
    findings: list[Layer2FindingRow] = []

    class CompletionRepository:
        def __init__(self, conn: Any, tenant_id: str) -> None:
            repositories.append((conn, tenant_id))

        def upsert_challenge(self, row: Layer2ChallengeRow) -> None:
            challenges.append(row)

        def upsert_finding(self, row: Layer2FindingRow) -> None:
            findings.append(row)

    monkeypatch.setattr(
        layer2_repository_module,
        "PostgresLayer2ChallengeRepository",
        CompletionRepository,
    )
    monkeypatch.setattr(layer2_repository_module, "is_postgres_configured", lambda: True)
    stager_builder = getattr(steps_module, "build_postgres_native_output_stager", None)
    assert callable(stager_builder), "canonical runtime is missing its production native stager"
    stager = stager_builder()
    context = SimpleNamespace(
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        run_id=RUN_ID,
        layer2_ic_challenge_record=record,
    )
    writers = stager(
        ctx=context,
        step_name=StepName.LAYER2_IC_CHALLENGE,
        result_summary=record.to_run_step_summary(),
    )
    assert len(writers) >= 1
    completion_connection = object()
    for writer in writers:
        assert not inspect.isfunction(writer)
        assert writer.contract.step_name == StepName.LAYER2_IC_CHALLENGE
        assert (
            writer.contract.output_type_id
            == EXPECTED_NATIVE_OUTPUT_CONTRACTS[StepName.LAYER2_IC_CHALLENGE][0]
        )
        assert isinstance(writer.output, writer.contract.output_model_type)
        payload = _typed_output_payload(writer.output)
        assert payload["tenant_id"] == TENANT_ID
        assert payload["deal_id"] == DEAL_ID
        assert payload["run_id"] == RUN_ID
        _require_parameters(
            writer.persist,
            {"db_conn", "lease_owner_id", "lease_fencing_token"},
            contract="production staged Layer-2 writer",
        )
        writer.persist(
            db_conn=completion_connection,
            lease_owner_id=SECOND_WORKER_ID,
            lease_fencing_token=3,
        )
    assert repositories == [(completion_connection, TENANT_ID)]
    assert challenges == [expected_challenge]
    assert findings == [expected_finding]


def test_native_semantic_poison_preserves_tenant_deal_and_run_binding() -> None:
    step_name = StepName.METHODOLOGY_VALIDATED_EVIDENCE_PACKAGE
    state = _initial_reopenable_claimed_state(owner_id=SECOND_WORKER_ID, token=3)
    state["native_writes"] = [
        {
            "params": {
                "tenant_id": TENANT_ID,
                "deal_id": DEAL_ID,
                "run_id": RUN_ID,
                "finding_count": 4,
            },
            "output_payload": {
                "tenant_id": TENANT_ID,
                "deal_id": DEAL_ID,
                "run_id": RUN_ID,
                "finding_count": 4,
            },
        }
    ]
    state["native_writes_by_step"] = {step_name.value: [0]}
    index, key, original, poisoned = _poison_bound_native_content(state, step_name)
    assert (index, key, original, poisoned) == (0, "finding_count", 4, 5)
    assert state["native_writes"][0]["params"] == {
        "tenant_id": TENANT_ID,
        "deal_id": DEAL_ID,
        "run_id": RUN_ID,
        "finding_count": 5,
    }


def test_context_oracle_normalizes_lease_identity_but_detects_dynamic_markers() -> None:
    first = SimpleNamespace(
        run_id=RUN_ID,
        tenant_id=TENANT_ID,
        deal_id=DEAL_ID,
        mode="FULL",
        documents=[],
        lease_owner_id=FIRST_WORKER_ID,
        lease_fencing_token=3,
    )
    second = copy.deepcopy(first)
    second.lease_owner_id = SECOND_WORKER_ID
    second.lease_fencing_token = 5
    assert _context_resume_snapshot(first) == _context_resume_snapshot(second)

    before = _context_mutation_snapshot(second)
    second._resume_proof = "unused-output-marker"
    after = _context_mutation_snapshot(second)
    assert before != after
    assert "_resume_proof" not in _CONTEXT_BUSINESS_FIELDS
    assert all(
        "_resume_proof" not in fields for fields in REVIEWED_REHYDRATION_CONTEXT_FIELDS.values()
    )


def test_restart_substrate_rejects_sql_targets_outside_the_reviewed_binding(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from idis.models.layer1_durability import ValidatedEvidencePackageRow
    from idis.persistence.repositories.layer1_evidence import (
        PostgresLayer1EvidenceRepository,
    )

    step_name = StepName.METHODOLOGY_VALIDATED_EVIDENCE_PACKAGE
    expected = REVIEWED_STEP_OUTPUT_BINDINGS[step_name].produced_write_bindings[0]
    binding = SimpleNamespace(
        row_model_type=ValidatedEvidencePackageRow,
        repository_type=PostgresLayer1EvidenceRepository,
        persist_method=expected.persist_method,
        load_methods=expected.load_methods,
        sql_targets=expected.sql_targets,
        non_sql_methods=expected.non_sql_methods,
    )
    contract = SimpleNamespace(
        step_name=step_name,
        output_type_id=EXPECTED_NATIVE_OUTPUT_CONTRACTS[step_name][0],
        persistence_target=EXPECTED_NATIVE_OUTPUT_CONTRACTS[step_name][1],
    )
    state_path = tmp_path / "reject-unreviewed-target.json"
    _write_reopenable_state(
        state_path,
        _initial_reopenable_claimed_state(owner_id=SECOND_WORKER_ID, token=3),
    )
    connection = _ReopenablePostgresConnection(
        path=state_path,
        connection_id="target-rejection-1",
        forbidden_connection_ids=set(),
    )
    connection.active_native_write = {
        "contract": contract,
        "binding": binding,
        "output_payload": {
            "tenant_id": TENANT_ID,
            "deal_id": DEAL_ID,
            "run_id": RUN_ID,
        },
    }

    def upsert_validated_evidence_package(self: Any, _row: Any) -> None:
        self._conn.execute(
            "INSERT INTO invented_native_outputs "
            "(tenant_id, deal_id, run_id) VALUES (:tenant_id, :deal_id, :run_id)",
            {"tenant_id": TENANT_ID, "deal_id": DEAL_ID, "run_id": RUN_ID},
        )

    monkeypatch.setattr(
        PostgresLayer1EvidenceRepository,
        "upsert_validated_evidence_package",
        upsert_validated_evidence_package,
    )
    repository = object.__new__(PostgresLayer1EvidenceRepository)
    repository._conn = connection
    repository._tenant_id = TENANT_ID
    with pytest.raises(AssertionError, match="unreviewed target"):
        repository.upsert_validated_evidence_package(object())
    assert connection.working["native_writes"] == []

    class ImposterLayer1Repository(PostgresLayer1EvidenceRepository):
        def upsert_validated_evidence_package(self, _row: Any) -> None:
            self._conn.execute(
                "INSERT INTO validated_evidence_packages "
                "(tenant_id, deal_id, run_id) VALUES (:tenant_id, :deal_id, :run_id)",
                {"tenant_id": TENANT_ID, "deal_id": DEAL_ID, "run_id": RUN_ID},
            )

    imposter = object.__new__(ImposterLayer1Repository)
    imposter._conn = connection
    imposter._tenant_id = TENANT_ID
    with pytest.raises(AssertionError, match="bypassed its declared production repository"):
        imposter.upsert_validated_evidence_package(object())
    assert connection.working["native_writes"] == []


def test_restart_substrate_rejects_native_reads_outside_the_reviewed_binding(
    tmp_path: Path,
) -> None:
    state_path = tmp_path / "reject-unreviewed-read.json"
    _write_reopenable_state(
        state_path,
        _initial_reopenable_claimed_state(owner_id=SECOND_WORKER_ID, token=3),
    )
    connection = _ReopenablePostgresConnection(
        path=state_path,
        connection_id="read-target-rejection-1",
        forbidden_connection_ids=set(),
    )
    connection.rls_tenants.append(TENANT_ID)
    trace = _TypedResumeTrace(
        active_step=StepName.METHODOLOGY_VALIDATED_EVIDENCE_PACKAGE,
        active_loader_contract=SimpleNamespace(
            step_name=StepName.METHODOLOGY_VALIDATED_EVIDENCE_PACKAGE
        ),
    )
    token = _ACTIVE_TYPED_RESUME_TRACE.set(trace)
    try:
        with pytest.raises(AssertionError, match="unreviewed target"):
            connection._record_bound_resume_read(
                table="invented_native_outputs",
                sql=_normalized_sql(
                    "SELECT * FROM invented_native_outputs "
                    "WHERE tenant_id = :tenant_id AND run_id = :run_id"
                ),
                values={"tenant_id": TENANT_ID, "run_id": RUN_ID},
            )
        with pytest.raises(AssertionError, match="result_summary"):
            connection._record_bound_resume_read(
                table="run_steps",
                sql=_normalized_sql(
                    "SELECT * FROM run_steps WHERE tenant_id = :tenant_id AND run_id = :run_id"
                ),
                values={"tenant_id": TENANT_ID, "run_id": RUN_ID},
            )
    finally:
        _ACTIVE_TYPED_RESUME_TRACE.reset(token)
    assert connection.resume_reads == []


@pytest.mark.parametrize(
    ("relation_clause", "expected_targets"),
    (
        (
            """
            FROM documents
            JOIN document_artifacts
              ON document_artifacts.doc_id = documents.doc_id
            JOIN invented_document_edges
              ON invented_document_edges.document_id = documents.document_id
            """,
            ("documents", "document_artifacts", "invented_document_edges"),
        ),
        (
            "FROM ONLY documents d, invented_document_edges e",
            ("documents", "invented_document_edges"),
        ),
        (
            "FROM (TABLE invented_document_edges) AS documents",
            ("invented_document_edges",),
        ),
        (
            """
            FROM documents d
            JOIN invented_document_edges e ON true
            JOIN (
                WITH invented_document_edges AS (VALUES (1))
                SELECT * FROM invented_document_edges
            ) q ON true
            """,
            ("documents", _UNSUPPORTED_CTE_RELATION_TARGET),
        ),
    ),
    ids=(
        "explicit-join",
        "comma-relation-with-only",
        "table-subquery",
        "nested-cte-shadow-fails-closed",
    ),
)
def test_restart_substrate_rejects_an_unreviewed_relation_target(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    relation_clause: str,
    expected_targets: tuple[str, ...],
) -> None:
    from idis.persistence.repositories.documents import PostgresDocumentsRepository

    state_path = tmp_path / "reject-unreviewed-join-target.json"
    _write_reopenable_state(
        state_path,
        _initial_reopenable_claimed_state(owner_id=SECOND_WORKER_ID, token=3),
    )
    connection = _ReopenablePostgresConnection(
        path=state_path,
        connection_id="join-target-rejection-1",
        forbidden_connection_ids=set(),
    )
    connection.rls_tenants.append(TENANT_ID)
    sql = _normalized_sql(
        f"""
        SELECT documents.document_id
        {relation_clause}
        WHERE documents.tenant_id = :tenant_id
          AND documents.run_id = :run_id
          AND documents.deal_id = :deal_id
        """
    )
    assert _sql_relation_targets(sql) == expected_targets

    def list_documents_by_deal(
        self: Any,
        deal_id: str,
        *,
        parsed_only: bool = True,
    ) -> list[dict[str, Any]]:
        del parsed_only
        result = self._conn.execute(
            sql,
            {
                "tenant_id": TENANT_ID,
                "run_id": RUN_ID,
                "deal_id": deal_id,
            },
        )
        return list(result.fetchall())

    monkeypatch.setattr(
        PostgresDocumentsRepository,
        "list_documents_by_deal",
        list_documents_by_deal,
    )
    repository = object.__new__(PostgresDocumentsRepository)
    repository._conn = connection
    repository._tenant_id = TENANT_ID
    trace = _TypedResumeTrace(
        active_step=StepName.DATA_ROOM_INGESTION_HANDOFF,
        active_loader_contract=SimpleNamespace(step_name=StepName.DATA_ROOM_INGESTION_HANDOFF),
    )
    token = _ACTIVE_TYPED_RESUME_TRACE.set(trace)
    try:
        with pytest.raises(AssertionError, match="unreviewed target"):
            repository.list_documents_by_deal(DEAL_ID)
    finally:
        _ACTIVE_TYPED_RESUME_TRACE.reset(token)
    assert trace.native_reads == [
        (StepName.DATA_ROOM_INGESTION_HANDOFF, table) for table in expected_targets[:-1]
    ]
    assert [read["table"] for read in connection.resume_reads] == list(expected_targets[:-1])


def test_sql_relation_parser_does_not_treat_a_cte_alias_as_a_durable_read() -> None:
    assert _sql_relation_targets("WITH documents AS (VALUES (1)) SELECT * FROM documents") == (
        _UNSUPPORTED_CTE_RELATION_TARGET,
    )
    assert _sql_relation_targets(
        "WITH documents AS (TABLE invented_document_edges) SELECT * FROM documents"
    ) == (
        "invented_document_edges",
        _UNSUPPORTED_CTE_RELATION_TARGET,
    )
    adversarial_nested_scope = (
        "SELECT * FROM documents d "
        "JOIN invented_document_edges e ON true "
        "JOIN (WITH invented_document_edges AS (VALUES (1)) "
        "SELECT * FROM invented_document_edges) q ON true"
    )
    assert _sql_relation_targets(adversarial_nested_scope) == (
        "documents",
        _UNSUPPORTED_CTE_RELATION_TARGET,
    )


def _task1_audit_checkpoint_context_factory(
    *,
    crash_after_step: StepName | None,
) -> Callable[..., RunContext]:
    def build(**components: Any) -> RunContext:
        def crash_after_commit(**boundary: Any) -> None:
            if (
                crash_after_step is not None
                and boundary["boundary"] == "COMPLETE"
                and boundary["step_name"] == crash_after_step
            ):
                raise _SimulatedWorkerCrash

        return _full_lease_context(
            owner_id=components["lease_owner_id"],
            token=components["lease_fencing_token"],
            boundary_uow=components["step_boundary_uow_fn"],
            after_boundary_commit=crash_after_commit,
            with_heartbeat=components["execute_with_lease_heartbeat_fn"],
            # Task 1 proves the generic immutable-audit checkpoint; Task 7 owns native rows.
            stage_native_outputs=None,
        )

    return build


def _audit_checkpoint_read_steps(factory: _ReopenableConnectionFactory) -> list[StepName]:
    return [
        StepName(read["params"]["step_name"])
        for connection in factory.connections
        for read in connection.resume_reads
        if read["table"] == "audit_events"
    ]


def _completion_checkpoint_events(state: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        event
        for event in state["audits"]
        if str(event.get("event_type", "")).endswith(".completed")
        and isinstance(event.get("payload", {}).get("safe"), dict)
        and event["payload"]["safe"].get("result_summary_sha256")
    ]


_TASK1_RESTART_STEPS = STRICT_V2_STEPS
_TASK1_RESTART_ORDER = {
    step_name.value: index for index, step_name in enumerate(_TASK1_RESTART_STEPS)
}


def _task1_ordered_rows(state: dict[str, Any]) -> list[dict[str, Any]]:
    return sorted(
        state["steps"].values(),
        key=lambda row: _TASK1_RESTART_ORDER[row["step_name"]],
    )


@pytest.mark.parametrize(
    ("cut_index", "cut_step"),
    list(enumerate(_TASK1_RESTART_STEPS[:-1])),
    ids=[f"after-{step.value.lower()}" for step in _TASK1_RESTART_STEPS[:-1]],
)
def test_reclaimed_lease_uses_strict_resume_at_each_of_27_restart_cut_points(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    cut_index: int,
    cut_step: StepName,
) -> None:
    """A fresh worker resumes only the durably committed prefix after reclaim."""
    import idis.persistence.repositories.run_steps as run_steps_repository_module

    # Task 1 proves the current canonical lease order; Task 7 owns strict-v2 activation.
    assert len(_TASK1_RESTART_STEPS) == 28
    monkeypatch.setattr(orchestrator_module, "FULL_STEPS", list(_TASK1_RESTART_STEPS))

    def task1_dispatch(
        _orchestrator: RunOrchestrator,
        step_name: StepName,
        _ctx: RunContext,
        _accumulated: dict[str, Any],
    ) -> dict[str, Any]:
        # Task 7 owns typed producer/rehydration semantics; Task 1 isolates all 27 lease cuts.
        return {
            f"task1_{step_name.value.lower()}_checkpoint": {
                "step_name": step_name.value,
                "step_order": STEP_ORDER[step_name],
            }
        }

    monkeypatch.setattr(RunOrchestrator, "_dispatch_step", task1_dispatch)
    for method_name in set(REHYDRATORS_BY_STEP.values()):
        monkeypatch.setattr(
            RunOrchestrator,
            method_name,
            lambda _orchestrator, _ctx, *_args, **_kwargs: None,
        )

    def establish_rls(conn: _ReopenablePostgresConnection, tenant_id: str) -> None:
        assert tenant_id == TENANT_ID
        conn.rls_tenants.append(tenant_id)

    monkeypatch.setattr(execution_module, "set_tenant_local", establish_rls, raising=False)
    monkeypatch.setattr(worker_module, "set_tenant_local", establish_rls)
    monkeypatch.setattr(runs_module, "set_tenant_local", establish_rls)
    monkeypatch.setattr(run_steps_repository_module, "set_tenant_local", establish_rls)
    monkeypatch.setattr(runs_module, "is_postgres_configured", lambda: True)
    monkeypatch.setattr(
        run_steps_repository_module,
        "is_postgres_configured",
        lambda: True,
    )
    _install_dispatch_consumer_spy(monkeypatch)

    builder = worker_module.build_postgres_claimed_run_runtime
    state_path = tmp_path / f"restart-{cut_index}.json"
    _write_reopenable_state(
        state_path,
        _initial_reopenable_claimed_state(owner_id=FIRST_WORKER_ID, token=1),
    )
    worker_a_connections = _ReopenableConnectionFactory(
        path=state_path,
        process_id="worker-a",
    )
    worker_a_trace = _TypedResumeTrace()
    runtime_a = builder(
        tenant_id=TENANT_ID,
        run_data={**_read_reopenable_state(state_path)["run"]},
        lease_config=_LeaseConfigProbe(),
        audit_sink=_ReopenableAuditSink(),
        connection_factory=worker_a_connections,
        provider_context_factory=_task1_audit_checkpoint_context_factory(
            crash_after_step=cut_step,
        ),
    )

    with pytest.raises(_SimulatedWorkerCrash):
        _execute_with_consumer_trace(runtime_a, worker_a_trace)
    assert _audit_checkpoint_read_steps(worker_a_connections) == []
    assert all(connection.closed for connection in worker_a_connections.connections)
    worker_a_connection_ids = {
        connection.connection_id for connection in worker_a_connections.connections
    }

    prefix_state = _read_reopenable_state(state_path)
    prefix_rows = _task1_ordered_rows(prefix_state)
    completed_prefix = list(_TASK1_RESTART_STEPS[: cut_index + 1])
    assert [row["step_name"] for row in prefix_rows] == [step.value for step in completed_prefix]
    assert all(row["status"] == StepStatus.COMPLETED.value for row in prefix_rows), prefix_rows
    assert _TASK1_RESTART_STEPS[cut_index + 1].value not in {
        row["step_name"] for row in prefix_rows
    }
    assert prefix_state["native_writes"] == []
    assert prefix_state["native_writes_by_step"] == {}
    prefix_checkpoints = _completion_checkpoint_events(prefix_state)
    assert len(prefix_checkpoints) == len(completed_prefix)
    checkpoints_by_step_id = {
        event["payload"]["safe"]["step_id"]: event for event in prefix_checkpoints
    }
    for row in prefix_rows:
        checkpoint = checkpoints_by_step_id[row["step_id"]]
        safe = checkpoint["payload"]["safe"]
        assert safe["run_id"] == RUN_ID
        assert safe["step_name"] == row["step_name"]
        assert safe["result_summary_sha256"] == (
            orchestrator_module.canonical_result_summary_sha256(row["result_summary"])
        )
        assert checkpoint["payload"]["hashes"] == [safe["result_summary_sha256"]]
    prefix_identity = {
        row["step_name"]: (row["step_id"], row["retry_count"], row["result_summary"])
        for row in prefix_rows
    }

    expired_state = _read_reopenable_state(state_path)
    expired_state["run"]["lease_expires_at"] = "2000-01-01T00:00:00Z"
    _write_reopenable_state(state_path, expired_state)
    recovery_connections = _ReopenableConnectionFactory(
        path=state_path,
        process_id="reclaimer",
        forbidden_connection_ids=worker_a_connection_ids,
    )
    recovery_audit = _ReopenableAuditSink()

    def transition_audit_emitter(
        *,
        db_conn: Any,
        code: str,
        **safe: Any,
    ) -> None:
        recovery_audit.emit_in_tx(
            db_conn,
            {
                "event_type": LEASE_EVENT_TYPES[code],
                "payload": {"safe": {"code": code, **safe}},
            },
        )

    with recovery_connections() as reclaim_connection:
        requeued = PostgresRunsRepository(
            reclaim_connection, TENANT_ID
        ).requeue_expired_worker_leases(
            limit=1,
            lease_audit_emitter=transition_audit_emitter,
        )
    assert len(requeued) == 1
    assert requeued[0]["status"] == "QUEUED"
    assert requeued[0]["lease_fencing_token"] == 2
    assert requeued[0]["lease_owner_id"] is None

    with recovery_connections() as claim_connection:
        claimed = PostgresRunsRepository(claim_connection, TENANT_ID).claim_queued_runs(
            limit=1,
            lease_owner_id=SECOND_WORKER_ID,
            lease_seconds=LEASE_SECONDS,
            lease_audit_emitter=transition_audit_emitter,
        )
    assert len(claimed) == 1
    assert claimed[0]["status"] == "RUNNING"
    assert claimed[0]["lease_fencing_token"] == 3
    assert claimed[0]["lease_owner_id"] == SECOND_WORKER_ID

    if cut_index == 0:
        tamper_path = tmp_path / "restart-audit-checkpoint-tampered.json"
        tampered_state = copy.deepcopy(_read_reopenable_state(state_path))
        tampered_row = next(iter(tampered_state["steps"].values()))
        tampered_row["result_summary"] = {
            **tampered_row["result_summary"],
            "_tampered_after_commit": True,
        }
        _write_reopenable_state(tamper_path, tampered_state)
        tamper_connections = _ReopenableConnectionFactory(
            path=tamper_path,
            process_id="checkpoint-tamper",
            forbidden_connection_ids=worker_a_connection_ids,
        )
        tamper_runtime = builder(
            tenant_id=TENANT_ID,
            run_data={**tampered_state["run"]},
            lease_config=_LeaseConfigProbe(),
            audit_sink=_ReopenableAuditSink(),
            connection_factory=tamper_connections,
            provider_context_factory=_task1_audit_checkpoint_context_factory(crash_after_step=None),
        )
        tamper_result = _execute_with_consumer_trace(tamper_runtime, _TypedResumeTrace())
        assert tamper_result.status == "FAILED"
        assert tamper_result.error_code == "STEP_SEMANTIC_VALIDATION_FAILED"
        assert _audit_checkpoint_read_steps(tamper_connections) == [completed_prefix[0]]
        tamper_after = _read_reopenable_state(tamper_path)
        assert next(iter(tamper_after["steps"].values()))["status"] == StepStatus.FAILED.value
        assert tamper_after["native_writes"] == []
        assert any(
            str(event.get("event_type", "")).endswith(".failed") for event in tamper_after["audits"]
        ), "resume checkpoint rejection did not commit through its fenced failure boundary"

    before_zombie_write = state_path.read_bytes()
    with pytest.raises(Exception) as zombie_error:
        runtime_a.step_boundary_uow_fn(
            run_id=RUN_ID,
            tenant_id=TENANT_ID,
            step_name=_TASK1_RESTART_STEPS[cut_index + 1],
            boundary="START",
            lease_owner_id=FIRST_WORKER_ID,
            lease_fencing_token=1,
            mutation=lambda **_kwargs: None,
            native_output_writes=(),
        )
    assert getattr(zombie_error.value, "code", None) == "WORKER_LEASE_FENCED"
    assert state_path.read_bytes() == before_zombie_write

    worker_b_connections = _ReopenableConnectionFactory(
        path=state_path,
        process_id="worker-b",
        forbidden_connection_ids=worker_a_connection_ids,
    )
    worker_b_trace = _TypedResumeTrace()
    runtime_b = builder(
        tenant_id=TENANT_ID,
        run_data={**_read_reopenable_state(state_path)["run"]},
        lease_config=_LeaseConfigProbe(),
        audit_sink=_ReopenableAuditSink(),
        connection_factory=worker_b_connections,
        provider_context_factory=_task1_audit_checkpoint_context_factory(crash_after_step=None),
    )
    result = _execute_with_consumer_trace(runtime_b, worker_b_trace)
    assert result.status == "SUCCEEDED", result
    assert _audit_checkpoint_read_steps(worker_b_connections) == completed_prefix
    assert worker_a_connection_ids.isdisjoint(
        {connection.connection_id for connection in worker_b_connections.connections}
    )
    assert all(connection.closed for connection in worker_b_connections.connections)

    final_state = _read_reopenable_state(state_path)
    final_rows = _task1_ordered_rows(final_state)
    assert [row["step_name"] for row in final_rows] == [step.value for step in _TASK1_RESTART_STEPS]
    assert final_state["native_writes"] == []
    assert final_state["native_writes_by_step"] == {}
    assert len(_completion_checkpoint_events(final_state)) == len(_TASK1_RESTART_STEPS)
    for row in final_rows[: cut_index + 1]:
        assert (row["step_id"], row["retry_count"], row["result_summary"]) == prefix_identity[
            row["step_name"]
        ]

    oracle_path = tmp_path / f"uninterrupted-{cut_index}.json"
    _write_reopenable_state(
        oracle_path,
        _initial_reopenable_claimed_state(owner_id=SECOND_WORKER_ID, token=3),
    )
    oracle_connections = _ReopenableConnectionFactory(
        path=oracle_path,
        process_id="oracle",
        forbidden_connection_ids=(
            worker_a_connection_ids
            | {connection.connection_id for connection in worker_b_connections.connections}
        ),
    )
    oracle_trace = _TypedResumeTrace()
    oracle_runtime = builder(
        tenant_id=TENANT_ID,
        run_data={**_read_reopenable_state(oracle_path)["run"]},
        lease_config=_LeaseConfigProbe(),
        audit_sink=_ReopenableAuditSink(),
        connection_factory=oracle_connections,
        provider_context_factory=_task1_audit_checkpoint_context_factory(crash_after_step=None),
    )
    oracle_result = _execute_with_consumer_trace(oracle_runtime, oracle_trace)
    assert oracle_result.status == "SUCCEEDED"
    assert _audit_checkpoint_read_steps(oracle_connections) == []
    assert _step_semantics(final_state) == _step_semantics(_read_reopenable_state(oracle_path))
    assert _native_write_semantics(final_state) == _native_write_semantics(
        _read_reopenable_state(oracle_path)
    )


@pytest.mark.parametrize(
    ("cut_index", "cut_step"),
    list(enumerate(STRICT_V2_STEPS[:-1])),
    ids=[f"after-{step.value.lower()}" for step in STRICT_V2_STEPS[:-1]],
)
def _task7_typed_receipt_restart_contract_pending_schema_review(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    cut_index: int,
    cut_step: StepName,
) -> None:
    """Both workers come from the production composition root over reopened durable state."""
    import idis.persistence.repositories.run_steps as run_steps_repository_module

    builder = getattr(worker_module, "build_postgres_claimed_run_runtime", None)
    assert callable(builder), (
        "canonical worker is missing its claimed-run production composition root"
    )
    _require_parameters(
        builder,
        {
            "tenant_id",
            "run_data",
            "lease_config",
            "audit_sink",
            "connection_factory",
            "provider_context_factory",
        },
        contract="Postgres claimed-run production runtime",
    )
    assert len(STRICT_V2_STEPS) == 28
    assert STRICT_V2_STEPS[cut_index] == cut_step
    state_path = tmp_path / f"restart-{cut_index}.json"
    _write_reopenable_state(
        state_path,
        _initial_reopenable_claimed_state(owner_id=FIRST_WORKER_ID, token=1),
    )

    def establish_rls(conn: _ReopenablePostgresConnection, tenant_id: str) -> None:
        assert tenant_id == TENANT_ID
        conn.rls_tenants.append(tenant_id)

    monkeypatch.setattr(execution_module, "set_tenant_local", establish_rls, raising=False)
    monkeypatch.setattr(runs_module, "set_tenant_local", establish_rls)
    monkeypatch.setattr(run_steps_repository_module, "set_tenant_local", establish_rls)
    _install_dispatch_consumer_spy(monkeypatch)
    worker_a_audit_sink = _ReopenableAuditSink()
    worker_a_lease_config = _LeaseConfigProbe()
    worker_a_validations: list[StepName] = []
    worker_a_resume_trace = _TypedResumeTrace()
    worker_a_connections = _ReopenableConnectionFactory(path=state_path, process_id="worker-a")
    runtime_a = builder(
        tenant_id=TENANT_ID,
        run_data={
            **_read_reopenable_state(state_path)["run"],
        },
        lease_config=worker_a_lease_config,
        audit_sink=worker_a_audit_sink,
        connection_factory=worker_a_connections,
        provider_context_factory=_claimed_runtime_provider_context_factory(
            validation_calls=worker_a_validations,
            resume_trace=worker_a_resume_trace,
            crash_after_step=cut_step,
        ),
    )
    _install_contract_resume_spies(monkeypatch, runtime_a.native_output_contracts)
    for attribute in (
        "context",
        "runs_repository_factory",
        "run_steps_repository_factory",
        "native_output_contracts",
        "native_output_repository_factories",
        "step_boundary_uow_fn",
        "stage_native_output_writes_fn",
        "completed_step_resume_validator",
        "execute_with_lease_heartbeat_fn",
        "execute_preclaimed",
    ):
        assert hasattr(runtime_a, attribute), f"claimed runtime omitted {attribute}"
    for attribute in (
        "runs_repository_factory",
        "run_steps_repository_factory",
        "step_boundary_uow_fn",
        "stage_native_output_writes_fn",
        "completed_step_resume_validator",
        "execute_with_lease_heartbeat_fn",
        "execute_preclaimed",
    ):
        assert callable(getattr(runtime_a, attribute)), f"claimed runtime {attribute} is inert"
    assert set(runtime_a.native_output_contracts) == set(STRICT_V2_STEPS)
    assert set(runtime_a.native_output_repository_factories) == set(STRICT_V2_STEPS)
    assert all(
        isinstance(factories, dict)
        for factories in runtime_a.native_output_repository_factories.values()
    )
    from idis.persistence.repositories.run_steps import PostgresRunStepsRepository

    with worker_a_connections() as repository_probe_connection:
        assert isinstance(
            runtime_a.runs_repository_factory(repository_probe_connection),
            PostgresRunsRepository,
        )
        assert isinstance(
            runtime_a.run_steps_repository_factory(repository_probe_connection),
            PostgresRunStepsRepository,
        )
        for step_name in STRICT_V2_STEPS:
            contract = runtime_a.native_output_contracts[step_name]
            expected_output_type, expected_target = EXPECTED_NATIVE_OUTPUT_CONTRACTS[step_name]
            assert contract.step_name == step_name
            assert contract.output_type_id == expected_output_type
            assert contract.persistence_target == expected_target
            _assert_contract_matches_reviewed_binding(step_name, contract)
            factories = runtime_a.native_output_repository_factories[step_name]
            assert set(factories) == {
                binding.repository_type for binding in contract.write_bindings
            }
            for repository_type, factory in factories.items():
                assert callable(factory)
                repository = factory(repository_probe_connection)
                assert type(repository) is repository_type
    assert runtime_a.context.step_boundary_uow_fn is runtime_a.step_boundary_uow_fn
    assert (
        runtime_a.context.stage_native_output_writes_fn.production_stager
        is runtime_a.stage_native_output_writes_fn
    )
    assert (
        runtime_a.context.stage_native_output_writes_fn.contract_registry
        is runtime_a.native_output_contracts
    )
    assert (
        runtime_a.context.execute_with_lease_heartbeat_fn
        is runtime_a.execute_with_lease_heartbeat_fn
    )

    with pytest.raises(_SimulatedWorkerCrash):
        _execute_with_consumer_trace(runtime_a, worker_a_resume_trace)
    assert worker_a_validations == []
    assert worker_a_resume_trace.load_attempts == []
    assert worker_a_resume_trace.rehydrate_calls == []
    assert list(worker_a_resume_trace.consumer_inputs) == list(STRICT_V2_STEPS[: cut_index + 1])
    worker_a_connection_ids = {
        connection.connection_id for connection in worker_a_connections.connections
    }
    assert worker_a_connection_ids
    assert all(connection.closed for connection in worker_a_connections.connections)
    prefix_state = _read_reopenable_state(state_path)
    prefix_rows = sorted(prefix_state["steps"].values(), key=lambda row: row["step_order"])
    assert [row["step_name"] for row in prefix_rows] == [
        step.value for step in STRICT_V2_STEPS[: cut_index + 1]
    ]
    assert STRICT_V2_STEPS[cut_index + 1].value not in {row["step_name"] for row in prefix_rows}
    assert all(row["status"] == StepStatus.COMPLETED.value for row in prefix_rows)
    assert set(prefix_state["native_writes_by_step"]) == {
        step.value for step in STRICT_V2_STEPS[: cut_index + 1]
    }
    assert all(prefix_state["native_writes_by_step"].values())
    prefix_native_writes = copy.deepcopy(prefix_state["native_writes"])
    prefix_identity = {
        row["step_name"]: (row["step_id"], row["retry_count"], row["result_summary"])
        for row in prefix_rows
    }

    expired_state = _read_reopenable_state(state_path)
    expired_state["run"]["lease_expires_at"] = "2000-01-01T00:00:00Z"
    _write_reopenable_state(state_path, expired_state)
    recovery_connections = _ReopenableConnectionFactory(
        path=state_path,
        process_id="reclaimer",
        forbidden_connection_ids=worker_a_connection_ids,
    )
    recovery_audit_sink = _ReopenableAuditSink()

    def transition_audit_emitter(
        *,
        db_conn: Any,
        code: str,
        _audit_sink: _ReopenableAuditSink = recovery_audit_sink,
        **safe: Any,
    ) -> None:
        _audit_sink.emit_in_tx(
            db_conn,
            {
                "event_type": LEASE_EVENT_TYPES[code],
                "payload": {"safe": {"code": code, **safe}},
            },
        )

    with recovery_connections() as reclaim_connection:
        reclaim_repository = PostgresRunsRepository(reclaim_connection, TENANT_ID)
        requeued = reclaim_repository.requeue_expired_worker_leases(
            limit=1,
            lease_audit_emitter=transition_audit_emitter,
        )
    assert len(requeued) == 1
    assert requeued[0]["status"] == "QUEUED"
    assert requeued[0]["lease_owner_id"] is None
    assert requeued[0]["lease_fencing_token"] == 2

    with recovery_connections() as claim_connection:
        claim_repository = PostgresRunsRepository(claim_connection, TENANT_ID)
        claimed = claim_repository.claim_queued_runs(
            limit=1,
            lease_owner_id=SECOND_WORKER_ID,
            lease_seconds=LEASE_SECONDS,
            lease_audit_emitter=transition_audit_emitter,
        )
    assert len(claimed) == 1
    assert claimed[0]["status"] == "RUNNING"
    assert claimed[0]["lease_owner_id"] == SECOND_WORKER_ID
    assert claimed[0]["lease_fencing_token"] == 3

    tamper_path = tmp_path / f"tampered-{cut_index}.json"
    tampered_state = copy.deepcopy(_read_reopenable_state(state_path))
    tampered_step = cut_step.value
    poison_proof = _poison_bound_native_content(tampered_state, cut_step)
    assert poison_proof[2] != poison_proof[3]
    assert tampered_state["native_writes_by_step"][tampered_step]
    assert tampered_state["steps"] == _read_reopenable_state(state_path)["steps"], (
        "native-row poison must leave the completed RunStep summaries intact"
    )
    _write_reopenable_state(tamper_path, tampered_state)
    tamper_validations: list[StepName] = []
    tamper_resume_trace = _TypedResumeTrace()
    tamper_connections = _ReopenableConnectionFactory(
        path=tamper_path,
        process_id="tamper-probe",
        forbidden_connection_ids=worker_a_connection_ids,
    )
    tamper_runtime = builder(
        tenant_id=TENANT_ID,
        run_data={**tampered_state["run"]},
        lease_config=_LeaseConfigProbe(),
        audit_sink=_ReopenableAuditSink(),
        connection_factory=tamper_connections,
        provider_context_factory=_claimed_runtime_provider_context_factory(
            validation_calls=tamper_validations,
            resume_trace=tamper_resume_trace,
            crash_after_step=None,
        ),
    )
    _install_contract_resume_spies(monkeypatch, tamper_runtime.native_output_contracts)
    tamper_error: Exception | None = None
    tamper_result: Any = None
    try:
        tamper_result = _execute_with_consumer_trace(tamper_runtime, tamper_resume_trace)
    except Exception as exc:
        tamper_error = exc
    assert tamper_validations == list(STRICT_V2_STEPS[: cut_index + 1])
    assert tamper_resume_trace.load_attempts == list(STRICT_V2_STEPS[: cut_index + 1])
    assert tamper_resume_trace.load_successes == list(STRICT_V2_STEPS[:cut_index])
    assert tamper_resume_trace.summary_calls == list(STRICT_V2_STEPS[:cut_index])
    assert tamper_resume_trace.rehydrate_calls == list(STRICT_V2_STEPS[:cut_index])
    assert list(tamper_resume_trace.rehydrated_fields) == list(STRICT_V2_STEPS[:cut_index])
    assert list(tamper_resume_trace.rehydrated_output_tokens) == list(STRICT_V2_STEPS[:cut_index])
    assert list(dict.fromkeys(step for step, _table in tamper_resume_trace.native_reads)) == list(
        STRICT_V2_STEPS[: cut_index + 1]
    )
    assert tamper_resume_trace.consumer_inputs == {}
    if tamper_error is not None:
        assert getattr(tamper_error, "code", None) == "STEP_REHYDRATION_FAILED"
    else:
        assert tamper_result.status == "FAILED"
        assert tamper_result.error_code == "STEP_REHYDRATION_FAILED"
    tamper_after = _read_reopenable_state(tamper_path)
    assert tamper_after["native_writes"] == tampered_state["native_writes"]
    assert tamper_after["native_writes_by_step"] == tampered_state["native_writes_by_step"]
    assert tamper_after["steps"] == tampered_state["steps"]

    before_zombie_write = state_path.read_bytes()
    zombie_mutations: list[str] = []
    caught: Exception | None = None
    try:
        runtime_a.step_boundary_uow_fn(
            run_id=RUN_ID,
            tenant_id=TENANT_ID,
            step_name=STRICT_V2_STEPS[cut_index + 1],
            boundary="START",
            lease_owner_id=FIRST_WORKER_ID,
            lease_fencing_token=1,
            mutation=lambda **_kwargs: zombie_mutations.append("step"),
            native_output_writes=(),
        )
    except Exception as exc:
        caught = exc
    assert caught is not None
    assert getattr(caught, "code", None) == "WORKER_LEASE_FENCED"
    assert zombie_mutations == []
    assert state_path.read_bytes() == before_zombie_write
    worker_a_connection_ids = {
        connection.connection_id for connection in worker_a_connections.connections
    }
    assert all(connection.closed for connection in worker_a_connections.connections)

    tamper_error = None
    del tamper_runtime
    del tamper_connections
    del reclaim_repository
    del claim_repository
    del reclaim_connection
    del claim_connection
    del transition_audit_emitter
    del recovery_connections
    del recovery_audit_sink

    worker_a_lifetime_refs = tuple(
        weakref.ref(component)
        for component in (
            runtime_a,
            runtime_a.context,
            runtime_a.runs_repository_factory,
            runtime_a.run_steps_repository_factory,
            runtime_a.step_boundary_uow_fn,
            runtime_a.stage_native_output_writes_fn,
            runtime_a.completed_step_resume_validator,
            runtime_a.execute_with_lease_heartbeat_fn,
            worker_a_connections,
            worker_a_audit_sink,
            worker_a_lease_config,
        )
    )
    caught = None
    del runtime_a
    del worker_a_connections
    del worker_a_audit_sink
    del worker_a_lease_config
    del repository_probe_connection
    gc.collect()
    assert all(reference() is None for reference in worker_a_lifetime_refs), (
        "worker-A runtime/context/factory objects survived simulated process loss"
    )

    worker_b_validations: list[StepName] = []
    worker_b_connections = _ReopenableConnectionFactory(
        path=state_path,
        process_id="worker-b",
        forbidden_connection_ids=worker_a_connection_ids,
    )
    worker_b_audit_sink = _ReopenableAuditSink()
    worker_b_lease_config = _LeaseConfigProbe()
    worker_b_resume_trace = _TypedResumeTrace()
    legacy_summary_rehydration_calls: list[StepName] = []

    def forbid_summary_rehydrator(step_name: StepName) -> Callable[..., Any]:
        def forbidden(
            _orchestrator: RunOrchestrator,
            ctx: RunContext,
            *_args: Any,
            **_kwargs: Any,
        ) -> Any:
            assert ctx.tenant_id == TENANT_ID
            assert ctx.run_id == RUN_ID
            legacy_summary_rehydration_calls.append(step_name)
            raise AssertionError(
                f"{step_name.value} attempted legacy result_summary reconstruction"
            )

        return forbidden

    for rehydrated_step, method_name in REHYDRATORS_BY_STEP.items():
        original = getattr(RunOrchestrator, method_name, None)
        assert callable(original), f"production rehydrator {method_name} is missing"
        monkeypatch.setattr(
            RunOrchestrator,
            method_name,
            forbid_summary_rehydrator(rehydrated_step),
        )

    runtime_b = builder(
        tenant_id=TENANT_ID,
        run_data={**_read_reopenable_state(state_path)["run"]},
        lease_config=worker_b_lease_config,
        audit_sink=worker_b_audit_sink,
        connection_factory=worker_b_connections,
        provider_context_factory=_claimed_runtime_provider_context_factory(
            validation_calls=worker_b_validations,
            resume_trace=worker_b_resume_trace,
            crash_after_step=None,
        ),
    )
    _install_contract_resume_spies(monkeypatch, runtime_b.native_output_contracts)
    result = _execute_with_consumer_trace(runtime_b, worker_b_resume_trace)
    assert result.status == "SUCCEEDED"
    resumed_context_snapshot = _context_resume_snapshot(runtime_b.context)
    completed_prefix = list(STRICT_V2_STEPS[: cut_index + 1])
    resumed_suffix = list(STRICT_V2_STEPS[cut_index + 1 :])
    assert worker_b_validations == completed_prefix
    assert worker_b_resume_trace.load_attempts == completed_prefix
    assert worker_b_resume_trace.load_successes == completed_prefix
    assert worker_b_resume_trace.summary_calls == completed_prefix
    assert worker_b_resume_trace.rehydrate_calls == completed_prefix
    assert list(worker_b_resume_trace.rehydrated_fields) == completed_prefix
    assert list(worker_b_resume_trace.rehydrated_output_tokens) == completed_prefix
    assert (
        list(dict.fromkeys(step for step, _table in worker_b_resume_trace.native_reads))
        == completed_prefix
    )
    assert list(worker_b_resume_trace.loaded_outputs) == completed_prefix
    assert list(worker_b_resume_trace.consumer_inputs) == resumed_suffix
    assert legacy_summary_rehydration_calls == []
    assert worker_b_connections.connections
    assert all(connection.closed for connection in worker_b_connections.connections)
    assert worker_a_connection_ids.isdisjoint(
        {connection.connection_id for connection in worker_b_connections.connections}
    )
    resume_read_connections = [
        connection for connection in worker_b_connections.connections if connection.resume_reads
    ]
    assert resume_read_connections, "restart resume performed no reopened durable reads"
    assert all(connection.rls_tenants for connection in resume_read_connections)
    assert any(
        read["table"] == "run_steps"
        for connection in resume_read_connections
        for read in connection.resume_reads
    )
    assert any(
        read["table"] != "run_steps"
        for connection in resume_read_connections
        for read in connection.resume_reads
    ), "strict resume validated summaries but never loaded first-class native output rows"

    final_state = _read_reopenable_state(state_path)
    final_rows = sorted(final_state["steps"].values(), key=lambda row: row["step_order"])
    assert [row["step_name"] for row in final_rows] == [step.value for step in STRICT_V2_STEPS]
    assert all(row["status"] == StepStatus.COMPLETED.value for row in final_rows)
    assert set(final_state["native_writes_by_step"]) == {step.value for step in STRICT_V2_STEPS}
    assert all(final_state["native_writes_by_step"].values())
    assert final_state["native_writes"][: len(prefix_native_writes)] == prefix_native_writes
    for row in final_rows[: cut_index + 1]:
        assert (row["step_id"], row["retry_count"], row["result_summary"]) == prefix_identity[
            row["step_name"]
        ]

    # The 27 inter-step cuts never resume after DELIVERABLES. Exercise one
    # all-completed restart explicitly so its loader/rehydrator cannot be a stub.
    if cut_index == len(STRICT_V2_STEPS) - 2:
        completed_resume_path = tmp_path / "restart-after-all-28.json"
        completed_resume_state = copy.deepcopy(final_state)
        completed_resume_state["run"].update(
            status="RUNNING",
            lease_owner_id=SECOND_WORKER_ID,
            lease_fencing_token=5,
            lease_expires_at="2026-07-15T12:10:00Z",
            last_heartbeat_at="2026-07-15T12:09:00Z",
            finished_at=None,
        )
        _write_reopenable_state(completed_resume_path, completed_resume_state)
        completed_validations: list[StepName] = []
        completed_trace = _TypedResumeTrace()
        completed_connections = _ReopenableConnectionFactory(
            path=completed_resume_path,
            process_id="all-completed-resumer",
            forbidden_connection_ids=(
                worker_a_connection_ids
                | {connection.connection_id for connection in worker_b_connections.connections}
            ),
        )
        completed_runtime = builder(
            tenant_id=TENANT_ID,
            run_data={**completed_resume_state["run"]},
            lease_config=_LeaseConfigProbe(),
            audit_sink=_ReopenableAuditSink(),
            connection_factory=completed_connections,
            provider_context_factory=_claimed_runtime_provider_context_factory(
                validation_calls=completed_validations,
                resume_trace=completed_trace,
                crash_after_step=None,
            ),
        )
        _install_contract_resume_spies(
            monkeypatch,
            completed_runtime.native_output_contracts,
        )
        completed_result = _execute_with_consumer_trace(completed_runtime, completed_trace)
        assert completed_result.status == "SUCCEEDED"
        full_step_list = list(STRICT_V2_STEPS)
        assert completed_validations == full_step_list
        assert completed_trace.load_attempts == full_step_list
        assert completed_trace.load_successes == full_step_list
        assert completed_trace.summary_calls == full_step_list
        assert completed_trace.rehydrate_calls == full_step_list
        assert list(completed_trace.rehydrated_fields) == full_step_list
        assert list(completed_trace.rehydrated_output_tokens) == full_step_list
        assert (
            list(dict.fromkeys(step for step, _table in completed_trace.native_reads))
            == full_step_list
        )
        assert completed_trace.consumer_inputs == {}
        assert legacy_summary_rehydration_calls == []
        assert _context_resume_snapshot(completed_runtime.context) == resumed_context_snapshot
        completed_after = _read_reopenable_state(completed_resume_path)
        assert completed_after["steps"] == completed_resume_state["steps"]
        assert completed_after["native_writes"] == completed_resume_state["native_writes"]
        assert (
            completed_after["native_writes_by_step"]
            == completed_resume_state["native_writes_by_step"]
        )

        deliverables_poison_path = tmp_path / "restart-after-poisoned-deliverables.json"
        deliverables_poison_state = copy.deepcopy(final_state)
        deliverables_poison_state["run"].update(
            status="RUNNING",
            lease_owner_id=SECOND_WORKER_ID,
            lease_fencing_token=7,
            lease_expires_at="2026-07-15T12:12:00Z",
            last_heartbeat_at="2026-07-15T12:11:00Z",
            finished_at=None,
        )
        _poison_bound_native_content(
            deliverables_poison_state,
            StepName.DELIVERABLES,
        )
        _write_reopenable_state(deliverables_poison_path, deliverables_poison_state)
        deliverables_poison_validations: list[StepName] = []
        deliverables_poison_trace = _TypedResumeTrace()
        deliverables_poison_connections = _ReopenableConnectionFactory(
            path=deliverables_poison_path,
            process_id="poisoned-deliverables-resumer",
            forbidden_connection_ids=worker_a_connection_ids,
        )
        deliverables_poison_runtime = builder(
            tenant_id=TENANT_ID,
            run_data={**deliverables_poison_state["run"]},
            lease_config=_LeaseConfigProbe(),
            audit_sink=_ReopenableAuditSink(),
            connection_factory=deliverables_poison_connections,
            provider_context_factory=_claimed_runtime_provider_context_factory(
                validation_calls=deliverables_poison_validations,
                resume_trace=deliverables_poison_trace,
                crash_after_step=None,
            ),
        )
        _install_contract_resume_spies(
            monkeypatch,
            deliverables_poison_runtime.native_output_contracts,
        )
        poison_error: Exception | None = None
        poison_result: Any = None
        try:
            poison_result = _execute_with_consumer_trace(
                deliverables_poison_runtime,
                deliverables_poison_trace,
            )
        except Exception as exc:
            poison_error = exc
        assert deliverables_poison_validations == full_step_list
        assert deliverables_poison_trace.load_attempts == full_step_list
        assert deliverables_poison_trace.load_successes == full_step_list[:-1]
        assert deliverables_poison_trace.summary_calls == full_step_list[:-1]
        assert deliverables_poison_trace.rehydrate_calls == full_step_list[:-1]
        assert (
            list(dict.fromkeys(step for step, _table in deliverables_poison_trace.native_reads))
            == full_step_list
        )
        assert deliverables_poison_trace.consumer_inputs == {}
        if poison_error is not None:
            assert getattr(poison_error, "code", None) == "STEP_REHYDRATION_FAILED"
        else:
            assert poison_result.status == "FAILED"
            assert poison_result.error_code == "STEP_REHYDRATION_FAILED"
        poison_after = _read_reopenable_state(deliverables_poison_path)
        assert poison_after["native_writes"] == deliverables_poison_state["native_writes"]
        assert (
            poison_after["native_writes_by_step"]
            == deliverables_poison_state["native_writes_by_step"]
        )
        assert poison_after["steps"] == deliverables_poison_state["steps"]

    oracle_path = tmp_path / f"uninterrupted-{cut_index}.json"
    _write_reopenable_state(
        oracle_path,
        _initial_reopenable_claimed_state(owner_id=SECOND_WORKER_ID, token=3),
    )
    oracle_validations: list[StepName] = []
    oracle_resume_trace = _TypedResumeTrace()
    oracle_connections = _ReopenableConnectionFactory(
        path=oracle_path,
        process_id="oracle",
        forbidden_connection_ids=(
            worker_a_connection_ids
            | {connection.connection_id for connection in worker_b_connections.connections}
        ),
    )
    oracle_runtime = builder(
        tenant_id=TENANT_ID,
        run_data={**_read_reopenable_state(oracle_path)["run"]},
        lease_config=_LeaseConfigProbe(),
        audit_sink=_ReopenableAuditSink(),
        connection_factory=oracle_connections,
        provider_context_factory=_claimed_runtime_provider_context_factory(
            validation_calls=oracle_validations,
            resume_trace=oracle_resume_trace,
            crash_after_step=None,
        ),
    )
    _install_contract_resume_spies(monkeypatch, oracle_runtime.native_output_contracts)
    oracle_result = _execute_with_consumer_trace(oracle_runtime, oracle_resume_trace)
    assert oracle_result.status == "SUCCEEDED"
    assert oracle_validations == []
    assert oracle_resume_trace.load_attempts == []
    assert oracle_resume_trace.rehydrate_calls == []
    assert list(oracle_resume_trace.consumer_inputs) == list(STRICT_V2_STEPS)
    for consumed_step in STRICT_V2_STEPS[: cut_index + 1]:
        assert (
            worker_a_resume_trace.consumer_inputs[consumed_step]
            == (oracle_resume_trace.consumer_inputs[consumed_step])
        )
    for consumed_step in resumed_suffix:
        assert (
            worker_b_resume_trace.consumer_inputs[consumed_step]
            == (oracle_resume_trace.consumer_inputs[consumed_step])
        ), (
            f"{consumed_step.value} received different business context/accumulated input "
            "after restart than during uninterrupted execution"
        )
    assert all(connection.closed for connection in oracle_connections.connections)
    assert resumed_context_snapshot == _context_resume_snapshot(oracle_runtime.context), (
        "resumed RunContext diverges semantically from uninterrupted execution; "
        "rehydration cannot satisfy the contract by copying output data into an unused marker"
    )
    oracle_state = _read_reopenable_state(oracle_path)
    assert _step_semantics(final_state) == _step_semantics(oracle_state)
    assert _native_write_semantics(final_state) == _native_write_semantics(oracle_state)
    assert final_state["native_writes_by_step"] == oracle_state["native_writes_by_step"]
