"""Strict two-company source/package preparation for Slice100 Task 3.

This module is deliberately narrower than the final live-run CLI.  It builds a
read-only, private-name-free contract for the approved source tree and prepares
two isolated durable deals/packages through an injected authenticated API
client.  The only local persistence is a strict, self-hashed resume ledger; the
durable API is re-read before any completed ledger row is trusted.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import sys
import time
import unicodedata
import uuid
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import MappingProxyType
from typing import Any, Never, Protocol

from idis.services.ingestion.no_substantive_content_attestation import (
    DOCUMENT_NO_SUBSTANTIVE_CONTENT_ATTESTATION_REQUIRED,
    DOCUMENT_NO_SUBSTANTIVE_CONTENT_CONFIRMED,
    DOCUMENT_NO_SUBSTANTIVE_CONTENT_PROOF_INCOMPLETE,
    NO_SUBSTANTIVE_CONTENT_RECEIPT_V2_SCHEMA_VERSION,
    WATERMARK_BATCH_ATTESTATION_NOT_ACTIVE,
    DocumentNoSubstantiveContentAttestationError,
    VerifiedDocumentNoSubstantiveContentAttestation,
    VerifiedDocumentNoSubstantiveContentAttestationManifest,
    project_safe_document_no_substantive_content_receipt,
    validate_document_no_substantive_content_receipt,
    validate_document_no_substantive_content_receipt_v2,
)

if sys.platform == "win32":
    import msvcrt as _msvcrt
else:
    import fcntl as _fcntl

DEFAULT_SOURCE_ROOT = Path(r"C:\Projects\IDIS\IDIS\real_example")
DEFAULT_EXPECTED_FILE_COUNTS = (58, 209)
DEFAULT_EXPECTED_COMPANY_BYTES = (496_782_706, 361_424_748)
DEFAULT_EXPECTED_TOTAL_FILES = 267
DEFAULT_EXPECTED_TOTAL_BYTES = 858_207_454
SOURCE_CONTRACT_SCHEMA_VERSION = "slice100-two-company-source-contract-v1"
SOURCE_PREPARATION_LEDGER_SCHEMA_VERSION = "slice100-source-preparation-ledger-jsonl-v1"
SOURCE_PREPARATION_IDEMPOTENCY_VERSION = "slice100-source-preparation-idempotency-v2"
HASH_CHUNK_SIZE_BYTES = 1024 * 1024
UPLOAD_CONTENT_TYPE = "application/octet-stream"
LEDGER_LOCK_TIMEOUT_SECONDS = 30.0
LEDGER_LOCK_RETRY_SECONDS = 0.05

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_EXTENSION_RE = re.compile(r"^[a-z0-9]{1,12}$")
_GENERATED_NAME_RE = re.compile(r"^company-00[12]-document-[0-9]{5}\.[a-z0-9]{1,12}$")
_COMPANY_ALIASES = ("company-001", "company-002")
_DEAL_ALIASES = ("deal-001", "deal-002")
_DOCUMENT_SET_ALIASES = ("documents-001", "documents-002")
_RUN_ALIASES = ("run-001", "run-002")
_LEDGER_ALIASES = ("ledger-001", "ledger-002")
_PACKAGE_ALIASES = ("package-001", "package-002")

_TerminalContentAdmission = (
    VerifiedDocumentNoSubstantiveContentAttestation
    | VerifiedDocumentNoSubstantiveContentAttestationManifest
)


if sys.platform == "win32":

    def _try_acquire_ledger_lock(file_descriptor: int) -> None:
        _msvcrt.locking(file_descriptor, _msvcrt.LK_NBLCK, 1)

    def _release_ledger_lock(file_descriptor: int) -> None:
        _msvcrt.locking(file_descriptor, _msvcrt.LK_UNLCK, 1)

else:

    def _try_acquire_ledger_lock(file_descriptor: int) -> None:
        _fcntl.flock(file_descriptor, _fcntl.LOCK_EX | _fcntl.LOCK_NB)

    def _release_ledger_lock(file_descriptor: int) -> None:
        _fcntl.flock(file_descriptor, _fcntl.LOCK_UN)


class SourcePackageContractError(Exception):
    """Fail-closed Task 3 error carrying only a stable, public-safe code."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class SourceFileContract:
    """Serializable safe binding for one private source file."""

    sequence: int
    generated_upload_name: str
    source_locator_sha256: str
    content_sha256: str
    size_bytes: int
    extension: str

    def safe_dict(self) -> dict[str, object]:
        return {
            "sequence": self.sequence,
            "generated_upload_name": self.generated_upload_name,
            "source_locator_sha256": self.source_locator_sha256,
            "content_sha256": self.content_sha256,
            "size_bytes": self.size_bytes,
            "extension": self.extension,
        }


@dataclass(frozen=True, slots=True)
class CompanySourceContract:
    """Safe source contract for exactly one sorted top-level company folder."""

    company_alias: str
    deal_alias: str
    document_set_alias: str
    run_alias: str
    ledger_alias: str
    package_alias: str
    source_company_locator_sha256: str
    file_count: int
    total_bytes: int
    source_contract_sha256: str
    files: tuple[SourceFileContract, ...]

    def safe_header(self) -> dict[str, object]:
        return {
            "company_alias": self.company_alias,
            "deal_alias": self.deal_alias,
            "document_set_alias": self.document_set_alias,
            "run_alias": self.run_alias,
            "ledger_alias": self.ledger_alias,
            "package_alias": self.package_alias,
            "source_company_locator_sha256": self.source_company_locator_sha256,
            "file_count": self.file_count,
            "total_bytes": self.total_bytes,
            "source_contract_sha256": self.source_contract_sha256,
        }

    def safe_contract_dict(self) -> dict[str, object]:
        return {**self.safe_header(), "files": [row.safe_dict() for row in self.files]}


@dataclass(frozen=True, slots=True, repr=False)
class _PrivateSourceAccess:
    """Ephemeral filesystem authority, intentionally separate from safe models."""

    root: Path
    paths: Mapping[tuple[str, int], Path]


class TwoCompanySourceContract:
    """Safe two-company contract with non-serializable private source access."""

    __slots__ = (
        "schema_version",
        "source_tree_fingerprint",
        "total_file_count",
        "total_bytes",
        "companies",
        "source_root_locator_sha256",
        "_private_source_access",
    )

    def __init__(
        self,
        *,
        schema_version: str,
        source_tree_fingerprint: str,
        total_file_count: int,
        total_bytes: int,
        companies: tuple[CompanySourceContract, CompanySourceContract],
        source_root_locator_sha256: str,
        private_source_access: _PrivateSourceAccess,
    ) -> None:
        self.schema_version = schema_version
        self.source_tree_fingerprint = source_tree_fingerprint
        self.total_file_count = total_file_count
        self.total_bytes = total_bytes
        self.companies = companies
        self.source_root_locator_sha256 = source_root_locator_sha256
        self._private_source_access = private_source_access

    @property
    def root(self) -> Path:
        """Private root for internal read-only enforcement; never serialized."""
        return self._private_source_access.root

    def source_path(self, company_alias: str, sequence: int) -> Path:
        """Return an ephemeral private path; it never enters a safe serializer."""
        try:
            return self._private_source_access.paths[(company_alias, sequence)]
        except KeyError:
            raise SourcePackageContractError(
                "DATA_ROOM_RECONCILIATION_INCOMPLETE",
                "Source contract membership is incomplete",
            ) from None

    def safe_summary(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "source_tree_fingerprint": self.source_tree_fingerprint,
            "source_root_locator_sha256": self.source_root_locator_sha256,
            "total_file_count": self.total_file_count,
            "total_bytes": self.total_bytes,
            "companies": [company.safe_header() for company in self.companies],
        }


class _ResponseLike(Protocol):
    status_code: int

    def json(self) -> dict[str, Any]: ...


class _PreparationApiClient(Protocol):
    def post(self, url: str, **kwargs: Any) -> _ResponseLike: ...

    def get(self, url: str, **kwargs: Any) -> _ResponseLike: ...


