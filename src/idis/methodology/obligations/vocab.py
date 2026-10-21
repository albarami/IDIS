"""Pinned vocabularies and constants for the GL-02 obligation register.

Record sort keys (applied by the JSONL writer, then by shard order
``PL``, ``CF``, ``LIAB``, ``ASSET``, ``CDD``, ``XS``):

- ``SourceCell``: sheet ordinal, row, column
- ``ParentRecord``: kind, sheet ordinal, row ordinal, parent id
- ``MergedInstruction``: instruction id
- ``PolicyViewRecord``: parent id
- ``ParentProfile``: parent id
- ``ChildObligation``: child id
- ``CrossRowInstruction``: instruction id
- ``Disposition``: parent id, field, kind, start, end
- ``Ambiguity``: ambiguity id
- ``OmissionAuditRecord``: parent id
- ``RoleAlias``: template role
- ``BaselineExpectation``: expectation id
- ``ViewRecord``: view id
- ``LedgerRecord``: child id
- ``SealRecord``: sequence number
"""

from __future__ import annotations

from enum import StrEnum

from idis.methodology.importers.fdd_excel import EXPECTED_ROW_COUNTS

REGISTER_SCHEMA = "gl02-obligation-register-v1"
REGISTER_VERSION = "gl02-register-v1"
EVIDENCE_TYPE_VOCAB_VERSION = "gl02-evidence-v1"

PINNED_WORKBOOK_SHA256 = "a3b83addfff02815c9fd44c4a3dcb7f56ce39410fe182faff12acfb845bd4352"
PINNED_WORKBOOK_BYTE_COUNT = 24905

MATERIAL_RUN_MIN_CONTENT_TOKENS = 4
MATERIAL_LABEL_MIN_CONTENT_TOKENS = 5
FOCUS_MAX_REACH = 2
QUOTE_BROAD_SEGMENT_MIN = 3
DISPOSITION_HEAVY_NUMERATOR = 1
DISPOSITION_HEAVY_DENOMINATOR = 3
LIAB_SCOPE_FIRST_ROW = 17
LIAB_SCOPE_LAST_ROW = 24
CHILD_SEQUENCE_WIDTH = 3

SHEET_NAMES: tuple[str, ...] = ("P&L", "Cash Flow", "Liabilities", "Assets")
SHEET_CODES: dict[str, str] = {
    "P&L": "PL",
    "Cash Flow": "CF",
    "Liabilities": "LIAB",
    "Assets": "ASSET",
}
EXPECTED_MERGED_RANGE_COUNTS: dict[str, int] = {
    "P&L": 11,
    "Cash Flow": 5,
    "Liabilities": 10,
    "Assets": 7,
}
EXPECTED_DATA_ROW_COUNTS: dict[str, int] = dict(EXPECTED_ROW_COUNTS)

QUOTE_FIELDS: tuple[str, ...] = ("question", "line_item", "term", "nature")
LABEL_FIELDS: frozenset[str] = frozenset({"line_item", "term", "nature"})
COORDINATE_KEYS: tuple[str, ...] = (
    "entity",
    "consolidation_scope",
    "period",
    "actual_forecast_status",
    "currency",
    "unit",
    "adjustment",
)

REFERENCE_MARKERS: tuple[str, ...] = (
    "above",
    "below",
    "same as",
    "as per",
    "see",
    "refer",
    "previous",
    "following",
    "valid for",
)
SCOPE_POINTER_MARKERS: tuple[str, ...] = (
    "p&l",
    "profit and loss",
    "cash flow",
    "balance sheet",
    "liabilities",
    "assets",
    "business plan",
    "bp",
    "reconcil",
    "consistent with",
    "tie",
)
PREFIX_MARKERS: frozenset[str] = frozenset({"reconcil"})

QUALIFIER_TOKENS: frozenset[str] = frozenset(
    {
        "not",
        "no",
        "never",
        "without",
        "any",
        "all",
        "each",
        "every",
        "only",
        "least",
        "most",
        "more",
        "less",
        "if",
        "unless",
        "except",
        "where",
        "when",
        "whether",
        "other",
        "others",
        "etc",
        "both",
        "excluding",
        "including",
        "excl",
        "incl",
        "net",
        "gross",
        "monthly",
        "quarterly",
        "annual",
        "annually",
        "yearly",
        "per",
    }
)

