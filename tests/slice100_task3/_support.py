"""Hermetic source trees and an external-API-shaped fake for Task 3."""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class SourceFixture:
    root: Path
    expected_file_counts: tuple[int, int]
    expected_company_bytes: tuple[int, int]
    expected_total_files: int
    expected_total_bytes: int
    private_tokens: tuple[str, ...]
    first_source_file: Path


def build_source_fixture(
    tmp_path: Path,
    *,
    files_by_sorted_company: tuple[tuple[tuple[str, bytes], ...], ...] = (
        (
            ("private-alpha-board.pdf", b"alpha-board-private"),
            ("nested/private-alpha-model.xlsx", b"alpha-model-private"),
        ),
        (
            ("private-zulu-report.docx", b"zulu-report-private"),
            ("private-zulu-notes.txt", b"zulu-notes-private"),
        ),
    ),
) -> SourceFixture:
    root = tmp_path / "approved-private-root"
    root.mkdir()
    private_folder_names = ("alpha-private-target", "zulu-private-acquisition")
    private_tokens: list[str] = [root.name, *private_folder_names]
    company_bytes: list[int] = []
    first_source_file: Path | None = None
    for folder_name, rows in zip(private_folder_names, files_by_sorted_company, strict=True):
        folder = root / folder_name
        folder.mkdir()
        total_bytes = 0
        for relative_name, content in rows:
            path = folder / relative_name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
            first_source_file = first_source_file or path
            private_tokens.extend(part for part in Path(relative_name).parts if part)
            total_bytes += len(content)
        company_bytes.append(total_bytes)
    assert first_source_file is not None
    counts = tuple(len(rows) for rows in files_by_sorted_company)
    return SourceFixture(
        root=root,
        expected_file_counts=(int(counts[0]), int(counts[1])),
        expected_company_bytes=(company_bytes[0], company_bytes[1]),
        expected_total_files=sum(counts),
        expected_total_bytes=sum(company_bytes),
        private_tokens=tuple(private_tokens),
        first_source_file=first_source_file,
    )


def contract_kwargs(fixture: SourceFixture) -> dict[str, Any]:
    return {
        "root": fixture.root,
        "expected_company_count": 2,
        "expected_file_counts": fixture.expected_file_counts,
        "expected_total_files": fixture.expected_total_files,
        "expected_company_bytes": fixture.expected_company_bytes,
        "expected_total_bytes": fixture.expected_total_bytes,
    }


class FakeResponse:
    def __init__(
        self,
        status_code: int,
        body: dict[str, Any],
        *,
        fail_json_once: bool = False,
    ) -> None:
        self.status_code = status_code
        self._body = body
        self._fail_json_once = fail_json_once

    def json(self) -> dict[str, Any]:
        if self._fail_json_once:
            self._fail_json_once = False
            raise ConnectionError("simulated disconnect after durable commit")
        return self._body


@dataclass
class FakeApiState:
    deals: dict[str, dict[str, Any]] = field(default_factory=dict)
    documents: dict[str, dict[str, Any]] = field(default_factory=dict)
    packages: dict[str, dict[str, Any]] = field(default_factory=dict)
    idempotency: dict[str, tuple[str, int, dict[str, Any]]] = field(default_factory=dict)