def build_two_company_source_contract(
    *,
    root: str | Path = DEFAULT_SOURCE_ROOT,
    expected_company_count: int = 2,
    expected_file_counts: Sequence[int] = DEFAULT_EXPECTED_FILE_COUNTS,
    expected_total_files: int = DEFAULT_EXPECTED_TOTAL_FILES,
    expected_company_bytes: Sequence[int] = DEFAULT_EXPECTED_COMPANY_BYTES,
    expected_total_bytes: int = DEFAULT_EXPECTED_TOTAL_BYTES,
) -> TwoCompanySourceContract:
    """Fingerprint exactly two sorted private folders without changing either."""
    if expected_company_count != 2:
        _fail_inventory("The source contract requires exactly two companies")
    if len(expected_file_counts) != 2 or len(expected_company_bytes) != 2:
        _fail_inventory("The expected source contract is malformed")

    root_path = _resolve_source_root(root)
    source_root_locator_sha256 = _resolved_locator_sha256("source-root", root_path)
    top_level = _safe_children(root_path)
    if len(top_level) != 2 or any(not child.is_dir() for child in top_level):
        _fail_inventory("The source root must contain exactly two company directories")

    companies: list[CompanySourceContract] = []
    private_paths: dict[tuple[str, int], Path] = {}
    all_content_hashes: set[str] = set()
    all_locator_hashes: set[str] = set()
    all_generated_names: set[str] = set()

    for index, company_root in enumerate(top_level):
        company_alias = _COMPANY_ALIASES[index]
        source_company_locator_sha256 = _resolved_locator_sha256(
            "source-company-root",
            company_root,
        )
        source_paths = _walk_source_files(company_root, root_path)
        safe_files: list[SourceFileContract] = []
        for sequence, source_path in enumerate(source_paths):
            extension = source_path.suffix.lower().removeprefix(".")
            if not _EXTENSION_RE.fullmatch(extension):
                _fail_inventory("A source file has no safe supported extension token")
            generated_name = f"{company_alias}-document-{sequence + 1:05d}.{extension}"
            if not _GENERATED_NAME_RE.fullmatch(generated_name):
                _fail_inventory("Generated upload naming failed its safety contract")
            relative = _normalized_private_relative_path(company_root, source_path)
            locator = _sha256_text(f"{company_alias}\x00{relative}")
            try:
                before = source_path.stat()
                digest = _hash_source_file(source_path)
                after = source_path.stat()
            except OSError:
                _fail_inventory("A source file could not be fingerprinted read-only")
            if before.st_size != after.st_size or before.st_mtime_ns != after.st_mtime_ns:
                raise SourcePackageContractError(
                    "SOURCE_TREE_MUTATED",
                    "The source tree changed while it was fingerprinted",
                )
            if not _SHA256_RE.fullmatch(digest):
                _fail_inventory("A source file is missing a canonical content hash")
            if digest in all_content_hashes:
                _fail_inventory("Duplicate source content hashes are not permitted")
            if locator in all_locator_hashes or generated_name in all_generated_names:
                _fail_inventory("Duplicate source bindings are not permitted")
            all_content_hashes.add(digest)
            all_locator_hashes.add(locator)
            all_generated_names.add(generated_name)
            file_contract = SourceFileContract(
                sequence=sequence,
                generated_upload_name=generated_name,
                source_locator_sha256=locator,
                content_sha256=digest,
                size_bytes=after.st_size,
                extension=extension,
            )
            safe_files.append(file_contract)
            private_paths[(company_alias, sequence)] = source_path

        file_count = len(safe_files)
        total_bytes = sum(row.size_bytes for row in safe_files)
        company_payload: dict[str, object] = {
            "schema_version": SOURCE_CONTRACT_SCHEMA_VERSION,
            "company_alias": company_alias,
            "deal_alias": _DEAL_ALIASES[index],
            "document_set_alias": _DOCUMENT_SET_ALIASES[index],
            "run_alias": _RUN_ALIASES[index],
            "ledger_alias": _LEDGER_ALIASES[index],
            "package_alias": _PACKAGE_ALIASES[index],
            "source_company_locator_sha256": source_company_locator_sha256,
            "file_count": file_count,
            "total_bytes": total_bytes,
            "files": [row.safe_dict() for row in safe_files],
        }
        companies.append(
            CompanySourceContract(
                company_alias=company_alias,
                deal_alias=_DEAL_ALIASES[index],
                document_set_alias=_DOCUMENT_SET_ALIASES[index],
                run_alias=_RUN_ALIASES[index],
                ledger_alias=_LEDGER_ALIASES[index],
                package_alias=_PACKAGE_ALIASES[index],
                source_company_locator_sha256=source_company_locator_sha256,
                file_count=file_count,
                total_bytes=total_bytes,
                source_contract_sha256=_canonical_sha256(company_payload),
                files=tuple(safe_files),
            )
        )

    actual_counts = tuple(company.file_count for company in companies)
    actual_company_bytes = tuple(company.total_bytes for company in companies)
    total_file_count = sum(actual_counts)
    total_bytes = sum(actual_company_bytes)
    if (
        actual_counts != tuple(expected_file_counts)
        or actual_company_bytes != tuple(expected_company_bytes)
        or total_file_count != expected_total_files
        or total_bytes != expected_total_bytes
    ):
        raise SourcePackageContractError(
            "DATA_ROOM_RECONCILIATION_INCOMPLETE",
            "The source inventory does not match the approved count and byte contract",
        )

    tree_payload = {
        "schema_version": SOURCE_CONTRACT_SCHEMA_VERSION,
        "source_root_locator_sha256": source_root_locator_sha256,
        "total_file_count": total_file_count,
        "total_bytes": total_bytes,
        "companies": [company.safe_contract_dict() for company in companies],
    }
    return TwoCompanySourceContract(
        schema_version=SOURCE_CONTRACT_SCHEMA_VERSION,
        source_tree_fingerprint=_canonical_sha256(tree_payload),
        total_file_count=total_file_count,
        total_bytes=total_bytes,
        companies=(companies[0], companies[1]),
        source_root_locator_sha256=source_root_locator_sha256,
        private_source_access=_PrivateSourceAccess(
            root=root_path,
            paths=MappingProxyType(private_paths),
        ),
    )


def _resolve_source_root(root: str | Path) -> Path:
    try:
        path = Path(root).resolve(strict=True)
    except OSError:
        _fail_inventory("The approved source root is unavailable")
    if not path.is_dir() or _is_link_or_reparse(path):
        _fail_inventory("The approved source root is not a plain directory")
    return path


def _safe_children(directory: Path) -> list[Path]:
    try:
        children = list(directory.iterdir())
    except OSError:
        _fail_inventory("The source tree could not be inventoried")
    for child in children:
        if _is_link_or_reparse(child):
            _fail_inventory("Linked source entries are forbidden")
    return sorted(children, key=_private_sort_key)


def _walk_source_files(company_root: Path, approved_root: Path) -> list[Path]:
    files: list[Path] = []
    pending = [company_root]
    while pending:
        current = pending.pop()
        for entry in reversed(_safe_children(current)):
            try:
                resolved = entry.resolve(strict=True)
            except OSError:
                _fail_inventory("A source entry could not be resolved")
            if not resolved.is_relative_to(company_root) or not resolved.is_relative_to(
                approved_root
            ):
                _fail_inventory("A source entry escapes its assigned company")
            if entry.is_dir():
                pending.append(entry)
            elif entry.is_file():
                files.append(entry)
            else:
                _fail_inventory("The source tree contains a non-file entry")
    files.sort(key=lambda path: _private_sort_key(path.relative_to(company_root)))
    return files


def _private_sort_key(path: Path) -> tuple[str, str]:
    normalized = unicodedata.normalize("NFC", path.as_posix())
    return (normalized.casefold(), normalized)


def _normalized_private_relative_path(company_root: Path, path: Path) -> str:
    try:
        relative = path.relative_to(company_root)
    except ValueError:
        _fail_inventory("A source entry escapes its assigned company")
    return unicodedata.normalize("NFC", relative.as_posix())


def _is_link_or_reparse(path: Path) -> bool:
    try:
        info = path.lstat()
    except OSError:
        return True
    attributes = getattr(info, "st_file_attributes", 0)
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    return path.is_symlink() or bool(attributes & reparse_flag)


def _hash_source_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(HASH_CHUNK_SIZE_BYTES), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_json_bytes(payload: object) -> bytes:
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")


def _canonical_sha256(payload: object) -> str:
    return hashlib.sha256(_canonical_json_bytes(payload)).hexdigest()


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _resolved_locator_sha256(kind: str, path: Path) -> str:
    """Hash a normalized resolved locator without retaining or emitting its text."""
    try:
        resolved = path.resolve(strict=True)
    except OSError:
        _fail_inventory("A required source locator became unavailable")
    normalized = unicodedata.normalize("NFC", resolved.as_posix())
    if os.name == "nt":
        normalized = normalized.casefold()
    return _sha256_text(f"slice100-{kind}-locator-v1\x00{normalized}")


def _fail_inventory(message: str) -> Never:
    raise SourcePackageContractError("DATA_ROOM_INVENTORY_PACKAGE_FAILED", message)