DIRECTIVE_TOKENS: frozenset[str] = frozenset(
    {
        "check",
        "confirm",
        "ensure",
        "make",
        "sure",
        "understand",
        "assess",
        "verify",
        "reconcile",
        "identify",
        "compare",
        "review",
        "test",
        "look",
        "read",
        "ask",
        "request",
        "find",
        "consider",
        "evaluate",
        "determine",
        "analyse",
        "analyze",
        "challenge",
        "validate",
        "obtain",
        "align",
        "should",
        "must",
        "need",
        "needs",
        "required",
        "requires",
        "important",
        "key",
        "careful",
    }
)

NON_BREAKING_ABBREVIATIONS: tuple[str, ...] = (
    "i.e.",
    "e.g.",
    "vs.",
    "approx.",
    "incl.",
    "excl.",
    "no.",
)

_STOPWORD_WORDS: tuple[str, ...] = (
    "a",
    "an",
    "the",
    "and",
    "or",
    "but",
    "nor",
    "so",
    "yet",
    "of",
    "in",
    "on",
    "at",
    "to",
    "for",
    "from",
    "by",
    "with",
    "as",
    "into",
    "onto",
    "upon",
    "over",
    "under",
    "about",
    "between",
    "through",
    "during",
    "before",
    "after",
    "against",
    "among",
    "around",
    "across",
    "behind",
    "beside",
    "besides",
    "beyond",
    "despite",
    "down",
    "off",
    "out",
    "outside",
    "inside",
    "near",
    "since",
    "toward",
    "towards",
    "until",
    "up",
    "via",
    "within",
    "i",
    "you",
    "he",
    "she",
    "it",
    "we",
    "they",
    "me",
    "him",
    "her",
    "us",
    "them",
    "my",
    "your",
    "his",
    "its",
    "our",
    "their",
    "mine",
    "yours",
    "ours",
    "theirs",
    "this",
    "that",
    "these",
    "those",
    "who",
    "whom",
    "whose",
    "which",
    "what",
    "am",
    "is",
    "are",
    "was",
    "were",
    "be",
    "been",
    "being",
    "do",
    "does",
    "did",
    "doing",
    "have",
    "has",
    "had",
    "having",
    "can",
    "could",
    "may",
    "might",
    "shall",
    "will",
    "would",
    "how",
    "why",
    "very",
    "just",
    "also",
    "too",
    "than",
    "then",
    "there",
    "here",
    "such",
    "own",
    "because",
    "while",
)


def _stopwords() -> frozenset[str]:
    words = frozenset(_STOPWORD_WORDS)
    overlap = words & QUALIFIER_TOKENS or words & DIRECTIVE_TOKENS
    if len(words) != len(_STOPWORD_WORDS) or len(words) > 120 or overlap:
        raise RuntimeError("GL02_STOPWORD_VOCABULARY")
    return words


STOPWORDS: frozenset[str] = _stopwords()


class Derivation(StrEnum):
    EXPLICIT = "explicit"
    ENUMERATED_ITEM = "enumerated_item"
    CROSS_REFERENCE_APPLIED = "cross_reference_applied"
    PLAN_MINIMUM = "plan_minimum"
    FORMULA_POLICY = "formula_policy"
    TEMPLATE_FIELD = "template_field"
    ANALYST_INFERENCE = "analyst_inference"


DERIVATIONS_REQUIRING_RATIONALE: frozenset[Derivation] = frozenset(
    {
        Derivation.CROSS_REFERENCE_APPLIED,
        Derivation.PLAN_MINIMUM,
        Derivation.FORMULA_POLICY,
        Derivation.TEMPLATE_FIELD,
        Derivation.ANALYST_INFERENCE,
    }
)
DERIVATIONS_REQUIRING_QUOTES: frozenset[Derivation] = frozenset(
    {
        Derivation.EXPLICIT,
        Derivation.ENUMERATED_ITEM,
        Derivation.CROSS_REFERENCE_APPLIED,
    }
)


class Independence(StrEnum):
    INTERNAL = "internal"
    EXTERNAL_INDEPENDENT = "external_independent"
    MANAGEMENT_ESTIMATE = "management_estimate"
    EITHER = "either"


