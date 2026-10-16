"""Durable tenant-scoped CDD/FDD methodology persistence.

The repository stores canonical question payloads, never workbook paths or
private source names.  Import is idempotent for byte-for-byte equivalent
registries, freeze revalidates the authoritative source-bound registry, and a
run selection can bind only one frozen CDD plus one frozen FDD version.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Never

from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from idis.methodology.ids import normalize_text
from idis.methodology.models import (
    MethodologyQuestion,
    MethodologyRegistry,
    MethodologyType,
    MethodologyVersion,
)
from idis.models.methodology_coverage import MethodologyCoverageRecord
from idis.persistence.db import set_tenant_local

METHODOLOGY_COVERAGE_INCOMPLETE = "METHODOLOGY_COVERAGE_INCOMPLETE"
_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)
_SAFE_ALIAS_RE = re.compile(r"[a-z0-9][a-z0-9._-]{0,127}\Z", re.ASCII)
_EXPECTED_COUNTS = {
    MethodologyType.COMMERCIAL_DD: 21,
    MethodologyType.FINANCIAL_DD: 79,
}
_EXPECTED_SOURCE_TYPES = {
    MethodologyType.COMMERCIAL_DD: "checked_in_template",
    MethodologyType.FINANCIAL_DD: "operator_workbook",
}


class MethodologiesRepositoryError(RuntimeError):
    """Static fail-closed error for invalid or unavailable durable state."""

    __slots__ = ("code",)

    def __init__(self) -> None:
        self.code = METHODOLOGY_COVERAGE_INCOMPLETE
        super().__init__(self.code)


def _fail() -> Never:
    raise MethodologiesRepositoryError


@dataclass(frozen=True, slots=True)
class MethodologyVersionRecord:
    """Safe metadata and revalidated canonical registry for one durable version."""

    tenant_id: str
    methodology_version_record_id: str
    methodology_version_id: str
    methodology_id: str
    methodology_type: MethodologyType
    version_label: str
    source_type: str
    source_alias: str
    source_sha256: str
    source_byte_count: int
    source_contract_hash: str
    source_trace_digest: str
    registry_hash: str
    version_hash: str
    question_count: int
    source_section_count: int
    header_row: int | None
    status: str
    imported_at: datetime
    frozen_at: datetime | None
    registry: MethodologyRegistry


@dataclass(frozen=True, slots=True)
class RunMethodologySelection:
    """Immutable two-methodology snapshot consumed by FULL-run initialization."""

    selection_id: str
    tenant_id: str
    deal_id: str
    run_id: str
    commercial_dd_version_record_id: str
    financial_dd_version_record_id: str
    selected_version_ids: tuple[str, str]
    selected_source_hashes: tuple[str, str]
    selected_registry_hashes: tuple[str, str]
    selected_version_hashes: tuple[str, str]
    selection_hash: str
    selected_question_count: int
    state: str = "FROZEN"
    immutable: bool = True


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _sha256_json(value: object) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _selection_hash(
    *,
    tenant_id: str,
    deal_id: str,
    run_id: str,
    commercial: MethodologyVersionRecord,
    financial: MethodologyVersionRecord,
    selected_question_count: int,
) -> str:
    """Bind one exact source/registry/version pair to one exact run scope."""
    return _sha256_json(
        {
            "tenant_id": tenant_id,
            "deal_id": deal_id,
            "run_id": run_id,
            "commercial_dd_version_record_id": commercial.methodology_version_record_id,
            "commercial_methodology_version_id": commercial.methodology_version_id,
            "commercial_source_sha256": commercial.source_sha256,
            "commercial_registry_hash": commercial.registry_hash,
            "commercial_version_hash": commercial.version_hash,
            "financial_dd_version_record_id": financial.methodology_version_record_id,
            "financial_methodology_version_id": financial.methodology_version_id,
            "financial_source_sha256": financial.source_sha256,
            "financial_registry_hash": financial.registry_hash,
            "financial_version_hash": financial.version_hash,
            "selected_question_count": selected_question_count,
        }
    )


def methodology_version_hash(registry: MethodologyRegistry) -> str:
    """Return the canonical hash of the registry's sole version payload."""
    validated = _validated_registry(registry)
    return _sha256_json(validated.current_version.model_dump(mode="json"))


def methodology_question_payload_hashes(
    registry: MethodologyRegistry,
) -> tuple[str, ...]:
    """Return ordered hashes for every complete canonical question payload."""
    validated = _validated_registry(registry)
    return tuple(
        _sha256_json(question.model_dump(mode="json"))
        for question in validated.current_version.questions
    )


def methodology_source_trace_digest(registry: MethodologyRegistry) -> str:
    """Return the canonical ordered digest of all source trace records."""
    validated = _validated_registry(registry)
    traces = [
        {
            "methodology_question_id": question.methodology_question_id,
            "source_trace": question.source_trace.model_dump(mode="json"),
        }
        for question in validated.current_version.questions
    ]
    return _sha256_json(traces)


