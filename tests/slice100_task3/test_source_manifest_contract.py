"""Task 3 RED tests for the immutable two-company source contract."""

from __future__ import annotations

import json
import re
from dataclasses import is_dataclass
from pathlib import Path

import pytest

from tests.slice100_task3._support import build_source_fixture, contract_kwargs


def _module():
    from idis.evaluation import real_example_source_package

    return real_example_source_package


def _assert_code(exc: pytest.ExceptionInfo[Exception], expected: str) -> None:
    assert getattr(exc.value, "code", None) == expected


def test_builds_two_sorted_isolated_company_contracts_without_private_names(
    tmp_path: Path,
) -> None:
    fixture = build_source_fixture(tmp_path)
    module = _module()

    contract = module.build_two_company_source_contract(**contract_kwargs(fixture))

    assert [company.company_alias for company in contract.companies] == [
        "company-001",
        "company-002",
    ]
    assert [company.deal_alias for company in contract.companies] == [
        "deal-001",
        "deal-002",
    ]
    assert [company.document_set_alias for company in contract.companies] == [
        "documents-001",
        "documents-002",
    ]
    assert [company.run_alias for company in contract.companies] == [
        "run-001",
        "run-002",
    ]
    assert [company.ledger_alias for company in contract.companies] == [
        "ledger-001",
        "ledger-002",
    ]
    assert [company.package_alias for company in contract.companies] == [
        "package-001",
        "package-002",
    ]
    assert [file.sequence for file in contract.companies[0].files] == [0, 1]
    assert [file.sequence for file in contract.companies[1].files] == [0, 1]
    assert all(
        re.fullmatch(r"company-00[12]-document-\d{5}\.[a-z0-9]{1,12}", file.generated_upload_name)
        for company in contract.companies
        for file in company.files
    )
    assert (
        len({file.content_sha256 for company in contract.companies for file in company.files})
        == fixture.expected_total_files
    )
    assert len(contract.source_tree_fingerprint) == 64

    serialized = json.dumps(contract.safe_summary(), sort_keys=True)
    assert not is_dataclass(contract)
    assert "private" not in repr(contract).lower()
    for private_token in fixture.private_tokens:
        assert private_token not in serialized


@pytest.mark.parametrize("top_level_directories", [1, 3])
def test_rejects_any_top_level_directory_count_other_than_two_before_http(
    tmp_path: Path,
    top_level_directories: int,
) -> None:
    fixture = build_source_fixture(tmp_path)
    if top_level_directories == 1:
        second = sorted(fixture.root.iterdir())[1]
        for child in sorted(second.rglob("*"), reverse=True):
            child.unlink() if child.is_file() else child.rmdir()
        second.rmdir()
    else:
        (fixture.root / "third-private-company").mkdir()
    module = _module()

    with pytest.raises(module.SourcePackageContractError) as exc:
        module.build_two_company_source_contract(**contract_kwargs(fixture))

    _assert_code(exc, "DATA_ROOM_INVENTORY_PACKAGE_FAILED")


def test_rejects_root_level_file(tmp_path: Path) -> None:
    fixture = build_source_fixture(tmp_path)
    (fixture.root / "private-root-note.txt").write_bytes(b"not a company")
    module = _module()

    with pytest.raises(module.SourcePackageContractError) as exc:
        module.build_two_company_source_contract(**contract_kwargs(fixture))

    _assert_code(exc, "DATA_ROOM_INVENTORY_PACKAGE_FAILED")


@pytest.mark.parametrize(
    ("override", "value"),
    [
        ("expected_file_counts", (1, 2)),
        ("expected_total_files", 3),
        ("expected_company_bytes", (1, 2)),
        ("expected_total_bytes", 1),
    ],
)
def test_rejects_count_or_byte_drift_before_http(
    tmp_path: Path,
    override: str,
    value: object,
) -> None:
    fixture = build_source_fixture(tmp_path)
    kwargs = contract_kwargs(fixture)
    kwargs[override] = value
    module = _module()

    with pytest.raises(module.SourcePackageContractError) as exc:
        module.build_two_company_source_contract(**kwargs)

    _assert_code(exc, "DATA_ROOM_RECONCILIATION_INCOMPLETE")


def test_rejects_duplicate_content_hash_instead_of_collapsing_rows(
    tmp_path: Path,
) -> None:
    fixture = build_source_fixture(
        tmp_path,
        files_by_sorted_company=(
            (("private-a.pdf", b"duplicate"),),
            (("private-b.pdf", b"duplicate"),),
        ),
    )
    module = _module()

    with pytest.raises(module.SourcePackageContractError) as exc:
        module.build_two_company_source_contract(**contract_kwargs(fixture))

    _assert_code(exc, "DATA_ROOM_INVENTORY_PACKAGE_FAILED")


def test_rejects_missing_content_hash(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    fixture = build_source_fixture(tmp_path)
    module = _module()
    monkeypatch.setattr(module, "_hash_source_file", lambda _path: "")

    with pytest.raises(module.SourcePackageContractError) as exc:
        module.build_two_company_source_contract(**contract_kwargs(fixture))

    _assert_code(exc, "DATA_ROOM_INVENTORY_PACKAGE_FAILED")


def test_fingerprinting_is_read_only_and_does_not_change_source_metadata(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fixture = build_source_fixture(tmp_path)
    module = _module()
    before = {
        path: (path.read_bytes(), path.stat().st_size, path.stat().st_mtime_ns)
        for path in fixture.root.rglob("*")
        if path.is_file()
    }
    real_open = Path.open
    source_open_modes: list[str] = []

    def tracked_open(path: Path, mode: str = "r", *args: object, **kwargs: object):
        if path.is_relative_to(fixture.root):
            source_open_modes.append(mode)
        return real_open(path, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "open", tracked_open)

    module.build_two_company_source_contract(**contract_kwargs(fixture))

    after = {
        path: (path.read_bytes(), path.stat().st_size, path.stat().st_mtime_ns)
        for path in fixture.root.rglob("*")
        if path.is_file()
    }
    assert before == after
    assert source_open_modes
    assert set(source_open_modes) == {"rb"}


def test_source_contract_is_exposed_through_existing_real_example_gate() -> None:
    from idis.evaluation import real_example_gate

    assert real_example_gate.build_two_company_source_contract is not None


def test_fingerprint_binds_private_top_level_folder_identity(tmp_path: Path) -> None:
    fixture = build_source_fixture(tmp_path)
    module = _module()
    before = module.build_two_company_source_contract(**contract_kwargs(fixture))
    first_company = sorted(fixture.root.iterdir())[0]
    first_company.rename(fixture.root / "renamed-private-target")

    after = module.build_two_company_source_contract(**contract_kwargs(fixture))

    assert after.source_tree_fingerprint != before.source_tree_fingerprint
    assert (
        after.companies[0].source_company_locator_sha256
        != before.companies[0].source_company_locator_sha256
    )


def test_fingerprint_binds_exact_resolved_source_root(tmp_path: Path) -> None:
    first_parent = tmp_path / "first-private-location"
    second_parent = tmp_path / "second-private-location"
    first_parent.mkdir()
    second_parent.mkdir()
    first = build_source_fixture(first_parent)
    second = build_source_fixture(second_parent)
    module = _module()

    first_contract = module.build_two_company_source_contract(**contract_kwargs(first))
    second_contract = module.build_two_company_source_contract(**contract_kwargs(second))

    assert first_contract.source_tree_fingerprint != second_contract.source_tree_fingerprint
    assert first_contract.source_root_locator_sha256 != second_contract.source_root_locator_sha256