class ApplicabilityDecision(StrEnum):
    ALWAYS = "always"
    CONDITIONAL = "conditional"
    NOT_APPLICABLE_BY_DEFAULT = "not_applicable_by_default"


class CapabilityStatus(StrEnum):
    UNASSESSED = "unassessed"
    SUPPORTED = "supported"
    PARTIAL = "partial"
    GAP = "gap"


class FundFeedbackStatus(StrEnum):
    PROPOSED = "proposed"
    UNDER_REVIEW = "under_review"
    ACCEPTED = "accepted"
    CHANGED = "changed"
    REJECTED = "rejected"


class ChildStatus(StrEnum):
    ACTIVE = "active"
    RETIRED = "retired"


class Shard(StrEnum):
    PL = "PL"
    CF = "CF"
    LIAB = "LIAB"
    ASSET = "ASSET"
    CDD = "CDD"
    XS = "XS"


SHARD_ORDER: tuple[Shard, ...] = (
    Shard.PL,
    Shard.CF,
    Shard.LIAB,
    Shard.ASSET,
    Shard.CDD,
    Shard.XS,
)
SHEET_SHARDS: dict[str, Shard] = {
    "P&L": Shard.PL,
    "Cash Flow": Shard.CF,
    "Liabilities": Shard.LIAB,
    "Assets": Shard.ASSET,
}


class ProducerKind(StrEnum):
    METHODOLOGY_EXTRACTION = "methodology_extraction"
    DETERMINISTIC_CALCULATION = "deterministic_calculation"
    SPECIALIST_ANALYSIS = "specialist_analysis"
    ANALYST_REVIEW = "analyst_review"
    MANAGEMENT_INQUIRY = "management_inquiry"
    EXTERNAL_ENRICHMENT = "external_enrichment"
    CROSS_CHECK = "cross_check"


class ConsumerKind(StrEnum):
    RECONCILIATION_CHECK = "reconciliation_check"
    SCORECARD_INPUT = "scorecard_input"
    REPORT_SECTION = "report_section"
    DOWNSTREAM_CALCULATION = "downstream_calculation"
    CHALLENGE_LAYER = "challenge_layer"
    DEFECT_REGISTER = "defect_register"
    FUND_DELIVERABLE = "fund_deliverable"


class PlanMinimumCode(StrEnum):
    PL_HIST_REV_COST_EVIDENCE = "PL_HIST_REV_COST_EVIDENCE"
    PL_FCST_REV_COST_EVIDENCE = "PL_FCST_REV_COST_EVIDENCE"
    PL_MARGIN = "PL_MARGIN"
    PL_EBITDA = "PL_EBITDA"
    PL_EBIT = "PL_EBIT"
    PL_TAX = "PL_TAX"
    PL_NET_INCOME = "PL_NET_INCOME"
    PL_FCST_ASSUMPTIONS = "PL_FCST_ASSUMPTIONS"
    PL_BP_RECONCILIATION = "PL_BP_RECONCILIATION"
    PL_CASH_RECONCILIATION = "PL_CASH_RECONCILIATION"
    CF_CASH_CONVERSION = "CF_CASH_CONVERSION"
    CF_WORKING_CAPITAL = "CF_WORKING_CAPITAL"
    CF_TAXES = "CF_TAXES"
    CF_CAPEX = "CF_CAPEX"
    CF_FINANCING = "CF_FINANCING"
    CF_OPENING_CLOSING_CASH = "CF_OPENING_CLOSING_CASH"
    CF_BANK_RECONCILIATION = "CF_BANK_RECONCILIATION"
    LIAB_EQUITY_INSTRUMENTS = "LIAB_EQUITY_INSTRUMENTS"
    LIAB_DEBT = "LIAB_DEBT"
    LIAB_ST_FINANCING = "LIAB_ST_FINANCING"
    LIAB_ACCRUALS = "LIAB_ACCRUALS"
    LIAB_DEFERRED_REVENUE = "LIAB_DEFERRED_REVENUE"
    LIAB_CONTINGENT = "LIAB_CONTINGENT"
    LIAB_OFF_BALANCE = "LIAB_OFF_BALANCE"
    ASSET_CASH = "ASSET_CASH"
    ASSET_RECEIVABLES = "ASSET_RECEIVABLES"
    ASSET_PREPAYMENTS = "ASSET_PREPAYMENTS"
    ASSET_OTHER_ASSETS = "ASSET_OTHER_ASSETS"
    ASSET_RECOVERABILITY = "ASSET_RECOVERABILITY"
    ASSET_SCHEDULES = "ASSET_SCHEDULES"
    ASSET_BS_RECONCILIATION = "ASSET_BS_RECONCILIATION"