def _validated_registry(registry: MethodologyRegistry) -> MethodologyRegistry:
    try:
        validated = MethodologyRegistry.model_validate(registry.model_dump(mode="json"))
    except (AttributeError, TypeError, ValueError):
        _fail()
    if len(validated.versions) != 1:
        _fail()
    version = validated.current_version
    expected_count = _EXPECTED_COUNTS.get(validated.methodology_type)
    source_type = _EXPECTED_SOURCE_TYPES.get(validated.methodology_type)
    if (
        validated.methodology_id != validated.methodology_type.value
        or version.methodology_id != validated.methodology_id
        or len(version.questions) != expected_count
        or any(question.source_trace.source_type != source_type for question in version.questions)
        or any(
            question.source_trace.source_hash != version.source_hash
            for question in version.questions
        )
    ):
        _fail()
    return validated


def _validated_uuid(value: str) -> str:
    try:
        parsed = uuid.UUID(str(value))
    except (TypeError, ValueError, AttributeError):
        _fail()
    return str(parsed)


def _validated_nonblank(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        _fail()
    return value.strip()


def _validated_hash(value: str) -> str:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        _fail()
    return value


def _validated_alias(value: str) -> str:
    cleaned = _validated_nonblank(value)
    if (
        _SAFE_ALIAS_RE.fullmatch(cleaned) is None
        or "/" in cleaned
        or "\\" in cleaned
        or cleaned.lower().endswith((".xlsx", ".xls", ".json"))
    ):
        _fail()
    return cleaned


def _semantic_row_hash(question: MethodologyQuestion) -> str:
    payload = {
        "methodology_type": question.methodology_type.value,
        "section": normalize_text(question.section),
        "sheet_or_source_section": normalize_text(question.sheet_or_source_section or ""),
        "term": normalize_text(question.term or ""),
        "nature": normalize_text(question.nature or ""),
        "line_item": normalize_text(question.line_item or ""),
        "question_text": normalize_text(question.question_text),
    }
    return _sha256_json(payload)


def _row_value(row: Any, name: str) -> Any:
    mapping = getattr(row, "_mapping", row)
    if not isinstance(mapping, Mapping):
        _fail()
    return mapping[name]


class PostgresMethodologiesRepository:
    """PostgreSQL-only methodology repository; no in-memory fallback exists."""

    is_durable = True
    backend = "postgresql"

    def __init__(self, conn: Connection, tenant_id: str) -> None:
        self._conn = conn
        self._tenant_id = _validated_uuid(tenant_id)
        set_tenant_local(conn, self._tenant_id)

    def import_registry(
        self,
        *,
        registry: MethodologyRegistry,
        source_type: str,
        source_alias: str,
        source_byte_count: int,
        source_contract_hash: str,
        source_trace_digest: str,
        imported_by_actor_id: str,
        import_audit_event_id: str,
    ) -> MethodologyVersionRecord:
        """Atomically import canonical questions, or return the exact prior import."""
        validated = _validated_registry(registry)
        version = validated.current_version
        expected_source_type = _EXPECTED_SOURCE_TYPES[validated.methodology_type]
        safe_alias = _validated_alias(source_alias)
        if (
            source_type != expected_source_type
            or version.source_name != safe_alias
            or any(
                question.source_trace.source_name != safe_alias for question in version.questions
            )
            or not isinstance(source_byte_count, int)
            or isinstance(source_byte_count, bool)
            or source_byte_count <= 0
        ):
            _fail()
        contract_hash = _validated_hash(source_contract_hash)
        trace_digest = _validated_hash(source_trace_digest)
        if trace_digest != methodology_source_trace_digest(validated):
            _fail()
        source_hash = _validated_hash(version.source_hash)
        actor_id = _validated_nonblank(imported_by_actor_id)
        audit_id = _validated_uuid(import_audit_event_id)
        registry_hash = _validated_hash(validated.registry_hash)
        version_hash = methodology_version_hash(validated)
        source_sections = {q.source_trace.sheet_or_section for q in version.questions}
        header_row = 2 if validated.methodology_type is MethodologyType.FINANCIAL_DD else None
        record_id = str(
            uuid.uuid5(
                uuid.NAMESPACE_URL,
                "idis:methodology-version:"
                f"{self._tenant_id}:{version.methodology_version_id}:{source_hash}",
            )
        )

        existing = self._find_version_by_external_id(version.methodology_version_id)
        if existing is not None:
            if not self._same_import(
                existing,
                registry=validated,
                source_type=source_type,
                source_alias=safe_alias,
                source_byte_count=source_byte_count,
                source_contract_hash=contract_hash,
                source_trace_digest=trace_digest,
                registry_hash=registry_hash,
                version_hash=version_hash,
                source_section_count=len(source_sections),
                header_row=header_row,
            ):
                _fail()
            return existing

        try:
            with self._conn.begin_nested():
                self._conn.execute(
                    text(
                        """
                        INSERT INTO methodology_versions (
                            tenant_id, methodology_version_record_id,
                            methodology_version_id, methodology_id, methodology_type,
                            version_label, source_type, source_alias, source_sha256,
                            source_byte_count, source_contract_hash, source_trace_digest,
                            registry_hash, version_hash, question_count,
                            source_section_count, header_row, status,
                            imported_by_actor_id, import_audit_event_id
                        ) VALUES (
                            CAST(:tenant_id AS uuid), CAST(:record_id AS uuid),
                            :version_id, :methodology_id, :methodology_type,
                            :version_label, :source_type, :source_alias, :source_hash,
                            :source_byte_count, :source_contract_hash, :source_trace_digest,
                            :registry_hash, :version_hash, :question_count,
                            :source_section_count, :header_row, 'IMPORTED',
                            :actor_id, CAST(:audit_id AS uuid)
                        )
                        """
                    ),
                    {
                        "tenant_id": self._tenant_id,
                        "record_id": record_id,
                        "version_id": version.methodology_version_id,
                        "methodology_id": version.methodology_id,
                        "methodology_type": version.methodology_type.value,
                        "version_label": version.version_label,
                        "source_type": source_type,
                        "source_alias": safe_alias,
                        "source_hash": source_hash,
                        "source_byte_count": source_byte_count,
                        "source_contract_hash": contract_hash,
                        "source_trace_digest": trace_digest,
                        "registry_hash": registry_hash,
                        "version_hash": version_hash,
                        "question_count": len(version.questions),
                        "source_section_count": len(source_sections),
                        "header_row": header_row,
                        "actor_id": actor_id,
                        "audit_id": audit_id,
                    },
                )
                for ordinal, question in enumerate(version.questions, start=1):
                    self._insert_question(
                        version_record_id=record_id,
                        question=question,
                        ordinal=ordinal,
                    )
        except IntegrityError:
            existing = self._find_version_by_external_id(version.methodology_version_id)
            if existing is None or not self._same_import(
                existing,
                registry=validated,
                source_type=source_type,
                source_alias=safe_alias,
                source_byte_count=source_byte_count,
                source_contract_hash=contract_hash,
                source_trace_digest=trace_digest,
                registry_hash=registry_hash,
                version_hash=version_hash,
                source_section_count=len(source_sections),
                header_row=header_row,
            ):
                _fail()
            return existing
        except SQLAlchemyError:
            _fail()
        return self._load_version_record(record_id)

    def freeze_version(
        self,
        *,
        methodology_version_record_id: str,
        expected_source_sha256: str,
        frozen_by_actor_id: str,
        freeze_audit_event_id: str,
        expected_registry: MethodologyRegistry | None = None,
        expected_registry_hash: str | None = None,
        expected_version_hash: str | None = None,
        expected_question_payload_hashes: Iterable[str] | None = None,
        expected_source_trace_digest: str | None = None,
    ) -> MethodologyVersionRecord:
        """Revalidate authoritative content and perform the sole freeze transition."""
        record_id = _validated_uuid(methodology_version_record_id)
        source_hash = _validated_hash(expected_source_sha256)
        actor_id = _validated_nonblank(frozen_by_actor_id)
        audit_id = _validated_uuid(freeze_audit_event_id)

        if expected_registry is not None:
            authoritative = _validated_registry(expected_registry)
            if authoritative.current_version.source_hash != source_hash:
                _fail()
            derived_registry_hash = authoritative.registry_hash
            derived_version_hash = methodology_version_hash(authoritative)
            derived_question_hashes = methodology_question_payload_hashes(authoritative)
            derived_trace_digest = methodology_source_trace_digest(authoritative)
            if (
                expected_registry_hash is not None
                and expected_registry_hash != derived_registry_hash
            ):
                _fail()
            if expected_version_hash is not None and expected_version_hash != derived_version_hash:
                _fail()
            if (
                expected_question_payload_hashes is not None
                and tuple(expected_question_payload_hashes) != derived_question_hashes
            ):
                _fail()
            if (
                expected_source_trace_digest is not None
                and expected_source_trace_digest != derived_trace_digest
            ):
                _fail()
            expected_registry_hash = derived_registry_hash
            expected_version_hash = derived_version_hash
            expected_question_payload_hashes = derived_question_hashes
            expected_source_trace_digest = derived_trace_digest

        if (
            expected_registry_hash is None
            or expected_version_hash is None
            or expected_question_payload_hashes is None
            or expected_source_trace_digest is None
        ):
            _fail()
        registry_hash = _validated_hash(expected_registry_hash)
        version_hash = _validated_hash(expected_version_hash)
        trace_digest = _validated_hash(expected_source_trace_digest)
        question_hashes = tuple(
            _validated_hash(value) for value in expected_question_payload_hashes
        )

        current = self._load_version_record(record_id)
        stored_question_hashes = self._stored_question_hashes(record_id)
        if (
            current.source_sha256 != source_hash
            or current.registry_hash != registry_hash
            or current.version_hash != version_hash
            or current.source_trace_digest != trace_digest
            or stored_question_hashes != question_hashes
            or current.registry.registry_hash != registry_hash
            or methodology_version_hash(current.registry) != version_hash
            or methodology_source_trace_digest(current.registry) != trace_digest
        ):
            _fail()
        if current.status == "FROZEN":
            return current
        if current.status != "IMPORTED":
            _fail()

        try:
            with self._conn.begin_nested():
                result = self._conn.execute(
                    text(
                        """
                        UPDATE methodology_versions
                        SET status = 'FROZEN', frozen_by_actor_id = :actor_id,
                            freeze_audit_event_id = CAST(:audit_id AS uuid),
                            frozen_at = now()
                        WHERE tenant_id = CAST(:tenant_id AS uuid)
                          AND methodology_version_record_id = CAST(:record_id AS uuid)
                          AND status = 'IMPORTED'
                        """
                    ),
                    {
                        "tenant_id": self._tenant_id,
                        "record_id": record_id,
                        "actor_id": actor_id,
                        "audit_id": audit_id,
                    },
                )
                if result.rowcount != 1:
                    _fail()
        except SQLAlchemyError:
            _fail()
        return self.get_frozen_version(methodology_version_record_id=record_id)

    def get_frozen_version(self, *, methodology_version_record_id: str) -> MethodologyVersionRecord:
        """Load one frozen version and revalidate every persisted digest."""
        record = self._load_version_record(_validated_uuid(methodology_version_record_id))
        if record.status != "FROZEN":
            _fail()
        return record

    def list_versions(
        self,
        *,
        methodology_type: MethodologyType | str | None = None,
    ) -> list[MethodologyVersionRecord]:
        """List safe tenant-scoped versions, revalidating canonical payloads."""
        params: dict[str, object] = {"tenant_id": self._tenant_id}
        predicate = ""
        if methodology_type is not None:
            try:
                selected_type = MethodologyType(str(methodology_type))
            except ValueError:
                _fail()
            predicate = " AND methodology_type = :methodology_type"
            params["methodology_type"] = selected_type.value
        record_ids = tuple(
            self._conn.execute(
                text(
                    "SELECT methodology_version_record_id FROM methodology_versions "
                    "WHERE tenant_id = CAST(:tenant_id AS uuid)"
                    + predicate
                    + " ORDER BY imported_at, methodology_version_record_id"
                ),
                params,
            ).scalars()
        )
        return [self._load_version_record(str(record_id)) for record_id in record_ids]

    def create_run_selection(
        self,
        *,
        deal_id: str,
        run_id: str,
        commercial_dd_version_record_id: str,
        financial_dd_version_record_id: str,
        created_by_actor_id: str,
        selection_audit_event_id: str,
    ) -> RunMethodologySelection:
        """Bind one frozen 21-row CDD and one frozen 79-row FDD to a run."""
        deal = _validated_uuid(deal_id)
        run = _validated_uuid(run_id)
        commercial = self.get_frozen_version(
            methodology_version_record_id=commercial_dd_version_record_id
        )
        financial = self.get_frozen_version(
            methodology_version_record_id=financial_dd_version_record_id
        )
        if (
            commercial.methodology_type is not MethodologyType.COMMERCIAL_DD
            or financial.methodology_type is not MethodologyType.FINANCIAL_DD
            or commercial.question_count != 21
            or financial.question_count != 79
            or commercial.methodology_version_record_id == financial.methodology_version_record_id
        ):
            _fail()
        actor_id = _validated_nonblank(created_by_actor_id)
        audit_id = _validated_uuid(selection_audit_event_id)
        selection_hash = _selection_hash(
            tenant_id=self._tenant_id,
            deal_id=deal,
            run_id=run,
            commercial=commercial,
            financial=financial,
            selected_question_count=100,
        )
        selection_id = str(
            uuid.uuid5(
                uuid.NAMESPACE_URL,
                f"idis:run-methodology-selection:{self._tenant_id}:{run}:{selection_hash}",
            )
        )

        existing = self.load_run_selection(
            tenant_id=self._tenant_id,
            deal_id=deal,
            run_id=run,
            required=False,
        )
        if existing is not None:
            if (
                existing.selection_id != selection_id
                or existing.commercial_dd_version_record_id
                != commercial.methodology_version_record_id
                or existing.financial_dd_version_record_id
                != financial.methodology_version_record_id
                or existing.selection_hash != selection_hash
            ):
                _fail()
            return existing

        try:
            with self._conn.begin_nested():
                self._conn.execute(
                    text(
                        """
                        INSERT INTO run_methodology_selections (
                            selection_id, tenant_id, deal_id, run_id,
                            commercial_dd_version_record_id,
                            commercial_methodology_version_id, commercial_version_hash,
                            financial_dd_version_record_id,
                            financial_methodology_version_id, financial_version_hash,
                            selection_hash, selected_question_count,
                            created_by_actor_id, selection_audit_event_id
                        ) VALUES (
                            CAST(:selection_id AS uuid), CAST(:tenant_id AS uuid),
                            CAST(:deal_id AS uuid), CAST(:run_id AS uuid),
                            CAST(:commercial_record_id AS uuid),
                            :commercial_version_id, :commercial_version_hash,
                            CAST(:financial_record_id AS uuid),
                            :financial_version_id, :financial_version_hash,
                            :selection_hash, 100, :actor_id, CAST(:audit_id AS uuid)
                        )
                        """
                    ),
                    {
                        "selection_id": selection_id,
                        "tenant_id": self._tenant_id,
                        "deal_id": deal,
                        "run_id": run,
                        "commercial_record_id": commercial.methodology_version_record_id,
                        "commercial_version_id": commercial.methodology_version_id,
                        "commercial_version_hash": commercial.version_hash,
                        "financial_record_id": financial.methodology_version_record_id,
                        "financial_version_id": financial.methodology_version_id,
                        "financial_version_hash": financial.version_hash,
                        "selection_hash": selection_hash,
                        "actor_id": actor_id,
                        "audit_id": audit_id,
                    },
                )
        except IntegrityError:
            existing = self.load_run_selection(
                tenant_id=self._tenant_id,
                deal_id=deal,
                run_id=run,
                required=False,
            )
            if (
                existing is None
                or existing.selection_id != selection_id
                or existing.commercial_dd_version_record_id
                != commercial.methodology_version_record_id
                or existing.financial_dd_version_record_id
                != financial.methodology_version_record_id
                or existing.selection_hash != selection_hash
            ):
                _fail()
            return existing
        except SQLAlchemyError:
            _fail()
        loaded = self.load_run_selection(
            tenant_id=self._tenant_id,
            deal_id=deal,
            run_id=run,
        )
        if loaded is None:
            _fail()
        return loaded

    def load_run_selection(
        self,
        *,
        tenant_id: str,
        deal_id: str,
        run_id: str,
        required: bool = True,
    ) -> RunMethodologySelection | None:
        """Load the immutable selection for one exact tenant/deal/run scope."""
        tenant = _validated_uuid(tenant_id)
        deal = _validated_uuid(deal_id)
        run = _validated_uuid(run_id)
        if tenant != self._tenant_id:
            _fail()
        row = self._conn.execute(
            text(
                """
                SELECT s.*, cv.source_sha256 AS commercial_source_sha256,
                       cv.registry_hash AS commercial_registry_hash,
                       fv.source_sha256 AS financial_source_sha256,
                       fv.registry_hash AS financial_registry_hash
                FROM run_methodology_selections s
                JOIN methodology_versions cv
                  ON cv.tenant_id = s.tenant_id
                 AND cv.methodology_version_record_id = s.commercial_dd_version_record_id
                JOIN methodology_versions fv
                  ON fv.tenant_id = s.tenant_id
                 AND fv.methodology_version_record_id = s.financial_dd_version_record_id
                WHERE s.tenant_id = CAST(:tenant_id AS uuid)
                  AND s.deal_id = CAST(:deal_id AS uuid)
                  AND s.run_id = CAST(:run_id AS uuid)
                """
            ),
            {"tenant_id": tenant, "deal_id": deal, "run_id": run},
        ).one_or_none()
        if row is None:
            if required:
                _fail()
            return None
        commercial = self.get_frozen_version(
            methodology_version_record_id=str(_row_value(row, "commercial_dd_version_record_id"))
        )
        financial = self.get_frozen_version(
            methodology_version_record_id=str(_row_value(row, "financial_dd_version_record_id"))
        )
        selected_question_count = int(_row_value(row, "selected_question_count"))
        stored_selection_hash = str(_row_value(row, "selection_hash"))
        expected_selection_hash = _selection_hash(
            tenant_id=tenant,
            deal_id=deal,
            run_id=run,
            commercial=commercial,
            financial=financial,
            selected_question_count=selected_question_count,
        )
        if stored_selection_hash != expected_selection_hash:
            _fail()
        return RunMethodologySelection(
            selection_id=str(_row_value(row, "selection_id")),
            tenant_id=str(_row_value(row, "tenant_id")),
            deal_id=str(_row_value(row, "deal_id")),
            run_id=str(_row_value(row, "run_id")),
            commercial_dd_version_record_id=str(_row_value(row, "commercial_dd_version_record_id")),
            financial_dd_version_record_id=str(_row_value(row, "financial_dd_version_record_id")),
            selected_version_ids=(
                str(_row_value(row, "commercial_methodology_version_id")),
                str(_row_value(row, "financial_methodology_version_id")),
            ),
            selected_source_hashes=(
                str(_row_value(row, "commercial_source_sha256")),
                str(_row_value(row, "financial_source_sha256")),
            ),
            selected_registry_hashes=(
                str(_row_value(row, "commercial_registry_hash")),
                str(_row_value(row, "financial_registry_hash")),
            ),
            selected_version_hashes=(
                str(_row_value(row, "commercial_version_hash")),
                str(_row_value(row, "financial_version_hash")),
            ),
            selection_hash=stored_selection_hash,
            selected_question_count=selected_question_count,
        )

    def load_selected_registries(
        self, *, selection: RunMethodologySelection
    ) -> tuple[MethodologyRegistry, MethodologyRegistry]:
        """Reconstruct and hash-check the selected frozen CDD and FDD registries."""
        if (
            selection.tenant_id != self._tenant_id
            or selection.immutable is not True
            or selection.selected_question_count != 100
        ):
            _fail()
        commercial = self.get_frozen_version(
            methodology_version_record_id=selection.commercial_dd_version_record_id
        )
        financial = self.get_frozen_version(
            methodology_version_record_id=selection.financial_dd_version_record_id
        )
        if (
            commercial.methodology_type is not MethodologyType.COMMERCIAL_DD
            or financial.methodology_type is not MethodologyType.FINANCIAL_DD
            or selection.selected_version_ids
            != (commercial.methodology_version_id, financial.methodology_version_id)
            or selection.selected_source_hashes
            != (commercial.source_sha256, financial.source_sha256)
            or selection.selected_registry_hashes
            != (commercial.registry_hash, financial.registry_hash)
            or selection.selected_version_hashes
            != (commercial.version_hash, financial.version_hash)
        ):
            _fail()
        return commercial.registry, financial.registry

    def insert_coverage_records(
        self, *, records: Iterable[MethodologyCoverageRecord]
    ) -> list[MethodologyCoverageRecord]:
        """Idempotently insert the exact 100-record selection coverage ledger."""
        try:
            validated = [
                MethodologyCoverageRecord.model_validate(record, from_attributes=True)
                for record in records
            ]
        except (TypeError, ValueError):
            _fail()
        if len(validated) != 100 or len({record.coverage_record_id for record in validated}) != 100:
            _fail()
        scopes = {(r.tenant_id, r.deal_id, r.run_id) for r in validated}
        if len(scopes) != 1:
            _fail()
        tenant_id, deal_id, run_id = next(iter(scopes))
        selection = self.load_run_selection(
            tenant_id=tenant_id,
            deal_id=deal_id,
            run_id=run_id,
        )
        if selection is None:
            _fail()
        question_rows = self._selected_question_rows(selection)
        if len(question_rows) != 100:
            _fail()

        try:
            with self._conn.begin_nested():
                for record in validated:
                    key = (record.methodology_version_id, record.methodology_question_id)
                    question = question_rows.get(key)
                    if question is None:
                        _fail()
                    question_record_id, version_record_id, stored_question = question
                    if (
                        stored_question.methodology_id != record.methodology_id
                        or stored_question.methodology_type is not record.methodology_type
                        or stored_question.section != record.section
                    ):
                        _fail()
                    self._conn.execute(
                        text(
                            """
                            INSERT INTO methodology_coverage_records (
                                coverage_record_id, tenant_id, deal_id, run_id,
                                selection_id, methodology_version_record_id,
                                methodology_version_id, methodology_question_record_id,
                                methodology_question_id, methodology_type, status,
                                reason_code, evidence_links, answer, conflict_ids, defect_ids
                            ) VALUES (
                                :coverage_record_id, CAST(:tenant_id AS uuid),
                                CAST(:deal_id AS uuid), CAST(:run_id AS uuid),
                                CAST(:selection_id AS uuid), CAST(:version_record_id AS uuid),
                                :version_id, CAST(:question_record_id AS uuid),
                                :question_id, :methodology_type, :status, :reason_code,
                                CAST(:evidence_links AS jsonb), CAST(:answer AS jsonb),
                                CAST(:conflict_ids AS text[]), CAST(:defect_ids AS text[])
                            )
                            ON CONFLICT (coverage_record_id) DO NOTHING
                            """
                        ),
                        {
                            "coverage_record_id": record.coverage_record_id,
                            "tenant_id": record.tenant_id,
                            "deal_id": record.deal_id,
                            "run_id": record.run_id,
                            "selection_id": selection.selection_id,
                            "version_record_id": version_record_id,
                            "version_id": record.methodology_version_id,
                            "question_record_id": question_record_id,
                            "question_id": record.methodology_question_id,
                            "methodology_type": record.methodology_type.value,
                            "status": record.status.value,
                            "reason_code": record.reason_code,
                            "evidence_links": _canonical_json(
                                [link.model_dump(mode="json") for link in record.evidence_links]
                            ),
                            "answer": (
                                None
                                if record.answer is None
                                else _canonical_json(record.answer.model_dump(mode="json"))
                            ),
                            "conflict_ids": list(record.conflict_ids),
                            "defect_ids": list(record.defect_ids),
                        },
                    )
        except SQLAlchemyError:
            _fail()
        persisted = self.list_coverage_records(deal_id=deal_id, run_id=run_id)
        expected = sorted(validated, key=lambda record: record.coverage_record_id)
        if persisted != expected:
            _fail()
        return persisted

    def list_coverage_records(
        self, *, deal_id: str, run_id: str
    ) -> list[MethodologyCoverageRecord]:
        """Load and reconstruct tenant-scoped coverage records in stable order."""
        deal = _validated_uuid(deal_id)
        run = _validated_uuid(run_id)
        rows = self._conn.execute(
            text(
                """
                SELECT c.*, q.question_payload
                FROM methodology_coverage_records c
                JOIN methodology_questions q
                  ON q.tenant_id = c.tenant_id
                 AND q.methodology_question_record_id = c.methodology_question_record_id
                WHERE c.tenant_id = CAST(:tenant_id AS uuid)
                  AND c.deal_id = CAST(:deal_id AS uuid)
                  AND c.run_id = CAST(:run_id AS uuid)
                ORDER BY c.coverage_record_id
                """
            ),
            {"tenant_id": self._tenant_id, "deal_id": deal, "run_id": run},
        )
        records: list[MethodologyCoverageRecord] = []
        for row in rows:
            question = self._question_from_payload(_row_value(row, "question_payload"))
            try:
                record = MethodologyCoverageRecord.model_validate(
                    {
                        "tenant_id": str(_row_value(row, "tenant_id")),
                        "deal_id": str(_row_value(row, "deal_id")),
                        "run_id": str(_row_value(row, "run_id")),
                        "methodology_id": question.methodology_id,
                        "methodology_version_id": str(_row_value(row, "methodology_version_id")),
                        "methodology_question_id": str(_row_value(row, "methodology_question_id")),
                        "methodology_type": str(_row_value(row, "methodology_type")),
                        "section": question.section,
                        "status": str(_row_value(row, "status")),
                        "reason_code": _row_value(row, "reason_code"),
                        "evidence_links": _row_value(row, "evidence_links"),
                        "answer": _row_value(row, "answer"),
                        "conflict_ids": list(_row_value(row, "conflict_ids")),
                        "defect_ids": list(_row_value(row, "defect_ids")),
                    }
                )
            except (TypeError, ValueError):
                _fail()
            if record.coverage_record_id != str(_row_value(row, "coverage_record_id")):
                _fail()
            records.append(record)
        return records

    def _insert_question(
        self,
        *,
        version_record_id: str,
        question: MethodologyQuestion,
        ordinal: int,
    ) -> None:
        payload = question.model_dump(mode="json")
        question_record_id = str(
            uuid.uuid5(
                uuid.NAMESPACE_URL,
                "idis:methodology-question:"
                f"{self._tenant_id}:{version_record_id}:{question.methodology_question_id}",
            )
        )
        self._conn.execute(
            text(
                """
                INSERT INTO methodology_questions (
                    tenant_id, methodology_question_record_id,
                    methodology_version_record_id, methodology_version_id,
                    methodology_question_id, methodology_id, methodology_type,
                    question_ordinal, section, source_sheet_or_section,
                    source_row_number, source_anchor_cell, source_sha256,
                    semantic_row_hash, question_payload_hash, question_payload
                ) VALUES (
                    CAST(:tenant_id AS uuid), CAST(:question_record_id AS uuid),
                    CAST(:version_record_id AS uuid), :version_id,
                    :question_id, :methodology_id, :methodology_type,
                    :ordinal, :section, :source_section, :source_row,
                    :source_anchor, :source_hash, :semantic_hash,
                    :payload_hash, CAST(:payload AS jsonb)
                )
                """
            ),
            {
                "tenant_id": self._tenant_id,
                "question_record_id": question_record_id,
                "version_record_id": version_record_id,
                "version_id": question.methodology_version_id,
                "question_id": question.methodology_question_id,
                "methodology_id": question.methodology_id,
                "methodology_type": question.methodology_type.value,
                "ordinal": ordinal,
                "section": question.section,
                "source_section": question.source_trace.sheet_or_section,
                "source_row": question.source_trace.row_number,
                "source_anchor": question.source_trace.source_anchor_cell,
                "source_hash": question.source_trace.source_hash,
                "semantic_hash": _semantic_row_hash(question),
                "payload_hash": _sha256_json(payload),
                "payload": _canonical_json(payload),
            },
        )

    def _find_version_by_external_id(
        self, methodology_version_id: str
    ) -> MethodologyVersionRecord | None:
        record_id = self._conn.execute(
            text(
                "SELECT methodology_version_record_id FROM methodology_versions "
                "WHERE tenant_id = CAST(:tenant_id AS uuid) "
                "AND methodology_version_id = :version_id"
            ),
            {"tenant_id": self._tenant_id, "version_id": methodology_version_id},
        ).scalar_one_or_none()
        if record_id is None:
            return None
        return self._load_version_record(str(record_id))

    def _load_version_record(self, record_id: str) -> MethodologyVersionRecord:
        row = self._conn.execute(
            text(
                "SELECT * FROM methodology_versions "
                "WHERE tenant_id = CAST(:tenant_id AS uuid) "
                "AND methodology_version_record_id = CAST(:record_id AS uuid)"
            ),
            {"tenant_id": self._tenant_id, "record_id": record_id},
        ).one_or_none()
        if row is None:
            _fail()
        question_rows = self._conn.execute(
            text(
                """
                SELECT question_payload, question_payload_hash
                FROM methodology_questions
                WHERE tenant_id = CAST(:tenant_id AS uuid)
                  AND methodology_version_record_id = CAST(:record_id AS uuid)
                ORDER BY question_ordinal, methodology_question_record_id
                """
            ),
            {"tenant_id": self._tenant_id, "record_id": record_id},
        ).fetchall()
        questions: list[MethodologyQuestion] = []
        for question_row in question_rows:
            payload = _row_value(question_row, "question_payload")
            if _sha256_json(payload) != str(_row_value(question_row, "question_payload_hash")):
                _fail()
            questions.append(self._question_from_payload(payload))
        try:
            methodology_type = MethodologyType(str(_row_value(row, "methodology_type")))
            version = MethodologyVersion(
                methodology_id=str(_row_value(row, "methodology_id")),
                methodology_version_id=str(_row_value(row, "methodology_version_id")),
                methodology_type=methodology_type,
                version_label=str(_row_value(row, "version_label")),
                source_hash=str(_row_value(row, "source_sha256")),
                source_name=str(_row_value(row, "source_alias")),
                questions=tuple(questions),
            )
            registry = MethodologyRegistry(
                methodology_id=version.methodology_id,
                methodology_type=version.methodology_type,
                versions=(version,),
            )
        except (TypeError, ValueError):
            _fail()
        record = MethodologyVersionRecord(
            tenant_id=str(_row_value(row, "tenant_id")),
            methodology_version_record_id=str(_row_value(row, "methodology_version_record_id")),
            methodology_version_id=version.methodology_version_id,
            methodology_id=version.methodology_id,
            methodology_type=methodology_type,
            version_label=version.version_label,
            source_type=str(_row_value(row, "source_type")),
            source_alias=str(_row_value(row, "source_alias")),
            source_sha256=str(_row_value(row, "source_sha256")),
            source_byte_count=int(_row_value(row, "source_byte_count")),
            source_contract_hash=str(_row_value(row, "source_contract_hash")),
            source_trace_digest=str(_row_value(row, "source_trace_digest")),
            registry_hash=str(_row_value(row, "registry_hash")),
            version_hash=str(_row_value(row, "version_hash")),
            question_count=int(_row_value(row, "question_count")),
            source_section_count=int(_row_value(row, "source_section_count")),
            header_row=_row_value(row, "header_row"),
            status=str(_row_value(row, "status")),
            imported_at=_row_value(row, "imported_at"),
            frozen_at=_row_value(row, "frozen_at"),
            registry=registry,
        )
        if (
            len(questions) != record.question_count
            or registry.registry_hash != record.registry_hash
            or methodology_version_hash(registry) != record.version_hash
            or methodology_source_trace_digest(registry) != record.source_trace_digest
            or any(q.source_trace.source_type != record.source_type for q in questions)
            or any(q.source_trace.source_name != record.source_alias for q in questions)
        ):
            _fail()
        return record

    def _same_import(
        self,
        existing: MethodologyVersionRecord,
        *,
        registry: MethodologyRegistry,
        source_type: str,
        source_alias: str,
        source_byte_count: int,
        source_contract_hash: str,
        source_trace_digest: str,
        registry_hash: str,
        version_hash: str,
        source_section_count: int,
        header_row: int | None,
    ) -> bool:
        return (
            existing.registry == registry
            and existing.source_type == source_type
            and existing.source_alias == source_alias
            and existing.source_byte_count == source_byte_count
            and existing.source_contract_hash == source_contract_hash
            and existing.source_trace_digest == source_trace_digest
            and existing.registry_hash == registry_hash
            and existing.version_hash == version_hash
            and existing.source_section_count == source_section_count
            and existing.header_row == header_row
        )

    def _stored_question_hashes(self, record_id: str) -> tuple[str, ...]:
        return tuple(
            str(value)
            for value in self._conn.execute(
                text(
                    "SELECT question_payload_hash FROM methodology_questions "
                    "WHERE tenant_id = CAST(:tenant_id AS uuid) "
                    "AND methodology_version_record_id = CAST(:record_id AS uuid) "
                    "ORDER BY question_ordinal, methodology_question_record_id"
                ),
                {"tenant_id": self._tenant_id, "record_id": record_id},
            ).scalars()
        )

    def _selected_question_rows(
        self, selection: RunMethodologySelection
    ) -> dict[tuple[str, str], tuple[str, str, MethodologyQuestion]]:
        rows = self._conn.execute(
            text(
                """
                SELECT methodology_question_record_id,
                       methodology_version_record_id,
                       methodology_version_id, methodology_question_id,
                       question_payload
                FROM methodology_questions
                WHERE tenant_id = CAST(:tenant_id AS uuid)
                  AND methodology_version_record_id IN (
                    CAST(:commercial_id AS uuid), CAST(:financial_id AS uuid)
                  )
                ORDER BY methodology_version_id, question_ordinal
                """
            ),
            {
                "tenant_id": self._tenant_id,
                "commercial_id": selection.commercial_dd_version_record_id,
                "financial_id": selection.financial_dd_version_record_id,
            },
        )
        result: dict[tuple[str, str], tuple[str, str, MethodologyQuestion]] = {}
        for row in rows:
            key = (
                str(_row_value(row, "methodology_version_id")),
                str(_row_value(row, "methodology_question_id")),
            )
            if key in result:
                _fail()
            result[key] = (
                str(_row_value(row, "methodology_question_record_id")),
                str(_row_value(row, "methodology_version_record_id")),
                self._question_from_payload(_row_value(row, "question_payload")),
            )
        return result

    @staticmethod
    def _question_from_payload(payload: object) -> MethodologyQuestion:
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except json.JSONDecodeError:
                _fail()
        try:
            return MethodologyQuestion.model_validate(payload)
        except (TypeError, ValueError):
            _fail()


def get_methodologies_repository(
    conn: Connection | None,
    tenant_id: str,
) -> PostgresMethodologiesRepository:
    """Return the required durable repository, failing closed without a connection."""
    if conn is None:
        _fail()
    return PostgresMethodologiesRepository(conn, tenant_id)


__all__ = [
    "METHODOLOGY_COVERAGE_INCOMPLETE",
    "MethodologiesRepositoryError",
    "MethodologyVersionRecord",
    "PostgresMethodologiesRepository",
    "RunMethodologySelection",
    "get_methodologies_repository",
    "methodology_question_payload_hashes",
    "methodology_source_trace_digest",
    "methodology_version_hash",
]