class FakePreparationApi:
    """HTTP-client-shaped fake retaining durable state across resume attempts."""

    def __init__(
        self,
        *,
        state: FakeApiState | None = None,
        same_deal_for_both: bool = False,
        same_package_for_both: bool = False,
        deal_alias_mismatch: bool = False,
        reuse_first_document_cross_company: bool = False,
        reuse_first_artifact_cross_company: bool = False,
        package_deal_mismatch: bool = False,
        package_alias_mismatch: bool = False,
        package_hash_mismatch: bool = False,
        upload_hash_missing: bool = False,
        upload_hash_mismatch: bool = False,
        fail_after_successful_uploads: int | None = None,
        crash_after_committed_uploads: int | None = None,
        crash_after_committed_deals: int | None = None,
        crash_after_committed_packages: int | None = None,
        mutate_after_first_upload: Any | None = None,
    ) -> None:
        self.state = state or FakeApiState()
        self.same_deal_for_both = same_deal_for_both
        self.same_package_for_both = same_package_for_both
        self.deal_alias_mismatch = deal_alias_mismatch
        self.reuse_first_document_cross_company = reuse_first_document_cross_company
        self.reuse_first_artifact_cross_company = reuse_first_artifact_cross_company
        self.package_deal_mismatch = package_deal_mismatch
        self.package_alias_mismatch = package_alias_mismatch
        self.package_hash_mismatch = package_hash_mismatch
        self.upload_hash_missing = upload_hash_missing
        self.upload_hash_mismatch = upload_hash_mismatch
        self.fail_after_successful_uploads = fail_after_successful_uploads
        self.crash_after_committed_uploads = crash_after_committed_uploads
        self.crash_after_committed_deals = crash_after_committed_deals
        self.crash_after_committed_packages = crash_after_committed_packages
        self.mutate_after_first_upload = mutate_after_first_upload
        self.calls: list[dict[str, Any]] = []
        self.successful_uploads = 0
        self.successful_deals = 0
        self.successful_packages = 0
        self._failed_once = False
        self._reused_artifact_once = False

    def post(self, url: str, **kwargs: Any) -> FakeResponse:
        self._record("POST", url, kwargs)
        if url == "/v1/deals":
            fingerprint = self._request_fingerprint(url=url, json_body=kwargs["json"])
            replay = self._idempotency_replay(kwargs, fingerprint)
            if replay is not None:
                return replay
            company_alias = str(kwargs["json"]["company_name"])
            deal_id = (
                next(iter(self.state.deals))
                if self.same_deal_for_both and self.state.deals
                else str(uuid.uuid4())
            )
            requested_name = str(kwargs["json"]["name"])
            response_name = "deal-999" if self.deal_alias_mismatch else requested_name
            response_company = "company-999" if self.deal_alias_mismatch else company_alias
            self.state.deals.setdefault(
                deal_id,
                {
                    "deal_id": deal_id,
                    "name": requested_name,
                    "company_name": company_alias,
                    "status": "NEW",
                },
            )
            response_body = {
                "deal_id": deal_id,
                "name": response_name,
                "company_name": response_company,
                "status": "NEW",
            }
            self._idempotency_commit(kwargs, fingerprint, 201, response_body)
            self.successful_deals += 1
            crash = self.crash_after_committed_deals == self.successful_deals
            if crash:
                self.crash_after_committed_deals = None
            return FakeResponse(201, response_body, fail_json_once=crash)

        upload_match = re.fullmatch(r"/v1/deals/([^/]+)/documents/upload", url)
        if upload_match:
            if (
                self.fail_after_successful_uploads is not None
                and self.successful_uploads >= self.fail_after_successful_uploads
                and not self._failed_once
            ):
                self._failed_once = True
                return FakeResponse(503, {"code": "SERVICE_UNAVAILABLE"})
            deal_id = upload_match.group(1)
            content = kwargs["content"]
            body = content if isinstance(content, bytes) else b"".join(content)
            actual_hash = hashlib.sha256(body).hexdigest()
            expected_hash = str(kwargs["params"]["sha256"])
            if actual_hash != expected_hash:
                return FakeResponse(400, {"code": "SHA256_MISMATCH"})
            fingerprint = self._request_fingerprint(
                url=url,
                params=kwargs["params"],
                content_sha256=actual_hash,
            )
            replay = self._idempotency_replay(kwargs, fingerprint)
            if replay is not None:
                return replay
            first_document = next(iter(self.state.documents.values()), None)
            if self.reuse_first_document_cross_company and first_document is not None:
                first_deal = str(first_document["deal_id"])
                if first_deal != deal_id:
                    response_body = dict(first_document)
                    self._idempotency_commit(kwargs, fingerprint, 201, response_body)
                    return FakeResponse(201, response_body)
            reuse_artifact = (
                self.reuse_first_artifact_cross_company
                and not self._reused_artifact_once
                and first_document is not None
                and str(first_document["deal_id"]) != deal_id
            )
            document_id = str(uuid.uuid4())
            row = {
                "deal_id": deal_id,
                "document_id": document_id,
                "doc_id": (str(first_document["doc_id"]) if reuse_artifact else str(uuid.uuid4())),
                "sha256": expected_hash,
                "parse_status": "PARSED",
                "source_metadata": {
                    "upload_filename": str(kwargs["params"]["filename"]),
                    "size_bytes": len(body),
                },
            }
            self._reused_artifact_once = self._reused_artifact_once or reuse_artifact
            self.state.documents[document_id] = row
            self.successful_uploads += 1
            if self.successful_uploads == 1 and self.mutate_after_first_upload is not None:
                self.mutate_after_first_upload()
            crash = self.crash_after_committed_uploads == self.successful_uploads
            if crash:
                self.crash_after_committed_uploads = None
            response_row = dict(row)
            if self.upload_hash_missing:
                response_row.pop("sha256")
            elif self.upload_hash_mismatch:
                response_row["sha256"] = "f" * 64
            self._idempotency_commit(kwargs, fingerprint, 201, response_row)
            return FakeResponse(201, response_row, fail_json_once=crash)

        package_match = re.fullmatch(r"/v1/deals/([^/]+)/data-room-packages", url)
        if package_match:
            deal_id = package_match.group(1)
            request = kwargs["json"]
            manifest = request["source_manifest"]
            fingerprint = self._request_fingerprint(url=url, json_body=request)
            replay = self._idempotency_replay(kwargs, fingerprint)
            if replay is not None:
                return replay
            package_id = (
                next(iter(self.state.packages))
                if self.same_package_for_both and self.state.packages
                else str(uuid.uuid4())
            )
            response_deal_id = (
                str(uuid.uuid5(uuid.NAMESPACE_URL, "task3-wrong-deal"))
                if self.package_deal_mismatch
                else deal_id
            )
            files: list[dict[str, Any]] = []
            for binding in manifest["files"]:
                digest = str(binding["content_sha256"])
                if self.package_hash_mismatch and not files:
                    digest = "f" * 64
                files.append(
                    {
                        "sequence": binding["sequence"],
                        "path_hash": binding["source_locator_sha256"],
                        "sha256": digest,
                        "extension": binding["extension"],
                        "document_id": binding["document_id"],
                        "doc_id": binding["doc_id"],
                    }
                )
            record = {
                "package_id": package_id,
                "deal_id": response_deal_id,
                "status": "OPEN",
                "file_count": len(files),
                "source_manifest": {
                    key: manifest[key]
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
                    )
                },
                "files": files,
            }
            if self.package_alias_mismatch:
                record["source_manifest"]["ledger_alias"] = "ledger-999"
            self.state.packages[package_id] = record
            response_body = {key: value for key, value in record.items() if key != "files"}
            self._idempotency_commit(kwargs, fingerprint, 201, response_body)
            self.successful_packages += 1
            crash = self.crash_after_committed_packages == self.successful_packages
            if crash:
                self.crash_after_committed_packages = None
            return FakeResponse(201, response_body, fail_json_once=crash)

        raise AssertionError(f"unexpected POST route shape: {url}")

    def get(self, url: str, **kwargs: Any) -> FakeResponse:
        self._record("GET", url, kwargs)
        deal_match = re.fullmatch(r"/v1/deals/([^/]+)", url)
        if deal_match:
            row = self.state.deals.get(deal_match.group(1))
            if row is None:
                return FakeResponse(404, {"code": "NOT_FOUND"})
            return FakeResponse(200, dict(row))

        document_match = re.fullmatch(r"/v1/deals/([^/]+)/documents/([^/]+)", url)
        if document_match:
            deal_id, document_id = document_match.groups()
            row = self.state.documents.get(document_id)
            if row is None or row["deal_id"] != deal_id:
                return FakeResponse(404, {"code": "NOT_FOUND"})
            return FakeResponse(200, dict(row))

        package_match = re.fullmatch(
            r"/v1/deals/([^/]+)/data-room-packages/([^/]+)",
            url,
        )
        if package_match:
            deal_id, package_id = package_match.groups()
            row = self.state.packages.get(package_id)
            if row is None or row["deal_id"] != deal_id:
                return FakeResponse(404, {"code": "DATA_ROOM_PACKAGE_NOT_FOUND"})
            return FakeResponse(200, dict(row))
        raise AssertionError(f"unexpected GET route shape: {url}")

    def _record(self, method: str, url: str, kwargs: dict[str, Any]) -> None:
        idempotency_key = (kwargs.get("headers") or {}).get("Idempotency-Key")
        self.calls.append(
            {
                "method": method,
                "url": url,
                "json": kwargs.get("json"),
                "params": kwargs.get("params"),
                "header_names": sorted((kwargs.get("headers") or {}).keys()),
                "idempotency_key_sha256": (
                    hashlib.sha256(str(idempotency_key).encode("utf-8")).hexdigest()
                    if idempotency_key is not None
                    else None
                ),
            }
        )

    def _idempotency_replay(
        self,
        kwargs: dict[str, Any],
        fingerprint: str,
    ) -> FakeResponse | None:
        key = str((kwargs.get("headers") or {}).get("Idempotency-Key") or "")
        if not key:
            return FakeResponse(400, {"code": "IDEMPOTENCY_KEY_REQUIRED"})
        existing = self.state.idempotency.get(key)
        if existing is None:
            return None
        existing_fingerprint, status, body = existing
        if existing_fingerprint != fingerprint:
            return FakeResponse(409, {"code": "IDEMPOTENCY_KEY_REUSED"})
        return FakeResponse(status, dict(body))

    def _idempotency_commit(
        self,
        kwargs: dict[str, Any],
        fingerprint: str,
        status: int,
        body: dict[str, Any],
    ) -> None:
        key = str((kwargs.get("headers") or {}).get("Idempotency-Key") or "")
        if not key:
            raise AssertionError("mutation omitted Idempotency-Key")
        self.state.idempotency[key] = (fingerprint, status, dict(body))

    @staticmethod
    def _request_fingerprint(
        *,
        url: str,
        json_body: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        content_sha256: str | None = None,
    ) -> str:
        payload = {
            "url": url,
            "json": json_body,
            "params": params,
            "content_sha256": content_sha256,
        }
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