class DerivedRuleCode(StrEnum):
    RETAINED_EARNINGS = "RETAINED_EARNINGS"
    DSO = "DSO"
    DPO = "DPO"
    CASH_CONVERSION = "CASH_CONVERSION"
    BANK_SCHEDULE = "BANK_SCHEDULE"
    ACCRUAL_SCHEDULE = "ACCRUAL_SCHEDULE"
    PREPAYMENT_SCHEDULE = "PREPAYMENT_SCHEDULE"
    DEFERRED_REVENUE_SCHEDULE = "DEFERRED_REVENUE_SCHEDULE"
    LEASE_SCHEDULE = "LEASE_SCHEDULE"
    BENEFIT_SCHEDULE = "BENEFIT_SCHEDULE"
    CONVERSION_SCHEDULE = "CONVERSION_SCHEDULE"
    UNIT_ECONOMICS = "UNIT_ECONOMICS"
    FORECAST_SENSITIVITY = "FORECAST_SENSITIVITY"
    MARKET_SIZE_RECONCILIATION = "MARKET_SIZE_RECONCILIATION"
    CAGR = "CAGR"
    CONCENTRATION_TOP_N = "CONCENTRATION_TOP_N"
    RETENTION_COHORT = "RETENTION_COHORT"
    CAC_PAYBACK = "CAC_PAYBACK"
    REVENUE_BRIDGE = "REVENUE_BRIDGE"


class CoordinateRequirement(StrEnum):
    REQUIRED = "required"
    INHERIT_FROM_DEAL = "inherit_from_deal"
    NOT_APPLICABLE = "not_applicable"


class DispositionKind(StrEnum):
    RUN = "run"
    MARKER = "marker"


class RunDisposition(StrEnum):
    CONNECTIVE = "connective"
    RATIONALE_ONLY = "rationale_only"
    RESTATES_CHILD = "restates_child"
    HEADING_OR_LABEL = "heading_or_label"
    NOT_AN_OBLIGATION = "not_an_obligation"


class MarkerDisposition(StrEnum):
    INTRA_CELL_REFERENCE = "intra_cell_reference"
    NOT_A_CROSS_REFERENCE = "not_a_cross_reference"


class AmbiguityTopic(StrEnum):
    CF_TAX_CONDITIONAL = "CF_TAX_CONDITIONAL"
    FCF_CONVERSION_DIRECTION = "FCF_CONVERSION_DIRECTION"
    ZERO_EBITDA = "ZERO_EBITDA"
    ST_FINANCING_LABELS = "ST_FINANCING_LABELS"
    ASSETS_ROW19_NEGATION = "ASSETS_ROW19_NEGATION"
    JURISDICTIONAL_TAX = "JURISDICTIONAL_TAX"
    DA_CAPEX_HEURISTIC = "DA_CAPEX_HEURISTIC"
    OTHER = "OTHER"


NAMED_AMBIGUITY_TOPICS: tuple[AmbiguityTopic, ...] = tuple(
    topic for topic in AmbiguityTopic if topic is not AmbiguityTopic.OTHER
)


class CrossRowKind(StrEnum):
    SAME_AS_ABOVE = "same_as_above"
    VALID_FOR_ALL = "valid_for_all"
    CROSS_SHEET = "cross_sheet"
    OTHER = "other"


class ViewStatus(StrEnum):
    PROPOSED = "proposed"
    AGREED = "agreed"


class ViewClosure(StrEnum):
    NONE = "none"
    POLICY_AND_CROSS_REFERENCE = "policy_and_cross_reference"


class OutputDestinationStatus(StrEnum):
    PROVISIONAL = "provisional"
    CONFIRMED = "confirmed"


