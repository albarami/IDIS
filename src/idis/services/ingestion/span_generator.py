"""Span generator — converts parser SpanDrafts to model-ready DocumentSpans.

Provides deterministic conversion from parser output (SpanDraft) to
persistence-ready DocumentSpan objects with stable locator JSON.

Requirements:
- Deterministic ordering: stable sort by locator then text excerpt
- Tenant scoping: all generated spans include tenant_id
- Stable locator JSON: canonical key ordering for reproducible hashes
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from idis.models.document_span import DocumentSpan, SpanType
from idis.parsers.base import SpanDraft


class SpanGenerator:
    """Generates DocumentSpan objects from parser SpanDrafts.

    Converts intermediate SpanDraft objects (from parsers) into
    fully-formed DocumentSpan models ready for persistence.

    Ensures:
    - Deterministic ordering for reproducible span sequences
    - Stable locator JSON with canonical key ordering
    - Proper tenant/document ID assignment
    """

    def generate_spans(
        self,
        span_drafts: list[SpanDraft],
        *,
        tenant_id: UUID,
        document_id: UUID,
    ) -> list[DocumentSpan]:
        """Generate DocumentSpan objects from SpanDrafts.

        Args:
            span_drafts: List of SpanDraft objects from parser.
            tenant_id: Tenant scope for generated spans.
            document_id: Parent document reference.

        Returns:
            List of DocumentSpan objects in deterministic order.

        Ordering:
            Spans are sorted by (locator_sort_key, text_excerpt) for
            reproducible ordering across ingestion runs.
        """
        sorted_drafts = self._sort_drafts(span_drafts)

        now = datetime.now(UTC)
        spans: list[DocumentSpan] = []

        for draft in sorted_drafts:
            span = self._create_span(
                draft=draft,
                tenant_id=tenant_id,
                document_id=document_id,
                timestamp=now,
            )
            spans.append(span)

        return spans

    def _sort_drafts(self, drafts: list[SpanDraft]) -> list[SpanDraft]:
        """Sort SpanDrafts deterministically.

        Sorting key:
            1. Locator JSON (canonical string representation)
            2. Text excerpt (for tiebreaker)

        Args:
            drafts: Unsorted SpanDraft list.

        Returns:
            Sorted SpanDraft list.
        """
        return sorted(
            drafts,
            key=lambda d: (
                self._locator_sort_key(d.locator),
                d.text_excerpt or "",
            ),
        )

    def _locator_sort_key(self, locator: dict[str, Any]) -> str:
        """Generate a stable sort key from a locator dict.

        Uses canonical JSON serialization (sorted keys, no whitespace)
        for deterministic ordering.

        Args:
            locator: Locator dictionary.

        Returns:
            Canonical JSON string for sorting.
        """
        return json.dumps(locator, sort_keys=True, separators=(",", ":"))

    def _normalize_locator(self, locator: dict[str, Any]) -> dict[str, Any]:
        """Normalize locator dict for stable storage.

        Ensures canonical key ordering by round-tripping through
        JSON serialization with sorted keys.

        Args:
            locator: Raw locator dictionary.

        Returns:
            Normalized locator with deterministic key ordering.
        """
        canonical = json.dumps(locator, sort_keys=True, separators=(",", ":"))
        result: dict[str, Any] = json.loads(canonical)
        return result

    def _map_span_type(self, draft_type: str) -> SpanType:
        """Map draft span type string to SpanType enum.

        Args:
            draft_type: Span type string from SpanDraft.

        Returns:
            Corresponding SpanType enum value.

        Raises:
            ValueError: If draft_type is not a valid SpanType.
        """
        type_map = {
            "PAGE_TEXT": SpanType.PAGE_TEXT,
            "PARAGRAPH": SpanType.PARAGRAPH,
            "CELL": SpanType.CELL,
            "TIMECODE": SpanType.TIMECODE,
        }
        if draft_type not in type_map:
            raise ValueError(f"Unknown span type: {draft_type}")
        return type_map[draft_type]

    def _create_span(
        self,
        draft: SpanDraft,
        *,
        tenant_id: UUID,
        document_id: UUID,
        timestamp: datetime,
    ) -> DocumentSpan:
        """Create a DocumentSpan from a SpanDraft.

        Args:
            draft: Source SpanDraft from parser.
            tenant_id: Tenant scope.
            document_id: Parent document reference.
            timestamp: Creation timestamp.

        Returns:
            Fully-formed DocumentSpan ready for persistence.
        """
        return DocumentSpan(
            span_id=uuid4(),
            tenant_id=tenant_id,
            document_id=document_id,
            span_type=self._map_span_type(draft.span_type),
            locator=self._normalize_locator(draft.locator),
            text_excerpt=draft.text_excerpt if draft.text_excerpt else None,
            created_at=timestamp,
            updated_at=timestamp,
        )
