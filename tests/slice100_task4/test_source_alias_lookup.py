"""RED contract for the approved local-only source-alias lookup.

All paths and content in this module are fictional pytest fixtures.  The real
``real_example`` tree and acceptance ledgers must never be consulted here.
"""

from __future__ import annotations

import importlib
import json
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest

from tests.slice100_task3._support import build_source_fixture, contract_kwargs


def _lookup_module() -> Any:
    return importlib.import_module("idis.tools.real_example_source_lookup")


def _source_module() -> Any:
    return importlib.import_module("idis.evaluation.real_example_source_package")


def _bound_fixture(tmp_path: Path) -> tuple[Any, Any, Path]:
    fixture = build_source_fixture(tmp_path)
    source = _source_module()
    contract = source.build_two_company_source_contract(**contract_kwargs(fixture))
    ledger_path = tmp_path / ".local_reports" / "slice100" / "acceptance-999"
    ledger_path = ledger_path / "prepare-ledger.jsonl"
    ledger = source._new_prepare_ledger(contract)  # noqa: SLF001 - canonical test fixture
    source._append_prepare_ledger_line(  # noqa: SLF001 - canonical test fixture
        ledger_path,
        ledger,
        contract.root,
    )
    return fixture, contract, ledger_path


def _contract_builder(contract: Any) -> Callable[[Path], Any]:
    def build(root: Path) -> Any:
        assert root == contract.root
        return contract

    return build


def _observable_exception(exc: BaseException, captured: pytest.CaptureResult[str]) -> str:
    return f"{type(exc).__name__}|{exc!s}|{exc!r}|{captured.out}|{captured.err}"


def _assert_no_private_tokens(value: str, tokens: tuple[str, ...]) -> None:
    for token in tokens:
        assert token not in value


def test_lookup_module_and_path_free_public_contract_exist() -> None:
    module = _lookup_module()

    assert callable(module.resolve_and_write_source_alias)
    assert callable(module.main)
    assert isinstance(module.SOURCE_LOOKUP_TTY_REQUIRED, str)