class SpecialistAgentType(StrEnum):
    FINANCIAL_AGENT = "financial_agent"
    HISTORIAN_AGENT = "historian_agent"
    MARKET_AGENT = "market_agent"
    RISK_OFFICER_AGENT = "risk_officer_agent"
    SECTOR_SPECIALIST_AGENT = "sector_specialist_agent"
    TEAM_AGENT = "team_agent"
    TECHNICAL_AGENT = "technical_agent"
    TERMS_AGENT = "terms_agent"


class EvidenceType(StrEnum):
    LEDGER_EXTRACT = "ledger_extract"
    TRIAL_BALANCE = "trial_balance"
    MANAGEMENT_ACCOUNTS = "management_accounts"
    AUDITED_FINANCIALS = "audited_financials"
    BANK_STATEMENT = "bank_statement"
    TAX_FILING = "tax_filing"
    INVOICE_REGISTER = "invoice_register"
    CUSTOMER_CONTRACT = "customer_contract"
    SUPPLIER_CONTRACT = "supplier_contract"
    BOARD_MINUTES = "board_minutes"
    MANAGEMENT_REPRESENTATION = "management_representation"
    FORECAST_MODEL = "forecast_model"
    BUDGET_PACK = "budget_pack"
    RECONCILIATION_PACK = "reconciliation_pack"
    AGING_SCHEDULE = "aging_schedule"
    CAPITALIZATION_TABLE = "capitalization_table"
    DEBT_AGREEMENT = "debt_agreement"
    LEASE_SCHEDULE = "lease_schedule"
    PAYROLL_REGISTER = "payroll_register"
    INSURANCE_SCHEDULE = "insurance_schedule"
    MARKET_STUDY = "market_study"
    CUSTOMER_INTERVIEW = "customer_interview"
    PIPELINE_EXTRACT = "pipeline_extract"
    PRICE_SCHEDULE = "price_schedule"
    COMPETITOR_PROFILE = "competitor_profile"
    SITE_VISIT_NOTE = "site_visit_note"
    SPECIALIST_REPORT = "specialist_report"
    CORRESPONDENCE = "correspondence"
    DATA_ROOM_INDEX = "data_room_index"
    WORKING_PAPER = "working_paper"
    OTHER_SPECIFIED = "other_specified"


class ParentKind(StrEnum):
    FINANCIAL = "financial"
    COMMERCIAL = "commercial"


class CellRole(StrEnum):
    HEADER = "header"
    TERM = "term"
    NATURE = "nature"
    LINE_ITEM = "line_item"
    QUESTION = "question"
    OTHER = "other"


class CalculationKind(StrEnum):
    POLICY_V2_ANCHOR = "policy_v2_anchor"
    POLICY_V2_BINDING = "policy_v2_binding"
    DERIVED_GL08 = "derived_gl08"
    NONE_NEEDED = "none_needed"


class AmbiguityStatus(StrEnum):
    OPEN = "open"
    TEXT_CLEAR = "text_clear"
    DECIDED = "decided"


class AmbiguityDecisionOwner(StrEnum):
    FUND = "fund"
    ANALYST = "analyst"
    PROGRAM_OWNER = "program_owner"


class OmissionVerdict(StrEnum):
    COMPLETE = "complete"
    GAPS = "gaps"


class RoleAliasOwner(StrEnum):
    GL_12 = "GL-12"
    PROGRAM_OWNER = "program_owner"


class RoleAliasStatus(StrEnum):
    PROPOSED = "proposed"


class BaselineKind(StrEnum):
    MERGED_INSTRUCTION = "merged_instruction"
    CROSS_ROW_INSTRUCTION = "cross_row_instruction"
    NAMED_CLARIFICATION = "named_clarification"


class FindingSeverity(StrEnum):
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


