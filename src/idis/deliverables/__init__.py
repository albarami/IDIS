"""IDIS Deliverables Generator — v6.3 Phase 6.1

Evidence-linked deliverables generation with hard trust invariants.

Non-negotiables:
- Every fact in memo has claim_id/calc_id reference
- Exports include audit appendix
- No randomness (no uuid4/uuid1/random/datetime.now/utcnow)
- Fail-closed: missing refs block export

Modules:
- screening: Screening Snapshot builder
- memo: IC Memo builder
- export: PDF/DOCX exporters + audit appendix rendering
"""

from idis.deliverables.export import (
    DeliverableExporter,
    export_to_docx,
    export_to_pdf,
)
from idis.deliverables.memo import (
    ICMemoBuilder,
    build_ic_memo,
)
from idis.deliverables.screening import (
    ScreeningSnapshotBuilder,
    build_screening_snapshot,
)

__all__ = [
    "DeliverableExporter",
    "ICMemoBuilder",
    "ScreeningSnapshotBuilder",
    "build_ic_memo",
    "build_screening_snapshot",
    "export_to_docx",
    "export_to_pdf",
]