def test_bound_alias_writes_one_private_path_only_to_terminal_callback(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    fixture, contract, ledger_path = _bound_fixture(tmp_path)
    source_file = contract.companies[0].files[0]
    expected_path = contract.source_path("company-001", source_file.sequence)
    terminal_values: list[Path] = []

    result = _lookup_module().resolve_and_write_source_alias(
        source_root=fixture.root,
        ledger_path=ledger_path,
        generated_upload_name=source_file.generated_upload_name,
        terminal_writer=terminal_values.append,
        contract_builder=_contract_builder(contract),
    )

    assert result is None
    assert terminal_values == [expected_path]
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


def test_lookup_accepts_a_contract_bound_row_not_yet_present_in_upload_tail(
    tmp_path: Path,
) -> None:
    fixture, contract, ledger_path = _bound_fixture(tmp_path)
    source_file = contract.companies[1].files[-1]
    terminal_values: list[Path] = []

    _lookup_module().resolve_and_write_source_alias(
        source_root=fixture.root,
        ledger_path=ledger_path,
        generated_upload_name=source_file.generated_upload_name,
        terminal_writer=terminal_values.append,
        contract_builder=_contract_builder(contract),
    )

    assert terminal_values == [contract.source_path("company-002", source_file.sequence)]


def test_lookup_rejects_a_validly_resealed_ledger_bound_to_another_tree_without_output(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    lookup = _lookup_module()
    fixture, contract, ledger_path = _bound_fixture(tmp_path)
    source = _source_module()
    event = json.loads(ledger_path.read_text(encoding="utf-8"))
    event["source_tree_fingerprint"] = "0" * 64
    source._seal_prepare_ledger(event)  # noqa: SLF001 - intentional valid re-seal
    ledger_path.write_text(
        json.dumps(event, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    terminal_values: list[Path] = []

    with pytest.raises(Exception) as exc_info:
        lookup.resolve_and_write_source_alias(
            source_root=fixture.root,
            ledger_path=ledger_path,
            generated_upload_name=contract.companies[0].files[0].generated_upload_name,
            terminal_writer=terminal_values.append,
            contract_builder=_contract_builder(contract),
        )

    assert terminal_values == []
    observable = _observable_exception(exc_info.value, capsys.readouterr())
    _assert_no_private_tokens(observable, fixture.private_tokens)


@pytest.mark.parametrize(
    "unsafe_alias",
    [
        "../company-001-document-00001.pdf",
        "C:/fictional/company-001-document-00001.pdf",
        "company-001-document-00001.pdf/extra",
        "COMPANY-001-DOCUMENT-00001.PDF",
        "company-003-document-00001.pdf",
        "company-001-document-00000.pdf",
        "company-001-document-00001.pdf\nforged",
    ],
)
def test_lookup_rejects_noncanonical_or_terminal_unsafe_alias_before_output(
    tmp_path: Path,
    unsafe_alias: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    lookup = _lookup_module()
    fixture, contract, ledger_path = _bound_fixture(tmp_path)
    terminal_values: list[Path] = []

    with pytest.raises(Exception) as exc_info:
        lookup.resolve_and_write_source_alias(
            source_root=fixture.root,
            ledger_path=ledger_path,
            generated_upload_name=unsafe_alias,
            terminal_writer=terminal_values.append,
            contract_builder=_contract_builder(contract),
        )

    assert terminal_values == []
    observable = _observable_exception(exc_info.value, capsys.readouterr())
    _assert_no_private_tokens(observable, fixture.private_tokens)


def test_lookup_rehashes_the_source_and_rejects_same_size_mutation(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    lookup = _lookup_module()
    fixture, contract, ledger_path = _bound_fixture(tmp_path)
    source_file = contract.companies[0].files[0]
    private_path = contract.source_path("company-001", source_file.sequence)
    original = private_path.read_bytes()
    private_path.write_bytes(bytes([original[0] ^ 1]) + original[1:])
    terminal_values: list[Path] = []

    with pytest.raises(Exception) as exc_info:
        lookup.resolve_and_write_source_alias(
            source_root=fixture.root,
            ledger_path=ledger_path,
            generated_upload_name=source_file.generated_upload_name,
            terminal_writer=terminal_values.append,
            contract_builder=_contract_builder(contract),
        )

    assert terminal_values == []
    observable = _observable_exception(exc_info.value, capsys.readouterr())
    _assert_no_private_tokens(observable, fixture.private_tokens)


def test_lookup_never_trusts_an_exception_code_as_terminal_safe_output(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    fixture = build_source_fixture(tmp_path)
    lookup = _lookup_module()
    source = _source_module()

    def unsafe_builder(_root: Path) -> Any:
        raise source.SourcePackageContractError(
            str(fixture.first_source_file),
            "fictional unsafe exception code",
        )

    with pytest.raises(lookup.SourceLookupError) as exc_info:
        lookup.resolve_and_write_source_alias(
            source_root=fixture.root,
            ledger_path=tmp_path / "fictional-ledger.jsonl",
            generated_upload_name="company-001-document-00001.pdf",
            terminal_writer=lambda _path: None,
            contract_builder=unsafe_builder,
        )

    observable = _observable_exception(exc_info.value, capsys.readouterr())
    _assert_no_private_tokens(observable, fixture.private_tokens)
    assert str(fixture.first_source_file) not in observable


def test_cli_non_tty_fails_before_resolver_and_never_emits_a_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = _lookup_module()
    calls: list[str] = []

    def forbidden_resolver(**_kwargs: object) -> None:
        calls.append("resolver")
        raise AssertionError("source lookup ran without a controlling terminal")

    def no_terminal() -> Iterator[Callable[[Path], None]]:
        raise OSError(module.SOURCE_LOOKUP_TTY_REQUIRED)
        yield lambda _path: None

    monkeypatch.setattr(module, "resolve_and_write_source_alias", forbidden_resolver)
    monkeypatch.setattr(
        module, "SOURCE_LOOKUP_REPORT_ROOT", tmp_path / ".local_reports" / "slice100"
    )

    result = module.main(
        [
            "--acceptance-namespace",
            "acceptance-999",
            "--generated-upload-name",
            "company-001-document-00001.pdf",
            "--acknowledge-private-terminal-output",
        ],
        terminal_factory=lambda: contextmanager(no_terminal)(),
    )

    assert result != 0
    assert calls == []
    captured = capsys.readouterr()
    assert module.SOURCE_LOOKUP_TTY_REQUIRED in captured.out + captured.err
    assert "approved-private-root" not in captured.out + captured.err


def test_cli_derives_the_sealed_ledger_and_returns_only_an_exit_code(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = _lookup_module()
    source_root = tmp_path / "fictional-source-root"
    report_root = tmp_path / ".local_reports" / "slice100"
    observed: dict[str, object] = {}
    terminal_values: list[Path] = []

    @contextmanager
    def terminal() -> Iterator[Callable[[Path], None]]:
        observed["terminal_opened"] = True
        yield terminal_values.append

    def resolver(**kwargs: object) -> None:
        observed.update(kwargs)
        writer = kwargs["terminal_writer"]
        assert callable(writer)
        writer(source_root / "fictional-private-name.pdf")

    monkeypatch.setattr(module, "DEFAULT_SOURCE_ROOT", source_root)
    monkeypatch.setattr(module, "SOURCE_LOOKUP_REPORT_ROOT", report_root)
    monkeypatch.setattr(module, "resolve_and_write_source_alias", resolver)

    result = module.main(
        [
            "--acceptance-namespace",
            "acceptance-999",
            "--generated-upload-name",
            "company-001-document-00001.pdf",
            "--acknowledge-private-terminal-output",
        ],
        terminal_factory=lambda: terminal(),
    )

    assert type(result) is int
    assert result == 0
    assert observed["terminal_opened"] is True
    assert observed["source_root"] == source_root
    assert observed["ledger_path"] == (report_root / "acceptance-999" / "prepare-ledger.jsonl")
    assert observed["generated_upload_name"] == "company-001-document-00001.pdf"
    assert terminal_values == [source_root / "fictional-private-name.pdf"]
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


def test_cli_requires_explicit_private_terminal_acknowledgement_before_any_lookup(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = _lookup_module()
    calls: list[str] = []

    def forbidden_resolver(**_kwargs: object) -> None:
        calls.append("resolver")
        raise AssertionError("lookup ran without explicit terminal acknowledgement")

    def forbidden_terminal() -> object:
        calls.append("terminal")
        raise AssertionError("terminal opened without explicit acknowledgement")

    monkeypatch.setattr(module, "resolve_and_write_source_alias", forbidden_resolver)

    result = module.main(
        [
            "--acceptance-namespace",
            "acceptance-999",
            "--generated-upload-name",
            "company-001-document-00001.pdf",
        ],
        terminal_factory=forbidden_terminal,
    )

    assert result != 0
    assert calls == []
    captured = capsys.readouterr()
    assert "approved-private-root" not in captured.out + captured.err


@pytest.mark.parametrize("forbidden_option", ["--root", "--ledger", "--output"])
def test_cli_has_no_arbitrary_path_or_output_option(
    forbidden_option: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = _lookup_module()
    calls: list[str] = []
    monkeypatch.setattr(
        module,
        "resolve_and_write_source_alias",
        lambda **_kwargs: calls.append("resolver"),
    )

    @contextmanager
    def terminal() -> Iterator[Callable[[Path], None]]:
        yield lambda _path: None

    result = module.main(
        [
            "--acceptance-namespace",
            "acceptance-999",
            "--generated-upload-name",
            "company-001-document-00001.pdf",
            "--acknowledge-private-terminal-output",
            forbidden_option,
            "fictional-private-argument",
        ],
        terminal_factory=terminal,
    )

    assert result != 0
    assert calls == []
    captured = capsys.readouterr()
    assert "approved-private-root" not in captured.out + captured.err