class InvariantStatus(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    SKIPPED = "skipped"


class Code(StrEnum):
    GL02_SOURCE_CHANGED_DURING_READ = "GL02_SOURCE_CHANGED_DURING_READ"
    GL02_SOURCE_COVERAGE = "GL02_SOURCE_COVERAGE"
    GL02_SOURCE_PIN_MISMATCH = "GL02_SOURCE_PIN_MISMATCH"
    GL02_SOURCE_FEATURE_PRESENT = "GL02_SOURCE_FEATURE_PRESENT"
    GL02_SOURCE_OTHER_CELL = "GL02_SOURCE_OTHER_CELL"
    GL02_IMPORTER_PARITY_FAILED = "GL02_IMPORTER_PARITY_FAILED"
    GL02_PARENT_COVERAGE = "GL02_PARENT_COVERAGE"
    GL02_PARENT_WITHOUT_CHILD = "GL02_PARENT_WITHOUT_CHILD"
    GL02_RAW_TEXT_INTEGRITY = "GL02_RAW_TEXT_INTEGRITY"
    GL02_RUN_UNDISPOSITIONED = "GL02_RUN_UNDISPOSITIONED"
    GL02_DISPOSITION_STALE = "GL02_DISPOSITION_STALE"
    GL02_DISPOSITION_DUPLICATE = "GL02_DISPOSITION_DUPLICATE"
    GL02_DISPOSITION_KIND_INVALID = "GL02_DISPOSITION_KIND_INVALID"
    GL02_DISPOSITION_RESTATES_UNLINKED = "GL02_DISPOSITION_RESTATES_UNLINKED"
    GL02_SEGMENT_NOT_FOCUSED = "GL02_SEGMENT_NOT_FOCUSED"
    GL02_SHARED_INSTRUCTION_UNAPPLIED = "GL02_SHARED_INSTRUCTION_UNAPPLIED"
    GL02_CROSS_ROW_UNAPPLIED = "GL02_CROSS_ROW_UNAPPLIED"
    GL02_CROSS_ROW_TARGET_EMPTY = "GL02_CROSS_ROW_TARGET_EMPTY"
    GL02_CROSS_ROW_SHARD = "GL02_CROSS_ROW_SHARD"
    GL02_MARKER_UNACCOUNTED = "GL02_MARKER_UNACCOUNTED"
    GL02_FIELD_UNRESOLVED = "GL02_FIELD_UNRESOLVED"
    GL02_DERIVATION_RATIONALE = "GL02_DERIVATION_RATIONALE"
    GL02_QUOTE_REQUIRED = "GL02_QUOTE_REQUIRED"
    GL02_QUOTE_NOT_FOUND = "GL02_QUOTE_NOT_FOUND"
    GL02_QUOTE_FOLD_MATCH_ONLY = "GL02_QUOTE_FOLD_MATCH_ONLY"
    GL02_QUOTE_FOLD_AMBIGUOUS = "GL02_QUOTE_FOLD_AMBIGUOUS"
    GL02_QUOTE_FIELD_INVALID = "GL02_QUOTE_FIELD_INVALID"
    GL02_CHILD_ID_INVALID = "GL02_CHILD_ID_INVALID"
    GL02_CHILD_ID_DUPLICATE = "GL02_CHILD_ID_DUPLICATE"
    GL02_RETIRED_REASON = "GL02_RETIRED_REASON"
    GL02_FACT_RULE = "GL02_FACT_RULE"
    GL02_JOINT_REASON_MISSING = "GL02_JOINT_REASON_MISSING"
    GL02_SPECIALIST_INVALID = "GL02_SPECIALIST_INVALID"
    GL02_CATEGORY_FORBIDDEN = "GL02_CATEGORY_FORBIDDEN"
    GL02_EVIDENCE_TYPE = "GL02_EVIDENCE_TYPE"
    GL02_PLAN_MINIMUM_MISSING = "GL02_PLAN_MINIMUM_MISSING"
    GL02_PLAN_MINIMUM_UNKNOWN = "GL02_PLAN_MINIMUM_UNKNOWN"
    GL02_POLICY_UNCARRIED = "GL02_POLICY_UNCARRIED"
    GL02_POLICY_RULE_UNKNOWN = "GL02_POLICY_RULE_UNKNOWN"
    GL02_POLICY_ROW_MISMATCH = "GL02_POLICY_ROW_MISMATCH"
    GL02_AMBIGUITY_TOPIC_MISSING = "GL02_AMBIGUITY_TOPIC_MISSING"
    GL02_SCOPE_NOT_RETAINED = "GL02_SCOPE_NOT_RETAINED"
    GL02_COMMERCIAL_DESTINATION = "GL02_COMMERCIAL_DESTINATION"
    GL02_COMMERCIAL_CATEGORY = "GL02_COMMERCIAL_CATEGORY"
    GL02_COMMERCIAL_COLUMN = "GL02_COMMERCIAL_COLUMN"
    GL02_ROLE_ALIAS_MISSING = "GL02_ROLE_ALIAS_MISSING"
    GL02_DERIVED_STALE = "GL02_DERIVED_STALE"
    GL02_MANIFEST_NOT_PUBLIC_SAFE = "GL02_MANIFEST_NOT_PUBLIC_SAFE"
    GL02_BASELINE_UNMET = "GL02_BASELINE_UNMET"
    GL02_VIEW_INVALID = "GL02_VIEW_INVALID"
    GL02_RECORD_SHARD_MISMATCH = "GL02_RECORD_SHARD_MISMATCH"
    GL02_DERIVED_RULE_UNKNOWN = "GL02_DERIVED_RULE_UNKNOWN"
    GL02_CATEGORY_MAP_MISMATCH = "GL02_CATEGORY_MAP_MISMATCH"
    GL02_LEDGER_INTEGRITY = "GL02_LEDGER_INTEGRITY"
    GL02_QUOTE_TOO_BROAD = "GL02_QUOTE_TOO_BROAD"
    GL02_DISPOSITION_HEAVY = "GL02_DISPOSITION_HEAVY"
    GL02_JSONL_DUPLICATE_KEY = "GL02_JSONL_DUPLICATE_KEY"
    GL02_JSONL_INVALID_UTF8 = "GL02_JSONL_INVALID_UTF8"
    GL02_JSONL_MISSING_NEWLINE = "GL02_JSONL_MISSING_NEWLINE"
    GL02_JSONL_INVALID_RECORD = "GL02_JSONL_INVALID_RECORD"
    GL02_JSONL_FLOAT = "GL02_JSONL_FLOAT"
    GL02_USAGE = "GL02_USAGE"
    GL02_IO = "GL02_IO"
    PENDING_CROSS_SHARD = "PENDING_CROSS_SHARD"


WARNING_CODES: frozenset[str] = frozenset(
    {Code.GL02_QUOTE_TOO_BROAD.value, Code.GL02_DISPOSITION_HEAVY.value}
)
RESERVED_CODES: frozenset[str] = frozenset({Code.GL02_LEDGER_INTEGRITY.value})

SKIPPED_WORKBOOK_ABSENT = "SKIPPED_WORKBOOK_ABSENT"
SKIPPED_POLICY_ABSENT = "SKIPPED_POLICY_ABSENT"
SKIPPED_BASELINE_ABSENT = "SKIPPED_BASELINE_ABSENT"
SKIPPED_VIEWS_ABSENT = "SKIPPED_VIEWS_ABSENT"
SKIPPED_LEDGER_LATER = "SKIPPED_LEDGER_LATER"
SKIPPED_CHECKS: frozenset[str] = frozenset(
    {
        SKIPPED_WORKBOOK_ABSENT,
        SKIPPED_POLICY_ABSENT,
        SKIPPED_BASELINE_ABSENT,
        SKIPPED_VIEWS_ABSENT,
        SKIPPED_LEDGER_LATER,
    }
)

INVARIANT_IDS: tuple[str, ...] = (
    "I-01",
    "I-02",
    "I-03",
    "I-04",
    "I-05",
    "I-06",
    "I-06b",
    "I-07",
    "I-08",
    "I-09",
    "I-10",
    "I-11",
    "I-12",
    "I-13",
    "I-14",
    "I-15",
    "I-16",
    "I-17",
    "I-18",
)

# invariant, function, primary code, passing test, mutation test
CODE_TABLE: tuple[tuple[str, str, str, str, str], ...] = (
    (
        "I-01",
        "check_source_coverage",
        Code.GL02_SOURCE_COVERAGE.value,
        "test_i01_source_coverage_passes",
        "test_i01_source_coverage_mutation",
    ),
    (
        "I-02",
        "check_importer_parity",
        Code.GL02_IMPORTER_PARITY_FAILED.value,
        "test_i02_importer_parity_passes",
        "test_i02_importer_parity_mutation",
    ),
    (
        "I-03",
        "check_parent_coverage",
        Code.GL02_PARENT_COVERAGE.value,
        "test_i03_parent_coverage_passes",
        "test_i03_parent_coverage_mutation",
    ),
    (
        "I-04",
        "check_active_children",
        Code.GL02_PARENT_WITHOUT_CHILD.value,
        "test_i04_active_child_passes",
        "test_i04_active_child_mutation",
    ),
    (
        "I-05",
        "check_raw_text_integrity",
        Code.GL02_RAW_TEXT_INTEGRITY.value,
        "test_i05_raw_text_integrity_passes",
        "test_i05_raw_text_integrity_mutation",
    ),
    (
        "I-06",
        "check_accounting",
        Code.GL02_RUN_UNDISPOSITIONED.value,
        "test_i06_wording_passes",
        "test_i06_wording_mutation",
    ),
    (
        "I-06b",
        "check_accounting",
        Code.GL02_SEGMENT_NOT_FOCUSED.value,
        "test_i06b_focus_passes",
        "test_i06b_focus_mutation",
    ),
    (
        "I-07",
        "check_shared_instructions",
        Code.GL02_SHARED_INSTRUCTION_UNAPPLIED.value,
        "test_i07_shared_instruction_passes",
        "test_i07_shared_instruction_mutation",
    ),
    (
        "I-08",
        "check_field_completeness",
        Code.GL02_FIELD_UNRESOLVED.value,
        "test_i08_field_completeness_passes",
        "test_i08_field_completeness_mutation",
    ),
    (
        "I-09",
        "check_plan_minimum",
        Code.GL02_PLAN_MINIMUM_MISSING.value,
        "test_i09_plan_minimum_passes",
        "test_i09_plan_minimum_mutation",
    ),
    (
        "I-10",
        "check_policy_coverage",
        Code.GL02_POLICY_UNCARRIED.value,
        "test_i10_policy_coverage_passes",
        "test_i10_policy_coverage_mutation",
    ),
    (
        "I-11",
        "check_named_clarifications",
        Code.GL02_AMBIGUITY_TOPIC_MISSING.value,
        "test_i11_named_clarifications_passes",
        "test_i11_named_clarifications_mutation",
    ),
    (
        "I-12",
        "check_scope_retention",
        Code.GL02_SCOPE_NOT_RETAINED.value,
        "test_i12_scope_retention_passes",
        "test_i12_scope_retention_mutation",
    ),
    (
        "I-13",
        "check_commercial_alignment",
        Code.GL02_COMMERCIAL_DESTINATION.value,
        "test_i13_commercial_alignment_passes",
        "test_i13_commercial_alignment_mutation",
    ),
    (
        "I-14",
        "check_determinism",
        Code.GL02_DERIVED_STALE.value,
        "test_i14_determinism_passes",
        "test_i14_determinism_mutation",
    ),
    (
        "I-15",
        "assert_public_safe",
        Code.GL02_MANIFEST_NOT_PUBLIC_SAFE.value,
        "test_i15_public_manifest_passes",
        "test_i15_public_manifest_mutation",
    ),
    (
        "I-16",
        "reserved",
        Code.GL02_LEDGER_INTEGRITY.value,
        "test_i16_ledger_code_is_reserved",
        "test_i16_ledger_code_is_reserved",
    ),
    (
        "I-17",
        "check_baseline",
        Code.GL02_BASELINE_UNMET.value,
        "test_i17_baseline_passes",
        "test_i17_baseline_mutation",
    ),
    (
        "I-18",
        "check_views",
        Code.GL02_VIEW_INVALID.value,
        "test_i18_views_passes",
        "test_i18_views_mutation",
    ),
    (
        "I-06",
        "check_accounting",
        Code.GL02_DISPOSITION_DUPLICATE.value,
        "test_disposition_duplicate_passes",
        "test_disposition_duplicate_mutation",
    ),
    (
        "I-08",
        "check_field_completeness",
        Code.GL02_JOINT_REASON_MISSING.value,
        "test_joint_reason_passes",
        "test_joint_reason_mutation",
    ),
)


def plan_code_shard(code: str) -> Shard:
    """Return the sheet shard that must carry a plan-minimum code."""
    prefix = code.split("_", 1)[0]
    mapping = {
        "PL": Shard.PL,
        "CF": Shard.CF,
        "LIAB": Shard.LIAB,
        "ASSET": Shard.ASSET,
    }
    try:
        return mapping[prefix]
    except KeyError as exc:
        raise ValueError(code) from exc