def write_reconciled_source_alias(
    *,
    contract: TwoCompanySourceContract,
    ledger_path: str | Path,
    generated_upload_name: str,
    private_path_writer: Callable[[Path], None],
) -> None:
    """Validate one safe alias against the sealed contract and emit no return value.

    This is the deliberately narrow private-path seam used by the operator-only
    console lookup.  The caller supplies the terminal writer; this function never
    serializes, logs, returns, or otherwise persists the resolved private path.
    """
    if not isinstance(generated_upload_name, str) or not _GENERATED_NAME_RE.fullmatch(
        generated_upload_name
    ):
        _fail_reconciliation("The requested source alias is not canonical")

    raw_ledger_path = Path(ledger_path)
    resolved_ledger_path = _validate_ledger_destination(raw_ledger_path, contract.root)
    if (
        not raw_ledger_path.is_absolute()
        or resolved_ledger_path != raw_ledger_path
        or not raw_ledger_path.is_file()
        or _is_link_or_reparse(raw_ledger_path)
    ):
        _fail_reconciliation("The source lookup ledger path is not canonical")
    try:
        ledger = _load_prepare_ledger_history(resolved_ledger_path)
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError):
        _fail_reconciliation("The source lookup ledger is invalid")
    _reconcile_ledger_with_contract(ledger, contract)

    matches = [
        (company, source_file)
        for company in contract.companies
        for source_file in company.files
        if source_file.generated_upload_name == generated_upload_name
    ]
    if len(matches) != 1:
        _fail_reconciliation("The requested source alias does not resolve uniquely")
    company, source_file = matches[0]
    private_path = _verified_lookup_source_path(contract, company, source_file)
    private_path_writer(private_path)


def _verified_lookup_source_path(
    contract: TwoCompanySourceContract,
    company: CompanySourceContract,
    source_file: SourceFileContract,
) -> Path:
    path = contract.source_path(company.company_alias, source_file.sequence)
    _assert_lookup_plain_source_chain(path, contract.root)
    _assert_safe_source_path(path, contract.root)
    try:
        root_relative = path.relative_to(contract.root)
    except ValueError:
        raise SourcePackageContractError(
            "SOURCE_TREE_MUTATED",
            "A source entry escaped its approved root",
        ) from None
    if len(root_relative.parts) < 2:
        raise SourcePackageContractError(
            "SOURCE_TREE_MUTATED",
            "A source entry lost its company binding",
        )
    company_relative = Path(*root_relative.parts[1:])
    normalized_relative = unicodedata.normalize("NFC", company_relative.as_posix())
    locator_sha256 = _sha256_text(f"{company.company_alias}\x00{normalized_relative}")
    if locator_sha256 != source_file.source_locator_sha256:
        raise SourcePackageContractError(
            "SOURCE_TREE_MUTATED",
            "A source locator changed after contract construction",
        )

    try:
        before = path.stat()
        current_hash = _hash_source_file(path)
        after = path.stat()
    except OSError:
        raise SourcePackageContractError(
            "SOURCE_TREE_MUTATED",
            "A source file became unavailable during lookup",
        ) from None
    _assert_lookup_plain_source_chain(path, contract.root)
    _assert_safe_source_path(path, contract.root)
    before_identity = (
        before.st_mode,
        before.st_size,
        before.st_mtime_ns,
        before.st_dev,
        before.st_ino,
    )
    after_identity = (
        after.st_mode,
        after.st_size,
        after.st_mtime_ns,
        after.st_dev,
        after.st_ino,
    )
    if (
        before_identity != after_identity
        or not stat.S_ISREG(after.st_mode)
        or after.st_size != source_file.size_bytes
        or current_hash != source_file.content_sha256
    ):
        raise SourcePackageContractError(
            "SOURCE_TREE_MUTATED",
            "A source file changed during lookup",
        )
    return path


def _assert_lookup_plain_source_chain(path: Path, root: Path) -> None:
    try:
        path.relative_to(root)
    except ValueError:
        raise SourcePackageContractError(
            "SOURCE_TREE_MUTATED",
            "A source entry escaped its approved root",
        ) from None
    candidate = path
    while True:
        if _is_link_or_reparse(candidate):
            raise SourcePackageContractError(
                "SOURCE_TREE_MUTATED",
                "A source entry acquired a linked or reparse component",
            )
        if candidate == root:
            return
        candidate = candidate.parent


