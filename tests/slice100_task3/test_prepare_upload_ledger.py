"""Task 3 RED tests for isolated preparation and the fail-closed resume ledger."""

from __future__ import annotations

import copy
import json
import multiprocessing
import os
from pathlib import Path
from typing import Any

import pytest

from tests.slice100_task3._support import (
    FakeApiState,
    FakePreparationApi,
    build_source_fixture,
    contract_kwargs,
)


def _module():
    from idis.evaluation import real_example_source_package

    return real_example_source_package


def _prepare(
    fixture: Any,
    api: FakePreparationApi,
    ledger_path: Path,
) -> dict[str, Any]:
    return _module().prepare_two_company_source_packages(
        deal_stage="SERIES_B",
        api_client=api,
        api_key="test-secret-that-must-never-be-serialized",
        ledger_path=ledger_path,
        **contract_kwargs(fixture),
    )


def _assert_code(exc: pytest.ExceptionInfo[Exception], expected: str) -> None:
    assert getattr(exc.value, "code", None) == expected


def _posts(api: FakePreparationApi, suffix: str) -> list[dict[str, Any]]:
    return [call for call in api.calls if call["method"] == "POST" and call["url"].endswith(suffix)]


def _ledger_events(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _hold_ledger_lock(
    ledger_path: str,
    source_root: str,
    acquired: Any,
    release: Any,
) -> None:
    module = _module()
    with module._exclusive_ledger_lock(Path(ledger_path), Path(source_root)):
        acquired.set()
        if not release.wait(10):
            raise RuntimeError("the lock holder was not released by the test")


def _contend_for_ledger_lock(ledger_path: str, source_root: str, result: Any) -> None:
    module = _module()
    module.LEDGER_LOCK_TIMEOUT_SECONDS = 0.5
    module.LEDGER_LOCK_RETRY_SECONDS = 0.01
    try:
        with module._exclusive_ledger_lock(Path(ledger_path), Path(source_root)):
            result.put("acquired")
    except TimeoutError:
        result.put("timed-out")


def test_prepares_exactly_two_deals_and_two_isolated_packages(tmp_path: Path) -> None:
    fixture = build_source_fixture(tmp_path)
    ledger_path = tmp_path / "private-checkpoints" / "prepare-ledger.json"
    api = FakePreparationApi()

    result = _prepare(fixture, api, ledger_path)

    deal_posts = [
        call for call in api.calls if call["method"] == "POST" and call["url"] == "/v1/deals"
    ]
    upload_posts = _posts(api, "/documents/upload")
    package_posts = _posts(api, "/data-room-packages")
    assert len(deal_posts) == 2
    assert len(upload_posts) == fixture.expected_total_files
    assert len(package_posts) == 2
    assert len({row["deal_id"] for row in result["companies"]}) == 2
    assert len({row["package_id"] for row in result["companies"]}) == 2
    assert result["total_file_count"] == fixture.expected_total_files
    assert result["total_bytes"] == fixture.expected_total_bytes
    assert all("/runs" not in call["url"] for call in api.calls)

    for company in result["companies"]:
        documents = [
            row for row in api.state.documents.values() if row["deal_id"] == company["deal_id"]
        ]
        package = api.state.packages[company["package_id"]]
        assert len(documents) == company["file_count"]
        assert package["deal_id"] == company["deal_id"]
        assert {row["document_id"] for row in documents} == {
            row["document_id"] for row in package["files"]
        }


def test_private_source_names_never_enter_summary_ledger_or_request_metadata(
    tmp_path: Path,
) -> None:
    fixture = build_source_fixture(tmp_path)
    ledger_path = tmp_path / "private-checkpoints" / "prepare-ledger.json"
    api = FakePreparationApi()

    result = _prepare(fixture, api, ledger_path)

    observable = json.dumps(
        {
            "result": result,
            "ledger": _ledger_events(ledger_path),
            "calls": api.calls,
        },
        sort_keys=True,
    )
    for private_token in fixture.private_tokens:
        assert private_token not in observable
    assert "test-secret-that-must-never-be-serialized" not in observable


def test_full_preparation_never_changes_source_bytes_or_metadata(tmp_path: Path) -> None:
    fixture = build_source_fixture(tmp_path)
    before = {
        path: (path.read_bytes(), path.stat().st_size, path.stat().st_mtime_ns)
        for path in fixture.root.rglob("*")
        if path.is_file()
    }

    _prepare(fixture, FakePreparationApi(), tmp_path / "ledger.jsonl")

    after = {
        path: (path.read_bytes(), path.stat().st_size, path.stat().st_mtime_ns)
        for path in fixture.root.rglob("*")
        if path.is_file()
    }
    assert before == after


def test_same_deal_for_both_companies_fails_before_any_upload_or_package(
    tmp_path: Path,
) -> None:
    fixture = build_source_fixture(tmp_path)
    api = FakePreparationApi(same_deal_for_both=True)

    with pytest.raises(_module().SourcePackageContractError) as exc:
        _prepare(fixture, api, tmp_path / "ledger.json")

    _assert_code(exc, "DATA_ROOM_RECONCILIATION_INCOMPLETE")
    assert not _posts(api, "/documents/upload")
    assert not _posts(api, "/data-room-packages")


def test_deal_creation_receipt_must_match_safe_aliases(tmp_path: Path) -> None:
    fixture = build_source_fixture(tmp_path)
    api = FakePreparationApi(deal_alias_mismatch=True)

    with pytest.raises(_module().SourcePackageContractError) as exc:
        _prepare(fixture, api, tmp_path / "ledger.jsonl")

    _assert_code(exc, "DATA_ROOM_DURABLE_HANDOFF_INCOMPLETE")
    assert not _posts(api, "/documents/upload")


def test_resumed_deal_alias_is_reconciled_with_durable_api(tmp_path: Path) -> None:
    fixture = build_source_fixture(tmp_path)
    ledger_path = tmp_path / "ledger.jsonl"
    initial = FakePreparationApi()
    _prepare(fixture, initial, ledger_path)
    first_deal = next(iter(initial.state.deals.values()))
    first_deal["name"] = "deal-999"
    resumed = FakePreparationApi(state=initial.state)

    with pytest.raises(_module().SourcePackageContractError) as exc:
        _prepare(fixture, resumed, ledger_path)

    _assert_code(exc, "DATA_ROOM_DURABLE_HANDOFF_INCOMPLETE")


def test_cross_deal_document_response_is_rejected(tmp_path: Path) -> None:
    fixture = build_source_fixture(tmp_path)
    api = FakePreparationApi(reuse_first_document_cross_company=True)

    with pytest.raises(_module().SourcePackageContractError) as exc:
        _prepare(fixture, api, tmp_path / "ledger.json")

    _assert_code(exc, "DATA_ROOM_RECONCILIATION_INCOMPLETE")


def test_cross_deal_artifact_reuse_is_rejected_before_packages(tmp_path: Path) -> None:
    fixture = build_source_fixture(tmp_path)
    api = FakePreparationApi(reuse_first_artifact_cross_company=True)

    with pytest.raises(_module().SourcePackageContractError) as exc:
        _prepare(fixture, api, tmp_path / "ledger.jsonl")

    _assert_code(exc, "DATA_ROOM_RECONCILIATION_INCOMPLETE")
    assert not _posts(api, "/data-room-packages")


def test_resume_reconciliation_rejects_cross_deal_artifact_reuse(tmp_path: Path) -> None:
    fixture = build_source_fixture(tmp_path)
    module = _module()
    contract = module.build_two_company_source_contract(**contract_kwargs(fixture))
    ledger = module._new_prepare_ledger(contract)
    shared_doc_id = "00000000-0000-4000-8000-000000000003"
    for company_index, (company, state) in enumerate(
        zip(contract.companies, ledger["companies"], strict=True),
        start=1,
    ):
        state["deal_id"] = f"00000000-0000-4000-8000-00000000000{company_index}"
        state["uploads"].append(
            {
                **company.files[0].safe_dict(),
                "document_id": f"00000000-0000-4000-8000-00000000001{company_index}",
                "doc_id": shared_doc_id,
            }
        )
    module._seal_prepare_ledger(ledger)

    with pytest.raises(module.SourcePackageContractError) as exc:
        module._reconcile_ledger_with_contract(ledger, contract)

    _assert_code(exc, "DATA_ROOM_RECONCILIATION_INCOMPLETE")


@pytest.mark.parametrize("api_option", ["upload_hash_missing", "upload_hash_mismatch"])
def test_upload_receipt_requires_the_exact_source_hash(
    tmp_path: Path,
    api_option: str,
) -> None:
    fixture = build_source_fixture(tmp_path)
    api = FakePreparationApi(**{api_option: True})

    with pytest.raises(_module().SourcePackageContractError) as exc:
        _prepare(fixture, api, tmp_path / "ledger.json")

    _assert_code(exc, "SHA256_MISMATCH")


@pytest.mark.parametrize(
    "api_option",
    ["package_deal_mismatch", "package_hash_mismatch", "package_alias_mismatch"],
)
def test_package_receipt_must_match_exact_deal_and_manifest(
    tmp_path: Path,
    api_option: str,
) -> None:
    fixture = build_source_fixture(tmp_path)
    api = FakePreparationApi(**{api_option: True})

    with pytest.raises(_module().SourcePackageContractError) as exc:
        _prepare(fixture, api, tmp_path / "ledger.json")

    _assert_code(exc, "DATA_ROOM_DURABLE_HANDOFF_INCOMPLETE")


def test_two_companies_cannot_share_one_package_id(tmp_path: Path) -> None:
    fixture = build_source_fixture(tmp_path)
    api = FakePreparationApi(same_package_for_both=True)

    with pytest.raises(_module().SourcePackageContractError) as exc:
        _prepare(fixture, api, tmp_path / "ledger.jsonl")

    _assert_code(exc, "DATA_ROOM_RECONCILIATION_INCOMPLETE")


def test_ledger_path_cannot_be_inside_the_read_only_source(tmp_path: Path) -> None:
    fixture = build_source_fixture(tmp_path)
    api = FakePreparationApi()

    with pytest.raises(_module().SourcePackageContractError) as exc:
        _prepare(fixture, api, fixture.root / "forbidden-ledger.json")

    _assert_code(exc, "DATA_ROOM_INVENTORY_PACKAGE_FAILED")
    assert api.calls == []


@pytest.mark.parametrize(
    "corrupt_payload",
    [
        "{",
        "[]",
        json.dumps({"schema_version": "unknown", "ledger_sha256": "0" * 64}),
        json.dumps(
            {
                "schema_version": "slice100-source-preparation-ledger-v1",
                "companies": [],
                "source_tree_fingerprint": "0" * 64,
                "ledger_sha256": "0" * 64,
                "unknown": "must fail closed",
            }
        ),
    ],
)
def test_corrupt_or_unknown_resume_ledger_fails_before_api_mutation(
    tmp_path: Path,
    corrupt_payload: str,
) -> None:
    fixture = build_source_fixture(tmp_path)
    ledger_path = tmp_path / "ledger.json"
    ledger_path.write_text(corrupt_payload, encoding="utf-8")
    api = FakePreparationApi()

    with pytest.raises(_module().SourcePackageContractError) as exc:
        _prepare(fixture, api, ledger_path)

    _assert_code(exc, "DATA_ROOM_RECONCILIATION_INCOMPLETE")
    assert api.calls == []


def test_tampered_valid_ledger_hash_fails_before_api_mutation(tmp_path: Path) -> None:
    fixture = build_source_fixture(tmp_path)
    ledger_path = tmp_path / "ledger.json"
    initial_api = FakePreparationApi()
    _prepare(fixture, initial_api, ledger_path)
    events = _ledger_events(ledger_path)
    events[-1]["companies"][0]["deal_id"] = events[-1]["companies"][1]["deal_id"]
    ledger_path.write_text(
        "".join(f"{json.dumps(event, sort_keys=True)}\n" for event in events),
        encoding="utf-8",
    )
    resumed_api = FakePreparationApi(state=initial_api.state)

    with pytest.raises(_module().SourcePackageContractError) as exc:
        _prepare(fixture, resumed_api, ledger_path)

    _assert_code(exc, "DATA_ROOM_RECONCILIATION_INCOMPLETE")
    assert resumed_api.calls == []


def test_prepare_ledger_is_append_only_jsonl_with_a_complete_hash_chain(
    tmp_path: Path,
) -> None:
    fixture = build_source_fixture(tmp_path)
    ledger_path = tmp_path / "ledger.jsonl"

    _prepare(fixture, FakePreparationApi(), ledger_path)

    events = _ledger_events(ledger_path)
    assert len(events) > fixture.expected_total_files
    assert [event["ledger_sequence"] for event in events] == list(range(len(events)))
    assert events[0]["previous_ledger_sha256"] is None
    assert all(
        event["previous_ledger_sha256"] == events[index - 1]["ledger_sha256"]
        for index, event in enumerate(events[1:], start=1)
    )


def test_rehashed_historical_ledger_edit_breaks_the_append_only_chain(
    tmp_path: Path,
) -> None:
    fixture = build_source_fixture(tmp_path)
    ledger_path = tmp_path / "ledger.jsonl"
    initial = FakePreparationApi()
    _prepare(fixture, initial, ledger_path)
    events = _ledger_events(ledger_path)
    historical = events[-2]
    historical["total_bytes"] += 1
    unhashed = dict(historical)
    unhashed.pop("ledger_sha256")
    historical["ledger_sha256"] = _module()._canonical_sha256(unhashed)
    ledger_path.write_text(
        "".join(f"{json.dumps(event, sort_keys=True)}\n" for event in events),
        encoding="utf-8",
    )
    resumed = FakePreparationApi(state=initial.state)

    with pytest.raises(_module().SourcePackageContractError) as exc:
        _prepare(fixture, resumed, ledger_path)

    _assert_code(exc, "DATA_ROOM_RECONCILIATION_INCOMPLETE")
    assert resumed.calls == []


def test_writer_rejects_duplicate_tail_created_by_a_competing_writer(
    tmp_path: Path,
) -> None:
    fixture = build_source_fixture(tmp_path)
    ledger_path = tmp_path / "ledger.jsonl"
    module = _module()
    contract = module.build_two_company_source_contract(**contract_kwargs(fixture))
    ledger = module._load_or_initialize_ledger(ledger_path, contract)
    last_line = ledger_path.read_bytes().splitlines(keepends=True)[-1]
    with ledger_path.open("ab") as stream:
        stream.write(last_line)

    with pytest.raises(module.SourcePackageContractError) as exc:
        module._save_prepare_ledger(ledger_path, ledger, contract.root)

    _assert_code(exc, "DATA_ROOM_DURABLE_HANDOFF_INCOMPLETE")


def test_exclusive_ledger_lock_blocks_a_competing_process(tmp_path: Path) -> None:
    source_root = tmp_path / "source"
    source_root.mkdir()
    ledger_path = tmp_path / "ledger.jsonl"
    context = multiprocessing.get_context("spawn")
    acquired = context.Event()
    release = context.Event()
    result = context.Queue()
    holder = context.Process(
        target=_hold_ledger_lock,
        args=(str(ledger_path), str(source_root), acquired, release),
    )
    contender = context.Process(
        target=_contend_for_ledger_lock,
        args=(str(ledger_path), str(source_root), result),
    )

    holder.start()
    try:
        assert acquired.wait(10)
        contender.start()
        contender.join(10)
        assert not contender.is_alive()
        assert contender.exitcode == 0
        assert result.get(timeout=2) == "timed-out"
    finally:
        release.set()
        holder.join(10)
        if holder.is_alive():
            holder.terminate()
            holder.join(5)
        if contender.is_alive():
            contender.terminate()
            contender.join(5)
        result.close()
        result.join_thread()

    assert holder.exitcode == 0
    with _module()._exclusive_ledger_lock(ledger_path, source_root):
        pass


def test_initialization_lock_failure_uses_durable_handoff_code(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = build_source_fixture(tmp_path)
    ledger_path = tmp_path / "ledger.jsonl"
    module = _module()
    contract = module.build_two_company_source_contract(**contract_kwargs(fixture))

    def _unavailable_lock(_file_descriptor: int) -> None:
        raise OSError("simulated lock contention")

    monkeypatch.setattr(module, "LEDGER_LOCK_TIMEOUT_SECONDS", 0.0)
    monkeypatch.setattr(module, "_try_acquire_ledger_lock", _unavailable_lock)

    with pytest.raises(module.SourcePackageContractError) as exc:
        module._load_or_initialize_ledger(ledger_path, contract)

    _assert_code(exc, "DATA_ROOM_DURABLE_HANDOFF_INCOMPLETE")


def test_only_one_valid_writer_can_advance_a_shared_ledger_tail(tmp_path: Path) -> None:
    fixture = build_source_fixture(tmp_path)
    ledger_path = tmp_path / "ledger.jsonl"
    module = _module()
    contract = module.build_two_company_source_contract(**contract_kwargs(fixture))
    first_writer = module._load_or_initialize_ledger(ledger_path, contract)
    second_writer = copy.deepcopy(first_writer)
    deal_id = "00000000-0000-4000-8000-000000000001"
    first_writer["companies"][0]["deal_id"] = deal_id
    second_writer["companies"][0]["deal_id"] = deal_id

    module._save_prepare_ledger(ledger_path, first_writer, contract.root)
    with pytest.raises(module.SourcePackageContractError) as exc:
        module._save_prepare_ledger(ledger_path, second_writer, contract.root)

    _assert_code(exc, "DATA_ROOM_DURABLE_HANDOFF_INCOMPLETE")
    events = _ledger_events(ledger_path)
    assert [event["ledger_sequence"] for event in events] == [0, 1]
    assert events[1]["previous_ledger_sha256"] == events[0]["ledger_sha256"]


def test_resume_reconciles_completed_rows_with_durable_api_and_skips_them(
    tmp_path: Path,
) -> None:
    fixture = build_source_fixture(tmp_path)
    ledger_path = tmp_path / "ledger.json"
    state = FakeApiState()
    interrupted = FakePreparationApi(state=state, fail_after_successful_uploads=1)

    with pytest.raises(_module().SourcePackageContractError):
        _prepare(fixture, interrupted, ledger_path)

    resumed = FakePreparationApi(state=state)
    result = _prepare(fixture, resumed, ledger_path)

    assert result["total_file_count"] == fixture.expected_total_files
    assert len(_posts(resumed, "/documents/upload")) == fixture.expected_total_files - 1
    assert any(call["method"] == "GET" and "/documents/" in call["url"] for call in resumed.calls)


def test_disconnect_after_server_commit_replays_with_deterministic_idempotency(
    tmp_path: Path,
) -> None:
    fixture = build_source_fixture(tmp_path)
    ledger_path = tmp_path / "ledger.json"
    state = FakeApiState()
    interrupted = FakePreparationApi(state=state, crash_after_committed_uploads=1)

    with pytest.raises(_module().SourcePackageContractError):
        _prepare(fixture, interrupted, ledger_path)

    committed_document_ids = set(state.documents)
    resumed = FakePreparationApi(state=state)
    result = _prepare(fixture, resumed, ledger_path)

    assert result["total_file_count"] == fixture.expected_total_files
    assert committed_document_ids <= set(state.documents)
    assert len(state.documents) == fixture.expected_total_files
    upload_headers = [call["header_names"] for call in _posts(resumed, "/documents/upload")]
    assert upload_headers
    assert all("Idempotency-Key" in names for names in upload_headers)
    first_interrupted_upload = _posts(interrupted, "/documents/upload")[0]
    replayed_upload = _posts(resumed, "/documents/upload")[0]
    assert (
        first_interrupted_upload["idempotency_key_sha256"]
        == replayed_upload["idempotency_key_sha256"]
    )


def test_disconnect_after_deal_commit_replays_same_idempotent_deal(tmp_path: Path) -> None:
    fixture = build_source_fixture(tmp_path)
    ledger_path = tmp_path / "ledger.jsonl"
    state = FakeApiState()
    interrupted = FakePreparationApi(state=state, crash_after_committed_deals=1)

    with pytest.raises(_module().SourcePackageContractError):
        _prepare(fixture, interrupted, ledger_path)

    committed_deal_ids = set(state.deals)
    resumed = FakePreparationApi(state=state)
    result = _prepare(fixture, resumed, ledger_path)

    assert result["total_file_count"] == fixture.expected_total_files
    assert committed_deal_ids <= set(state.deals)
    assert len(state.deals) == 2
    interrupted_key = next(
        call["idempotency_key_sha256"]
        for call in interrupted.calls
        if call["method"] == "POST" and call["url"] == "/v1/deals"
    )
    resumed_key = next(
        call["idempotency_key_sha256"]
        for call in resumed.calls
        if call["method"] == "POST" and call["url"] == "/v1/deals"
    )
    assert interrupted_key == resumed_key


def test_disconnect_after_package_commit_replays_same_idempotent_package(
    tmp_path: Path,
) -> None:
    fixture = build_source_fixture(tmp_path)
    ledger_path = tmp_path / "ledger.jsonl"
    state = FakeApiState()
    interrupted = FakePreparationApi(state=state, crash_after_committed_packages=1)

    with pytest.raises(_module().SourcePackageContractError):
        _prepare(fixture, interrupted, ledger_path)

    committed_package_ids = set(state.packages)
    resumed = FakePreparationApi(state=state)
    result = _prepare(fixture, resumed, ledger_path)

    assert result["total_file_count"] == fixture.expected_total_files
    assert committed_package_ids <= set(state.packages)
    assert len(state.packages) == 2
    interrupted_key = _posts(interrupted, "/data-room-packages")[0]["idempotency_key_sha256"]
    resumed_key = _posts(resumed, "/data-room-packages")[0]["idempotency_key_sha256"]
    assert interrupted_key == resumed_key


def test_same_size_same_mtime_source_mutation_is_detected(tmp_path: Path) -> None:
    fixture = build_source_fixture(tmp_path)
    target = fixture.first_source_file

    def mutate_source() -> None:
        before = target.stat()
        original = target.read_bytes()
        replacement = bytes([original[0] ^ 1]) + original[1:]
        target.write_bytes(replacement)
        os.utime(target, ns=(before.st_atime_ns, before.st_mtime_ns))

    api = FakePreparationApi(mutate_after_first_upload=mutate_source)

    with pytest.raises(_module().SourcePackageContractError) as exc:
        _prepare(fixture, api, tmp_path / "ledger.json")

    _assert_code(exc, "SOURCE_TREE_MUTATED")


@pytest.mark.parametrize("mutation", ["add", "delete", "rename"])
def test_source_tree_membership_mutation_is_detected(
    tmp_path: Path,
    mutation: str,
) -> None:
    fixture = build_source_fixture(tmp_path)
    target = fixture.first_source_file

    def mutate_source() -> None:
        if mutation == "add":
            (target.parent / "new-private-file.pdf").write_bytes(b"new private content")
        elif mutation == "delete":
            target.unlink()
        else:
            target.rename(target.with_name("renamed-private-file.pdf"))

    api = FakePreparationApi(mutate_after_first_upload=mutate_source)

    with pytest.raises(_module().SourcePackageContractError) as exc:
        _prepare(fixture, api, tmp_path / "ledger.json")

    _assert_code(exc, "SOURCE_TREE_MUTATED")


def test_count_drift_fails_before_any_api_call(tmp_path: Path) -> None:
    fixture = build_source_fixture(tmp_path)
    api = FakePreparationApi()
    kwargs = contract_kwargs(fixture)
    kwargs["expected_total_files"] = fixture.expected_total_files + 1

    with pytest.raises(_module().SourcePackageContractError) as exc:
        _module().prepare_two_company_source_packages(
            deal_stage="SERIES_B",
            api_client=api,
            api_key="test-secret",
            ledger_path=tmp_path / "ledger.json",
            **kwargs,
        )

    _assert_code(exc, "DATA_ROOM_RECONCILIATION_INCOMPLETE")
    assert api.calls == []
