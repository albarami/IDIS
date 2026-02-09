"""IDIS Deliverables Generator — v6.3 Phase 6.1 + Phase 10

Evidence-linked deliverables generation with hard trust invariants.

Non-negotiables:
- Every fact in memo has claim_id/calc_id reference
- Exports include audit appendix
- No randomness (no uuid4/uuid1/random/datetime.now/utcnow)
- Fail-closed: missing refs block export

Modules:
- screening: Screening Snapshot builder
- memo: IC Memo builder
- truth_dashboard: Truth Dashboard builder (Phase 10)
- qa_brief: QA Brief builder (Phase 10)
- decline_letter: Decline Letter builder (Phase 10)
- generator: Deliverables bundle generator orchestrator (Phase 10)
- export: PDF/DOCX exporters + audit appendix rendering
"""

from idis.deliverables.decline_letter import DeclineLetterBuilder
from idis.deliverables.export import (
    DeliverableExporter,
    export_to_docx,
    export_to_pdf,
)
from idis.deliverables.generator import DeliverablesGenerator
from idis.deliverables.memo import (
    ICMemoBuilder,
    build_ic_memo,
)
from idis.deliverables.qa_brief import QABriefBuilder
from idis.deliverables.screening import (
    ScreeningSnapshotBuilder,
    build_screening_snapshot,
)
from idis.deliverables.truth_dashboard import TruthDashboardBuilder

__all__ = [
    "DeclineLetterBuilder",
    "DeliverableExporter",
    "DeliverablesGenerator",
    "ICMemoBuilder",
    "QABriefBuilder",
    "ScreeningSnapshotBuilder",
    "TruthDashboardBuilder",
    "build_ic_memo",
    "build_screening_snapshot",
    "export_to_docx",
    "export_to_pdf",
]