def prepare_two_company_source_packages(
    *,
    api_client: _PreparationApiClient,
    api_key: str,
    ledger_path: str | Path,
    root: str | Path = DEFAULT_SOURCE_ROOT,
    expected_company_count: int = 2,
    expected_file_counts: Sequence[int] = DEFAULT_EXPECTED_FILE_COUNTS,
    expected_total_files: int = DEFAULT_EXPECTED_TOTAL_FILES,
    expected_company_bytes: Sequence[int] = DEFAULT_EXPECTED_COMPANY_BYTES,
    expected_total_bytes: int = DEFAULT_EXPECTED_TOTAL_BYTES,
    no_substantive_content_attestation: _TerminalContentAdmission | None = None,
) -> dict[str, Any]:
    """Prepare two isolated durable deals/packages with authenticated resume checks.

    This is an injected-client boundary, not the final external launcher.  It
    performs no provider probes and never creates a run.  Every accepted resume
    row is checked against the deal-scoped durable API before it is skipped.
    """
    if not isinstance(api_key, str) or not api_key.strip():
        raise SourcePackageContractError(
            "DATA_ROOM_DURABLE_HANDOFF_INCOMPLETE",
            "Authenticated source preparation requires an API key",
        )
    contract = build_two_company_source_contract(
        root=root,
        expected_company_count=expected_company_count,
        expected_file_counts=expected_file_counts,
        expected_total_files=expected_total_files,
        expected_company_bytes=expected_company_bytes,
        expected_total_bytes=expected_total_bytes,
    )
    _require_unique_attestation_source_binding(contract, no_substantive_content_attestation)
    resolved_ledger_path = _validate_ledger_destination(Path(ledger_path), contract.root)
    ledger = _load_or_initialize_ledger(resolved_ledger_path, contract)
    _reconcile_ledger_with_contract(ledger, contract)

    headers = {"X-IDIS-API-Key": api_key}
    company_states = _ledger_companies(ledger)

    # Create or authenticate/reconcile both deals before the first upload.  This
    # closes the one-deal-for-both-folders failure before document membership can
    # be contaminated.
    for company, state in zip(contract.companies, company_states, strict=True):
        deal_id = state["deal_id"]
        if deal_id is None:
            response = _api_post_json(
                api_client,
                "/v1/deals",
                expected_status=201,
                headers={
                    **headers,
                    "Idempotency-Key": _preparation_idempotency_key(
                        contract,
                        company,
                        operation="deal-create",
                        discriminator=company.source_contract_sha256,
                    ),
                },
                json_body={
                    "name": company.deal_alias,
                    "company_name": company.company_alias,
                },
            )
            state["deal_id"] = _validate_deal_record(
                response,
                company=company,
            )
            _save_prepare_ledger(resolved_ledger_path, ledger, contract.root)
        else:
            response = _api_get_json(
                api_client,
                f"/v1/deals/{deal_id}",
                expected_status=200,
                headers=headers,
            )
            if _validate_deal_record(response, company=company) != deal_id:
                _fail_durable("A resumed deal does not match its source company")

    deal_ids = [state["deal_id"] for state in company_states]
    if any(deal_id is None for deal_id in deal_ids) or len(set(deal_ids)) != 2:
        raise SourcePackageContractError(
            "DATA_ROOM_RECONCILIATION_INCOMPLETE",
            "The two source companies must map to two distinct durable deals",
        )

    seen_documents: set[str] = set()
    seen_artifacts: set[str] = set()
    for company, state in zip(contract.companies, company_states, strict=True):
        deal_id = str(state["deal_id"])
        uploads = _ledger_uploads(state)
        completed_by_sequence = {int(row["sequence"]): row for row in uploads}
        for source_file in company.files:
            completed = completed_by_sequence.get(source_file.sequence)
            if completed is not None:
                _reconcile_completed_upload(
                    api_client=api_client,
                    headers=headers,
                    deal_id=deal_id,
                    source_file=source_file,
                    receipt=completed,
                    source_tree_fingerprint=contract.source_tree_fingerprint,
                    no_substantive_content_attestation=no_substantive_content_attestation,
                )
                receipt = completed
            else:
                _assert_source_file_unchanged(contract, company, source_file)
                attestation_envelope_sha256 = None
                watermark_bindings: dict[str, str] = {}
                if isinstance(
                    no_substantive_content_attestation,
                    VerifiedDocumentNoSubstantiveContentAttestation,
                ) and _attestation_matches_source(
                    contract, source_file, no_substantive_content_attestation
                ):
                    attestation_envelope_sha256 = no_substantive_content_attestation.envelope_sha256
                elif isinstance(
                    no_substantive_content_attestation,
                    VerifiedDocumentNoSubstantiveContentAttestationManifest,
                ):
                    watermark_summary = no_substantive_content_attestation.runtime_summary()
                    watermark_bindings = {
                        "watermark_manifest_sha256": str(watermark_summary["manifest_sha256"]),
                        "watermark_classifier_policy_sha256": str(
                            watermark_summary["classifier_policy_sha256"]
                        ),
                        "watermark_candidate_set_sha256": str(
                            watermark_summary["candidate_set_sha256"]
                        ),
                        "watermark_batch_envelope_sha256": str(
                            watermark_summary["batch_envelope_sha256"]
                        ),
                    }
                idempotency_key = _preparation_idempotency_key(
                    contract,
                    company,
                    operation="document-upload",
                    discriminator=(f"{source_file.sequence}:{source_file.content_sha256}"),
                    attestation_envelope_sha256=attestation_envelope_sha256,
                    **watermark_bindings,
                )
                response = _api_post_json(
                    api_client,
                    f"/v1/deals/{deal_id}/documents/upload",
                    expected_status=201,
                    headers={
                        **headers,
                        "Idempotency-Key": idempotency_key,
                        "Content-Type": UPLOAD_CONTENT_TYPE,
                        "X-IDIS-Source-Tree-Fingerprint": contract.source_tree_fingerprint,
                    },
                    params={
                        "filename": source_file.generated_upload_name,
                        "doc_type": "DATA_ROOM_FILE",
                        "sha256": source_file.content_sha256,
                        "source_system": "slice100-strict-source-preparation",
                    },
                    content=_iter_source_bytes(
                        contract.source_path(company.company_alias, source_file.sequence),
                        contract.root,
                    ),
                )
                receipt = _validated_upload_receipt(
                    response=response,
                    deal_id=deal_id,
                    source_file=source_file,
                    source_tree_fingerprint=contract.source_tree_fingerprint,
                    no_substantive_content_attestation=no_substantive_content_attestation,
                )
                _admit_unique_upload_receipt(
                    receipt,
                    seen_documents=seen_documents,
                    seen_artifacts=seen_artifacts,
                )
                uploads.append(receipt)
                _save_prepare_ledger(resolved_ledger_path, ledger, contract.root)
                continue
            _admit_unique_upload_receipt(
                receipt,
                seen_documents=seen_documents,
                seen_artifacts=seen_artifacts,
            )
    for company, state in zip(contract.companies, company_states, strict=True):
        deal_id = str(state["deal_id"])
        uploads = _ledger_uploads(state)
        manifest = _source_package_manifest(company, contract, uploads)
        manifest_payload = manifest.model_dump(mode="json")
        package_id = state["package_id"]
        if package_id is None:
            response = _api_post_json(
                api_client,
                f"/v1/deals/{deal_id}/data-room-packages",
                expected_status=201,
                headers={
                    **headers,
                    "Idempotency-Key": _preparation_idempotency_key(
                        contract,
                        company,
                        operation="package-create",
                        discriminator=str(manifest.manifest_sha256),
                    ),
                },
                json_body={
                    "document_ids": [row["document_id"] for row in uploads],
                    "source_manifest": manifest_payload,
                },
            )
            package_id = _required_uuid(response, "package_id")
            _validate_durable_package_header(
                record=response,
                deal_id=deal_id,
                package_id=package_id,
                manifest_payload=manifest_payload,
            )
            state["package_id"] = package_id
            state["manifest_sha256"] = str(manifest.manifest_sha256)
            _save_prepare_ledger(resolved_ledger_path, ledger, contract.root)
        elif state["manifest_sha256"] != str(manifest.manifest_sha256):
            _fail_reconciliation("A resumed package manifest does not match the source contract")

        package_record = _api_get_json(
            api_client,
            f"/v1/deals/{deal_id}/data-room-packages/{package_id}",
            expected_status=200,
            headers=headers,
        )
        _validate_durable_package_record(
            record=package_record,
            deal_id=deal_id,
            package_id=str(package_id),
            manifest_payload=manifest_payload,
        )

    package_ids = [state["package_id"] for state in company_states]
    if any(package_id is None for package_id in package_ids) or len(set(package_ids)) != 2:
        _fail_reconciliation("The two source companies must have distinct durable packages")

    # Bytes, not size/mtime metadata, are the final source authority.
    try:
        final_contract = build_two_company_source_contract(
            root=root,
            expected_company_count=expected_company_count,
            expected_file_counts=expected_file_counts,
            expected_total_files=expected_total_files,
            expected_company_bytes=expected_company_bytes,
            expected_total_bytes=expected_total_bytes,
        )
    except SourcePackageContractError:
        raise SourcePackageContractError(
            "SOURCE_TREE_MUTATED",
            "The source tree changed during durable preparation",
        ) from None
    if final_contract.source_tree_fingerprint != contract.source_tree_fingerprint:
        raise SourcePackageContractError(
            "SOURCE_TREE_MUTATED",
            "The source tree changed during durable preparation",
        )

    _save_prepare_ledger(resolved_ledger_path, ledger, contract.root)
    return _preparation_safe_summary(ledger)


def _admit_unique_upload_receipt(
    receipt: Mapping[str, Any],
    *,
    seen_documents: set[str],
    seen_artifacts: set[str],
) -> None:
    document_id = str(receipt["document_id"])
    artifact_id = str(receipt["doc_id"])
    if document_id in seen_documents or artifact_id in seen_artifacts:
        raise SourcePackageContractError(
            "DATA_ROOM_RECONCILIATION_INCOMPLETE",
            "A durable document or artifact is bound to more than one source row",
        )
    seen_documents.add(document_id)
    seen_artifacts.add(artifact_id)


def _validate_ledger_destination(path: Path, source_root: Path) -> Path:
    try:
        resolved = path.resolve(strict=False)
    except OSError:
        _fail_inventory("The preparation ledger destination is invalid")
    if resolved == source_root or resolved.is_relative_to(source_root):
        _fail_inventory("The preparation ledger must be outside the read-only source")
    if resolved.exists() and (_is_link_or_reparse(resolved) or resolved.is_dir()):
        _fail_inventory("The preparation ledger destination is unsafe")
    return resolved


@contextmanager
def _exclusive_ledger_lock(path: Path, source_root: Path) -> Iterator[None]:
    """Serialize complete-chain validation and append across gate processes."""
    lock_path = path.with_name(f".{path.name}.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    if _validate_ledger_destination(lock_path, source_root) != lock_path:
        _fail_inventory("The preparation ledger lock destination changed unexpectedly")

    with lock_path.open("a+b", buffering=0) as stream:
        stream.seek(0, os.SEEK_END)
        if stream.tell() == 0:
            stream.write(b"\0")
            os.fsync(stream.fileno())

        deadline = time.monotonic() + LEDGER_LOCK_TIMEOUT_SECONDS
        while True:
            try:
                stream.seek(0)
                _try_acquire_ledger_lock(stream.fileno())
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise TimeoutError("preparation ledger lock timed out") from None
                time.sleep(LEDGER_LOCK_RETRY_SECONDS)

        try:
            yield
        finally:
            stream.seek(0)
            _release_ledger_lock(stream.fileno())


def _new_prepare_ledger(contract: TwoCompanySourceContract) -> dict[str, Any]:
    ledger: dict[str, Any] = {
        "schema_version": SOURCE_PREPARATION_LEDGER_SCHEMA_VERSION,
        "source_tree_fingerprint": contract.source_tree_fingerprint,
        "source_root_locator_sha256": contract.source_root_locator_sha256,
        "total_file_count": contract.total_file_count,
        "total_bytes": contract.total_bytes,
        "companies": [
            {
                **company.safe_header(),
                "deal_id": None,
                "uploads": [],
                "package_id": None,
                "manifest_sha256": None,
            }
            for company in contract.companies
        ],
        "ledger_sequence": 0,
        "previous_ledger_sha256": None,
        "ledger_sha256": "",
    }
    _seal_prepare_ledger(ledger)
    return ledger


def _load_or_initialize_ledger(
    path: Path,
    contract: TwoCompanySourceContract,
) -> dict[str, Any]:
    existing_ledger: bool | None = None
    try:
        with _exclusive_ledger_lock(path, contract.root):
            existing_ledger = path.exists()
            if existing_ledger:
                return _load_prepare_ledger_history(path)
            ledger = _new_prepare_ledger(contract)
            _append_prepare_ledger_line(path, ledger, contract.root)
            persisted = _load_prepare_ledger_history(path)
            if persisted["ledger_sha256"] != ledger["ledger_sha256"]:
                raise ValueError("preparation ledger initialization changed concurrently")
            return persisted
    except OSError:
        _fail_durable("The source preparation ledger could not be initialized safely")
    except (UnicodeError, json.JSONDecodeError, TypeError, ValueError):
        if existing_ledger:
            _fail_reconciliation("The preparation resume ledger is invalid")
        _fail_durable("The source preparation ledger could not be initialized safely")


def _load_prepare_ledger_history(path: Path) -> dict[str, Any]:
    previous: dict[str, Any] | None = None
    with path.open("r", encoding="utf-8", newline="") as stream:
        for expected_sequence, line in enumerate(stream):
            if not line.endswith("\n") or not line.strip():
                raise ValueError("truncated preparation ledger event")
            payload = json.loads(line, object_pairs_hook=_reject_duplicate_json_keys)
            _validate_prepare_ledger_event(payload, expected_sequence, previous)
            assert isinstance(payload, dict)
            previous = payload
    if previous is None:
        raise ValueError("empty preparation ledger")
    return previous


def _validate_prepare_ledger_event(
    payload: Any,
    expected_sequence: int,
    previous: dict[str, Any] | None,
) -> None:
    _validate_prepare_ledger_shape(payload)
    assert isinstance(payload, dict)
    if payload["ledger_sequence"] != expected_sequence:
        raise ValueError("preparation ledger sequence gap")
    previous_hash = None if previous is None else previous["ledger_sha256"]
    if payload["previous_ledger_sha256"] != previous_hash:
        raise ValueError("preparation ledger hash-chain mismatch")
    expected_hash = str(payload["ledger_sha256"])
    unhashed = dict(payload)
    unhashed.pop("ledger_sha256")
    if not _SHA256_RE.fullmatch(expected_hash) or _canonical_sha256(unhashed) != expected_hash:
        raise ValueError("preparation ledger event digest mismatch")
    if previous is not None:
        _validate_prepare_ledger_transition(previous, payload)


def _reject_duplicate_json_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


_LEDGER_KEYS = frozenset(
    {
        "schema_version",
        "source_tree_fingerprint",
        "source_root_locator_sha256",
        "total_file_count",
        "total_bytes",
        "companies",
        "ledger_sequence",
        "previous_ledger_sha256",
        "ledger_sha256",
    }
)
_LEDGER_COMPANY_KEYS = frozenset(
    {
        "company_alias",
        "deal_alias",
        "document_set_alias",
        "run_alias",
        "ledger_alias",
        "package_alias",
        "source_company_locator_sha256",
        "file_count",
        "total_bytes",
        "source_contract_sha256",
        "deal_id",
        "uploads",
        "package_id",
        "manifest_sha256",
    }
)
_LEDGER_UPLOAD_KEYS = frozenset(
    {
        "sequence",
        "generated_upload_name",
        "source_locator_sha256",
        "content_sha256",
        "size_bytes",
        "extension",
        "document_id",
        "doc_id",
    }
)


def _validate_prepare_ledger_shape(payload: Any) -> None:
    if not isinstance(payload, dict) or set(payload) != _LEDGER_KEYS:
        raise ValueError("invalid preparation ledger")
    if payload.get("schema_version") != SOURCE_PREPARATION_LEDGER_SCHEMA_VERSION:
        raise ValueError("invalid preparation ledger version")
    for hash_key in (
        "source_tree_fingerprint",
        "source_root_locator_sha256",
        "ledger_sha256",
    ):
        if not isinstance(payload.get(hash_key), str) or not _SHA256_RE.fullmatch(
            payload[hash_key]
        ):
            raise ValueError("invalid preparation ledger hash")
    previous_hash = payload.get("previous_ledger_sha256")
    if previous_hash is not None and (
        not isinstance(previous_hash, str) or not _SHA256_RE.fullmatch(previous_hash)
    ):
        raise ValueError("invalid previous preparation ledger hash")
    _require_nonnegative_int(payload.get("ledger_sequence"))
    _require_nonnegative_int(payload.get("total_file_count"))
    _require_nonnegative_int(payload.get("total_bytes"))
    companies = payload.get("companies")
    if not isinstance(companies, list) or len(companies) != 2:
        raise ValueError("invalid preparation ledger companies")
    for company in companies:
        if not isinstance(company, dict) or set(company) != _LEDGER_COMPANY_KEYS:
            raise ValueError("invalid preparation company row")
        for key in (
            "company_alias",
            "deal_alias",
            "document_set_alias",
            "run_alias",
            "ledger_alias",
            "package_alias",
            "source_company_locator_sha256",
            "source_contract_sha256",
        ):
            if not isinstance(company.get(key), str):
                raise ValueError("invalid preparation company value")
        for hash_key in ("source_company_locator_sha256", "source_contract_sha256"):
            if not _SHA256_RE.fullmatch(company[hash_key]):
                raise ValueError("invalid preparation company hash")
        _require_nonnegative_int(company.get("file_count"))
        _require_nonnegative_int(company.get("total_bytes"))
        _require_optional_uuid(company.get("deal_id"))
        _require_optional_uuid(company.get("package_id"))
        manifest_hash = company.get("manifest_sha256")
        if manifest_hash is not None and (
            not isinstance(manifest_hash, str) or not _SHA256_RE.fullmatch(manifest_hash)
        ):
            raise ValueError("invalid package manifest hash")
        if (company.get("package_id") is None) != (manifest_hash is None):
            raise ValueError("partial package receipt")
        uploads = company.get("uploads")
        if not isinstance(uploads, list):
            raise ValueError("invalid upload receipts")
        for upload in uploads:
            _validate_upload_ledger_shape(upload)


def _validate_upload_ledger_shape(upload: Any) -> None:
    if not isinstance(upload, dict) or set(upload) != _LEDGER_UPLOAD_KEYS:
        raise ValueError("invalid upload receipt")
    _require_nonnegative_int(upload.get("sequence"))
    _require_nonnegative_int(upload.get("size_bytes"))
    if not isinstance(upload.get("generated_upload_name"), str) or not _GENERATED_NAME_RE.fullmatch(
        upload["generated_upload_name"]
    ):
        raise ValueError("invalid generated upload name")
    for key in ("source_locator_sha256", "content_sha256"):
        value = upload.get(key)
        if not isinstance(value, str) or not _SHA256_RE.fullmatch(value):
            raise ValueError("invalid upload receipt hash")
    extension = upload.get("extension")
    if not isinstance(extension, str) or not _EXTENSION_RE.fullmatch(extension):
        raise ValueError("invalid upload extension")
    _require_uuid(upload.get("document_id"))
    _require_uuid(upload.get("doc_id"))


def _require_nonnegative_int(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("expected nonnegative integer")
    return int(value)


def _require_uuid(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError("expected UUID")
    canonical = str(uuid.UUID(value))
    if canonical != value:
        raise ValueError("expected canonical UUID")
    return value


def _require_optional_uuid(value: Any) -> None:
    if value is not None:
        _require_uuid(value)


def _ledger_companies(ledger: dict[str, Any]) -> list[dict[str, Any]]:
    companies = ledger["companies"]
    assert isinstance(companies, list)
    return companies


def _ledger_uploads(company_state: dict[str, Any]) -> list[dict[str, Any]]:
    uploads = company_state["uploads"]
    assert isinstance(uploads, list)
    return uploads


def _reconcile_ledger_with_contract(
    ledger: dict[str, Any],
    contract: TwoCompanySourceContract,
) -> None:
    if (
        ledger["source_tree_fingerprint"] != contract.source_tree_fingerprint
        or ledger["source_root_locator_sha256"] != contract.source_root_locator_sha256
        or ledger["total_file_count"] != contract.total_file_count
        or ledger["total_bytes"] != contract.total_bytes
    ):
        _fail_reconciliation("The resume ledger is bound to a different source tree")
    company_states = _ledger_companies(ledger)
    seen_deals: set[str] = set()
    seen_packages: set[str] = set()
    seen_documents: set[str] = set()
    seen_artifacts: set[str] = set()
    for company, state in zip(contract.companies, company_states, strict=True):
        for key, expected in company.safe_header().items():
            if state.get(key) != expected:
                _fail_reconciliation("The resume ledger company binding has drifted")
        deal_id = state["deal_id"]
        if deal_id is not None and deal_id in seen_deals:
            _fail_reconciliation("The resume ledger reuses a deal across companies")
        if deal_id is not None:
            seen_deals.add(deal_id)
        uploads = _ledger_uploads(state)
        if len(uploads) > len(company.files):
            _fail_reconciliation("The resume ledger has extra upload rows")
        for sequence, receipt in enumerate(uploads):
            expected_file = company.files[sequence]
            expected_safe = expected_file.safe_dict()
            for key, expected in expected_safe.items():
                if receipt.get(key) != expected:
                    _fail_reconciliation("A resume upload row has drifted")
            document_id = str(receipt["document_id"])
            artifact_id = str(receipt["doc_id"])
            if document_id in seen_documents or artifact_id in seen_artifacts:
                _fail_reconciliation("The resume ledger reuses a durable document or artifact")
            seen_documents.add(document_id)
            seen_artifacts.add(artifact_id)
        package_id = state["package_id"]
        if package_id is not None:
            if len(uploads) != len(company.files) or package_id in seen_packages:
                _fail_reconciliation("A resume package receipt is incomplete or duplicated")
            seen_packages.add(package_id)


def _seal_prepare_ledger(ledger: dict[str, Any]) -> None:
    ledger.pop("ledger_sha256", None)
    ledger["ledger_sha256"] = _canonical_sha256(ledger)


def _save_prepare_ledger(path: Path, ledger: dict[str, Any], source_root: Path) -> None:
    try:
        with _exclusive_ledger_lock(path, source_root):
            previous = _load_prepare_ledger_history(path)
            if previous["ledger_sha256"] != ledger["ledger_sha256"]:
                raise ValueError("preparation ledger changed concurrently")
            if _ledger_business_payload(previous) == _ledger_business_payload(ledger):
                return
            ledger["ledger_sequence"] = int(previous["ledger_sequence"]) + 1
            ledger["previous_ledger_sha256"] = previous["ledger_sha256"]
            _seal_prepare_ledger(ledger)
            _validate_prepare_ledger_event(
                ledger,
                int(previous["ledger_sequence"]) + 1,
                previous,
            )
            _append_prepare_ledger_line(path, ledger, source_root)
            persisted = _load_prepare_ledger_history(path)
            if persisted["ledger_sha256"] != ledger["ledger_sha256"]:
                raise ValueError("preparation ledger append was not durable")
    except (OSError, TypeError, ValueError):
        raise SourcePackageContractError(
            "DATA_ROOM_DURABLE_HANDOFF_INCOMPLETE",
            "The source preparation ledger could not be persisted safely",
        ) from None


def _append_prepare_ledger_line(
    path: Path,
    ledger: dict[str, Any],
    source_root: Path,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if _validate_ledger_destination(path, source_root) != path:
        _fail_inventory("The preparation ledger destination changed unexpectedly")
    encoded = _canonical_json_bytes(ledger) + b"\n"
    with path.open("ab") as stream:
        stream.write(encoded)
        stream.flush()
        os.fsync(stream.fileno())


def _ledger_business_payload(ledger: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in ledger.items()
        if key not in {"ledger_sequence", "previous_ledger_sha256", "ledger_sha256"}
    }


def _validate_prepare_ledger_transition(
    previous: Mapping[str, Any],
    current: Mapping[str, Any],
) -> None:
    for key in (
        "schema_version",
        "source_tree_fingerprint",
        "source_root_locator_sha256",
        "total_file_count",
        "total_bytes",
    ):
        if previous[key] != current[key]:
            raise ValueError("immutable preparation ledger header changed")
    previous_companies = previous["companies"]
    current_companies = current["companies"]
    if not isinstance(previous_companies, list) or not isinstance(current_companies, list):
        raise ValueError("invalid preparation ledger companies")
    immutable_company_keys = _LEDGER_COMPANY_KEYS - {
        "deal_id",
        "uploads",
        "package_id",
        "manifest_sha256",
    }
    for old, new in zip(previous_companies, current_companies, strict=True):
        if any(old[key] != new[key] for key in immutable_company_keys):
            raise ValueError("immutable preparation company binding changed")
        if old["deal_id"] is not None and new["deal_id"] != old["deal_id"]:
            raise ValueError("durable deal receipt changed")
        old_uploads = old["uploads"]
        new_uploads = new["uploads"]
        if (
            not isinstance(old_uploads, list)
            or not isinstance(new_uploads, list)
            or new_uploads[: len(old_uploads)] != old_uploads
            or len(new_uploads) > len(old_uploads) + 1
        ):
            raise ValueError("upload receipts are not append-only")
        if old["package_id"] is not None and (
            new["package_id"] != old["package_id"]
            or new["manifest_sha256"] != old["manifest_sha256"]
        ):
            raise ValueError("durable package receipt changed")
        if new["package_id"] is not None and len(new_uploads) != new["file_count"]:
            raise ValueError("package receipt precedes complete upload receipts")


def _preparation_idempotency_key(
    contract: TwoCompanySourceContract,
    company: CompanySourceContract,
    *,
    operation: str,
    discriminator: str,
    attestation_envelope_sha256: str | None = None,
    watermark_manifest_sha256: str | None = None,
    watermark_classifier_policy_sha256: str | None = None,
    watermark_candidate_set_sha256: str | None = None,
    watermark_batch_envelope_sha256: str | None = None,
) -> str:
    digest_bindings = (
        attestation_envelope_sha256,
        watermark_manifest_sha256,
        watermark_classifier_policy_sha256,
        watermark_candidate_set_sha256,
        watermark_batch_envelope_sha256,
    )
    if any(value is not None and not _SHA256_RE.fullmatch(value) for value in digest_bindings):
        _fail_reconciliation("The attestation idempotency binding is invalid")
    watermark_digests = digest_bindings[1:]
    if any(value is not None for value in watermark_digests) and not all(
        value is not None for value in watermark_digests
    ):
        _fail_reconciliation("The watermark idempotency binding is incomplete")
    material_fields = [
        SOURCE_PREPARATION_IDEMPOTENCY_VERSION,
        contract.source_tree_fingerprint,
        company.company_alias,
        operation,
        discriminator,
        attestation_envelope_sha256 or "no-attestation",
    ]
    if any(value is not None for value in watermark_digests):
        material_fields.extend(
            (
                watermark_manifest_sha256 or "no-watermark-manifest",
                watermark_classifier_policy_sha256 or "no-watermark-policy",
                watermark_candidate_set_sha256 or "no-watermark-candidates",
                watermark_batch_envelope_sha256 or "no-watermark-batch",
            )
        )
    material = "\x00".join(material_fields)
    return f"idis-{_sha256_text(material)}"


def _api_post_json(
    client: _PreparationApiClient,
    url: str,
    *,
    expected_status: int,
    headers: dict[str, str],
    json_body: dict[str, Any] | None = None,
    params: dict[str, Any] | None = None,
    content: Iterable[bytes] | None = None,
) -> dict[str, Any]:
    kwargs: dict[str, Any] = {"headers": headers}
    if json_body is not None:
        kwargs["json"] = json_body
    if params is not None:
        kwargs["params"] = params
    if content is not None:
        kwargs["content"] = content
    try:
        response = client.post(url, **kwargs)
    except Exception:
        _fail_durable("The durable preparation API request failed")
    return _validated_api_response(response, expected_status)


def _api_get_json(
    client: _PreparationApiClient,
    url: str,
    *,
    expected_status: int,
    headers: dict[str, str],
) -> dict[str, Any]:
    try:
        response = client.get(url, headers=headers)
    except Exception:
        _fail_durable("The durable reconciliation API request failed")
    return _validated_api_response(response, expected_status)


def _validated_api_response(
    response: _ResponseLike,
    expected_status: int,
) -> dict[str, Any]:
    if response.status_code != expected_status:
        safe_code: str | None = None
        try:
            candidate = response.json().get("code")
            if isinstance(candidate, str):
                safe_code = candidate
        except Exception:
            safe_code = None
        if safe_code == "SHA256_MISMATCH":
            raise SourcePackageContractError(
                "SHA256_MISMATCH",
                "The durable upload rejected its content hash",
            )
        if safe_code == "ocr_image_too_large":
            raise SourcePackageContractError(
                "ocr_image_too_large",
                "The durable upload rejected an OCR page above the admitted image bound",
            )
        _fail_durable("The durable preparation API rejected a required operation")
    try:
        body = response.json()
    except Exception:
        _fail_durable("The durable preparation API response was incomplete")
    if not isinstance(body, dict):
        _fail_durable("The durable preparation API response was invalid")
    return body


def _required_uuid(body: Mapping[str, Any], key: str) -> str:
    value = _optional_uuid(body.get(key))
    if value is None:
        _fail_durable("A durable preparation receipt omitted a required identifier")
    return value


def _validate_deal_record(
    body: Mapping[str, Any],
    *,
    company: CompanySourceContract,
) -> str:
    deal_id = _required_uuid(body, "deal_id")
    if body.get("name") != company.deal_alias or body.get("company_name") != company.company_alias:
        _fail_durable("A durable deal does not match its approved safe aliases")
    return deal_id


def _optional_uuid(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        canonical = str(uuid.UUID(value))
    except ValueError:
        return None
    return canonical if canonical == value else None


def _validate_terminal_receipt_against_admission(
    *,
    raw_receipt: object,
    admission: _TerminalContentAdmission,
    source_tree_fingerprint: str,
    source_file: SourceFileContract,
) -> Mapping[str, object]:
    """Revalidate either receipt generation against its current trust root."""

    now = datetime.now(UTC)
    if (
        isinstance(raw_receipt, Mapping)
        and raw_receipt.get("schema_version") == NO_SUBSTANTIVE_CONTENT_RECEIPT_V2_SCHEMA_VERSION
    ):
        if not isinstance(admission, VerifiedDocumentNoSubstantiveContentAttestationManifest):
            raise SourcePackageContractError(
                DOCUMENT_NO_SUBSTANTIVE_CONTENT_ATTESTATION_REQUIRED,
                "The current signed watermark manifest is required",
            )
        summary = admission.runtime_summary()
        admission.exemplar.require_active(now=now)
        if now < admission.batch.approved_at or now >= admission.batch.expires_at:
            raise DocumentNoSubstantiveContentAttestationError(
                WATERMARK_BATCH_ATTESTATION_NOT_ACTIVE
            )
        validate_document_no_substantive_content_receipt_v2(
            receipt=raw_receipt,
            verified_manifest=summary,
            tenant_id=str(summary["tenant_id"]),
            source_tree_fingerprint=source_tree_fingerprint,
            safe_alias=source_file.generated_upload_name,
            content_sha256=source_file.content_sha256,
            now=now,
        )
        return raw_receipt

    if not isinstance(admission, VerifiedDocumentNoSubstantiveContentAttestation):
        raise SourcePackageContractError(
            DOCUMENT_NO_SUBSTANTIVE_CONTENT_ATTESTATION_REQUIRED,
            "The signed one-document terminal attestation is required",
        )
    projected = project_safe_document_no_substantive_content_receipt(raw_receipt)
    if projected is None:
        raise SourcePackageContractError(
            DOCUMENT_NO_SUBSTANTIVE_CONTENT_PROOF_INCOMPLETE,
            "The confirmed terminal upload receipt is incomplete",
        )
    validate_document_no_substantive_content_receipt(
        receipt=projected,
        attestation=admission,
        tenant_id=admission.tenant_id,
        source_tree_fingerprint=source_tree_fingerprint,
        safe_alias=source_file.generated_upload_name,
        content_sha256=source_file.content_sha256,
        now=now,
    )
    return projected


def _validated_upload_receipt(
    *,
    response: Mapping[str, Any],
    deal_id: str,
    source_file: SourceFileContract,
    source_tree_fingerprint: str = "",
    no_substantive_content_attestation: _TerminalContentAdmission | None = None,
) -> dict[str, Any]:
    if _optional_uuid(response.get("deal_id")) != deal_id:
        _fail_reconciliation("A document upload receipt crossed deal scope")
    parse_status = response.get("parse_status")
    metadata = response.get("metadata")
    if isinstance(metadata, Mapping) and any(
        str(key)
        .lower()
        .startswith(("document_illegibility", "document_no_substantive_content_attestation"))
        for key in metadata
    ):
        _fail_reconciliation("A document upload carried a superseded or private attestation field")
    raw_receipt = (
        metadata.get("document_no_substantive_content_receipt")
        if isinstance(metadata, Mapping)
        else None
    )
    if parse_status == "PARSED":
        if response.get("terminal_disposition") is not None:
            _fail_reconciliation("A parsed upload carried a contradictory terminal receipt")
        if raw_receipt is not None:
            if no_substantive_content_attestation is None:
                raise SourcePackageContractError(
                    DOCUMENT_NO_SUBSTANTIVE_CONTENT_ATTESTATION_REQUIRED,
                    "The current signed watermark manifest is required",
                )
            try:
                validated_receipt = _validate_terminal_receipt_against_admission(
                    raw_receipt=raw_receipt,
                    admission=no_substantive_content_attestation,
                    source_tree_fingerprint=source_tree_fingerprint,
                    source_file=source_file,
                )
            except DocumentNoSubstantiveContentAttestationError as error:
                raise SourcePackageContractError(
                    error.code,
                    "The page-level watermark receipt was not admitted",
                ) from None
            if (
                validated_receipt.get("schema_version")
                != NO_SUBSTANTIVE_CONTENT_RECEIPT_V2_SCHEMA_VERSION
                or validated_receipt.get("whole_document_terminal") is not False
            ):
                _fail_reconciliation("A parsed upload carried a contradictory terminal receipt")
    elif (
        parse_status == "FAILED"
        and response.get("terminal_disposition") == DOCUMENT_NO_SUBSTANTIVE_CONTENT_CONFIRMED
    ):
        if no_substantive_content_attestation is None:
            raise SourcePackageContractError(
                DOCUMENT_NO_SUBSTANTIVE_CONTENT_ATTESTATION_REQUIRED,
                "The signed one-document terminal attestation is required",
            )
        try:
            validated_receipt = _validate_terminal_receipt_against_admission(
                raw_receipt=raw_receipt,
                admission=no_substantive_content_attestation,
                source_tree_fingerprint=source_tree_fingerprint,
                source_file=source_file,
            )
        except DocumentNoSubstantiveContentAttestationError as error:
            raise SourcePackageContractError(
                error.code,
                "The confirmed terminal upload receipt was not admitted",
            ) from None
        if (
            validated_receipt.get("schema_version")
            == NO_SUBSTANTIVE_CONTENT_RECEIPT_V2_SCHEMA_VERSION
            and validated_receipt.get("whole_document_terminal") is not True
        ):
            _fail_reconciliation("A failed upload carried a non-terminal watermark receipt")
    else:
        _fail_durable("A document upload did not produce an admitted terminal state")
    response_hash = response.get("sha256")
    if response_hash != source_file.content_sha256:
        raise SourcePackageContractError(
            "SHA256_MISMATCH",
            "A durable upload receipt does not match its source content hash",
        )
    if not _source_metadata_matches(response, source_file):
        _fail_reconciliation("A document upload receipt does not match its source binding")
    document_id = _required_uuid(response, "document_id")
    doc_id = _required_uuid(response, "doc_id")
    return {
        **source_file.safe_dict(),
        "document_id": document_id,
        "doc_id": doc_id,
    }


def _source_metadata_matches(record: Mapping[str, Any], source_file: SourceFileContract) -> bool:
    metadata = record.get("source_metadata")
    return (
        isinstance(metadata, Mapping)
        and metadata.get("upload_filename") == source_file.generated_upload_name
        and type(metadata.get("size_bytes")) is int
        and metadata.get("size_bytes") == source_file.size_bytes
    )


def _reconcile_completed_upload(
    *,
    api_client: _PreparationApiClient,
    headers: dict[str, str],
    deal_id: str,
    source_file: SourceFileContract,
    receipt: Mapping[str, Any],
    source_tree_fingerprint: str,
    no_substantive_content_attestation: _TerminalContentAdmission | None,
) -> None:
    document_id = str(receipt["document_id"])
    record = _api_get_json(
        api_client,
        f"/v1/deals/{deal_id}/documents/{document_id}",
        expected_status=200,
        headers=headers,
    )
    validated = _validated_upload_receipt(
        response=record,
        deal_id=deal_id,
        source_file=source_file,
        source_tree_fingerprint=source_tree_fingerprint,
        no_substantive_content_attestation=no_substantive_content_attestation,
    )
    if validated["document_id"] != document_id or validated["doc_id"] != receipt["doc_id"]:
        _fail_durable("A resumed document does not match its durable receipt")


def _attestation_matches_source(
    contract: TwoCompanySourceContract,
    source_file: SourceFileContract,
    attestation: _TerminalContentAdmission | None,
) -> bool:
    if isinstance(attestation, VerifiedDocumentNoSubstantiveContentAttestationManifest):
        summary = attestation.runtime_summary()
        candidates = summary.get("candidates")
        return (
            summary.get("source_tree_fingerprint") == contract.source_tree_fingerprint
            and isinstance(candidates, list)
            and any(
                isinstance(candidate, Mapping)
                and candidate.get("safe_alias") == source_file.generated_upload_name
                and candidate.get("content_sha256") == source_file.content_sha256
                for candidate in candidates
            )
        )
    return (
        isinstance(attestation, VerifiedDocumentNoSubstantiveContentAttestation)
        and attestation.source_tree_fingerprint == contract.source_tree_fingerprint
        and attestation.safe_alias == source_file.generated_upload_name
        and attestation.content_sha256 == source_file.content_sha256
    )


def _require_unique_attestation_source_binding(
    contract: TwoCompanySourceContract,
    attestation: _TerminalContentAdmission | None,
) -> None:
    if attestation is None:
        return
    if isinstance(attestation, VerifiedDocumentNoSubstantiveContentAttestationManifest):
        summary = attestation.runtime_summary()
        candidates = summary.get("candidates")
        if (
            summary.get("source_tree_fingerprint") != contract.source_tree_fingerprint
            or not isinstance(candidates, list)
            or not candidates
        ):
            raise SourcePackageContractError(
                "DOCUMENT_NO_SUBSTANTIVE_CONTENT_ATTESTATION_MISMATCH",
                "The signed watermark manifest does not bind this source tree",
            )
        source_identities = {
            (source_file.generated_upload_name, source_file.content_sha256)
            for company in contract.companies
            for source_file in company.files
        }
        candidate_identities = [
            (
                candidate.get("safe_alias"),
                candidate.get("content_sha256"),
                candidate.get("page_number"),
            )
            for candidate in candidates
            if isinstance(candidate, Mapping)
        ]
        if (
            len(candidate_identities) != len(candidates)
            or len(set(candidate_identities)) != len(candidate_identities)
            or any(identity[:2] not in source_identities for identity in candidate_identities)
        ):
            raise SourcePackageContractError(
                "DOCUMENT_NO_SUBSTANTIVE_CONTENT_ATTESTATION_MISMATCH",
                "The signed watermark manifest contains an unbound source row",
            )
        return
    matches = [
        source_file
        for company in contract.companies
        for source_file in company.files
        if _attestation_matches_source(contract, source_file, attestation)
    ]
    if len(matches) != 1:
        raise SourcePackageContractError(
            "DOCUMENT_NO_SUBSTANTIVE_CONTENT_ATTESTATION_MISMATCH",
            "The signed terminal attestation does not bind one source row",
        )


def _assert_source_file_unchanged(
    contract: TwoCompanySourceContract,
    company: CompanySourceContract,
    source_file: SourceFileContract,
) -> None:
    path = contract.source_path(company.company_alias, source_file.sequence)
    _assert_safe_source_path(path, contract.root)
    try:
        current_size = path.stat().st_size
        current_hash = _hash_source_file(path)
    except OSError:
        raise SourcePackageContractError(
            "SOURCE_TREE_MUTATED",
            "A source file became unavailable during preparation",
        ) from None
    if current_size != source_file.size_bytes or current_hash != source_file.content_sha256:
        raise SourcePackageContractError(
            "SOURCE_TREE_MUTATED",
            "A source file changed during preparation",
        )


def _assert_safe_source_path(path: Path, root: Path) -> None:
    try:
        resolved = path.resolve(strict=True)
    except OSError:
        raise SourcePackageContractError(
            "SOURCE_TREE_MUTATED",
            "A source entry became unavailable during preparation",
        ) from None
    if _is_link_or_reparse(path) or not resolved.is_relative_to(root) or not path.is_file():
        raise SourcePackageContractError(
            "SOURCE_TREE_MUTATED",
            "A source entry changed type or escaped its approved root",
        )


def _iter_source_bytes(path: Path, root: Path) -> Iterator[bytes]:
    _assert_safe_source_path(path, root)
    try:
        with path.open("rb") as stream:
            while chunk := stream.read(HASH_CHUNK_SIZE_BYTES):
                yield chunk
    except OSError:
        raise SourcePackageContractError(
            "SOURCE_TREE_MUTATED",
            "A source file became unreadable during upload",
        ) from None


def _source_package_manifest(
    company: CompanySourceContract,
    contract: TwoCompanySourceContract,
    uploads: Sequence[Mapping[str, Any]],
) -> Any:
    from idis.models.source_package_manifest import (
        SOURCE_PACKAGE_MANIFEST_SCHEMA_VERSION,
        SourcePackageManifest,
        canonical_source_manifest_sha256,
    )

    if len(uploads) != len(company.files):
        _fail_reconciliation("A source company is missing durable upload receipts")
    payload: dict[str, Any] = {
        "schema_version": SOURCE_PACKAGE_MANIFEST_SCHEMA_VERSION,
        "company_alias": company.company_alias,
        "deal_alias": company.deal_alias,
        "document_set_alias": company.document_set_alias,
        "run_alias": company.run_alias,
        "ledger_alias": company.ledger_alias,
        "package_alias": company.package_alias,
        "source_tree_fingerprint": contract.source_tree_fingerprint,
        "file_count": company.file_count,
        "total_bytes": company.total_bytes,
        "files": [
            {
                **source_file.safe_dict(),
                "document_id": receipt["document_id"],
                "doc_id": receipt["doc_id"],
            }
            for source_file, receipt in zip(company.files, uploads, strict=True)
        ],
    }
    payload["manifest_sha256"] = canonical_source_manifest_sha256(payload)
    try:
        return SourcePackageManifest.model_validate(payload)
    except ValueError:
        _fail_reconciliation("The durable source manifest failed validation")


def _validate_durable_package_record(
    *,
    record: Mapping[str, Any],
    deal_id: str,
    package_id: str,
    manifest_payload: Mapping[str, Any],
) -> None:
    _validate_durable_package_header(
        record=record,
        deal_id=deal_id,
        package_id=package_id,
        manifest_payload=manifest_payload,
    )
    files = record.get("files")
    expected_files = manifest_payload["files"]
    if not isinstance(files, list) or not isinstance(expected_files, list):
        _fail_durable("The durable package file ledger is incomplete")
    if len(files) != len(expected_files):
        _fail_durable("The durable package file ledger cardinality has drifted")
    status = record.get("status")
    if status not in {"OPEN", "SEALED"}:
        _fail_durable("The durable package lifecycle state is invalid")
    for actual, expected in zip(files, expected_files, strict=True):
        if not isinstance(actual, Mapping) or not isinstance(expected, Mapping):
            _fail_durable("The durable package file ledger is invalid")
        required = {
            "sequence": expected["sequence"],
            "path_hash": expected["source_locator_sha256"],
            "sha256": expected["content_sha256"],
            "extension": expected["extension"],
        }
        if any(actual.get(key) != value for key, value in required.items()):
            _fail_durable("The durable package file ledger does not match its manifest")
        exact_pair = (
            actual.get("document_id") == expected["document_id"]
            and actual.get("doc_id") == expected["doc_id"]
        )
        staged_pair = (
            status == "OPEN" and actual.get("document_id") is None and actual.get("doc_id") is None
        )
        if not (exact_pair or staged_pair):
            _fail_durable("The durable package file ledger does not match its manifest")


def _validate_durable_package_header(
    *,
    record: Mapping[str, Any],
    deal_id: str,
    package_id: str,
    manifest_payload: Mapping[str, Any],
) -> None:
    if (
        _optional_uuid(record.get("deal_id")) != deal_id
        or _optional_uuid(record.get("package_id")) != package_id
        or record.get("file_count") != manifest_payload["file_count"]
    ):
        _fail_durable("The durable package header does not match its source manifest")
    manifest_header = record.get("source_manifest")
    if not isinstance(manifest_header, Mapping):
        _fail_durable("The durable package omitted its source-manifest proof")
    for key in (
        "schema_version",
        "company_alias",
        "deal_alias",
        "document_set_alias",
        "run_alias",
        "ledger_alias",
        "package_alias",
        "source_tree_fingerprint",
        "manifest_sha256",
        "file_count",
        "total_bytes",
    ):
        if manifest_header.get(key) != manifest_payload[key]:
            _fail_durable("The durable package source-manifest proof has drifted")


def _preparation_safe_summary(ledger: Mapping[str, Any]) -> dict[str, Any]:
    companies = ledger["companies"]
    assert isinstance(companies, list)
    return {
        "schema_version": "slice100-source-preparation-summary-v1",
        "source_tree_fingerprint": ledger["source_tree_fingerprint"],
        "source_root_locator_sha256": ledger["source_root_locator_sha256"],
        "total_file_count": ledger["total_file_count"],
        "total_bytes": ledger["total_bytes"],
        "companies": [
            {
                "company_alias": state["company_alias"],
                "deal_alias": state["deal_alias"],
                "document_set_alias": state["document_set_alias"],
                "run_alias": state["run_alias"],
                "ledger_alias": state["ledger_alias"],
                "package_alias": state["package_alias"],
                "source_company_locator_sha256": state["source_company_locator_sha256"],
                "source_contract_sha256": state["source_contract_sha256"],
                "deal_id": state["deal_id"],
                "package_id": state["package_id"],
                "file_count": state["file_count"],
                "total_bytes": state["total_bytes"],
                "manifest_sha256": state["manifest_sha256"],
            }
            for state in companies
        ],
    }


def _fail_reconciliation(message: str) -> Never:
    raise SourcePackageContractError("DATA_ROOM_RECONCILIATION_INCOMPLETE", message)


def _fail_durable(message: str) -> Never:
    raise SourcePackageContractError("DATA_ROOM_DURABLE_HANDOFF_INCOMPLETE", message)
